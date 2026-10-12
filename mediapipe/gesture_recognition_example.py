"""MediaPipe Gesture Recognizer 시연.

손 랜드마크를 바탕으로 7가지 기본 제스처를 분류합니다.
(Closed_Fist, Open_Palm, Pointing_Up, Thumb_Down, Thumb_Up, Victory, ILoveYou)
실행: python gesture_recognition_example.py [이미지 경로 ...]
"""

import sys

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision
from mediapipe.tasks.python.vision import drawing_styles, drawing_utils

from common import MODEL_DIR, SAMPLE_DIR, download, save

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task"
IMAGE_URLS = [
    f"https://storage.googleapis.com/mediapipe-tasks/gesture_recognizer/{name}.jpg"
    for name in ("thumbs_up", "victory", "pointing_up", "thumbs_down")
]


def annotate(image_path, result):
    image = cv2.imread(image_path)
    if not result.gestures:
        cv2.putText(image, "No hand", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
        return image
    for gestures, landmarks in zip(result.gestures, result.hand_landmarks):
        drawing_utils.draw_landmarks(
            image,
            landmarks,
            vision.HandLandmarksConnections.HAND_CONNECTIONS,
            drawing_styles.get_default_hand_landmarks_style(),
            drawing_styles.get_default_hand_connections_style(),
        )
        top = gestures[0]
        scale = min(image.shape[:2]) / 480  # 이미지 크기와 무관하게 글자 크기가 비슷하도록
        cv2.putText(image, f"{top.category_name} ({top.score:.2f})", (10, int(40 * scale)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0 * scale, (0, 0, 255), max(2, int(3 * scale)))
    return image


def main():
    print("[Gesture Recognition]")
    model_path = download(MODEL_URL, MODEL_DIR)
    image_paths = sys.argv[1:] or [download(u, SAMPLE_DIR) for u in IMAGE_URLS]

    options = vision.GestureRecognizerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        num_hands=2,
    )
    tiles = []
    with vision.GestureRecognizer.create_from_options(options) as recognizer:
        for path in image_paths:
            result = recognizer.recognize(mp.Image.create_from_file(path))
            names = [f"{g[0].category_name}({g[0].score:.2f})" for g in result.gestures]
            print(f"  {path.split('/')[-1]}: {', '.join(names) or '손 없음'}")
            tile = annotate(path, result)
            tiles.append(tile)

    # 여러 결과를 같은 높이로 맞춰 가로로 이어 붙여 한 장으로 저장
    tiles = [cv2.resize(t, (int(t.shape[1] * 480 / t.shape[0]), 480)) for t in tiles]
    save(np.hstack(tiles), "gesture_recognition")


if __name__ == "__main__":
    main()
