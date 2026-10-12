"""MediaPipe Pose Landmarker 시연.

전신 33개 키포인트(얼굴, 어깨, 팔꿈치, 손목, 골반, 무릎, 발목 등)와
사람 영역 분할 마스크를 추정합니다.
실행: python pose_landmark_example.py [이미지 경로]
"""

import sys

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision
from mediapipe.tasks.python.vision import drawing_styles, drawing_utils

from common import MODEL_DIR, SAMPLE_DIR, download, save

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task"
IMAGE_URL = "https://cdn.pixabay.com/photo/2019/03/12/20/39/girl-4051811_960_720.jpg"


def main():
    print("[Pose Landmark Detection]")
    model_path = download(MODEL_URL, MODEL_DIR)
    image_path = sys.argv[1] if len(sys.argv) > 1 else download(IMAGE_URL, SAMPLE_DIR)

    options = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        output_segmentation_masks=True,
        num_poses=2,
    )
    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        result = landmarker.detect(mp.Image.create_from_file(image_path))

    image = cv2.imread(image_path)
    print(f"  검출된 사람: {len(result.pose_landmarks)}명")

    # 분할 마스크로 사람 영역에 초록색 오버레이
    for mask in result.segmentation_masks or []:
        m = mask.numpy_view().squeeze()[..., None]
        image = (image * (1 - 0.4 * m) + np.array([0, 200, 0]) * 0.4 * m).astype(np.uint8)

    names = vision.PoseLandmark
    for landmarks in result.pose_landmarks:
        drawing_utils.draw_landmarks(
            image,
            landmarks,
            vision.PoseLandmarksConnections.POSE_LANDMARKS,
            drawing_styles.get_default_pose_landmarks_style(),
        )
        for idx in (names.LEFT_WRIST, names.RIGHT_WRIST, names.LEFT_ANKLE, names.RIGHT_ANKLE):
            lm = landmarks[idx]
            print(f"  - {names(idx).name}: ({lm.x:.3f}, {lm.y:.3f}), visibility={lm.visibility:.2f}")

    save(image, "pose_landmark")


if __name__ == "__main__":
    main()
