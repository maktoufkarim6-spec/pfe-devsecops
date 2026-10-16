"""Applications de la plateforme : un modèle par application, à côté des modèles par nœud."""
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
BOUTIQUE = '{"application": "boutique", "composants": ["api", "site"], "base": true}'


def boutique():
    return parse_targets(f"[{BOUTIQUE}]")[0]


def app_normal(rng, n):
    """cpu_api, ram_api_mb, cpu_site, ram_site_mb, ram_base_mb, app_containers, web_ok, web_ms"""
    return np.column_stack([
        np.abs(rng.normal(0.5, 0.1, n)), 80 + rng.normal(0, 0.5, n),
        np.abs(rng.normal(0.2, 0.05, n)), 12 + rng.normal(0, 0.2, n),
        30 + rng.normal(0, 0.3, n), np.full(n, 3.0),
        np.ones(n), 40 + np.abs(rng.normal(0, 3, n)),
    ])


def make(tmp_path, target, history):
    prom = FakeProm(history, T0 - len(history) * 30 + np.arange(len(history)) * 30, target=target)
    clock = {"now": float(T0)}
    engine = Engine(Config(data_dir=str(tmp_path), warmup_cycles=0), prom, clock=lambda: clock["now"], target=target)
    return engine, prom, clock


def run(engine, prom, clock, stream):
    for i, x in enumerate(stream):
        clock["now"] = T0 + i * CYCLE
        prom.current = x
        engine.run_once()


def metric(name, node, **labels):
    return REGISTRY.get_sample_value(name, {"node": node, **labels})


def test_variables_d_une_application():
    t = boutique()
    assert (t.name, t.kind, t.application) == ("appli-boutique", "application", "boutique")
    assert t.names == ["cpu_api", "ram_api_mb", "cpu_site", "ram_site_mb", "ram_base_mb",
                       "app_containers", "web_ok", "web_ms"]
    assert all('pod_namespace="boutique"' in f.expr for f in t.features[:6])
    assert all('application="boutique"' in f.expr for f in t.features[6:])
    assert not any("node=" in f.expr for f in t.features)   # pas liée à un nœud : pods sur n'importe lequel
    assert set(t.freshness) == {"cadvisor-k3s", "sonde-web"}
    assert not t.legacy and not t.forecasts
    sans_web = parse_targets('[{"application": "worker", "composants": ["job"], "web": false}]')[0]
    assert sans_web.names == ["cpu_job", "ram_job_mb", "app_containers"] and set(sans_web.freshness) == {"cadvisor-k3s"}


def test_noeuds_et_applications_ensemble():
    targets = parse_targets(f'[{{"node": "app-1", "app": true}}, {{"node": "monitoring"}}, {BOUTIQUE}]')
    assert [(t.name, t.kind) for t in targets] == [("app-1", "node"), ("monitoring", "node"),
                                                   ("appli-boutique", "application")]


@pytest.mark.parametrize("bad", [
    '[{"application": "boutique", "composants": []}]',                  # aucun composant
    '[{"application": "boutique", "composants": ["base"]}]',            # nom réservé à la base
    '[{"application": "boutique", "composants": ["api", "api"]}]',      # doublon
    '[{"application": "Boutique", "composants": ["api"]}]',             # nom invalide
    '[{"application": "boutique", "composants": ["Api Web"]}]',         # composant invalide
    '[{"node": "appli-boutique"}, {"application": "boutique", "composants": ["api"]}]',   # même nom
    '["boutique"]',
])
def test_cibles_application_invalides(bad):
    with pytest.raises(ValueError):
        parse_targets(bad)


def test_fuite_memoire_d_un_composant_designee(tmp_path):
    rng = np.random.default_rng(11)
    t = boutique()
    engine, prom, clock = make(tmp_path, t, app_normal(rng, 1440))
    stream = app_normal(rng, 40)
    stream[10:, 1] = np.linspace(120, 400, 30)   # ram_api_mb : fuite mémoire de l'API
    run(engine, prom, clock, stream)
    assert engine.active
    assert metric("aiops_anomaly_active", "appli-boutique") == 1
    assert metric("aiops_top_feature", "appli-boutique", feature="ram_api_mb") == 1
    assert REGISTRY.get_sample_value("aiops_target_info", {"node": "appli-boutique", "kind": "application",
                                                           "application": "boutique"}) == 1


def test_application_injoignable_detectee(tmp_path):
    rng = np.random.default_rng(12)
    t = boutique()
    engine, prom, clock = make(tmp_path, t, app_normal(rng, 1440))
    stream = app_normal(rng, 40)
    stream[10:, 6] = 0        # web_ok : la sonde échoue
    stream[10:, 7] = 5000     # web_ms : délai de la sonde
    run(engine, prom, clock, stream)
    assert engine.active
    assert metric("aiops_top_feature", "appli-boutique", feature="web_ok") == 1 \
        or metric("aiops_top_feature", "appli-boutique", feature="web_ms") == 1


def test_application_isolee_des_noeuds(tmp_path):
    """Une panne de l'application n'ouvre pas d'anomalie sur le nœud, et inversement."""
    rng = np.random.default_rng(13)
    a = Target("app-1", "app-1", app=True)
    ea, pa, ca = make(tmp_path, a, normal(rng, 1440))
    eb, pb, cb = make(tmp_path, boutique(), app_normal(rng, 1440))
    xa, xb = normal(rng, 40), app_normal(rng, 40)
    xb[10:, 2] = 300   # cpu_site : boucle infinie du site
    for i in range(40):
        for engine, prom, clock, x in ((ea, pa, ca, xa), (eb, pb, cb, xb)):
            clock["now"] = T0 + i * CYCLE
            prom.current = x[i]
            engine.run_once()
    assert eb.active and not ea.active


def test_fichiers_propres_a_l_application(tmp_path):
    rng = np.random.default_rng(14)
    engine, prom, clock = make(tmp_path, boutique(), app_normal(rng, 800))
    prom.current = app_normal(rng, 1)[0]
    engine.run_once()
    engine.forecast(T0)   # pas de prévision disque/RAM pour une application : sans erreur
    assert sorted(os.listdir(tmp_path)) == ["detections-appli-boutique.csv", "model-appli-boutique.joblib"]


def test_maintenance_d_un_noeud_couvre_les_applications(tmp_path):
    """Build Jenkins ou déploiement annoncé sur un nœud : les applications (sans nœud attitré) se taisent."""
    rng = np.random.default_rng(15)
    engine, prom, clock = make(tmp_path, boutique(), app_normal(rng, 800))
    prom.maintenance_until = T0 + 20 * 60
    assert engine.maintenance_active(T0)
    assert not engine.maintenance_active(T0 + 21 * 60)
