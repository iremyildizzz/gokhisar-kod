"""Hedef Takip Modülü — ByteTrack + KTR Kalman gelecek tahmini.

KTR 4.2.2.4:
  • ByteTrack: IoU + Macar; yüksek güven önce, düşük güven telafi turu
  • Ölçüm yokken yalnız tahmin adımı (predict)
  • ByteTrack'ten bağımsız ikinci Kalman: merkez konumunu yumuşatır /
    kısa kayıpta gelecek konumu tahmin eder (ServoKalman + per-track)

Akış:
  tespitler → ByteTrack (kimlik) → her iz için TrackKalman
  ölçüm var  → predict+correct → filtrelenmiş kutu
  ölçüm yok  → predict_only   → kutu hızla ilerler (donmuş hayalet yok)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
# pyrefly: ignore [missing-import]
import supervision as sv

import config
from detection.yolo_detector import Detection


def _as_float(x) -> float:
    """NumPy 2.x: float(array([v])) TypeError verir — tek skaler çıkar."""
    if isinstance(x, np.ndarray):
        return float(x.reshape(-1)[0])
    return float(x)


def _as_int(x) -> int:
    if isinstance(x, np.ndarray):
        return int(x.reshape(-1)[0])
    return int(x)


def _box_iou(a: np.ndarray, b: np.ndarray) -> float:
    """İki xyxy kutusu arasındaki IoU."""
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    if intersection <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return float(intersection / union) if union > 0 else 0.0


def _box_center_dist(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    acx, acy = (a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0
    bcx, bcy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
    return math.hypot(acx - bcx, acy - bcy)


def boxes_same_object(a: np.ndarray, b: np.ndarray,
                      iou_threshold: float | None = None,
                      center_ratio: float | None = None) -> bool:
    """IoU veya merkez yakınlığına göre iki kutunun aynı nesneye ait olup olmadığı."""
    iou_threshold = config.DEDUPE_IOU if iou_threshold is None else iou_threshold
    center_ratio = config.DEDUPE_CENTER_RATIO if center_ratio is None else center_ratio
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    if _box_iou(a, b) > iou_threshold:
        return True
    max_side = max(a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1])
    return _box_center_dist(a, b) < max_side * center_ratio


def detections_same_object(a: Detection, b: Detection,
                           iou_threshold: float | None = None,
                           center_ratio: float | None = None) -> bool:
    """Detection çifti için aynı-nesne testi."""
    if a.class_id != b.class_id:
        return False
    return boxes_same_object(a.as_xyxy(), b.as_xyxy(), iou_threshold, center_ratio)


def _detection_at_center(
    det: Detection, cx: float, cy: float, *, conf: float | None = None
) -> Detection:
    """Aynı boyutta, yeni merkezli kutu (Kalman tahmini için)."""
    w = max(1.0, det.x2 - det.x1)
    h = max(1.0, det.y2 - det.y1)
    return Detection(
        cx - w / 2.0,
        cy - h / 2.0,
        cx + w / 2.0,
        cy + h / 2.0,
        conf=det.conf if conf is None else conf,
        class_id=det.class_id,
        source=det.source,
    )


class TrackKalman:
    """Sabit-hız Kalman — merkez (cx, cy) + hız (vx, vy).

    KTR: ölçüm varken doğru/yumuşat; ölçüm yokken gelecek tahmini.
    Hızlı balon için süreç gürültüsü yüksek, ölçüme güven yüksek tutulur
    (kutu geride kalmasın).
    """

    def __init__(self) -> None:
        q = float(getattr(config, "TRACK_KALMAN_PROCESS", 5e-2))
        r = float(getattr(config, "TRACK_KALMAN_MEASURE", 1e-2))
        self.kf = cv2.KalmanFilter(4, 2)
        self.kf.transitionMatrix = np.array(
            [[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]],
            np.float32,
        )
        self.kf.measurementMatrix = np.array(
            [[1, 0, 0, 0], [0, 1, 0, 0]], np.float32
        )
        self.kf.processNoiseCov = np.eye(4, dtype=np.float32) * q
        self.kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * r
        self.kf.errorCovPost = np.eye(4, dtype=np.float32)
        self.initialized = False

    def update(self, cx: float, cy: float) -> tuple[float, float]:
        meas = np.array([[np.float32(cx)], [np.float32(cy)]])
        if not self.initialized:
            self.kf.statePost = np.array(
                [[np.float32(cx)], [np.float32(cy)], [0], [0]], np.float32
            )
            self.initialized = True
        self.kf.predict()
        est = self.kf.correct(meas)
        return _as_float(est[0, 0]), _as_float(est[1, 0])

    def predict_only(self) -> tuple[float, float]:
        """Ölçüm yok — KTR 'yalnız tahmin adımı'."""
        if not self.initialized:
            return 0.0, 0.0
        pred = self.kf.predict()
        return _as_float(pred[0, 0]), _as_float(pred[1, 0])

    @property
    def velocity(self) -> tuple[float, float]:
        if not self.initialized:
            return 0.0, 0.0
        s = self.kf.statePost
        return _as_float(s[2, 0]), _as_float(s[3, 0])


@dataclass
class TrackedTarget:
    track_id: int
    det: Detection
    age: int = 0                 # kaç karedir takipte
    misses: int = 0              # ardışık kayıp kare sayısı
    center_history: list = field(default_factory=list)
    servo_corrections: list = field(default_factory=list)
    predicted: bool = False      # bu karede kutu Kalman tahmini mi?


class TargetTracker:
    def __init__(self, fps: int = 30):
        self.tracker = sv.ByteTrack(
            track_activation_threshold=config.TRACK_HIGH_CONF,
            minimum_matching_threshold=config.TRACK_MATCH_IOU,
            lost_track_buffer=config.TRACK_BUFFER,
            frame_rate=fps,
            minimum_consecutive_frames=2,
        )
        self.targets: dict[int, TrackedTarget] = {}
        self._kalmans: dict[int, TrackKalman] = {}

    def set_fps(self, fps: int) -> None:
        """Ölçülen pipeline FPS → ByteTrack zaman adımı (Sena track update)."""
        if hasattr(self.tracker, "frame_rate"):
            self.tracker.frame_rate = max(1, int(fps))

    def _kf(self, track_id: int) -> TrackKalman:
        if track_id not in self._kalmans:
            self._kalmans[track_id] = TrackKalman()
        return self._kalmans[track_id]

    def _drop(self, track_id: int) -> None:
        self.targets.pop(track_id, None)
        self._kalmans.pop(track_id, None)

    def _coast_missing(self, tid: int) -> None:
        """Ölçüm yok: Kalman predict + kutuyu ilerlet (hayalet donmasın)."""
        t = self.targets[tid]
        t.misses += 1
        if t.misses > config.TRACK_BUFFER:
            self._drop(tid)
            return
        kf = self._kf(tid)
        if not kf.initialized:
            return
        cx, cy = kf.predict_only()
        # Güveni hafif düşür — tahmin olduğunu ayırt etmek için
        conf = max(0.05, t.det.conf * 0.92)
        t.det = _detection_at_center(t.det, cx, cy, conf=conf)
        t.predicted = True
        t.center_history.append((cx, cy))
        if len(t.center_history) > 60:
            t.center_history.pop(0)

    @staticmethod
    def _suppress_overlapping(tracked: sv.Detections) -> sv.Detections:
        """Aynı sınıfta yüksek örtüşen takiplerden yalnızca en güvenilir olanı bırak."""
        n = len(tracked)
        if n <= 1:
            return tracked

        order = sorted(
            range(n), key=lambda i: _as_float(tracked.confidence[i]), reverse=True
        )
        keep: list[int] = []
        for i in order:
            cls_i = _as_int(tracked.class_id[i])
            box_i = tracked.xyxy[i]
            if any(
                _as_int(tracked.class_id[j]) == cls_i
                and boxes_same_object(
                    box_i, tracked.xyxy[j], iou_threshold=config.TRACK_DEDUPE_IOU
                )
                for j in keep
            ):
                continue
            keep.append(i)
        return tracked[keep]

    @staticmethod
    def _to_sv(detections: list[Detection]) -> sv.Detections:
        """Birleşik tespit listesi -> supervision formatı."""
        valid_dets = [d for d in detections if d.conf >= config.TRACK_LOW_CONF]
        if not valid_dets:
            return sv.Detections.empty()
        return sv.Detections(
            xyxy=np.array([d.as_xyxy() for d in valid_dets]),
            confidence=np.array([d.conf for d in valid_dets]),
            class_id=np.array([d.class_id for d in valid_dets]),
        )

    def update(self, detections: list[Detection]) -> dict[int, TrackedTarget]:
        """ByteTrack kimlik + TrackKalman konum (ölçüm / tahmin)."""
        tracked = self.tracker.update_with_detections(self._to_sv(detections))
        if len(tracked) == 0 or tracked.tracker_id is None:
            for tid in list(self.targets):
                self._coast_missing(tid)
            return self.targets

        tracked = self._suppress_overlapping(tracked)
        if len(tracked) == 0 or tracked.tracker_id is None:
            for tid in list(self.targets):
                self._coast_missing(tid)
            return self.targets

        seen: set[int] = set()
        for xyxy, conf, cls_id, tid in zip(
            tracked.xyxy,
            tracked.confidence,
            tracked.class_id,
            tracked.tracker_id,
        ):
            tid = _as_int(tid)
            seen.add(tid)
            x1, y1, x2, y2 = (float(v) for v in np.asarray(xyxy).reshape(-1)[:4])
            raw = Detection(
                x1,
                y1,
                x2,
                y2,
                conf=_as_float(conf),
                class_id=_as_int(cls_id),
                source="track",
            )
            # KF'yi sadece miss coast için besle. Ölçüm varken ham merkez kullan —
            # filtrelenmiş kutu gecikince taret önce geçer, sonra geri gelir (üst→alt→orta).
            self._kf(tid).update(raw.cx, raw.cy)
            det = raw
            fx, fy = raw.cx, raw.cy

            if tid in self.targets:
                t = self.targets[tid]
                t.det = det
                t.age += 1
                t.misses = 0
                t.predicted = False
            else:
                t = TrackedTarget(track_id=tid, det=det, age=1, predicted=False)
                self.targets[tid] = t
            t.center_history.append((fx, fy))
            if len(t.center_history) > 60:
                t.center_history.pop(0)

        for tid in list(self.targets):
            if tid in seen:
                continue
            t = self.targets[tid]
            # Çift iz: aktif ölçümlü örtüşen varsa düş
            if any(
                other.det.class_id == t.det.class_id
                and boxes_same_object(
                    t.det.as_xyxy(),
                    other.det.as_xyxy(),
                    iou_threshold=config.TRACK_DEDUPE_IOU,
                )
                for other_id, other in self.targets.items()
                if other_id in seen
            ):
                self._drop(tid)
                continue
            self._coast_missing(tid)

        return self.targets

    @staticmethod
    def stability(t: TrackedTarget) -> float:
        """Merkez geçmişindeki oynaklıktan 0-1 arası kararlılık metriği."""
        if len(t.center_history) < 5:
            return 0.0
        pts = np.array(t.center_history[-20:])
        jitter = float(np.mean(np.std(pts, axis=0)))
        return 1.0 / (1.0 + jitter / 10.0)


class ServoKalman:
    """Servoya giden merkez — yumuşak CV Kalman (sıçramasız).

    lead_time varsayılan 0: hız gürültüsü tavan/fırlama yapıyordu.
    Çıkış kareler arası SERVO_TARGET_MAX_JUMP_PX ile sınırlanır.
    """

    def __init__(self, lead_time: float | None = None):
        self.lead_time = float(
            lead_time
            if lead_time is not None
            else getattr(config, "SERVO_KALMAN_LEAD_S", 0.0)
        )
        self._max_jump = float(getattr(config, "SERVO_TARGET_MAX_JUMP_PX", 40.0))
        self._max_speed = 600.0  # px/s — absürt vy/vx kes
        self.kf = cv2.KalmanFilter(4, 2)
        self.kf.measurementMatrix = np.array(
            [[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32
        )
        # Ölçüme daha çok güven; hızı az serbest bırak (Sena 0.8 çok agresifti)
        self.kf.processNoiseCov = np.diag([0.08, 0.08, 0.15, 0.15]).astype(np.float32)
        self.kf.measurementNoiseCov = np.diag([0.08, 0.08]).astype(np.float32)
        self.kf.errorCovPost = np.eye(4, dtype=np.float32)
        self._set_dt(0.033)
        self.initialized = False
        self._last_out: tuple[float, float] | None = None

    def _set_dt(self, dt: float) -> None:
        dt = max(0.001, min(float(dt), 0.2))
        self.kf.transitionMatrix = np.array(
            [[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]],
            dtype=np.float32,
        )

    def reset(self) -> None:
        self.kf.statePost = np.zeros((4, 1), dtype=np.float32)
        self.kf.statePre = np.zeros((4, 1), dtype=np.float32)
        self.kf.errorCovPost = np.eye(4, dtype=np.float32)
        self.initialized = False
        self._last_out = None

    def _clamp_speed(self) -> None:
        sp = self.kf.statePost
        vx = float(np.clip(_as_float(sp[2, 0]), -self._max_speed, self._max_speed))
        vy = float(np.clip(_as_float(sp[3, 0]), -self._max_speed, self._max_speed))
        sp[2, 0] = np.float32(vx)
        sp[3, 0] = np.float32(vy)

    def _limit_jump(self, x: float, y: float) -> tuple[float, float]:
        if self._last_out is None:
            self._last_out = (x, y)
            return x, y
        lx, ly = self._last_out
        dx = max(-self._max_jump, min(self._max_jump, x - lx))
        dy = max(-self._max_jump, min(self._max_jump, y - ly))
        out = (lx + dx, ly + dy)
        self._last_out = out
        return out

    def update(
        self, cx: float, cy: float, dt: float = 0.033
    ) -> tuple[float, float]:
        self._set_dt(dt)
        if not self.initialized:
            self.kf.statePost = np.array(
                [[np.float32(cx)], [np.float32(cy)], [0], [0]], dtype=np.float32
            )
            self.kf.statePre = self.kf.statePost.copy()
            self.initialized = True
            return self._limit_jump(float(cx), float(cy))

        self.kf.predict()
        measurement = np.array(
            [[np.float32(cx)], [np.float32(cy)]], dtype=np.float32
        )
        estimate = self.kf.correct(measurement)
        self._clamp_speed()
        x = _as_float(estimate[0, 0])
        y = _as_float(estimate[1, 0])
        vx = _as_float(self.kf.statePost[2, 0])
        vy = _as_float(self.kf.statePost[3, 0])
        if self.lead_time > 0.0:
            x += vx * self.lead_time
            y += vy * self.lead_time
        return self._limit_jump(x, y)

    def predict_only(self, dt: float = 0.033) -> tuple[float, float]:
        if not self.initialized:
            return 0.0, 0.0
        # Miss'te son çıkışı tut — hayalet hızla tavana gitmesin
        if self._last_out is not None:
            return self._last_out
        self._set_dt(dt)
        pred = self.kf.predict()
        self._clamp_speed()
        return self._limit_jump(_as_float(pred[0, 0]), _as_float(pred[1, 0]))
