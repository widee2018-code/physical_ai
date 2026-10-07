"""[S1]~[S6] 온습도 분기.

S1 물리 범위 → S2 변화율·Hampel → S3 결측 계층 처리 + 품질 플래그 → S4 오프셋 보정
→ S5 그리드 정렬·EMA → S6 이슬점·절대습도·dT/dt

S3 실시간 처리 주의: 선형 보간은 다음 샘플이 있어야 가능하다. 실시간 제어에는 직전값을 INTERP
플래그와 함께 내보내고(제습 전환 등 모드 변경 금지), 통신이 복구되면 해당 그리드 시각들을
선형 보간값으로 다시 계산해 backfill로 넘긴다(로그·학습 데이터용).
"""
from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .config import ClimateConfig
from .occupancy import StreamingHampel
from .utils import Flag, absolute_humidity_gm3, dew_point_c


class ChannelCleaner:
    """단일 센서 채널의 S1~S3."""

    def __init__(self, name: str, valid_range: Tuple[float, float], rate_per_10s: float,
                 hampel_window: int, hampel_k: float, hampel_min_scale: float,
                 reject_streak_accept: int, ffill_s: float, interp_s: float, grid_s: float):
        self.name = name
        self.valid_range = valid_range
        self.rate_per_10s = rate_per_10s
        self.hampel = StreamingHampel(hampel_window, hampel_k, hampel_min_scale)
        self.reject_streak_accept = reject_streak_accept
        self.ffill_s = ffill_s
        self.interp_s = interp_s
        self.grid_s = grid_s
        self.last: Optional[Tuple[float, float]] = None
        self._streak: List[float] = []
        self._pending: List[float] = []
        self._backfill: List[Tuple[float, float]] = []
        self.stats: Counter = Counter()

    def push(self, ts: float, value: Optional[float]) -> None:
        lo, hi = self.valid_range
        if value is None or not math.isfinite(value) or not (lo <= value <= hi):      # S1
            self.stats["range_reject"] += 1
            return
        if self.last is not None:                                                     # S2 변화율
            dt = max(ts - self.last[0], 1.0)
            limit = self.rate_per_10s * dt / 10.0
            if abs(value - self.last[1]) > limit:
                self._streak.append(value)
                consistent = max(self._streak) - min(self._streak) <= self.rate_per_10s
                if len(self._streak) >= self.reject_streak_accept and consistent:
                    # 같은 수준이 연속으로 들어오면 실제 변화(예: 문 개방)로 보고 수용
                    self.stats["level_shift_accepted"] += 1
                    self.hampel.reset()
                    self._streak.clear()
                else:
                    self.stats["rate_reject"] += 1
                    if len(self._streak) > self.reject_streak_accept:
                        self._streak.pop(0)
                    return
            else:
                self._streak.clear()
            if ts - self.last[0] > self.ffill_s:
                # 통신 공백 후에는 이전 창이 현재 수준을 대표하지 못하므로 Hampel 창을 비운다.
                self.hampel.reset()
        value, outlier = self.hampel.push(value)                                      # S2 Hampel
        if outlier:
            self.stats["hampel_replaced"] += 1
        self._resolve_gap(ts, value)
        self.last = (ts, value)
        self.stats["accepted"] += 1

    def _resolve_gap(self, ts: float, value: float) -> None:
        if self._pending and self.last is not None:
            t0, v0 = self.last
            gap = ts - t0
            if 0 < gap <= self.interp_s:
                for g in self._pending:
                    self._backfill.append((g, v0 + (value - v0) * (g - t0) / gap))
        self._pending.clear()

    def value_at(self, g: float) -> Tuple[Optional[float], Flag]:
        """S3: 그리드 시각 g의 값과 품질 플래그."""
        if self.last is None:
            return None, Flag.INVALID
        ts, v = self.last
        age = g - ts
        if age <= self.grid_s:
            return v, Flag.OK
        if age <= self.ffill_s:
            return v, Flag.HELD
        if age <= self.interp_s:
            self._pending.append(g)
            return v, Flag.INTERP
        self._pending.clear()
        return None, Flag.INVALID

    def drain_backfill(self) -> List[Tuple[float, float]]:
        out, self._backfill = self._backfill, []
        return out


@dataclass
class ClimateSample:
    ts: float
    temp_c: Optional[float]          # S4 보정 후
    rh_pct: Optional[float]
    temp_flag: Flag
    rh_flag: Flag
    temp_ema: Optional[float]        # S5
    rh_ema: Optional[float]
    dew_point_c: Optional[float]     # S6
    abs_humidity_gm3: Optional[float]
    dtdt_c_per_min: Optional[float]


class ClimateProcessor:
    """온도·습도 두 채널의 S1~S6."""

    def __init__(self, cfg: ClimateConfig, grid_s: float):
        self.cfg = cfg
        self.temp = ChannelCleaner("temp", cfg.temp_range, cfg.temp_rate_limit_per_10s, cfg.hampel_window,
                                   cfg.hampel_k, cfg.temp_hampel_min_scale, cfg.reject_streak_accept,
                                   cfg.ffill_s, cfg.interp_s, grid_s)
        self.rh = ChannelCleaner("rh", cfg.rh_range, cfg.rh_rate_limit_per_10s, cfg.hampel_window,
                                 cfg.hampel_k, cfg.rh_hampel_min_scale, cfg.reject_streak_accept,
                                 cfg.ffill_s, cfg.interp_s, grid_s)
        self._ema_t: Optional[float] = None
        self._ema_h: Optional[float] = None
        self._hist: deque = deque()

    def push(self, ts: float, temp_c: Optional[float], rh_pct: Optional[float]) -> None:
        self.temp.push(ts, temp_c)
        self.rh.push(ts, rh_pct)

    def _offset_t(self, v: float) -> float:
        return v + self.cfg.temp_offset_c

    def _offset_h(self, v: float) -> float:
        return min(100.0, max(0.0, v + self.cfg.rh_offset_pct))

    def tick(self, g: float) -> ClimateSample:
        t, tf = self.temp.value_at(g)                                   # S3
        h, hf = self.rh.value_at(g)
        t = self._offset_t(t) if t is not None else None                # S4
        h = self._offset_h(h) if h is not None else None
        a = self.cfg.ema_alpha                                          # S5
        if t is not None:
            self._ema_t = t if self._ema_t is None else a * t + (1 - a) * self._ema_t
        if h is not None:
            self._ema_h = h if self._ema_h is None else a * h + (1 - a) * self._ema_h

        dp = ah = None                                                  # S6
        if self._ema_t is not None and self._ema_h is not None and Flag.INVALID not in (tf, hf):
            dp = dew_point_c(self._ema_t, self._ema_h)
            ah = absolute_humidity_gm3(self._ema_t, self._ema_h)
        dtdt = None
        if self._ema_t is not None and tf is not Flag.INVALID:
            self._hist.append((g, self._ema_t))
            while self._hist and g - self._hist[0][0] > self.cfg.dtdt_window_s:
                self._hist.popleft()
            if len(self._hist) >= 2 and g > self._hist[0][0]:
                dtdt = (self._ema_t - self._hist[0][1]) / (g - self._hist[0][0]) * 60.0
        return ClimateSample(g, t, h, tf, hf, self._ema_t, self._ema_h, dp, ah, dtdt)

    def drain_backfill(self) -> List[Tuple[float, str, float]]:
        """통신 복구 후 사후 선형 보간값 (S4 보정 적용). 로그·학습 데이터 갱신용."""
        out = [(g, "temp_c", self._offset_t(v)) for g, v in self.temp.drain_backfill()]
        out += [(g, "rh_pct", self._offset_h(v)) for g, v in self.rh.drain_backfill()]
        return out

    @property
    def stats(self) -> dict:
        return {"temp": dict(self.temp.stats), "rh": dict(self.rh.stats)}
