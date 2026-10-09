"""Détecteur à états : lissage EWMA + vote k-sur-n + hystérésis."""
from collections import deque
from dataclasses import dataclass

from .mathlib import ewma


@dataclass
class DetectorState:
    raw: float
    smoothed: float
    active: bool
    opened: bool
    closed: bool


class HysteresisDetector:
    """Ouvre une anomalie si k des n derniers scores lissés dépassent `high`.
    La ferme seulement quand les n derniers scores lissés sont tous sous `low` (< high).
    """

    def __init__(self, high: float, low: float, k: int = 3, n: int = 4, lam: float = 0.4):
        if not 1 <= k <= n:
            raise ValueError("il faut 1 <= k <= n")
        self.k, self.n, self.lam = k, n, lam
        self.set_thresholds(high, low)
        self.reset()

    def set_thresholds(self, high: float, low: float):
        if not low < high:
            raise ValueError("le seuil bas doit être strictement inférieur au seuil haut")
        self.high, self.low = float(high), float(low)

    def reset(self):
        self.smoothed = None
        self.active = False
        self._above = deque(maxlen=self.n)
        self._below = deque(maxlen=self.n)

    def update(self, raw: float) -> DetectorState:
        self.smoothed = ewma(self.smoothed, raw, self.lam)
        self._above.append(self.smoothed > self.high)
        self._below.append(self.smoothed < self.low)
        opened = closed = False
        if not self.active and sum(self._above) >= self.k:
            self.active = opened = True
        elif self.active and len(self._below) == self.n and all(self._below):
            self.active = False
            closed = True
        return DetectorState(float(raw), self.smoothed, self.active, opened, closed)
