"""[V7]~[V9] 인원수 시계열.

V7 중앙값 필터 → Hampel 필터 → V8 10초 리샘플링 → V9 재실 상태 머신(비대칭 Debounce) → 재실 단계 이산화
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .config import OccupancyConfig
from .utils import median


class RollingMedian:
    """[V7] 인과적 이동 중앙값 필터."""

    def __init__(self, window: int):
        self._buf: deque = deque(maxlen=max(1, int(window)))

    def push(self, x: float) -> float:
        self._buf.append(x)
        return median(self._buf)

    def reset(self) -> None:
        self._buf.clear()


class StreamingHampel:
    """[V7][S2] 인과적 Hampel 필터.

    창 = 직전 (window-1)개 원시값 + 현재값.
    |x - 중앙값| > k * max(1.4826 * MAD, min_scale) 이면 이상치로 보고 중앙값으로 대체한다.
    min_scale은 MAD가 0이 되기 쉬운 정수 카운트·저잡음 센서에서 필터가 과민해지는 것을 막는다.
    """

    def __init__(self, window: int, k: float, min_scale: float, min_samples: int = 3):
        self._buf: deque = deque(maxlen=max(3, int(window)))
        self.k = k
        self.min_scale = min_scale
        self.min_samples = min_samples

    def push(self, x: float) -> Tuple[float, bool]:
        self._buf.append(x)
        if len(self._buf) < self.min_samples:
            return x, False
        med = median(self._buf)
        mad = median(abs(v - med) for v in self._buf)
        scale = max(1.4826 * mad, self.min_scale)
        if abs(x - med) > self.k * scale:
            return med, True
        return x, False

    def reset(self) -> None:
        self._buf.clear()


def ceil_median(values: Sequence[float]) -> int:
    """구간 중앙값을 올림. 0.5명 같은 애매한 값은 재실 쪽으로 보수적으로 해석한다."""
    return int(math.ceil(median(values) - 1e-9))


class BinResampler:
    """[V8] 왼쪽 닫힌 구간 [t, t+grid) 단위 집계."""

    def __init__(self, grid_s: float, reducer: Callable[[Sequence[float]], float] = median):
        self.grid_s = grid_s
        self.reducer = reducer
        self._bins: Dict[float, List[float]] = {}

    def bin_start(self, ts: float) -> float:
        return math.floor(ts / self.grid_s) * self.grid_s

    def push(self, ts: float, value: float) -> None:
        self._bins.setdefault(self.bin_start(ts), []).append(value)

    def pop(self, start: float) -> Optional[float]:
        vals = self._bins.pop(start, None)
        for k in [k for k in self._bins if k < start]:
            del self._bins[k]
        return self.reducer(vals) if vals else None


class OccState(str, Enum):
    VACANT = "vacant"
    OCCUPIED = "occupied"


@dataclass
class OccupancyOutput:
    ts: float
    state: OccState
    level: int
    count: Optional[int]
    missing_s: float
    missing_level: str  # none | short | medium | fault
    camera_fault: bool

    @property
    def occupied(self) -> bool:
        return self.state is OccState.OCCUPIED


class LevelDiscretizer:
    """[V9] 재실 단계 이산화 + 히스테리시스.

    단계 0 = 공실(상태 머신이 결정), 1 = 1~2명, 2 = 3명 이상 (기본값).
    올라갈 때: 해당 단계 하한 이상이 hold_s 유지.
    내려갈 때: 현재 단계 하한보다 margin명 더 적은 상태가 hold_s 유지.
    """

    def __init__(self, thresholds: Sequence[int], margin: int, hold_s: float):
        self.th = sorted(thresholds)
        self.margin = margin
        self.hold_s = hold_s
        self.level = 0
        self._cand: Optional[int] = None
        self._since = 0.0

    def raw(self, count: int) -> int:
        return sum(1 for t in self.th if count >= t)

    def update(self, ts: float, count: int, occupied: bool) -> int:
        if not occupied:
            self.level, self._cand = 0, None
            return 0
        raw = max(1, self.raw(count))
        if self.level == 0:  # 재실 진입 시 즉시 반영
            self.level, self._cand = raw, None
            return self.level
        target = self.level
        if raw > self.level:
            target = raw
        elif raw < self.level and count < self.th[self.level - 1] - self.margin:
            target = max(1, raw)
        if target == self.level:
            self._cand = None
        elif target != self._cand:
            self._cand, self._since = target, ts
        elif ts - self._since >= self.hold_s:
            self.level, self._cand = target, None
        return self.level


class OccupancyStateMachine:
    """[V9] 비대칭 Debounce 재실 상태 머신.

    - 입실: count>0이 entry_debounce_s 동안 유지되면 OCCUPIED
    - 공실: count==0이 vacancy_confirm_s 동안 유지되면 VACANT
    - 결측: 상태 유지, 공실 타이머 정지(재실 쪽으로 보수적 해석). missing_fault_s 초과 시 카메라 이상.
    """

    def __init__(self, cfg: OccupancyConfig):
        self.cfg = cfg
        self.state = OccState.VACANT
        self.levels = LevelDiscretizer(cfg.level_thresholds, cfg.level_margin, cfg.level_hold_s)
        self._positive_since: Optional[float] = None
        self._zero_since: Optional[float] = None
        self._last_valid: Optional[float] = None

    def update(self, ts: float, count: Optional[int]) -> OccupancyOutput:
        c = self.cfg
        if count is None:
            missing = ts - self._last_valid if self._last_valid is not None else math.inf
            self._positive_since = None
            self._zero_since = None
            if missing <= c.missing_hold_s:
                ml = "short"
            elif missing <= c.missing_fault_s:
                ml = "medium"
            else:
                ml = "fault"
            return OccupancyOutput(ts, self.state, self.levels.level, None, missing, ml, ml == "fault")

        self._last_valid = ts
        if count > 0:
            self._zero_since = None
            if self._positive_since is None:
                self._positive_since = ts
            if self.state is OccState.VACANT and ts - self._positive_since >= c.entry_debounce_s:
                self.state = OccState.OCCUPIED
        else:
            self._positive_since = None
            if self._zero_since is None:
                self._zero_since = ts
            if self.state is OccState.OCCUPIED and ts - self._zero_since >= c.vacancy_confirm_s:
                self.state = OccState.VACANT
        level = self.levels.update(ts, count, self.state is OccState.OCCUPIED)
        return OccupancyOutput(ts, self.state, level, count, 0.0, "none", False)
