"""[M1]~[M4] 통합.

M1 10초 그리드 병합(각 분기의 as-of 조회 + 허용 오차) → M2 품질 플래그 통합·안전 모드 판정
→ M3 1분 제어 주기 집계 → M4 (ML 사용 시에만) 학습 통계 기반 정규화
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .hvac import HVACState
from .occupancy import OccupancyOutput
from .sensors import ClimateSample
from .utils import Flag


@dataclass
class GridRecord:
    """M1 결과: 10초 그리드 한 칸의 통합 레코드 (로그·대시보드·학습 데이터의 기본 단위)."""
    # 그리드 끝 시각(유닉스 초)
    ts: float
    # 필터링된 전체 인원 수와 구역별 인원 수 (없으면 None)
    count: Optional[int]
    zone_counts: Dict[str, Optional[float]]
    # 재실 판정, 실내 온습도 처리 결과
    occupancy: OccupancyOutput
    climate: ClimateSample
    # 외기온도와 그 품질 플래그, 운영 스케줄 상태
    outdoor_c: Optional[float]
    outdoor_flag: Flag
    schedule: str
    # 최근 1분 전력 상태와 추정 에어컨 상태
    energy_state: Optional[str]
    hvac: Optional[HVACState]
    # M2 안전 모드 여부와 그 사유
    safe_mode: bool = False
    safety_reasons: List[str] = field(default_factory=list)


def assess_safety(occ: OccupancyOutput, clim: ClimateSample) -> Tuple[bool, List[str]]:
    """M2: 제어 입력을 믿을 수 없으면 안전 모드(스케줄 기반 보수적 운전)."""
    # 안전 모드로 가야 하는 사유를 모은다
    reasons = []
    # 카메라 고장 → 재실 정보를 믿을 수 없음
    if occ.camera_fault:
        reasons.append("camera_fault")
    # 실내 온도가 무효 → 온도 기반 제어 불가
    if clim.temp_flag is Flag.INVALID:
        reasons.append("temp_invalid")
    # 사유가 하나라도 있으면 안전 모드
    return bool(reasons), reasons


@dataclass
class ControlInput:
    """M3 결과: 1분 제어 주기 입력 벡터."""
    ts: float
    # 재실 여부, 재실 단계, 구간 내 최대 인원
    occupied: bool
    level: int
    max_count: Optional[int]
    # 실내 온습도(EMA), 이슬점, 온도 변화율(℃/분)
    temp_c: Optional[float]
    rh_pct: Optional[float]
    dew_point_c: Optional[float]
    dtdt_c_per_min: Optional[float]
    outdoor_c: Optional[float]
    schedule: str
    # 습도 신뢰 불가 여부와 안전 모드 정보
    rh_unreliable: bool
    safe_mode: bool
    safety_reasons: List[str]

    # ML 모델 입력용 수치 특징 딕셔너리로 변환 (bool은 0/1 실수로)
    def features(self) -> Dict[str, Optional[float]]:
        return {"occupied": float(self.occupied), "level": float(self.level),
                "temp_c": self.temp_c, "rh_pct": self.rh_pct, "dew_point_c": self.dew_point_c,
                "dtdt_c_per_min": self.dtdt_c_per_min, "outdoor_c": self.outdoor_c}


class ControlAggregator:
    """M3: 재실은 구간 최댓값(보수적), 온습도는 최신 EMA, 습도 신뢰도는 구간 내 하나라도 보간이면 불신."""

    # 1분 동안 쌓인 10초 그리드 레코드 버퍼
    def __init__(self) -> None:
        self._buf: List[GridRecord] = []

    # 10초 그리드 레코드 하나를 버퍼에 추가
    def add(self, rec: GridRecord) -> None:
        self._buf.append(rec)

    # 버퍼를 비우면서 1분치 레코드를 제어 입력 하나로 요약
    def flush(self, ts: float) -> ControlInput:
        # 버퍼를 꺼내고 새 빈 버퍼로 교체 (호출 전 최소 1개는 쌓여 있다고 가정)
        recs, self._buf = self._buf, []
        last = recs[-1]
        # 결측이 아닌 인원 수만 모은다
        counts = [r.count for r in recs if r.count is not None]
        # 온습도는 가장 최근 그리드의 값을 사용
        c = last.climate
        return ControlInput(
            ts=ts,
            # 재실: 1분 중 한 번이라도 재실이면 재실 (사람을 놓치지 않도록 보수적으로)
            occupied=any(r.occupancy.occupied for r in recs),
            level=max(r.occupancy.level for r in recs),
            max_count=max(counts) if counts else None,
            # 온습도: 마지막 그리드의 EMA 값
            temp_c=c.temp_ema,
            rh_pct=c.rh_ema,
            dew_point_c=c.dew_point_c,
            dtdt_c_per_min=c.dtdt_c_per_min,
            # 외기·스케줄: 마지막 그리드 값
            outdoor_c=last.outdoor_c,
            schedule=last.schedule,
            # 구간 내 습도가 한 번이라도 보간/무효였으면 습도를 불신
            rh_unreliable=any(r.climate.rh_flag in (Flag.INTERP, Flag.INVALID) for r in recs),
            # 안전 모드는 마지막 그리드 상태를 따름
            safe_mode=last.safe_mode,
            safety_reasons=list(last.safety_reasons),
        )


class Normalizer:
    """M4: 학습 데이터에서 평균·표준편차를 구해 고정하고 운영 중에는 그대로 적용한다.

    반드시 이상치·결측 처리가 끝난 데이터(보간·추정 구간 제외 권장)로 fit 한다.
    """

    def __init__(self) -> None:
        # 특징 이름별 평균·표준편차 (fit 후 고정)
        self.mean: Dict[str, float] = {}
        self.std: Dict[str, float] = {}

    # 학습 데이터 행들로 특징별 평균·표준편차를 계산한다
    def fit(self, rows: Sequence[Dict[str, Optional[float]]]) -> "Normalizer":
        # 모든 행에 등장하는 특징 이름을 모은다
        keys = {k for r in rows for k in r}
        for k in keys:
            # 결측(None)은 제외하고 통계를 낸다
            vals = [r[k] for r in rows if r.get(k) is not None]
            if not vals:
                continue
            # 평균과 모집단 표준편차
            m = sum(vals) / len(vals)
            self.mean[k] = m
            self.std[k] = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))
        return self

    # 저장된 통계로 한 행을 z-점수 (v - 평균) / 표준편차로 정규화
    def transform(self, row: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
        out = {}
        for k, v in row.items():
            # 결측이거나 학습 때 없던 특징은 None
            if v is None or k not in self.mean:
                out[k] = None
            else:
                s = self.std[k]
                # 표준편차가 거의 0(상수 특징)이면 0으로 나누지 않도록 0.0
                out[k] = (v - self.mean[k]) / s if s > 1e-12 else 0.0
        return out

    # 학습한 통계를 JSON 문자열로 저장 (운영 장비로 옮기기 위함)
    def to_json(self) -> str:
        return json.dumps({"mean": self.mean, "std": self.std})

    @classmethod
    # JSON 문자열에서 통계를 읽어 Normalizer를 복원
    def from_json(cls, s: str) -> "Normalizer":
        d = json.loads(s)
        n = cls()
        n.mean, n.std = d["mean"], d["std"]
        return n
