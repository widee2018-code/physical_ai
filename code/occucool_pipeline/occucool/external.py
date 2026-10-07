"""[O1] 외기온도, [O2] 운영 스케줄. 두 작업은 서로 독립이다."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from .config import OutdoorConfig, ScheduleConfig
from .occupancy import RollingMedian
from .utils import Flag


class OutdoorProcessor:
    """[O1] 범위 검사 → 중앙값 필터 → 장주기 EMA(시간 간격 반영) → 1분 그리드 조회.

    결측: ffill_s 이내 직전값, interp_s 이내 직전값+INTERP 플래그, 초과 시 INVALID(외기 제외 모드).
    """

    def __init__(self, cfg: OutdoorConfig):
        self.cfg = cfg
        self._med = RollingMedian(cfg.median_window)
        self.ema: Optional[float] = None
        self.last_ts: Optional[float] = None
        self.rejects = 0

    def push(self, ts: float, value: Optional[float]) -> None:
        lo, hi = self.cfg.valid_range
        if value is None or not math.isfinite(value) or not (lo <= value <= hi):
            self.rejects += 1
            return
        m = self._med.push(value)
        if self.ema is None or self.last_ts is None:
            self.ema = m
        else:
            a = 1.0 - math.exp(-max(ts - self.last_ts, 0.0) / self.cfg.ema_tau_s)
            self.ema += a * (m - self.ema)
        self.last_ts = ts

    def value_at(self, ts: float) -> Tuple[Optional[float], Flag]:
        if self.ema is None or self.last_ts is None:
            return None, Flag.INVALID
        age = ts - self.last_ts
        if age <= self.cfg.sample_period_s * 1.5:
            return self.ema, Flag.OK
        if age <= self.cfg.ffill_s:
            return self.ema, Flag.HELD
        if age <= self.cfg.interp_s:
            return self.ema, Flag.INTERP
        return None, Flag.INVALID


class OperatingSchedule:
    """[O2] 운영 시간 마스크와 공휴일 캘린더. 반환: operating | precool | closed."""

    def __init__(self, cfg: ScheduleConfig):
        self.cfg = cfg
        try:
            from zoneinfo import ZoneInfo
            self.tz = ZoneInfo(cfg.timezone)
        except Exception:  # tzdata가 없는 장비 대비
            self.tz = timezone(timedelta(hours=9))
        self._start = self._minutes(cfg.start)
        self._end = self._minutes(cfg.end)

    @staticmethod
    def _minutes(hhmm: str) -> float:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    def status(self, ts: float) -> str:
        dt = datetime.fromtimestamp(ts, self.tz)
        if dt.weekday() not in self.cfg.weekdays or dt.date().isoformat() in self.cfg.holidays:
            return "closed"
        now = dt.hour * 60 + dt.minute + dt.second / 60.0
        if self._start <= now < self._end:
            return "operating"
        if self._start - self.cfg.precool_min <= now < self._start:
            return "precool"
        return "closed"
