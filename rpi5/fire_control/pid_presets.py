"""Kayıtlı PID / yerçekimi önbesleme ayarları.

iyi_yatay — yatay kilit için iyi bulunan kazançlar (2026-08):
  P=0.034  I=0  D=0.010
Dikey salınım / yerçekimi droop için ayrıca tilt gravity FF kullanılır.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PidPreset:
    name: str
    kp: float
    ki: float
    kd: float
    # STM'ye giden tilt komutuna eklenen yerçekimi ofseti (derece)
    tilt_gravity_kg: float = 0.0
    # "cos": Kg*cos(elev), elev=tilt-90°; "const": sabit Kg
    tilt_gravity_mode: str = "cos"


# Ad: iyi_yatay
IYI_YATAY = PidPreset(
    name="iyi_yatay",
    kp=0.034,
    ki=0.0,
    kd=0.010,
    tilt_gravity_kg=0.8,
    tilt_gravity_mode="cos",
)

PRESETS: dict[str, PidPreset] = {
    IYI_YATAY.name: IYI_YATAY,
}


def tilt_gravity_ff(tilt_deg: float, kg: float, mode: str = "cos") -> float:
    """PID durum açısına yerçekimi önbeslemesi ekle (STM komutu).

    Hobby servo açı komutunda Δ'ya her döngü Kg eklemek ramp yapar.
    Bu yüzden FF, tutulan `tilt` state'ine biriktirilmez; yalnızca
    downlink anında `tilt + u_ff` olarak uygulanır (statik droop telafisi).

    elev = tilt - 90° (UI elevation). Yatayda cos≈1 → tam Kg;
    aşağı/yukarı uçlarda azalır.
    """
    if kg == 0.0:
        return tilt_deg
    mode_l = (mode or "cos").lower()
    if mode_l == "const":
        return tilt_deg + kg
    elev_rad = math.radians(tilt_deg - 90.0)
    return tilt_deg + kg * math.cos(elev_rad)
