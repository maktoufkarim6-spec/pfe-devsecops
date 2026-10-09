import math

import numpy as np
import pytest

from aiops.mathlib import (c_n, cyclic_hour, ewma, hours_to_saturation, kofn_false_alarm_probability,
                           psi, robust_center_scale, robust_z, theil_sen)


def test_c_n_valeur_de_reference():
    # c(256) = 2(ln 255 + gamma) - 2*255/256
    assert c_n(256) == pytest.approx(10.2448, abs=1e-3)
    assert c_n(2) == 1.0 and c_n(1) == 0.0


def test_mad_resiste_aux_valeurs_aberrantes():
    data = np.array([[10.0]] * 99 + [[1e6]])
    median, mad = robust_center_scale(data)
    assert median[0] == 10.0
    assert mad[0] == pytest.approx(0.1)  # plancher 1 % de la médiane, non influencé par 1e6


def test_robust_z_formule():
    z = robust_z(np.array([14.0]), np.array([10.0]), np.array([2.0]))
    assert z[0] == pytest.approx(0.6745 * 4 / 2)


def test_ewma():
    assert ewma(None, 5, 0.4) == 5
    assert ewma(10, 0, 0.4) == pytest.approx(6)
    with pytest.raises(ValueError):
        ewma(1, 1, 0)


def test_probabilite_fausse_alarme_k_sur_n():
    p = kofn_false_alarm_probability(0.005, 3, 4)
    assert p == pytest.approx(4 * 0.005 ** 3 * 0.995 + 0.005 ** 4)
    assert p < 1e-6


def test_theil_sen_ignore_les_aberrants():
    t = np.arange(50, dtype=float)
    y = 2 * t + 1
    y[[5, 17, 30, 44]] = 1000  # 8 % de points aberrants
    beta, alpha, _ = theil_sen(t, y)
    assert beta == pytest.approx(2, abs=1e-9)
    assert alpha == pytest.approx(1, abs=1e-9)


def test_prevision_saturation():
    t = np.linspace(0, 6, 360)
    y = 50 + 2 * t  # +2 points de % par heure
    hours, beta, r2, reliable = hours_to_saturation(t, y)
    assert hours == pytest.approx((100 - 62) / 2)
    assert reliable and r2 == pytest.approx(1)


def test_prevision_non_fiable_si_bruit():
    rng = np.random.default_rng(0)
    t = np.linspace(0, 6, 360)
    _, _, _, reliable = hours_to_saturation(t, 50 + rng.normal(0, 1, 360))
    assert not reliable


def test_psi():
    rng = np.random.default_rng(0)
    ref = rng.normal(0, 1, 5000)
    assert psi(ref, rng.normal(0, 1, 1000)) < 0.1
    assert psi(ref, rng.normal(1.5, 1, 1000)) > 0.25


def test_heure_cyclique_continuite_minuit():
    s23, c23 = cyclic_hour(23 * 3600)
    s0, c0 = cyclic_hour(0)
    s12, c12 = cyclic_hour(12 * 3600)
    d_23_0 = math.hypot(s23 - s0, c23 - c0)
    d_12_0 = math.hypot(s12 - s0, c12 - c0)
    assert d_23_0 < 0.3 < d_12_0
