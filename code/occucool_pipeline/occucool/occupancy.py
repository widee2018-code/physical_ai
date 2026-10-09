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
        # 최근 window개 값만 보관하는 고정 길이 큐 (오래된 값은 자동 삭제)
        self._buf: deque = deque(maxlen=max(1, int(window)))

    # 새 값을 넣고 현재 창의 중앙값을 반환 (순간적으로 튀는 값 완화)
    def push(self, x: float) -> float:
        self._buf.append(x)
        return median(self._buf)

    # 버퍼를 비운다
    def reset(self) -> None:
        self._buf.clear()


class StreamingHampel:
    """[V7][S2] 인과적 Hampel 필터.

    창 = 직전 (window-1)개 원시값 + 현재값.
    |x - 중앙값| > k * max(1.4826 * MAD, min_scale) 이면 이상치로 보고 중앙값으로 대체한다.
    min_scale은 MAD가 0이 되기 쉬운 정수 카운트·저잡음 센서에서 필터가 과민해지는 것을 막는다.
    """

    def __init__(self, window: int, k: float, min_scale: float, min_samples: int = 3):
        # 창 크기는 최소 3 (중앙값·MAD 계산에 필요)
        self._buf: deque = deque(maxlen=max(3, int(window)))
        self.k = k
        self.min_scale = min_scale
        self.min_samples = min_samples

    # 새 값을 넣고 (필터링된 값, 이상치 여부)를 반환
    def push(self, x: float) -> Tuple[float, bool]:
        self._buf.append(x)
        # 샘플이 충분히 쌓이기 전에는 판정하지 않고 그대로 통과
        if len(self._buf) < self.min_samples:
            return x, False
        # MAD(중앙값 절대 편차) 계산. 1.4826을 곱하면 정규분포 표준편차와 같은 척도가 된다
        med = median(self._buf)
        mad = median(abs(v - med) for v in self._buf)
        scale = max(1.4826 * mad, self.min_scale)
        # 중앙값에서 k × 스케일보다 멀리 벗어나면 이상치 → 중앙값으로 대체
        if abs(x - med) > self.k * scale:
            return med, True
        return x, False

    # 버퍼를 비운다 (급격한 수준 변화·통신 공백 후 재시작용)
    def reset(self) -> None:
        self._buf.clear()


def ceil_median(values: Sequence[float]) -> int:
    """구간 중앙값을 올림. 0.5명 같은 애매한 값은 재실 쪽으로 보수적으로 해석한다."""
    # 1e-9를 빼서 3.0000000001 같은 부동소수점 오차가 4로 올림되는 것을 방지
    return int(math.ceil(median(values) - 1e-9))


class BinResampler:
    """[V8] 왼쪽 닫힌 구간 [t, t+grid) 단위 집계."""

    def __init__(self, grid_s: float, reducer: Callable[[Sequence[float]], float] = median):
        self.grid_s = grid_s
        self.reducer = reducer
        # 구간 시작 시각 → 그 구간에 들어온 값 목록
        self._bins: Dict[float, List[float]] = {}

    # 타임스탬프가 속한 구간의 시작 시각 (예: grid 10초면 23.4 → 20.0)
    def bin_start(self, ts: float) -> float:
        return math.floor(ts / self.grid_s) * self.grid_s

    # 값을 해당 구간에 추가
    def push(self, ts: float, value: float) -> None:
        self._bins.setdefault(self.bin_start(ts), []).append(value)

    # 지정 구간의 값을 집계(기본: 중앙값)해 꺼내고, 그보다 오래된 구간은 버린다. 값이 없으면 None
    def pop(self, start: float) -> Optional[float]:
        vals = self._bins.pop(start, None)
        for k in [k for k in self._bins if k < start]:
            del self._bins[k]
        return self.reducer(vals) if vals else None


# 재실 상태: 공실 / 재실
class OccState(str, Enum):
    VACANT = "vacant"
    OCCUPIED = "occupied"


# 상태 머신 출력 한 건 (missing_s: 결측 지속 시간[초], camera_fault: 카메라 이상 여부)
@dataclass
class OccupancyOutput:
    ts: float
    state: OccState
    level: int
    count: Optional[int]
    missing_s: float
    missing_level: str  # none | short | medium | fault
    camera_fault: bool

    # 재실 상태인지 여부
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
        # th: 각 단계의 하한 인원(오름차순), _cand/_since: 변경 후보 단계와 후보가 된 시각
        self.th = sorted(thresholds)
        self.margin = margin
        self.hold_s = hold_s
        self.level = 0
        self._cand: Optional[int] = None
        self._since = 0.0

    # 히스테리시스 없이 인원수만으로 단계 계산 (넘어선 하한 개수)
    def raw(self, count: int) -> int:
        return sum(1 for t in self.th if count >= t)

    # 인원수로 재실 단계를 갱신한다 (변경은 후보가 hold_s 동안 유지될 때만 확정)
    def update(self, ts: float, count: int, occupied: bool) -> int:
        # 공실이면 즉시 단계 0으로 초기화
        if not occupied:
            self.level, self._cand = 0, None
            return 0
        # 재실 중이면 최소 1단계
        raw = max(1, self.raw(count))
        if self.level == 0:  # 재실 진입 시 즉시 반영
            self.level, self._cand = raw, None
            return self.level
        # 목표 단계 결정: 올라갈 때는 바로 후보, 내려갈 때는 하한보다 margin명 더 적어야 후보
        target = self.level
        if raw > self.level:
            target = raw
        elif raw < self.level and count < self.th[self.level - 1] - self.margin:
            target = max(1, raw)
        # 후보 단계가 hold_s초 동안 유지되었을 때만 실제 단계를 변경
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
        # 인원>0이 시작된 시각, 인원==0이 시작된 시각, 마지막 유효 데이터 시각
        self._positive_since: Optional[float] = None
        self._zero_since: Optional[float] = None
        self._last_valid: Optional[float] = None

    # 새 인원수(None = 결측)를 받아 재실 상태를 갱신하고 결과를 반환
    def update(self, ts: float, count: Optional[int]) -> OccupancyOutput:
        c = self.cfg
        # 결측 처리: 상태는 그대로 두고, 결측 지속 시간에 따라 short / medium / fault로 분류
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

        # 유효 데이터가 들어왔으므로 마지막 유효 시각 갱신
        self._last_valid = ts
        # 사람 있음: 공실 타이머 리셋, 인원>0이 entry_debounce_s 이상 지속되면 재실로 전환
        if count > 0:
            self._zero_since = None
            if self._positive_since is None:
                self._positive_since = ts
            if self.state is OccState.VACANT and ts - self._positive_since >= c.entry_debounce_s:
                self.state = OccState.OCCUPIED
        else:
            # 사람 없음: 입실 타이머 리셋, 인원==0이 vacancy_confirm_s 이상 지속되면 공실로 전환
            self._positive_since = None
            if self._zero_since is None:
                self._zero_since = ts
            if self.state is OccState.OCCUPIED and ts - self._zero_since >= c.vacancy_confirm_s:
                self.state = OccState.VACANT
        # 재실 단계를 계산해 결과 반환
        level = self.levels.update(ts, count, self.state is OccState.OCCUPIED)
        return OccupancyOutput(ts, self.state, level, count, 0.0, "none", False)
