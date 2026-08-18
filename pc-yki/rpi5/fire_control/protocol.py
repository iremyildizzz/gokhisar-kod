"""RPi5 <-> STM32 7-byte binary frame helpers."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

SYNC_DOWN = 0xAA
SYNC_UP = 0x55
FRAME_LEN = 7

FLAG_FIRE = 0x01
FLAG_ARM = 0x02
FLAG_HEARTBEAT = 0x04
FLAG_HOME = 0x08
FLAG_SAFE = 0x40
FLAG_ENABLE = 0x80


def stage_flags(stage: int) -> int:
    stage = max(0, min(3, int(stage)))
    return (stage & 0x03) << 4


def checksum(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x & 0xFF


@dataclass
class DownlinkCommand:
    pan_deg: float = 0.0
    tilt_deg: float = 0.0
    fire: bool = False
    arm: bool = False
    heartbeat: bool = True
    home: bool = False
    safe: bool = False
    enable: bool = True
    stage: int = 0

    def to_frame(self) -> bytes:
        pan_cdeg = int(round(self.pan_deg * 10.0))
        tilt_cdeg = int(round(self.tilt_deg * 10.0))
        pan_cdeg = max(-32768, min(32767, pan_cdeg))
        tilt_cdeg = max(-32768, min(32767, tilt_cdeg))

        flags = stage_flags(self.stage)
        if self.fire:
            flags |= FLAG_FIRE
        if self.arm:
            flags |= FLAG_ARM
        if self.heartbeat:
            flags |= FLAG_HEARTBEAT
        if self.home:
            flags |= FLAG_HOME
        if self.safe:
            flags |= FLAG_SAFE
        if self.enable:
            flags |= FLAG_ENABLE

        body = struct.pack("<BhhB", SYNC_DOWN, pan_cdeg, tilt_cdeg, flags)
        return body + bytes([checksum(body)])


@dataclass
class UplinkTelemetry:
    status: int
    pan_deg: float
    tilt_deg: float
    fired: bool = False
    armed: bool = False
    failsafe: bool = False
    enabled: bool = False
    busy: bool = False
    angle_limit: bool = False

    @classmethod
    def from_frame(cls, frame: bytes) -> Optional["UplinkTelemetry"]:
        if len(frame) != FRAME_LEN or frame[0] != SYNC_UP:
            return None
        if checksum(frame[:6]) != frame[6]:
            return None
        status, pan_cdeg, tilt_cdeg = struct.unpack_from("<Bhh", frame, 1)
        return cls(
            status=status,
            pan_deg=pan_cdeg / 10.0,
            tilt_deg=tilt_cdeg / 10.0,
            fired=bool(status & 0x01),
            armed=bool(status & 0x02),
            failsafe=bool(status & 0x04),
            enabled=bool(status & 0x08),
            busy=bool(status & 0x10),
            angle_limit=bool(status & 0x20),
        )


class FrameParser:
    """Byte-stream sync for STM32 uplink frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[UplinkTelemetry]:
        out: list[UplinkTelemetry] = []
        for b in data:
            if not self._buf:
                if b != SYNC_UP:
                    continue
                self._buf.append(b)
                continue
            self._buf.append(b)
            if len(self._buf) < FRAME_LEN:
                continue
            tel = UplinkTelemetry.from_frame(bytes(self._buf))
            self._buf.clear()
            if tel is not None:
                out.append(tel)
        return out
