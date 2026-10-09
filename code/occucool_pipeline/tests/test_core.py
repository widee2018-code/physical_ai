"""핵심 필터·상태 머신·출력 보호 단위 테스트.  실행: python -m pytest -q"""
import math
import os
import sys

# 상위 폴더를 import 경로에 추가해 occucool 패키지를 불러올 수 있게 함
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from occucool.config import ClimateConfig, ControlConfig, HVACConfig, OccupancyConfig, VisionConfig
from occucool.control import ControlGuard
from occucool.hvac import CommandReconciler, HVACState, estimate_dead_time
from occucool.occupancy import LevelDiscretizer, OccState, OccupancyStateMachine, StreamingHampel
from occucool.sensors import ClimateProcessor
from occucool.utils import Flag, dew_point_c
from occucool.vision import Detection, IoUTracker, postprocess


# ---------------- V4 / V5 ----------------
# V4 후처리: 저신뢰·너무 작은·가로로 긴 박스는 버리고, 겹치는 중복은 NMS로 하나만 남는지 확인
def test_postprocess_filters():
    cfg = VisionConfig()
    dets = [
        Detection(0, 0, 40, 90, 0.9),       # 정상
        Detection(0, 0, 40, 90, 0.2),       # 저신뢰
        Detection(100, 100, 110, 115, 0.9), # 너무 작음
        Detection(200, 0, 300, 60, 0.9),    # 가로로 긴 박스
        Detection(2, 1, 42, 91, 0.8),       # 중복 → NMS
    ]
    # 정상 박스(conf 0.9) 하나만 남아야 함
    out = postprocess(dets, cfg)
    assert len(out) == 1 and out[0].conf == 0.9


# V5 트래커: 연속 3프레임 검출돼야 트랙이 확정되고, 미확정 트랙은 한 번 놓치면 폐기되는지 확인
def test_tracker_requires_consecutive_frames():
    tr = IoUTracker(VisionConfig())
    # 같은 박스를 0.5초 간격으로 3번 넣으면 3번째에 확정(newly)
    d = Detection(0, 0, 40, 90, 0.9)
    tr.update([d], 0.0)
    tr.update([d], 0.5)
    active, newly, _ = tr.update([d], 1.0)
    assert len(active) == 1 and len(newly) == 1
    # 두 번째 프레임에서 놓치면 미확정 트랙은 사라져 확정되지 않음
    tr2 = IoUTracker(VisionConfig())
    tr2.update([d], 0.0)
    tr2.update([], 0.5)          # 미확정 트랙은 한 번 놓치면 폐기
    active, _, _ = tr2.update([d], 1.0)
    assert active == []


# 확정 트랙은 잠시 안 보여도 10초 동안 유지(인원 수 유지)되고, 넘으면 제거되는지 확인
def test_tracker_lost_buffer_keeps_count():
    tr = IoUTracker(VisionConfig())
    d = Detection(0, 0, 40, 90, 0.9)
    # 3프레임 연속 검출로 트랙 확정
    for i in range(3):
        tr.update([d], i * 0.5)
    active, _, removed = tr.update([], 9.0)      # 10초 이내
    assert len(active) == 1 and removed == []
    active, _, removed = tr.update([], 12.0)     # 10초 초과
    assert active == [] and len(removed) == 1


# ---------------- V7 / V9 ----------------
# V7 Hampel 필터: 인원 스파이크(2→6)는 중앙값으로 대체, 1명 정도 변화는 그대로 통과하는지 확인
def test_hampel_replaces_spike():
    # 창 크기 7, 임계 배수 k=3, 최소 스케일 0.5
    h = StreamingHampel(7, 3.0, 0.5)
    # 평소 2명이 계속 관측되다가
    for _ in range(6):
        h.push(2)
    # 갑자기 6명 → 이상치로 판단해 2로 대체
    val, outlier = h.push(6)
    assert outlier and val == 2
    val, outlier = h.push(3)      # 1명 차이는 통과
    assert not outlier and val == 3


# 비대칭 디바운스: 입실은 30초, 공실은 300초 동안 유지돼야 상태가 바뀌는지 확인 (10초 간격 입력)
def test_asymmetric_debounce():
    sm = OccupancyStateMachine(OccupancyConfig())
    t = 0.0
    for _ in range(3):
        sm.update(t, 1); t += 10
    assert sm.state is OccState.VACANT       # 20초: 아직 입실 미확정
    sm.update(t, 1); t += 10
    assert sm.state is OccState.OCCUPIED     # 30초
    # 이후 0명이 계속 들어와도 300초가 되기 전까지는 재실 유지
    for _ in range(30):
        sm.update(t, 0); t += 10
    assert sm.state is OccState.OCCUPIED     # 290초: 아직 공실 미확정
    sm.update(t, 0)
    assert sm.state is OccState.VACANT       # 300초


# 카메라 결측(None)이 계속돼도 공실로 바꾸지 않고 재실 유지 + 카메라 고장 표시하는지 확인
def test_missing_never_vacates():
    # 40초 동안 1명 → 재실 확정
    sm = OccupancyStateMachine(OccupancyConfig())
    for i in range(4):
        sm.update(i * 10.0, 1)
    out = None
    # 이후 약 21분간 카메라 값 없음(None)
    for i in range(4, 130):
        out = sm.update(i * 10.0, None)
    assert out.state is OccState.OCCUPIED and out.camera_fault


# V9 단계 이산화: 올라갈 땐 즉시, 내려갈 땐 여유(1명)를 두고 120초 유지돼야 하강하는지 확인
def test_level_hysteresis():
    # 단계 경계 (1명, 3명), 여유 1명, 유지 시간 120초
    lv = LevelDiscretizer((1, 3), 1, 120)
    # 3명 → 단계 2
    assert lv.update(0, 3, True) == 2
    assert lv.update(10, 2, True) == 2       # 2명: 하한-여유(2) 미만 아님 → 유지
    # 20초부터 1명으로 감소
    lv.update(20, 1, True)
    assert lv.update(130, 1, True) == 2      # 110초 → 아직 유지
    assert lv.update(140, 1, True) == 1      # 120초 → 하강


# ---------------- S1~S6 ----------------
# 온습도 정제: 범위 밖 값·급변 값 거부, 결측 시 직전값 유지→보간, 복구 후 사후 보간, 장기 결측 시 무효 처리 확인
def test_climate_range_rate_and_backfill():
    # 10초 격자로 처리하는 온습도 처리기
    cp = ClimateProcessor(ClimateConfig(), 10.0)
    # 0~90초 동안 정상값 25℃ 입력
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
    # 온도 채널 사후 보간값만 골라 끊김 구간(150·200초)이 25.0~25.2 사이로 단조 증가하는지 확인
    bf = [b for b in cp.drain_backfill() if b[1] == "temp_c"]
    assert [g for g, _, _ in bf] == [150.0, 200.0]
    assert 25.0 < bf[0][2] < bf[1][2] < 25.2
    # 오랫동안 값이 없으면 무효(INVALID)
    assert cp.tick(600.0).temp_flag is Flag.INVALID


# 이슬점 계산: 24℃, 60% → 약 15.8℃
def test_dew_point():
    assert abs(dew_point_c(24.0, 60.0) - 15.8) < 0.2


# ---------------- A2 / A3 ----------------
# A2 명령 확인: 피드백이 없으면 재전송 2회 후 경보·자동 제어 중지, 사용자가 설정을 바꾸면 수동 조작으로 감지하는지 확인
def test_reconciler_retry_alarm_and_manual():
    rc = CommandReconciler(HVACConfig())
    # 초기 상태(꺼짐)를 피드백으로 받고, 10초에 '냉방 24℃' 명령 전송
    rc.on_feedback(HVACState(False), 0)
    cmd = HVACState(True, "cool", 24.0)
    rc.on_command_sent(cmd, 10)
    # 확인 대기 시간(30초) 전에는 아무 이벤트 없음
    assert rc.check_timeouts(39) == []
    # 40·70·100초에 차례로 재전송, 재전송, 경보 → 자동 제어 비활성화
    kinds = [e.kind for t in (40, 70, 100) for e in rc.check_timeouts(t)]
    assert kinds == ["retry", "retry", "alarm"] and not rc.auto_enabled(101)
    # 명령이 반영된 뒤 누군가 22℃로 바꾸면 수동 조작으로 판단하고 자동 제어 보류
    rc.on_feedback(HVACState(True, "cool", 24.0), 200)
    ev = rc.on_feedback(HVACState(True, "cool", 22.0), 210)
    assert ev[0].kind == "manual_override" and rc.in_manual_hold(220)


# A3 데드타임 추정: 120초부터 온도가 0.002℃/초씩 내려가면 0.2℃ 하강까지 약 220초가 걸리는지 확인
def test_dead_time():
    series = [(t, 27.0 - max(0, t - 120) * 0.002) for t in range(0, 1000, 10)]
    lag = estimate_dead_time([0.0], series)
    assert 200 <= lag <= 230


# ---------------- P1 ----------------
# P1 출력 보호: 최소 OFF 시간, 설정온도 변화율 제한, 쾌적 방향 즉시 반영, Deadband, 수동 조작 보류 확인
def test_guard_min_off_and_rate_limit():
    g = ControlGuard(ControlConfig())
    on24, off = HVACState(True, "cool", 24.0), HVACState(False, "cool", 24.0)
    # 0초에 에어컨이 꺼졌다고 기록
    g.observe(on24, off, 0.0)
    # 100초 만에 다시 켜려 하면 최소 OFF 시간(min_off) 때문에 거부
    cmd, notes = g.apply(on24, off, 100.0, False)
    assert cmd is None and "min_off" in notes
    # 301초(최소 OFF 시간 경과)에는 켜기 허용
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


# pytest 없이 직접 실행할 때: test_로 시작하는 함수를 이름순으로 모두 실행
if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
    print(f"{len(fns)} passed")
