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
        # 설정값, 이상치 제거용 이동 중앙값 필터, EMA 상태를 준비
        self.cfg = cfg
        self._med = RollingMedian(cfg.median_window)
        # 장주기 EMA 값과 마지막 측정 시각 (아직 측정이 없으면 None)
        self.ema: Optional[float] = None
        self.last_ts: Optional[float] = None
        # 범위 밖/결측으로 버려진 샘플 수 (진단용)
        self.rejects = 0

    # 외기온도 측정값 1개를 받아 필터링하고 EMA를 갱신한다
    def push(self, ts: float, value: Optional[float]) -> None:
        # 유효 범위(℃) 밖이거나 결측(None/NaN/inf)이면 버리고 카운트만 올린다
        lo, hi = self.cfg.valid_range
        if value is None or not math.isfinite(value) or not (lo <= value <= hi):
            self.rejects += 1
            return
        # 이동 중앙값으로 튀는 값(스파이크)을 먼저 제거
        m = self._med.push(value)
        # 첫 샘플이면 EMA를 그 값으로 초기화
        if self.ema is None or self.last_ts is None:
            self.ema = m
        # 시간 간격 dt를 반영한 EMA 계수: a = 1 - exp(-dt/τ)
        # (샘플 간격이 불규칙해도 시상수 τ초 기준으로 일정하게 평활화됨)
        else:
            a = 1.0 - math.exp(-max(ts - self.last_ts, 0.0) / self.cfg.ema_tau_s)
            self.ema += a * (m - self.ema)
        self.last_ts = ts

    # 특정 시각 ts의 외기온도와 품질 플래그를 돌려준다 (1분 그리드 조회용)
    def value_at(self, ts: float) -> Tuple[Optional[float], Flag]:
        # 한 번도 유효값을 받지 못했다면 사용 불가
        if self.ema is None or self.last_ts is None:
            return None, Flag.INVALID
        # 마지막 측정 후 경과 시간(초)에 따라 신뢰도를 단계적으로 낮춘다
        age = ts - self.last_ts
        # 정상 주기의 1.5배 이내 → 정상
        if age <= self.cfg.sample_period_s * 1.5:
            return self.ema, Flag.OK
        # ffill_s 이내 → 직전값 유지
        if age <= self.cfg.ffill_s:
            return self.ema, Flag.HELD
        # interp_s 이내 → 직전값을 쓰되 보간 구간으로 표시
        if age <= self.cfg.interp_s:
            return self.ema, Flag.INTERP
        # 너무 오래됨 → 외기 정보 없이 동작(외기 제외 모드)
        return None, Flag.INVALID


class OperatingSchedule:
    """[O2] 운영 시간 마스크와 공휴일 캘린더. 반환: operating | precool | closed."""

    def __init__(self, cfg: ScheduleConfig):
        # 설정된 시간대(예: Asia/Seoul)를 불러오고, 실패하면 UTC+9로 대체
        self.cfg = cfg
        try:
            from zoneinfo import ZoneInfo
            self.tz = ZoneInfo(cfg.timezone)
        except Exception:  # tzdata가 없는 장비 대비
            self.tz = timezone(timedelta(hours=9))
        # 운영 시작/종료 시각을 자정 기준 분 단위로 미리 변환
        self._start = self._minutes(cfg.start)
        self._end = self._minutes(cfg.end)

    @staticmethod
    # "HH:MM" 문자열을 자정 기준 분(min)으로 변환
    def _minutes(hhmm: str) -> float:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    # 시각 ts가 운영 중 / 사전 냉방(precool) / 휴무 중 무엇인지 판정
    def status(self, ts: float) -> str:
        # 유닉스 타임스탬프를 현지 시간으로 변환
        dt = datetime.fromtimestamp(ts, self.tz)
        # 운영 요일이 아니거나 공휴일이면 휴무
        if dt.weekday() not in self.cfg.weekdays or dt.date().isoformat() in self.cfg.holidays:
            return "closed"
        # 현재 시각을 자정 기준 분으로 (초는 소수로 반영)
        now = dt.hour * 60 + dt.minute + dt.second / 60.0
        # 운영 시간 [시작, 종료) 안이면 운영 중
        if self._start <= now < self._end:
            return "operating"
        # 시작 precool_min 분 전부터 시작 직전까지는 미리 냉방
        if self._start - self.cfg.precool_min <= now < self._start:
            return "precool"
        return "closed"
