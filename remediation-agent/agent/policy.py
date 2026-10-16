"""Politique de remédiation : quelle action sûre pour quelle alerte (fonctions pures, sans effet de bord).

L'agent ne choisit jamais une commande libre : il choisit dans une liste fermée d'actions, que le script
pfe-runbook de chaque nœud revérifie avant d'agir. Pour les anomalies AIOps, le choix dépend du
diagnostic du moteur (variable la plus déviante).
"""
import re
from dataclasses import dataclass

# Actions connues du script pfe-runbook des nœuds (même liste des deux côtés)
ACTIONS = ("status", "decouvrir", "restart-container", "rollout-restart", "rollout-app", "stop-sonarqube",
           "clean-disk")

# Applications de la plateforme (applications/<nom>/) : alertes portant l'étiquette application
APP_ALERTS = ("AnomalieApplication", "ApplicationHorsLigne")
# Variable du moteur qui désigne un composant : cpu_<composant> ou ram_<composant>_mb
COMPONENT_FEATURE = re.compile(r"^(?:cpu_(?P<cpu>[a-z0-9-]+)|ram_(?P<ram>[a-z0-9-]+)_mb)$")

# Variables du moteur AIOps qui désignent l'API de l'application
SERVER_FEATURES = ("cpu_server", "ram_server_mb")
HOST_LOAD_FEATURES = ("cpu_host", "ram_host")
COLLECTOR_JOBS = ("cadvisor", "cadvisor-k3s")


@dataclass(frozen=True)
class Plan:
    """Décision de la politique. action=None : aucune action automatique (escalade humaine)."""
    node: str
    action: str = None
    arg: str = ""
    reason: str = ""
    cible: str = ""     # étiquette node de l'alerte, si elle diffère du nœud qui agit (vérification de l'effet)

    @property
    def key(self):
        return f"{self.node}:{self.action}:{self.arg}"

    @property
    def label(self):
        return f"{self.action} {self.arg}".strip() if self.action else "aucune"


def target_node(alert, cfg):
    """Nœud concerné par l'alerte (les alertes du moteur AIOps visent le serveur de supervision)."""
    labels = alert.get("labels", {})
    if labels.get("alertname") == "MoteurAIOpsArrete":
        mon = cfg.monitoring_node()
        return mon.name if mon else None
    return labels.get("node")


def decide_application(alert, cfg, diagnose=None):
    """Alerte d'une application de la plateforme. Seule action possible : redémarrage progressif du composant
    que le moteur désigne (cpu_<c> ou ram_<c>_mb en hausse), jamais la base, toujours via le nœud de contrôle."""
    labels = alert.get("labels", {})
    name, app, cible = labels.get("alertname", ""), labels.get("application", ""), labels.get("node", "")
    composants = cfg.applications.get(app)
    ctl = cfg.k3s_node()
    if composants is None:
        return Plan(ctl.name if ctl else "-", reason=f"application inconnue de l'agent ({app!r})", cible=cible)
    if ctl is None:
        return Plan("-", reason="aucun nœud de contrôle k3s connu : pas d'action sur le cluster", cible=cible)
    if name == "ApplicationHorsLigne":
        return Plan(ctl.name, reason=f"{app} injoignable : Argo CD remet les pods manquants (self-heal) ; "
                                     "pas de redémarrage à l'aveugle, escalade humaine si l'alerte dure", cible=cible)
    diag = diagnose(cible) if diagnose and cible else None
    feature, z = diag if isinstance(diag, tuple) else (diag, None)
    if feature is None:
        return Plan(ctl.name, reason=f"diagnostic AIOps de {app} indisponible : pas d'action à l'aveugle", cible=cible)
    m = COMPONENT_FEATURE.match(feature)
    if feature == "ram_base_mb":
        return Plan(ctl.name, reason=f"anomalie sur la base de {app} : jamais redémarrée automatiquement, escalade humaine",
                    cible=cible)
    if not m:
        return Plan(ctl.name, reason=f"anomalie IA de {app} sur {feature} : pas d'action automatique sûre, escalade humaine",
                    cible=cible)
    composant = m.group("cpu") or m.group("ram")
    if composant not in composants:
        return Plan(ctl.name, reason=f"{feature} : composant {composant!r} hors liste de {app}", cible=cible)
    if z is not None and z < 0:
        return Plan(ctl.name, reason=f"{feature} en baisse (z={z:+.1f}) : pas une surcharge, aucune action", cible=cible)
    return Plan(ctl.name, "rollout-app", f"{app}/{composant}",
                f"anomalie IA de {app} sur {feature} : redémarrage progressif de {composant}, sans coupure", cible=cible)


def decide(alert, cfg, diagnose=None):
    """Choisit l'action pour une alerte en cours. diagnose(node) -> variable AIOps la plus déviante ou None."""
    labels = alert.get("labels", {})
    name = labels.get("alertname", "")
    if name in APP_ALERTS and labels.get("source") not in ("agent", "backup"):
        return decide_application(alert, cfg, diagnose)
    node_name = target_node(alert, cfg)
    if labels.get("source") in ("agent", "backup"):
        return Plan(node_name or "-", reason="alerte de l'agent ou des sauvegardes : jamais d'action sur soi-même")
    node = cfg.node(node_name) if node_name else None
    if node is None:
        return Plan(node_name or "-", reason=f"nœud inconnu de l'agent ({node_name!r})")

    if name == "NoeudInjoignable":
        return Plan(node.name, "restart-container", "node-exporter",
                    "node-exporter muet : redémarrage du collecteur (échec SSH = serveur réellement en panne)")
    if name == "CollecteurArrete":
        job = labels.get("job", "")
        if job not in COLLECTOR_JOBS or (job == "cadvisor-k3s" and not node.app):
            return Plan(node.name, reason=f"collecteur {job!r} hors liste des redémarrages autorisés")
        return Plan(node.name, "restart-container", job, f"collecteur {job} muet : redémarrage")
    if name == "MoteurAIOpsArrete":
        return Plan(node.name, "restart-container", "mon-aiops-engine", "moteur AIOps arrêté : redémarrage")
    if name == "RamElevee":
        if node.app:
            return Plan(node.name, "stop-sonarqube", "",
                        "RAM élevée : arrêt de SonarQube s'il tourne hors build (refusé pendant un build)")
        return Plan(node.name, reason="RAM élevée sans cause automatisable connue sur ce nœud")
    if name in ("DisquePlein", "DisquePleinPrevu"):
        if not cfg.disk_cleanup:
            return Plan(node.name, reason="nettoyage disque désactivé : accord du propriétaire requis (remediation_disk_cleanup)")
        return Plan(node.name, "clean-disk", "", "disque saturé ou saturation prévue : images Docker inutilisées et vieux journaux")
    if name == "AnomalieAIOps":
        diag = diagnose(node.name) if diagnose else None
        feature, z = diag if isinstance(diag, tuple) else (diag, None)
        if feature is None:
            return Plan(node.name, reason="diagnostic AIOps indisponible : pas d'action à l'aveugle")
        if z is not None and z < 0 and feature in SERVER_FEATURES + HOST_LOAD_FEATURES:
            # Une baisse (ex. cache mémoire libéré par le noyau) n'est pas une surcharge : rien à réparer
            return Plan(node.name, reason=f"{feature} en baisse (z={z:+.1f}) : pas une surcharge, aucune action")
        if node.app and feature in SERVER_FEATURES:
            # Action sur le cluster : exécutée par le nœud de contrôle k3s, quel que soit le nœud de l'anomalie
            ctl = cfg.k3s_node() or node
            where = "" if ctl.name == node.name else f" (via le nœud de contrôle {ctl.name})"
            return Plan(ctl.name, "rollout-restart", cfg.app_api,
                        f"anomalie IA sur {feature} ({node.name}) : redémarrage progressif de l'API, sans coupure{where}")
        if node.app and feature in HOST_LOAD_FEATURES:
            return Plan(node.name, "stop-sonarqube", "",
                        f"anomalie IA sur {feature} : arrêt de SonarQube s'il tourne hors build")
        return Plan(node.name, reason=f"anomalie IA sur {feature} : pas d'action automatique sûre, escalade humaine")
    return Plan(node.name, reason=f"{name} : surveillance seulement, pas d'action automatique")
