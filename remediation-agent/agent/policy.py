"""Politique de remédiation : quelle action sûre pour quelle alerte (fonctions pures, sans effet de bord).

L'agent ne choisit jamais une commande libre : il choisit dans une liste fermée d'actions, que le script
pfe-runbook de chaque nœud revérifie avant d'agir. Pour les anomalies AIOps, le choix dépend du
diagnostic du moteur (variable la plus déviante).
"""
from dataclasses import dataclass

# Actions connues du script pfe-runbook des nœuds (même liste des deux côtés)
ACTIONS = ("status", "restart-container", "rollout-restart", "stop-sonarqube", "clean-disk")

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


def decide(alert, cfg, diagnose=None):
    """Choisit l'action pour une alerte en cours. diagnose(node) -> variable AIOps la plus déviante ou None."""
    labels = alert.get("labels", {})
    name = labels.get("alertname", "")
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
            return Plan(node.name, "rollout-restart", "server",
                        f"anomalie IA sur {feature} : redémarrage progressif de l'API (sans coupure)")
        if node.app and feature in HOST_LOAD_FEATURES:
            return Plan(node.name, "stop-sonarqube", "",
                        f"anomalie IA sur {feature} : arrêt de SonarQube s'il tourne hors build")
        return Plan(node.name, reason=f"anomalie IA sur {feature} : pas d'action automatique sûre, escalade humaine")
    return Plan(node.name, reason=f"{name} : surveillance seulement, pas d'action automatique")
