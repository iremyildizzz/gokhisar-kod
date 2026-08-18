"""Hedef Doğrulama ve Eşleştirme Modülü (VALIDATE durumu).

Maket–balon ilişkisini konumsal yakınlık ve hizalanma kriterleriyle
doğrular. Her maketin sınır kutusu altında piksel yüksekliğine göre
boyutlandırılan dinamik bir eşleştirme bölgesi oluşturur; hedef
küçüldükçe arama alanı kademeli uzatma katsayısıyla genişletilir.

İki eşleştirme yöntemi:
  1) Maketin altındaki bölgeye düşen balonlar doğrudan ilişkilendirilir.
  2) HSV balonunun üst ROI'sinde YOLO yeniden çalıştırılarak maket
     doğrulanır (uzak hedeflerde başarımı artırır).
"""
from dataclasses import dataclass

import numpy as np

import config
from detection.yolo_detector import Detection, YoloDetector
from detection.hsv_detector import HsvBalloonDetector


@dataclass
class OperationalTarget:
    """Başarılı eşleşme sonucu oluşan operasyonel hedef kaydı."""
    model_det: Detection      # maket
    balloon_det: Detection    # ilişkili balon


class TargetMatcher:
    def __init__(self, yolo: YoloDetector):
        self.yolo = yolo

    @staticmethod
    def _match_region(model: Detection) -> tuple[float, float, float, float]:
        """Maket altında dinamik eşleştirme bölgesi.
        Küçük (uzak) hedeflerde bölge kademeli olarak uzatılır."""
        ratio = config.MATCH_REGION_BASE_RATIO
        if model.h < config.SMALL_TARGET_PX_HEIGHT:
            shrink = (config.SMALL_TARGET_PX_HEIGHT - model.h) / config.SMALL_TARGET_PX_HEIGHT
            ratio += shrink * config.MATCH_REGION_EXTEND_STEP * 4
        depth = model.h * ratio
        return model.x1, model.y2, model.x2, model.y2 + depth

    @staticmethod
    def _inside(region, det: Detection) -> bool:
        x1, y1, x2, y2 = region
        return x1 <= det.cx <= x2 and y1 <= det.cy <= y2

    def match(self, frame: np.ndarray,
              models: list[Detection],
              balloons: list[Detection]) -> tuple[list[OperationalTarget], list[Detection]]:
        """Dönüş: (doğrulanan operasyonel hedefler, eşleşemeyenler).
        Eşleşemeyenler geçici elenir; sonraki karelerde yeniden değerlendirilir."""
        validated: list[OperationalTarget] = []
        used_balloons: set[int] = set()

        # Yöntem 1: maket altındaki bölgeye düşen balonlar
        for m in models:
            region = self._match_region(m)
            for i, b in enumerate(balloons):
                if i in used_balloons:
                    continue
                if self._inside(region, b):
                    validated.append(OperationalTarget(m, b))
                    used_balloons.add(i)
                    break

        # Yöntem 2: eşleşmeyen HSV balonlarının üst ROI'sinde YOLO doğrulaması
        for i, b in enumerate(balloons):
            if i in used_balloons or b.source != "hsv":
                continue
            roi = HsvBalloonDetector.upper_roi(b, frame.shape)
            roi_dets = self.yolo.detect_in_roi(frame, roi)
            models_in_roi = [d for d in roi_dets
                             if d.class_id in config.MODEL_CLASS_IDS]
            if models_in_roi:
                best = max(models_in_roi, key=lambda d: d.conf)
                validated.append(OperationalTarget(best, b))
                used_balloons.add(i)

        unmatched = [b for i, b in enumerate(balloons) if i not in used_balloons]
        return validated, unmatched
