from agent.guards import Guards
from agent.policy import Plan
from tests.helpers import Clock, make_cfg

PLAN = Plan("app-1", "restart-container", "cadvisor")


def test_pause_entre_deux_actions_identiques(tmp_path):
    clock = Clock()
    g = Guards(make_cfg(tmp_path), clock=clock)
    assert g.check(PLAN, "i1") is None
    g.record(PLAN, "i1")
    assert g.check(PLAN, "i2")[0] == "pause"
    clock.advance(31 * 60)
    assert g.check(PLAN, "i2") is None


def test_quota_horaire(tmp_path):
    clock = Clock()
    g = Guards(make_cfg(tmp_path, max_actions_per_hour=2), clock=clock)
    for i in range(2):
        g.record(Plan("app-1", "restart-container", f"c{i}"), f"i{i}")
    assert g.check(Plan("app-1", "stop-sonarqube"), "i9")[0] == "quota"
    clock.advance(3601)
    assert g.check(Plan("app-1", "stop-sonarqube"), "i9") is None


def test_escalade_apres_tentatives(tmp_path):
    clock = Clock()
    g = Guards(make_cfg(tmp_path, max_attempts_per_incident=2), clock=clock)
    for _ in range(2):
        g.record(PLAN, "i1")
        clock.advance(31 * 60)
    assert g.check(PLAN, "i1")[0] == "escalade"


def test_disjoncteur(tmp_path):
    g = Guards(make_cfg(tmp_path, failure_breaker=3), clock=Clock())
    g.record_result(False)
    g.record_result(True)    # un succès remet le compteur à zéro
    for _ in range(3):
        g.record_result(False)
    assert g.check(PLAN, "i1")[0] == "suspendu"
    g.resume()
    assert g.check(PLAN, "i1") is None


def test_etat_conserve_apres_redemarrage(tmp_path):
    clock = Clock()
    cfg = make_cfg(tmp_path)
    Guards(cfg, clock=clock).record(PLAN, "i1")
    assert Guards(cfg, clock=clock).check(PLAN, "i2")[0] == "pause"


def test_etat_illisible_suspend_par_securite(tmp_path):
    (tmp_path / "state.json").write_text("{pas du json")
    g = Guards(make_cfg(tmp_path), clock=Clock())
    assert g.check(PLAN, "i1")[0] == "suspendu"
