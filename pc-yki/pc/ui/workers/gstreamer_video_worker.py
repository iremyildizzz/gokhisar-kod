"""
GStreamer Video Worker - RTP/JPEG akışını gst-launch-1.0 subprocess'i ile alır.

Neden subprocess yaklaşımı?
- pip ile kurulu OpenCV (4.8.0) GStreamer desteği OLMADAN derlenmiş, yani
  ``cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)`` çalışmaz.
- Sistemdeki ``python3-gi`` paketi farklı bir Python sürümü için derlendiği
  için venv'in içinden ``import gi`` yapılamıyor (ABI uyumsuzluğu).
- ``gst-launch-1.0`` zaten sistemde kurulu ve kullanıcı bunu test etmiş.
- Subprocess yaklaşımı; GStreamer'ın RTP depay/JPEG birleştirme işlerini
  kendi adımıza yapmasına izin verirken, Python tarafında ek bağımlılık
  yaratmaz. UI thread'i bloklamaz çünkü worker QThread içinde çalışır.

İletişim Sözleşmesi (UDPVideoWorker ile aynı):
- ``frame_received: Signal(bytes)``  -> Tam bir JPEG karesi
- ``connection_status: Signal(bool)`` -> Pipeline çalışıyor mu?

Bu sayede ``VideoDisplay.update_frame_from_bytes`` slot'unu değiştirmeden
bu yeni worker'a doğrudan bağlanabiliriz (Liskov Substitution Principle).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

from PySide6.QtCore import Signal

from pc.ui.workers.base_worker import BaseWorker
from pc.ui.utils.config import NetworkConfig


# JPEG dosya formatı sınır işaretleri (markers)
# Her JPEG karesi 0xFFD8 ile başlar (Start Of Image - SOI),
# 0xFFD9 ile biter (End Of Image - EOI).
# rtpjpegdepay'in çıktısındaki her buffer tam bir JPEG karesi olduğu için
# bu marker'lar arasında kalan baytları toplayarak frame sınırlarını bulabiliriz.
JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


class GStreamerVideoWorker(BaseWorker):
    """
    UDP üzerinden RTP/JPEG video akışını alan GStreamer tabanlı worker.

    Sinyaller (UDPVideoWorker ile birebir aynı arayüz):
    - frame_received(bytes)    : Decode edilmeye hazır JPEG karesi
    - connection_status(bool)  : Pipeline aktif/pasif

    BaseWorker'dan miras alınan:
    - status_changed(str)
    - error_occurred(str)
    """

    frame_received = Signal(bytes)
    connection_status = Signal(bool)

    # ---------- Tunables ----------
    # stdout'tan bir seferde okunacak maksimum bayt. Çok küçük olursa
    # syscall maliyeti artar; çok büyükse latency artar. 64KB iyi bir orta yol.
    _READ_CHUNK = 64 * 1024

    # JPEG SOI bulunmadan biriken çöpün üst limiti. Bunu aşarsa bellek
    # şişmesini önlemek için tamponu sıfırlarız. Tipik bir 720p JPEG karesi
    # 50-200 KB civarındadır; 4 MB üst limit fazlasıyla güvenlidir.
    _MAX_BUFFER = 4 * 1024 * 1024

    def __init__(
        self,
        port: int | None = None,
        rtp_caps: str | None = None,
        gst_bin: str | None = None,
        parent=None,
    ):
        super().__init__(parent)

        self._port: int = port or NetworkConfig.UDP_VIDEO_PORT
        self._caps: str = rtp_caps or NetworkConfig.GST_RTP_CAPS
        self._gst_bin: str = gst_bin or NetworkConfig.GST_BIN

        self._proc: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------
    # Pipeline kurulum
    # ------------------------------------------------------------------
    def _build_pipeline_args(self) -> list[str]:
        """
        gst-launch-1.0 komut satırı argümanlarını üret.

        Pipeline:
            udpsrc port=N caps="..." ! rtpjpegdepay ! fdsink fd=1

        - ``-q`` : Quiet mod. State change mesajları stdout'a yazılmaz, böylece
          stdout'tan saf JPEG bayt akışı okuruz. Hatalar yine stderr'e gider.
        - ``fdsink fd=1`` : Çıktıyı stdout'a (file descriptor 1) yönlendirir.
          subprocess.Popen ile stdout=PIPE diyince Python tarafında okuruz.
        """
        return [
            self._gst_bin,
            "-q",
            "udpsrc",
            f"port={self._port}",
            f"caps={self._caps}",
            "!",
            "rtpjpegdepay",
            "!",
            "fdsink",
            "fd=1",
            "sync=false",
        ]

    def _ensure_gst_available(self) -> bool:
        """gst-launch-1.0 PATH'te veya mutlak yolda var mı."""
        bin_path = self._gst_bin
        ok = os.path.isfile(bin_path) if os.path.isabs(bin_path) else shutil.which(bin_path) is not None
        if not ok:
            self.emit_error(
                f"'{bin_path}' bulunamadı. "
                "GStreamer kurulu mu? Windows: "
                r"C:\Program Files\gstreamer\1.0\msvc_x86_64\bin PATH'e eklenmeli."
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Ana döngü
    # ------------------------------------------------------------------
    def run(self):
        """
        QThread.run override.

        1) gst-launch-1.0 subprocess'ini başlat
        2) stdout'tan byte chunk'ları oku
        3) JPEG SOI/EOI marker'larıyla tam kareleri ayıkla
        4) Her kare için frame_received sinyalini yay
        """
        if not self._ensure_gst_available():
            self.connection_status.emit(False)
            return

        try:
            args = self._build_pipeline_args()
            self.emit_status("GStreamer pipeline başlatılıyor: " + " ".join(args))

            # start_new_session=True -> gst-launch'i kendi process group'una alır.
            # Böylece terminate ederken sadece bu süreci hedefleriz, ana
            # uygulamayı etkilemeyiz. bufsize=0 -> pipe okumalarında
            # ekstra Python-tarafı tamponlama yok (latency düşer).
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                start_new_session=True,
            )

            self.connection_status.emit(True)
            self.emit_status(f"UDP/RTP-JPEG dinleniyor (port {self._port})")

            self._read_loop()

        except FileNotFoundError as e:
            self.emit_error(f"GStreamer çalıştırılamadı: {e}")
            self.connection_status.emit(False)
        except Exception as e:
            self.emit_error(f"Pipeline hatası: {e}")
            self.connection_status.emit(False)
        finally:
            self._cleanup()

    def _read_loop(self):
        """
        stdout'tan akan baytları toplayıp JPEG kareleri çıkar.

        Algoritma:
        - ``buffer`` adında bir bayt birikimi tut.
        - Her okumada chunk'ı buffer'a ekle.
        - Buffer içinde [SOI...EOI] çiftini ara; bulduğun her tam kareyi
          frame_received ile yay ve buffer'dan at.
        - Birden fazla kare aynı chunk'ta gelebilir; while ile hepsini boşalt.
        - SOI bulunmadan birikim büyürse ``_MAX_BUFFER`` üstünde sıfırla
          (RTP paket kaybı / desync senaryolarında bellek şişmesini önler).
        """
        buffer = bytearray()
        proc = self._proc
        assert proc is not None and proc.stdout is not None

        while self.is_running:
            chunk = proc.stdout.read(self._READ_CHUNK)
            if not chunk:
                # EOF -> subprocess sonlandı (terminate edilmiş veya çökmüş)
                if self.is_running:
                    self.emit_status("GStreamer pipeline EOF")
                break

            buffer.extend(chunk)

            # Bu chunk'la birlikte buffer'da birden fazla tam kare olabilir.
            while True:
                soi = buffer.find(JPEG_SOI)
                if soi == -1:
                    # Hiç SOI yok; muhtemelen geçici çöp. Buffer'ı boşalt.
                    if len(buffer) > self._MAX_BUFFER:
                        buffer.clear()
                    break

                # SOI'den önceki çöpü at (varsa)
                if soi > 0:
                    del buffer[:soi]

                # SOI'den sonra EOI ara
                eoi = buffer.find(JPEG_EOI, len(JPEG_SOI))
                if eoi == -1:
                    # Kare henüz tamamlanmamış; sonraki okumayı bekle
                    if len(buffer) > self._MAX_BUFFER:
                        # Bozuk akış; baştan başla
                        buffer.clear()
                    break

                frame_end = eoi + len(JPEG_EOI)
                frame = bytes(buffer[:frame_end])
                del buffer[:frame_end]

                # Sinyal -> VideoDisplay.update_frame_from_bytes
                self.frame_received.emit(frame)

    # ------------------------------------------------------------------
    # Yaşam döngüsü
    # ------------------------------------------------------------------
    def stop_worker(self):
        """
        Worker'ı durdur. BaseWorker.stop_worker flag'i kapatır ama bizim
        stdout.read() çağrımız BLOKLU; subprocess'i terminate ederek
        stdout'a EOF yollamak okuma döngüsünü kırar.
        """
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                # Process group'a SIGTERM yolla (start_new_session=True ile
                # ayrı session açmıştık). Bu, gst-launch ve alt elementlerini
                # birlikte düşürür.
                os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM
            except (ProcessLookupError, PermissionError):
                # Düşmüş veya izin yok -> terminate'a düş
                try:
                    proc.terminate()
                except Exception:
                    pass
        super().stop_worker()

    def _cleanup(self):
        """Subprocess'i ve sinyalleri temizle."""
        proc = self._proc
        if proc:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                # stderr'i okuyup loga yansıt (debug için faydalı)
                if proc.stderr is not None:
                    try:
                        err = proc.stderr.read().decode("utf-8", errors="replace")
                        if err.strip():
                            self.emit_status(f"GStreamer stderr: {err.strip()}")
                    except Exception:
                        pass
            except Exception as e:
                self.emit_error(f"Subprocess kapatma hatası: {e}")
            finally:
                self._proc = None

        self.connection_status.emit(False)
        self.emit_status("GStreamer pipeline kapatıldı")
