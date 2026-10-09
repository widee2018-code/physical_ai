"""공용 유틸리티: 품질 플래그, 통계, 기하, 습공기 계산."""
from __future__ import annotations

import math
from enum import Enum
from typing import Iterable, Sequence, Tuple


class Flag(str, Enum):
    """값마다 함께 저장하는 품질 플래그 (보고서 4장)."""
    OK = "ok"  # 정상 측정값
    HELD = "held"            # 직전값 유지
    INTERP = "interp"        # 보간 구간 (실시간에는 직전값, 사후에 선형 보간으로 재기록)
    ESTIMATED = "estimated"  # 다른 신호로 추정
    INVALID = "invalid"  # 사용 불가 (범위 밖·장기 결측 등)


# 값 목록의 중앙값을 구한다 (이상치에 강한 대표값). 빈 입력이면 예외.
def median(values: Iterable[float]) -> float:
    # 정렬 후 가운데 위치를 찾는다
    s = sorted(values)
    n = len(s)
    if n == 0:
        raise ValueError("빈 값의 중앙값")
    m = n // 2
    # 개수가 홀수면 가운데 값, 짝수면 가운데 두 값의 평균
    return float(s[m]) if n % 2 else (s[m - 1] + s[m]) / 2.0


def point_in_polygon(pt: Tuple[float, float], poly: Sequence[Tuple[float, float]]) -> bool:
    """Ray casting 방식 점-다각형 포함 판정."""
    # 점에서 오른쪽으로 수평선을 쏘아 다각형 변과 몇 번 교차하는지 센다
    # (홀수 번 교차 → 내부, 짝수 번 → 외부)
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    # 인접한 두 꼭짓점 (j → i)로 이루어진 변을 하나씩 검사
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        # 변이 점의 y 높이를 가로지르는 경우에만 교차 가능
        if (yi > y) != (yj > y):
            # 그 높이에서 변과 수평선이 만나는 x 좌표
            x_cross = xi + (y - yi) * (xj - xi) / (yj - yi)
            # 교차점이 점의 오른쪽에 있으면 교차 1회로 보고 내부/외부를 뒤집는다
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def dew_point_c(temp_c: float, rh_pct: float) -> float:
    """Magnus 근사식 이슬점(℃)."""
    # Magnus 식 계수 (물 위 포화수증기압 기준)
    a, b = 17.62, 243.12
    # log(0) 방지를 위해 상대습도를 1~100% 범위로 제한
    rh = min(max(rh_pct, 1.0), 100.0)
    g = math.log(rh / 100.0) + a * temp_c / (b + temp_c)
    return b * g / (a - g)


def absolute_humidity_gm3(temp_c: float, rh_pct: float) -> float:
    """절대습도(g/m³)."""
    # 포화수증기압(hPa) × 상대습도 비율을 이상기체 식으로 g/m³ 단위로 환산
    return 6.112 * math.exp(17.67 * temp_c / (temp_c + 243.5)) * rh_pct * 2.1674 / (273.15 + temp_c)


def round_half(x: float) -> float:
    """에어컨 설정온도 단위(0.5℃)로 반올림."""
    # 0.5 단위로 맞추기 위해 2배 → 반올림 → 다시 2로 나눈다
    return round(x * 2.0) / 2.0
