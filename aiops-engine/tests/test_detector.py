import pytest

from aiops.detector import HysteresisDetector


def feed(det, values):
    return [det.update(v) for v in values]


def test_un_pic_isole_ne_declenche_pas():
    det = HysteresisDetector(high=3.5, low=2.5, k=3, n=4, lam=0.4)
    states = feed(det, [0.5] * 20 + [50] + [0.5] * 20)
    # EWMA : un pic unique reste au-dessus du seuil au plus 2-3 cycles puis retombe
    assert sum(s.opened for s in states) <= 1


def test_pic_de_une_mesure_amorti():
    det = HysteresisDetector(high=3.5, low=2.5, k=3, n=4, lam=0.4)
    states = feed(det, [0.5] * 20 + [8] + [0.5] * 20)
    assert not any(s.opened for s in states)


def test_anomalie_persistante_detectee_en_moins_de_2_minutes():
    det = HysteresisDetector(high=3.5, low=2.5, k=3, n=4, lam=0.4)
    states = feed(det, [0.5] * 20 + [5.0] * 20)
    first = next(i for i, s in enumerate(states) if s.opened) - 20
    assert first * 15 <= 120


def test_hysteresis_pas_de_clignotement():
    det = HysteresisDetector(high=3.5, low=2.5, k=3, n=4, lam=0.4)
    states = feed(det, [0.5] * 10 + [6] * 10 + [3.0, 3.8, 3.0, 3.8, 3.0, 3.8] * 5 + [0.5] * 20)
    assert sum(s.opened for s in states) == 1
    assert sum(s.closed for s in states) == 1


def test_parametres_invalides():
    with pytest.raises(ValueError):
        HysteresisDetector(high=1, low=2)
    with pytest.raises(ValueError):
        HysteresisDetector(high=2, low=1, k=5, n=4)
