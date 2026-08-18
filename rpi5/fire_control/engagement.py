"""Aşama-3 imha mesafeleri — gokhisar class_id (0–4)."""
from __future__ import annotations

from typing import Optional

# shared/classes.py / pc/config.CLASS_NAMES ile aynı
CLASS_ID_ALIASES: dict[int, str] = {
    0: "fuze",
    1: "helikopter",
    2: "iha",
    3: "ucak",
    4: "balon",
}

DISPLAY_NAMES: dict[int, str] = {
    0: "Balistik Füze",
    1: "Helikopter",
    2: "İHA",
    3: "Savaş Uçağı",
    4: "Balon",
}

# yarışma puanlı imha bandı (metre)
ENGAGE_RANGE_M: dict[str, tuple[float, float]] = {
    "fuze": (5.0, 15.0),
    "helikopter": (5.0, 15.0),
    "iha": (0.0, 15.0),
    "ucak": (10.0, 15.0),
}


def resolve_target_class(class_name: str = "", class_id: int = -1) -> str:
    if class_id >= 0 and class_id in CLASS_ID_ALIASES:
        return CLASS_ID_ALIASES[class_id]
    if class_name in ENGAGE_RANGE_M or class_name == "balon":
        return class_name
    return ""


def display_name(class_id: int = -1, class_name: str = "") -> str:
    if class_id in DISPLAY_NAMES:
        return DISPLAY_NAMES[class_id]
    return class_name or "bilinmiyor"


def engage_range_for(class_name: str = "", class_id: int = -1) -> Optional[tuple[float, float]]:
    key = resolve_target_class(class_name, class_id)
    return ENGAGE_RANGE_M.get(key)


def distance_allows_fire(
    stage: int,
    distance_m: Optional[float],
    class_name: str = "",
    class_id: int = -1,
    *,
    require_lidar_stage3: bool = True,
) -> tuple[bool, str]:
    if stage < 3:
        return True, "stage_lt_3"

    if distance_m is None:
        if require_lidar_stage3:
            return False, "no_lidar"
        return True, "lidar_optional"

    key = resolve_target_class(class_name, class_id)
    if key == "balon":
        return False, "balon_not_engageable"

    rng = engage_range_for(class_name, class_id)
    if rng is None:
        return False, "unknown_class"

    lo, hi = rng
    if lo <= distance_m <= hi:
        return True, f"in_range:{key}:{lo}-{hi}"
    return False, f"out_of_range:{key}:{distance_m:.2f} not in {lo}-{hi}"
