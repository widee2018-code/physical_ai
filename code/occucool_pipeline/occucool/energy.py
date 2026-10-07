"""[E1] CT 전력 집계·운전 상태 분류, [E2] 기준선 정규화(대시보드 전용, 제어에 사용하지 않음)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from .config import EnergyConfig
from .utils import Flag


@dataclass
class MinuteEnergy:
    minute_ts: float
    avg_w: Optional[float]
    wh: Optional[float]
    state: Optional[str]   # off | fan | compressor
    flag: Flag


class PowerAggregator:
    """[E1] 1초 CT 값 → 1분 평균 → Wh 적산 → 운전 상태 분류.

    결측 분: 직전 운전 상태의 평균 전력으로 추정(ESTIMATED), 그것도 없으면 INVALID.
    """

    def __init__(self, cfg: EnergyConfig):
        self.cfg = cfg
        self._bins: Dict[float, List[float]] = {}
        self._next_minute: Optional[float] = None
        self._state_mean: Dict[str, float] = {}
        self._state_n: Dict[str, int] = {}
        self.last_state: Optional[str] = None
        self.compressor_starts = 0
        self.total_wh = 0.0
        self.estimated_minutes = 0
        self.missing_minutes = 0

    def start(self, ts: float) -> None:
        if self._next_minute is None:
            self._next_minute = math.floor(ts / 60.0) * 60.0

    def classify(self, w: float) -> str:
        if w < self.cfg.fan_threshold_w:
            return "off"
        if w < self.cfg.compressor_ratio * self.cfg.rated_power_w:
            return "fan"
        return "compressor"

    def push(self, ts: float, watts: Optional[float]) -> None:
        self.start(ts)
        if watts is None or not math.isfinite(watts) or watts < 0:
            return
        self._bins.setdefault(math.floor(ts / 60.0) * 60.0, []).append(float(watts))

    def close_until(self, now: float) -> List[MinuteEnergy]:
        out: List[MinuteEnergy] = []
        if self._next_minute is None:
            return out
        while self._next_minute + 60.0 <= now:
            out.append(self._close(self._next_minute))
            self._next_minute += 60.0
        return out

    def _close(self, m: float) -> MinuteEnergy:
        vals = self._bins.pop(m, None)
        for k in [k for k in self._bins if k < m]:
            del self._bins[k]
        if vals:
            avg = sum(vals) / len(vals)
            state, flag = self.classify(avg), Flag.OK
            n = self._state_n.get(state, 0)
            self._state_mean[state] = (self._state_mean.get(state, 0.0) * n + avg) / (n + 1)
            self._state_n[state] = n + 1
        elif self.last_state in self._state_mean:
            state, flag = self.last_state, Flag.ESTIMATED
            avg = self._state_mean[state]
            self.estimated_minutes += 1
        else:
            state, avg, flag = None, None, Flag.INVALID
            self.missing_minutes += 1
        wh = avg / 60.0 if avg is not None else None
        if state == "compressor" and self.last_state != "compressor":
            self.compressor_starts += 1
        if state is not None:
            self.last_state = state
        if wh is not None:
            self.total_wh += wh
        return MinuteEnergy(m, avg, wh, state, flag)


@dataclass
class DailyEnergy:
    date: str
    kwh: Optional[float]
    mean_outdoor_c: float
    occupied_hours: float
    valid: bool = True        # 결측·추정 비율이 높은 날은 False로 두고 계산에서 제외


class BaselineModel:
    """[E2] 일 에너지 = a + b·CDD + c·재실시간 회귀 (IPMVP 방식 B 참고).

    기존 1개월 이상 데이터로 fit 하고, 도입 후 데이터로 절감량을 계산한다.
    """

    def __init__(self, cdd_base_c: float = 24.0):
        self.cdd_base_c = cdd_base_c
        self.coef: Optional[np.ndarray] = None

    def _row(self, d: DailyEnergy) -> List[float]:
        return [1.0, max(0.0, d.mean_outdoor_c - self.cdd_base_c), d.occupied_hours]

    def fit(self, days: Sequence[DailyEnergy]) -> np.ndarray:
        use = [d for d in days if d.valid and d.kwh is not None]
        if len(use) < 4:
            raise ValueError("기준선 학습에는 유효한 날이 4일 이상 필요합니다.")
        X = np.array([self._row(d) for d in use])
        y = np.array([d.kwh for d in use])
        self.coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        return self.coef

    def predict(self, d: DailyEnergy) -> float:
        if self.coef is None:
            raise RuntimeError("fit 먼저 호출")
        return float(np.dot(self.coef, self._row(d)))

    def savings(self, days: Sequence[DailyEnergy]) -> List[dict]:
        out = []
        for d in days:
            if not d.valid or d.kwh is None:
                continue
            pred = self.predict(d)
            out.append({"date": d.date, "baseline_kwh": pred, "actual_kwh": d.kwh,
                        "saving_kwh": pred - d.kwh, "saving_pct": (pred - d.kwh) / pred * 100 if pred > 0 else None})
        return out
