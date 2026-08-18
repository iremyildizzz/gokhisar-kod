"""TF02-PRO LiDAR UART okuyucu (Benewake frame)."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

import serial


@dataclass
class LidarReading:
    distance_m: float
    strength: int


class TF02Pro:
    """
    Frame: 0x59 0x59 Dist_L Dist_H Strength_L Strength_H Temp_L Temp_H Checksum
    """

    def __init__(self, port: str = "/dev/ttyAMA1", baud: int = 115200) -> None:
        self._ser = serial.Serial(port, baud, timeout=0.02)
        self._buf = bytearray()
        self.last: Optional[LidarReading] = None

    def close(self) -> None:
        self._ser.close()

    def poll(self) -> Optional[LidarReading]:
        chunk = self._ser.read(64)
        if chunk:
            self._buf.extend(chunk)

        while len(self._buf) >= 9:
            if self._buf[0] != 0x59 or self._buf[1] != 0x59:
                del self._buf[0]
                continue
            frame = bytes(self._buf[:9])
            if (sum(frame[:8]) & 0xFF) != frame[8]:
                del self._buf[0]
                continue
            dist_cm, strength = struct.unpack_from("<HH", frame, 2)
            del self._buf[:9]
            self.last = LidarReading(distance_m=dist_cm / 100.0, strength=strength)
            return self.last

        return None
