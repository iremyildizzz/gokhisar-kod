"""Atış kontrol ana döngü — Raspberry Pi 5 (gokhisar JSON uyumlu)."""
from __future__ import annotations

import argparse
import signal
import time
from dataclasses import dataclass

from .engagement import distance_allows_fire, engage_range_for
from .lidar_tf02 import TF02Pro
from .optics import GS_16MM, CameraOptics
from .pid import PID, PIDGains
from .protocol import DownlinkCommand
from .tcp_server import MissionState, TcpJsonServer
from .uart_bridge import Stm32Bridge


@dataclass
class Limits:
    # gokhisar servo uzayı: 0…180°, orta = 90°
    pan_min: float = 0.0
    pan_max: float = 180.0
    tilt_min: float = 0.0
    tilt_max: float = 180.0
    home_deg: float = 90.0
    engage_err_deg: float = 0.35
    engage_stable_s: float = 1.0  # menzilde kararlı kalma


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def iff_allows_fire(stage: int, iff: str, engage_active: bool) -> bool:
    if iff.lower() in ("dost", "friend", "friendly"):
        return False
    # gokhisar engage → PC IFF geçmiş sayılır
    if engage_active and iff.lower() in ("dusman", "düşman", "enemy", "bilinmiyor"):
        return True
    if stage >= 3 and iff.lower() not in ("dusman", "düşman", "enemy"):
        return False
    return True


def run(args: argparse.Namespace) -> None:
    limits = Limits(engage_stable_s=args.engage_stable)
    optics: CameraOptics = GS_16MM
    state = MissionState(frame_w=args.frame_w, frame_h=args.frame_h)
    tcp = TcpJsonServer(host=args.tcp_host, port=args.tcp_port, state=state)
    bridge = Stm32Bridge(port=args.stm_port, baud=args.baud)

    lidar = None
    if args.lidar_port:
        try:
            lidar = TF02Pro(port=args.lidar_port, baud=args.lidar_baud)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] LiDAR açılamadı: {exc}")

    pid_x = PID(PIDGains(kp=args.kp, ki=args.ki, kd=args.kd, output_limit=args.out_limit))
    pid_y = PID(PIDGains(kp=args.kp, ki=args.ki, kd=args.kd, output_limit=args.out_limit))

    pan = limits.home_deg
    tilt = limits.home_deg
    in_range_since: float | None = None
    stop = False

    def _stop(*_a: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    tcp.start()
    print(f"[OK] TCP JSON : {args.tcp_host}:{args.tcp_port} (gokhisar uyumlu)")
    print(f"[OK] Frame merkezi: {args.frame_w}x{args.frame_h}")
    print(f"[OK] STM32 UART: {args.stm_port} @ {args.baud}")
    print(
        f"[OK] Optik GS+16mm: HFOV≈{optics.hfov_deg:.1f}° VFOV≈{optics.vfov_deg:.1f}°"
    )

    last_t = time.monotonic()
    last_status = 0.0

    try:
        while not stop:
            now = time.monotonic()
            dt = max(1e-3, now - last_t)
            last_t = now

            bridge.poll()
            if lidar:
                lidar.poll()

            gains = state.consume_pid_gains()
            if gains is not None:
                pid_x.set_gains(*gains)
                pid_y.set_gains(*gains)
                print(f"[PID] kp={gains[0]} ki={gains[1]} kd={gains[2]}")

            snap = state.snapshot()
            stage = int(snap.stage)

            home = False
            with state.lock:
                if state.home:
                    home = True
                    state.home = False

            if snap.estop:
                bridge.send(
                    DownlinkCommand(
                        pan_deg=pan,
                        tilt_deg=tilt,
                        enable=False,
                        arm=False,
                        fire=False,
                        safe=True,
                        heartbeat=True,
                        stage=stage,
                    )
                )
                pid_x.reset()
                pid_y.reset()
                in_range_since = None
                time.sleep(0.02)
                continue

            if home:
                pan = limits.home_deg
                tilt = limits.home_deg
                pid_x.reset()
                pid_y.reset()

            # --- Manuel adım (gokhisar dx/dy) ---
            dpan, dtilt = state.consume_manual_delta()
            if dpan or dtilt:
                pan += dpan
                tilt += dtilt
                pid_x.reset()
                pid_y.reset()
                stage = max(stage, 1)

            err_pan_deg = 0.0
            err_tilt_deg = 0.0

            # Aşama-1 / manuel absolute servo
            if stage <= 1 and snap.mode == "manuel":
                if snap.pan_cmd_deg is not None:
                    pan = snap.pan_cmd_deg
                if snap.tilt_cmd_deg is not None:
                    tilt = snap.tilt_cmd_deg
            elif snap.mode == "otonom" or stage >= 2:
                err_pan_deg, err_tilt_deg = optics.pixel_offset_to_deg(
                    snap.err_x,
                    snap.err_y,
                    frame_w=snap.frame_w,
                    frame_h=snap.frame_h,
                )
                if snap.locked or snap.engage_active:
                    sx = -1.0 if args.invert_x else 1.0
                    sy = -1.0 if args.invert_y else 1.0
                    pan += sx * pid_x.step(err_pan_deg, dt)
                    tilt += sy * pid_y.step(err_tilt_deg, dt)
                else:
                    pid_x.reset()
                    pid_y.reset()

            pan = clamp(pan, limits.pan_min, limits.pan_max)
            tilt = clamp(tilt, limits.tilt_min, limits.tilt_max)

            dist = None
            if lidar and lidar.last is not None:
                dist = lidar.last.distance_m

            if stage >= 3:
                lidar_ok, range_reason = distance_allows_fire(
                    stage,
                    dist,
                    class_name=snap.class_name,
                    class_id=snap.class_id,
                    require_lidar_stage3=True,
                )
            else:
                lidar_ok, range_reason = True, "lidar_not_required"

            # gokhisar: menzilde N sn kararlı kal
            range_stable = True
            if stage >= 3 and snap.engage_active:
                if lidar_ok:
                    if in_range_since is None:
                        in_range_since = now
                    range_stable = (now - in_range_since) >= limits.engage_stable_s
                    if not range_stable:
                        range_reason = f"range_warming:{(now - in_range_since):.2f}/{limits.engage_stable_s}"
                else:
                    in_range_since = None
                    range_stable = False
            else:
                in_range_since = None

            allow_iff = iff_allows_fire(stage, snap.iff, snap.engage_active)
            centered = abs(err_pan_deg) <= limits.engage_err_deg and abs(err_tilt_deg) <= limits.engage_err_deg

            fire_intent = bool(snap.fire or snap.engage_active)

            if stage <= 1 and snap.mode == "manuel":
                want_fire = bool(fire_intent and snap.arm and snap.enable and allow_iff)
            elif stage == 2:
                want_fire = bool(
                    fire_intent
                    and snap.arm
                    and snap.enable
                    and (snap.locked or snap.engage_active)
                    and centered
                    and allow_iff
                )
            else:
                want_fire = bool(
                    fire_intent
                    and snap.arm
                    and snap.enable
                    and (snap.locked or snap.engage_active)
                    and centered
                    and allow_iff
                    and lidar_ok
                    and range_stable
                )

            sent = bridge.send(
                DownlinkCommand(
                    pan_deg=pan,
                    tilt_deg=tilt,
                    fire=want_fire,
                    arm=snap.arm and allow_iff,
                    heartbeat=True,
                    home=home,
                    safe=False,
                    enable=snap.enable,
                    stage=stage,
                )
            )

            # Engage'i ancak FIRE frame gerçekten UART'a yazıldıysa kapat
            if want_fire and sent:
                state.clear_engage()
                in_range_since = None

            if now - last_status >= 0.2:
                last_status = now
                tel = bridge.last_telem
                rng = engage_range_for(snap.class_name, snap.class_id) if stage >= 3 else None
                status = {
                    "type": "status",
                    "mode": snap.mode,
                    "stage": stage,
                    "class_id": snap.class_id,
                    "class_name": snap.class_name,
                    "pan_deg": round(pan, 2),
                    "tilt_deg": round(tilt, 2),
                    "err_px": {"x": round(snap.err_x, 1), "y": round(snap.err_y, 1)},
                    "err_deg": {"pan": round(err_pan_deg, 3), "tilt": round(err_tilt_deg, 3)},
                    "frame": [snap.frame_w, snap.frame_h],
                    "locked": snap.locked,
                    "engage_active": snap.engage_active,
                    "iff": snap.iff,
                    "lidar_m": None if dist is None else round(dist, 3),
                    "engage_range_m": None if rng is None else {"min": rng[0], "max": rng[1]},
                    "range_ok": lidar_ok,
                    "range_stable": range_stable,
                    "range_reason": range_reason,
                    "want_fire": want_fire,
                    # YKİ'nin gösterdiği katsayıların gerçekten uygulandığını
                    # operatör telemetriden doğrulayabilsin.
                    "pid": {
                        "kp": pid_x.gains.kp,
                        "ki": pid_x.gains.ki,
                        "kd": pid_x.gains.kd,
                    },
                    "stm": None
                    if tel is None
                    else {
                        "failsafe": tel.failsafe,
                        "armed": tel.armed,
                        "fired": tel.fired,
                        "busy": tel.busy,
                        "enabled": tel.enabled,
                    },
                }
                tcp.broadcast_status(status)

            time.sleep(0.01)
    finally:
        try:
            bridge.send(
                DownlinkCommand(enable=False, arm=False, fire=False, safe=True, heartbeat=True),
                min_period_s=0.0,
            )
        except Exception:  # noqa: BLE001
            pass
        tcp.stop()
        bridge.close()
        if lidar:
            lidar.close()
        print("[OK] kapatıldı")


def main() -> None:
    p = argparse.ArgumentParser(description="Hava savunma atış kontrol — RPi5")
    p.add_argument("--tcp-host", default="0.0.0.0")
    p.add_argument("--tcp-port", type=int, default=5005, help="gokhisar RPI_PORT")
    p.add_argument("--frame-w", type=int, default=1280, help="PC FRAME_WIDTH (cx merkezi)")
    p.add_argument("--frame-h", type=int, default=720, help="PC FRAME_HEIGHT (cy merkezi)")
    p.add_argument("--stm-port", default="/dev/ttyAMA0", help="STM32 UART")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--lidar-port", default="/dev/ttyAMA1", help="TF02-PRO; boş = kapalı")
    p.add_argument("--lidar-baud", type=int, default=115200)
    p.add_argument("--engage-stable", type=float, default=1.0, help="Aşama-3 menzil kararlılık sn")
    p.add_argument("--kp", type=float, default=0.55)
    p.add_argument("--ki", type=float, default=0.05)
    p.add_argument("--kd", type=float, default=0.08)
    p.add_argument("--out-limit", type=float, default=4.0)
    p.add_argument("--invert-x", action="store_true")
    p.add_argument("--invert-y", action="store_true")
    args = p.parse_args()
    if args.lidar_port.strip() == "":
        args.lidar_port = None
    run(args)


if __name__ == "__main__":
    main()
