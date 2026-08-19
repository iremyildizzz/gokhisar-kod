"""PID controller — KTR Şekil 4.9 (RPi5 üzerinde çalışır).

İki bölge (GPU / düşük lag için):
  - Uzak: tam P → anlık yaklaşma
  - Yakın: P kısılır, D frenler → geçmeden kilit bandında dur
  - Ölü bant: çıkış 0
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PIDGains:
    kp: float = 0.0
    ki: float = 0.0
    kd: float = 0.0
    integral_limit: float = 200.0
    output_limit: float = 5.0  # uzak mesafe için daha yüksek tavan
    d_limit: float = 50.0
    # Yaklaşma bandı: P yumuşar, D açık kalır
    near_err_deg: float = 2.5
    near_p_scale: float = 0.25  # yakında P'nin oranı (D tam)
    near_out_scale: float = 0.40  # yakında adım tavanı
    deadzone_deg: float = 0.28


class PID:
    def __init__(self, gains: PIDGains | None = None) -> None:
        self.gains = gains or PIDGains()
        self._i = 0.0
        self._prev_err = 0.0
        self._has_prev = False

    def set_gains(
        self,
        kp: float | None = None,
        ki: float | None = None,
        kd: float | None = None,
        output_limit: float | None = None,
        reset: bool = True,
    ) -> None:
        """Arayüzden gelen P/I/D güncellemesi."""
        if kp is not None:
            self.gains.kp = float(kp)
        if ki is not None:
            self.gains.ki = float(ki)
        if kd is not None:
            self.gains.kd = float(kd)
        if output_limit is not None:
            self.gains.output_limit = float(output_limit)
        if reset:
            self.reset()

    def reset(self) -> None:
        self._i = 0.0
        self._prev_err = 0.0
        self._has_prev = False

    def step(self, error: float, dt: float) -> float:
        if dt <= 0.0:
            return 0.0

        g = self.gains
        abs_e = abs(error)

        if abs_e <= g.deadzone_deg:
            self._i = 0.0
            self._prev_err = error
            self._has_prev = True
            return 0.0

        if g.ki != 0.0:
            self._i += error * dt
            self._i = max(-g.integral_limit, min(g.integral_limit, self._i))
        else:
            self._i = 0.0

        d = 0.0
        if self._has_prev:
            d = (error - self._prev_err) / dt
            d = max(-g.d_limit, min(g.d_limit, d))
        self._prev_err = error
        self._has_prev = True

        # Uzak: tam P. Yakın: P düşür, D fren olarak kalsın.
        if abs_e <= g.near_err_deg:
            # Merkeze yaklaştıkça P lineer azalır (near_p_scale → 1.0 dışı)
            t = abs_e / g.near_err_deg  # 0..1
            p_scale = g.near_p_scale + (1.0 - g.near_p_scale) * t
            lim = max(0.3, g.output_limit * g.near_out_scale)
        else:
            p_scale = 1.0
            lim = g.output_limit

        out = (g.kp * p_scale) * error + g.ki * self._i + g.kd * d
        return max(-lim, min(lim, out))
