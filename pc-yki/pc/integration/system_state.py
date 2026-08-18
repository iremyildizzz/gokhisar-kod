"""Sistem seviyesi sonlu durum makinesi (KTR 4.4.2).

Görüntü işleme tarafındaki `lifecycle.state_machine` **hedef başına** durum
tutar (DETECT → VALIDATE → TRACK → EVALUATE → TARGET_LOCK → DESTROYED).
KTR 4.4.2 ise bunun üstünde, operatöre gösterilen **sistem** durumunu tarif
eder: IDLE, SCANNING, DETECT, TRACK, EVALUATE, TARGET_LOCK, ENGAGEMENT,
DESTROYED, LOST, FAIL_SAFE.

İki depoda da bu üst katman yoktu. Burada, boru hattı sonucundan ve operatör
olaylarından türetilerek üretiliyor; hiçbir hedef durumunu değiştirmiyor,
yalnızca okuyup özetliyor.
"""

from __future__ import annotations

import time
from enum import Enum


class SystemState(Enum):
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    DETECT = "DETECT"
    TRACK = "TRACK"
    EVALUATE = "EVALUATE"
    TARGET_LOCK = "TARGET_LOCK"
    ENGAGEMENT = "ENGAGEMENT"
    DESTROYED = "DESTROYED"
    LOST = "LOST"
    FAIL_SAFE = "FAIL_SAFE"


# Geçici durumların operatöre görünür kalma süresi. Bunlar olay niteliğinde
# olduğu için tek kare gösterilseler gözden kaçar.
_ENGAGEMENT_HOLD_S = 2.0
_DESTROYED_HOLD_S = 2.5
_LOST_HOLD_S = 3.0


class SystemStateMachine:
    """Boru hattı çıktısını ve operatör olaylarını sistem durumuna çevirir."""

    def __init__(self):
        self._state = SystemState.IDLE
        self._running = False
        self._fail_safe_reason: str | None = None
        self._engaged_at = 0.0
        self._destroyed_at = 0.0
        self._lost_at = 0.0
        self._had_tracks = False

    @property
    def state(self) -> SystemState:
        return self._state

    @property
    def fail_safe_reason(self) -> str | None:
        return self._fail_safe_reason

    # ---------- operatör / sistem olayları ----------
    def on_start(self) -> SystemState:
        self._running = True
        self._fail_safe_reason = None
        return self._set(SystemState.SCANNING)

    def on_stop(self) -> SystemState:
        self._running = False
        return self._set(SystemState.IDLE)

    def on_reset(self) -> SystemState:
        self.__init__()
        return self._state

    def on_engagement(self) -> SystemState:
        self._engaged_at = time.monotonic()
        return self._set(SystemState.ENGAGEMENT)

    def on_fail_safe(self, reason: str) -> SystemState:
        """Kritik hata / güvenlik ihlali: her durumdan FAIL_SAFE'e geçilir.

        Çıkış yalnızca RESET ile olur; hatanın kendiliğinden "geçmiş" görünmesi
        operatörü yanıltırdı.
        """
        self._fail_safe_reason = reason
        return self._set(SystemState.FAIL_SAFE)

    # ---------- boru hattı sonucundan türetme ----------
    def update(self, result) -> SystemState:
        """`PipelineResult`'tan sistem durumunu türet.

        Sıralama önem taşır: kilit, değerlendirmeden; değerlendirme, takipten
        önce gelir. Aksi hâlde daha zayıf bir koşul daha güçlüsünü gölgeler.
        """
        if self._state is SystemState.FAIL_SAFE or not self._running:
            return self._state

        now = time.monotonic()

        if result.destroyed_track_ids:
            self._destroyed_at = now
        if result.lost_track_ids and not result.tracks:
            self._lost_at = now

        has_tracks = bool(result.tracks)
        if has_tracks:
            self._had_tracks = True

        if now - self._engaged_at < _ENGAGEMENT_HOLD_S:
            return self._set(SystemState.ENGAGEMENT)
        if now - self._destroyed_at < _DESTROYED_HOLD_S:
            return self._set(SystemState.DESTROYED)
        if result.locked:
            return self._set(SystemState.TARGET_LOCK)
        if result.candidate is not None:
            return self._set(SystemState.EVALUATE)
        if any(track.validated for track in result.tracks):
            return self._set(SystemState.TRACK)
        if result.detections:
            return self._set(SystemState.DETECT)
        if self._had_tracks and now - self._lost_at < _LOST_HOLD_S:
            return self._set(SystemState.LOST)

        self._had_tracks = False
        return self._set(SystemState.SCANNING)

    def _set(self, state: SystemState) -> SystemState:
        self._state = state
        return state
