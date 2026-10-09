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

# 이 모듈 전용 로거
log = logging.getLogger("occucool.hvac")


# 에어컨 한 시점의 상태(전원·모드·설정온도·풍량). frozen=True라 생성 후 변경 불가
@dataclass(frozen=True)
class HVACState:
    power: bool
    mode: str = "cool"           # cool | heat | dry | fan | auto
    setpoint: float = 24.0
    fan: Optional[str] = None    # 피드백이 풍량을 주지 않으면 None

    # 두 상태가 '사실상 같은지' 비교. 설정온도는 sp_tol(℃) 이내 차이를 허용
    def matches(self, other: Optional["HVACState"], sp_tol: float = 0.25) -> bool:
        # 비교 대상이 없거나 전원이 다르면 불일치
        if other is None or self.power != other.power:
            return False
        # 둘 다 OFF면 모드·온도는 의미 없으므로 일치로 본다
        if not self.power:
            return True
        # ON일 때는 모드와 설정온도까지 비교
        if self.mode != other.mode or abs(self.setpoint - other.setpoint) > sp_tol:
            return False
        # 풍량은 양쪽 모두 값이 있을 때만 비교(피드백에 없으면 무시)
        return not (self.fan and other.fan and self.fan != other.fan)

    # 로그·알림용 사람이 읽기 쉬운 상태 문자열 반환
    def describe(self) -> str:
        if not self.power:
            return "OFF"
        return f"ON {self.mode} {self.setpoint:.1f}℃" + (f" fan={self.fan}" if self.fan else "")


class StateTimeline:
    """[A1] 상태 이벤트를 계단 함수로 보관하고 임의 시각의 상태를 돌려준다."""

    def __init__(self, retention_s: float = 86400.0):
        self.retention_s = retention_s
        # 이벤트 시각 목록과 상태 목록을 같은 순서로 나란히 저장
        self._ts: List[float] = []
        self._st: List[HVACState] = []

    def record(self, ts: float, state: HVACState) -> None:
        # 직전과 같은 상태면 저장하지 않음(변화가 있을 때만 기록)
        if self._st and self._st[-1] == state:
            return
        self._ts.append(ts)
        self._st.append(state)
        # 보관 기간(retention_s)보다 오래된 기록 삭제. 단, 기준점이 될 1개는 남김
        while len(self._ts) > 1 and ts - self._ts[1] > self.retention_s:
            self._ts.pop(0)
            self._st.pop(0)

    def value_at(self, ts: float) -> Optional[HVACState]:
        # 이진 탐색으로 ts 이전의 마지막 이벤트를 찾음(계단 함수 값)
        i = bisect_right(self._ts, ts) - 1
        return self._st[i] if i >= 0 else None


# 대조 과정에서 발생한 사건(확인·재전송·알림 등) 기록용
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
        # believed: 현재 에어컨 상태라고 믿는 값 / _pending: 피드백을 기다리는 명령
        self.feedback_available = feedback_available
        self.believed: Optional[HVACState] = None
        self._pending: Optional[dict] = None
        # 이 시각 전까지는 수동 조작 보류·알림 정지 상태 (-inf면 해당 없음)
        self.manual_hold_until = -math.inf
        self.alarm_until = -math.inf
        self._mismatch_minutes = 0

    # 확인 대기 중인 명령이 있는지 여부
    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    # 명령을 보냈을 때 호출: 피드백이 있으면 확인 대기, 없으면 명령대로 됐다고 가정
    def on_command_sent(self, cmd: HVACState, ts: float) -> None:
        if self.feedback_available:
            self._pending = {"cmd": cmd, "sent": ts, "retries": 0}
        else:
            self.believed = cmd

    # 에어컨에서 실제 상태 피드백을 받았을 때 호출해 명령과 대조
    def on_feedback(self, state: HVACState, ts: float) -> List[ReconcileEvent]:
        ev: List[ReconcileEvent] = []
        prev = self.believed
        # 확인 대기 중인 명령이 있는 경우
        if self._pending is not None:
            cmd = self._pending["cmd"]
            # 피드백이 명령과 일치 → 명령 성공
            if state.matches(cmd):
                ev.append(ReconcileEvent("confirmed", ts, f"retries={self._pending['retries']}", state))
                self._pending = None
            # 명령과도, 이전 상태와도 다르면 사람이 리모컨으로 바꾼 것으로 판단
            elif prev is not None and not state.matches(prev):
                ev.append(self._manual(state, ts, "명령 대기 중 다른 상태로 변경"))
                self._pending = None
        # 대기 명령이 없는데 상태가 바뀌었다면 수동 조작
        elif prev is not None and not state.matches(prev):
            ev.append(self._manual(state, ts, f"{prev.describe()} → {state.describe()}"))
        # 피드백 값을 새로운 '믿는 상태'로 갱신
        self.believed = state
        return ev

    # 수동 조작 감지: 일정 시간(manual_hold_s) 자동 제어를 보류
    def _manual(self, state: HVACState, ts: float, detail: str) -> ReconcileEvent:
        self.manual_hold_until = ts + self.cfg.manual_hold_s
        return ReconcileEvent("manual_override", ts, detail, state)

    # 주기적으로 호출: 확인 시간이 지난 명령은 재전송하거나, 횟수 초과 시 알림
    def check_timeouts(self, ts: float) -> List[ReconcileEvent]:
        p = self._pending
        # 대기 명령이 없거나 아직 확인 시간 안이면 할 일 없음
        if p is None or ts - p["sent"] < self.cfg.confirm_s:
            return []
        # 재전송 횟수가 남아 있으면 다시 보내고 대기 시각을 갱신
        if p["retries"] < self.cfg.max_retries:
            p["retries"] += 1
            p["sent"] = ts
            return [ReconcileEvent("retry", ts, f"재전송 {p['retries']}회", p["cmd"])]
        # 재전송을 다 써도 실패 → 알림 후 일정 시간 자동 제어 정지
        self._pending = None
        self.alarm_until = ts + self.cfg.alarm_cooldown_s
        return [ReconcileEvent("alarm", ts, "IR 명령 반영 실패", p["cmd"])]

    def cross_check_energy(self, energy_state: Optional[str], ts: float) -> List[ReconcileEvent]:
        """1분마다 호출. 믿고 있는 상태와 전력 기반 운전 상태가 계속 어긋나면 알림."""
        b = self.believed
        if b is None or energy_state is None:
            return []
        # OFF라고 믿는데 압축기가 돌거나, ON이라고 믿는데 전력이 0이면 불일치
        mismatch = (not b.power and energy_state == "compressor") or (b.power and energy_state == "off")
        # 불일치가 연속된 분(minute) 수를 셈. 일치하면 0으로 리셋
        self._mismatch_minutes = self._mismatch_minutes + 1 if mismatch else 0
        # 정확히 기준 분수에 도달한 순간 1번만 알림(중복 알림 방지)
        if self._mismatch_minutes == self.cfg.energy_mismatch_minutes:
            return [ReconcileEvent("energy_mismatch", ts, f"상태={b.describe()} 전력={energy_state}")]
        return []

    # 지금이 수동 조작 보류 기간인지
    def in_manual_hold(self, ts: float) -> bool:
        return ts < self.manual_hold_until

    # 알림 정지 기간이 끝나 자동 제어가 가능한지
    def auto_enabled(self, ts: float) -> bool:
        return ts >= self.alarm_until


def estimate_dead_time(on_times: Sequence[float], temp_series: Sequence[Tuple[float, float]],
                       drop_c: float = 0.2, max_wait_s: float = 1800.0, cooling: bool = True) -> Optional[float]:
    """[A3] 냉(난)방 시작 후 실내 온도가 drop_c만큼 변하기까지 걸린 시간의 중앙값(초).

    초기 학습 시 운전 로그로 1회 실행하고, 이후 주기적으로 갱신한다.
    """
    # 온도 데이터가 없으면 추정 불가
    if not temp_series:
        return None
    ts = [t for t, _ in temp_series]
    # 냉방이면 온도가 내려가는 방향(-), 난방이면 올라가는 방향(+)을 변화로 본다
    sign = -1.0 if cooling else 1.0
    lags = []
    # 각 운전 시작 시각마다 온도가 drop_c만큼 변하기까지 걸린 시간을 구함
    for t0 in on_times:
        # 운전 시작 직전의 온도를 기준값으로 사용
        i0 = bisect_right(ts, t0) - 1
        if i0 < 0:
            continue
        base = temp_series[i0][1]
        # 이후 온도를 따라가며 max_wait_s 안에 목표 변화가 생기는지 확인
        for t, v in temp_series[i0 + 1:]:
            if t - t0 > max_wait_s:
                break
            if sign * (v - base) >= drop_c:
                lags.append(t - t0)
                break
    # 이상치 영향을 줄이려 평균 대신 중앙값 반환
    return median(lags) if lags else None
