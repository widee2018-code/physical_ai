"""OccuCool 하드웨어 블록 다이어그램 생성 (hardware_report.md 1장)."""
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch
from pathlib import Path

for f in font_manager.findSystemFonts():
    if "AppleSDGothicNeo" in f:
        font_manager.fontManager.addfont(f)
plt.rcParams["font.family"] = "Apple SD Gothic Neo"

BG, PANEL, TEXT, SUB = "#0d1117", "#111720", "#e6edf3", "#8b949e"
C = {"power": "#e0a03a", "proc": "#2ccfd3", "periph": "#a98bf5", "ext": "#7d8590"}
POWER_LINE, SIGNAL_LINE = "#a8692a", "#2a9fa3"

W, H = 9.2, 4.6  # 기본 박스 크기
# key: (x, y, 제목, 부제, 종류, w, h)
N = {
    "poe":   (9, 54, "PoE PD", "802.3af/at · 37~57V", "power", W, H),
    "dc":    (9, 34, "DC 어댑터", "12V / 2A", "power", W, H),
    "bus":   (23, 44, "12V 버스", "역전압·서지 보호, 퓨즈", "power", W, H),
    "buck5": (38, 56, "Buck 5V / 3A", "12V → 5V", "power", W, H),
    "buck3": (38, 33, "Buck 3.3V / 2A", "+ MCU 전용 LDO", "power", W, H),
    "soc":   (60, 54, "i.MX 8M Plus SoM", "A53×4 · NPU 2.3 TOPS · ISP", "proc", 13, 7),
    "mcu":   (60, 24, "서브 MCU (Cortex-M)", "제어 루프 · 보호 규칙 · 워치독", "proc", 13, 7),
    "cam":   (60, 71, "2MP 광각 카메라", "IMX462 · 110~130° · IR-Cut", "periph", W + 2, H),
    "wifi":  (85, 71, "Wi-Fi 6 + BLE", "IW612 콤보 모듈", "periph", W, H),
    "phy":   (85, 59, "GbE PHY", "RTL8211F · RJ45", "periph", W, H),
    "se":    (85, 48, "보안 소자 SE050", "X.509 키 저장", "periph", W, H),
    "irtx":  (85, 37, "IR 송신 LED ×3~5", "940nm · 5V · MOSFET", "periph", W, H),
    "irrx":  (85, 28, "IR 수신기", "TSOP38238 · 38kHz", "periph", W, H),
    "rs485": (85, 17, "RS-485 트랜시버", "절연형 ADM2587E", "periph", W, H),
    "sht":   (30, 6, "SHT4x 온습도", "필수 · 10초 주기", "periph", W, H),
    "scd":   (45, 6, "SCD41 CO₂", "선택 · 환기 알림", "periph", W, H),
    "rtc":   (60, 6, "RTC RV-3028", "슈퍼커패시터 백업", "periph", W, H),
    "ui":    (75, 6, "상태 LED·버튼·부저", "페어링 · 상태 표시", "periph", W + 1, H),
    "cloud": (110, 65, "AWS IoT Core", "클라우드 서버", "ext", W, H),
    "phone": (110, 76, "설치 앱 (스마트폰)", "페어링 · 설정", "ext", W, H),
    "remote": (110, 27, "사용자 리모컨", "수동 조작", "ext", W, H),
    "ac":    (110, 39, "천장형 카세트 에어컨", "IR 수신부", "ext", W + 1, H),
    "hvac":  (110, 15.5, "시스템 에어컨", "/ BACnet 게이트웨이", "ext", W, H),
    "meter": (110, 5, "CT 전력량계", "· 외기 온습도 센서", "ext", W, H),
}

POWER = [  # (from, to, 라벨)
    ("poe", "bus", "12V"), ("dc", "bus", "12V"), ("bus", "buck5", "12V"),
    ("buck5", "soc", "5V"), ("buck5", "buck3", "5V"), ("buck3", "mcu", "3.3V"),
]
SIGNAL = [  # (from, to, 라벨, 외부 무선/버스 여부)
    ("cam", "soc", "MIPI CSI-2 · I²C(SCCB)", False),
    ("soc", "mcu", "UART/SPI · CRC16\n하트비트 · 리셋", False),
    ("soc", "wifi", "SDIO 3.0 · UART(HCI)", False),
    ("soc", "phy", "RGMII · MDIO", False),
    ("soc", "se", "I²C 0x48", False),
    ("mcu", "irtx", "PWM 38kHz", False),
    ("irrx", "mcu", "GPIO 입력 캡처", False),
    ("mcu", "rs485", "UART + DE/RE", False),
    ("mcu", "sht", "I²C 0x44", False),
    ("mcu", "scd", "I²C 0x62", False),
    ("mcu", "rtc", "I²C 0x52", False),
    ("mcu", "ui", "GPIO/PWM", False),
    ("wifi", "cloud", "MQTT over TLS :8883", True),
    ("wifi", "phone", "BLE GATT", True),
    ("phy", "cloud", "Ethernet · MQTT/TLS\nBACnet/IP", True),
    ("remote", "irrx", "IR 38kHz", True),
    ("irtx", "ac", "IR 38kHz", True),
    ("rs485", "hvac", "Modbus RTU / MS/TP", True),
    ("rs485", "meter", "Modbus RTU", True),
]


def clip(a, b):
    """a 박스 중심에서 b 박스 중심 방향으로, 박스 경계에서 잘린 선분 반환."""
    (x1, y1, *_r1, w1, h1), (x2, y2, *_r2, w2, h2) = N[a], N[b]
    dx, dy = x2 - x1, y2 - y1

    def t_of(w, h):
        tx = (w / 2) / abs(dx) if dx else float("inf")
        ty = (h / 2) / abs(dy) if dy else float("inf")
        return min(tx, ty)

    t1, t2 = t_of(w1, h1), t_of(w2, h2)
    return (x1 + dx * t1, y1 + dy * t1), (x2 - dx * t2, y2 - dy * t2)


fig = plt.figure(figsize=(20, 13), dpi=150, facecolor=BG)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(-2, 122)
ax.set_ylim(-8, 88)
ax.axis("off")
ax.add_patch(FancyBboxPatch((1, -5.5), 118, 86, boxstyle="round,pad=0,rounding_size=1",
                            fc=PANEL, ec="#232b36", lw=1.2, zorder=0))

ax.text(1, 85.2, "OCCUCOOL HARDWARE", color=C["proc"], fontsize=11, family="monospace",
        va="center", weight="bold")
ax.text(19, 85.2, "블록 다이어그램 — 부품 연결 및 통신 방식", color=TEXT, fontsize=16,
        va="center", weight="bold")

# 범례
lx = 76
ax.plot([lx, lx + 4], [85.2] * 2, color=POWER_LINE, lw=3.5, solid_capstyle="round")
ax.text(lx + 5, 85.2, "전원 배선", color=TEXT, fontsize=11, va="center")
ax.plot([lx + 13, lx + 17], [85.2] * 2, color=SIGNAL_LINE, lw=2.4, ls=(0, (3, 2)))
ax.text(lx + 18, 85.2, "보드 내 신호", color=TEXT, fontsize=11, va="center")
ax.plot([lx + 28, lx + 32], [85.2] * 2, color=C["ext"], lw=2.4, ls=(0, (1, 1.6)))
ax.text(lx + 33, 85.2, "외부 무선·버스", color=TEXT, fontsize=11, va="center")


def label(p, q, text, color, frac=0.5):
    mx, my = p[0] + (q[0] - p[0]) * frac, p[1] + (q[1] - p[1]) * frac
    ax.text(mx, my, text, color=color, fontsize=10, ha="center", va="center",
            family="Apple SD Gothic Neo", zorder=4, linespacing=1.15,
            bbox=dict(boxstyle="round,pad=0.25", fc=PANEL, ec="none"))


for a, b, t in POWER:
    p, q = clip(a, b)
    ax.plot([p[0], q[0]], [p[1], q[1]], color=POWER_LINE, lw=3.5, solid_capstyle="round", zorder=2)
    label(p, q, t, C["power"])

LABEL_FRAC = {("mcu", "sht"): 0.6, ("mcu", "scd"): 0.55, ("mcu", "ui"): 0.6,
              ("soc", "se"): 0.55, ("mcu", "irtx"): 0.6, ("mcu", "rs485"): 0.6}
for a, b, t, ext in SIGNAL:
    p, q = clip(a, b)
    color, ls, lw = (C["ext"], (0, (1, 1.6)), 2.4) if ext else (SIGNAL_LINE, (0, (3, 2)), 2.4)
    ax.plot([p[0], q[0]], [p[1], q[1]], color=color, lw=lw, ls=ls, zorder=2)
    label(p, q, t, "#b9c2cc" if ext else "#5fd8dc", LABEL_FRAC.get((a, b), 0.5))

for x, y, title, sub, kind, w, h in N.values():
    col = C[kind]
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                boxstyle="round,pad=0,rounding_size=0.6", fc="#141b24", ec=col,
                                lw=2.6 if kind == "proc" else 1.8,
                                ls="--" if kind == "ext" else "-", zorder=3))
    big = kind == "proc"
    ax.text(x, y + (1.2 if big else 0.85), title, color=TEXT, fontsize=14 if big else 12,
            weight="bold", ha="center", va="center", zorder=5)
    ax.text(x, y - (1.6 if big else 1.15), sub, color=SUB, fontsize=10 if big else 9,
            ha="center", va="center", zorder=5)

ax.plot([3, 117], [-3.2, -3.2], color="#4b5563", lw=1.2, ls=(0, (4, 3)))
ax.text(60, -3.2, "  모든 GND 공통 연결 (RS-485 버스 측은 절연)  ", color=SUB, fontsize=10,
        ha="center", va="center", bbox=dict(fc=PANEL, ec="none"))

out = Path(__file__).with_name("hardware_block_diagram.png")
fig.savefig(out, facecolor=BG)
print(out)
