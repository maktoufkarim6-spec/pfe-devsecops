"""Variables observées, par nœud supervisé.

min_effect = plus petit écart (en unités de la variable) jugé opérationnellement significatif.
direction  = sens dangereux : "up" (seule une hausse compte), "down", ou "both".
critical   = niveau au-delà duquel un nouvel état ne peut jamais être accepté comme normal.

Multi-nœuds : chaque nœud est identifié dans Prometheus par l'étiquette node="<nom>",
posée sur tous ses collecteurs (node-exporter, cAdvisor). Le moteur entraîne un modèle par nœud.
Un nœud « applicatif » (qui héberge l'application k3s) ajoute les 4 variables de l'application.
"""
import json
import re
from collections import namedtuple
from dataclasses import dataclass, field

INF = float("inf")
Feature = namedtuple("Feature", "name expr lo hi min_effect direction critical")
NODE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

NET = 'device!~"lo|veth.*|docker.*|br-.*|cni.*|flannel.*"'
DISK = 'mountpoint="/",fstype="ext4"'
IDLE = 'mode="idle"'
K3S = 'job="cadvisor-k3s"'
APP = 'job="cadvisor-k3s",container_label_io_kubernetes_pod_namespace="default"'


def _sel(*parts):
    """Assemble un sélecteur PromQL en ignorant les morceaux vides ("" si aucun)."""
    kept = [p for p in parts if p]
    return "{" + ",".join(kept) + "}" if kept else ""


def build_features(node=None, app=True):
    """Variables d'un nœud. node=None : pas de filtre (mode historique, un seul serveur)."""
    n = f'node="{node}"' if node else ""
    host = [
        ("cpu_host", f'100 - avg(rate(node_cpu_seconds_total{_sel(IDLE, n)}[2m])) * 100',
         0, 100.5, 5, "up", 80),
        ("ram_host", f'(1 - avg(node_memory_MemAvailable_bytes{_sel(n)}) / avg(node_memory_MemTotal_bytes{_sel(n)})) * 100',
         0, 100, 5, "up", 85),
        ("disk_host", f'100 - avg(node_filesystem_avail_bytes{_sel(DISK, n)}) / avg(node_filesystem_size_bytes{_sel(DISK, n)}) * 100',
         0, 100, 5, "up", 85),
        ("net_rx_kb", f'sum(rate(node_network_receive_bytes_total{_sel(NET, n)}[2m])) / 1024',
         0, 1e7, 50, "both", INF),
    ]
    a = _sel(APP, n)[1:-1]
    apps = [
        ("cpu_server", f'(sum(rate(container_cpu_usage_seconds_total{{{a},container_label_io_kubernetes_container_name="server"}}[2m])) * 100) or vector(0)',
         0, 6400, 5, "up", INF),
        ("ram_server_mb", f'(sum(container_memory_working_set_bytes{{{a},container_label_io_kubernetes_container_name="server"}}) / 1048576) or vector(0)',
         0, 1e6, 30, "both", INF),
        ("ram_postgres_mb", f'(sum(container_memory_working_set_bytes{{{a},container_label_io_kubernetes_container_name="postgres"}}) / 1048576) or vector(0)',
         0, 1e6, 20, "both", INF),
        ("app_containers", f'count(count by (container_label_io_kubernetes_container_name) (container_memory_working_set_bytes{{{a},container_label_io_kubernetes_container_name!="",container_label_io_kubernetes_container_name!="POD"}})) or vector(0)',
         0, 1000, 0.5, "both", INF),
    ]
    return [Feature(*f) for f in host + (apps if app else [])]


def build_freshness(node=None, app=True):
    """Âge (s) du dernier échantillon de chaque source du nœud."""
    n = f'node="{node}"' if node else ""
    out = {"node-exporter": f"time() - max(timestamp(node_memory_MemAvailable_bytes{_sel(n)}))"}
    if app:
        out["cadvisor-k3s"] = f"time() - max(timestamp(container_memory_working_set_bytes{_sel(K3S, n)}))"
    return out


@dataclass
class Target:
    """Un nœud supervisé. name : nom affiché et étiquette Prometheus ; app : héberge l'application."""
    name: str = "local"
    node: str = None
    app: bool = True
    features: list = field(init=False)
    freshness: dict = field(init=False)

    def __post_init__(self):
        if not NODE_NAME.match(self.name):
            raise ValueError(f"nom de nœud invalide : {self.name!r} (minuscules, chiffres, tirets)")
        self.features = build_features(self.node, self.app)
        self.freshness = build_freshness(self.node, self.app)

    names = property(lambda self: [f.name for f in self.features])
    min_effects = property(lambda self: [f.min_effect for f in self.features])
    directions = property(lambda self: [f.direction for f in self.features])
    critical = property(lambda self: [f.critical for f in self.features])
    pods = property(lambda self: self.names.index("app_containers") if self.app else None)

    @property
    def legacy(self):
        """Mode historique (un seul serveur) : noms de fichiers inchangés."""
        return self.node is None


def parse_targets(raw):
    """AIOPS_TARGETS = '[{"node": "app-1", "app": true}, {"node": "monitoring", "app": false}]'.
    Vide : un seul nœud, sans filtre (compatible avec le déploiement mono-serveur)."""
    if not raw or not raw.strip():
        return [Target()]
    items = json.loads(raw)
    if not isinstance(items, list) or not items:
        raise ValueError("AIOPS_TARGETS doit être une liste JSON non vide")
    targets = [Target(name=i["node"], node=i["node"], app=bool(i.get("app", False))) for i in items]
    if len({t.name for t in targets}) != len(targets):
        raise ValueError("AIOPS_TARGETS contient deux fois le même nœud")
    return targets


# Compatibilité : jeu de variables par défaut (un serveur applicatif, sans filtre de nœud)
_DEFAULT = Target()
FEATURES = _DEFAULT.features
NAMES = _DEFAULT.names
MIN_EFFECTS = _DEFAULT.min_effects
DIRECTIONS = _DEFAULT.directions
CRITICAL = _DEFAULT.critical
FRESHNESS = _DEFAULT.freshness
