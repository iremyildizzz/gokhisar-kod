"""Dost-Düşman Ayrım Modülü (IFF).

Aşama-2: aktif balona sahip tüm maketler doğrudan DÜŞMAN.
Aşama-3: renk tabanlı ayrım — kırmızı = DÜŞMAN, camgöbeği = DOST.
Hedef bölgesi HSV'ye dönüştürülür, Hue medyanı referans aralıklarla
karşılaştırılır. Tek kare yeterli sayılmaz; sınıflandırma geçmişi
tutulur ve ardışık karelerde zamansal oylama uygulanır.
"""
from collections import defaultdict, deque
from enum import Enum

import cv2
import numpy as np

import config
from detection.yolo_detector import Detection


class IFFLabel(Enum):
    UNKNOWN = "UNKNOWN"
    FRIEND = "DOST"
    FOE = "DUSMAN"


class FriendFoeClassifier:
    def __init__(self, stage: int = 3):
        self.stage = stage
        # track_id -> son N karedeki etiket geçmişi
        self.history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=config.IFF_HISTORY_LEN))

    # ---------- tek kare renk sınıflandırması ----------
    @staticmethod
    def _classify_frame(frame: np.ndarray, det: Detection) -> IFFLabel:
        x1, y1 = max(0, int(det.x1)), max(0, int(det.y1))
        x2, y2 = int(det.x2), int(det.y2)
        region = frame[y1:y2, x1:x2]
        if region.size == 0:
            return IFFLabel.UNKNOWN

        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        # doygunluğu düşük (gri/siyah arka plan) pikselleri dışla
        sat_mask = hsv[:, :, 1] > 80
        hues = hsv[:, :, 0][sat_mask]
        if hues.size < 10:
            return IFFLabel.UNKNOWN

        hue_median = float(np.median(hues))
        for lo, hi in config.HUE_RED_RANGES:
            if lo <= hue_median <= hi:
                return IFFLabel.FOE
        lo, hi = config.HUE_CYAN_RANGE
        if lo <= hue_median <= hi:
            return IFFLabel.FRIEND
        return IFFLabel.UNKNOWN

    # ---------- zamansal oylama ----------
    def classify(self, frame: np.ndarray, det: Detection,
                 track_id: int) -> IFFLabel:
        if self.stage == 2:
            # Aşama-2: balonlu tüm maketler düşman kabul edilir
            return IFFLabel.FOE

        label = self._classify_frame(frame, det)
        hist = self.history[track_id]
        hist.append(label)

        foe_votes = sum(1 for l in hist if l is IFFLabel.FOE)
        friend_votes = sum(1 for l in hist if l is IFFLabel.FRIEND)

        # Düşman doğrulaması: kırmızının birden fazla karede tutarlı gözlemi
        if foe_votes >= config.IFF_VOTE_MIN_FRAMES and foe_votes > friend_votes:
            return IFFLabel.FOE
        if friend_votes >= config.IFF_VOTE_MIN_FRAMES and friend_votes > foe_votes:
            return IFFLabel.FRIEND
        return IFFLabel.UNKNOWN

    def drop(self, track_id: int):
        self.history.pop(track_id, None)
