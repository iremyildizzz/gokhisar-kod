"""PC ↔ Raspberry Pi 5 komut/telemetri kanalı.

Temel mesajların (target/engage/manual/mode) gönderimi görüntü işleme
reposundaki `pc/vision/comms/rpi_link.py` (`RpiLink`) ile yapılır; o dosya
değiştirilmediği için burada yeniden yazılmaz, sarmalanır.

`RpiLink` üç şeyi sunmaz, bu modül onları ekler:

1. **Bağlantı durumu.** `RpiLink` bağlantıyı ilk gönderimde tembel kurar;
   arayüzdeki "TCP Kontrol" LED'i için gönderim beklemeden bağlanmayı
   deneyebilmek gerekiyor.
2. **Okuma yönü.** KTR 4.3'e göre LiDAR mesafesi ve ateşleme durumu RPi'den
   PC'ye telemetri olarak dönmelidir; `RpiLink` yalnızca yazar. Karşı taraf
   (`rpi5/fire_control`) 200 ms'de bir `status` satırı yazıyor.
3. **Arayüzün ihtiyaç duyduğu ek komutlar.** KTR 4.3 PID katsayılarının
   operatör tarafından ayarlanabilmesini, Aşama-3 kapılarının da açılmasını
   istiyor; ikisi de `RpiLink`'te olmayan alanlar gerektiriyor. Bu mesajlar
   `shared.protocol` kurucularıyla üretilir, böylece şema tek yerde kalır.
"""

from __future__ import annotations

import json
import select
import socket
import threading

from pc.integration import bootstrap  # noqa: F401  (sys.path kurulumu)
from pc.integration.settings import RpiSettings
from shared import protocol

from comms.rpi_link import RpiLink

_RECV_CHUNK = 4096
# Telemetri tamponunun üst sınırı. Karşı taraf satır sonu koymayan bozuk bir
# akış üretirse bellek şişmesin.
_MAX_BUFFER = 64 * 1024


class RpiChannel:
    """`RpiLink` üzerine bağlantı yönetimi ve telemetri okuması ekler."""

    def __init__(self, settings: RpiSettings):
        self.settings = settings
        self.link = RpiLink(host=settings.host, port=settings.port)
        self._buffer = bytearray()
        self._recv_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Bağlantı
    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self.link.sock is not None

    def connect(self) -> bool:
        """Bağlantıyı kurmayı dene; zaten kuruluysa dokunma.

        `RpiLink` herkese açık bir `connect()` sunmuyor. Bağlantı durumunu
        gönderim beklemeden gösterebilmek için içerideki `_connect()` çağrılıyor
        — sarmalayıcının `RpiLink` iç detayına dokunduğu tek yer burasıdır,
        bilerek tek noktada tutuldu.
        """
        if self.connected:
            return True
        with self.link.lock:
            self.link._connect()
        if self.connected:
            with self._recv_lock:
                self._buffer.clear()
        return self.connected

    def close(self) -> None:
        self.link.close()
        with self._recv_lock:
            self._buffer.clear()

    # ------------------------------------------------------------------
    # Gönderim (KTR 4.3: TCP/IP + JSON)
    # ------------------------------------------------------------------
    def send_target(self, cx: float, cy: float, class_id: int,
                    track_id: int, locked: bool) -> bool:
        """Hedef merkez koordinatı, sınıfı ve kilit durumu → PID yönelimi."""
        return self.link.send_target(cx, cy, class_id, track_id, locked)

    def send_engage(self, track_id: int, class_id: int) -> bool:
        """Angajman talebi. Mesafe doğrulaması RPi tarafında yapılır."""
        return self.link.send_engage(track_id, class_id)

    def send_manual(self, dx: float, dy: float) -> bool:
        """Manuel yönelim: mutlak açı değil, **artım** gönderilir."""
        return self.link.send_manual(dx, dy)

    def send_mode(self, autonomous: bool, stage: int | None = None) -> bool:
        """Çalışma kipi ve yarışma aşaması.

        Aşama gönderilmezse `RpiLink`'in kendi kodlayıcısı kullanılır. Aşama
        varsa mesajı sözleşme kurucusu üretir: RPi tarafı `stage` alanını
        görmezse Aşama-3 LiDAR menzil kapısını hiç açmıyor, yani arayüzdeki
        "3. AŞAMA" seçiminin donanımda karşılığı olmuyor.
        """
        if stage is None:
            return self.link.send_mode(autonomous)
        return self._send(protocol.mode(autonomous, stage))

    def send_pid(self, kp: float, ki: float, kd: float) -> bool:
        """Operatörün ayarladığı PID katsayılarını RPi denetleyicisine uygula."""
        return self._send(protocol.pid(kp, ki, kd))

    def _send(self, payload: dict) -> bool:
        """`RpiLink`'in gönderim yolunu ödünç al.

        Sokete burada doğrudan yazmıyoruz: `RpiLink._send` kilidi, tembel
        yeniden bağlanmayı ve hata durumunda soketi düşürmeyi hâlihazırda
        yönetiyor. İkinci bir gönderim yolu açmak, iki farklı yeniden bağlanma
        davranışı demek olurdu. `_connect()` ile birlikte `RpiLink` iç
        detayına dokunduğumuz iki nokta bunlar; bilerek tek dosyada tutuldu.
        """
        return bool(self.link._send(payload))

    # ------------------------------------------------------------------
    # Telemetri okuma
    # ------------------------------------------------------------------
    def poll(self, timeout: float = 0.2) -> list[dict]:
        """Bekleyen telemetri satırlarını oku ve ayrıştır.

        Soket tam-çift yönlüdür; okuma, `RpiLink`'in gönderim kilidini
        beklemeden yapılabilir. Bağlantı düşerse soket kapatılır ve bir sonraki
        `connect()` çağrısında yeniden kurulur.
        """
        sock = self.link.sock
        if sock is None:
            return []

        try:
            readable, _, _ = select.select([sock], [], [], timeout)
        except (OSError, ValueError):
            self._drop_socket()
            return []
        if not readable:
            return []

        try:
            chunk = sock.recv(_RECV_CHUNK)
        except (socket.timeout, BlockingIOError):
            return []
        except OSError:
            self._drop_socket()
            return []

        if not chunk:
            self._drop_socket()
            return []

        messages: list[dict] = []
        with self._recv_lock:
            self._buffer.extend(chunk)
            if len(self._buffer) > _MAX_BUFFER:
                self._buffer.clear()
                return []
            while b"\n" in self._buffer:
                raw, _, rest = bytes(self._buffer).partition(b"\n")
                self._buffer = bytearray(rest)
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    messages.append(payload)
        return messages

    def _drop_socket(self) -> None:
        with self.link.lock:
            if self.link.sock is not None:
                try:
                    self.link.sock.close()
                except OSError:
                    pass
                self.link.sock = None
        with self._recv_lock:
            self._buffer.clear()
