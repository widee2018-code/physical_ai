"""실제 엣지 장비 실행 예시 (Raspberry Pi 5 + 카메라 기준 골격).

표시된 TODO 부분을 현장 장비에 맞게 구현한다.
기본값은 그림자 모드(shadow_mode=True)라서 판단만 기록하고 에어컨에 명령을 보내지 않는다.
"""
import json
import logging
import os
import sys
import time

# 상위 폴더를 import 경로에 추가해 occucool 패키지를 불러올 수 있게 함
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2

from occucool import OccuCoolPipeline, PipelineConfig
from occucool.hvac import HVACState
from occucool.vision import UltralyticsDetector, lock_camera_exposure


# 에어컨 명령 송신기: 파이프라인이 send()로 HVACState 명령을 넘겨준다
class MyIR:
    def send(self, command: HVACState) -> None:
        # TODO: IR 블래스터 / 유선 리모컨 단자 / 제조사 API로 명령 전송
        logging.info("IR send: %s", command.describe())


# 온습도 센서 값 읽기: (온도℃, 습도%) 튜플 또는 None 반환
def read_climate():
    # TODO: Wi-Fi 온습도 센서 수신 (MQTT 등). 새 값이 없으면 None 반환
    return None


# 에어컨 현재 상태(HVACState) 읽기: 피드백이 없으면 None 반환
def read_hvac_feedback():
    # TODO: 상태 피드백 경로 확인 필요 (보고서 7장: IR 단방향 vs 피드백 모순)
    return None


# 에어컨 소비전력(W) 읽기
def read_power_w():
    # TODO: CT 센서
    return None


# 파이프라인 결과 콜백: 제어 판단 레코드만 JSON 한 줄로 출력
def log_sink(kind, obj):
    if kind == "control":
        ci = obj.inputs
        print(json.dumps({"ts": obj.ts, "occupied": ci.occupied, "level": ci.level, "temp": ci.temp_c,
                          "reason": obj.decision.reason, "cmd": obj.command.describe() if obj.command else None,
                          "notes": obj.notes, "shadow": obj.shadow}, ensure_ascii=False))


# 현장 장비에서 파이프라인을 실행하는 메인 루프
def main():
    logging.basicConfig(level=logging.INFO)
    cfg = PipelineConfig()
    # TODO: 현장 캘리브레이션 값 입력
    # cfg.vision.homography = [[...], [...], [...]]    # 바닥 격자 4점 이상으로 cv2.findHomography
    # cfg.vision.entrance_zone = [(x, y), ...]          # 바닥 좌표(m)
    # cfg.vision.far_tiles = [(x0, y0, x1, y1)]         # 사선 설치 시 원거리 영역
    # cfg.climate.temp_offset_c = -0.4                  # 24시간 기준 온도계 비교 결과
    # cfg.control.shadow_mode = False                   # 그림자 모드 검증 후 해제

    # YOLO 사람 검출기와 IR 송신기를 연결해 파이프라인 생성
    pipe = OccuCoolPipeline(cfg, UltralyticsDetector("yolo11n.pt", imgsz=cfg.vision.model_input_size),
                            transmitter=MyIR(), feedback_available=True, sink=log_sink)
    # 카메라 열기 (1920×1080) 후 자동 노출 고정 시도
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    if cfg.vision.lock_exposure and not lock_camera_exposure(cap):
        logging.warning("노출 고정 실패: 카메라 백엔드 설정 확인 필요")

    # 마지막으로 전력을 읽은 시각
    last_power = 0.0
    # 약 20Hz로 반복: 카메라 → 센서 입력 → 파이프라인 주기 처리
    while True:
        now = time.time()
        ok, frame = cap.read()
        if ok:
            pipe.on_frame(frame, now)      # 프레임은 메모리에서만 처리
        del frame                           # 저장·전송 금지 (조사표 10-1)

        # 온습도·에어컨 피드백은 새 값이 있을 때만 입력
        c = read_climate()
        if c is not None:
            pipe.on_climate(now, *c)
        fb = read_hvac_feedback()
        if fb is not None:
            pipe.on_hvac_feedback(now, fb)
        # 전력은 1초마다 입력
        if now - last_power >= 1.0:
            pipe.on_power(now, read_power_w())
            last_power = now

        # 주기 처리(집계·제어 판단) 후 50ms 대기
        pipe.tick(now)
        time.sleep(0.05)


if __name__ == "__main__":
    main()
