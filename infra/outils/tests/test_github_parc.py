"""Tests de github_parc.py contre un faux GitHub (serveur HTTP local)."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FauxGitHub(BaseHTTPRequestHandler):
    etat = {}

    def log_message(self, *a):
        pass

    def repondre(self, code, corps=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if corps is not None:
            self.wfile.write(json.dumps(corps).encode())

    def do_GET(self):
        e = self.etat
        e["appels"].append(("GET", self.path, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer bon-jeton":
            return self.repondre(401, {"message": "Bad credentials"})
        if self.path == "/user":
            return self.repondre(200, {"login": "encadrant"})
        if self.path == "/repos/encadrant/pfe-devsecops":
            return self.repondre(200 if e["fork_demande"] or e["fork_existe"] else 404, {})
        if self.path == "/repos/encadrant/pfe-devsecops/branches/main":
            e["attente"] -= 1
            return self.repondre(200 if e["attente"] < 0 else 404, {})
        if self.path == "/repos/encadrant/pfe-devsecops/hooks":
            return self.repondre(200, e["hooks"])
        return self.repondre(404, {"message": "Not Found"})

    def do_POST(self):
        e = self.etat
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        e["appels"].append(("POST", self.path, corps))
        if self.path == "/repos/maktoufkarim6-spec/pfe-devsecops/forks":
            e["fork_demande"] = False   # le fork n'existe qu'après le délai de GitHub
            e["fork_post"] = True
            return self.repondre(202, {})
        if self.path == "/repos/encadrant/pfe-devsecops/hooks":
            e["hooks"].append(corps)
            return self.repondre(201, corps)
        return self.repondre(404, {})


@pytest.fixture
def gh(monkeypatch):
    FauxGitHub.etat = {"appels": [], "fork_demande": False, "fork_existe": False, "attente": 2, "hooks": []}
    srv = HTTPServer(("127.0.0.1", 0), FauxGitHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("PFE_GITHUB_API", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("GITHUB_TOKEN", "bon-jeton")
    import importlib
    import github_parc
    importlib.reload(github_parc)
    yield github_parc, FauxGitHub.etat
    srv.shutdown()


def test_utilisateur(gh):
    g, _ = gh
    assert g.utilisateur() == "encadrant"


def test_fork_attend_que_github_l_ait_cree(gh):
    g, e = gh
    assert g.fork("maktoufkarim6-spec/pfe-devsecops", pause=0.01) == "https://github.com/encadrant/pfe-devsecops"
    assert e["fork_post"] and e["attente"] < 0


def test_fork_deja_fait_rien_n_est_demande(gh):
    g, e = gh
    e["fork_existe"] = True
    assert g.fork("maktoufkarim6-spec/pfe-devsecops") == "https://github.com/encadrant/pfe-devsecops"
    assert not any(a[0] == "POST" for a in e["appels"])


def test_son_propre_depot_pas_de_fork(gh):
    g, e = gh
    assert g.fork("encadrant/pfe-devsecops") == "https://github.com/encadrant/pfe-devsecops"
    assert not any(a[0] == "POST" for a in e["appels"])


def test_webhook_cree_une_seule_fois(gh):
    g, e = gh
    url = "http://10.0.0.3:8080/github-webhook/"
    assert g.webhook("encadrant/pfe-devsecops", url) == "créé"
    assert g.webhook("encadrant/pfe-devsecops", url) == "existe déjà"
    assert len(e["hooks"]) == 1 and e["hooks"][0]["events"] == ["push"] and e["hooks"][0]["config"]["url"] == url


@pytest.mark.parametrize("depot, url", [("encadrant/pfe;rm", "http://10.0.0.3:8080/github-webhook/"),
                                        ("encadrant/pfe-devsecops", "http://evil.example/hook"),
                                        ("encadrant/pfe-devsecops", "https://10.0.0.3:8080/github-webhook/")])
def test_webhook_parametres_refuses(gh, depot, url):
    g, _ = gh
    with pytest.raises(g.Erreur):
        g.webhook(depot, url)


def test_jeton_refuse_message_clair_sans_le_jeton(gh, monkeypatch, capsys):
    g, _ = gh
    monkeypatch.setenv("GITHUB_TOKEN", "mauvais-jeton")
    assert g.main(["utilisateur"]) == 1
    err = capsys.readouterr().err
    assert "HTTP 401" in err and "jeton refusé" in err and "mauvais-jeton" not in err
