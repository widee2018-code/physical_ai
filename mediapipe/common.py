"""MediaPipe 시연 스크립트 공용 헬퍼: 모델/샘플 이미지 다운로드, 결과 저장."""

import urllib.request
from pathlib import Path

import cv2

ROOT = Path(__file__).parent
MODEL_DIR = ROOT / "models"
SAMPLE_DIR = ROOT / "samples"
OUT_DIR = ROOT / "outputs"
for d in (MODEL_DIR, SAMPLE_DIR, OUT_DIR):
    d.mkdir(exist_ok=True)


def download(url, folder):
    """파일이 없으면 url에서 받아 folder에 저장하고 경로를 반환."""
    path = folder / url.split("/")[-1]
    if not path.exists():
        print(f"  다운로드: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as r:
            path.write_bytes(r.read())
    return str(path)


def save(image_bgr, name):
    path = OUT_DIR / f"{name}.jpg"
    cv2.imwrite(str(path), image_bgr)
    print(f"  저장: {path}")
