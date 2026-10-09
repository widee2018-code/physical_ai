"""OccuCool 파이프라인 시뮬레이션 (카메라·센서 없이 실행).

현장 조사 결과를 반영한 가상 시나리오:
  100㎡급 공간, 천장 카세트 1대(IR), 온습도 센서 1개(Wi-Fi), 최대 5명, 출입구 1개.

시나리오 (2026-07-15 수, KST):
  08:30 예냉 시작 / 09:00 운영 시작
  09:05 A 입실 (11:00 퇴실, 10:50~10:56 사각지대에 들어감 → 위험 1위)
  09:20, 09:21 B·C 입실 (10:00, 10:01 퇴실) → 3명 이상 → 단계 2
  09:40 D 입실 (10:10 퇴실, 09:50~10:05 사각지대)
  09:30 온도 85℃ 오류값, 09:45 +3℃ 스파이크, 10:30~10:33 Wi-Fi 끊김
  10:20 누군가 리모컨으로 22℃ 설정 (수동 조작 → 60분 자동 제어 보류)
  IR 명령은 10% 확률로 유실 (→ 재전송)
"""
from __future__ import annotations

import logging
import math
import random
from datetime import datetime
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np

from occucool import OccuCoolPipeline, PipelineConfig
from occucool.hvac import HVACState
from occucool.vision import Detection

# 시뮬레이션 공통 상수
KST = ZoneInfo("Asia/Seoul")
SCALE = 0.02                  # 1px = 2cm (Homography: 이미지 → 바닥 m)
IMG_W, IMG_H = 640, 360       # 바닥 12.8m × 7.2m ≈ 92㎡
ENTRANCE = (0.5, 3.6)  # 출입구 바닥 좌표(m)
rng = random.Random(7)  # 시드 고정 난수 → 실행할 때마다 같은 결과
LOST_IR = {3}                 # 3번째 IR 전송은 반드시 유실 → A2 재전송 시연


# 'HH:MM' 문자열을 시나리오 날짜(2026-07-15, KST)의 유닉스 타임스탬프(초)로 변환
def at(hhmm: str) -> float:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 7, 15, h, m, tzinfo=KST).timestamp()


# 타임스탬프(초)를 'HH:MM:SS' 형식의 KST 시각 문자열로 변환 (로그 출력용)
def hm(ts: float) -> str:
    return datetime.fromtimestamp(ts, KST).strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# 가상 세계
# ---------------------------------------------------------------------------
# 가상 재실자 한 명: 입실·퇴실 시각, 좌석 위치(m), 카메라 사각지대 구간을 가진다
class SimPerson:
    def __init__(self, name, enter, leave, seat, hidden=()):
        # 시각 문자열은 모두 타임스탬프(초)로 바꿔 저장
        self.name, self.enter, self.leave, self.seat = name, at(enter), at(leave), seat
        self.hidden = [(at(a), at(b)) for a, b in hidden]
        # 출입구→좌석 이동 시간(초) = 거리(m) / 보행 속도 0.6m/s
        self.walk_s = math.dist(ENTRANCE, seat) / 0.6

    # 시각 t에 이 사람이 바닥 어디(m)에 있는지 반환, 방 밖이면 None
    def floor_pos(self, t: float) -> Optional[Tuple[float, float]]:
        # 입실 전이거나, 퇴실 후 출입구까지 다 걸어 나간 뒤 → 방에 없음
        if t < self.enter or t > self.leave + self.walk_s:
            return None
        # 입실 직후: 출입구에서 좌석까지 직선 이동 중 (f = 진행 비율 0~1)
        if t < self.enter + self.walk_s:
            f = (t - self.enter) / self.walk_s
            return (ENTRANCE[0] + (self.seat[0] - ENTRANCE[0]) * f, ENTRANCE[1] + (self.seat[1] - ENTRANCE[1]) * f)
        # 좌석에 앉아 있는 중
        if t <= self.leave:
            return self.seat
        # 퇴실 중: 좌석에서 출입구로 걸어 나가는 중
        f = (t - self.leave) / self.walk_s
        return (self.seat[0] + (ENTRANCE[0] - self.seat[0]) * f, self.seat[1] + (ENTRANCE[1] - self.seat[1]) * f)

    # 시각 t에 카메라에 보이는지 여부 (사각지대 구간이면 False)
    def visible(self, t: float) -> bool:
        return not any(a <= t < b for a, b in self.hidden)


# 시나리오 등장인물 4명: (이름, 입실, 퇴실, 좌석 좌표 m, 사각지대 구간)
PEOPLE = [
    SimPerson("A", "09:05", "11:00", (8.0, 2.0), hidden=[("10:50", "10:56")]),
    SimPerson("B", "09:20", "10:00", (3.0, 5.0)),
    SimPerson("C", "09:21", "10:01", (4.0, 5.6)),
    SimPerson("D", "09:40", "10:10", (10.0, 5.0), hidden=[("09:50", "10:05")]),
]


# 시각 t에 실제로 방 안에 있는 사람 수 (정답값, 정확도 평가용)
def truth_count(t: float) -> int:
    return sum(1 for p in PEOPLE if p.floor_pos(t) is not None)


# 시뮬레이션 현재 시각을 가짜 검출기·IR 송신기와 공유하기 위한 전역 시계
class Clock:
    t = 0.0


class FakeDetector:
    """사람 검출기 대역: 15% 미검출, 좌표 잡음, 저신뢰·소형·단발 오검출 포함."""

    def detect(self, image: np.ndarray) -> List[Detection]:
        # 방 안에 있고 카메라에 보이는 사람마다 검출 박스 생성 (15% 확률로 일부러 놓침)
        t = Clock.t
        dets = []
        for p in PEOPLE:
            pos = p.floor_pos(t)
            if pos is None or not p.visible(t) or rng.random() < 0.15:
                continue
            # 바닥 좌표(m) → 이미지 픽셀로 변환 + ±2px 잡음, 박스는 발 위치(u, v) 기준 40×90px
            u, v = pos[0] / SCALE + rng.gauss(0, 2), pos[1] / SCALE + rng.gauss(0, 2)
            dets.append(Detection(u - 20, v - 90, u + 20, v, rng.uniform(0.55, 0.95)))
        # 아래는 일부러 섞는 오검출 (후처리·트래커 필터가 걸러내는지 확인용)
        if rng.random() < 0.03:   # 저신뢰 오검출 → V4 신뢰도 필터
            dets.append(Detection(300, 100, 340, 190, 0.25))
        if rng.random() < 0.02:   # 작은 고신뢰 오검출 → V4 최소 크기 필터
            dets.append(Detection(500, 300, 510, 315, 0.8))
        if rng.random() < 0.005:  # 단발 정상크기 오검출 → V5 트랙 확정 조건
            x, y = rng.uniform(100, 600), rng.uniform(100, 350)
            dets.append(Detection(x - 20, y - 90, x + 20, y, 0.6))
        return dets


def render_frame(t: float) -> np.ndarray:
    """움직임 감지(V2)용 가상 프레임. 실제 영상이 아니라 사람 위치에 사각형만 그린다."""
    # 검은 화면에서 보이는 사람 위치마다 밝은 사각형(값 200)을 칠한다
    frame = np.zeros((IMG_H, IMG_W, 3), np.uint8)
    for p in PEOPLE:
        pos = p.floor_pos(t)
        if pos is not None and p.visible(t):
            u, v = int(pos[0] / SCALE), int(pos[1] / SCALE)
            frame[max(v - 90, 0):v, max(u - 20, 0):u + 20] = 200
    return frame


class SimAC:
    """천장형 카세트 대역: 내부 온도조절 + IR 유실 10% + 1초 반영 지연."""

    def __init__(self):
        # 초기 상태: 전원 꺼짐, 냉방 모드, 설정 24℃
        self.state = HVACState(False, "cool", 24.0)
        self.compressor = False  # 압축기(실외기) 가동 여부
        self.queue: List[Tuple[float, HVACState]] = []  # (반영 시각, 명령) 대기열 → 1초 반영 지연 표현
        # IR 유실/전송 횟수 통계
        self.ir_lost = 0
        self.ir_sent = 0

    # IR 명령 수신: 유실되면 무시하고, 아니면 1초 뒤 반영되도록 대기열에 넣는다
    def receive_ir(self, cmd: HVACState, t: float):
        self.ir_sent += 1
        # 지정된 순번(LOST_IR)이거나 10% 확률이면 유실
        if self.ir_sent in LOST_IR or rng.random() < 0.10:
            self.ir_lost += 1
            return
        self.queue.append((t + 1.0, cmd))

    # 매 스텝 호출: 반영 시각이 된 명령을 적용한 뒤 실내 온도에 따라 압축기 ON/OFF
    def step(self, t: float, room_t: float):
        # 반영 시각이 지난 명령을 실제 에어컨 상태로 적용
        for item in [q for q in self.queue if q[0] <= t]:
            self.state = item[1]
            self.queue.remove(item)
        s = self.state
        # 냉방·제습 모드면 설정온도 ±0.5℃ 히스테리시스로 압축기를 켜고 끈다
        if s.power and s.mode in ("cool", "dry"):
            if room_t > s.setpoint + 0.5:
                self.compressor = True
            elif room_t < s.setpoint - 0.5:
                self.compressor = False
        else:
            self.compressor = False

    # 현재 소비전력(W): 꺼짐 1~4W, 송풍만 약 55W, 압축기 가동 약 2800W
    def power_w(self) -> float:
        if not self.state.power:
            return rng.uniform(1, 4)
        return rng.gauss(2800, 50) if self.compressor else rng.gauss(55, 5)


# 파이프라인이 사용하는 IR 송신기 대역: 받은 명령을 SimAC에 그대로 전달
class SimIR:
    def __init__(self, ac: SimAC):
        self.ac = ac

    def send(self, command: HVACState) -> None:
        self.ac.receive_ir(command, Clock.t)


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
# 08:30~11:45를 0.5초 간격으로 시뮬레이션한 뒤 제어 로그와 요약 통계를 출력
def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    # 파이프라인 설정: 바닥 좌표 변환, 구역 A/B(좌우 절반), 출입구 영역, 움직임 감지 영역
    cfg = PipelineConfig()
    cfg.control.shadow_mode = False          # 데모에서는 실제 제어
    v = cfg.vision
    v.homography = [[SCALE, 0, 0], [0, SCALE, 0], [0, 0, 1]]
    v.zones = {"A": [(0, 0), (6.4, 0), (6.4, 7.2), (0, 7.2)], "B": [(6.4, 0), (12.8, 0), (12.8, 7.2), (6.4, 7.2)]}
    v.entrance_zone = [(0, 2.6), (1.2, 2.6), (1.2, 4.6), (0, 4.6)]
    v.motion_region = (0, 40, 60, 230)       # 출입구 이미지 영역

    # 가상 에어컨과 결과 수집용 리스트
    ac = SimAC()
    grid_log, controls, events, backfills = [], [], [], []

    # 파이프라인이 내보내는 결과를 종류별로 모으는 콜백 (grid 레코드는 정답 인원수와 함께 저장)
    def sink(kind, obj):
        if kind == "grid":
            grid_log.append((obj, truth_count(obj.ts)))
        elif kind == "control":
            controls.append(obj)
        elif kind == "hvac_event":
            events.append(obj)
        elif kind == "backfill":
            backfills.append(obj)

    # 파이프라인 생성: 가짜 검출기·가짜 IR 송신기 연결, 에어컨 상태 피드백 사용
    pipe = OccuCoolPipeline(cfg, FakeDetector(), SimIR(ac), feedback_available=True, sink=sink)

    # 시뮬레이션 구간(초)·간격 0.5초, 초기 실내 온도 27.5℃·습도 52%
    t0, t_end, dt = at("08:30"), at("11:45"), 0.5
    room_t, rh = 27.5, 52.0
    # 압축기가 켜진 시각 (냉방 효과 지연 계산용)
    comp_on_since: Optional[float] = None
    manual_done = False
    t = t0
    step = 0
    # 메인 루프: 0.5초씩 시간을 진행하며 물리 → 센서 입력 → 파이프라인 처리
    while t < t_end:
        Clock.t = t
        # --- 가상 물리 ---
        # 외기 온도는 29℃에서 33℃까지 선형 상승
        tout = 29.0 + 4.0 * (t - t0) / (t_end - t0)
        n = truth_count(t)
        # 에어컨 상태 갱신 후 압축기 연속 가동 시작 시각을 추적
        ac.step(t, room_t)
        if ac.compressor:
            comp_on_since = comp_on_since if comp_on_since is not None else t
        else:
            comp_on_since = None
        effective = comp_on_since is not None and t - comp_on_since >= 90.0    # 데드타임 90초
        # 압축기가 90초 이상 돌아야 냉방 효과 발생 (제습 모드는 냉각량 절반)
        cool_rate = (0.0006 if ac.state.mode == "dry" else 0.0012) if effective else 0.0
        # 실내 온도 변화 = 외기 유입(시정수 3시간) + 사람 발열 - 냉방
        # 습도도 같은 방식으로 계산하고 20~90% 범위로 제한
        room_t += dt * ((tout - room_t) / 10800.0 + n * 0.00004 - cool_rate)
        rh += dt * ((52.0 - rh) / 3600.0 + n * 0.0004 - (0.0015 if effective else 0.0))
        rh = min(max(rh, 20.0), 90.0)

        # 10:20 리모컨으로 22℃ 설정 → 파이프라인이 수동 조작으로 감지해야 함
        if not manual_done and t >= at("10:20"):     # 리모컨 수동 조작
            ac.state = HVACState(True, "cool", 22.0)
            manual_done = True

        # --- 센서 입력 ---
        # 영상은 매 스텝(0.5초)마다 입력
        pipe.on_frame(render_frame(t), t)
        # 전력은 1초마다, 에어컨 상태 피드백은 5초마다 입력
        if step % 2 == 0:
            pipe.on_power(t, ac.power_w())
        if step % 10 == 0:
            pipe.on_hvac_feedback(t, ac.state)
        # 온습도는 10초마다 입력 (Wi-Fi 끊김·오류값·스파이크 시나리오 포함)
        if step % 20 == 6:
            if at("10:30") <= t < at("10:33"):
                pass                                      # Wi-Fi 끊김
            elif abs(t - at("09:30")) < 5:
                pipe.on_climate(t, 85.0, rh)              # 범위 밖 오류값
            elif abs(t - at("09:45")) < 5:
                pipe.on_climate(t, room_t + 3.0, rh)      # 스파이크
            else:
                pipe.on_climate(t, room_t + rng.gauss(0, 0.03), rh + rng.gauss(0, 0.3))
        # 외기 온도는 60초마다 입력
        if step % 120 == 0:
            pipe.on_outdoor(t, tout + rng.gauss(0, 0.1))

        # 파이프라인 주기 처리(10초 집계·제어 판단) 후 시간 진행
        pipe.tick(t)
        t += dt
        step += 1

    # ------------------------------------------------------------------ 출력
    # 제어 로그 표 헤더 출력
    print("=" * 96)
    print("제어 로그 (명령이 나갔거나 보호 로직이 개입한 시점)")
    print("=" * 96)
    print(f"{'시각':8} {'재실':4} {'단계':2} {'최대인원':>4} {'실내℃':>6} {'RH%':>5} {'스케줄':9} {'판단':22} {'명령':20} 비고")
    # 명령이나 보호 로직(notes)이 있는 시점만 출력, 같은 보호 로직 반복은 묶어서 횟수만 표시
    prev_notes, repeat = None, 0
    for o in controls:
        if not (o.command or o.notes):
            prev_notes = None
            continue
        if o.command is None and o.notes == prev_notes:
            repeat += 1
            continue
        if repeat:
            print(f"{'':8} ... 같은 보호 로직 {repeat}분 더 지속")
            repeat = 0
        prev_notes = o.notes
        # 한 줄 출력용 값 정리 (값이 없으면 '-')
        ci = o.inputs
        cmd = o.command.describe() if o.command else "-"
        temp = f"{ci.temp_c:.2f}" if ci.temp_c is not None else "-"
        rhs = f"{ci.rh_pct:.1f}" if ci.rh_pct is not None else "-"
        print(f"{hm(o.ts):8} {'O' if ci.occupied else '-':4} {ci.level:2} {str(ci.max_count):>4} {temp:>6} {rhs:>5} "
              f"{ci.schedule:9} {o.decision.reason:22} {cmd:20} {','.join(o.notes)}")

    # 반복 구간에서 루프가 끝났으면 남은 반복 횟수 출력
    if repeat:
        print(f"{'':8} ... 같은 보호 로직 {repeat}분 더 지속")
    # 에어컨 이벤트 중 정상 확인(confirmed)을 뺀 재전송·경보·수동 조작 등만 출력
    print("\n에어컨 이벤트 (A2)")
    for e in events:
        if e.kind != "confirmed":
            print(f"  {hm(e.ts)} {e.kind:16} {e.detail}")

    # 정확도 지표: 10초 카운트 평균 절대 오차(MAE), 공실 오판정·재실 오판정 레코드 수
    # (공실 오판정은 첫 입실 확정 지연을 감안해 09:06 이후만 집계)
    recs = [r for r, _ in grid_log]
    valid = [(r.count, tr) for r, tr in grid_log if r.count is not None]
    mae = sum(abs(c - tr) for c, tr in valid) / len(valid)
    false_vacant = sum(1 for r, tr in grid_log if tr > 0 and not r.occupancy.occupied and r.ts > at("09:06"))
    stuck = sum(1 for r, tr in grid_log if tr == 0 and r.occupancy.occupied)
    # 요약 통계 출력 (레코드 1개 = 10초)
    print("\n요약")
    print(f"  영상: 추론 프레임 {pipe.vision.frames_inferred}개 (입력 {step}개, 적응형 샘플링)")
    print(f"  인원: 10초 카운트 MAE {mae:.2f}명 | 재실자가 있는데 공실 판정 {false_vacant * 10}초 "
          f"| 무인인데 재실 판정 {stuck * 10}초 (공실 확정 지연 포함)")
    print(f"  온습도 정제 통계: {pipe.climate.stats}")
    print(f"  Wi-Fi 결측 사후 보간(backfill) 레코드: {len(backfills)}개")
    print(f"  에너지: {pipe.energy.total_wh / 1000:.2f} kWh | 압축기 기동 {pipe.energy.compressor_starts}회 "
          f"| 추정 분 {pipe.energy.estimated_minutes}")
    print(f"  IR: 전송 {ac.ir_sent}회, 유실 {ac.ir_lost}회 | 안전 모드 레코드 {sum(r.safe_mode for r in recs)}개")


if __name__ == "__main__":
    main()
