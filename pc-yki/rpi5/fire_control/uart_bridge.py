"""STM32 UART bridge — binary downlink + telemetry parse."""
from __future__ import annotations

import time
from typing import Callable, Optional

import serial

from .protocol import DownlinkCommand, FrameParser, UplinkTelemetry


class Stm32Bridge:
    def __init__(
        self,
        port: str = "/dev/ttyAMA0",
        baud: int = 115200,
        on_telemetry: Optional[Callable[[UplinkTelemetry], None]] = None,
    ) -> None:
        self._ser = serial.Serial(port, baud, timeout=0.01)
        self._parser = FrameParser()
        self._on_telemetry = on_telemetry
        self.last_telem: Optional[UplinkTelemetry] = None
        self._last_send = 0.0

    def close(self) -> None:
        self._ser.close()

    def poll(self) -> list[UplinkTelemetry]:
        data = self._ser.read(64)
        tels = self._parser.feed(data) if data else []
        for t in tels:
            self.last_telem = t
            if self._on_telemetry:
                self._on_telemetry(t)
        return tels

    def send(self, cmd: DownlinkCommand, min_period_s: float = 0.02) -> bool:
        """Frame yazıldıysa True. Ateş (FIRE) frame'leri rate-limit'i aşar."""
        now = time.monotonic()
        force = bool(cmd.fire or cmd.home or cmd.safe)
        if not force and now - self._last_send < min_period_s:
            return False
        self._last_send = now
        self._ser.write(cmd.to_frame())
        self._ser.flush()
        return True
