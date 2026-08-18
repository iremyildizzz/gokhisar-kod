"""
IMX296 Global Shutter + 16mm telephoto optik modeli.

Sensor: Sony IMX296, 1456 x 1088, pixel 3.45 µm
Focal: Raspberry Pi 16mm telephoto (C/CS)

Piksel ofset → açı (derece) mesafe bağımsızdır (pinhole).
Mesafe sadece LiDAR ateş kapısında (Aşama-3) kullanılır.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CameraOptics:
    width_px: int = 1456
    height_px: int = 1088
    pixel_um: float = 3.45
    focal_mm: float = 16.0

    @property
    def sensor_w_mm(self) -> float:
        return self.width_px * self.pixel_um / 1000.0

    @property
    def sensor_h_mm(self) -> float:
        return self.height_px * self.pixel_um / 1000.0

    @property
    def hfov_deg(self) -> float:
        return math.degrees(2.0 * math.atan(self.sensor_w_mm / (2.0 * self.focal_mm)))

    @property
    def vfov_deg(self) -> float:
        return math.degrees(2.0 * math.atan(self.sensor_h_mm / (2.0 * self.focal_mm)))

    def pixel_offset_to_deg(
        self,
        err_x_px: float,
        err_y_px: float,
        frame_w: int | None = None,
        frame_h: int | None = None,
    ) -> tuple[float, float]:
        """
        Görüntü merkezi ofseti (px) → pan/tilt açı hatası (deg).
        PC işleme çözünürlüğü (ör. 1280x720) native sensörden farklıysa
        frame_w/h verilir; tam FOV varsayımıyla sensöre oranlanır.
        """
        w = float(frame_w or self.width_px)
        h = float(frame_h or self.height_px)
        x_mm = (err_x_px / w) * self.sensor_w_mm
        y_mm = (err_y_px / h) * self.sensor_h_mm
        pan_deg = math.degrees(math.atan2(x_mm, self.focal_mm))
        tilt_deg = math.degrees(math.atan2(y_mm, self.focal_mm))
        return pan_deg, tilt_deg


# Varsayılan: RPi GS Camera + 16mm
GS_16MM = CameraOptics()
