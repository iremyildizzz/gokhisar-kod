"""Hedef Değerlendirme ve Önceliklendirme Modülü (EVALUATE).

Öncelik puanı 5 temel ölçütün ağırlıklı birleşimidir:
1. Boyut (W_SIZE = 0.35): Büyük görünen hedef daha yakın/öncelikli.
2. Kamera Merkezine Uzaklık (W_CENTER = 0.25): Görüş merkezine yakın hedef daha öncelikli.
3. Takip Kararlılığı (W_STABILITY = 0.20): Jitter az olan hedef kararlı sayılır.
4. Angajman Uygunluğu (W_ENGAGEMENT = 0.10): IFF/Durum uygunluğu (DÜŞMAN = 1.0, UNKNOWN = 0.5, DOST = 0.0).
5. Servo Yönelim Kararlılığı (W_SERVO = 0.10): Az servo düzeltmesi gerektiren hedef kararlı sayılır.
"""
import math
import numpy as np

import config
from iff.friend_foe import IFFLabel
from tracking.tracker import TrackedTarget, TargetTracker


class TargetPrioritizer:
    def __init__(self):
        self.frame_area = config.FRAME_WIDTH * config.FRAME_HEIGHT

    def _servo_stability(self, t: TrackedTarget) -> float:
        """Servonun hedefi merkezde tutmak için yaptığı düzeltme
        miktarından kararlılık metriği (az düzeltme = yüksek puan)."""
        if not t.servo_corrections:
            return 0.5
        mean_corr = float(np.mean(np.abs(t.servo_corrections[-20:])))
        return 1.0 / (1.0 + mean_corr / 15.0)

    def score(self, t: TrackedTarget, iff_label: IFFLabel = IFFLabel.FOE) -> float:
        """5 ölçütün ağırlıklı toplamıyla öncelik puanını hesaplar (0..1)."""
        # 1. Boyut skoru (0..1)
        size_score = min(1.0, t.det.area / (self.frame_area * 0.05))

        # 2. Kamera merkezine uzaklık skoru (0..1, merkez=1.0)
        fx, fy = config.FRAME_CENTER
        dist = math.hypot(t.det.cx - fx, t.det.cy - fy)
        max_dist = math.hypot(fx, fy)
        center_score = max(0.0, 1.0 - (dist / max_dist))

        # 3. Takip kararlılığı skoru (0..1)
        track_score = TargetTracker.stability(t)

        # 4. Angajman uygunluğu skoru (0..1)
        if iff_label is IFFLabel.FOE:
            engagement_score = 1.0
        elif iff_label is IFFLabel.UNKNOWN:
            engagement_score = 0.5
        else:
            engagement_score = 0.0

        # 5. Servo yönelim kararlılığı skoru (0..1)
        servo_score = self._servo_stability(t)

        return (config.W_SIZE * size_score
                + config.W_CENTER * center_score
                + config.W_STABILITY * track_score
                + config.W_ENGAGEMENT * engagement_score
                + config.W_SERVO * servo_score)

    def select(self, targets: list[TrackedTarget] | list[tuple[TrackedTarget, IFFLabel]],
               current_candidate_id: int | None = None) -> TrackedTarget | None:
        """Düşman doğrulanmış hedefler arasından 5 ölçütlü puanla angajman adayını seç.
        
        current_candidate_id verilirse, mevcut adaya Hysteresis (bağlılık) primi verilir;
        böylece yakın puanlı hedefler arasında sürekli aday sıçraması (chatter) önlenir.
        """
        if not targets:
            return None

        hysteresis = float(getattr(config, "CANDIDATE_HYSTERESIS", 0.15))

        candidates = []
        for item in targets:
            if isinstance(item, tuple):
                target, iff_label = item
            else:
                target, iff_label = item, IFFLabel.FOE
            
            raw_score = self.score(target, iff_label)
            # Mevcut aday ise bağlılık primi (hysteresis) ekle
            bonus = hysteresis if (current_candidate_id is not None and target.track_id == current_candidate_id) else 0.0
            candidates.append((target, raw_score + bonus))

        candidates.sort(key=lambda c: c[1], reverse=True)
        return candidates[0][0]

