"""Cas réels du VPS du 17/09 (nettoyage Docker) et changement de régime."""
import numpy as np
import pytest

from aiops.config import Config
from aiops.engine import Engine
from aiops.features import NAMES
from tests.synthetic import FakeProm, normal

CYCLE = 15
I = {n: k for k, n in enumerate(NAMES)}


def make_engine(tmp_path, seed=11):
    rng = np.random.default_rng(seed)
    t0 = 1_800_000_000
    prom = FakeProm(normal(rng, 1440), t0 - 1440 * 30 + np.arange(1440) * 30)
    clock = {"now": float(t0)}
    engine = Engine(Config(data_dir=str(tmp_path), warmup_cycles=0), prom, clock=lambda: clock["now"])
    return engine, prom, clock, rng, t0


def run(engine, prom, clock, t0, stream, offset=0):
    opens, regimes = [], []
    for i, x in enumerate(stream):
        clock["now"] = t0 + (offset + i) * CYCLE
        prom.current = x
        was = engine.active
        r = engine.run_once()
        if engine.active and not was:
            opens.append(offset + i)
        if r and r.get("regime") == "accepté":
            regimes.append(offset + i)
    return opens, regimes


def test_disque_qui_se_vide_n_est_pas_une_anomalie(tmp_path):
    """VPS 08:08 : nettoyage Docker, disque 50 % -> 44.9 % (z=-3.71) et RAM +3.8 points."""
    engine, prom, clock, rng, t0 = make_engine(tmp_path)
    run(engine, prom, clock, t0, normal(rng, 1))           # premier cycle : apprentissage
    engine.if_det.set_thresholds(high=0.35, low=0.30)      # IF interne bloqué, comme sur le VPS
    stream = normal(rng, 80)
    stream[:, I["disk_host"]] -= 5.3
    stream[:, I["ram_host"]] += 3.8
    opens, _ = run(engine, prom, clock, t0, stream, offset=1)
    assert opens == []


def test_disque_qui_se_remplit_reste_une_anomalie(tmp_path):
    engine, prom, clock, rng, t0 = make_engine(tmp_path)
    stream = normal(rng, 40)
    stream[:, I["disk_host"]] += 8
    opens, _ = run(engine, prom, clock, t0, stream)
    assert len(opens) == 1 and opens[0] * CYCLE <= 120


def test_chute_de_ram_postgres_reste_une_anomalie(tmp_path):
    engine, prom, clock, rng, t0 = make_engine(tmp_path)
    stream = normal(rng, 40)
    stream[:, I["ram_postgres_mb"]] = 0
    opens, _ = run(engine, prom, clock, t0, stream)
    assert len(opens) == 1


def test_pic_isole_de_pertinence_ne_contourne_pas_le_vote(tmp_path):
    """VPS 08:08:53 : IF interne ouvert + un seul cycle avec E >= 1 => ouverture immédiate en v2.1.2."""
    engine, prom, clock, rng, t0 = make_engine(tmp_path)
    run(engine, prom, clock, t0, normal(rng, 1))
    engine.if_det.set_thresholds(high=0.35, low=0.30)
    stream = normal(rng, 30)
    stream[10, I["cpu_server"]] = 7                        # 1 seul cycle au-dessus d'un effet minimal
    opens, _ = run(engine, prom, clock, t0, stream, offset=1)
    assert opens == []


def test_changement_de_regime_stable_accepte(tmp_path):
    """Plateau durable, stable et non critique (CPU 8 % -> 17 % après ajout d'un service)."""
    engine, prom, clock, rng, t0 = make_engine(tmp_path)
    stream = normal(rng, 240)                              # 60 min
    stream[:, I["cpu_host"]] += 9
    opens, regimes = run(engine, prom, clock, t0, stream)
    assert len(opens) == 1                                 # signalé une fois
    assert len(regimes) == 1                               # puis accepté comme nouveau régime
    assert (regimes[0] - opens[0]) * CYCLE == pytest.approx(30 * 60, abs=CYCLE)
    assert not engine.active and engine.model.if_suspended
    assert engine.model.median[I["cpu_host"]] > 15         # nouvelle référence
    assert engine.model.regime_start == t0 + opens[0] * CYCLE
    lines = (tmp_path / "events.csv").read_text().strip().splitlines()
    assert lines[-1].endswith("changement_de_regime")


def test_fuite_memoire_progressive_jamais_acceptee(tmp_path):
    engine, prom, clock, rng, t0 = make_engine(tmp_path)
    stream = normal(rng, 240)
    stream[:, I["ram_server_mb"]] = np.linspace(100, 900, 240)
    opens, regimes = run(engine, prom, clock, t0, stream)
    assert len(opens) == 1 and regimes == [] and engine.active


def test_conteneurs_perdus_jamais_acceptes(tmp_path):
    engine, prom, clock, rng, t0 = make_engine(tmp_path)
    stream = normal(rng, 240)
    stream[:, I["app_containers"]] = 2
    stream[:, I["ram_postgres_mb"]] = 0
    opens, regimes = run(engine, prom, clock, t0, stream)
    assert len(opens) == 1 and regimes == [] and engine.active


def test_niveau_critique_jamais_accepte(tmp_path):
    engine, prom, clock, rng, t0 = make_engine(tmp_path)
    stream = normal(rng, 240)
    stream[:, I["cpu_host"]] = 92 + rng.normal(0, 1, 240)
    opens, regimes = run(engine, prom, clock, t0, stream)
    assert len(opens) == 1 and regimes == [] and engine.active


def test_reentrainement_limite_au_nouveau_regime(tmp_path):
    engine, prom, clock, rng, t0 = make_engine(tmp_path)
    stream = normal(rng, 240)
    stream[:, I["cpu_host"]] += 9
    run(engine, prom, clock, t0, stream)
    start = engine.model.regime_start
    X, ts = engine.fetch_training_data(t0 + 240 * CYCLE)
    assert len(ts) == 0 or ts.min() >= start               # l'ancien régime est exclu
    assert engine.should_train(t0 + 240 * CYCLE + engine.cfg.train_retry_seconds)
