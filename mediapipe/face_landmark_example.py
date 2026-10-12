"""MediaPipe Face Landmarker 시연.

얼굴 478개 3D 랜드마크(메시, 눈/눈썹/입술 윤곽, 홍채)와
52개 블렌드쉐이프(표정 계수)를 추정합니다.
실행: python face_landmark_example.py [이미지 경로]
"""

import sys

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision
from mediapipe.tasks.python.vision import drawing_styles, drawing_utils

from common import MODEL_DIR, SAMPLE_DIR, download, save

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
IMAGE_URL = "https://storage.googleapis.com/mediapipe-assets/business-person.png"

Conn = vision.FaceLandmarksConnections


def main():
    print("[Face Landmark Detection]")
    model_path = download(MODEL_URL, MODEL_DIR)
    image_path = sys.argv[1] if len(sys.argv) > 1 else download(IMAGE_URL, SAMPLE_DIR)

    options = vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        output_face_blendshapes=True,
        num_faces=1,
    )
    with vision.FaceLandmarker.create_from_options(options) as landmarker:
        result = landmarker.detect(mp.Image.create_from_file(image_path))

    image = cv2.imread(image_path)
    print(f"  검출된 얼굴: {len(result.face_landmarks)}개")
    for landmarks in result.face_landmarks:
        print(f"  - 랜드마크 수: {len(landmarks)}")
        # 메시(삼각망) → 윤곽 → 홍채 순서로 그리기
        drawing_utils.draw_landmarks(image, landmarks, Conn.FACE_LANDMARKS_TESSELATION, None,
                                     drawing_styles.get_default_face_mesh_tesselation_style())
        drawing_utils.draw_landmarks(image, landmarks, Conn.FACE_LANDMARKS_CONTOURS, None,
                                     drawing_styles.get_default_face_mesh_contours_style())
        drawing_utils.draw_landmarks(image, landmarks,
                                     Conn.FACE_LANDMARKS_LEFT_IRIS + Conn.FACE_LANDMARKS_RIGHT_IRIS, None,
                                     drawing_styles.get_default_face_mesh_iris_connections_style())

    # 점수가 높은 블렌드쉐이프(표정) 상위 5개 표시
    for blendshapes in result.face_blendshapes:
        top5 = sorted(blendshapes, key=lambda c: c.score, reverse=True)[:5]
        for i, c in enumerate(top5):
            text = f"{c.category_name}: {c.score:.2f}"
            print(f"    {text}")
            cv2.putText(image, text, (10, 30 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    save(image, "face_landmark")


if __name__ == "__main__":
    main()
