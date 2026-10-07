"""공용 유틸리티: 품질 플래그, 통계, 기하, 습공기 계산."""
from __future__ import annotations

import math
from enum import Enum
from typing import Iterable, Sequence, Tuple


class Flag(str, Enum):
    """값마다 함께 저장하는 품질 플래그 (보고서 4장)."""
    OK = "ok"
    HELD = "held"            # 직전값 유지
    INTERP = "interp"        # 보간 구간 (실시간에는 직전값, 사후에 선형 보간으로 재기록)
    ESTIMATED = "estimated"  # 다른 신호로 추정
    INVALID = "invalid"


def median(values: Iterable[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        raise ValueError("빈 값의 중앙값")
    m = n // 2
    return float(s[m]) if n % 2 else (s[m - 1] + s[m]) / 2.0


def point_in_polygon(pt: Tuple[float, float], poly: Sequence[Tuple[float, float]]) -> bool:
    """Ray casting 방식 점-다각형 포함 판정."""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def dew_point_c(temp_c: float, rh_pct: float) -> float:
    """Magnus 근사식 이슬점(℃)."""
    a, b = 17.62, 243.12
    rh = min(max(rh_pct, 1.0), 100.0)
    g = math.log(rh / 100.0) + a * temp_c / (b + temp_c)
    return b * g / (a - g)


def absolute_humidity_gm3(temp_c: float, rh_pct: float) -> float:
    """절대습도(g/m³)."""
    return 6.112 * math.exp(17.67 * temp_c / (temp_c + 243.5)) * rh_pct * 2.1674 / (273.15 + temp_c)


def round_half(x: float) -> float:
    """에어컨 설정온도 단위(0.5℃)로 반올림."""
    return round(x * 2.0) / 2.0
