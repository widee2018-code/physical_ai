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

KST = ZoneInfo("Asia/Seoul")
SCALE = 0.02                  # 1px = 2cm (Homography: 이미지 → 바닥 m)
IMG_W, IMG_H = 640, 360       # 바닥 12.8m × 7.2m ≈ 92㎡
ENTRANCE = (0.5, 3.6)
rng = random.Random(7)
LOST_IR = {3}                 # 3번째 IR 전송은 반드시 유실 → A2 재전송 시연


def at(hhmm: str) -> float:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 7, 15, h, m, tzinfo=KST).timestamp()


def hm(ts: float) -> str:
    return datetime.fromtimestamp(ts, KST).strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# 가상 세계
# ---------------------------------------------------------------------------
class SimPerson:
    def __init__(self, name, enter, leave, seat, hidden=()):
        self.name, self.enter, self.leave, self.seat = name, at(enter), at(leave), seat
        self.hidden = [(at(a), at(b)) for a, b in hidden]
        self.walk_s = math.dist(ENTRANCE, seat) / 0.6

    def floor_pos(self, t: float) -> Optional[Tuple[float, float]]:
        if t < self.enter or t > self.leave + self.walk_s:
            return None
        if t < self.enter + self.walk_s:
            f = (t - self.enter) / self.walk_s
            return (ENTRANCE[0] + (self.seat[0] - ENTRANCE[0]) * f, ENTRANCE[1] + (self.seat[1] - ENTRANCE[1]) * f)
        if t <= self.leave:
            return self.seat
        f = (t - self.leave) / self.walk_s
        return (self.seat[0] + (ENTRANCE[0] - self.seat[0]) * f, self.seat[1] + (ENTRANCE[1] - self.seat[1]) * f)

    def visible(self, t: float) -> bool:
        return not any(a <= t < b for a, b in self.hidden)


PEOPLE = [
    SimPerson("A", "09:05", "11:00", (8.0, 2.0), hidden=[("10:50", "10:56")]),
    SimPerson("B", "09:20", "10:00", (3.0, 5.0)),
    SimPerson("C", "09:21", "10:01", (4.0, 5.6)),
    SimPerson("D", "09:40", "10:10", (10.0, 5.0), hidden=[("09:50", "10:05")]),
]


def truth_count(t: float) -> int:
    return sum(1 for p in PEOPLE if p.floor_pos(t) is not None)


class Clock:
    t = 0.0


class FakeDetector:
    """사람 검출기 대역: 15% 미검출, 좌표 잡음, 저신뢰·소형·단발 오검출 포함."""

    def detect(self, image: np.ndarray) -> List[Detection]:
        t = Clock.t
        dets = []
        for p in PEOPLE:
            pos = p.floor_pos(t)
            if pos is None or not p.visible(t) or rng.random() < 0.15:
                continue
            u, v = pos[0] / SCALE + rng.gauss(0, 2), pos[1] / SCALE + rng.gauss(0, 2)
            dets.append(Detection(u - 20, v - 90, u + 20, v, rng.uniform(0.55, 0.95)))
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
        self.state = HVACState(False, "cool", 24.0)
        self.compressor = False
        self.queue: List[Tuple[float, HVACState]] = []
        self.ir_lost = 0
        self.ir_sent = 0

    def receive_ir(self, cmd: HVACState, t: float):
        self.ir_sent += 1
        if self.ir_sent in LOST_IR or rng.random() < 0.10:
            self.ir_lost += 1
            return
        self.queue.append((t + 1.0, cmd))

    def step(self, t: float, room_t: float):
        for item in [q for q in self.queue if q[0] <= t]:
            self.state = item[1]
            self.queue.remove(item)
        s = self.state
        if s.power and s.mode in ("cool", "dry"):
            if room_t > s.setpoint + 0.5:
                self.compressor = True
            elif room_t < s.setpoint - 0.5:
                self.compressor = False
        else:
            self.compressor = False

    def power_w(self) -> float:
        if not self.state.power:
            return rng.uniform(1, 4)
        return rng.gauss(2800, 50) if self.compressor else rng.gauss(55, 5)


class SimIR:
    def __init__(self, ac: SimAC):
        self.ac = ac

    def send(self, command: HVACState) -> None:
        self.ac.receive_ir(command, Clock.t)


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    cfg = PipelineConfig()
    cfg.control.shadow_mode = False          # 데모에서는 실제 제어
    v = cfg.vision
    v.homography = [[SCALE, 0, 0], [0, SCALE, 0], [0, 0, 1]]
    v.zones = {"A": [(0, 0), (6.4, 0), (6.4, 7.2), (0, 7.2)], "B": [(6.4, 0), (12.8, 0), (12.8, 7.2), (6.4, 7.2)]}
    v.entrance_zone = [(0, 2.6), (1.2, 2.6), (1.2, 4.6), (0, 4.6)]
    v.motion_region = (0, 40, 60, 230)       # 출입구 이미지 영역

    ac = SimAC()
    grid_log, controls, events, backfills = [], [], [], []

    def sink(kind, obj):
        if kind == "grid":
            grid_log.append((obj, truth_count(obj.ts)))
        elif kind == "control":
            controls.append(obj)
        elif kind == "hvac_event":
            events.append(obj)
        elif kind == "backfill":
            backfills.append(obj)

    pipe = OccuCoolPipeline(cfg, FakeDetector(), SimIR(ac), feedback_available=True, sink=sink)

    t0, t_end, dt = at("08:30"), at("11:45"), 0.5
    room_t, rh = 27.5, 52.0
    comp_on_since: Optional[float] = None
    manual_done = False
    t = t0
    step = 0
    while t < t_end:
        Clock.t = t
        # --- 가상 물리 ---
        tout = 29.0 + 4.0 * (t - t0) / (t_end - t0)
        n = truth_count(t)
        ac.step(t, room_t)
        if ac.compressor:
            comp_on_since = comp_on_since if comp_on_since is not None else t
        else:
            comp_on_since = None
        effective = comp_on_since is not None and t - comp_on_since >= 90.0    # 데드타임 90초
        cool_rate = (0.0006 if ac.state.mode == "dry" else 0.0012) if effective else 0.0
        room_t += dt * ((tout - room_t) / 10800.0 + n * 0.00004 - cool_rate)
        rh += dt * ((52.0 - rh) / 3600.0 + n * 0.0004 - (0.0015 if effective else 0.0))
        rh = min(max(rh, 20.0), 90.0)

        if not manual_done and t >= at("10:20"):     # 리모컨 수동 조작
            ac.state = HVACState(True, "cool", 22.0)
            manual_done = True

        # --- 센서 입력 ---
        pipe.on_frame(render_frame(t), t)
        if step % 2 == 0:
            pipe.on_power(t, ac.power_w())
        if step % 10 == 0:
            pipe.on_hvac_feedback(t, ac.state)
        if step % 20 == 6:
            if at("10:30") <= t < at("10:33"):
                pass                                      # Wi-Fi 끊김
            elif abs(t - at("09:30")) < 5:
                pipe.on_climate(t, 85.0, rh)              # 범위 밖 오류값
            elif abs(t - at("09:45")) < 5:
                pipe.on_climate(t, room_t + 3.0, rh)      # 스파이크
            else:
                pipe.on_climate(t, room_t + rng.gauss(0, 0.03), rh + rng.gauss(0, 0.3))
        if step % 120 == 0:
            pipe.on_outdoor(t, tout + rng.gauss(0, 0.1))

        pipe.tick(t)
        t += dt
        step += 1

    # ------------------------------------------------------------------ 출력
    print("=" * 96)
    print("제어 로그 (명령이 나갔거나 보호 로직이 개입한 시점)")
    print("=" * 96)
    print(f"{'시각':8} {'재실':4} {'단계':2} {'최대인원':>4} {'실내℃':>6} {'RH%':>5} {'스케줄':9} {'판단':22} {'명령':20} 비고")
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
        ci = o.inputs
        cmd = o.command.describe() if o.command else "-"
        temp = f"{ci.temp_c:.2f}" if ci.temp_c is not None else "-"
        rhs = f"{ci.rh_pct:.1f}" if ci.rh_pct is not None else "-"
        print(f"{hm(o.ts):8} {'O' if ci.occupied else '-':4} {ci.level:2} {str(ci.max_count):>4} {temp:>6} {rhs:>5} "
              f"{ci.schedule:9} {o.decision.reason:22} {cmd:20} {','.join(o.notes)}")

    if repeat:
        print(f"{'':8} ... 같은 보호 로직 {repeat}분 더 지속")
    print("\n에어컨 이벤트 (A2)")
    for e in events:
        if e.kind != "confirmed":
            print(f"  {hm(e.ts)} {e.kind:16} {e.detail}")

    recs = [r for r, _ in grid_log]
    valid = [(r.count, tr) for r, tr in grid_log if r.count is not None]
    mae = sum(abs(c - tr) for c, tr in valid) / len(valid)
    false_vacant = sum(1 for r, tr in grid_log if tr > 0 and not r.occupancy.occupied and r.ts > at("09:06"))
    stuck = sum(1 for r, tr in grid_log if tr == 0 and r.occupancy.occupied)
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
