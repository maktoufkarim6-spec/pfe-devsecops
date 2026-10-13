import pytest

from agent.config import Config, parse_nodes
from agent.policy import ACTIONS, decide
from tests.helpers import alert, make_cfg


@pytest.fixture
def cfg(tmp_path):
    return make_cfg(tmp_path)


def test_toutes_les_actions_choisies_sont_dans_la_liste_fermee(cfg):
    cases = [alert("NoeudInjoignable"), alert("CollecteurArrete", job="cadvisor"), alert("MoteurAIOpsArrete", node=None),
             alert("RamElevee"), alert("AnomalieAIOps")]
    for a in cases:
        plan = decide(a, cfg, lambda n: "ram_server_mb")
        assert plan.action is None or plan.action in ACTIONS


def test_anomalie_api_redemarrage_progressif(cfg):
    plan = decide(alert("AnomalieAIOps"), cfg, lambda n: "cpu_server")
    assert (plan.node, plan.action, plan.arg) == ("app-1", "rollout-restart", "server")


def test_anomalie_charge_hote_arret_sonarqube(cfg):
    plan = decide(alert("AnomalieAIOps"), cfg, lambda n: "ram_host")
    assert plan.action == "stop-sonarqube"


def test_anomalie_en_hausse_avec_zscore_signe(cfg):
    assert decide(alert("AnomalieAIOps"), cfg, lambda n: ("ram_server_mb", 6.2)).action == "rollout-restart"


def test_baisse_de_memoire_n_est_pas_une_surcharge(cfg):
    plan = decide(alert("AnomalieAIOps"), cfg, lambda n: ("ram_server_mb", -5.6))
    assert plan.action is None and "baisse" in plan.reason


def test_anomalie_sans_diagnostic_pas_d_action(cfg):
    assert decide(alert("AnomalieAIOps"), cfg, lambda n: None).action is None


def test_anomalie_variable_non_automatisable(cfg):
    assert decide(alert("AnomalieAIOps"), cfg, lambda n: "net_rx_kb").action is None


def test_anomalie_api_sur_noeud_non_applicatif(cfg):
    assert decide(alert("AnomalieAIOps", node="monitoring"), cfg, lambda n: "cpu_server").action is None


def test_moteur_arrete_vise_le_serveur_de_supervision(cfg):
    plan = decide(alert("MoteurAIOpsArrete", node=None), cfg)
    assert (plan.node, plan.action, plan.arg) == ("monitoring", "restart-container", "mon-aiops-engine")


def test_collecteur_k3s_refuse_hors_noeud_applicatif(cfg):
    assert decide(alert("CollecteurArrete", node="monitoring", job="cadvisor-k3s"), cfg).action is None
    assert decide(alert("CollecteurArrete", job="cadvisor-k3s"), cfg).arg == "cadvisor-k3s"


def test_collecteur_inconnu_refuse(cfg):
    assert decide(alert("CollecteurArrete", job="jenkins"), cfg).action is None


def test_disque_desactive_par_defaut(cfg):
    plan = decide(alert("DisquePlein"), cfg)
    assert plan.action is None and "accord" in plan.reason


def test_disque_autorise_explicitement(tmp_path):
    cfg = make_cfg(tmp_path, disk_cleanup=True)
    assert decide(alert("DisquePleinPrevu"), cfg).action == "clean-disk"


def test_jamais_d_action_sur_les_alertes_de_l_agent(cfg):
    assert decide(alert("AgentRemediationSuspendu", source="agent"), cfg).action is None
    assert decide(alert("SauvegardeEnRetard", source="backup"), cfg).action is None


def test_noeud_inconnu(cfg):
    assert decide(alert("NoeudInjoignable", node="pirate"), cfg).action is None


def test_alerte_sans_regle(cfg):
    assert decide(alert("CpuEleve"), cfg).action is None


def test_config_refuse_mode_inconnu():
    with pytest.raises(ValueError):
        Config(mode="yolo").validate()


def test_config_lit_l_environnement():
    cfg = Config.from_env({"AGENT_MODE": "actif", "AGENT_DISK_CLEANUP": "true", "AGENT_MAX_ACTIONS_PER_HOUR": "5",
                           "AGENT_NODES": '[{"node": "app-1", "address": "1.2.3.4", "app": true}]'})
    assert cfg.mode == "actif" and cfg.disk_cleanup and cfg.max_actions_per_hour == 5
    assert cfg.node("app-1").app


@pytest.mark.parametrize("raw", ['[{"node": "App 1", "address": "x"}]',
                                 '[{"node": "a", "address": "x"}, {"node": "a", "address": "y"}]'])
def test_noeuds_invalides(raw):
    with pytest.raises(ValueError):
        parse_nodes(raw)


def test_deploiement_de_l_api_configurable(tmp_path):
    cfg = make_cfg(tmp_path, app_api="api-gateway")
    plan = decide(alert("AnomalieAIOps"), cfg, lambda n: ("cpu_server", 8.0))
    assert (plan.action, plan.arg) == ("rollout-restart", "api-gateway")
    with pytest.raises(ValueError):
        make_cfg(tmp_path, app_api="Pas valide; reboot")
