"""PC (YKİ) TCP JSON sunucusu — gokhisar + atis-kontrol mesajları."""
from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# gokhisar shared/classes.py TargetClass ile aynı
PEER_CLASS_NAMES = {
    0: "fuze",         # Balistik Füze
    1: "helikopter",   # Helikopter
    2: "iha",          # İHA
    3: "ucak",         # Savaş Uçağı
    4: "balon",        # Balon
}


@dataclass
class MissionState:
    mode: str = "manuel"  # manuel | otonom
    stage: int = 0
    err_x: float = 0.0
    err_y: float = 0.0
    class_id: int = -1  # henüz gelmedi; 0=fuze ile karışmasın
    class_name: str = ""
    iff: str = "bilinmiyor"  # dost | dusman | bilinmiyor
    track_id: int = -1
    locked: bool = False
    pan_cmd_deg: Optional[float] = None
    tilt_cmd_deg: Optional[float] = None
    # gokhisar manuel: açı adımı (tüketilince sıfırlanır)
    manual_dpan: float = 0.0
    manual_dtilt: float = 0.0
    arm: bool = False
    fire: bool = False
    engage_active: bool = False  # gokhisar "engage" talebi
    enable: bool = True
    home: bool = False
    estop: bool = False
    frame_w: int = 640
    frame_h: int = 480
    target_mono: float = 0.0
    pid_kp: Optional[float] = None
    pid_ki: Optional[float] = None
    pid_kd: Optional[float] = None
    pid_dirty: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> "MissionState":
        with self.lock:
            return MissionState(
                mode=self.mode,
                stage=self.stage,
                err_x=self.err_x,
                err_y=self.err_y,
                class_id=self.class_id,
                class_name=self.class_name,
                iff=self.iff,
                track_id=self.track_id,
                locked=self.locked,
                pan_cmd_deg=self.pan_cmd_deg,
                tilt_cmd_deg=self.tilt_cmd_deg,
                manual_dpan=self.manual_dpan,
                manual_dtilt=self.manual_dtilt,
                arm=self.arm,
                fire=self.fire,
                engage_active=self.engage_active,
                enable=self.enable,
                home=self.home,
                estop=self.estop,
                frame_w=self.frame_w,
                frame_h=self.frame_h,
                target_mono=self.target_mono,
                pid_kp=self.pid_kp,
                pid_ki=self.pid_ki,
                pid_kd=self.pid_kd,
                pid_dirty=self.pid_dirty,
            )

    def consume_manual_delta(self) -> tuple[float, float]:
        with self.lock:
            dpan, dtilt = self.manual_dpan, self.manual_dtilt
            self.manual_dpan = 0.0
            self.manual_dtilt = 0.0
            return dpan, dtilt

    def clear_engage(self) -> None:
        with self.lock:
            self.engage_active = False
            self.fire = False

    def clear_pid_dirty(self) -> None:
        with self.lock:
            self.pid_dirty = False

    def _clear_track(self) -> None:
        self.track_id = -1
        self.class_id = -1
        self.locked = False
        self.err_x = 0.0
        self.err_y = 0.0
        self.target_mono = 0.0

    def apply_message(self, msg: dict[str, Any]) -> None:
        t = msg.get("type", "")
        with self.lock:
            if t == "mode":
                if "autonomous" in msg:
                    self.mode = "otonom" if bool(msg["autonomous"]) else "manuel"
                    if bool(msg["autonomous"]) and self.stage < 2:
                        self.stage = 2
                    if not bool(msg["autonomous"]):
                        self.engage_active = False
                        self.fire = False
                        self.arm = False
                        self._clear_track()
                if "mode" in msg:
                    m = str(msg["mode"]).lower()
                    if m in ("otonom", "auto", "autonomous"):
                        self.mode = "otonom"
                    elif m in ("manuel", "manual"):
                        self.mode = "manuel"
                        self.engage_active = False
                        self.fire = False
                        self.arm = False
                        self._clear_track()
                if "stage" in msg:
                    self.stage = int(msg["stage"])

            elif t == "manual":
                self.mode = "manuel"
                if self.stage == 0:
                    self.stage = 1
                self.manual_dpan += float(msg.get("dx", 0.0))
                self.manual_dtilt += float(msg.get("dy", 0.0))

            elif t == "target":
                # KTR: hedef yalnız otonom 2+
                if self.mode == "otonom" and self.stage >= 2:
                    self.target_mono = time.monotonic()
                    if "cx" in msg and "cy" in msg:
                        cx = float(msg["cx"])
                        cy = float(msg["cy"])
                        self.err_x = cx - (self.frame_w * 0.5)
                        self.err_y = cy - (self.frame_h * 0.5)
                    if "err_x" in msg:
                        self.err_x = float(msg["err_x"])
                    if "err_y" in msg:
                        self.err_y = float(msg["err_y"])
                    if "class_id" in msg:
                        self.class_id = int(msg["class_id"])
                        if "class_name" not in msg and "class" not in msg:
                            self.class_name = PEER_CLASS_NAMES.get(
                                self.class_id, self.class_name
                            )
                    if "class_name" in msg:
                        self.class_name = str(msg["class_name"])
                    elif "class" in msg:
                        self.class_name = str(msg["class"])
                    if "iff" in msg:
                        self.iff = str(msg["iff"])
                    if "track_id" in msg:
                        self.track_id = int(msg["track_id"])
                    if "locked" in msg:
                        self.locked = bool(msg["locked"])
                    if "stage" in msg:
                        self.stage = int(msg["stage"])

            elif t == "engage":
                # gokhisar angajman talebi → arm+fire niyeti
                self.engage_active = True
                self.arm = True
                self.fire = True
                if "class_id" in msg:
                    self.class_id = int(msg["class_id"])
                    self.class_name = PEER_CLASS_NAMES.get(self.class_id, self.class_name)
                if "track_id" in msg:
                    self.track_id = int(msg["track_id"])
                if "stage" in msg:
                    self.stage = int(msg["stage"])
                # PC IFF yapmış varsayımı (engage sadece düşmana)
                if self.iff == "bilinmiyor":
                    self.iff = "dusman"

            elif t == "fire":
                self.arm = bool(msg.get("arm", self.arm))
                self.fire = bool(msg.get("fire", False))
                if self.fire:
                    self.engage_active = True

            elif t == "servo":
                if "pan_deg" in msg:
                    self.pan_cmd_deg = float(msg["pan_deg"])
                if "tilt_deg" in msg:
                    self.tilt_cmd_deg = float(msg["tilt_deg"])
                if "enable" in msg:
                    self.enable = bool(msg["enable"])

            elif t == "home":
                self.home = True
            elif t == "estop":
                self.estop = True
                self.arm = False
                self.fire = False
                self.enable = False
                self.engage_active = False
            elif t == "clear_estop":
                self.estop = False
            elif t == "stage":
                self.stage = int(msg.get("stage", self.stage))
            elif t == "pid":
                if "kp" in msg:
                    self.pid_kp = float(msg["kp"])
                if "ki" in msg:
                    self.pid_ki = float(msg["ki"])
                if "kd" in msg:
                    self.pid_kd = float(msg["kd"])
                self.pid_dirty = True
            elif t == "video":
                pass


class TcpJsonServer:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5005,
        state: Optional[MissionState] = None,
        on_client_connect: Optional[Callable[[str], None]] = None,
        on_client_disconnect: Optional[Callable[[str], None]] = None,
        on_message: Optional[Callable[[dict[str, Any], str], None]] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.state = state or MissionState()
        self.on_client_connect = on_client_connect
        self.on_client_disconnect = on_client_disconnect
        self.on_message = on_message
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(4)
        self._sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._accept_loop, name="tcp-json", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        with self._clients_lock:
            for c in self._clients:
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()
        if self._sock:
            self._sock.close()

    def broadcast_status(self, payload: dict[str, Any]) -> None:
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with self._clients_lock:
            dead: list[socket.socket] = []
            for c in self._clients:
                try:
                    c.sendall(line)
                except OSError:
                    dead.append(c)
            for c in dead:
                self._clients.remove(c)
                try:
                    c.close()
                except OSError:
                    pass

    def _accept_loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            peer = addr[0] if addr else ""
            conn.settimeout(0.5)
            with self._clients_lock:
                self._clients.append(conn)
            if self.on_client_connect and peer:
                try:
                    self.on_client_connect(peer)
                except Exception as exc:  # noqa: BLE001
                    print(f"[WARN] on_client_connect: {exc}")
            threading.Thread(
                target=self._client_loop, args=(conn, peer), daemon=True
            ).start()

    def _client_loop(self, conn: socket.socket, peer: str = "") -> None:
        buf = b""
        try:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if isinstance(msg, dict):
                        self.state.apply_message(msg)
                        if self.on_message:
                            try:
                                self.on_message(msg, peer)
                            except Exception as exc:  # noqa: BLE001
                                print(f"[WARN] on_message: {exc}")
        finally:
            with self._clients_lock:
                if conn in self._clients:
                    self._clients.remove(conn)
            try:
                conn.close()
            except OSError:
                pass
            if self.on_client_disconnect and peer:
                try:
                    self.on_client_disconnect(peer)
                except Exception as exc:  # noqa: BLE001
                    print(f"[WARN] on_client_disconnect: {exc}")
