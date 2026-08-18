"""PC (YKİ) -> Raspberry Pi 5 haberleşme katmanı.

Hedef merkez koordinatları, sınıf bilgisi ve takip kimliği JSON satır
formatında TCP ile RPi'ye aktarılır. Bağlantı kopmalarına dayanıklıdır.
"""
import json
import socket
import threading
import time

import config


class RpiLink:
    def __init__(self, host: str = config.RPI_HOST, port: int = config.RPI_PORT):
        self.host, self.port = host, port
        self.sock: socket.socket | None = None
        self.lock = threading.Lock()

    def _connect(self):
        try:
            s = socket.create_connection((self.host, self.port), timeout=2)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock = s
        except OSError:
            self.sock = None

    def _send(self, payload: dict) -> bool:
        line = (json.dumps(payload) + "\n").encode()
        with self.lock:
            if self.sock is None:
                self._connect()
            if self.sock is None:
                return False
            try:
                self.sock.sendall(line)
                return True
            except OSError:
                self.sock = None
                return False

    # ---------- mesaj tipleri ----------
    def send_target(self, cx: float, cy: float, class_id: int,
                    track_id: int, locked: bool):
        """Hedef konum/sınıf/kimlik + kilit durumu."""
        return self._send({
            "type": "target",
            "t": time.time(),
            "cx": round(cx, 1), "cy": round(cy, 1),
            "class_id": class_id, "track_id": track_id,
            "locked": locked,
        })

    def send_engage(self, track_id: int, class_id: int):
        """Angajman talebi (mesafe doğrulaması RPi'de yapılır)."""
        return self._send({"type": "engage", "track_id": track_id,
                           "class_id": class_id})

    def send_manual(self, dx: float, dy: float):
        """Manuel mod: operatörden gelen yönelim komutu."""
        return self._send({"type": "manual", "dx": dx, "dy": dy})

    def send_mode(self, autonomous: bool):
        return self._send({"type": "mode", "autonomous": autonomous})

    def close(self):
        with self.lock:
            if self.sock:
                self.sock.close()
                self.sock = None
