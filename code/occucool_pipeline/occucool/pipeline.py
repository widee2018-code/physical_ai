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

log = logging.getLogger("occucool")

Sink = Callable[[str, object], None]


class IRTransmitter(Protocol):
    def send(self, command: HVACState) -> None:
        """에어컨으로 명령 전송 (IR, RS-485 등)."""


@dataclass
class ControlOutcome:
    ts: float
    inputs: ControlInput
    decision: Decision
    command: Optional[HVACState]
    notes: List[str] = field(default_factory=list)
    sent: bool = False
    shadow: bool = False


class OccuCoolPipeline:
    def __init__(self, cfg: PipelineConfig, detector: Detector, transmitter: Optional[IRTransmitter] = None,
                 feedback_available: bool = True, sink: Optional[Sink] = None):
        self.cfg = cfg
        self.transmitter = transmitter
        self.sink = sink
        g = cfg.grid_s
        oc = cfg.occupancy
        # V
        self.vision = VisionFrontEnd(cfg.vision, detector)
        self.count_median = RollingMedian(oc.median_window)
        self.count_hampel = StreamingHampel(oc.hampel_window, oc.hampel_k, oc.hampel_min_scale)
        self.count_bins = BinResampler(g, reducer=ceil_median)
        self.zone_bins: Dict[str, BinResampler] = {}
        self.occ = OccupancyStateMachine(oc)
        # S
        self.climate = ClimateProcessor(cfg.climate, g)
        # A
        self.timeline = StateTimeline()
        self.hvac = CommandReconciler(cfg.hvac, feedback_available)
        # O, E
        self.outdoor = OutdoorProcessor(cfg.outdoor)
        self.schedule = OperatingSchedule(cfg.schedule)
        self.energy = PowerAggregator(cfg.energy)
        # M, P
        self.aggregator = ControlAggregator()
        self.controller = RuleController(cfg.control)
        self.guard = ControlGuard(cfg.control)

        self._next_grid: Optional[float] = None
        self._next_control: Optional[float] = None
        self._last_energy: Optional[MinuteEnergy] = None
        self._last_believed: Optional[HVACState] = None
        self.events: List[ReconcileEvent] = []
        self.last_vision: Optional[VisionResult] = None

    # ------------------------------------------------------------------ 0단계
    def _ensure_clock(self, ts: float) -> None:
        if self._next_grid is None:
            self._next_grid = self.count_bins.bin_start(ts)
            self._next_control = self._next_grid + self.cfg.control_period_s
            self.energy.start(ts)

    def _emit(self, kind: str, obj: object) -> None:
        if self.sink is not None:
            self.sink(kind, obj)

    # ------------------------------------------------------------------ 입력
    def on_frame(self, frame: np.ndarray, ts: float) -> Optional[VisionResult]:
        """V1~V8 입력. 프레임은 이 호출 안에서만 쓰이고 저장되지 않는다."""
        self._ensure_clock(ts)
        res = self.vision.process(frame, ts, occupied=self.occ.state is OccState.OCCUPIED)
        if res is None:
            return None
        med = self.count_median.push(res.total)                 # V7 중앙값
        val, _ = self.count_hampel.push(med)                     # V7 Hampel
        self.count_bins.push(ts, val)                            # V8
        for z, c in res.zone_counts.items():
            self.zone_bins.setdefault(z, BinResampler(self.cfg.grid_s, reducer=median)).push(ts, c)
        self.last_vision = res
        return res

    def on_climate(self, ts: float, temp_c: Optional[float], rh_pct: Optional[float]) -> None:
        self._ensure_clock(ts)
        self.climate.push(ts, temp_c, rh_pct)                   # S1~S2 (도착 즉시)

    def on_hvac_feedback(self, ts: float, state: HVACState) -> None:
        self._ensure_clock(ts)
        self.timeline.record(ts, state)                          # A1
        self._handle_events(self.hvac.on_feedback(state, ts), ts)  # A2
        self._sync_guard(ts)

    def on_outdoor(self, ts: float, temp_c: Optional[float]) -> None:
        self._ensure_clock(ts)
        self.outdoor.push(ts, temp_c)                            # O1

    def on_power(self, ts: float, watts: Optional[float]) -> None:
        self._ensure_clock(ts)
        self.energy.push(ts, watts)                              # E1

    # ------------------------------------------------------------------ 처리
    def _sync_guard(self, ts: float) -> None:
        b = self.hvac.believed
        if b != self._last_believed:
            self.guard.observe(self._last_believed, b, ts)
            self._last_believed = b

    def _handle_events(self, events: List[ReconcileEvent], ts: float) -> None:
        for ev in events:
            self.events.append(ev)
            self._emit("hvac_event", ev)
            if ev.kind == "retry" and ev.command is not None and self.transmitter is not None:
                self.transmitter.send(ev.command)
            if ev.kind in ("alarm", "energy_mismatch", "manual_override"):
                log.info("[%s] %s", ev.kind, ev.detail)

    def tick(self, now: float) -> List[ControlOutcome]:
        """닫힌 10초 그리드를 순서대로 처리하고, 제어 주기가 되면 제어 결과를 반환한다."""
        outcomes: List[ControlOutcome] = []
        if self._next_grid is None:
            return outcomes
        grid = self.cfg.grid_s
        while self._next_grid + grid <= now:
            g0 = self._next_grid
            g1 = g0 + grid
            # V8~V9
            count = self.count_bins.pop(g0)
            zones = {z: b.pop(g0) for z, b in self.zone_bins.items()}
            occ = self.occ.update(g1, None if count is None else int(count))
            # S3~S6
            clim = self.climate.tick(g1)
            # O1, O2
            out_v, out_f = self.outdoor.value_at(g1)
            sched = self.schedule.status(g1)
            # E1 (+A2 전력 교차 검증)
            for m in self.energy.close_until(g1):
                self._last_energy = m
                self._emit("energy", m)
                self._handle_events(self.hvac.cross_check_energy(m.state, g1), g1)
            # A2 타임아웃·재전송
            self._handle_events(self.hvac.check_timeouts(g1), g1)
            self._sync_guard(g1)
            # M1, M2
            safe, reasons = assess_safety(occ, clim)
            rec = GridRecord(g1, None if count is None else int(count), zones, occ, clim, out_v, out_f, sched,
                             self._last_energy.state if self._last_energy else None, self.hvac.believed,
                             safe, reasons)
            self.aggregator.add(rec)
            self._emit("grid", rec)
            # M3 → 제어 → P1
            if g1 >= self._next_control:
                ci = self.aggregator.flush(g1)
                outcomes.append(self._control(ci, g1))
                self._next_control += self.cfg.control_period_s
            self._next_grid = g1
        for bf in self.climate.drain_backfill():
            self._emit("backfill", bf)
        return outcomes

    def _control(self, ci: ControlInput, ts: float) -> ControlOutcome:
        decision = self.controller.decide(ci, ts)
        shadow = self.cfg.control.shadow_mode
        out = ControlOutcome(ts, ci, decision, None, [], False, shadow)
        if not self.hvac.auto_enabled(ts):
            out.notes.append("hvac_alarm_cooldown")
        elif self.hvac.has_pending:
            out.notes.append("awaiting_feedback")
        else:
            cmd, notes = self.guard.apply(decision.desired, self.hvac.believed, ts, self.hvac.in_manual_hold(ts))
            out.command, out.notes = cmd, notes
            if cmd is not None and not shadow and self.transmitter is not None:
                self.transmitter.send(cmd)
                self.hvac.on_command_sent(cmd, ts)
                self._sync_guard(ts)
                out.sent = True
        self._emit("control", out)
        return out
