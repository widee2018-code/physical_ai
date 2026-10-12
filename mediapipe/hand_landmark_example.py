"""MediaPipe Hand Landmarker 시연.

손 21개 키포인트(손목, 손가락 관절/끝)를 검출하고 왼손/오른손을 구분합니다.
실행: python hand_landmark_example.py [이미지 경로]
"""

import sys

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision
from mediapipe.tasks.python.vision import drawing_styles, drawing_utils

from common import MODEL_DIR, SAMPLE_DIR, download, save

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
IMAGE_URL = "https://storage.googleapis.com/mediapipe-tasks/hand_landmarker/woman_hands.jpg"


def main():
    print("[Hand Landmark Detection]")
    model_path = download(MODEL_URL, MODEL_DIR)
    image_path = sys.argv[1] if len(sys.argv) > 1 else download(IMAGE_URL, SAMPLE_DIR)

    options = vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        num_hands=2,
    )
    with vision.HandLandmarker.create_from_options(options) as landmarker:
        result = landmarker.detect(mp.Image.create_from_file(image_path))

    image = cv2.imread(image_path)
    h, w = image.shape[:2]
    print(f"  검출된 손: {len(result.hand_landmarks)}개")
    for landmarks, handedness in zip(result.hand_landmarks, result.handedness):
        drawing_utils.draw_landmarks(
            image,
            landmarks,
            vision.HandLandmarksConnections.HAND_CONNECTIONS,
            drawing_styles.get_default_hand_landmarks_style(),
            drawing_styles.get_default_hand_connections_style(),
        )
        label = f"{handedness[0].category_name} ({handedness[0].score:.2f})"
        x = int(min(lm.x for lm in landmarks) * w)
        y = int(min(lm.y for lm in landmarks) * h) - 10
        cv2.putText(image, label, (x, max(y, 20)), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (88, 205, 54), 2)
        print(f"  - {label}, 검지 끝(8) = ({landmarks[8].x:.3f}, {landmarks[8].y:.3f})")

    save(image, "hand_landmark")


if __name__ == "__main__":
    main()
