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
    ts: float
    count: Optional[int]
    zone_counts: Dict[str, Optional[float]]
    occupancy: OccupancyOutput
    climate: ClimateSample
    outdoor_c: Optional[float]
    outdoor_flag: Flag
    schedule: str
    energy_state: Optional[str]
    hvac: Optional[HVACState]
    safe_mode: bool = False
    safety_reasons: List[str] = field(default_factory=list)


def assess_safety(occ: OccupancyOutput, clim: ClimateSample) -> Tuple[bool, List[str]]:
    """M2: 제어 입력을 믿을 수 없으면 안전 모드(스케줄 기반 보수적 운전)."""
    reasons = []
    if occ.camera_fault:
        reasons.append("camera_fault")
    if clim.temp_flag is Flag.INVALID:
        reasons.append("temp_invalid")
    return bool(reasons), reasons


@dataclass
class ControlInput:
    """M3 결과: 1분 제어 주기 입력 벡터."""
    ts: float
    occupied: bool
    level: int
    max_count: Optional[int]
    temp_c: Optional[float]
    rh_pct: Optional[float]
    dew_point_c: Optional[float]
    dtdt_c_per_min: Optional[float]
    outdoor_c: Optional[float]
    schedule: str
    rh_unreliable: bool
    safe_mode: bool
    safety_reasons: List[str]

    def features(self) -> Dict[str, Optional[float]]:
        return {"occupied": float(self.occupied), "level": float(self.level),
                "temp_c": self.temp_c, "rh_pct": self.rh_pct, "dew_point_c": self.dew_point_c,
                "dtdt_c_per_min": self.dtdt_c_per_min, "outdoor_c": self.outdoor_c}


class ControlAggregator:
    """M3: 재실은 구간 최댓값(보수적), 온습도는 최신 EMA, 습도 신뢰도는 구간 내 하나라도 보간이면 불신."""

    def __init__(self) -> None:
        self._buf: List[GridRecord] = []

    def add(self, rec: GridRecord) -> None:
        self._buf.append(rec)

    def flush(self, ts: float) -> ControlInput:
        recs, self._buf = self._buf, []
        last = recs[-1]
        counts = [r.count for r in recs if r.count is not None]
        c = last.climate
        return ControlInput(
            ts=ts,
            occupied=any(r.occupancy.occupied for r in recs),
            level=max(r.occupancy.level for r in recs),
            max_count=max(counts) if counts else None,
            temp_c=c.temp_ema,
            rh_pct=c.rh_ema,
            dew_point_c=c.dew_point_c,
            dtdt_c_per_min=c.dtdt_c_per_min,
            outdoor_c=last.outdoor_c,
            schedule=last.schedule,
            rh_unreliable=any(r.climate.rh_flag in (Flag.INTERP, Flag.INVALID) for r in recs),
            safe_mode=last.safe_mode,
            safety_reasons=list(last.safety_reasons),
        )


class Normalizer:
    """M4: 학습 데이터에서 평균·표준편차를 구해 고정하고 운영 중에는 그대로 적용한다.

    반드시 이상치·결측 처리가 끝난 데이터(보간·추정 구간 제외 권장)로 fit 한다.
    """

    def __init__(self) -> None:
        self.mean: Dict[str, float] = {}
        self.std: Dict[str, float] = {}

    def fit(self, rows: Sequence[Dict[str, Optional[float]]]) -> "Normalizer":
        keys = {k for r in rows for k in r}
        for k in keys:
            vals = [r[k] for r in rows if r.get(k) is not None]
            if not vals:
                continue
            m = sum(vals) / len(vals)
            self.mean[k] = m
            self.std[k] = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))
        return self

    def transform(self, row: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
        out = {}
        for k, v in row.items():
            if v is None or k not in self.mean:
                out[k] = None
            else:
                s = self.std[k]
                out[k] = (v - self.mean[k]) / s if s > 1e-12 else 0.0
        return out

    def to_json(self) -> str:
        return json.dumps({"mean": self.mean, "std": self.std})

    @classmethod
    def from_json(cls, s: str) -> "Normalizer":
        d = json.loads(s)
        n = cls()
        n.mean, n.std = d["mean"], d["std"]
        return n
