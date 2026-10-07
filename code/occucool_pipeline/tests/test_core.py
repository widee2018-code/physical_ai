"""핵심 필터·상태 머신·출력 보호 단위 테스트.  실행: python -m pytest -q"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from occucool.config import ClimateConfig, ControlConfig, HVACConfig, OccupancyConfig, VisionConfig
from occucool.control import ControlGuard
from occucool.hvac import CommandReconciler, HVACState, estimate_dead_time
from occucool.occupancy import LevelDiscretizer, OccState, OccupancyStateMachine, StreamingHampel
from occucool.sensors import ClimateProcessor
from occucool.utils import Flag, dew_point_c
from occucool.vision import Detection, IoUTracker, postprocess


# ---------------- V4 / V5 ----------------
def test_postprocess_filters():
    cfg = VisionConfig()
    dets = [
        Detection(0, 0, 40, 90, 0.9),       # 정상
        Detection(0, 0, 40, 90, 0.2),       # 저신뢰
        Detection(100, 100, 110, 115, 0.9), # 너무 작음
        Detection(200, 0, 300, 60, 0.9),    # 가로로 긴 박스
        Detection(2, 1, 42, 91, 0.8),       # 중복 → NMS
    ]
    out = postprocess(dets, cfg)
    assert len(out) == 1 and out[0].conf == 0.9


def test_tracker_requires_consecutive_frames():
    tr = IoUTracker(VisionConfig())
    d = Detection(0, 0, 40, 90, 0.9)
    tr.update([d], 0.0)
    tr.update([d], 0.5)
    active, newly, _ = tr.update([d], 1.0)
    assert len(active) == 1 and len(newly) == 1
    tr2 = IoUTracker(VisionConfig())
    tr2.update([d], 0.0)
    tr2.update([], 0.5)          # 미확정 트랙은 한 번 놓치면 폐기
    active, _, _ = tr2.update([d], 1.0)
    assert active == []


def test_tracker_lost_buffer_keeps_count():
    tr = IoUTracker(VisionConfig())
    d = Detection(0, 0, 40, 90, 0.9)
    for i in range(3):
        tr.update([d], i * 0.5)
    active, _, removed = tr.update([], 9.0)      # 10초 이내
    assert len(active) == 1 and removed == []
    active, _, removed = tr.update([], 12.0)     # 10초 초과
    assert active == [] and len(removed) == 1


# ---------------- V7 / V9 ----------------
def test_hampel_replaces_spike():
    h = StreamingHampel(7, 3.0, 0.5)
    for _ in range(6):
        h.push(2)
    val, outlier = h.push(6)
    assert outlier and val == 2
    val, outlier = h.push(3)      # 1명 차이는 통과
    assert not outlier and val == 3


def test_asymmetric_debounce():
    sm = OccupancyStateMachine(OccupancyConfig())
    t = 0.0
    for _ in range(3):
        sm.update(t, 1); t += 10
    assert sm.state is OccState.VACANT       # 20초: 아직 입실 미확정
    sm.update(t, 1); t += 10
    assert sm.state is OccState.OCCUPIED     # 30초
    for _ in range(30):
        sm.update(t, 0); t += 10
    assert sm.state is OccState.OCCUPIED     # 290초: 아직 공실 미확정
    sm.update(t, 0)
    assert sm.state is OccState.VACANT       # 300초


def test_missing_never_vacates():
    sm = OccupancyStateMachine(OccupancyConfig())
    for i in range(4):
        sm.update(i * 10.0, 1)
    out = None
    for i in range(4, 130):
        out = sm.update(i * 10.0, None)
    assert out.state is OccState.OCCUPIED and out.camera_fault


def test_level_hysteresis():
    lv = LevelDiscretizer((1, 3), 1, 120)
    assert lv.update(0, 3, True) == 2
    assert lv.update(10, 2, True) == 2       # 2명: 하한-여유(2) 미만 아님 → 유지
    lv.update(20, 1, True)
    assert lv.update(130, 1, True) == 2      # 110초 → 아직 유지
    assert lv.update(140, 1, True) == 1      # 120초 → 하강


# ---------------- S1~S6 ----------------
def test_climate_range_rate_and_backfill():
    cp = ClimateProcessor(ClimateConfig(), 10.0)
    for i in range(10):
        cp.push(i * 10.0, 25.0, 50.0)
    cp.push(100.0, 85.0, 50.0)               # 범위 밖
    cp.push(105.0, 28.0, 50.0)               # 변화율 초과
    assert cp.temp.stats["range_reject"] == 1 and cp.temp.stats["rate_reject"] == 1
    s = cp.tick(110.0)                       # 마지막 정상값(90초)이 20초 전 → 직전값 유지
    assert s.temp_flag is Flag.HELD and abs(s.temp_c - 25.0) < 1e-9
    for g in (150.0, 200.0):                 # 끊김 → INTERP
        assert cp.tick(g).temp_flag is Flag.INTERP
    cp.push(210.0, 25.2, 50.0)               # 복구 → 사후 보간
    bf = [b for b in cp.drain_backfill() if b[1] == "temp_c"]
    assert [g for g, _, _ in bf] == [150.0, 200.0]
    assert 25.0 < bf[0][2] < bf[1][2] < 25.2
    assert cp.tick(600.0).temp_flag is Flag.INVALID


def test_dew_point():
    assert abs(dew_point_c(24.0, 60.0) - 15.8) < 0.2


# ---------------- A2 / A3 ----------------
def test_reconciler_retry_alarm_and_manual():
    rc = CommandReconciler(HVACConfig())
    rc.on_feedback(HVACState(False), 0)
    cmd = HVACState(True, "cool", 24.0)
    rc.on_command_sent(cmd, 10)
    assert rc.check_timeouts(39) == []
    kinds = [e.kind for t in (40, 70, 100) for e in rc.check_timeouts(t)]
    assert kinds == ["retry", "retry", "alarm"] and not rc.auto_enabled(101)
    rc.on_feedback(HVACState(True, "cool", 24.0), 200)
    ev = rc.on_feedback(HVACState(True, "cool", 22.0), 210)
    assert ev[0].kind == "manual_override" and rc.in_manual_hold(220)


def test_dead_time():
    series = [(t, 27.0 - max(0, t - 120) * 0.002) for t in range(0, 1000, 10)]
    lag = estimate_dead_time([0.0], series)
    assert 200 <= lag <= 230


# ---------------- P1 ----------------
def test_guard_min_off_and_rate_limit():
    g = ControlGuard(ControlConfig())
    on24, off = HVACState(True, "cool", 24.0), HVACState(False, "cool", 24.0)
    g.observe(on24, off, 0.0)
    cmd, notes = g.apply(on24, off, 100.0, False)
    assert cmd is None and "min_off" in notes
    cmd, _ = g.apply(on24, off, 301.0, False)
    assert cmd == on24
    # 불쾌 방향(냉방 중 설정온도 상승)은 1℃/10분 제한
    cmd, notes = g.apply(HVACState(True, "cool", 26.0), on24, 400.0, False)
    assert cmd.setpoint == 25.0 and "rate_limit" in notes
    g.observe(on24, cmd, 400.0)
    cmd, notes = g.apply(HVACState(True, "cool", 26.0), HVACState(True, "cool", 25.0), 500.0, False)
    assert cmd is None and "rate_limit" in notes
    # 쾌적 방향(재실 복귀)은 즉시
    cmd, _ = g.apply(on24, HVACState(True, "cool", 25.0), 510.0, False)
    assert cmd.setpoint == 24.0
    # Deadband, 수동 조작 보류
    assert g.apply(HVACState(True, "cool", 24.3), on24, 900.0, False)[0] is None
    assert g.apply(off, on24, 900.0, True) == (None, ["manual_hold"])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
    print(f"{len(fns)} passed")
