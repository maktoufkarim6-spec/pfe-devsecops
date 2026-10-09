"""Backtest de bout en bout : le moteur complet doit détecter les pannes injectées
sans fausse alerte. Sert de porte de qualité dans le pipeline CI."""
import numpy as np

from aiops.config import Config
from aiops.engine import Engine
from tests.synthetic import FakeProm, inject, normal

CYCLE = 15
GRACE = 8  # cycles (2 min) autorisés après la fin d'une panne


def run_backtest(tmp_path, seed=7):
    rng = np.random.default_rng(seed)
    t0 = 1_800_000_000
    train_X = normal(rng, 1440)                                  # 12 h d'historique à 30 s
    train_ts = t0 - 1440 * 30 + np.arange(1440) * 30
    stream, events = inject(normal(rng, 1100))                   # 4,6 h de flux à 15 s

    prom = FakeProm(train_X, train_ts)
    clock = {"now": float(t0)}
    engine = Engine(Config(data_dir=str(tmp_path), warmup_cycles=0), prom, clock=lambda: clock["now"])

    opens = []
    for i, x in enumerate(stream):
        clock["now"] = t0 + i * CYCLE
        prom.current = x
        was = engine.active
        engine.run_once()
        if engine.active and not was:
            opens.append(i)
    return events, opens, engine


def evaluate(events, opens):
    detected, delays, matched = 0, [], set()
    for _, start, end in events:
        hits = [o for o in opens if start <= o <= end + GRACE]
        if hits:
            detected += 1
            delays.append((hits[0] - start) * CYCLE)
            matched.update(hits)
    false_alarms = len([o for o in opens if o not in matched])
    precision = detected / (detected + false_alarms) if detected + false_alarms else 0.0
    recall = detected / len(events)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "false_alarms": false_alarms, "delays_s": delays}


def test_backtest_porte_de_qualite(tmp_path):
    events, opens, engine = run_backtest(tmp_path)
    r = evaluate(events, opens)
    print("\nBacktest :", r)
    assert engine.model is not None
    assert r["f1"] >= 0.8
    assert r["false_alarms"] == 0
    assert r["delays_s"][0] <= 120   # surcharge CPU : critère < 2 min
    assert r["delays_s"][2] <= 120   # crash postgres : critère < 2 min
    assert (tmp_path / "model.joblib").exists()
    assert (tmp_path / "events.csv").exists()


def test_abstention_si_donnees_perimees(tmp_path):
    rng = np.random.default_rng(0)
    X = normal(rng, 600)
    prom = FakeProm(X, np.arange(600) * 30.0)
    engine = Engine(Config(data_dir=str(tmp_path)), prom, clock=lambda: 1e9)
    prom.current = X[0]
    prom.staleness = 600
    assert engine.run_once() is None and not engine.active


def test_redemarrage_recharge_le_modele(tmp_path):
    run_backtest(tmp_path)
    restarted = Engine(Config(data_dir=str(tmp_path), warmup_cycles=0), FakeProm(np.zeros((1, 8)), np.zeros(1)))
    assert restarted.model is not None


def test_decalage_faible_et_durable_sans_fausse_alerte(tmp_path):
    """Cas réel du VPS : après ajout d'outils, CPU +0.8 point et disque +1 point de façon durable."""
    rng = np.random.default_rng(3)
    t0 = 1_800_000_000
    train_X = normal(rng, 1440)
    train_X[:, 0] = 9.39 + rng.normal(0, 0.22, 1440)
    prom = FakeProm(train_X, t0 - 1440 * 30 + np.arange(1440) * 30)
    clock = {"now": float(t0)}
    engine = Engine(Config(data_dir=str(tmp_path), warmup_cycles=0), prom, clock=lambda: clock["now"])
    stream = normal(rng, 600)
    stream[:, 0] = 10.2 + rng.normal(0, 0.22, 600)
    stream[:, 2] += 1.0
    opened = 0
    for i, x in enumerate(stream):
        clock["now"] = t0 + i * CYCLE
        prom.current = x
        was = engine.active
        engine.run_once()
        opened += engine.active and not was
    assert opened == 0


def test_detecteur_if_bloque_n_empeche_pas_le_reentrainement(tmp_path):
    """Cas réel du VPS (17/09 07:46) : après un changement d'environnement, le score IF brut au repos
    reste au-dessus du seuil bas, donc le détecteur IF interne ne se ferme jamais.
    La porte de pertinence neutralise l'alerte, mais le ré-entraînement ne doit PAS être bloqué."""
    rng = np.random.default_rng(5)
    t0 = 1_800_000_000
    train_X = normal(rng, 1440)
    prom = FakeProm(train_X, t0 - 1440 * 30 + np.arange(1440) * 30)
    clock = {"now": float(t0)}
    engine = Engine(Config(data_dir=str(tmp_path), warmup_cycles=0), prom, clock=lambda: clock["now"])
    prom.current = normal(rng, 1)[0]
    engine.run_once()
    # Décalage de distribution : le score au repos (~0.4) reste au-dessus du seuil bas
    engine.if_det.set_thresholds(high=0.35, low=0.30)
    engine.relevance.clear()
    x = normal(rng, 1)[0]
    for i in range(1, 6):                     # repos : aucun écart significatif
        clock["now"] = t0 + i * CYCLE
        prom.current = x
        engine.run_once()
    assert engine.if_det.active               # IF interne bloqué ouvert, comme sur le VPS
    assert not engine.active                  # alerte effective fermée (porte de pertinence)
    engine.drift = True
    later = t0 + 2 * 3600
    assert engine.should_train(later)         # le ré-entraînement reste possible


def test_ecart_isole_sous_l_effet_minimal_ne_declenche_pas_l_if(tmp_path):
    """Cas réel du VPS (17/09 07:48) : détecteur IF interne ouvert + RAM serveur +2.57 points
    (51 % de l'effet minimal de 5 points) => fausse alerte en v2.1.0. Ne doit plus s'ouvrir."""
    rng = np.random.default_rng(6)
    t0 = 1_800_000_000
    prom = FakeProm(normal(rng, 1440), t0 - 1440 * 30 + np.arange(1440) * 30)
    clock = {"now": float(t0)}
    engine = Engine(Config(data_dir=str(tmp_path), warmup_cycles=0), prom, clock=lambda: clock["now"])
    prom.current = normal(rng, 1)[0]
    engine.run_once()
    engine.if_det.set_thresholds(high=0.35, low=0.30)   # IF interne bloqué ouvert
    x = np.median(normal(rng, 500), axis=0)
    x[1] += 2.57                                        # ram_host
    opened = 0
    for i in range(1, 40):
        clock["now"] = t0 + i * CYCLE
        prom.current = x
        was = engine.active
        engine.run_once()
        opened += engine.active and not was
    assert opened == 0
