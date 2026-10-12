"""SigLIP2 이미지-텍스트 벡터 유사도 간단 테스트.

같은 물체를 표현하는 이미지 벡터와 텍스트 벡터가 실제로 가까운지 확인합니다.
- 각 이미지/텍스트를 SigLIP2로 임베딩 → L2 정규화 → 코사인 유사도 행렬 계산
- SigLIP 방식의 확률(sigmoid(logit_scale * cos + logit_bias))도 함께 출력
- 각 이미지별로 가장 유사한 텍스트가 "정답 텍스트"인지 확인

실행: python sigLIP2_example.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import torch
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Rectangle
from PIL import Image
from transformers import AutoModel, AutoProcessor
from ultralytics.utils import ASSETS

MODEL_ID = "google/siglip2-base-patch16-224"
OUT_PATH = Path(__file__).parent / "siglip2_similarity.jpg"
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"

# ultralytics 내장 예제 이미지 활용 (bus.jpg: 버스 + 사람들, zidane.jpg: 축구 선수 2명)
bus_img = Image.open(ASSETS / "bus.jpg").convert("RGB")
zidane_img = Image.open(ASSETS / "zidane.jpg").convert("RGB")

# (이름, 이미지, 정답 텍스트 인덱스) - 그림에 쓰이는 이름은 PLOT_NAMES 참고
IMAGES = [
    ("bus 전체 이미지", bus_img.crop((0, 200, 810, 800)), 0),  # 버스 영역
    ("bus 속 사람 crop", bus_img.crop((40, 390, 250, 910)), 1),  # 왼쪽 보행자
    ("zidane 이미지", zidane_img, 2),
]

PLOT_NAMES = ["Bus (crop)", "Pedestrian (crop)", "Soccer players"]

TEXTS = [
    "a photo of a bus",
    "a photo of a person walking",
    "a photo of two soccer players",
    "a photo of a cat",
    "a photo of a car",
    "a photo of a pizza",
]


def visualize(cos):
    """사용한 이미지 + 코사인 유사도 히트맵을 한 장의 JPEG로 저장."""
    # 단일 색상(파랑) 순차 팔레트: 밝을수록 낮은 유사도, 진할수록 높은 유사도
    cmap = LinearSegmentedColormap.from_list(
        "blue_seq", ["#f4f8fd", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
    )
    ink, ink2, grid = "#0b0b0b", "#52514e", "#fcfcfb"

    fig = plt.figure(figsize=(12, 9), dpi=150, facecolor="#fcfcfb")
    gs = fig.add_gridspec(2, len(IMAGES), height_ratios=[1, 1.15], hspace=0.35, wspace=0.15)

    # 1행: 테스트에 사용한 이미지
    for i, ((_, img, gt), name) in enumerate(zip(IMAGES, PLOT_NAMES)):
        ax = fig.add_subplot(gs[0, i])
        ax.imshow(img)
        ax.set_title(f"{name}\nanswer: \"{TEXTS[gt]}\"", fontsize=10, color=ink)
        ax.axis("off")

    # 2행: 코사인 유사도 히트맵 (행=이미지, 열=텍스트)
    ax = fig.add_subplot(gs[1, :])
    data = cos.numpy()
    vmax = float(data.max())
    im = ax.imshow(data, cmap=cmap, vmin=0, vmax=vmax, aspect="auto")
    ax.set_xticks(range(len(TEXTS)), [t.replace("a photo of ", "") for t in TEXTS], fontsize=10, color=ink2)
    ax.set_yticks(range(len(IMAGES)), PLOT_NAMES, fontsize=10, color=ink2)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    # 셀 사이 2px 간격
    ax.set_xticks([x - 0.5 for x in range(1, len(TEXTS))], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(IMAGES))], minor=True)
    ax.grid(which="minor", color=grid, linewidth=2)
    ax.tick_params(which="minor", length=0)

    for r, (_, _, gt) in enumerate(IMAGES):
        best = int(data[r].argmax())
        for c in range(len(TEXTS)):
            v = data[r, c]
            color = "white" if v > vmax * 0.55 else ink
            weight = "bold" if c == best else "normal"
            ax.text(c, r, f"{v:.3f}", ha="center", va="center", fontsize=11, color=color, fontweight=weight)
        # 정답 텍스트 칸 표시
        ax.add_patch(Rectangle((gt - 0.47, r - 0.47), 0.94, 0.94, fill=False, edgecolor="#eb6834", linewidth=2.5))

    ax.set_title("SigLIP2 image-text cosine similarity  (orange box = correct text, bold = row max)",
                 fontsize=12, color=ink, pad=10)
    cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(labelsize=9, colors=ink2, length=0)
    cbar.set_label("cosine similarity", color=ink2, fontsize=9)

    fig.suptitle(f"Model: {MODEL_ID}", fontsize=13, color=ink, y=0.98)
    fig.savefig(OUT_PATH, format="jpeg", dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"\n시각화 저장: {OUT_PATH}")


def main():
    print(f"모델 로드: {MODEL_ID} (device={DEVICE})")
    model = AutoModel.from_pretrained(MODEL_ID).to(DEVICE).eval()
    processor = AutoProcessor.from_pretrained(MODEL_ID)

    images = [img for _, img, _ in IMAGES]
    # SigLIP2는 학습 시 padding="max_length", max_length=64 로 텍스트를 처리함
    inputs = processor(
        text=TEXTS, images=images, padding="max_length", max_length=64, return_tensors="pt"
    ).to(DEVICE)

    with torch.no_grad():
        img_emb = model.get_image_features(pixel_values=inputs["pixel_values"])
        txt_emb = model.get_text_features(input_ids=inputs["input_ids"])
        # 최신 transformers는 출력 객체를 반환할 수 있으므로 텐서만 추출
        img_emb = getattr(img_emb, "pooler_output", img_emb)
        txt_emb = getattr(txt_emb, "pooler_output", txt_emb)

    img_emb = img_emb / img_emb.norm(dim=-1, keepdim=True)
    txt_emb = txt_emb / txt_emb.norm(dim=-1, keepdim=True)
    print(f"이미지 벡터 shape: {tuple(img_emb.shape)}, 텍스트 벡터 shape: {tuple(txt_emb.shape)}")

    cos = (img_emb @ txt_emb.T).cpu()  # [이미지 수, 텍스트 수]
    probs = torch.sigmoid(cos * model.logit_scale.exp().cpu() + model.logit_bias.cpu())

    print("\n[1] 코사인 유사도 (이미지 벡터 · 텍스트 벡터)")
    print(f"{'':20s}" + "".join(f"{t[11:][:14]:>16s}" for t in TEXTS))
    for (name, _, _), row in zip(IMAGES, cos):
        print(f"{name:20s}" + "".join(f"{v:16.4f}" for v in row.tolist()))

    print("\n[2] SigLIP 확률 sigmoid(scale*cos + bias)")
    for (name, _, gt), row in zip(IMAGES, probs):
        best = int(row.argmax())
        mark = "O" if best == gt else "X"
        print(f"  [{mark}] {name}: 정답='{TEXTS[gt]}' (p={row[gt]:.4f}) | "
              f"최고='{TEXTS[best]}' (p={row[best]:.4f})")

    print("\n[3] 정답 텍스트 vs 나머지 텍스트 평균 코사인 유사도")
    for (name, _, gt), row in zip(IMAGES, cos):
        others = torch.cat([row[:gt], row[gt + 1:]])
        print(f"  {name}: 정답 {row[gt]:.4f}  vs  오답 평균 {others.mean():.4f}  "
              f"(차이 {row[gt] - others.mean():+.4f})")

    visualize(cos)


if __name__ == "__main__":
    main()
