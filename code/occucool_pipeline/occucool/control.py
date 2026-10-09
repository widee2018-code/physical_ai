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


# 제어 판단 결과: 원하는 에어컨 상태와 그 이유
@dataclass
class Decision:
    desired: HVACState
    reason: str


# 재실·스케줄·습도를 보고 원하는 에어컨 상태를 정하는 규칙 기반 제어기
class RuleController:
    def __init__(self, cfg: ControlConfig):
        self.cfg = cfg
        # 공실 시작 시각, 제습 모드 활성 여부, 제습 조건 시작 시각
        self.vacant_since: Optional[float] = None
        self.dehum_active = False
        self._dehum_since: Optional[float] = None

    def _update_dehum(self, ci: ControlInput, ts: float) -> None:
        """제습 판단(S6 이후). 보간·무효 습도로는 모드를 바꾸지 않는다 (위험 5위 대응)."""
        c = self.cfg
        # 냉방 계절이 아니거나 습도 값을 믿을 수 없으면 판단하지 않음
        if c.season != "cool" or ci.rh_pct is None or ci.rh_unreliable:
            return
        dp = ci.dew_point_c
        # 제습 OFF 상태: 습도 또는 이슬점이 높은 상태가 일정 시간 지속되면 제습 ON
        if not self.dehum_active:
            cond = ci.rh_pct >= c.dehum_enter_rh or (dp is not None and dp >= c.dehum_enter_dewpoint_c)
            if cond:
                # 조건이 처음 만족된 시각을 기억(이미 있으면 유지)
                self._dehum_since = self._dehum_since if self._dehum_since is not None else ts
                if ts - self._dehum_since >= c.dehum_enter_hold_s:
                    self.dehum_active = True
            else:
                # 조건이 깨지면 타이머 초기화
                self._dehum_since = None
        # 제습 ON 상태: 습도와 이슬점이 충분히 내려가야 해제(진입·해제 기준을 달리해 떨림 방지)
        else:
            dp_ok = dp is None or dp < c.dehum_enter_dewpoint_c - c.dehum_exit_dewpoint_margin_c
            if ci.rh_pct <= c.dehum_exit_rh and dp_ok:
                self.dehum_active = False
                self._dehum_since = None

    def decide(self, ci: ControlInput, ts: float) -> Decision:
        # 계절에 따라 모드와 방향 부호 결정(냉방 +1, 난방 -1)
        c = self.cfg
        self._update_dehum(ci, ts)
        cool = c.season == "cool"
        mode = "cool" if cool else "heat"
        sign = 1.0 if cool else -1.0
        base = c.base_setpoint_cool_c if cool else c.base_setpoint_heat_c

        # 1) 안전 모드: 센서를 믿을 수 없으면 스케줄만 보고 ON/OFF
        if ci.safe_mode:
            on = ci.schedule in ("operating", "precool")
            st = HVACState(on, mode, base) if on else HVACState(False, mode, base)
            return Decision(st, "safe_mode(" + ",".join(ci.safety_reasons) + ")")

        # 2) 재실: 3명 이상(단계 2)이면 설정온도를 강화, 제습 조건이면 dry 모드
        if ci.occupied:
            self.vacant_since = None
            sp = base - sign * c.level2_boost_c if ci.level >= 2 else base
            m = "dry" if (cool and self.dehum_active) else mode
            return Decision(HVACState(True, m, round_half(sp)), f"occupied L{ci.level}" + (" dehum" if m == "dry" else ""))

        # 3) 공실 + 비운영 시간: 예냉 시간이면 ON, 그 외에는 OFF
        if ci.schedule != "operating":
            self.vacant_since = None
            if ci.schedule == "precool":
                return Decision(HVACState(True, mode, base), "precool")
            return Decision(HVACState(False, mode, base), "closed")

        # 4) 공실 + 운영 시간: 처음엔 Setback(온도 완화), 오래 비면 OFF
        if self.vacant_since is None:
            self.vacant_since = ts
        if ts - self.vacant_since < c.vacancy_off_after_s:
            return Decision(HVACState(True, mode, round_half(base + sign * c.setback_c)), "vacant setback")
        return Decision(HVACState(False, mode, base), "vacant long → off")


class ControlGuard:
    """[P1] 출력 보호: 수동 조작 보류 → 압축기 최소 ON/OFF → Deadband → 설정온도 변화율 제한."""

    def __init__(self, cfg: ControlConfig):
        self.cfg = cfg
        # 마지막 전원 변경 시각(처음엔 -inf = 아주 오래전으로 간주)
        self._power_changed_at = -math.inf
        self._sp_changes: deque = deque()   # (ts, |Δ|)

    def observe(self, prev: Optional[HVACState], new: Optional[HVACState], ts: float) -> None:
        """실제(믿고 있는) 상태 변화를 기록. 시작 시 이력을 모르면 오래전 변경으로 간주한다."""
        if new is None or prev is None:
            return
        # 전원이 바뀌면 시각 기록(압축기 최소 ON/OFF 판단용)
        if prev.power != new.power:
            self._power_changed_at = ts
        # ON 상태에서 설정온도가 바뀌면 변화량 기록(변화율 제한용)
        elif prev.power and new.power and abs(prev.setpoint - new.setpoint) > 1e-6:
            self._sp_changes.append((ts, abs(prev.setpoint - new.setpoint)))

    # 설정온도 변화가 '더 시원하게/따뜻하게' 하는 쾌적 방향인지 판단
    def _comfort_direction(self, delta: float) -> bool:
        return delta < 0 if self.cfg.season == "cool" else delta > 0

    # 원하는 상태에 보호 규칙을 적용해 실제로 보낼 명령을 결정. None이면 명령 안 보냄
    def apply(self, desired: HVACState, current: Optional[HVACState], ts: float,
              manual_hold: bool) -> Tuple[Optional[HVACState], List[str]]:
        c = self.cfg
        # 수동 조작 보류 중이면 아무 명령도 보내지 않음
        if manual_hold:
            return None, ["manual_hold"]
        # 현재 상태를 모르면 원하는 상태를 그대로 보냄
        if current is None:
            return desired, ["initial"]
        notes: List[str] = []

        # 전원을 바꾸려 할 때: 압축기 보호를 위해 최소 ON/OFF 시간이 지났는지 확인
        if desired.power != current.power:
            need = c.min_on_s if current.power else c.min_off_s
            if ts - self._power_changed_at < need:
                notes.append("min_on" if current.power else "min_off")
                return None, notes

        # 끄는 경우: 현재 설정은 유지하고 전원만 OFF
        if not desired.power:
            final = HVACState(False, current.mode, current.setpoint, current.fan)
        # 켜는 경우: 원하는 설정 그대로 ON
        elif not current.power:
            final = HVACState(True, desired.mode, round_half(desired.setpoint), desired.fan)
        # 이미 ON인 상태에서 설정온도만 바꾸는 경우
        else:
            sp = current.setpoint
            delta = desired.setpoint - current.setpoint
            # 변화가 Deadband보다 작으면 무시(잦은 명령 방지)
            if abs(delta) < c.deadband_c:
                if abs(delta) > 1e-6:
                    notes.append("deadband")
            # 불쾌 방향(또는 양방향 제한 설정)일 때만 변화율 제한 적용
            elif c.rate_limit_both_directions or not self._comfort_direction(delta):
                # 제한 창 밖의 오래된 변경 기록은 제거
                while self._sp_changes and ts - self._sp_changes[0][0] >= c.setpoint_rate_window_s:
                    self._sp_changes.popleft()
                # 창 안에서 이미 쓴 변화량을 빼고 남은 허용량 계산
                allowed = max(0.0, c.setpoint_rate_c - sum(d for _, d in self._sp_changes))
                if abs(delta) > allowed:
                    delta = math.copysign(allowed, delta)
                    notes.append("rate_limit")
                sp = round_half(current.setpoint + delta)
            # 쾌적 방향(예: 재실 복귀)은 즉시 반영
            else:
                sp = round_half(desired.setpoint)
            final = HVACState(True, desired.mode, sp, desired.fan)

        # 최종 상태가 현재와 같으면 명령을 보낼 필요 없음
        if final.matches(current):
            return None, notes
        return final, notes
