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
        # 설정값 저장: 유효 범위, 10초당 최대 변화량, Hampel 파라미터, 결측 처리 시간(초) 등
        self.name = name
        self.valid_range = valid_range
        self.rate_per_10s = rate_per_10s
        self.hampel = StreamingHampel(hampel_window, hampel_k, hampel_min_scale)
        self.reject_streak_accept = reject_streak_accept
        self.ffill_s = ffill_s
        self.interp_s = interp_s
        self.grid_s = grid_s
        # last: 마지막으로 수용된 (시각, 값) / _streak: 변화율 초과로 거부된 연속 값 / _pending: 보간 대기 그리드 시각 / _backfill: 사후 보간 결과
        self.last: Optional[Tuple[float, float]] = None
        self._streak: List[float] = []
        self._pending: List[float] = []
        self._backfill: List[Tuple[float, float]] = []
        self.stats: Counter = Counter()

    # 원시 센서값 하나를 검사(S1 범위 → S2 변화율 → S2 Hampel)하고 통과하면 저장한다
    def push(self, ts: float, value: Optional[float]) -> None:
        lo, hi = self.valid_range
        # 값이 없거나 NaN/무한대이거나 물리적으로 불가능한 범위면 버림
        if value is None or not math.isfinite(value) or not (lo <= value <= hi):      # S1
            self.stats["range_reject"] += 1
            return
        if self.last is not None:                                                     # S2 변화율
            # 경과 시간에 비례한 허용 변화량 계산 (최소 1초로 간주해 과민 반응 방지)
            dt = max(ts - self.last[0], 1.0)
            limit = self.rate_per_10s * dt / 10.0
            # 허용 변화량을 넘으면 일단 거부 후보로 쌓아둔다
            if abs(value - self.last[1]) > limit:
                self._streak.append(value)
                # 쌓인 값들이 서로 비슷한지(편차가 10초 허용치 이내) 확인
                consistent = max(self._streak) - min(self._streak) <= self.rate_per_10s
                if len(self._streak) >= self.reject_streak_accept and consistent:
                    # 같은 수준이 연속으로 들어오면 실제 변화(예: 문 개방)로 보고 수용
                    self.stats["level_shift_accepted"] += 1
                    self.hampel.reset()
                    self._streak.clear()
                else:
                    # 그렇지 않으면 튄 값으로 보고 거부 (후보 목록 길이는 일정하게 유지)
                    self.stats["rate_reject"] += 1
                    if len(self._streak) > self.reject_streak_accept:
                        self._streak.pop(0)
                    return
            else:
                # 정상 범위 변화면 거부 후보 초기화
                self._streak.clear()
            if ts - self.last[0] > self.ffill_s:
                # 통신 공백 후에는 이전 창이 현재 수준을 대표하지 못하므로 Hampel 창을 비운다.
                self.hampel.reset()
        value, outlier = self.hampel.push(value)                                      # S2 Hampel
        if outlier:
            self.stats["hampel_replaced"] += 1
        # 공백 동안 대기 중이던 그리드 시각들을 보간 처리한 뒤 최신값으로 저장
        self._resolve_gap(ts, value)
        self.last = (ts, value)
        self.stats["accepted"] += 1

    # 새 값이 들어오면, 공백 동안 대기 중이던 그리드 시각들을 '직전값~새 값' 선형 보간해 backfill에 저장
    def _resolve_gap(self, ts: float, value: float) -> None:
        if self._pending and self.last is not None:
            t0, v0 = self.last
            gap = ts - t0
            # 공백이 interp_s 이내일 때만 보간 (너무 길면 신뢰할 수 없어 버림)
            if 0 < gap <= self.interp_s:
                for g in self._pending:
                    self._backfill.append((g, v0 + (value - v0) * (g - t0) / gap))
        self._pending.clear()

    def value_at(self, g: float) -> Tuple[Optional[float], Flag]:
        """S3: 그리드 시각 g의 값과 품질 플래그."""
        if self.last is None:
            return None, Flag.INVALID
        ts, v = self.last
        # 마지막 값의 나이에 따라: 최신 → OK, ffill_s 이내 → HELD(직전값 유지), interp_s 이내 → INTERP(나중에 보간), 초과 → INVALID
        age = g - ts
        if age <= self.grid_s:
            return v, Flag.OK
        if age <= self.ffill_s:
            return v, Flag.HELD
        if age <= self.interp_s:
            self._pending.append(g)
            return v, Flag.INTERP
        # 너무 오래 끊겼으면 보간 대기도 취소하고 무효 처리
        self._pending.clear()
        return None, Flag.INVALID

    # 쌓인 사후 보간값을 꺼내고 내부 목록은 비운다
    def drain_backfill(self) -> List[Tuple[float, float]]:
        out, self._backfill = self._backfill, []
        return out


# 그리드 시각 하나의 온습도 처리 결과 (flag: 데이터 품질, ema: 평활값, dtdt: 분당 온도 변화율)
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
        # 온도·습도 채널별로 S1~S3 정제기 생성
        self.cfg = cfg
        self.temp = ChannelCleaner("temp", cfg.temp_range, cfg.temp_rate_limit_per_10s, cfg.hampel_window,
                                   cfg.hampel_k, cfg.temp_hampel_min_scale, cfg.reject_streak_accept,
                                   cfg.ffill_s, cfg.interp_s, grid_s)
        self.rh = ChannelCleaner("rh", cfg.rh_range, cfg.rh_rate_limit_per_10s, cfg.hampel_window,
                                 cfg.hampel_k, cfg.rh_hampel_min_scale, cfg.reject_streak_accept,
                                 cfg.ffill_s, cfg.interp_s, grid_s)
        # EMA 상태값과 dT/dt 계산용 (시각, 온도) 이력
        self._ema_t: Optional[float] = None
        self._ema_h: Optional[float] = None
        self._hist: deque = deque()

    # 원시 온습도 샘플을 각 채널 정제기에 전달
    def push(self, ts: float, temp_c: Optional[float], rh_pct: Optional[float]) -> None:
        self.temp.push(ts, temp_c)
        self.rh.push(ts, rh_pct)

    # S4: 온도 오프셋 보정 (센서 교정값을 더함)
    def _offset_t(self, v: float) -> float:
        return v + self.cfg.temp_offset_c

    # S4: 습도 오프셋 보정 후 0~100% 범위로 제한
    def _offset_h(self, v: float) -> float:
        return min(100.0, max(0.0, v + self.cfg.rh_offset_pct))

    # 그리드 시각 g마다 호출: S3 값 조회 → S4 보정 → S5 EMA → S6 파생 지표 계산
    def tick(self, g: float) -> ClimateSample:
        t, tf = self.temp.value_at(g)                                   # S3
        h, hf = self.rh.value_at(g)
        t = self._offset_t(t) if t is not None else None                # S4
        h = self._offset_h(h) if h is not None else None
        a = self.cfg.ema_alpha                                          # S5
        # EMA(지수이동평균): 새 값에 alpha, 이전 평균에 (1 - alpha) 가중치. 첫 값은 그대로 시작
        if t is not None:
            self._ema_t = t if self._ema_t is None else a * t + (1 - a) * self._ema_t
        if h is not None:
            self._ema_h = h if self._ema_h is None else a * h + (1 - a) * self._ema_h

        dp = ah = None                                                  # S6
        # 두 채널 모두 유효할 때만 이슬점·절대습도 계산
        if self._ema_t is not None and self._ema_h is not None and Flag.INVALID not in (tf, hf):
            dp = dew_point_c(self._ema_t, self._ema_h)
            ah = absolute_humidity_gm3(self._ema_t, self._ema_h)
        # dT/dt: dtdt_window_s 구간 동안의 EMA 온도 변화를 분당(°C/min)으로 환산
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

    # 채널별 처리 통계(거부·대체·수용 횟수)를 반환
    @property
    def stats(self) -> dict:
        return {"temp": dict(self.temp.stats), "rh": dict(self.rh.stats)}
