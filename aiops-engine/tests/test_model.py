import numpy as np

from aiops.features import DIRECTIONS, MIN_EFFECTS, NAMES
from aiops.model import AnomalyModel, train_champion_challenger
from tests.synthetic import normal

CPU, RAM, DISK, NET, CPU_SRV, RAM_SRV, RAM_PG, PODS = range(8)


def _model(X=None):
    rng = np.random.default_rng(0)
    X = normal(rng, 1500) if X is None else X
    return AnomalyModel(NAMES, min_effects=MIN_EFFECTS).fit(X, np.arange(len(X)) * 30.0), np.median(X, axis=0)


def test_cas_reel_petite_variation_cpu_non_significative():
    """Reproduit la fausse alerte observée sur le VPS le 17/09 :
    MAD(cpu_host)=0.15 apprise, CPU 9.39 % -> 10.1 % => z brut = 3.2 (limite).
    Avec le plancher (effet minimal 5 points), ce n'est plus une anomalie."""
    rng = np.random.default_rng(1)
    X = normal(rng, 1500)
    X[:, CPU] = 9.39 + rng.normal(0, 0.22, 1500)       # CPU très stable, comme sur le VPS
    m, base = _model(X)
    assert m.mad_raw[CPU] < 0.2
    x = base.copy()
    x[CPU] = 10.14
    z_brut = 0.6745 * (x[CPU] - m.median[CPU]) / m.mad_raw[CPU]
    assert z_brut > 3                                   # sans plancher : quasi-alerte
    assert abs(m.zscores(x)[CPU]) < 1                   # avec plancher : négligeable
    assert not m.is_relevant(x)[0]                      # porte de pertinence fermée
    assert m.combined_effect(x)[0] < 0.2


def test_plancher_equivaut_a_l_effet_minimal():
    m, base = _model()
    x = base.copy()
    x[CPU] = base[CPU] + 5.01
    assert m.zscores(x)[CPU] > 3.5          # un écart >= effet minimal reste détectable
    x[CPU] = base[CPU] + 4.9
    assert m.zscores(x)[CPU] < 3.5 or m.mad_raw[CPU] > m.mad[CPU] * 0.99


def test_complementarite_isolation_forest_zscore():
    m, base = _model()
    # 1) Anomalie univariée extrême : diluée pour l'IF, évidente pour le z-score
    x = base.copy()
    x[CPU] = 95
    assert m.score(x, 0)[0] < m.threshold
    assert np.abs(m.zscores(x)).max() > 3.5
    # 2) Anomalie multivariée modérée : sous le seuil variable par variable, détectée par l'IF
    y = base.copy()
    for k in (CPU, RAM, RAM_SRV, RAM_PG):
        y[k] = base[k] + 3.0 * m.mad[k] / 0.6745
    assert np.abs(m.zscores(y)).max() < 3.5
    assert m.score(y, 0)[0] > m.threshold
    assert m.is_relevant(y)[0]


def test_effet_combine():
    m, base = _model()
    x = base.copy()
    x[RAM] = base[RAM] + 2.57          # 0.51 effet minimal, seul : non pertinent
    assert abs(m.combined_effect(x)[0] - 0.514) < 1e-3 and not m.is_relevant(x)[0]
    x[CPU] = base[CPU] + 4.3           # 0.86 effet minimal en plus : E = 1.0
    assert m.is_relevant(x)[0]


def test_explication_variable_responsable():
    m, base = _model()
    x = base.copy()
    x[RAM_SRV] = 400
    assert m.explain(x)[0] == "ram_server_mb"


def test_persistance_atomique(tmp_path):
    rng = np.random.default_rng(0)
    X = normal(rng, 600)
    m = AnomalyModel(NAMES, min_effects=MIN_EFFECTS).fit(X, np.arange(600) * 30.0)
    path = tmp_path / "model.joblib"
    m.save(str(path))
    loaded = AnomalyModel.load(str(path))
    assert np.allclose(loaded.score(X[:10], np.zeros(10)), m.score(X[:10], np.zeros(10)))
    assert np.allclose(loaded.mad, m.mad)
    assert list(tmp_path.iterdir()) == [path]


def test_saisonnalite_activee_selon_historique():
    rng = np.random.default_rng(0)
    X = normal(rng, 800)
    short, _ = train_champion_challenger(X, np.arange(800) * 30.0, NAMES, min_effects=MIN_EFFECTS)
    long_, _ = train_champion_challenger(X, np.arange(800) * 240.0, NAMES, min_effects=MIN_EFFECTS)
    assert not short.seasonal and long_.seasonal


def test_apprentissage_exclut_les_pannes_passees():
    rng = np.random.default_rng(0)
    X = normal(rng, 2000)
    ts = np.arange(2000) * 30.0
    champion, info = train_champion_challenger(X, ts, NAMES, min_effects=MIN_EFFECTS)
    assert info["accepted"] and champion.version == 1
    polluted = X.copy()
    polluted[1500:1700, CPU] = 95
    challenger, info2 = train_champion_challenger(polluted, ts, NAMES, champion=champion, min_effects=MIN_EFFECTS)
    assert info2["removed_as_anomalies"] >= 195
    assert challenger.version == 2
    assert challenger.median[CPU] < 12


def test_isolation_forest_calme_au_repos_apres_derives_insignifiantes():
    """Cas réel du VPS (17/09 10:50) : diagnostic mesuré, 46 % des cycles de repos au-dessus du seuil IF.
    Cause : dérives insignifiantes cumulées (CPU +1 pt, RAM +2,3 pts, postgres +1 Mo, disque -5 pts)."""
    rng = np.random.default_rng(0)
    mad = np.array([0.148, 0.632, 0.502, 0.010, 0.010, 0.626, 0.320, 0.0])
    med = np.array([9.4, 63.18, 50.23, 0.045, 0.001, 62.645, 32.047, 3.0])
    X = med + rng.normal(0, 1, (1300, 8)) * mad / 0.6745
    X[:, PODS] = 3
    m = AnomalyModel(NAMES, min_effects=MIN_EFFECTS, directions=DIRECTIONS).fit(X, np.arange(1300) * 30.0)
    rest = np.array([10.392, 65.433, 44.982, 0.045, 0.001, 63.324, 33.039, 3.0])
    rest = rest + rng.normal(0, 1, (240, 8)) * mad / 0.6745 * 0.5
    rest[:, PODS] = 3
    above = np.mean(m.score(rest, np.zeros(240)) > m.threshold)
    assert above < 0.02


def test_isolation_forest_garde_son_role_multivarie():
    """4 variables à 0,9 effet minimal : chaque z = 3,15 < 3,5 (invisible pour le z-score), l'IF doit détecter."""
    m, base = _model()
    d = np.array(MIN_EFFECTS)
    y = base.copy()
    for k in (CPU, RAM, RAM_SRV, RAM_PG):
        y[k] = m.median[k] + 0.9 * d[k]
    assert np.abs(m.directional_z(y)).max() < 3.5
    assert m.score(y, 0)[0] > m.threshold
