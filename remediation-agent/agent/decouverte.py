"""Découverte continue des services exposés par les nœuds (ports Docker publics, NodePort et Ingress de k3s).

Chaque minute, l'agent lance l'action « decouvrir » (lecture seule) de pfe-runbook sur chaque nœud, retire les
services déjà déclarés à la main, et réécrit le fichier lu par Prometheus (file_sd), qui fait tester chaque
service par blackbox-exporter. Un nœud injoignable garde ses derniers services connus.
"""
import json
import logging
import os
import time

log = logging.getLogger("agent")


def entries(services, declares):
    """Cibles Prometheus (format file_sd) pour une liste de services découverts."""
    out = []
    for s in services:
        cible = str(s.get("cible", ""))
        if not cible or cible in declares:
            continue
        out.append({"targets": [cible],
                    "labels": {"service": str(s.get("nom", cible)),
                               "module": "http_2xx" if s.get("type") == "http" else "tcp_connect",
                               "lien": str(s.get("lien", "")), "noeud": str(s.get("noeud", "")),
                               "source": str(s.get("source", ""))}})
    return out


class Decouverte:
    def __init__(self, cfg, executor, metrics, clock=time.time):
        self.cfg = cfg
        self.executor = executor
        self.metrics = metrics
        self.clock = clock
        self.cache = {}          # nœud -> cibles connues
        self.last = 0.0
        self.written = None
        self._load()

    def _load(self):
        """Reprise après redémarrage : le fichier écrit au déploiement ou par la dernière découverte."""
        try:
            with open(self.cfg.discovery_file) as f:
                for e in json.load(f):
                    self.cache.setdefault(e["labels"].get("noeud", ""), []).append(e)
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def run(self, force=False):
        now = self.clock()
        if not force and now - self.last < self.cfg.discovery_interval_seconds:
            return
        self.last = now
        declares = set(self.cfg.services_declares)
        for node in self.cfg.nodes:
            res = self.executor.execute(node, "decouvrir")
            try:
                if res.code != 0:
                    raise ValueError(res.output[-200:])
                found = entries(json.loads(res.output).get("services", []), declares)
            except (ValueError, AttributeError) as exc:
                self.metrics.set("pfe_agent_discovery_ok", 0, node=node.name)
                log.warning("[%s] découverte impossible, services connus conservés : %s", node.name, exc)
                continue
            self.cache[node.name] = found
            self.metrics.set("pfe_agent_discovery_ok", 1, node=node.name)
            self.metrics.set("pfe_agent_services_discovered", len(found), node=node.name)
        self._write()

    def _write(self):
        content = [e for name in sorted(self.cache) for e in self.cache[name]]
        if content == self.written:
            return
        tmp = self.cfg.discovery_file + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(content, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.cfg.discovery_file)
            self.written = content
            log.info("Services découverts : %d (fichier mis à jour)", len(content))
        except OSError as exc:
            log.warning("Écriture de %s impossible : %s", self.cfg.discovery_file, exc)
