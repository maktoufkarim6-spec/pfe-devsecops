"""Fonctions mathématiques pures (testées unitairement).

Références :
  - Iglewicz & Hoaglin (1993) : z-score modifié, constante 0.6745, seuil 3.5
  - Liu, Ting & Zhou (2008)   : Isolation Forest, s(x,n) = 2^(-E[h(x)]/c(n))
  - Sen (1968)                : estimateur de Theil-Sen (point de rupture ~29 %)
  - Siddiqi (2006)            : Population Stability Index
"""
import math

import numpy as np

EULER_GAMMA = 0.5772156649015329
MAD_CONSTANT = 0.6745


def c_n(n: int) -> float:
    """Longueur moyenne d'un chemin infructueux dans un arbre binaire de recherche.

    c(n) = 2 H(n-1) - 2(n-1)/n   avec   H(i) ~ ln(i) + gamma
    """
    if n > 2:
        return 2.0 * (math.log(n - 1) + EULER_GAMMA) - 2.0 * (n - 1) / n
    return 1.0 if n == 2 else 0.0


def robust_center_scale(X, floor_rel=0.01, floor_abs=0.01):
    """Médiane et MAD par colonne, avec plancher pour éviter la division par zéro."""
    X = np.asarray(X, dtype=float)
    median = np.median(X, axis=0)
    mad = np.median(np.abs(X - median), axis=0)
    floor = np.maximum(floor_rel * np.abs(median), floor_abs)
    return median, np.maximum(mad, floor)


def robust_z(x, median, mad):
    """z = 0.6745 (x - médiane) / MAD."""
    return MAD_CONSTANT * (np.asarray(x, dtype=float) - median) / mad


def ewma(previous, value, lam):
    """Lissage exponentiel : S_t = lambda * s_t + (1 - lambda) * S_{t-1}."""
    if not 0.0 < lam <= 1.0:
        raise ValueError("lambda doit être dans ]0, 1]")
    return float(value) if previous is None else lam * float(value) + (1.0 - lam) * previous


def kofn_false_alarm_probability(p: float, k: int, n: int) -> float:
    """P(au moins k dépassements sur n) pour des points indépendants de probabilité p."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def theil_sen(t, y):
    """Pente = médiane des pentes entre toutes les paires ; ordonnée = médiane(y - beta t).

    Retourne (beta, alpha, r2).
    """
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(t) < 2:
        return 0.0, float(y.mean()) if len(y) else 0.0, 0.0
    i, j = np.triu_indices(len(t), k=1)
    dt = t[j] - t[i]
    mask = dt != 0
    if not mask.any():
        return 0.0, float(np.median(y)), 0.0
    beta = float(np.median((y[j] - y[i])[mask] / dt[mask]))
    alpha = float(np.median(y - beta * t))
    y_hat = alpha + beta * t
    ss_res = float(((y - y_hat) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return beta, alpha, r2


def hours_to_saturation(t_hours, y, capacity=100.0, min_r2=0.7, horizon=8760.0):
    """Temps avant d'atteindre `capacity` selon Theil-Sen.

    Retourne (heures, beta, r2, fiable). Non fiable si la tendance n'est pas linéaire (R² < min_r2)
    ou si la ressource ne croît pas.
    """
    beta, alpha, r2 = theil_sen(t_hours, y)
    y_now = alpha + beta * float(np.asarray(t_hours)[-1])
    if beta <= 1e-9:
        return horizon, beta, r2, False
    hours = max(0.0, min((capacity - y_now) / beta, horizon))
    return hours, beta, r2, bool(r2 >= min_r2)


def psi(expected, actual, bins=10, eps=1e-4):
    """Population Stability Index : sum (a_i - e_i) ln(a_i / e_i).

    Tranches définies par les quantiles de la distribution d'apprentissage.
    < 0.1 stable, 0.1-0.25 dérive modérée, > 0.25 dérive importante.
    """
    expected = np.asarray(expected, dtype=float)
    actual = np.asarray(actual, dtype=float)
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, bins=edges)[0] / len(expected)
    a = np.histogram(actual, bins=edges)[0] / len(actual)
    e = np.clip(e, eps, None)
    a = np.clip(a, eps, None)
    return float(((a - e) * np.log(a / e)).sum())


def cyclic_hour(epoch_seconds):
    """Encodage cyclique de l'heure UTC : (sin(2 pi h/24), cos(2 pi h/24))."""
    h = (np.asarray(epoch_seconds, dtype=float) % 86400.0) / 3600.0
    angle = 2.0 * np.pi * h / 24.0
    return np.sin(angle), np.cos(angle)
