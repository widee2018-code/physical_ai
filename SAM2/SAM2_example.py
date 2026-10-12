"""SAM 2 (Segment Anything Model 2) 프롬프트 방식 간단 시연.

SAM은 "무엇(클래스)"을 찾는 모델이 아니라, "어디"를 알려주면 그 물체의
정확한 윤곽(마스크)을 잘라내는 모델입니다. 그래서 프롬프트가 위치 정보입니다.

1) Point prompt      : 물체 위의 점 하나를 클릭 → 그 물체의 마스크
2) Positive/Negative : 포함할 점(1) + 제외할 점(0)으로 마스크를 다듬기
3) Box prompt        : 박스로 범위를 지정 → 박스 안 물체의 마스크
4) Segment everything: 프롬프트 없이 이미지 전체를 자동 분할
   (마스크만 나오고 클래스 이름은 없음 → YOLOE 와의 차이점)

실행: python SAM2_example.py
결과 이미지는 ./outputs 폴더에 저장됩니다.
"""

from pathlib import Path

import cv2
from ultralytics import SAM
from ultralytics.utils import ASSETS

HERE = Path(__file__).parent
OUT_DIR = HERE / "outputs"
OUT_DIR.mkdir(exist_ok=True)

BUS = str(ASSETS / "bus.jpg")  # ultralytics 내장 예제 이미지 (810x1080, 버스 + 사람들)

# SAM 2.1 tiny 모델 (처음 실행 시 자동 다운로드, ~80MB)
# 더 정확한 모델: sam2.1_s.pt / sam2.1_b.pt / sam2.1_l.pt
model = SAM(str(HERE / "sam2.1_t.pt"))


def show(result, name, points=None, labels=None):
    """마스크 개수/면적을 출력하고, 프롬프트 점을 함께 그려 저장."""
    masks = result.masks
    n = 0 if masks is None else len(masks)
    areas = [] if masks is None else [int(m.sum()) for m in masks.data]
    print(f"  마스크 {n}개, 면적(픽셀): {areas[:10]}{' ...' if n > 10 else ''}")

    img = result.plot(labels=False, boxes=False)  # 마스크만 칠해서 보기
    for (x, y), lb in zip(points or [], labels or []):
        color = (0, 255, 0) if lb == 1 else (0, 0, 255)  # 초록=포함, 빨강=제외
        cv2.circle(img, (x, y), 12, color, -1)
        cv2.circle(img, (x, y), 12, (255, 255, 255), 2)

    path = OUT_DIR / f"{name}.jpg"
    cv2.imwrite(str(path), img)
    print(f"  저장: {path}")


def point_prompt_demo():
    print("\n[1] Point prompt: 점 하나 → 그 물체")
    pts, lbs = [[150, 600]], [1]  # 왼쪽 사람의 몸통 위 한 점
    result = model(BUS, points=pts, labels=lbs, verbose=False)[0]
    show(result, "1_point", pts, lbs)


def pos_neg_points_demo():
    print("\n[2] Positive/Negative points: 포함 점 + 제외 점")
    # 버스 위 두 점(포함) + 버스 앞 사람 위 한 점(제외)
    # 바깥 [] 하나 = 물체 하나. 여러 점이 같은 물체를 설명함
    pts, lbs = [[400, 350], [700, 600], [150, 600]], [1, 1, 0]
    result = model(BUS, points=[pts], labels=[lbs], verbose=False)[0]
    show(result, "2_pos_neg_points", pts, lbs)


def box_prompt_demo():
    print("\n[3] Box prompt: 박스 [x1, y1, x2, y2] → 박스 안 물체")
    boxes = [
        [48, 398, 245, 902],  # 왼쪽 사람
        [670, 380, 810, 875],  # 오른쪽 사람
    ]
    result = model(BUS, bboxes=boxes, verbose=False)[0]  # 박스마다 마스크 1개
    show(result, "3_box")


def everything_demo():
    print("\n[4] Segment everything: 프롬프트 없이 전체 자동 분할 (조금 느림)")
    result = model(BUS, verbose=False)[0]
    show(result, "4_everything")


if __name__ == "__main__":
    point_prompt_demo()
    pos_neg_points_demo()
    box_prompt_demo()
    everything_demo()
