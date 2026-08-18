"""Hedef Tespit Modülü — YOLOv8s tabanlı tespit.

DETECT durumunda kare içindeki maket ve balon hedefleri tespit eder;
her hedef için (sınıf, sınır kutusu, güven skoru) üretir. Çıktılar ön
tespit verisidir; doğrulama ve takip modüllerine giriş sağlar.
"""
from dataclasses import dataclass, field

import numpy as np
from ultralytics import YOLO

import config


@dataclass
class Detection:
    """Tüm tespit yöntemleri için ortak sınır kutusu formatı."""
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float
    class_id: int
    source: str = "yolo"          # "yolo" | "hsv" | "yolo_roi"

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def w(self) -> float:
        return self.x2 - self.x1

    @property
    def h(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def as_xyxy(self) -> np.ndarray:
        return np.array([self.x1, self.y1, self.x2, self.y2], dtype=np.float32)


class YoloDetector:
    def __init__(self, model_path: str = config.YOLO_MODEL_PATH):
        self.model = YOLO(model_path)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Tam kare üzerinde YOLOv8s çıkarımı."""
        results = self.model.predict(
            frame,
            imgsz=config.YOLO_IMG_SIZE,
            conf=config.YOLO_CONF_THRESHOLD,
            iou=config.YOLO_IOU_THRESHOLD,
            verbose=False,
        )[0]

        detections: list[Detection] = []
        for box in results.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            detections.append(
                Detection(x1, y1, x2, y2,
                          conf=float(box.conf[0]),
                          class_id=int(box.cls[0]),
                          source="yolo")
            )
        return detections

    def detect_in_roi(self, frame: np.ndarray,
                      roi: tuple[int, int, int, int]) -> list[Detection]:
        """Dinamik ROI'yi YOLO giriş çözünürlüğüne ölçekleyip yeniden
        değerlendirir; koordinatları tam kare uzayına geri dönüştürür.
        Uzak/küçük hedeflerin etkin çözünürlüğünü artırır."""
        rx1, ry1, rx2, ry2 = roi
        crop = frame[ry1:ry2, rx1:rx2]
        if crop.size == 0:
            return []

        results = self.model.predict(
            crop,
            imgsz=config.YOLO_IMG_SIZE,
            conf=config.YOLO_CONF_THRESHOLD,
            iou=config.YOLO_IOU_THRESHOLD,
            verbose=False,
        )[0]

        detections: list[Detection] = []
        for box in results.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            detections.append(
                Detection(x1 + rx1, y1 + ry1, x2 + rx1, y2 + ry1,
                          conf=float(box.conf[0]),
                          class_id=int(box.cls[0]),
                          source="yolo_roi")
            )
        return detections
