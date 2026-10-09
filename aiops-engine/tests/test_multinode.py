"""Supervision multi-nœuds : un modèle par nœud, isolation des nœuds entre eux."""
import os

import numpy as np
import pytest
from prometheus_client import REGISTRY

from aiops.config import Config
from aiops.engine import Engine
from aiops.features import Target, parse_targets
from aiops.synthetic import FakeProm, normal

CYCLE = 15
T0 = 1_800_000_000


def host_normal(rng, n):
    return normal(rng, n)[:, :4]  # nœud sans application : 4 variables système


def make(tmp_path, target, history, seed=0):
    prom = FakeProm(history, T0 - len(history) * 30 + np.arange(len(history)) * 30, target=target)
    clock = {"now": float(T0)}
    engine = Engine(Config(data_dir=str(tmp_path), warmup_cycles=0), prom, clock=lambda: clock["now"], target=target)
    return engine, prom, clock


def metric(name, node, **labels):
    return REGISTRY.get_sample_value(name, {"node": node, **labels})


def test_variables_filtrees_par_noeud():
    app, mon = Target("app-1", "app-1", app=True), Target("monitoring", "monitoring", app=False)
    assert app.names[:4] == mon.names and len(app.names) == 8 and len(mon.names) == 4
    assert all('node="app-1"' in f.expr for f in app.features)
    assert all('node="monitoring"' in f.expr for f in mon.features)
    assert set(mon.freshness) == {"node-exporter"}  # pas de cAdvisor-k3s attendu sur ce nœud


def test_configuration_des_noeuds():
    targets = parse_targets('[{"node": "app-1", "app": true}, {"node": "monitoring"}]')
    assert [(t.name, t.app) for t in targets] == [("app-1", True), ("monitoring", False)]
    assert parse_targets("")[0].legacy  # vide : mode mono-serveur historique
    for bad in ('[{"node": "app-1"}, {"node": "app-1"}]', '[{"node": "App 1"}]', "[]", '{"node": "x"}'):
        with pytest.raises(ValueError):
            parse_targets(bad)


def test_une_anomalie_sur_un_noeud_n_alerte_que_ce_noeud(tmp_path):
    rng = np.random.default_rng(1)
    a = Target("app-1", "app-1", app=True)
    b = Target("monitoring", "monitoring", app=False)
    ea, pa, ca = make(tmp_path, a, normal(rng, 1440))
    eb, pb, cb = make(tmp_path, b, host_normal(rng, 1440))
    xa, xb = normal(rng, 40), host_normal(rng, 40)
    xb[10:, 0] = 95  # surcharge CPU du serveur de supervision uniquement
    for i in range(40):
        for engine, prom, clock, x in ((ea, pa, ca, xa), (eb, pb, cb, xb)):
            clock["now"] = T0 + i * CYCLE
            prom.current = x[i]
            engine.run_once()
    assert eb.active and not ea.active
    assert metric("aiops_anomaly_active", "monitoring") == 1
    assert metric("aiops_anomaly_active", "app-1") == 0
    assert metric("aiops_top_feature", "monitoring", feature="cpu_host") == 1


def test_fichiers_et_modeles_separes_par_noeud(tmp_path):
    rng = np.random.default_rng(2)
    a = Target("app-1", "app-1", app=True)
    b = Target("monitoring", "monitoring", app=False)
    for t, hist in ((a, normal(rng, 800)), (b, host_normal(rng, 800))):
        engine, prom, clock = make(tmp_path, t, hist)
        prom.current = hist[-1]
        engine.run_once()
    files = sorted(os.listdir(tmp_path))
    assert files == ["detections-app-1.csv", "detections-monitoring.csv", "model-app-1.joblib", "model-monitoring.joblib"]


def test_modele_d_un_autre_noeud_jamais_recharge(tmp_path):
    """Un modèle à 8 variables (nœud applicatif) n'est pas utilisé pour un nœud à 4 variables."""
    rng = np.random.default_rng(3)
    a = Target("app-1", "app-1", app=True)
    engine, prom, clock = make(tmp_path, a, normal(rng, 800))
    prom.current = normal(rng, 1)[0]
    engine.run_once()
    os.rename(tmp_path / "model-app-1.joblib", tmp_path / "model-monitoring.joblib")
    b = Target("monitoring", "monitoring", app=False)
    other = Engine(Config(data_dir=str(tmp_path)), FakeProm(host_normal(rng, 1), np.zeros(1), target=b), target=b)
    assert other.model is None


def test_maintenance_propre_a_un_noeud(tmp_path):
    rng = np.random.default_rng(4)
    a = Target("app-1", "app-1", app=True)
    b = Target("monitoring", "monitoring", app=False)
    ea, _, _ = make(tmp_path, a, normal(rng, 800))
    eb, _, _ = make(tmp_path, b, host_normal(rng, 800))
    (tmp_path / "maintenance-app-1").touch()
    now = os.path.getmtime(tmp_path / "maintenance-app-1")
    assert ea.maintenance_active(now) and not eb.maintenance_active(now)
    (tmp_path / "maintenance").touch()  # fichier global : tous les nœuds
    assert eb.maintenance_active(os.path.getmtime(tmp_path / "maintenance"))


def test_noeud_injoignable_isole(tmp_path):
    """Données périmées sur un nœud (collecteur en panne) : abstention sur ce nœud seulement."""
    rng = np.random.default_rng(5)
    a = Target("app-1", "app-1", app=True)
    b = Target("monitoring", "monitoring", app=False)
    ea, pa, ca = make(tmp_path, a, normal(rng, 800))
    eb, pb, cb = make(tmp_path, b, host_normal(rng, 800))
    pa.current, pb.current = normal(rng, 1)[0], host_normal(rng, 1)[0]
    pa.staleness = 900
    assert ea.run_once() is None and eb.run_once() is not None
    assert metric("aiops_data_quality", "app-1") == 0
    assert metric("aiops_data_quality", "monitoring") == 1


def test_maintenance_annoncee_par_le_noeud_via_prometheus(tmp_path):
    """Jenkins annonce sa maintenance sur le nœud applicatif (métrique node-exporter) :
    seul ce nœud est en maintenance, et l'annonce expire seule."""
    rng = np.random.default_rng(6)
    a = Target("app-1", "app-1", app=True)
    b = Target("monitoring", "monitoring", app=False)
    ea, pa, ca = make(tmp_path, a, normal(rng, 800))
    eb, pb, cb = make(tmp_path, b, host_normal(rng, 800))
    now = T0
    pa.maintenance_until = now + 30 * 60
    assert ea.maintenance_active(now) and not eb.maintenance_active(now)
    assert not ea.maintenance_active(now + 31 * 60)  # expirée


def test_maintenance_annoncee_trop_longue_ignoree(tmp_path):
    rng = np.random.default_rng(7)
    a = Target("app-1", "app-1", app=True)
    ea, pa, _ = make(tmp_path, a, normal(rng, 800))
    pa.maintenance_until = T0 + 24 * 3600  # « silence » d'une journée : refusé
    assert not ea.maintenance_active(T0)


def test_alerte_supprimee_pendant_maintenance_annoncee(tmp_path):
    rng = np.random.default_rng(8)
    a = Target("app-1", "app-1", app=True)
    ea, pa, ca = make(tmp_path, a, normal(rng, 1440))
    stream = normal(rng, 30)
    stream[:, 0] = 95
    pa.maintenance_until = T0 + 20 * 60
    for i in range(30):
        ca["now"] = T0 + i * CYCLE
        pa.current = stream[i]
        ea.run_once()
    assert not ea.active
    assert metric("aiops_maintenance", "app-1") == 1
