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
# pyrefly: ignore [missing-import]
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
    vx: float = 0.0              # X eksenindeki hız kestirimi (piksel/kare)
    vy: float = 0.0              # Y eksenindeki hız kestirimi (piksel/kare)


class TargetTracker:
    def __init__(self, fps: int = 30):
        self.tracker = sv.ByteTrack(
            track_activation_threshold=config.TRACK_HIGH_CONF,
            minimum_matching_threshold=config.TRACK_MATCH_IOU,
            lost_track_buffer=config.TRACK_BUFFER,
            frame_rate=fps,
        )
        self.targets: dict[int, TrackedTarget] = {}

    def set_fps(self, fps: int) -> None:
        """Pipeline ölçülen FPS değerine göre ByteTrack zaman adımını senkronize et."""
        if hasattr(self.tracker, "frame_rate"):
            self.tracker.frame_rate = max(1, fps)

    @staticmethod
    def _to_sv(detections: list[Detection]) -> sv.Detections:
        """Birleşik tespit listesi -> supervision formatı.

        Gürültü tespiti önlemek için conf < TRACK_LOW_CONF (0.1) olanlar filtrelenir;
        TRACK_LOW_CONF <= conf < TRACK_HIGH_CONF (0.1..0.5) arasındaki düşük güvenli
        tespitler ByteTrack'in 2. tur telafi eşleştirmesinde kullanılır.
        """
        valid_dets = [d for d in detections if d.conf >= config.TRACK_LOW_CONF]
        if not valid_dets:
            return sv.Detections.empty()
        return sv.Detections(
            xyxy=np.array([d.as_xyxy() for d in valid_dets]),
            confidence=np.array([d.conf for d in valid_dets]),
            class_id=np.array([d.class_id for d in valid_dets]),
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
            det = Detection(*xyxy.tolist(), conf=float(conf),
                            class_id=int(cls_id), source="track")
            if tid in self.targets:
                t = self.targets[tid]
                # Hız kestirimi (Constant-Velocity / Sabit Hız modeli)
                dx = det.cx - t.det.cx
                dy = det.cy - t.det.cy
                # EMA (Exponential Moving Average) ile hız gürültüsünü filtrele
                alpha = 0.85

                t.vx = (
                    alpha * dx +
                    (1.0 - alpha) * t.vx
                ) if t.age > 1 else dx
                t.vy = (
                    alpha * dy +
                    (1.0 - alpha) * t.vy
                ) if t.age > 1 else dy
                t.det = det
                t.age += 1
                t.misses = 0
            else:
                t = TrackedTarget(track_id=tid, det=det, age=1)
                self.targets[tid] = t
            t.center_history.append((det.cx, det.cy))
            if len(t.center_history) > 60:
                t.center_history.pop(0)

        # Ölçüm alınamayan takipler: donmuş kutu yerine Constant-Velocity bbox prediction (coasting)
        for tid in list(self.targets):
            if tid not in seen:
                t = self.targets[tid]
                t.misses += 1
                if t.misses > config.TRACK_BUFFER:
                    del self.targets[tid]
                    continue

                # Miss sırasında kutuyu dondurmak yerine son kestirilen hızla ilerlet (coasting)
                decay = 0.75 ** t.misses

                vx = t.vx * decay
                vy = t.vy * decay

                new_x1 = t.det.x1 + vx
                new_y1 = t.det.y1 + vy
                new_x2 = t.det.x2 + vx
                new_y2 = t.det.y2 + vy
                coasted_conf = max(0.05, t.det.conf * 0.85)

                t.det = Detection(new_x1, new_y1, new_x2, new_y2,
                                      conf=coasted_conf, class_id=t.det.class_id,
                                      source="coast")
                t.center_history.append((t.det.cx, t.det.cy))
                if len(t.center_history) > 60:
                    t.center_history.pop(0)

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
    """
    Servo hedef merkezi için düşük gecikmeli Constant-Velocity Kalman.

    Durum:
        [x, y, vx, vy]

    x, y:
        pixel

    vx, vy:
        pixel / second
    """

    def __init__(self, lead_time: float = 0.06):
        self.kf = cv2.KalmanFilter(4, 2)

        self.lead_time = lead_time

        self.kf.measurementMatrix = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0]
        ], dtype=np.float32)

        self.kf.processNoiseCov = np.diag([
            0.05,
            0.05,
            0.8,
            0.8
        ]).astype(np.float32)

        self.kf.measurementNoiseCov = np.diag([
            0.15,
            0.15
        ]).astype(np.float32)

        self.kf.errorCovPost = np.eye(
            4,
            dtype=np.float32
        )

        self.initialized = False

    def _set_dt(self, dt: float) -> None:
        """
        Gerçek frame süresine göre transition matrix güncellenir.
        """

        dt = max(0.001, min(float(dt), 0.2))

        self.kf.transitionMatrix = np.array([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1, 0],
            [0, 0, 0, 1]
        ], dtype=np.float32)

    def reset(self) -> None:
        """
        Filtreyi tamamen sıfırlar.
        """

        self.kf.statePost = np.zeros(
            (4, 1),
            dtype=np.float32
        )

        self.kf.statePre = np.zeros(
            (4, 1),
            dtype=np.float32
        )

        self.kf.errorCovPost = np.eye(
            4,
            dtype=np.float32
        )

        self.initialized = False

    def update(
        self,
        cx: float,
        cy: float,
        dt: float = 0.033
    ) -> tuple[float, float]:
        """
        Gerçek detection bulunduğunda kullanılır.

        Önce Kalman tahmini yapılır,
        sonra gerçek ölçüm ile düzeltilir.

        Servo gecikmesini azaltmak için hız kullanarak
        kısa süreli ileri konum tahmini yapılır.
        """

        self._set_dt(dt)

        if not self.initialized:
            self.kf.statePost = np.array([
                [cx],
                [cy],
                [0],
                [0]
            ], dtype=np.float32)

            self.kf.statePre = self.kf.statePost.copy()

            self.initialized = True

            return float(cx), float(cy)

        # 1. Hareket modeliyle tahmin
        self.kf.predict()

        # 2. Gerçek detection
        measurement = np.array([
            [cx],
            [cy]
        ], dtype=np.float32)

        # 3. Ölçüm ile Kalman düzeltmesi
        estimate = self.kf.correct(measurement)

        x = float(estimate[0, 0])
        y = float(estimate[1, 0])

        vx = float(estimate[2, 0])
        vy = float(estimate[3, 0])

        # Servo / haberleşme gecikmesini kompanse etmek için
        # hedefin biraz ilerisine bak.
        servo_x = x + vx * self.lead_time
        servo_y = y + vy * self.lead_time

        return servo_x, servo_y

    def predict_only(
        self,
        dt: float = 0.033
    ) -> tuple[float, float]:
        """
        Detection bulunamadığında kullanılır.

        Ölçüm ile correct() yapılmaz.
        Sadece hareket modeli ilerletilir.
        """

        if not self.initialized:
            return 0.0, 0.0

        self._set_dt(dt)

        prediction = self.kf.predict()

        x = float(prediction[0, 0])
        y = float(prediction[1, 0])

        vx = float(prediction[2, 0])
        vy = float(prediction[3, 0])

        return (
            x + vx * self.lead_time,
            y + vy * self.lead_time
        )