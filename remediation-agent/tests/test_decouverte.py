import json

from agent.decouverte import Decouverte, entries
from agent.executor import Result
from agent.metrics import Metrics
from tests.helpers import Clock, make_cfg

SORTIE = {"node": "app-1", "services": [
    {"nom": "jenkins:50000", "type": "tcp", "cible": "10.0.0.2:50000", "source": "docker", "noeud": "app-1", "lien": ""},
    {"nom": "jenkins:8080", "type": "tcp", "cible": "10.0.0.2:8080", "source": "docker", "noeud": "app-1", "lien": "x"},
    {"nom": "ingress default/app-ingress/", "type": "http", "cible": "http://10.0.0.2/", "source": "kubernetes",
     "noeud": "app-1", "lien": "http://10.0.0.2/"},
]}


class Exec:
    def __init__(self):
        self.reponses = {"app-1": Result(0, json.dumps(SORTIE), 0.1), "monitoring": Result(0, '{"services": []}', 0.1)}

    def execute(self, node, action, arg=""):
        assert action == "decouvrir"
        return self.reponses[node.name]


def build(tmp_path, **kw):
    cfg = make_cfg(tmp_path, discovery_file=str(tmp_path / "services.json"), **kw)
    return cfg, Exec(), Metrics(), Clock()


def test_services_declares_exclus_et_modules_choisis():
    out = entries(SORTIE["services"], {"10.0.0.2:8080"})
    assert [e["labels"]["service"] for e in out] == ["jenkins:50000", "ingress default/app-ingress/"]
    assert [e["labels"]["module"] for e in out] == ["tcp_connect", "http_2xx"]


def test_fichier_ecrit_pour_prometheus(tmp_path):
    cfg, ex, m, clock = build(tmp_path, services_declares=["10.0.0.2:8080"])
    Decouverte(cfg, ex, m, clock).run(force=True)
    data = json.loads((tmp_path / "services.json").read_text())
    assert {e["targets"][0] for e in data} == {"10.0.0.2:50000", "http://10.0.0.2/"}
    assert m.get("pfe_agent_services_discovered", node="app-1") == 2


def test_noeud_injoignable_garde_ses_services(tmp_path):
    cfg, ex, m, clock = build(tmp_path)
    d = Decouverte(cfg, ex, m, clock)
    d.run(force=True)
    ex.reponses["app-1"] = Result(255, "connexion SSH impossible", 10.0)
    d.run(force=True)
    data = json.loads((tmp_path / "services.json").read_text())
    assert len(data) == 3 and m.get("pfe_agent_discovery_ok", node="app-1") == 0


def test_reprise_depuis_le_fichier_apres_redemarrage(tmp_path):
    cfg, ex, m, clock = build(tmp_path)
    Decouverte(cfg, ex, m, clock).run(force=True)
    ex.reponses["app-1"] = Result(1, "erreur", 0.1)
    d2 = Decouverte(cfg, ex, m, clock)
    d2.run(force=True)
    assert len(json.loads((tmp_path / "services.json").read_text())) == 3


def test_rythme_d_une_minute(tmp_path):
    cfg, ex, m, clock = build(tmp_path)
    d = Decouverte(cfg, ex, m, clock)
    d.run()
    first = d.last
    clock.advance(30)
    d.run()
    assert d.last == first
    clock.advance(31)
    d.run()
    assert d.last > first
