from agent.guards import Guards
from agent.journal import Journal
from agent.metrics import Metrics
from agent.worker import Agent
from tests.helpers import Clock, FakeContext, FakeExecutor, alert, make_cfg


def build(tmp_path, mode="actif", code=0, **ctx):
    clock = Clock()
    cfg = make_cfg(tmp_path, mode=mode)
    ex = FakeExecutor(code=code)
    m = Metrics()
    agent = Agent(cfg, ex, FakeContext(**ctx), Guards(cfg, clock=clock), Journal(str(tmp_path), clock=clock), m, clock=clock)
    return agent, ex, m, clock


def decisions(tmp_path):
    return [r["decision"] for r in Journal(str(tmp_path)).tail(100) if r["type"] == "decision"]


def test_mode_conseil_ne_touche_a_rien(tmp_path):
    agent, ex, m, _ = build(tmp_path, mode="conseil")
    agent.handle(alert("CollecteurArrete", job="cadvisor"))
    assert ex.calls == []
    assert decisions(tmp_path) == ["simulee"]
    assert m.get("pfe_agent_actions_total", node="app-1", action="restart-container cadvisor", outcome="simulee") == 1


def test_mode_actif_execute_puis_verifie(tmp_path):
    agent, ex, m, clock = build(tmp_path, firing=False)
    agent.handle(alert("CollecteurArrete", job="cadvisor"))
    assert ex.calls == [("app-1", "restart-container", "cadvisor")]
    clock.advance(301)
    agent.verify_due()
    rows = Journal(str(tmp_path)).tail(10)
    assert rows[-1]["type"] == "verification" and rows[-1]["verification"] == "resolu"
    assert rows[-1]["id"] == rows[-2]["id"]


def test_maintenance_bloque_l_action(tmp_path):
    agent, ex, _, _ = build(tmp_path, maintenance=True)
    agent.handle(alert("RamElevee"))
    assert ex.calls == [] and decisions(tmp_path) == ["maintenance"]


def test_prometheus_muet_abstention(tmp_path):
    agent, ex, _, _ = build(tmp_path, maintenance=None)
    agent.handle(alert("RamElevee"))
    assert ex.calls == [] and decisions(tmp_path) == ["contexte_indisponible"]


def test_repetition_d_alerte_bloquee_par_la_pause(tmp_path):
    agent, ex, _, clock = build(tmp_path)
    agent.handle(alert("CollecteurArrete", job="cadvisor"))
    clock.advance(60)
    agent.handle(alert("CollecteurArrete", job="cadvisor"))
    assert len(ex.calls) == 1 and decisions(tmp_path) == ["executee", "pause"]


def test_echecs_repetes_suspendent_l_agent(tmp_path):
    agent, ex, m, clock = build(tmp_path, code=1)
    for i, job in enumerate(["cadvisor", "cadvisor-k3s"]):
        agent.handle(alert("CollecteurArrete", job=job, fp=f"f{i}"))
    agent.handle(alert("NoeudInjoignable", fp="f3"))
    assert m.get("pfe_agent_suspended") == 1
    clock.advance(3601)
    agent.handle(alert("RamElevee", fp="f4"))
    assert len(ex.calls) == 3 and decisions(tmp_path)[-1] == "suspendu"


def test_refus_du_noeud_n_est_pas_un_echec(tmp_path):
    agent, ex, m, _ = build(tmp_path, code=4)
    agent.handle(alert("RamElevee"))
    assert agent.guards.consecutive_failures == 0
    assert m.get("pfe_agent_actions_total", node="app-1", action="stop-sonarqube", outcome="refusee") == 1


def test_alerte_sans_action_journalisee_une_seule_fois(tmp_path):
    agent, ex, _, _ = build(tmp_path)
    for _ in range(3):
        agent.handle(alert("CpuEleve"))
    assert decisions(tmp_path) == ["aucune_action"]


def test_submit_ignore_les_alertes_resolues(tmp_path):
    agent, _, _, _ = build(tmp_path)
    n = agent.submit({"alerts": [alert("RamElevee"), alert("RamElevee", status="resolved")]})
    assert n == 1 and agent.queue.qsize() == 1


def test_sonde_ssh(tmp_path):
    agent, ex, m, _ = build(tmp_path)
    agent.probe(force=True)
    assert {c[1] for c in ex.calls} == {"status"}
    assert m.get("pfe_agent_node_reachable", node="monitoring") == 1
