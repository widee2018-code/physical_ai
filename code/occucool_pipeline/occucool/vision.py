"""[V1]~[V6] 영상 분기.

V1 노출 고정·메모리 내 처리·ROI 마스크 → V2 적응형 샘플링 → V3 (타일) 검출
→ V4 검출 후처리 + 프레임 폐기 → V5 추적 → V6 발 위치 왜곡 보정·바닥 투영·구역 할당

V4 이후 단계는 영상을 전혀 참조하지 않는다. 호출자도 프레임을 저장하거나 전송하면 안 된다.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Tuple

import numpy as np

from .config import Point, Polygon, VisionConfig
from .utils import point_in_polygon

try:  # 선택 의존성
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

log = logging.getLogger("occucool.vision")


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------
# 검출된 사람 1명의 바운딩 박스(좌상단 x1,y1 ~ 우하단 x2,y2, 픽셀 단위)와 신뢰도(conf)
@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float
    scale: float = 1.0  # 원본 px → 모델 입력 px 배율 ([V4] 최소 크기 판정용)

    # 아래 속성들: 박스의 너비·높이·중심점·발 위치를 계산
    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def center(self) -> Point:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def foot(self) -> Point:
        """발 위치 = 박스 하단 중앙. 사선 시점에서 박스 중심 대신 사용 (보고서 2장 V6)."""
        return ((self.x1 + self.x2) / 2.0, self.y2)


# 두 박스의 IoU(겹친 넓이 ÷ 합친 넓이) 계산. 0이면 안 겹침, 1이면 완전히 같은 박스
def iou(a: Detection, b: Detection) -> float:
    # 교집합 사각형의 좌상단·우하단 좌표 (겹치지 않으면 넓이가 0 이하)
    ix1, iy1 = max(a.x1, b.x1), max(a.y1, b.y1)
    ix2, iy2 = min(a.x2, b.x2), min(a.y2, b.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    # 합집합 = 두 박스 넓이의 합 - 교집합 (교집합을 두 번 세지 않도록)
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union > 0 else 0.0


# 검출기 인터페이스: detect()만 구현하면 어떤 모델이든 교체해서 쓸 수 있다
class Detector(Protocol):
    def detect(self, image: np.ndarray) -> List[Detection]:
        """사람 박스를 입력 이미지 좌표로 반환."""


class UltralyticsDetector:
    """[V3] YOLO(Ultralytics) 사람 검출기. INT8/NCNN/Hailo 등으로 내보낸 가중치도 사용 가능."""

    def __init__(self, weights: str = "yolo11n.pt", imgsz: int = 640):
        from ultralytics import YOLO  # 지연 import

        self.model = YOLO(weights)
        self.imgsz = imgsz

    def detect(self, image: np.ndarray) -> List[Detection]:
        # 임계값은 [V4]에서 일괄 적용하므로 여기서는 낮게 둔다.
        res = self.model.predict(image, imgsz=self.imgsz, classes=[0], conf=0.1, verbose=False)[0]
        # 결과 박스를 Detection 객체로 변환 (classes=[0]은 COCO 데이터셋의 'person' 클래스)
        out = []
        for b in res.boxes:
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
            out.append(Detection(x1, y1, x2, y2, float(b.conf[0])))
        return out


# ---------------------------------------------------------------------------
# [V1] 캡처·마스킹
# ---------------------------------------------------------------------------
def lock_camera_exposure(cap) -> bool:
    """[V1] OpenCV VideoCapture의 자동 노출·화이트밸런스를 끈다.

    값의 의미는 백엔드(V4L2, GStreamer 등)마다 다르므로 실제 장비에서 확인해야 한다.
    """
    # OpenCV가 없으면 카메라 설정을 바꿀 수 없으므로 실패 반환
    if cv2 is None:
        return False
    ok1 = cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)  # V4L2: 0.25 = 수동 노출
    ok2 = cap.set(cv2.CAP_PROP_AUTO_WB, 0)
    return bool(ok1 and ok2)


class RoiMasker:
    """[V1] 정적 ROI 마스크. OpenCV가 없으면 [V4]에서 발 위치 기준으로 걸러낸다."""

    def __init__(self, polygon: Optional[Polygon]):
        self.polygon = polygon
        self._mask: Optional[np.ndarray] = None

    # ROI 다각형 바깥 영역을 검게(0) 지워 검출 대상에서 제외한다
    def apply(self, frame: np.ndarray) -> np.ndarray:
        # ROI가 없거나 OpenCV가 없으면 원본 프레임을 그대로 사용
        if not self.polygon or cv2 is None:
            return frame
        h, w = frame.shape[:2]
        # 마스크는 프레임 크기가 바뀔 때만 새로 만들고 재사용 (매 프레임 생성 비용 절약)
        if self._mask is None or self._mask.shape != (h, w):
            self._mask = np.zeros((h, w), np.uint8)
            cv2.fillPoly(self._mask, [np.array(self.polygon, np.int32)], 255)
        return cv2.bitwise_and(frame, frame, mask=self._mask)

    # 점이 ROI 안에 있는지 판정 (ROI 미설정이면 항상 True)
    def contains(self, pt: Point) -> bool:
        return (not self.polygon) or point_in_polygon(pt, self.polygon)


# ---------------------------------------------------------------------------
# [V2] 적응형 샘플링
# ---------------------------------------------------------------------------
class AdaptiveSampler:
    """[V2] 사람이 있거나 출입구에 움직임이 있으면 기본 속도, 아니면 저속으로 추론한다.

    움직임이 없어도 fps_vacant 주기의 추론은 항상 유지한다(정지한 재실자 누락 방지).
    """

    def __init__(self, cfg: VisionConfig):
        self.cfg = cfg
        self._last = -math.inf
        self._prev: Optional[np.ndarray] = None

    # 직전 프레임과의 밝기 차이로 움직임 여부를 판단한다
    def _motion(self, frame: np.ndarray) -> bool:
        r = self.cfg.motion_region
        # motion_region(x1, y1, x2, y2)이 있으면 그 영역(예: 출입구)만 잘라서 본다
        roi = frame if r is None else frame[r[1]:r[3], r[0]:r[2]]
        # 8픽셀 간격으로 솎아 연산량을 줄이고, 컬러 영상이면 채널 평균으로 흑백 변환
        small = roi[::8, ::8]
        gray = small.mean(axis=2) if small.ndim == 3 else small
        gray = gray.astype(np.float32)
        # 이전 프레임과의 픽셀 평균 절대차가 임계값보다 크면 움직임 (첫 프레임은 비교 대상이 없어 False)
        moved = (
            self._prev is not None
            and self._prev.shape == gray.shape
            and float(np.abs(gray - self._prev).mean()) > self.cfg.motion_threshold
        )
        # 다음 비교를 위해 현재 프레임을 저장
        self._prev = gray
        return moved

    # 이번 프레임에서 추론을 돌릴지 결정 (ts: 초 단위 타임스탬프, busy: 사람이 있다고 알려진 상태)
    def should_infer(self, frame: np.ndarray, ts: float, busy: bool) -> bool:
        motion = self._motion(frame)
        # 사람이 있거나 움직임이 있으면 빠른 fps, 아니면 느린 fps 선택
        fps = self.cfg.fps_occupied if (busy or motion) else self.cfg.fps_vacant
        # 마지막 추론 후 1/fps초가 지났으면 추론 (1e-3은 부동소수점 오차 허용치)
        if ts - self._last >= 1.0 / fps - 1e-3:
            self._last = ts
            return True
        return False


# ---------------------------------------------------------------------------
# [V3] 타일 검출
# ---------------------------------------------------------------------------
class TiledDetector:
    """[V3] 근거리는 전체 프레임, 원거리는 원본 해상도 타일로 추론하고 좌표를 원본으로 되돌린다."""

    def __init__(self, base: Detector, cfg: VisionConfig):
        self.base = base
        self.cfg = cfg

    def detect(self, frame: np.ndarray) -> List[Detection]:
        # 1) 전체 프레임 검출: 원본이 모델 입력 크기로 줄어드는 배율을 기록
        h, w = frame.shape[:2]
        full_scale = self.cfg.model_input_size / max(h, w)
        dets: List[Detection] = []
        for d in self.base.detect(frame):
            d.scale = full_scale
            dets.append(d)
        # 2) 원거리 타일 검출: 멀리 있어 작게 보이는 사람을 원본 해상도로 잘라서 따로 추론
        for (x0, y0, x1, y1) in self.cfg.far_tiles:
            tile = frame[y0:y1, x0:x1]
            # 타일이 프레임 밖이라 비어 있으면 건너뜀
            if tile.size == 0:
                continue
            s = self.cfg.model_input_size / max(tile.shape[0], tile.shape[1])
            # 타일 내부 좌표에 타일 시작점(x0, y0)을 더해 원본 프레임 좌표로 되돌린다
            for d in self.base.detect(tile):
                dets.append(Detection(d.x1 + x0, d.y1 + y0, d.x2 + x0, d.y2 + y0, d.conf, scale=s))
        return dets


# ---------------------------------------------------------------------------
# [V4] 검출 후처리
# ---------------------------------------------------------------------------
# NMS(비최대 억제): 같은 사람을 중복 검출한 박스 중 신뢰도가 가장 높은 것만 남긴다
def nms(dets: List[Detection], iou_thr: float) -> List[Detection]:
    keep: List[Detection] = []
    # 신뢰도 높은 순으로 보면서, 이미 남긴 박스와 많이 겹치면(IoU ≥ 임계값) 버린다
    for d in sorted(dets, key=lambda x: x.conf, reverse=True):
        if all(iou(d, k) < iou_thr for k in keep):
            keep.append(d)
    return keep


def postprocess(dets: List[Detection], cfg: VisionConfig, roi: Optional[RoiMasker] = None) -> List[Detection]:
    """[V4] 신뢰도 → 최소 박스 크기(모델 입력 기준) → 종횡비 → ROI → NMS."""
    keep = []
    for d in dets:
        # 신뢰도가 낮은 검출 제거
        if d.conf < cfg.conf_threshold:
            continue
        # 모델 입력 기준 박스 높이가 너무 작으면 제거 (원거리 노이즈 방지)
        if d.height * d.scale < cfg.min_box_height_px:
            continue
        # 세로/가로 비율이 너무 낮으면(옆으로 납작한 박스) 서 있는 사람이 아니라고 보고 제거
        if d.width <= 0 or d.height / d.width < cfg.min_aspect_ratio:
            continue
        # 발 위치가 ROI 밖이면 제거
        if roi is not None and not roi.contains(d.foot):
            continue
        keep.append(d)
    # 남은 박스끼리 중복 제거 후 반환
    return nms(keep, cfg.nms_iou)


# ---------------------------------------------------------------------------
# [V5] 추적
# ---------------------------------------------------------------------------
# 추적 중인 사람 1명의 상태 (tid: 트랙 번호, hits: 매칭 횟수, pending_*: 구역 변경 대기 정보)
@dataclass
class Track:
    tid: int
    box: Detection
    first_ts: float
    last_seen: float
    hits: int = 1
    confirmed: bool = False
    floor: Optional[Point] = None      # 최신 바닥 좌표
    origin: Optional[Point] = None     # 처음 관측된 바닥 좌표
    zone: Optional[str] = None
    pending_zone: Optional[str] = None
    pending_count: int = 0


class IoUTracker:
    """[V5] 외형 특징 없는 경량 트래커.

    - 매칭: IoU ≥ 임계값 또는 중심거리 ≤ 박스높이×비율 (2fps 보행 이동 대응)
    - 확정: 연속 N프레임 매칭 (미확정 트랙은 한 번이라도 놓치면 폐기)
    - 소실 유지: 확정 트랙은 lost_buffer 동안 미관측이어도 유지·카운트
    """

    def __init__(self, cfg: VisionConfig):
        self.cfg = cfg
        self.tracks: Dict[int, Track] = {}
        self._next_id = 1

    # 트랙과 검출의 매칭 점수: IoU가 충분하면 1~2점, 아니면 중심거리 기반 0~1점, 매칭 불가면 0점
    def _score(self, t: Track, d: Detection) -> float:
        s = iou(t.box, d)
        if s >= self.cfg.track_match_iou:
            return 1.0 + s
        # IoU가 낮아도 중심 거리가 '박스 높이 × 비율' 이내면 같은 사람으로 본다 (가까울수록 높은 점수)
        (tx, ty), (dx, dy) = t.box.center, d.center
        gate = self.cfg.track_max_center_dist_ratio * max(t.box.height, 1.0)
        dist = math.hypot(tx - dx, ty - dy)
        return 1.0 - dist / gate if dist <= gate else 0.0

    def update(self, dets: List[Detection], ts: float) -> Tuple[List[Track], List[Track], List[Track]]:
        """반환: (활성 확정 트랙, 이번에 새로 확정된 트랙, 제거된 확정 트랙)."""
        # 1) 모든 (트랙, 검출) 쌍의 점수를 계산해 높은 순으로 정렬
        pairs = sorted(
            ((self._score(t, d), tid, i) for tid, t in self.tracks.items() for i, d in enumerate(dets)),
            reverse=True,
        )
        # 2) 점수가 높은 쌍부터 1:1로 매칭 (이미 쓰인 트랙·검출은 건너뜀)
        used_t, used_d = set(), set()
        for score, tid, i in pairs:
            # 점수 0 이하부터는 모두 매칭 불가이므로 종료
            if score <= 0:
                break
            if tid in used_t or i in used_d:
                continue
            used_t.add(tid)
            used_d.add(i)
            t = self.tracks[tid]
            # 매칭된 트랙의 박스·마지막 관측 시각·매칭 횟수를 갱신
            t.box, t.last_seen, t.hits = dets[i], ts, t.hits + 1

        # 3) 기존 트랙 정리: 확정 승격 / 미확정 폐기 / 오래 소실된 확정 트랙 제거
        newly, removed = [], []
        for tid in list(self.tracks):
            t = self.tracks[tid]
            if tid in used_t:
                # 매칭 횟수가 기준 프레임 수에 도달하면 확정 트랙으로 승격
                if not t.confirmed and t.hits >= self.cfg.track_confirm_frames:
                    t.confirmed = True
                    newly.append(t)
                continue
            # 이번에 매칭되지 않은 미확정 트랙은 즉시 폐기 (잡음 검출 방지)
            if not t.confirmed:
                del self.tracks[tid]
            # 확정 트랙은 lost_buffer(초) 동안 안 보여도 유지하고, 그보다 오래 안 보이면 제거
            elif ts - t.last_seen > self.cfg.track_lost_buffer_s:
                removed.append(t)
                del self.tracks[tid]

        # 4) 매칭되지 않은 검출은 새 트랙으로 생성
        for i, d in enumerate(dets):
            if i in used_d:
                continue
            t = Track(self._next_id, d, ts, ts)
            self._next_id += 1
            # 확정 기준이 1프레임 이하면 생성 즉시 확정
            if self.cfg.track_confirm_frames <= 1:
                t.confirmed = True
                newly.append(t)
            self.tracks[t.tid] = t

        # 확정된 트랙만 인원수 계산에 사용
        active = [t for t in self.tracks.values() if t.confirmed]
        return active, newly, removed


# ---------------------------------------------------------------------------
# [V6] 발 위치 왜곡 보정·바닥 투영·구역 할당
# ---------------------------------------------------------------------------
class FloorProjector:
    """[V6] 전체 프레임이 아니라 발 위치 점 하나만 Undistort + Homography 변환한다."""

    def __init__(self, cfg: VisionConfig):
        self.K = np.array(cfg.camera_matrix, float) if cfg.camera_matrix else None
        self.D = np.array(cfg.dist_coeffs, float) if cfg.dist_coeffs else None
        self.H = np.array(cfg.homography, float) if cfg.homography else None

    # 이미지 픽셀 좌표(발 위치) → 바닥 평면 좌표로 변환
    def to_floor(self, pt: Point) -> Point:
        x, y = pt
        # 1) 렌즈 왜곡 보정: 카메라 행렬 K와 왜곡 계수 D로 점 하나만 보정 (P=K로 다시 픽셀 좌표계로 돌림)
        if self.K is not None and self.D is not None and cv2 is not None:
            p = cv2.undistortPoints(np.array([[[x, y]]], np.float32), self.K, self.D, P=self.K)
            x, y = float(p[0, 0, 0]), float(p[0, 0, 1])
        # 2) 호모그래피 H로 바닥에 투영: [x, y, 1]에 H를 곱한 뒤 마지막 성분으로 나눈다
        if self.H is not None:
            v = self.H @ np.array([x, y, 1.0])
            if abs(v[2]) > 1e-9:
                return (float(v[0] / v[2]), float(v[1] / v[2]))
        # 보정 정보가 없거나 투영할 수 없으면 (왜곡 보정만 된) 픽셀 좌표를 그대로 반환
        return (x, y)


class ZoneAssigner:
    """[V6] 트랙 단위 구역 할당 + 경계 히스테리시스 (구역 변경은 N프레임 연속일 때만 확정)."""

    # 구역을 정의하지 않았을 때 쓰는 단일 구역 이름
    DEFAULT = "all"

    def __init__(self, cfg: VisionConfig):
        self.zones = cfg.zones
        self.n = max(1, cfg.zone_switch_frames)

    # 집계에 사용할 구역 이름 목록
    @property
    def names(self) -> List[str]:
        return list(self.zones) or [self.DEFAULT]

    # 바닥 좌표가 속한 구역을 바로 판정 (히스테리시스 적용 전 원시값, 어느 구역에도 없으면 None)
    def _raw(self, pt: Optional[Point]) -> Optional[str]:
        if not self.zones:
            return self.DEFAULT
        if pt is None:
            return None
        for name, poly in self.zones.items():
            if point_in_polygon(pt, poly):
                return name
        return None

    # 트랙의 구역을 갱신. 다른 구역이 N프레임 연속 나올 때만 바꿔 경계에서 왔다 갔다 하는 것을 막는다
    def update(self, t: Track) -> None:
        raw = self._raw(t.floor)
        if raw is None:  # 구역 밖: 기존 구역 유지
            return
        # 처음 구역이 정해지는 경우에는 바로 할당
        if t.zone is None:
            t.zone = raw
            return
        # 현재 구역과 같으면 변경 대기 상태 초기화
        if raw == t.zone:
            t.pending_zone, t.pending_count = None, 0
            return
        # 같은 후보 구역이 연속으로 나오면 카운트 증가, 새 후보면 1부터 다시 시작
        if raw == t.pending_zone:
            t.pending_count += 1
        else:
            t.pending_zone, t.pending_count = raw, 1
        # N프레임 연속이면 구역 변경 확정
        if t.pending_count >= self.n:
            t.zone, t.pending_zone, t.pending_count = raw, None, 0


# ---------------------------------------------------------------------------
# V1~V6 통합
# ---------------------------------------------------------------------------
# 한 프레임 처리 결과: 구역별 인원, 실제 트랙 수(live), 유령 수, 후처리 전/후 검출 수
@dataclass
class VisionResult:
    ts: float
    zone_counts: Dict[str, int]
    live: int
    ghosts: int
    n_raw: int
    n_filtered: int

    @property
    def total(self) -> int:
        return sum(self.zone_counts.values())


# 사각지대로 사라진 사람을 잠시 인원에 남겨두는 '유령' 기록 (expires: 만료 시각)
@dataclass
class _Ghost:
    zone: Optional[str]
    floor: Optional[Point]
    expires: float


class VisionFrontEnd:
    """[V1]~[V6]을 순서대로 실행하고 프레임 단위 구역별 인원을 반환한다.

    위험 1위(사각으로 인한 거짓 공실) 대응: 출입구를 거치지 않고 사라진 확정 트랙은
    '유령(ghost)'으로 ghost_hold_s 동안 카운트에 남긴다. 출입구 밖에서 새로 확정된
    트랙이 나타나면 사각에서 돌아온 것으로 보고 가장 가까운 유령 1명을 지운다.
    """

    def __init__(self, cfg: VisionConfig, detector: Detector):
        # 각 단계(V1~V6) 모듈 생성
        self.cfg = cfg
        self.masker = RoiMasker(cfg.roi_polygon)
        self.sampler = AdaptiveSampler(cfg)
        self.detector = TiledDetector(detector, cfg)
        self.tracker = IoUTracker(cfg)
        self.projector = FloorProjector(cfg)
        self.zone_assigner = ZoneAssigner(cfg)
        # 유령 목록과 추론한 프레임 수 카운터
        self.ghosts: List[_Ghost] = []
        self.frames_inferred = 0
        if cfg.entrance_zone is None:
            log.warning("entrance_zone 미설정: 출입구 기반 퇴실 보정(유령 유지)을 끕니다. 조사표 1-6 확인 필요.")

    # 바닥 좌표가 출입구 구역 안에 있는지 확인
    def _in_entrance(self, pt: Optional[Point]) -> bool:
        return pt is not None and self.cfg.entrance_zone is not None and point_in_polygon(pt, self.cfg.entrance_zone)

    # 새로 나타난 트랙과 가장 가까운 유령의 인덱스를 찾는다 (같은 구역 우선, 그다음 거리 순)
    def _nearest_ghost(self, t: Track) -> int:
        def key(i: int) -> Tuple[int, float]:
            g = self.ghosts[i]
            same = 0 if g.zone == t.zone else 1
            if g.floor is None or t.floor is None:
                return (same, math.inf)
            return (same, math.hypot(g.floor[0] - t.floor[0], g.floor[1] - t.floor[1]))
        return min(range(len(self.ghosts)), key=key)

    def process(self, frame: np.ndarray, ts: float, occupied: bool) -> Optional[VisionResult]:
        # 재실 중이거나 추적·유령이 남아 있으면 '바쁨'으로 보고 빠르게 샘플링
        busy = occupied or bool(self.tracker.tracks) or bool(self.ghosts)
        if not self.sampler.should_infer(frame, ts, busy):          # V2
            return None
        masked = self.masker.apply(frame)                            # V1
        raw = self.detector.detect(masked)                           # V3
        del masked                                                   # V4: 이후 영상 미참조
        dets = postprocess(raw, self.cfg, self.masker)               # V4
        active, newly, removed = self.tracker.update(dets, ts)       # V5
        self.frames_inferred += 1

        for t in self.tracker.tracks.values():                       # V6
            # 이번 프레임에 관측된 트랙만 발 위치를 바닥 좌표로 바꾸고 구역 갱신
            if t.last_seen == ts:
                t.floor = self.projector.to_floor(t.box.foot)
                # 처음 관측된 바닥 위치를 기억 (출입구에서 들어왔는지 판단용)
                if t.origin is None:
                    t.origin = t.floor
                self.zone_assigner.update(t)

        # 유령 처리: 출입구가 아닌 곳에서 사라진 트랙은 사각에 있다고 보고 유령으로 등록
        if self.cfg.entrance_zone is not None:
            for t in removed:
                if not self._in_entrance(t.floor):
                    self.ghosts.append(_Ghost(t.zone, t.floor, ts + self.cfg.ghost_hold_s))
            # 출입구가 아닌 곳에서 새로 나타난 트랙은 사각에서 돌아온 것 → 가장 가까운 유령 1명 제거
            for t in newly:
                if self.ghosts and not self._in_entrance(t.origin):
                    self.ghosts.pop(self._nearest_ghost(t))
        # 유지 시간이 지난 유령은 삭제
        self.ghosts = [g for g in self.ghosts if g.expires > ts]

        # 구역별 인원 집계: 활성 확정 트랙 + 유령 (구역이 없으면 'unassigned')
        counts = {name: 0 for name in self.zone_assigner.names}
        for t in active:
            z = t.zone or "unassigned"
            counts[z] = counts.get(z, 0) + 1
        for g in self.ghosts:
            z = g.zone or "unassigned"
            counts[z] = counts.get(z, 0) + 1
        # 원시/후처리 검출 수도 함께 반환 (진단용)
        return VisionResult(ts, counts, len(active), len(self.ghosts), len(raw), len(dets))
