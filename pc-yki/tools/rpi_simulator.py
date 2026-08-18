#!/usr/bin/env python3
"""Donanımsız uçtan uca test için Raspberry Pi taklidi.

`rpi5/fire_control` paketinin **ağ davranışını** taklit eder: 5005 portunu
dinler, satır tabanlı JSON okur, `mode` / `manual` / `target` / `engage` /
`pid` mesajlarını aynı mantıkla işler ve 200 ms'de bir gerçeğiyle aynı alan
adlarına sahip `status` telemetrisi yazar (`lidar_m`, `pan_deg`, `tilt_deg`,
iç içe `stm` bayrakları).

Neden alan adları önemli: simülatör eskiden `distance_cm` gibi yalnızca
kendisinde var olan alanlar gönderiyordu. Arayüz onunla çalışıyor, gerçek
donanımla sessizce çalışmıyordu — yani simülatör "çalışıyor" hissi veren bir
yanlıştı. Simülasyonun tek işi gerçeği temsil etmek.

Tek bilinçli fark: `in_forbidden_zone`. Sözleşmede (`shared/engagement.py`)
tanımlı, KTR Bölüm 6 tarafından isteniyor ama `rpi5/fire_control` henüz
uygulamıyor. Arayüzdeki "KRİTİK BÖLGE" uyarı yolunun test edilebilir kalması
için simülatör bunu hesaplayıp ek alan olarak gönderir.

`pc/vision/` ve `rpi5/` altındaki kod değiştirilmedi; bu dosya yalnızca bir
test aracıdır ve `tools/` altında durur.

Çalıştırma:
    python tools/rpi_simulator.py
    python tools/rpi_simulator.py --distance 450 --verbose

Sonra ayrı bir terminalde:
    GOKHISAR_RPI_HOST=127.0.0.1 python main.py
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Güvenlik kuralları ve protokol sözleşmeden geliyor; RPi ile aynı değerleri
# taşıdığını `tests/test_contract.py` doğruluyor.
from shared import engagement, geometry, protocol
from shared.engagement import in_forbidden_zone

#: `rpi5/fire_control/main.py` açılış katsayıları.
_DEFAULT_GAINS = (0.55, 0.05, 0.08)
_TELEMETRY_PERIOD_S = 0.2


class SimulatedPanTilt:
    """PID'siz, basitleştirilmiş yönelim.

    Simülatörün amacı PID'yi doğrulamak değil, protokolü doğrulamak. Gerçek
    PID'yi taklit etmeye çalışmak yanıltıcı bir "çalışıyor" hissi verirdi;
    katsayılar yalnızca kaydedilir ve telemetride geri bildirilir.
    """

    def __init__(self, frame_w: int = geometry.FRAME_WIDTH,
                 frame_h: int = geometry.FRAME_HEIGHT):
        self.cx, self.cy = frame_w / 2, frame_h / 2
        self.pan = engagement.SERVO_CENTER_ANGLE
        self.tilt = engagement.SERVO_CENTER_ANGLE
        self.kp, self.ki, self.kd = _DEFAULT_GAINS

    def set_gains(self, kp: float, ki: float, kd: float) -> None:
        self.kp, self.ki, self.kd = float(kp), float(ki), float(kd)

    def step(self, target_x: float, target_y: float) -> tuple[float, float]:
        clamp = engagement.clamp_angle
        self.pan = clamp(self.pan + (target_x - self.cx) * 0.01)
        self.tilt = clamp(self.tilt + (target_y - self.cy) * 0.01)
        return self.pan, self.tilt

    def manual(self, dx: float, dy: float) -> tuple[float, float]:
        clamp = engagement.clamp_angle
        self.pan = clamp(self.pan + dx)
        self.tilt = clamp(self.tilt + dy)
        return self.pan, self.tilt


class SimulatedRpi:
    def __init__(self, conn: socket.socket, distance_cm: float, verbose: bool):
        self.conn = conn
        self.ctrl = SimulatedPanTilt()
        self.distance_cm = distance_cm
        self.verbose = verbose
        self.autonomous = False
        self.stage = 0
        self.locked = False
        self.class_id: int | None = None
        self.track_id: int | None = None
        self.engage_active = False
        self.in_range_since: float | None = None
        # STM32'nin bir sonraki telemetride bildireceği "ateşlendi" bayrağı.
        self._fired_pending = False
        self._armed = False
        self._send_lock = threading.Lock()
        self._stop = threading.Event()

    # ---------- telemetri ----------
    def start_telemetry(self) -> threading.Thread:
        thread = threading.Thread(target=self._telemetry_loop, daemon=True)
        thread.start()
        return thread

    def _telemetry_loop(self) -> None:
        while not self._stop.is_set():
            self.send(self._status_payload())
            self._stop.wait(_TELEMETRY_PERIOD_S)

    def _status_payload(self) -> dict:
        """`rpi5/fire_control/main.py`'nin yazdığı `status` satırının eşi."""
        fired = self._fired_pending
        self._fired_pending = False

        range_ok, range_reason = True, "stage_lt_3"
        if self.stage >= 3:
            range_ok = engagement.is_safe_distance(
                self.class_id if self.class_id is not None else -1,
                self.distance_cm,
            )
            range_reason = "in_range" if range_ok else "out_of_range"

        return {
            "type": protocol.MessageType.STATUS,
            "mode": "otonom" if self.autonomous else "manuel",
            "stage": self.stage,
            "class_id": -1 if self.class_id is None else self.class_id,
            "pan_deg": round(self.ctrl.pan, 2),
            "tilt_deg": round(self.ctrl.tilt, 2),
            "locked": self.locked,
            "engage_active": self.engage_active,
            "lidar_m": round(self.distance_cm / 100.0, 3),
            "range_ok": range_ok,
            "range_reason": range_reason,
            "pid": {"kp": self.ctrl.kp, "ki": self.ctrl.ki, "kd": self.ctrl.kd},
            # Sözleşmede var, gerçek RPi5'te henüz yok — bkz. modül başlığı.
            "in_forbidden_zone": in_forbidden_zone(self.ctrl.pan, self.ctrl.tilt),
            "stm": {
                "failsafe": False,
                "armed": self._armed,
                "fired": fired,
                "busy": False,
                "enabled": True,
            },
            "track_id": -1 if self.track_id is None else self.track_id,
        }

    def send(self, payload: dict) -> None:
        line = protocol.encode_line(payload)
        with self._send_lock:
            try:
                self.conn.sendall(line)
            except OSError:
                self._stop.set()

    def stop(self) -> None:
        self._stop.set()

    # ---------- mesaj işleme (rpi5/fire_control/tcp_server.py mantığı) ----------
    def handle(self, msg: dict) -> None:
        mtype = msg.get("type")

        if mtype == protocol.MessageType.MODE:
            self.autonomous = bool(msg["autonomous"])
            if "stage" in msg:
                self.stage = int(msg["stage"])
            elif self.autonomous and self.stage < 2:
                self.stage = 2
            self.engage_active = False
            self._armed = False
            self._log(f"mod: {'OTONOM' if self.autonomous else 'MANUEL'} "
                      f"aşama={self.stage}")

        elif mtype == protocol.MessageType.PID:
            self.ctrl.set_gains(msg["kp"], msg["ki"], msg["kd"])
            self._log(f"PID kp={self.ctrl.kp} ki={self.ctrl.ki} kd={self.ctrl.kd}")

        elif mtype == protocol.MessageType.MANUAL:
            # dx/dy artımdır (protocol.MANUAL_IS_DELTA), mutlak açı değil.
            if self.stage == 0:
                self.stage = 1
            pan, tilt = self.ctrl.manual(msg["dx"], msg["dy"])
            self._command_angles(pan, tilt)

        elif mtype == protocol.MessageType.TARGET and self.autonomous:
            self.class_id = msg.get("class_id", self.class_id)
            self.track_id = msg.get("track_id", self.track_id)
            self.locked = bool(msg.get("locked", self.locked))
            pan, tilt = self.ctrl.step(msg["cx"], msg["cy"])
            self._command_angles(pan, tilt)
            self._check_engagement()

        elif mtype == protocol.MessageType.ENGAGE:
            self.engage_active = True
            self._armed = True
            self.class_id = msg.get("class_id", self.class_id)
            self.track_id = msg.get("track_id", self.track_id)
            self.in_range_since = None
            self._log(f"angajman talebi: {msg}")
            # Aşama-1 (manuel görev): menzil kapısı yok, ateş hemen çıkar.
            if self.stage <= 1:
                self._fire()
            else:
                self._check_engagement()

    def _command_angles(self, pan: float, tilt: float) -> None:
        if in_forbidden_zone(pan, tilt):
            self._log(f"YASAK BÖLGE pan={pan:.1f} tilt={tilt:.1f} — iletilmedi")
            return
        self._log(f"servo pan={pan:.1f} tilt={tilt:.1f}")

    def _check_engagement(self) -> None:
        if not self.engage_active:
            return
        if self.stage < 3:
            self._fire()
            return
        if engagement.is_safe_distance(
            self.class_id if self.class_id is not None else -1, self.distance_cm
        ):
            if self.in_range_since is None:
                self.in_range_since = time.monotonic()
            elif (time.monotonic() - self.in_range_since
                    >= engagement.ENGAGE_STABLE_SECONDS):
                self._fire()
        else:
            self.in_range_since = None

    def _fire(self) -> None:
        self._log(f"ATEŞ: class={self.class_id} dist={self.distance_cm:.0f}cm")
        self._fired_pending = True
        self.engage_active = False
        self._armed = False
        self.in_range_since = None

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[SIM] {message}", flush=True)


def serve(host: str, port: int, distance_cm: float, verbose: bool) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(1)
    print(f"[SIM] PC bağlantısı bekleniyor {host}:{port}", flush=True)

    while True:
        conn, addr = server.accept()
        print(f"[SIM] PC bağlandı: {addr}", flush=True)
        rpi = SimulatedRpi(conn, distance_cm, verbose)
        rpi.start_telemetry()
        buffer = b""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    message = protocol.decode_line(line)
                    if message is not None:
                        rpi.handle(message)
        except OSError:
            pass
        finally:
            rpi.stop()
            conn.close()
            print("[SIM] PC bağlantısı koptu, yeniden bekleniyor", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="GÖKHİSAR RPi simülatörü")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=protocol.COMMAND_PORT)
    parser.add_argument("--distance", type=float, default=500.0,
                        help="Sabit LiDAR mesafesi (cm)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    try:
        serve(args.host, args.port, args.distance, args.verbose)
    except KeyboardInterrupt:
        print("\n[SIM] kapatıldı", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
