"""전체 파이프라인 오케스트레이터 (보고서 2장).

    0 → [V1→…→V9] ∥ [S1→…→S6] ∥ [A1→A2→A3] ∥ [O1, O2] ∥ [E1→E2] → M1 → M2 → M3 → (M4) → P1

사용법:
    pipe = OccuCoolPipeline(cfg, detector, transmitter)
    루프에서 on_frame / on_climate / on_hvac_feedback / on_outdoor / on_power 로 원시 데이터를 넣고
    tick(now)를 자주 호출한다. tick은 닫힌 10초 그리드를 처리하고, 1분마다 제어 결과를 돌려준다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol

import numpy as np

from .config import PipelineConfig
from .control import ControlGuard, Decision, RuleController
from .energy import MinuteEnergy, PowerAggregator
from .external import OperatingSchedule, OutdoorProcessor
from .fusion import ControlAggregator, ControlInput, GridRecord, assess_safety
from .hvac import CommandReconciler, HVACState, ReconcileEvent, StateTimeline
from .occupancy import BinResampler, OccState, OccupancyStateMachine, RollingMedian, StreamingHampel, ceil_median
from .sensors import ClimateProcessor
from .utils import median
from .vision import Detector, VisionFrontEnd, VisionResult

# 모듈 공용 로거
log = logging.getLogger("occucool")

# 외부로 결과를 내보내는 콜백: (종류 문자열, 객체)
Sink = Callable[[str, object], None]


# 에어컨 명령 전송기 인터페이스 (send 메서드만 있으면 됨)
class IRTransmitter(Protocol):
    def send(self, command: HVACState) -> None:
        """에어컨으로 명령 전송 (IR, RS-485 등)."""


# 1분 제어 주기 한 번의 결과: 입력, 판단, 실제 명령, 메모, 전송/섀도 여부
@dataclass
class ControlOutcome:
    # 제어 시각
    ts: float
    # 제어에 사용한 입력 벡터
    inputs: ControlInput
    # 규칙 제어기의 판단
    decision: Decision
    # 가드를 통과해 만들어진 실제 명령 (없으면 None)
    command: Optional[HVACState]
    # 명령을 보내지 않은 이유 등 부가 메모
    notes: List[str] = field(default_factory=list)
    # 실제로 에어컨에 전송했는지
    sent: bool = False
    # 섀도 모드(판단만 하고 전송 안 함) 여부
    shadow: bool = False


# 모든 처리 단계를 묶어 원시 입력 → 10초 그리드 → 1분 제어까지 수행하는 본체
class OccuCoolPipeline:
    def __init__(self, cfg: PipelineConfig, detector: Detector, transmitter: Optional[IRTransmitter] = None,
                 feedback_available: bool = True, sink: Optional[Sink] = None):
        # 설정, 명령 전송기, 결과 출력 콜백 저장
        self.cfg = cfg
        self.transmitter = transmitter
        self.sink = sink
        # g: 그리드 간격(초, 기본 10초), oc: 재실 관련 설정
        g = cfg.grid_s
        oc = cfg.occupancy
        # V
        # 카메라 프레임 → 인원 검출 (V1~V6)
        self.vision = VisionFrontEnd(cfg.vision, detector)
        # 인원 수 잡음 제거: 이동 중앙값 → Hampel 이상치 필터 (V7)
        self.count_median = RollingMedian(oc.median_window)
        self.count_hampel = StreamingHampel(oc.hampel_window, oc.hampel_k, oc.hampel_min_scale)
        # 10초 그리드로 묶기 (V8). 구역별 묶음은 처음 등장할 때 생성
        self.count_bins = BinResampler(g, reducer=ceil_median)
        self.zone_bins: Dict[str, BinResampler] = {}
        # 인원 수 → 재실 상태 머신 (V9)
        self.occ = OccupancyStateMachine(oc)
        # S
        # 실내 온습도 처리기
        self.climate = ClimateProcessor(cfg.climate, g)
        # A
        # 에어컨 상태 기록과 명령-피드백 대조기
        self.timeline = StateTimeline()
        self.hvac = CommandReconciler(cfg.hvac, feedback_available)
        # O, E
        # 외기온도, 운영 스케줄, 전력 집계
        self.outdoor = OutdoorProcessor(cfg.outdoor)
        self.schedule = OperatingSchedule(cfg.schedule)
        self.energy = PowerAggregator(cfg.energy)
        # M, P
        # 1분 집계기, 규칙 제어기, 명령 안전 가드
        self.aggregator = ControlAggregator()
        self.controller = RuleController(cfg.control)
        self.guard = ControlGuard(cfg.control)

        # 다음에 처리할 10초 그리드 시작 시각과 다음 제어 시각 (첫 입력 때 정해짐)
        self._next_grid: Optional[float] = None
        self._next_control: Optional[float] = None
        # 가장 최근 1분 전력 결과와 마지막으로 가드에 알린 에어컨 상태
        self._last_energy: Optional[MinuteEnergy] = None
        self._last_believed: Optional[HVACState] = None
        # 에어컨 이벤트 기록과 최근 비전 결과 (디버깅·대시보드용)
        self.events: List[ReconcileEvent] = []
        self.last_vision: Optional[VisionResult] = None

    # ------------------------------------------------------------------ 0단계
    # 처음 들어온 데이터 시각을 기준으로 그리드·제어·전력 시계를 시작한다
    def _ensure_clock(self, ts: float) -> None:
        # 이미 시작되었다면 아무것도 하지 않음
        if self._next_grid is None:
            # 첫 그리드는 ts가 속한 10초 구간의 시작, 첫 제어는 그로부터 제어 주기 후
            self._next_grid = self.count_bins.bin_start(ts)
            self._next_control = self._next_grid + self.cfg.control_period_s
            self.energy.start(ts)

    # 출력 콜백이 설정되어 있으면 (종류, 객체)를 내보낸다
    def _emit(self, kind: str, obj: object) -> None:
        if self.sink is not None:
            self.sink(kind, obj)

    # ------------------------------------------------------------------ 입력
    def on_frame(self, frame: np.ndarray, ts: float) -> Optional[VisionResult]:
        """V1~V8 입력. 프레임은 이 호출 안에서만 쓰이고 저장되지 않는다."""
        self._ensure_clock(ts)
        # 비전 처리. 현재 재실 상태를 함께 넘겨 처리 방식(예: 프레임 간격)을 조절
        res = self.vision.process(frame, ts, occupied=self.occ.state is OccState.OCCUPIED)
        # 이번 프레임은 처리하지 않고 건너뜀
        if res is None:
            return None
        med = self.count_median.push(res.total)                 # V7 중앙값
        val, _ = self.count_hampel.push(med)                     # V7 Hampel
        self.count_bins.push(ts, val)                            # V8
        # 구역별 인원 수도 각 구역의 10초 묶음에 넣는다
        for z, c in res.zone_counts.items():
            self.zone_bins.setdefault(z, BinResampler(self.cfg.grid_s, reducer=median)).push(ts, c)
        self.last_vision = res
        return res

    # 실내 온습도 측정값 입력
    def on_climate(self, ts: float, temp_c: Optional[float], rh_pct: Optional[float]) -> None:
        self._ensure_clock(ts)
        self.climate.push(ts, temp_c, rh_pct)                   # S1~S2 (도착 즉시)

    # 에어컨 실제 상태 피드백 입력: 기록 → 명령과 대조 → 가드에 상태 변화 반영
    def on_hvac_feedback(self, ts: float, state: HVACState) -> None:
        self._ensure_clock(ts)
        self.timeline.record(ts, state)                          # A1
        self._handle_events(self.hvac.on_feedback(state, ts), ts)  # A2
        self._sync_guard(ts)

    # 외기온도 측정값 입력
    def on_outdoor(self, ts: float, temp_c: Optional[float]) -> None:
        self._ensure_clock(ts)
        self.outdoor.push(ts, temp_c)                            # O1

    # 전력(W) 측정값 입력
    def on_power(self, ts: float, watts: Optional[float]) -> None:
        self._ensure_clock(ts)
        self.energy.push(ts, watts)                              # E1

    # ------------------------------------------------------------------ 처리
    # 추정 에어컨 상태가 바뀌었으면 가드에 알려 변경 이력(최소 간격 등)을 갱신
    def _sync_guard(self, ts: float) -> None:
        b = self.hvac.believed
        if b != self._last_believed:
            self.guard.observe(self._last_believed, b, ts)
            self._last_believed = b

    # 에어컨 대조 이벤트를 기록·출력하고, 재전송·경보 등을 처리
    def _handle_events(self, events: List[ReconcileEvent], ts: float) -> None:
        for ev in events:
            self.events.append(ev)
            self._emit("hvac_event", ev)
            # 재전송 요청이면 같은 명령을 다시 보낸다
            if ev.kind == "retry" and ev.command is not None and self.transmitter is not None:
                self.transmitter.send(ev.command)
            # 경보·전력 불일치·수동 조작은 로그로 남긴다
            if ev.kind in ("alarm", "energy_mismatch", "manual_override"):
                log.info("[%s] %s", ev.kind, ev.detail)

    def tick(self, now: float) -> List[ControlOutcome]:
        """닫힌 10초 그리드를 순서대로 처리하고, 제어 주기가 되면 제어 결과를 반환한다."""
        # 아직 데이터가 하나도 안 들어왔으면 처리할 것이 없음
        outcomes: List[ControlOutcome] = []
        if self._next_grid is None:
            return outcomes
        grid = self.cfg.grid_s
        # now 기준으로 완전히 끝난(닫힌) 10초 그리드를 오래된 것부터 하나씩 처리
        while self._next_grid + grid <= now:
            # g0: 그리드 시작, g1: 그리드 끝
            g0 = self._next_grid
            g1 = g0 + grid
            # V8~V9
            # 이 그리드 구간의 인원 수(전체·구역별)를 꺼내 재실 상태 갱신
            count = self.count_bins.pop(g0)
            zones = {z: b.pop(g0) for z, b in self.zone_bins.items()}
            occ = self.occ.update(g1, None if count is None else int(count))
            # S3~S6
            clim = self.climate.tick(g1)
            # O1, O2
            out_v, out_f = self.outdoor.value_at(g1)
            sched = self.schedule.status(g1)
            # E1 (+A2 전력 교차 검증)
            # 끝난 1분 전력 구간을 닫고, 전력으로 에어컨 실제 동작을 교차 확인
            for m in self.energy.close_until(g1):
                self._last_energy = m
                self._emit("energy", m)
                self._handle_events(self.hvac.cross_check_energy(m.state, g1), g1)
            # A2 타임아웃·재전송
            # 명령 후 피드백이 오지 않은 경우 타임아웃 처리·재전송
            self._handle_events(self.hvac.check_timeouts(g1), g1)
            self._sync_guard(g1)
            # M1, M2
            # 안전 모드 판정 후 그리드 레코드를 만들어 1분 집계기에 쌓는다
            safe, reasons = assess_safety(occ, clim)
            rec = GridRecord(g1, None if count is None else int(count), zones, occ, clim, out_v, out_f, sched,
                             self._last_energy.state if self._last_energy else None, self.hvac.believed,
                             safe, reasons)
            self.aggregator.add(rec)
            self._emit("grid", rec)
            # M3 → 제어 → P1
            # 제어 시각에 도달하면 1분치를 요약해 제어 수행
            if g1 >= self._next_control:
                ci = self.aggregator.flush(g1)
                outcomes.append(self._control(ci, g1))
                self._next_control += self.cfg.control_period_s
            # 다음 그리드로 이동
            self._next_grid = g1
        # 사후 보간으로 다시 계산된 온습도 값을 내보낸다
        for bf in self.climate.drain_backfill():
            self._emit("backfill", bf)
        return outcomes

    # 제어 입력으로 판단 → 상황에 따라 명령 생성·전송하고 결과를 반환
    def _control(self, ci: ControlInput, ts: float) -> ControlOutcome:
        # 규칙 제어기가 원하는 상태를 결정
        decision = self.controller.decide(ci, ts)
        shadow = self.cfg.control.shadow_mode
        out = ControlOutcome(ts, ci, decision, None, [], False, shadow)
        # 에어컨 경보 후 냉각 기간이면 자동 제어 중지
        if not self.hvac.auto_enabled(ts):
            out.notes.append("hvac_alarm_cooldown")
        # 이전 명령의 피드백을 기다리는 중이면 새 명령 보류
        elif self.hvac.has_pending:
            out.notes.append("awaiting_feedback")
        else:
            # 가드가 최소 변경 간격·수동 조작 유지 등을 검사해 실제 명령을 만든다
            cmd, notes = self.guard.apply(decision.desired, self.hvac.believed, ts, self.hvac.in_manual_hold(ts))
            out.command, out.notes = cmd, notes
            # 섀도 모드가 아니고 전송기가 있으면 실제로 전송하고 대기 상태로 등록
            if cmd is not None and not shadow and self.transmitter is not None:
                self.transmitter.send(cmd)
                self.hvac.on_command_sent(cmd, ts)
                self._sync_guard(ts)
                out.sent = True
        self._emit("control", out)
        return out
