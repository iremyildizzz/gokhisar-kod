"""Hedef Takip Modülü — ByteTrack + servo Kalman filtresi.

Tüm tespit yöntemlerinden (YOLO, HSV, YOLO-ROI) gelen hedefler ortak
sınır kutusu formatına dönüştürülüp birleşik listede toplanır; böylece
yöntemler arası geçişte kimlikler korunur. ByteTrack, dahili Kalman
filtresiyle sonraki konumu tahmin eder; IoU + Macar algoritması ile
eşleştirme yapar; yüksek güvenli tespitler önce, düşük güvenliler
telafi turunda eşleştirilir.

Servo kararlılığı için, takip edilen hedefin merkez koordinatına
ByteTrack'ten bağımsız ikinci bir Kalman filtresi uygulanır.
"""
from dataclasses import dataclass, field

import cv2
import numpy as np
import supervision as sv

import config
from detection.yolo_detector import Detection


@dataclass
class TrackedTarget:
    track_id: int
    det: Detection
    age: int = 0                 # kaç karedir takipte
    misses: int = 0              # ardışık kayıp kare sayısı
    center_history: list = field(default_factory=list)
    servo_corrections: list = field(default_factory=list)


class TargetTracker:
    def __init__(self, fps: int = 30):
        self.tracker = sv.ByteTrack(
            track_activation_threshold=config.TRACK_HIGH_CONF,
            minimum_matching_threshold=config.TRACK_MATCH_IOU,
            lost_track_buffer=config.TRACK_BUFFER,
            frame_rate=fps,
        )
        self.targets: dict[int, TrackedTarget] = {}

    @staticmethod
    def _to_sv(detections: list[Detection]) -> sv.Detections:
        """Birleşik tespit listesi -> supervision formatı."""
        if not detections:
            return sv.Detections.empty()
        return sv.Detections(
            xyxy=np.array([d.as_xyxy() for d in detections]),
            confidence=np.array([d.conf for d in detections]),
            class_id=np.array([d.class_id for d in detections]),
        )

    def update(self, detections: list[Detection]) -> dict[int, TrackedTarget]:
        """ByteTrack güncellemesi; kimlikleri korunmuş hedef sözlüğü döner."""
        tracked = self.tracker.update_with_detections(self._to_sv(detections))

        seen: set[int] = set()
        for xyxy, conf, cls_id, tid in zip(tracked.xyxy,
                                           tracked.confidence,
                                           tracked.class_id,
                                           tracked.tracker_id):
            tid = int(tid)
            seen.add(tid)
            det = Detection(*xyxy.tolist(), conf=float(conf.item() if hasattr(conf, 'item') else conf),
                            class_id=int(cls_id), source="track")
            if tid in self.targets:
                t = self.targets[tid]
                t.det = det
                t.age += 1
                t.misses = 0
            else:
                t = TrackedTarget(track_id=tid, det=det, age=1)
                self.targets[tid] = t
            t.center_history.append((det.cx, det.cy))
            if len(t.center_history) > 60:
                t.center_history.pop(0)

        # ölçüm alınamayan takipler: kayıp sayacı artar, buffer aşılınca düşer
        for tid in list(self.targets):
            if tid not in seen:
                self.targets[tid].misses += 1
                if self.targets[tid].misses > config.TRACK_BUFFER:
                    del self.targets[tid]

        return self.targets

    @staticmethod
    def stability(t: TrackedTarget) -> float:
        """Merkez geçmişindeki oynaklıktan 0-1 arası kararlılık metriği."""
        if len(t.center_history) < 5:
            return 0.0
        pts = np.array(t.center_history[-20:])
        jitter = float(np.mean(np.std(pts, axis=0)).item())
        return 1.0 / (1.0 + jitter / 10.0)


class ServoKalman:
    """Servoya giden hedef merkez koordinatı için sabit-hız Kalman filtresi.
    Ölçüm gürültüsünü bastırarak kararlı konum kestirimi sağlar."""

    def __init__(self):
        self.kf = cv2.KalmanFilter(4, 2)  # durum: [x, y, vx, vy], ölçüm: [x, y]
        self.kf.transitionMatrix = np.array(
            [[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]], np.float32)
        self.kf.measurementMatrix = np.array(
            [[1, 0, 0, 0], [0, 1, 0, 0]], np.float32)
        self.kf.processNoiseCov = np.eye(4, dtype=np.float32) * 1e-3
        self.kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * 5e-2
        self.initialized = False

    def update(self, cx: float, cy: float) -> tuple[float, float]:
        meas = np.array([[np.float32(cx)], [np.float32(cy)]])
        if not self.initialized:
            self.kf.statePost = np.array([[cx], [cy], [0], [0]], np.float32)
            self.initialized = True
        self.kf.predict()
        est = self.kf.correct(meas)
        return float(est[0][0]), float(est[1][0])

    def predict_only(self) -> tuple[float, float]:
        """Ölçüm yokken yalnızca tahmin adımı — takip sürekliliği."""
        pred = self.kf.predict()
        return float(pred[0][0]), float(pred[1][0])
