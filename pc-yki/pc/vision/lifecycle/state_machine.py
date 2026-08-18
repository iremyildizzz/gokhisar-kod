"""Hedef Yaşam Döngüsü + Kilitlenme + İmha Değerlendirme.

Her hedef için DETECT -> VALIDATE -> TRACK -> EVALUATE -> TARGET_LOCK ->
DESTROYED durumları tanımlıdır; geçişler belirlenen koşullarla yapılır.

İmha doğrulaması üç koşulun EŞ ZAMANLI sağlanmasını gerektirir:
  1) belirli süre yeniden tespit edilememe,
  2) takip zincirinin sonlanması,
  3) güven skorlarının eşik altında kalması.
Koşullar tam sağlanmazsa hedef yeniden takip/doğrulamaya alınır
(geçici algılama kayıplarından kaynaklı yanlış imha kararlarını önler).
"""
import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto

import config
from iff.friend_foe import IFFLabel
from tracking.tracker import TrackedTarget


class TargetState(Enum):
    DETECT = auto()
    VALIDATE = auto()
    TRACK = auto()
    EVALUATE = auto()
    TARGET_LOCK = auto()
    DESTROYED = auto()


@dataclass
class TargetRecord:
    track_id: int
    state: TargetState = TargetState.DETECT
    iff: IFFLabel = IFFLabel.UNKNOWN
    lock_stable_frames: int = 0
    fired: bool = False
    fire_time: float = 0.0
    miss_count: int = 0
    last_conf: float = 1.0


class TargetLifecycleManager:
    def __init__(self):
        self.records: dict[int, TargetRecord] = {}

    def get(self, track_id: int) -> TargetRecord:
        if track_id not in self.records:
            self.records[track_id] = TargetRecord(track_id)
        return self.records[track_id]

    # ---------- durum geçişleri ----------
    def on_validated(self, track_id: int):
        rec = self.get(track_id)
        if rec.state in (TargetState.DETECT, TargetState.VALIDATE):
            rec.state = TargetState.TRACK

    def on_iff(self, track_id: int, label: IFFLabel):
        rec = self.get(track_id)
        rec.iff = label
        if rec.state is TargetState.TRACK and label is IFFLabel.FOE:
            rec.state = TargetState.EVALUATE

    def on_selected_for_lock(self, track_id: int):
        rec = self.get(track_id)
        if rec.state is TargetState.EVALUATE:
            rec.state = TargetState.TARGET_LOCK
            rec.lock_stable_frames = 0

    def update_lock(self, rec: TargetRecord, target: TrackedTarget) -> bool:
        """Kilit: balon merkezi merkez bandında kalsın (kısa jitter sayacı silmesin)."""
        ox = float(getattr(config, "AIM_OFFSET_X_PX", 0.0))
        oy = float(getattr(config, "AIM_OFFSET_Y_PX", 0.0))
        ax = target.det.cx + ox
        ay = target.det.cy + oy
        fx, fy = config.FRAME_CENTER
        err = math.hypot(ax - fx, ay - fy)

        tol = float(config.LOCK_TOLERANCE_PX)
        if err <= tol:
            rec.lock_stable_frames += 1
        else:
            # Tek kare dışarı = sıfırlama (eski); jitter için 1 düşür
            rec.lock_stable_frames = max(0, rec.lock_stable_frames - 1)
            if err > tol * 4:
                rec.state = TargetState.TRACK
                rec.lock_stable_frames = 0
                return False
        return rec.lock_stable_frames >= config.LOCK_STABLE_FRAMES

    def on_fired(self, track_id: int):
        rec = self.get(track_id)
        rec.fired = True
        rec.fire_time = time.time()

    # ---------- imha değerlendirme ----------
    def evaluate_destroyed(self, rec: TargetRecord,
                           target: TrackedTarget | None) -> bool:
        """Üç bağımsız koşulun EŞ ZAMANLI kontrolü:
        1. (a) Yeniden tespit edilememe süresi (miss_count >= DESTROY_MISS_FRAMES)
        2. (b) Takip zincirinin sonlanması / aşırı kayıp (target is None veya miss_count >= DESTROY_MISS_FRAMES)
        3. (c) Güven skorunun imha eşiğinin altına düşmesi (last_conf < DESTROY_CONF_THRESHOLD)
        """
        if not rec.fired:
            return False

        if target is not None:
            rec.miss_count = target.misses
            rec.last_conf = target.det.conf
        else:
            rec.miss_count += 1
            rec.last_conf = 0.0

        cond_miss = rec.miss_count >= config.DESTROY_MISS_FRAMES
        cond_track_ended = (target is None) or (rec.miss_count >= config.DESTROY_MISS_FRAMES)
        cond_low_conf = rec.last_conf < config.DESTROY_CONF_THRESHOLD

        if cond_miss and cond_track_ended and cond_low_conf:
            rec.state = TargetState.DESTROYED
            return True

        # Koşullar tam sağlanmadı: hedef yeniden yüksek güvenle tespit edildiyse takibe dön
        if target is not None and target.misses == 0 and target.det.conf >= config.DESTROY_CONF_THRESHOLD:
            rec.state = TargetState.TRACK
            rec.fired = False
        return False

    def drop(self, track_id: int):
        self.records.pop(track_id, None)
