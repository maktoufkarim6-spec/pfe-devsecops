"""Générateur de données synthétiques calibré sur les mesures réelles du VPS."""
import numpy as np

from .features import NAMES, Target


def normal(rng, n):
    X = np.column_stack([
        8 + rng.normal(0, 1.0, n),                # cpu_host (%)
        62 + rng.normal(0, 0.8, n),               # ram_host (%)
        50.1 + rng.normal(0, 0.02, n),            # disk_host (%)
        0.06 + np.abs(rng.normal(0, 0.01, n)),    # net_rx_kb
        np.abs(rng.normal(0.01, 0.004, n)),       # cpu_server (%)
        61.2 + rng.normal(0, 0.5, n),             # ram_server_mb
        25.6 + rng.normal(0, 0.3, n),             # ram_postgres_mb
        np.full(n, 3.0),                          # app_containers
    ])
    return X


def inject(X):
    """Injecte 3 pannes et renvoie la liste (nom, début, fin) en indices de cycles."""
    X = X.copy()
    i = {n: k for k, n in enumerate(NAMES)}
    events = [("surcharge_cpu", 200, 260), ("fuite_memoire_server", 450, 650), ("crash_postgres", 850, 900)]
    a, b = events[0][1:]
    X[a:b, i["cpu_host"]] = 95 + np.random.default_rng(1).normal(0, 2, b - a)
    a, b = events[1][1:]
    leak = np.linspace(61.2, 400, b - a)
    X[a:b, i["ram_server_mb"]] = leak
    X[a:b, i["ram_host"]] += (leak - 61.2) / 7800 * 100
    a, b = events[2][1:]
    X[a:b, i["app_containers"]] = 2
    X[a:b, i["ram_postgres_mb"]] = 0
    return X, events


class FakeProm:
    """Remplace Prometheus : sert un historique d'apprentissage et un flux temps réel."""

    def __init__(self, train_X, train_ts, target=None):
        target = target or Target()
        self.freshness = set(target.freshness.values())
        self.expr_index = {f.expr: k for k, f in enumerate(target.features)}
        self.train = {k: dict(zip(train_ts.astype(int), train_X[:, k])) for k in range(train_X.shape[1])}
        self.current = None
        self.staleness = 5.0

    def range(self, expr, start, end, step):
        return {t: v for t, v in self.train[self.expr_index[expr]].items() if start <= t <= end}

    def instant(self, expr, at=None):
        if expr in self.freshness:
            return self.staleness
        return float(self.current[self.expr_index[expr]])
