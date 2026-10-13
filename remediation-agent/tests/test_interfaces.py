import json
import subprocess
import threading
import urllib.error
import urllib.request

import pytest

from agent.config import Node
from agent.executor import SshExecutor
from agent.metrics import Metrics
from agent.server import make_handler, serve
from tests.helpers import alert, make_cfg


class Collect:
    def __init__(self):
        self.payloads = []

    def submit(self, payload):
        self.payloads.append(payload)
        return len(payload["alerts"])


@pytest.fixture
def http(tmp_path):
    sink, metrics = Collect(), Metrics()
    httpd = serve(0, make_handler(sink, "secret", metrics, lambda: True))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", sink, metrics
    httpd.shutdown()


def post(url, body, token="secret"):
    req = urllib.request.Request(url + "/alert", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


def test_webhook_accepte_avec_jeton(http):
    url, sink, _ = http
    assert post(url, {"alerts": [alert("RamElevee")]}) == 202
    assert len(sink.payloads) == 1


def test_webhook_refuse_sans_bon_jeton(http):
    url, sink, metrics = http
    assert post(url, {"alerts": []}, token="faux") == 401
    assert sink.payloads == [] and metrics.get("pfe_agent_webhooks_total", status="refuse") == 1


def test_webhook_refuse_un_format_inattendu(http):
    url, _, _ = http
    assert post(url, ["pas", "un", "objet"]) == 400


def test_metrics_et_sante(http):
    url, _, metrics = http
    metrics.set("pfe_agent_mode", 0)
    body = urllib.request.urlopen(url + "/metrics", timeout=5).read().decode()
    assert "pfe_agent_mode 0" in body
    assert urllib.request.urlopen(url + "/healthz", timeout=5).status == 200


def test_metriques_echappent_les_etiquettes():
    m = Metrics()
    m.inc("pfe_agent_decisions_total", alertname='a"b', node="x", action="y", decision="z")
    assert 'alertname="a\\"b"' in m.render()


class FakeRun:
    def __init__(self, code=0, out="", exc=None):
        self.code, self.out, self.exc, self.cmd = code, out, exc, None

    def __call__(self, cmd, **kw):
        self.cmd = cmd
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(cmd, self.code, self.out, "")


def test_commande_ssh_stricte(tmp_path):
    run = FakeRun(out="redémarré")
    ex = SshExecutor(make_cfg(tmp_path), run=run)
    res = ex.execute(Node("app-1", "10.0.0.2"), "restart-container", "cadvisor")
    assert res.outcome == "succes"
    assert "StrictHostKeyChecking=yes" in run.cmd and "HostKeyAlias=app-1" in run.cmd
    assert run.cmd[-2:] == ["pfe-agent@10.0.0.2", "restart-container cadvisor"]


@pytest.mark.parametrize("action,arg", [("rm", "-rf"), ("restart-container", "x; reboot"), ("status", "A")])
def test_action_ou_argument_hors_liste_refuses_avant_ssh(tmp_path, action, arg):
    with pytest.raises(ValueError):
        SshExecutor(make_cfg(tmp_path), run=FakeRun()).command(Node("app-1", "x"), action, arg)


def test_codes_retour(tmp_path):
    cfg = make_cfg(tmp_path)
    node = Node("app-1", "x")
    assert SshExecutor(cfg, run=FakeRun(code=4)).execute(node, "stop-sonarqube").outcome == "refusee"
    assert SshExecutor(cfg, run=FakeRun(code=3)).execute(node, "stop-sonarqube").outcome == "rien_a_faire"
    ssh_err = SshExecutor(cfg, run=FakeRun(code=255, out="Connection refused")).execute(node, "status")
    assert ssh_err.outcome == "echec" and "SSH" in ssh_err.output
    timeout = SshExecutor(cfg, run=FakeRun(exc=subprocess.TimeoutExpired("ssh", 1))).execute(node, "status")
    assert timeout.outcome == "echec"


def test_horodatage_publie_sans_arrondi():
    """Bug réel : le format %g arrondissait 1791879123.4 en 1.79188e+09 (jusqu'à 1 h 30 d'écart)."""
    m = Metrics()
    m.set("pfe_agent_last_action_timestamp_seconds", 1791879123.4, node="app-1", action="x", outcome="succes")
    valeur = float(m.render().strip().splitlines()[-1].split()[-1])
    assert abs(valeur - 1791879123.4) < 0.01
