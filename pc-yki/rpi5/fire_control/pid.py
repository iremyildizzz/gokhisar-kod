"""PID controller — KTR Şekil 4.9 (RPi5 üzerinde çalışır)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PIDGains:
    kp: float = 0.035
    ki: float = 0.002
    kd: float = 0.008
    integral_limit: float = 200.0
    output_limit: float = 8.0  # derece / adım


class PID:
    def __init__(self, gains: PIDGains | None = None) -> None:
        self.gains = gains or PIDGains()
        self._i = 0.0
        self._prev_err = 0.0
        self._has_prev = False

    def reset(self) -> None:
        self._i = 0.0
        self._prev_err = 0.0
        self._has_prev = False

    def set_gains(self, kp: float, ki: float, kd: float) -> None:
        """YKİ'den gelen katsayıları uygula (KTR 4.3: operatör ayarlayabilir).

        İntegral terimi eski katsayıyla birikmiş olduğu için sıfırlanır; aksi
        hâlde yeni Ki ile birlikte birikmiş hata bir sıçrama üretir.
        """
        self.gains.kp = float(kp)
        self.gains.ki = float(ki)
        self.gains.kd = float(kd)
        self.reset()

    def step(self, error: float, dt: float) -> float:
        if dt <= 0.0:
            return 0.0

        g = self.gains
        self._i += error * dt
        self._i = max(-g.integral_limit, min(g.integral_limit, self._i))

        d = 0.0
        if self._has_prev:
            d = (error - self._prev_err) / dt
        self._prev_err = error
        self._has_prev = True

        out = g.kp * error + g.ki * self._i + g.kd * d
        return max(-g.output_limit, min(g.output_limit, out))
