"""YOLOE 세 가지 프롬프트 모드 간단 시연.

1) Text prompt   : 찾고 싶은 클래스 이름을 텍스트로 지정
2) Visual prompt : 이미지 안의 박스(예시 객체)로 "이런 것"을 찾도록 지정
3) Prompt free   : 프롬프트 없이 내장 어휘(4,500+ 클래스)로 모든 객체 탐지

실행: python YOLO_E_example.py
결과 이미지는 ./outputs 폴더에 저장됩니다.
"""

from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLOE
from ultralytics.models.yolo.yoloe import YOLOEVPSegPredictor
from ultralytics.utils import ASSETS

OUT_DIR = Path(__file__).parent / "outputs"
OUT_DIR.mkdir(exist_ok=True)

BUS = str(ASSETS / "bus.jpg")  # ultralytics 내장 예제 이미지 (버스 + 사람들)
ZIDANE = str(ASSETS / "zidane.jpg")  # ultralytics 내장 예제 이미지 (축구 선수 2명)


def show(result, name):
    """탐지 결과를 출력하고 이미지로 저장."""
    names = [result.names[int(c)] for c in result.boxes.cls]
    confs = result.boxes.conf.tolist()
    print(f"  탐지 {len(names)}개: " + ", ".join(f"{n}({c:.2f})" for n, c in zip(names, confs)))
    path = OUT_DIR / f"{name}.jpg"
    cv2.imwrite(str(path), result.plot())
    print(f"  저장: {path}")


def text_prompt_demo():
    print("\n[1] Text prompt 모드")
    model = YOLOE("yoloe-26s-seg.pt")
    classes = ["person wearing a white jacket"] #classes = ["person", "bus", "glasses"]
    model.set_classes(classes)  # 텍스트 임베딩을 계산해 탐지 클래스로 설정
    print(f"  프롬프트: {classes}")
    result = model.predict(BUS, conf=0.25, verbose=False)[0]
    show(result, "1_text_prompt")


def visual_prompt_demo():
    print("\n[2] Visual prompt 모드")
    model = YOLOE("yoloe-26s-seg.pt")

    # bus.jpg 속 사람 한 명을 박스로 지정 → "이런 객체"를 찾아라
    prompts = {
        "bboxes": np.array([[221, 405, 344, 857]]),  # x1, y1, x2, y2
        "cls": np.array([0]),  # 박스마다 클래스 인덱스
    }

    # (a) 같은 이미지에서 찾기
    result = model.predict(BUS, visual_prompts=prompts, predictor=YOLOEVPSegPredictor, verbose=False)[0]
    print("  (a) bus.jpg 속 박스 → 같은 이미지에서 탐색")
    show(result, "2a_visual_prompt_same_image")

    # (b) 참조 이미지(bus.jpg)의 프롬프트로 다른 이미지(zidane.jpg)에서 찾기
    result = model.predict(
        ZIDANE, refer_image=BUS, visual_prompts=prompts, predictor=YOLOEVPSegPredictor, verbose=False
    )[0]
    print("  (b) bus.jpg 속 박스 → zidane.jpg에서 탐색 (refer_image)")
    show(result, "2b_visual_prompt_refer_image")


def prompt_free_demo():
    print("\n[3] Prompt free 모드")
    model = YOLOE("yoloe-26s-seg-pf.pt")  # -pf: 내장 어휘를 쓰는 prompt-free 가중치
    result = model.predict(BUS, conf=0.25, verbose=False)[0]
    show(result, "3_prompt_free")


if __name__ == "__main__":
    text_prompt_demo()
    visual_prompt_demo()
    prompt_free_demo()

