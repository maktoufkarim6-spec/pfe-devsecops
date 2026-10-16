"""Applications de la plateforme : l'agent ne redémarre que le composant désigné, jamais la base."""
import pytest

from agent.config import Config, Node, parse_applications
from agent.executor import SshExecutor
from agent.policy import ACTIONS, decide
from tests.helpers import alert, make_cfg

APPS = {"boutique": ["api", "site"], "demo-podinfo": ["web"]}


@pytest.fixture
def cfg(tmp_path):
    return make_cfg(tmp_path, applications=dict(APPS))


def app_alert(name="AnomalieApplication", app="boutique"):
    a = alert(name, node=f"appli-{app}" if name == "AnomalieApplication" else None, application=app)
    return a


def test_composant_en_surcharge_redemarre(cfg):
    plan = decide(app_alert(), cfg, lambda n: ("ram_api_mb", 7.5))
    assert (plan.node, plan.action, plan.arg) == ("app-1", "rollout-app", "boutique/api")
    assert plan.cible == "appli-boutique"   # vérification de l'effet sur l'alerte de l'application
    assert plan.action in ACTIONS


def test_diagnostic_demande_pour_la_cible_de_l_application(cfg):
    vus = []
    decide(app_alert(app="demo-podinfo"), cfg, lambda n: vus.append(n) or ("cpu_web", 4.0))
    assert vus == ["appli-demo-podinfo"]


def test_cpu_d_un_composant(cfg):
    plan = decide(app_alert(app="demo-podinfo"), cfg, lambda n: ("cpu_web", 4.0))
    assert (plan.action, plan.arg) == ("rollout-app", "demo-podinfo/web")


@pytest.mark.parametrize("feature, motif", [
    (("ram_base_mb", 9.0), "base"),                 # la base n'est jamais redémarrée
    (("web_ok", -8.0), "pas d'action"),             # site injoignable : pas de redémarrage à l'aveugle
    (("app_containers", -3.0), "pas d'action"),     # conteneur perdu : Argo CD le remet
    (("ram_api_mb", -6.0), "baisse"),               # une baisse n'est pas une surcharge
    (("cpu_inconnu", 5.0), "hors liste"),           # composant non déclaré
    (None, "indisponible"),                          # pas de diagnostic : pas d'action
])
def test_aucune_action_hors_cas_surs(cfg, feature, motif):
    plan = decide(app_alert(), cfg, lambda n: feature)
    assert plan.action is None and motif in plan.reason


def test_application_inconnue(cfg):
    plan = decide(app_alert(app="inconnue"), cfg, lambda n: ("cpu_api", 5.0))
    assert plan.action is None and "inconnue" in plan.reason


def test_application_hors_ligne_escalade(cfg):
    plan = decide(app_alert("ApplicationHorsLigne"), cfg, lambda n: ("cpu_api", 5.0))
    assert plan.action is None and "Argo CD" in plan.reason


def test_action_via_le_noeud_de_controle(tmp_path):
    nodes = [Node("app-1", "10.0.0.2", app=True), Node("app-2", "10.0.0.3", app=True, k3s=True),
             Node("monitoring", "host.docker.internal", monitoring=True)]
    cfg = Config(data_dir=str(tmp_path), nodes=nodes, applications=dict(APPS))
    cfg.validate()
    assert decide(app_alert(), cfg, lambda n: ("cpu_site", 3.0)).node == "app-2"


def test_configuration_des_applications():
    assert parse_applications('{"boutique": ["api", "site"]}') == {"boutique": ["api", "site"]}
    assert parse_applications("") == {}
    for bad in ('{"Boutique": ["api"]}', '{"boutique": ["base"]}', '{"boutique": "api"}', '["boutique"]',
                '{"boutique": ["api web"]}'):
        with pytest.raises(ValueError):
            parse_applications(bad)
    cfg = Config.from_env({"AGENT_APPLICATIONS": '{"demo-podinfo": ["web"]}'})
    assert cfg.applications == {"demo-podinfo": ["web"]}


def test_argument_de_l_action_verifie_par_l_agent(cfg):
    ex = SshExecutor(cfg)
    node = cfg.node("app-1")
    assert ex.command(node, "rollout-app", "boutique/api")[-1] == "rollout-app boutique/api"
    for bad in ("boutique", "boutique/api/x", "Boutique/api", "boutique/api;rm", "../etc/x", "boutique/"):
        with pytest.raises(ValueError):
            ex.command(node, "rollout-app", bad)
    with pytest.raises(ValueError):   # la forme application/composant est réservée à rollout-app
        ex.command(node, "rollout-restart", "boutique/api")
