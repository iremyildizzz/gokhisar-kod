"""RPi kamera → PC UDP RTP/JPEG video akışı.

Arayüz (GStreamerVideoWorker) port 5000'de RTP/JPEG bekler.
Pi sürekli yayınlar; arayüz yalnızca dinler / kendi tarafında kapatır.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from typing import Optional

_BIN_CANDIDATES = (
    "/usr/bin",
    "/usr/local/bin",
    "/bin",
)


def _find_bin(*names: str) -> Optional[str]:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
        for folder in _BIN_CANDIDATES:
            path = os.path.join(folder, name)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return None


class VideoStreamer:
    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        port: int = 5000,
        enabled: bool = True,
    ) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.port = port
        self.enabled = enabled
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._host: Optional[str] = None
        # PATH dar olabilir; bilinen konumlara da bak.
        path = os.environ.get("PATH", "")
        extra = ":".join(_BIN_CANDIDATES)
        if extra not in path:
            os.environ["PATH"] = f"{extra}:{path}" if path else extra
        self._rpicam = _find_bin("rpicam-vid", "libcamera-vid")
        self._gst = _find_bin("gst-launch-1.0")

    @property
    def running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def start(self, host: str, port: Optional[int] = None) -> bool:
        """PC IP'sine UDP akışı başlat. Aynı hedefe zaten gidiyorsa no-op."""
        if not self.enabled:
            return False
        host = (host or "").strip()
        if not host or host.startswith("127."):
            print(f"[WARN] Video: geçersiz hedef host={host!r}")
            return False
        if not self._rpicam or not self._gst:
            print(
                "[WARN] Video: rpicam-vid/libcamera-vid veya gst-launch-1.0 yok.\n"
                "  Pi'de dene: which rpicam-vid gst-launch-1.0\n"
                "  Yoksa: sudo apt install -y gstreamer1.0-tools "
                "gstreamer1.0-plugins-good gstreamer1.0-plugins-base"
            )
            return False

        out_port = int(port or self.port)
        with self._lock:
            if (
                self._proc is not None
                and self._proc.poll() is None
                and self._host == host
                and out_port == self.port
            ):
                return True
            self._stop_locked()
            self.port = out_port
            cmd = (
                f"{self._rpicam} --width {self.width} --height {self.height} "
                f"--framerate {self.fps} --codec mjpeg -t 0 -o - | "
                f"{self._gst} -q fdsrc do-timestamp=true ! jpegparse ! "
                f"rtpjpegpay pt=26 ! queue ! "
                f"udpsink host={host} port={out_port} sync=false async=false "
                f"buffer-size=262144"
            )
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except OSError as exc:
                print(f"[WARN] Video başlatılamadı: {exc}")
                self._proc = None
                self._host = None
                return False
            self._host = host
            print(
                f"[OK] Video UDP → {host}:{out_port} "
                f"({self.width}x{self.height}@{self.fps}) "
                f"via {self._rpicam}"
            )
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        host = self._host
        self._host = None
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
        except OSError:
            pass
        if host:
            print(f"[OK] Video durdu ({host})")
