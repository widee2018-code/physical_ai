"""OccuCool 파이프라인 설정값.

보고서 3장 '필터별 초기 파라미터'의 권장값을 기본값으로 둔다.
각 필드 주석의 [코드]는 보고서 2장 파이프라인의 단계 번호다.
보고서에 값이 없어 설계상 정한 값은 '가정'으로 표시했다. 현장 확인 후 바꿔야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

# 타입 별칭: 좌표점(x, y), 다각형(점 목록), 사각형 영역
Point = Tuple[float, float]
Polygon = List[Point]
Rect = Tuple[int, int, int, int]  # (x0, y0, x1, y1) 픽셀


# 카메라 영상 처리(V1~V6) 관련 설정
@dataclass
class VisionConfig:
    # [V1] 캡처·마스킹
    roi_polygon: Optional[Polygon] = None          # 이미지 좌표. None이면 전체 사용
    lock_exposure: bool = True  # True면 카메라 자동 노출을 고정해 밝기 변화로 인한 오검출을 줄임
    # [V2] 적응형 샘플링
    fps_occupied: float = 2.0  # 사람이 있을 때 초당 처리 프레임 수
    fps_vacant: float = 0.5  # 공실일 때는 2초에 1장만 처리해 연산량 절약
    motion_region: Optional[Rect] = None           # 출입구 영역(이미지 좌표). None이면 전체 프레임
    motion_threshold: float = 8.0                  # 가정: 축소 그레이 프레임 평균 절대차(0~255)
    # [V3] 검출
    model_input_size: int = 640  # 검출 모델 입력 이미지 한 변 크기(px)
    far_tiles: List[Rect] = field(default_factory=list)   # 사선 설치 시 원거리 타일
    # [V4] 검출 후처리
    conf_threshold: float = 0.40
    min_box_height_px: float = 24.0                # 모델 입력 해상도 기준
    min_aspect_ratio: float = 1.2                  # 높이/폭
    nms_iou: float = 0.50
    # [V5] 추적
    track_match_iou: float = 0.30                  # 가정
    track_max_center_dist_ratio: float = 0.6       # 가정: IoU가 낮을 때 중심거리 ≤ 박스높이×비율이면 매칭
    track_confirm_frames: int = 3
    track_lost_buffer_s: float = 10.0
    ghost_hold_s: float = 300.0                    # 가정: 출입구를 거치지 않고 사라진 사람 유지 시간(위험 1위 대응)
    # [V6] 바닥 투영·구역 할당
    camera_matrix: Optional[List[List[float]]] = None
    dist_coeffs: Optional[List[float]] = None
    homography: Optional[List[List[float]]] = None  # 이미지(px) → 바닥(m)
    zones: Dict[str, Polygon] = field(default_factory=dict)   # 바닥 좌표(m). 비우면 단일 구역 "all"
    entrance_zone: Optional[Polygon] = None        # 바닥 좌표(m)
    zone_switch_frames: int = 2                    # 가정: 구역 변경 확정 프레임 수(경계 히스테리시스)


# 재실 인원 수 정제(V7)와 재실/공실 판정(V9) 설정
@dataclass
class OccupancyConfig:
    # [V7]
    median_window: int = 5  # 중앙값 필터 창 크기(샘플 수)
    hampel_window: int = 7  # Hampel 이상치 필터 창 크기
    hampel_k: float = 3.0  # 중앙값에서 MAD×k 이상 벗어나면 이상치로 판단
    hampel_min_scale: float = 0.5                  # 가정: 정수 카운트용 MAD 하한
    # [V9]
    entry_debounce_s: float = 30.0  # 입실 판정 전 대기 시간(초): 잠깐 지나가는 사람 무시
    vacancy_confirm_s: float = 300.0  # 공실 확정까지 대기 시간(초, 5분)
    level_thresholds: Tuple[int, ...] = (1, 3)     # 단계1: 1~2명, 단계2: 3명 이상
    level_margin: int = 1  # 단계 하향 시 경계값보다 이만큼 더 내려가야 변경(떨림 방지)
    level_hold_s: float = 120.0  # 단계 변경 후 최소 유지 시간(초)
    missing_hold_s: float = 30.0  # 인원 데이터 결측 시 직전 값을 유지하는 시간(초)
    missing_fault_s: float = 300.0  # 결측이 이 시간 이상 지속되면 고장으로 판단(초)


# 실내 온도·습도 센서 정제(S1~S6) 설정
@dataclass
class ClimateConfig:
    # [S1]
    temp_range: Tuple[float, float] = (0.0, 45.0)  # 온도 유효 범위(℃), 벗어나면 무효 처리
    rh_range: Tuple[float, float] = (5.0, 95.0)  # 상대습도 유효 범위(%)
    # [S2]
    temp_rate_limit_per_10s: float = 0.5  # 10초당 최대 허용 온도 변화(℃), 초과 시 튀는 값으로 간주
    rh_rate_limit_per_10s: float = 5.0             # 가정
    hampel_window: int = 7  # Hampel 필터 창 크기
    hampel_k: float = 3.0  # Hampel 이상치 판정 배수
    temp_hampel_min_scale: float = 0.05            # 가정
    rh_hampel_min_scale: float = 0.5               # 가정
    reject_streak_accept: int = 3                  # 가정: 같은 수준의 값이 연속으로 거부되면 실제 변화로 수용
    # [S3]
    ffill_s: float = 30.0  # 결측 시 이 시간까지는 직전 값으로 채움(초)
    interp_s: float = 300.0  # 이 시간까지는 선형 보간, 그 이상은 무효(초)
    # [S4] 기준 온도계와 24시간 비교 후 입력
    temp_offset_c: float = 0.0
    rh_offset_pct: float = 0.0
    # [S5]
    ema_alpha: float = 0.2  # 지수이동평균 가중치(클수록 최신 값 반영 큼)
    # [S6]
    dtdt_window_s: float = 60.0                    # 가정


# 에어컨 명령-피드백 대조(A2) 설정
@dataclass
class HVACConfig:
    # [A2]
    confirm_s: float = 30.0  # 명령 후 이 시간 안에 피드백이 일치해야 성공(초)
    max_retries: int = 2  # 실패 시 최대 재전송 횟수
    manual_hold_s: float = 3600.0  # 수동 조작 감지 후 자동 제어를 멈추는 시간(초, 1시간)
    alarm_cooldown_s: float = 600.0                # 가정: 명령 실패 알림 후 자동 제어 정지 시간
    energy_mismatch_minutes: int = 3               # 가정: 전력-상태 불일치 지속 시 알림


# 제어 정책(설정온도·Setback·제습)과 출력 보호(P1) 설정
@dataclass
class ControlConfig:
    season: str = "cool"                           # "cool" | "heat"
    base_setpoint_cool_c: float = 24.0             # 가정: 운영자 정책
    base_setpoint_heat_c: float = 22.0             # 가정
    level2_boost_c: float = 1.0                    # 가정: 3명 이상일 때 냉·난방 강화폭
    setback_c: float = 2.0  # 공실 시 설정온도를 완화하는 폭(℃)
    vacancy_off_after_s: float = 1800.0            # 가정: Setback 후 OFF까지(2단계 전략)
    # [P1]
    deadband_c: float = 0.5  # 설정온도 차이가 이보다 작으면 명령 생략(℃)
    min_on_s: float = 300.0  # 압축기 보호: 켠 뒤 최소 유지 시간(초)
    min_off_s: float = 300.0  # 압축기 보호: 끈 뒤 최소 유지 시간(초)
    setpoint_rate_c: float = 1.0  # 변화율 창 동안 허용되는 설정온도 총 변화량(℃)
    setpoint_rate_window_s: float = 600.0  # 변화율 제한 창 길이(초)
    rate_limit_both_directions: bool = False       # 가정: False면 쾌적 방향(재실 복귀)은 즉시 허용
    # 제습 (S6 이후 제어 판단)
    dehum_enter_rh: float = 65.0  # 습도가 이 값(%) 이상이면 제습 진입 후보
    dehum_enter_dewpoint_c: float = 18.0  # 이슬점이 이 값(℃) 이상이어도 제습 진입 후보
    dehum_enter_hold_s: float = 600.0  # 진입 조건이 이 시간 동안 지속돼야 제습 시작(초)
    dehum_exit_rh: float = 55.0  # 습도가 이 값(%) 이하로 내려가면 제습 해제 후보
    dehum_exit_dewpoint_margin_c: float = 1.0      # 가정
    shadow_mode: bool = True                       # 보고서 권장: 첫 1~2주는 그림자 모드


# 외기 온도 데이터 정제(O1) 설정
@dataclass
class OutdoorConfig:
    # [O1]
    valid_range: Tuple[float, float] = (-30.0, 50.0)  # 외기 온도 유효 범위(℃)
    median_window: int = 5  # 중앙값 필터 창 크기
    ema_tau_s: float = 600.0  # EMA 시정수(초)
    sample_period_s: float = 60.0  # 외기 데이터 수집 주기(초)
    ffill_s: float = 300.0  # 결측 시 직전 값 유지 시간(초)
    interp_s: float = 1800.0  # 결측 시 선형 보간 허용 시간(초)


# 운영 시간표(O2) 설정: 이 시간대를 '운영 중'으로 본다
@dataclass
class ScheduleConfig:
    # [O2] 조사표 7-2 공란 → 가정값
    timezone: str = "Asia/Seoul"
    weekdays: Tuple[int, ...] = (0, 1, 2, 3, 4)    # 월~금
    start: str = "09:00"  # 운영 시작 시각
    end: str = "18:00"  # 운영 종료 시각
    precool_min: float = 30.0  # 운영 시작 전 미리 냉방하는 시간(분)
    holidays: FrozenSet[str] = frozenset()         # "YYYY-MM-DD"


# 전력 측정(E1)·기준선 정규화(E2) 설정
@dataclass
class EnergyConfig:
    # [E1]
    rated_power_w: float = 3500.0                  # 가정: 에어컨 모델 확인 필요
    fan_threshold_w: float = 30.0                  # 가정
    compressor_ratio: float = 0.2  # 정격의 20% 이상 소비하면 압축기 가동으로 분류
    # [E2]
    cdd_base_c: float = 24.0                       # 가정


# 전체 파이프라인 설정: 위의 모든 하위 설정을 하나로 묶는다
@dataclass
class PipelineConfig:
    grid_s: float = 10.0                           # [V8][S5][A1][M1] 공통 그리드
    control_period_s: float = 60.0                 # [M3] 제어 주기
    vision: VisionConfig = field(default_factory=VisionConfig)
    occupancy: OccupancyConfig = field(default_factory=OccupancyConfig)
    climate: ClimateConfig = field(default_factory=ClimateConfig)
    hvac: HVACConfig = field(default_factory=HVACConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    outdoor: OutdoorConfig = field(default_factory=OutdoorConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    energy: EnergyConfig = field(default_factory=EnergyConfig)
