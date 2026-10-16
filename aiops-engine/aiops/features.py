"""Variables observées, par nœud supervisé.

min_effect = plus petit écart (en unités de la variable) jugé opérationnellement significatif.
direction  = sens dangereux : "up" (seule une hausse compte), "down", ou "both".
critical   = niveau au-delà duquel un nouvel état ne peut jamais être accepté comme normal.

Multi-nœuds : chaque nœud est identifié dans Prometheus par l'étiquette node="<nom>",
posée sur tous ses collecteurs (node-exporter, cAdvisor). Le moteur entraîne un modèle par nœud.
Un nœud « applicatif » (qui héberge l'application k3s) ajoute les 4 variables de l'application.

Applications de la plateforme (applications/<nom>/, docs/APPLICATIONS.md) : une cible « application » par
application, nommée appli-<nom>, avec son propre modèle. Variables : CPU et RAM de chaque composant (et de sa
base), nombre de conteneurs, disponibilité et temps de réponse de son adresse web (sonde blackbox, job
« applications »). Elle n'est liée à aucun nœud : ses pods peuvent tourner sur n'importe lequel.

Profil de l'application cobaye (pour superviser une autre application, sans changer le code) :
  AIOPS_APP_NAMESPACE (défaut default), AIOPS_APP_API (conteneur de l'API, défaut server),
  AIOPS_APP_DB (conteneur de la base, défaut postgres). Les noms internes des variables
  (cpu_server, ram_server_mb, ram_postgres_mb) restent fixes : « server » désigne l'API, « postgres » la base.
"""
import hashlib
import json
import os
import re
from collections import namedtuple
from dataclasses import dataclass, field

INF = float("inf")
Feature = namedtuple("Feature", "name expr lo hi min_effect direction critical")
NODE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
K8S_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


def _k8s_env(var, default):
    value = os.getenv(var, default)
    if not K8S_NAME.match(value):
        raise ValueError(f"{var} invalide : {value!r} (nom Kubernetes attendu)")
    return value


APP_NAMESPACE = _k8s_env("AIOPS_APP_NAMESPACE", "default")
APP_API = _k8s_env("AIOPS_APP_API", "server")
APP_DB = _k8s_env("AIOPS_APP_DB", "postgres")

NET = 'device!~"lo|veth.*|docker.*|br-.*|cni.*|flannel.*"'
DISK = 'mountpoint="/",fstype="ext4"'
IDLE = 'mode="idle"'
K3S = 'job="cadvisor-k3s"'
APP = f'job="cadvisor-k3s",container_label_io_kubernetes_pod_namespace="{APP_NAMESPACE}"'


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
        ("cpu_server", f'(sum(rate(container_cpu_usage_seconds_total{{{a},container_label_io_kubernetes_container_name="{APP_API}"}}[2m])) * 100) or vector(0)',
         0, 6400, 5, "up", INF),
        ("ram_server_mb", f'(sum(container_memory_working_set_bytes{{{a},container_label_io_kubernetes_container_name="{APP_API}"}}) / 1048576) or vector(0)',
         0, 1e6, 30, "both", INF),
        ("ram_postgres_mb", f'(sum(container_memory_working_set_bytes{{{a},container_label_io_kubernetes_container_name="{APP_DB}"}}) / 1048576) or vector(0)',
         0, 1e6, 20, "both", INF),
        ("app_containers", f'count(count by (container_label_io_kubernetes_container_name) (container_memory_working_set_bytes{{{a},container_label_io_kubernetes_container_name!="",container_label_io_kubernetes_container_name!="POD"}})) or vector(0)',
         0, 1000, 0.5, "both", INF),
    ]
    return [Feature(*f) for f in host + (apps if app else [])]


def build_app_features(app, composants, base=False, web=True):
    """Variables d'une application de la plateforme (namespace = nom de l'application)."""
    ns = f'job="cadvisor-k3s",container_label_io_kubernetes_pod_namespace="{app}"'
    out = []
    for c in composants:
        sel = f'{ns},container_label_io_kubernetes_container_name="{c}"'
        out += [
            (f"cpu_{c}", f'(sum(rate(container_cpu_usage_seconds_total{{{sel}}}[2m])) * 100) or vector(0)',
             0, 6400, 5, "up", INF),
            (f"ram_{c}_mb", f'(sum(container_memory_working_set_bytes{{{sel}}}) / 1048576) or vector(0)',
             0, 1e6, 20, "both", INF),
        ]
    if base:
        out.append(("ram_base_mb", f'(sum(container_memory_working_set_bytes{{{ns},container_label_io_kubernetes_container_name="postgres"}}) / 1048576) or vector(0)',
                    0, 1e6, 20, "both", INF))
    out.append(("app_containers", f'count(count by (container_label_io_kubernetes_container_name) (container_memory_working_set_bytes{{{ns},container_label_io_kubernetes_container_name!="",container_label_io_kubernetes_container_name!="POD"}})) or vector(0)',
                0, 1000, 0.5, "both", INF))
    if web:
        # Sans « or vector(0) » : avant la création de la sonde, il n'y a PAS de mesure (et non « 0 ») ;
        # l'apprentissage ne garde que les instants où toutes les variables existent.
        probe = f'job="applications",application="{app}"'
        out += [
            ("web_ok", f'min(probe_success{{{probe}}})', 0, 1, 0.5, "down", INF),
            ("web_ms", f'max(probe_duration_seconds{{{probe}}}) * 1000', 0, 60000, 200, "up", INF),
        ]
    return [Feature(*f) for f in out]


def build_app_freshness(app, web=True):
    """Fraîcheur des sources d'une application : collecteur des pods (tous nœuds) et sonde de son adresse."""
    out = {"cadvisor-k3s": 'time() - max(timestamp(container_memory_working_set_bytes{job="cadvisor-k3s"}))'}
    if web:
        out["sonde-web"] = f'time() - max(timestamp(probe_success{{job="applications",application="{app}"}}))'
    return out


def build_freshness(node=None, app=True):
    """Âge (s) du dernier échantillon de chaque source du nœud."""
    n = f'node="{node}"' if node else ""
    out = {"node-exporter": f"time() - max(timestamp(node_memory_MemAvailable_bytes{_sel(n)}))"}
    if app:
        out["cadvisor-k3s"] = f"time() - max(timestamp(container_memory_working_set_bytes{_sel(K3S, n)}))"
    return out


@dataclass
class Target:
    """Une cible supervisée : un nœud (kind="node", app : héberge l'application cobaye) ou une application
    de la plateforme (kind="application", name appli-<nom>, composants, base, web)."""
    name: str = "local"
    node: str = None
    app: bool = True
    kind: str = "node"
    composants: tuple = ()
    base: bool = False
    web: bool = True
    features: list = field(init=False)
    freshness: dict = field(init=False)

    def __post_init__(self):
        if not NODE_NAME.match(self.name):
            raise ValueError(f"nom de nœud invalide : {self.name!r} (minuscules, chiffres, tirets)")
        if self.kind == "application":
            if self.node is not None or not self.name.startswith(APP_PREFIX):
                raise ValueError(f"cible application mal formée : {self.name!r}")
            if not K8S_NAME.match(self.application) or not self.composants:
                raise ValueError(f"application invalide : {self.application!r} (nom Kubernetes et au moins un composant)")
            for c in self.composants:
                if not K8S_NAME.match(c) or c == "base":
                    raise ValueError(f"composant invalide dans {self.application} : {c!r}")
            if len(set(self.composants)) != len(self.composants):
                raise ValueError(f"composant en double dans {self.application}")
            self.app = True   # porte la variable app_containers (même logique que le nœud applicatif)
            self.features = build_app_features(self.application, self.composants, self.base, self.web)
            self.freshness = build_app_freshness(self.application, self.web)
        elif self.kind == "node":
            self.features = build_features(self.node, self.app)
            self.freshness = build_freshness(self.node, self.app)
        else:
            raise ValueError(f"type de cible inconnu : {self.kind!r}")

    names = property(lambda self: [f.name for f in self.features])
    min_effects = property(lambda self: [f.min_effect for f in self.features])
    directions = property(lambda self: [f.direction for f in self.features])
    critical = property(lambda self: [f.critical for f in self.features])
    pods = property(lambda self: self.names.index("app_containers") if self.app else None)
    application = property(lambda self: self.name[len(APP_PREFIX):] if self.kind == "application" else "")

    @property
    def legacy(self):
        """Mode historique (un seul serveur) : noms de fichiers inchangés."""
        return self.kind == "node" and self.node is None

    @property
    def signature(self):
        """Empreinte des formules des variables : un modèle appris avec d'autres formules n'est pas réutilisé."""
        return hashlib.sha256("\n".join(f.expr for f in self.features).encode()).hexdigest()[:16]

    @property
    def forecasts(self):
        """Prévisions de saturation (disque, RAM du serveur) : nœuds seulement."""
        return self.kind == "node"


APP_PREFIX = "appli-"


def parse_targets(raw):
    """AIOPS_TARGETS = '[{"node": "app-1", "app": true}, {"node": "monitoring", "app": false},
                         {"application": "boutique", "composants": ["api", "site"], "base": true, "web": true}]'.
    Vide : un seul nœud, sans filtre (compatible avec le déploiement mono-serveur)."""
    if not raw or not raw.strip():
        return [Target()]
    items = json.loads(raw)
    if not isinstance(items, list) or not items:
        raise ValueError("AIOPS_TARGETS doit être une liste JSON non vide")
    targets = []
    for i in items:
        if not isinstance(i, dict):
            raise ValueError(f"cible invalide : {i!r}")
        if "application" in i:
            targets.append(Target(name=APP_PREFIX + str(i["application"]), node=None, kind="application",
                                  composants=tuple(i.get("composants") or ()), base=bool(i.get("base", False)),
                                  web=bool(i.get("web", True))))
        else:
            targets.append(Target(name=i["node"], node=i["node"], app=bool(i.get("app", False))))
    if len({t.name for t in targets}) != len(targets):
        raise ValueError("AIOPS_TARGETS contient deux fois la même cible")
    return targets


# Compatibilité : jeu de variables par défaut (un serveur applicatif, sans filtre de nœud)
_DEFAULT = Target()
FEATURES = _DEFAULT.features
NAMES = _DEFAULT.names
MIN_EFFECTS = _DEFAULT.min_effects
DIRECTIONS = _DEFAULT.directions
CRITICAL = _DEFAULT.critical
FRESHNESS = _DEFAULT.freshness
