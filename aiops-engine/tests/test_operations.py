"""Chauffe, fenêtre de maintenance et calibration : les couches anti-fausses-alertes."""
import json
import os

import numpy as np

from aiops.calibrate import search
from aiops.config import Config
from aiops.engine import Engine
from aiops.features import NAMES
from aiops.synthetic import FakeProm, normal

CYCLE = 15
I = {n: k for k, n in enumerate(NAMES)}


def make(tmp_path, seed=21, **cfg):
    rng = np.random.default_rng(seed)
    t0 = 1_800_000_000
    prom = FakeProm(normal(rng, 1440), t0 - 1440 * 30 + np.arange(1440) * 30)
    clock = {"now": float(t0)}
    engine = Engine(Config(data_dir=str(tmp_path), **cfg), prom, clock=lambda: clock["now"])
    return engine, prom, clock, rng, t0


def run(engine, prom, clock, t0, stream, offset=0):
    opens = []
    for i, x in enumerate(stream):
        clock["now"] = t0 + (offset + i) * CYCLE
        prom.current = x
        was = engine.active
        engine.run_once()
        if engine.active and not was:
            opens.append(offset + i)
    return opens


def test_chauffe_supprime_l_alerte_du_deploiement(tmp_path):
    """Cas réel : pic CPU du build visible dans rate[2m] juste après le démarrage du moteur."""
    engine, prom, clock, rng, t0 = make(tmp_path)
    stream = normal(rng, 40)
    stream[:12, I["cpu_host"]] = 30            # trace du build pendant 3 min
    assert run(engine, prom, clock, t0, stream) == []


def test_apres_chauffe_la_detection_est_intacte(tmp_path):
    engine, prom, clock, rng, t0 = make(tmp_path)
    stream = normal(rng, 60)
    stream[30:, I["cpu_host"]] = 95
    opens = run(engine, prom, clock, t0, stream)
    assert len(opens) == 1 and (opens[0] - 30) * CYCLE <= 120


def test_fenetre_de_maintenance_active_puis_expiree(tmp_path):
    engine, prom, clock, rng, t0 = make(tmp_path, warmup_cycles=0)
    flag = tmp_path / "maintenance"
    flag.touch()
    os.utime(flag, (t0, t0))                   # posée maintenant
    stream = normal(rng, 30)
    stream[:, I["cpu_host"]] = 95
    assert run(engine, prom, clock, t0, stream) == []          # supprimée pendant la fenêtre
    clock2 = t0 + int(35 * 60 / CYCLE) * CYCLE                 # 35 min plus tard : expirée
    opens = run(engine, prom, clock, (clock2 - 0), stream, offset=0)
    assert len(opens) == 1


def test_calibration_choisit_un_reglage_faisable_et_plus_calme(tmp_path):
    """Journal réel simulé : bruit + décalages lents, comme observé sur le VPS."""
    rng = np.random.default_rng(0)
    n = 4 * 3600 // CYCLE                       # 4 h de journal
    z = np.abs(rng.normal(1.2, 0.8, n))
    z[300:340] += 3.2                           # dérive de 10 min (classe "nettoyage")
    z[600:604] += 4.0                           # pic court
    epochs = 1_800_000_000 + np.arange(n) * CYCLE
    rows = search(epochs, z, quiet=True)
    best = rows[0]
    assert best["feasible"]
    default = next(r for r in rows if (r["ewma_lambda"], r["vote_k"], r["vote_n"], r["z_high"]) == (0.4, 3, 4, 3.5))
    assert best["alerts_per_day"] <= default["alerts_per_day"]
    for name, limit in (("surcharge_cpu", 120), ("crash_postgres", 120), ("fuite_memoire_server", 600)):
        assert best["delays"][name] is not None and best["delays"][name] <= limit


def test_tuning_json_applique_au_demarrage(tmp_path):
    (tmp_path / "tuning.json").write_text(json.dumps(
        {"z_high": 4.5, "z_low": 3.5, "ewma_lambda": 0.3, "vote_k": 4, "vote_n": 6}))
    engine, *_ = make(tmp_path)
    assert engine.cfg.z_high == 4.5 and engine.z_det.high == 4.5
    assert engine.z_det.n == 6 and engine.relevance.maxlen == 6


def test_tuning_json_corrompu_ignore(tmp_path):
    (tmp_path / "tuning.json").write_text("{pas du json")
    engine, *_ = make(tmp_path)
    assert engine.cfg.z_high == 3.5             # valeurs par défaut conservées
