"""Kayıtlı PID ayarları.

en_iyi_dikey — son denenen / en iyi dikey (ayrı tilt kazancı)
iyi_yatay — pan referansı (aynı aile)
dikey_ayar1 — eski: ortak P/D + gravity FF
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
    # Dikey ayrı kazanç (None → pan ile aynı)
    kp_tilt: float | None = None
    ki_tilt: float | None = None
    kd_tilt: float | None = None
    # STM tilt komutuna droop ofseti (derece); overshoot için 0 tercih
    tilt_gravity_kg: float = 0.0
    tilt_gravity_mode: str = "cos"


# Ad: en_iyi_dikey — son yapılan dikey ayar (pan iyi + tilt yumuşak/frenli)
EN_IYI_DIKEY = PidPreset(
    name="en_iyi_dikey",
    kp=0.034,
    ki=0.0,
    kd=0.010,
    kp_tilt=0.018,
    ki_tilt=0.0,
    kd_tilt=0.022,
    tilt_gravity_kg=0.0,
    tilt_gravity_mode="cos",
)

# Ad: iyi_yatay — pan referansı (en_iyi_dikey ile aynı kazanç ailesi)
IYI_YATAY = PidPreset(
    name="iyi_yatay",
    kp=0.034,
    ki=0.0,
    kd=0.010,
    kp_tilt=0.018,
    ki_tilt=0.0,
    kd_tilt=0.022,
    tilt_gravity_kg=0.0,
    tilt_gravity_mode="cos",
)

# Ad: dikey_ayar1 — az önceki (ortak kazanç + gravity FF)
DIKEY_AYAR1 = PidPreset(
    name="dikey_ayar1",
    kp=0.034,
    ki=0.0,
    kd=0.010,
    kp_tilt=0.034,
    ki_tilt=0.0,
    kd_tilt=0.010,
    tilt_gravity_kg=0.8,
    tilt_gravity_mode="cos",
)

PRESETS: dict[str, PidPreset] = {
    EN_IYI_DIKEY.name: EN_IYI_DIKEY,
    IYI_YATAY.name: IYI_YATAY,
    DIKEY_AYAR1.name: DIKEY_AYAR1,
}


def resolve_tilt_gains(
    kp: float,
    ki: float,
    kd: float,
    kp_tilt: float | None,
    ki_tilt: float | None,
    kd_tilt: float | None,
) -> tuple[float, float, float]:
    """Pan kazancından tilt kazancını çöz."""
    return (
        float(kp if kp_tilt is None else kp_tilt),
        float(ki if ki_tilt is None else ki_tilt),
        float(kd if kd_tilt is None else kd_tilt),
    )


def tilt_gravity_ff(tilt_deg: float, kg: float, mode: str = "cos") -> float:
    """STM komutuna yerçekimi ofseti (state'e birikmez)."""
    if kg == 0.0:
        return tilt_deg
    mode_l = (mode or "cos").lower()
    if mode_l == "const":
        return tilt_deg + kg
    elev_rad = math.radians(tilt_deg - 90.0)
    return tilt_deg + kg * math.cos(elev_rad)
