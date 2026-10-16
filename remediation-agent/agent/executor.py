"""Exécution d'une action sur un nœud : SSH vers le compte pfe-agent, dont la clé est bridée côté nœud
(commande forcée pfe-runbook, adresse source imposée). L'agent ne peut donc lancer aucune autre commande."""
import re
import subprocess
import time
from dataclasses import dataclass

from .policy import ACTIONS

# Argument : un mot, ou <application>/<composant> pour rollout-app (revérifié par pfe-runbook)
ARG = re.compile(r"^[a-z0-9-]{0,40}$")
APP_ARG = re.compile(r"^[a-z0-9-]{1,32}/[a-z0-9-]{1,32}$")

# Codes de retour de pfe-runbook
OK, FAILED, USAGE, NOTHING, REFUSED = 0, 1, 2, 3, 4
OUTCOMES = {OK: "succes", NOTHING: "rien_a_faire", REFUSED: "refusee"}


@dataclass
class Result:
    code: int
    output: str
    duration: float

    @property
    def outcome(self):
        return OUTCOMES.get(self.code, "echec")


class SshExecutor:
    def __init__(self, cfg, run=subprocess.run, clock=time.monotonic):
        self.cfg = cfg
        self.run = run
        self.clock = clock

    def command(self, node, action, arg=""):
        if action not in ACTIONS or not (APP_ARG if action == "rollout-app" else ARG).match(arg or ""):
            raise ValueError(f"Action refusée par l'agent : {action!r} {arg!r}")
        return ["ssh", "-F", "/dev/null", "-i", self.cfg.ssh_key,
                "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", f"UserKnownHostsFile={self.cfg.known_hosts}", "-o", f"HostKeyAlias={node.name}",
                "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
                "-o", "IdentitiesOnly=yes", "-o", "LogLevel=ERROR",
                f"{self.cfg.ssh_user}@{node.address}", f"{action} {arg}".strip()]

    def execute(self, node, action, arg=""):
        cmd = self.command(node, action, arg)
        start = self.clock()
        try:
            p = self.run(cmd, capture_output=True, text=True, timeout=self.cfg.ssh_timeout)
            out = (p.stdout + p.stderr).strip()
            code = p.returncode
            if code == 255:   # erreur SSH elle-même (réseau, clé, empreinte) : jamais confondue avec le script
                out = f"connexion SSH impossible : {out}"
        except subprocess.TimeoutExpired:
            code, out = FAILED, f"délai de {self.cfg.ssh_timeout} s dépassé"
        except OSError as exc:
            code, out = FAILED, f"ssh introuvable ou inutilisable : {exc}"
        return Result(code, out[-200_000:], self.clock() - start)   # le journal, lui, tronque à 500 caractères
