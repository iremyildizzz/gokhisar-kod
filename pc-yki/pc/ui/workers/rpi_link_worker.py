"""RpiLinkWorker — PC ↔ Raspberry Pi komut kanalını arka planda yürütür.

Neden mevcut `TCPCommandWorker` kullanılmadı: o worker kendi ikili (binary)
paket biçimini üretiyor, `rpi5/fire_control` ise satır tabanlı JSON okuyor
(ikili çerçeve yalnızca RPi ile STM32 arasında kullanılıyor). İki uç birbirini
hiç anlamazdı. Temel mesajların kodlayıcısı görüntü işleme reposundaki
`pc/vision/comms/rpi_link.py`; bu worker onu `RpiChannel` üzerinden kullanır.
`TCPCommandWorker` yerinde bırakıldı ama artık uygulamanın yolunda değil.

Worker'ın çözdüğü dört problem:

1. **Bağlantı dayanıklılığı.** RPi geç açılabilir ya da kablo çıkabilir.
   Döngü, koparsa periyodik olarak yeniden bağlanmayı dener; arayüzdeki LED
   gerçek duruma göre yanar.
2. **Hedef gönderim hızının sınırlanması.** Boru hattı 60 FPS üretse bile
   servo döngüsünün bundan faydası yok; sınırsız gönderim TCP tamponunu
   şişirip gecikme yaratır. Hedef mesajları `max_target_rate_hz` ile
   kısılır ve daima *en yeni* hedef gönderilir (latest-only).
3. **Mutlak açı → artım dönüşümü.** Arayüzün eksen göstergeleri mutlak açı
   verir, RPi'nin `manual` mesajı ise artım bekler. Dönüşüm burada yapılır;
   böylece RPi kodu değişmez.
4. **Ağ G/Ç'sinin UI thread'inden ayrılması.** `sendall` bloklayabilir;
   ana thread'de yapılırsa arayüz donar.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QMutex, QMutexLocker, Signal

from pc.integration import bootstrap  # noqa: F401
from pc.integration.rpi_channel import RpiChannel
from pc.integration.settings import RpiSettings
from pc.ui.workers.base_worker import BaseWorker

import config

# `rpi5/fire_control` pan home 90°, tilt home 80° (UI elevation -10).
_SERVO_PAN_HOME = float(getattr(config, "SERVO_PAN_HOME_DEG", 90.0))
_SERVO_TILT_HOME = float(getattr(config, "SERVO_TILT_HOME_DEG", 80.0))
_LOOP_POLL_S = 0.05


@dataclass(frozen=True)
class TargetCommand:
    cx: float
    cy: float
    class_id: int
    track_id: int
    locked: bool


class RpiLinkWorker(BaseWorker):
    """RPi komut kanalını süren worker.

    Sinyaller:
        connection_changed(bool)   : TCP bağlantı durumu değişti.
        telemetry_received(object) : RPi'den gelen JSON sözlüğü.
        engagement_sent(int)       : Angajman komutu gitti (track_id).
    """

    connection_changed = Signal(bool)
    telemetry_received = Signal(object)
    engagement_sent = Signal(int)

    def __init__(self, settings: RpiSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._channel = RpiChannel(settings)

        self._mutex_out = QMutex()
        self._pending_target: TargetCommand | None = None
        self._commands: deque = deque(maxlen=64)
        self._manual_target: tuple[float, float] | None = None
        self._manual_deltas: list[tuple[float, float]] = []

        self._pan = _SERVO_PAN_HOME
        self._tilt = _SERVO_TILT_HOME
        self._last_target_sent = 0.0
        self._last_connect_attempt = 0.0
        self._was_connected = False

    # ------------------------------------------------------------------
    # UI tarafından çağrılan API
    # ------------------------------------------------------------------
    def send_target(self, cx: float, cy: float, class_id: int,
                    track_id: int, locked: bool) -> None:
        """Otonom yönelim hedefi. Eski hedef beklemedeyse üzerine yazılır."""
        with QMutexLocker(self._mutex_out):
            self._pending_target = TargetCommand(cx, cy, class_id, track_id, locked)

    def set_manual_angles(self, pan: float, tilt: float) -> None:
        """Kaydırıcıların mutlak açısı. Artıma dönüşümü worker yapar."""
        with QMutexLocker(self._mutex_out):
            self._manual_target = (float(pan), float(tilt))

    def queue_manual_delta(self, dx: float, dy: float) -> None:
        """Klavye: doğrudan artım (mutlak açı hesabı yok)."""
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return
        with QMutexLocker(self._mutex_out):
            self._manual_deltas.append((float(dx), float(dy)))

    def send_mode(self, autonomous: bool, stage: int | None = None) -> None:
        """Çalışma kipi (+ yarışma aşaması) komutu."""
        with QMutexLocker(self._mutex_out):
            self._commands.append(("mode", bool(autonomous), stage))

    def send_pid(self, kp: float, ki: float, kd: float) -> None:
        """Kontrol panelindeki P/I/D katsayılarını RPi'ye ilet (KTR 4.3)."""
        with QMutexLocker(self._mutex_out):
            self._commands.append(("pid", float(kp), float(ki), float(kd)))

    def sync_angles(self, pan: float, tilt: float) -> None:
        """Mutlak→artım dönüşümünün aynasını RPi'nin bildirdiği açıya çek.

        Artım hesabı "arayüzün en son gönderdiği açı" referansına dayanıyor.
        RPi otonom modda PID ile ya da yasak açı kenetlemesiyle farklı bir
        açıda kalırsa, referans kaymış olur ve manuel moda dönüldüğünde ilk
        komut yanlış yöne bir sıçrama üretir. Telemetri gerçeği söylediği için
        aynayı ona göre düzeltiyoruz.
        """
        with QMutexLocker(self._mutex_out):
            self._pan = float(pan)
            self._tilt = float(tilt)

    def send_engage(self, track_id: int, class_id: int) -> None:
        """Angajman talebi. Mesafe/yasak bölge doğrulaması RPi'de yapılır."""
        with QMutexLocker(self._mutex_out):
            self._commands.append(("engage", int(track_id), int(class_id)))

    def clear_pending(self) -> None:
        """Bekleyen tüm komutları at (DURDUR / RESET)."""
        with QMutexLocker(self._mutex_out):
            self._pending_target = None
            self._manual_target = None
            self._manual_deltas.clear()
            self._commands.clear()

    # ------------------------------------------------------------------
    # QThread gövdesi
    # ------------------------------------------------------------------
    def run(self):
        try:
            while self.is_running:
                if not self._ensure_connected():
                    # Bağlantı yokken komut biriktirmenin anlamı yok; bayat
                    # hedefle nişan almak yanlış yöne dönmek demektir.
                    self.clear_pending()
                    time.sleep(_LOOP_POLL_S)
                    continue

                self._drain_commands()
                self._push_manual()
                self._push_target()
                self._read_telemetry()
        finally:
            self._channel.close()
            if self._was_connected:
                self._was_connected = False
                self.connection_changed.emit(False)
            self.emit_status("RPi komut kanalı kapatıldı")

    # ------------------------------------------------------------------
    def _ensure_connected(self) -> bool:
        if self._channel.connected:
            return True

        if self._was_connected:
            self._was_connected = False
            self.connection_changed.emit(False)
            self.emit_error("RPi bağlantısı koptu, yeniden deneniyor")

        now = time.monotonic()
        if now - self._last_connect_attempt < self._settings.reconnect_period_s:
            return False
        self._last_connect_attempt = now

        if not self._channel.connect():
            return False

        # RPi home referansı: pan 90° / tilt 80° (UI elev -10).
        with QMutexLocker(self._mutex_out):
            self._pan = _SERVO_PAN_HOME
            self._tilt = _SERVO_TILT_HOME
        self._was_connected = True
        self.connection_changed.emit(True)
        self.emit_status(
            f"RPi bağlandı: {self._settings.host}:{self._settings.port}"
        )
        return True

    def _drain_commands(self) -> None:
        while True:
            with QMutexLocker(self._mutex_out):
                if not self._commands:
                    return
                command = self._commands.popleft()

            kind = command[0]
            if kind == "mode":
                if not self._channel.send_mode(command[1], command[2]):
                    self.emit_error("Mod komutu gönderilemedi")
            elif kind == "pid":
                if not self._channel.send_pid(command[1], command[2], command[3]):
                    self.emit_error("PID katsayıları gönderilemedi")
            elif kind == "engage":
                track_id, class_id = command[1], command[2]
                if self._channel.send_engage(track_id, class_id):
                    self.engagement_sent.emit(track_id)
                else:
                    self.emit_error("Angajman komutu gönderilemedi")

    def _push_manual(self) -> None:
        with QMutexLocker(self._mutex_out):
            target = self._manual_target
            self._manual_target = None
            deltas = list(self._manual_deltas)
            self._manual_deltas.clear()
            ref_pan, ref_tilt = self._pan, self._tilt

        if target is not None:
            pan, tilt = target
            dx, dy = pan - ref_pan, tilt - ref_tilt
            if abs(dx) >= 0.05 or abs(dy) >= 0.05:
                if self._channel.send_manual(dx, dy):
                    ref_pan, ref_tilt = pan, tilt
                else:
                    self.emit_error("Manuel komut gönderilemedi")

        for dx, dy in deltas:
            if not self._channel.send_manual(dx, dy):
                self.emit_error("Manuel komut gönderilemedi")
            else:
                ref_pan += dx
                ref_tilt += dy

        with QMutexLocker(self._mutex_out):
            self._pan, self._tilt = ref_pan, ref_tilt

    def _push_target(self) -> None:
        with QMutexLocker(self._mutex_out):
            target = self._pending_target
            self._pending_target = None
        if target is None:
            return

        now = time.monotonic()
        min_period = 1.0 / max(1.0, self._settings.max_target_rate_hz)
        if now - self._last_target_sent < min_period:
            # Kısıtlama aşımı: hedefi geri koymuyoruz. Bir sonraki karede
            # zaten daha güncel bir hedef gelecek; bayatını göndermek
            # servoyu geriye çeker.
            return
        self._last_target_sent = now

        if not self._channel.send_target(target.cx, target.cy, target.class_id,
                                         target.track_id, target.locked):
            self.emit_error("Hedef komutu gönderilemedi")

    def _read_telemetry(self) -> None:
        """Telemetriyi oku; aynı zamanda döngünün hız ayarını yapar.

        `poll()` içindeki select zaman aşımı, mesaj yokken döngünün boşa CPU
        yakmasını engelleyen doğal bir bekleme noktasıdır.
        """
        for message in self._channel.poll(timeout=_LOOP_POLL_S):
            self.telemetry_received.emit(message)

    # ------------------------------------------------------------------
    def stop_worker(self):
        self.clear_pending()
        super().stop_worker()
