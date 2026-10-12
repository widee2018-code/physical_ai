"""PaddleOCR (PP-OCRv5) 텍스트 검출 + 인식 간단 시연.

PaddleOCR는 현재 가장 널리 쓰이는 오픈소스 OCR 툴킷입니다.
- 1단계: 한국어/영어/숫자가 섞인 테스트 이미지 생성 (간판, 영수증, 기울어진 라벨)
- 2단계: PP-OCRv5 로 텍스트 영역 검출(det) → 문자 인식(rec)
- 3단계: 정답 텍스트와 비교해 인식 정확도 출력
- 4단계: [원본 + 검출 박스 | 인식 결과 재구성] 을 한 장의 JPEG로 저장

실행: python OCR_example.py
"""

import difflib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from paddleocr import PaddleOCR

LANG = "korean"  # korean 모델은 한글 + 영어 + 숫자를 함께 인식
OUT_DIR = Path(__file__).parent / "outputs"
INPUT_PATH = OUT_DIR / "ocr_input.jpg"
OUT_PATH = OUT_DIR / "ocr_result.jpg"
FONT_PATH = "/System/Library/Fonts/AppleSDGothicNeo.ttc"  # macOS 기본 한글 폰트

# (텍스트, 위치, 글자 크기, 글자색, 회전각도) - 회전각도는 반시계 방향
GT_LINES = [
    ("청년 피지컬AI 일경험 프로젝트", (40, 40), 44, "#ffffff", 0),
    ("Physical AI Lab - OCR Demo", (40, 105), 30, "#cde2fb", 0),
    ("편의점 영수증", (60, 210), 32, "#0b0b0b", 0),
    ("삼각김밥 1,500원", (60, 265), 26, "#0b0b0b", 0),
    ("바나나우유 1,700원", (60, 305), 26, "#0b0b0b", 0),
    ("합계 3,200원", (60, 355), 28, "#0b0b0b", 0),
    ("2026-10-12 14:25", (60, 405), 22, "#52514e", 0),
    ("출입금지", (560, 230), 48, "#ffffff", 8),
    ("NO ENTRY", (575, 300), 34, "#ffffff", 8),
    ("Tel. 02-1234-5678", (520, 440), 26, "#0b0b0b", -5),
]


def make_test_image():
    """간판 / 영수증 / 경고 표지판이 섞인 테스트 이미지 생성."""
    w, h = 900, 520
    rng = np.random.default_rng(0)
    bg = rng.normal(225, 12, (h, w, 3)).clip(0, 255).astype(np.uint8)  # 약간의 노이즈 배경
    img = Image.fromarray(bg)
    draw = ImageDraw.Draw(img)

    draw.rectangle((20, 20, 880, 160), fill="#1c5cab")  # 상단 간판
    draw.rectangle((40, 190, 400, 450), fill="#fcfcfb", outline="#52514e", width=2)  # 영수증
    draw.rectangle((530, 200, 820, 370), fill="#d03b3b")  # 경고 표지판

    for text, (x, y), size, color, angle in GT_LINES:
        font = ImageFont.truetype(FONT_PATH, size)
        if angle == 0:
            draw.text((x, y), text, font=font, fill=color)
            continue
        # 회전된 텍스트: 투명 레이어에 그린 뒤 회전해서 붙이기
        _, _, r, b = font.getbbox(text)
        layer = Image.new("RGBA", (r + 10, b + 10), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((5, 5), text, font=font, fill=color)
        layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
        img.paste(layer, (x, y - layer.height // 3), layer)

    img = img.filter(ImageFilter.GaussianBlur(0.6))  # 실제 촬영처럼 살짝 흐리게
    OUT_DIR.mkdir(exist_ok=True)
    img.save(INPUT_PATH, quality=92)
    return img


def run_ocr(ocr, img_path):
    """OCR 실행 → [(텍스트, 점수, 폴리곤 4점)] 리스트 반환."""
    res = ocr.predict(str(img_path))[0]
    return list(zip(res["rec_texts"], res["rec_scores"], res["rec_polys"]))


def evaluate(preds):
    """정답 줄마다 가장 비슷한 인식 결과를 찾아 문자 유사도 계산.

    큰 글씨는 단어 단위로 쪼개져 검출되기도 하므로, 연속된 검출 결과를 최대 6개까지 이어붙여 비교.
    """
    pred_texts = [t for t, _, _ in preds]
    candidates = [" ".join(pred_texts[i:j]) for i in range(len(pred_texts))
                  for j in range(i + 1, min(i + 7, len(pred_texts) + 1))]
    rows = []
    for gt, *_ in GT_LINES:
        best, best_sim = "", 0.0
        for cand in candidates:
            sim = difflib.SequenceMatcher(None, gt.replace(" ", ""), cand.replace(" ", "")).ratio()
            if sim > best_sim:
                best, best_sim = cand, sim
        rows.append((gt, best, best_sim))
    return rows


def visualize(img, preds, rows):
    """왼쪽: 원본 + 검출 박스, 오른쪽: 인식된 텍스트를 같은 위치에 재구성, 하단: 정답 비교표."""
    w, h = img.size
    table_h = 40 + 30 * len(rows)
    canvas = Image.new("RGB", (w * 2 + 30, h + 70 + table_h), "#fcfcfb")
    draw = ImageDraw.Draw(canvas)
    title_font = ImageFont.truetype(FONT_PATH, 26)
    font_small = ImageFont.truetype(FONT_PATH, 18)

    draw.text((10, 12), f"PaddleOCR PP-OCRv5 (lang={LANG})  -  검출 {len(preds)}개", font=title_font, fill="#0b0b0b")
    left, right = (10, 55), (w + 20, 55)

    # 왼쪽: 원본 이미지 + 검출 폴리곤 + 번호
    boxed = img.copy()
    bd = ImageDraw.Draw(boxed)
    for i, (_, _, poly) in enumerate(preds):
        pts = [tuple(map(int, p)) for p in poly]
        bd.polygon(pts, outline="#eb6834", width=3)
        bd.text((pts[0][0], pts[0][1] - 18), str(i + 1), font=font_small, fill="#eb6834")
    canvas.paste(boxed, left)

    # 오른쪽: 인식된 텍스트를 박스 위치에 다시 그림 (점수 포함)
    draw.rectangle((right[0], right[1], right[0] + w, right[1] + h), fill="#ffffff", outline="#cccccc")
    for i, (text, score, poly) in enumerate(preds):
        poly = np.asarray(poly) + right
        x0, y0 = poly.min(axis=0)
        _, y1 = poly.max(axis=0)
        draw.polygon([tuple(map(int, p)) for p in poly], outline="#86b6ef", width=2)
        size = int(max(12, min(30, (y1 - y0) * 0.6)))
        draw.text((x0 + 3, y0 + 2), f"{i + 1}. {text}", font=ImageFont.truetype(FONT_PATH, size), fill="#0d366b")
        draw.text((x0 + 3, y1 - 2), f"{score:.3f}", font=ImageFont.truetype(FONT_PATH, 14), fill="#52514e")

    # 하단: 정답 vs 인식 결과 비교표
    y = h + 75
    draw.text((10, y), "정답 텍스트", font=font_small, fill="#52514e")
    draw.text((420, y), "인식 결과", font=font_small, fill="#52514e")
    draw.text((850, y), "문자 유사도", font=font_small, fill="#52514e")
    for gt, pred, sim in rows:
        y += 30
        color = "#1a7f37" if sim >= 0.999 else ("#b26b00" if sim >= 0.8 else "#d03b3b")
        draw.text((10, y), gt, font=font_small, fill="#0b0b0b")
        draw.text((420, y), pred, font=font_small, fill="#0b0b0b")
        draw.text((850, y), f"{sim * 100:.1f}%", font=font_small, fill=color)

    canvas.save(OUT_PATH, quality=95)
    print(f"\n시각화 저장: {OUT_PATH}")


def main():
    img = make_test_image()
    print(f"테스트 이미지 저장: {INPUT_PATH}")

    print(f"모델 로드: PaddleOCR PP-OCRv5 (lang={LANG})")
    ocr = PaddleOCR(
        lang=LANG,
        use_doc_orientation_classify=False,  # 문서 방향 보정 끔 (단순 장면 이미지)
        use_doc_unwarping=False,  # 문서 펴기 끔
        use_textline_orientation=False,
    )

    preds = run_ocr(ocr, INPUT_PATH)
    print(f"\n[1] 인식 결과 ({len(preds)}개)")
    for i, (text, score, _) in enumerate(preds):
        print(f"  {i + 1:2d}. {text:30s} (score={score:.4f})")

    rows = evaluate(preds)
    print("\n[2] 정답 대비 문자 유사도")
    for gt, pred, sim in rows:
        mark = "O" if sim >= 0.999 else "X"
        print(f"  [{mark}] 정답='{gt}' | 인식='{pred}' | {sim * 100:.1f}%")
    exact = sum(sim >= 0.999 for _, _, sim in rows)
    print(f"\n  완전 일치: {exact}/{len(rows)}  |  평균 유사도: {np.mean([s for *_, s in rows]) * 100:.1f}%")

    visualize(img, preds, rows)


if __name__ == "__main__":
    main()
