"""규칙 기반 제어 판단 + [P1] 출력 보호.

제어 정책 (보고서 6장 위험 2위 대응: 공실 시 1단계 Setback → 장시간 공실 시 OFF):
- 안전 모드: 스케줄 기반 (운영·예냉 시간 ON 기본 설정, 그 외 OFF)
- 재실: 단계 1 기본 설정온도, 단계 2 강화. 제습 조건이면 dry 모드
- 공실 + 비운영: OFF / 공실 + 예냉: ON 기본 설정
- 공실 + 운영: vacancy_off_after_s 전까지 Setback, 이후 OFF
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .config import ControlConfig
from .fusion import ControlInput
from .hvac import HVACState
from .utils import round_half


@dataclass
class Decision:
    desired: HVACState
    reason: str


class RuleController:
    def __init__(self, cfg: ControlConfig):
        self.cfg = cfg
        self.vacant_since: Optional[float] = None
        self.dehum_active = False
        self._dehum_since: Optional[float] = None

    def _update_dehum(self, ci: ControlInput, ts: float) -> None:
        """제습 판단(S6 이후). 보간·무효 습도로는 모드를 바꾸지 않는다 (위험 5위 대응)."""
        c = self.cfg
        if c.season != "cool" or ci.rh_pct is None or ci.rh_unreliable:
            return
        dp = ci.dew_point_c
        if not self.dehum_active:
            cond = ci.rh_pct >= c.dehum_enter_rh or (dp is not None and dp >= c.dehum_enter_dewpoint_c)
            if cond:
                self._dehum_since = self._dehum_since if self._dehum_since is not None else ts
                if ts - self._dehum_since >= c.dehum_enter_hold_s:
                    self.dehum_active = True
            else:
                self._dehum_since = None
        else:
            dp_ok = dp is None or dp < c.dehum_enter_dewpoint_c - c.dehum_exit_dewpoint_margin_c
            if ci.rh_pct <= c.dehum_exit_rh and dp_ok:
                self.dehum_active = False
                self._dehum_since = None

    def decide(self, ci: ControlInput, ts: float) -> Decision:
        c = self.cfg
        self._update_dehum(ci, ts)
        cool = c.season == "cool"
        mode = "cool" if cool else "heat"
        sign = 1.0 if cool else -1.0
        base = c.base_setpoint_cool_c if cool else c.base_setpoint_heat_c

        if ci.safe_mode:
            on = ci.schedule in ("operating", "precool")
            st = HVACState(on, mode, base) if on else HVACState(False, mode, base)
            return Decision(st, "safe_mode(" + ",".join(ci.safety_reasons) + ")")

        if ci.occupied:
            self.vacant_since = None
            sp = base - sign * c.level2_boost_c if ci.level >= 2 else base
            m = "dry" if (cool and self.dehum_active) else mode
            return Decision(HVACState(True, m, round_half(sp)), f"occupied L{ci.level}" + (" dehum" if m == "dry" else ""))

        if ci.schedule != "operating":
            self.vacant_since = None
            if ci.schedule == "precool":
                return Decision(HVACState(True, mode, base), "precool")
            return Decision(HVACState(False, mode, base), "closed")

        if self.vacant_since is None:
            self.vacant_since = ts
        if ts - self.vacant_since < c.vacancy_off_after_s:
            return Decision(HVACState(True, mode, round_half(base + sign * c.setback_c)), "vacant setback")
        return Decision(HVACState(False, mode, base), "vacant long → off")


class ControlGuard:
    """[P1] 출력 보호: 수동 조작 보류 → 압축기 최소 ON/OFF → Deadband → 설정온도 변화율 제한."""

    def __init__(self, cfg: ControlConfig):
        self.cfg = cfg
        self._power_changed_at = -math.inf
        self._sp_changes: deque = deque()   # (ts, |Δ|)

    def observe(self, prev: Optional[HVACState], new: Optional[HVACState], ts: float) -> None:
        """실제(믿고 있는) 상태 변화를 기록. 시작 시 이력을 모르면 오래전 변경으로 간주한다."""
        if new is None or prev is None:
            return
        if prev.power != new.power:
            self._power_changed_at = ts
        elif prev.power and new.power and abs(prev.setpoint - new.setpoint) > 1e-6:
            self._sp_changes.append((ts, abs(prev.setpoint - new.setpoint)))

    def _comfort_direction(self, delta: float) -> bool:
        return delta < 0 if self.cfg.season == "cool" else delta > 0

    def apply(self, desired: HVACState, current: Optional[HVACState], ts: float,
              manual_hold: bool) -> Tuple[Optional[HVACState], List[str]]:
        c = self.cfg
        if manual_hold:
            return None, ["manual_hold"]
        if current is None:
            return desired, ["initial"]
        notes: List[str] = []

        if desired.power != current.power:
            need = c.min_on_s if current.power else c.min_off_s
            if ts - self._power_changed_at < need:
                notes.append("min_on" if current.power else "min_off")
                return None, notes

        if not desired.power:
            final = HVACState(False, current.mode, current.setpoint, current.fan)
        elif not current.power:
            final = HVACState(True, desired.mode, round_half(desired.setpoint), desired.fan)
        else:
            sp = current.setpoint
            delta = desired.setpoint - current.setpoint
            if abs(delta) < c.deadband_c:
                if abs(delta) > 1e-6:
                    notes.append("deadband")
            elif c.rate_limit_both_directions or not self._comfort_direction(delta):
                while self._sp_changes and ts - self._sp_changes[0][0] >= c.setpoint_rate_window_s:
                    self._sp_changes.popleft()
                allowed = max(0.0, c.setpoint_rate_c - sum(d for _, d in self._sp_changes))
                if abs(delta) > allowed:
                    delta = math.copysign(allowed, delta)
                    notes.append("rate_limit")
                sp = round_half(current.setpoint + delta)
            else:
                sp = round_half(desired.setpoint)
            final = HVACState(True, desired.mode, sp, desired.fan)

        if final.matches(current):
            return None, notes
        return final, notes
