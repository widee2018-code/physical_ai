"""[A1]~[A3] 에어컨 상태·명령 분기.

A1 이벤트 → 계단 함수(Zero-Order Hold) → A2 명령-피드백 대조, IR 실패 재전송, 수동 조작 감지,
전력 교차 검증(E1 연계) → A3 응답 지연 추정(오프라인)
"""
from __future__ import annotations

import logging
import math
from bisect import bisect_right
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .config import HVACConfig
from .utils import median

log = logging.getLogger("occucool.hvac")


@dataclass(frozen=True)
class HVACState:
    power: bool
    mode: str = "cool"           # cool | heat | dry | fan | auto
    setpoint: float = 24.0
    fan: Optional[str] = None    # 피드백이 풍량을 주지 않으면 None

    def matches(self, other: Optional["HVACState"], sp_tol: float = 0.25) -> bool:
        if other is None or self.power != other.power:
            return False
        if not self.power:
            return True
        if self.mode != other.mode or abs(self.setpoint - other.setpoint) > sp_tol:
            return False
        return not (self.fan and other.fan and self.fan != other.fan)

    def describe(self) -> str:
        if not self.power:
            return "OFF"
        return f"ON {self.mode} {self.setpoint:.1f}℃" + (f" fan={self.fan}" if self.fan else "")


class StateTimeline:
    """[A1] 상태 이벤트를 계단 함수로 보관하고 임의 시각의 상태를 돌려준다."""

    def __init__(self, retention_s: float = 86400.0):
        self.retention_s = retention_s
        self._ts: List[float] = []
        self._st: List[HVACState] = []

    def record(self, ts: float, state: HVACState) -> None:
        if self._st and self._st[-1] == state:
            return
        self._ts.append(ts)
        self._st.append(state)
        while len(self._ts) > 1 and ts - self._ts[1] > self.retention_s:
            self._ts.pop(0)
            self._st.pop(0)

    def value_at(self, ts: float) -> Optional[HVACState]:
        i = bisect_right(self._ts, ts) - 1
        return self._st[i] if i >= 0 else None


@dataclass
class ReconcileEvent:
    kind: str           # confirmed | retry | alarm | manual_override | energy_mismatch
    ts: float
    detail: str
    command: Optional[HVACState] = None


class CommandReconciler:
    """[A2] 명령-피드백 대조.

    feedback_available=False면(IR 단방향만 가능) 명령 로그로 상태를 추정하고,
    전력(E1) 교차 검증만으로 이상을 감지한다.
    """

    def __init__(self, cfg: HVACConfig, feedback_available: bool = True):
        self.cfg = cfg
        self.feedback_available = feedback_available
        self.believed: Optional[HVACState] = None
        self._pending: Optional[dict] = None
        self.manual_hold_until = -math.inf
        self.alarm_until = -math.inf
        self._mismatch_minutes = 0

    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    def on_command_sent(self, cmd: HVACState, ts: float) -> None:
        if self.feedback_available:
            self._pending = {"cmd": cmd, "sent": ts, "retries": 0}
        else:
            self.believed = cmd

    def on_feedback(self, state: HVACState, ts: float) -> List[ReconcileEvent]:
        ev: List[ReconcileEvent] = []
        prev = self.believed
        if self._pending is not None:
            cmd = self._pending["cmd"]
            if state.matches(cmd):
                ev.append(ReconcileEvent("confirmed", ts, f"retries={self._pending['retries']}", state))
                self._pending = None
            elif prev is not None and not state.matches(prev):
                ev.append(self._manual(state, ts, "명령 대기 중 다른 상태로 변경"))
                self._pending = None
        elif prev is not None and not state.matches(prev):
            ev.append(self._manual(state, ts, f"{prev.describe()} → {state.describe()}"))
        self.believed = state
        return ev

    def _manual(self, state: HVACState, ts: float, detail: str) -> ReconcileEvent:
        self.manual_hold_until = ts + self.cfg.manual_hold_s
        return ReconcileEvent("manual_override", ts, detail, state)

    def check_timeouts(self, ts: float) -> List[ReconcileEvent]:
        p = self._pending
        if p is None or ts - p["sent"] < self.cfg.confirm_s:
            return []
        if p["retries"] < self.cfg.max_retries:
            p["retries"] += 1
            p["sent"] = ts
            return [ReconcileEvent("retry", ts, f"재전송 {p['retries']}회", p["cmd"])]
        self._pending = None
        self.alarm_until = ts + self.cfg.alarm_cooldown_s
        return [ReconcileEvent("alarm", ts, "IR 명령 반영 실패", p["cmd"])]

    def cross_check_energy(self, energy_state: Optional[str], ts: float) -> List[ReconcileEvent]:
        """1분마다 호출. 믿고 있는 상태와 전력 기반 운전 상태가 계속 어긋나면 알림."""
        b = self.believed
        if b is None or energy_state is None:
            return []
        mismatch = (not b.power and energy_state == "compressor") or (b.power and energy_state == "off")
        self._mismatch_minutes = self._mismatch_minutes + 1 if mismatch else 0
        if self._mismatch_minutes == self.cfg.energy_mismatch_minutes:
            return [ReconcileEvent("energy_mismatch", ts, f"상태={b.describe()} 전력={energy_state}")]
        return []

    def in_manual_hold(self, ts: float) -> bool:
        return ts < self.manual_hold_until

    def auto_enabled(self, ts: float) -> bool:
        return ts >= self.alarm_until


def estimate_dead_time(on_times: Sequence[float], temp_series: Sequence[Tuple[float, float]],
                       drop_c: float = 0.2, max_wait_s: float = 1800.0, cooling: bool = True) -> Optional[float]:
    """[A3] 냉(난)방 시작 후 실내 온도가 drop_c만큼 변하기까지 걸린 시간의 중앙값(초).

    초기 학습 시 운전 로그로 1회 실행하고, 이후 주기적으로 갱신한다.
    """
    if not temp_series:
        return None
    ts = [t for t, _ in temp_series]
    sign = -1.0 if cooling else 1.0
    lags = []
    for t0 in on_times:
        i0 = bisect_right(ts, t0) - 1
        if i0 < 0:
            continue
        base = temp_series[i0][1]
        for t, v in temp_series[i0 + 1:]:
            if t - t0 > max_wait_s:
                break
            if sign * (v - base) >= drop_c:
                lags.append(t - t0)
                break
    return median(lags) if lags else None
