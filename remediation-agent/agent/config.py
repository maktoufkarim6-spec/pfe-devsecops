"""Configuration par variables d'environnement (valeurs par défaut documentées)."""
import json
import os
import re
from dataclasses import dataclass, field

MODES = ("conseil", "actif")
NODE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


@dataclass(frozen=True)
class Node:
    name: str
    address: str        # adresse SSH (IP, ou host.docker.internal pour le serveur de supervision)
    app: bool = False   # héberge l'application k3s
    monitoring: bool = False


@dataclass
class Config:
    mode: str = "conseil"                 # conseil : simule et journalise ; actif : exécute
    prom_url: str = "http://mon-prometheus:9090"
    listen_port: int = 9095
    data_dir: str = "/data"
    token_file: str = "/secrets/webhook-token"
    ssh_key: str = "/secrets/id_ed25519"
    known_hosts: str = "/config/known_hosts"
    ssh_user: str = "pfe-agent"
    ssh_timeout: int = 240                # s, couvre un redémarrage progressif de pod (180 s max)
    cooldown_minutes: float = 30          # délai entre deux actions identiques sur un même nœud
    max_actions_per_hour: int = 3         # toutes actions confondues (fenêtre glissante)
    max_attempts_per_incident: int = 2    # au-delà : escalade humaine
    failure_breaker: int = 3              # échecs consécutifs avant auto-suspension
    verify_delay_seconds: int = 300       # vérification de l'effet après une action
    probe_interval_seconds: int = 300     # test de joignabilité SSH des nœuds
    disk_cleanup: bool = False            # nettoyage disque : accord explicite du propriétaire requis
    app_api: str = "server"               # déploiement de l'API de l'application (redémarrage progressif)
    nodes: list = field(default_factory=list)

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        cfg = cls()
        for name, cast in (("mode", str), ("prom_url", str), ("app_api", str), ("listen_port", int), ("data_dir", str),
                           ("token_file", str), ("ssh_key", str), ("known_hosts", str), ("ssh_user", str),
                           ("ssh_timeout", int), ("cooldown_minutes", float), ("max_actions_per_hour", int),
                           ("max_attempts_per_incident", int), ("failure_breaker", int),
                           ("verify_delay_seconds", int), ("probe_interval_seconds", int)):
            raw = env.get(f"AGENT_{name.upper()}")
            if raw is not None:
                setattr(cfg, name, cast(raw))
        cfg.disk_cleanup = env.get("AGENT_DISK_CLEANUP", "false").lower() in ("1", "true", "oui", "yes")
        cfg.nodes = parse_nodes(env.get("AGENT_NODES", "[]"))
        cfg.validate()
        return cfg

    def validate(self):
        if self.mode not in MODES:
            raise ValueError(f"AGENT_MODE doit valoir {' ou '.join(MODES)} (reçu : {self.mode!r})")
        if not re.match(r"^[a-z0-9]([-a-z0-9]{0,38}[a-z0-9])?$", self.app_api):
            raise ValueError(f"AGENT_APP_API invalide : {self.app_api!r} (nom de déploiement Kubernetes)")
        if self.max_actions_per_hour < 1 or self.failure_breaker < 1 or self.max_attempts_per_incident < 1:
            raise ValueError("Les limites de l'agent doivent être >= 1")

    def node(self, name):
        return next((n for n in self.nodes if n.name == name), None)

    def monitoring_node(self):
        return next((n for n in self.nodes if n.monitoring), None)


def parse_nodes(raw):
    """AGENT_NODES = [{"node": "app-1", "address": "1.2.3.4", "app": true, "monitoring": false}, ...]"""
    nodes = []
    for item in json.loads(raw or "[]"):
        name = str(item["node"])
        if not NODE_NAME.match(name):
            raise ValueError(f"Nom de nœud invalide : {name!r}")
        nodes.append(Node(name, str(item["address"]), bool(item.get("app", False)),
                          bool(item.get("monitoring", False))))
    if len({n.name for n in nodes}) != len(nodes):
        raise ValueError("Nœuds en double dans AGENT_NODES")
    return nodes
