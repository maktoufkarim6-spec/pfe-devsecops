"""Cœur de l'agent : alerte -> diagnostic -> politique -> garde-fous -> action -> vérification -> journal.

Un seul travailleur traite les alertes une par une (file d'attente) : jamais deux actions simultanées.
"""
import logging
import queue
import time
import uuid
from datetime import datetime

from .executor import OUTCOMES
from .policy import decide

log = logging.getLogger("agent")


class Agent:
    def __init__(self, cfg, executor, context, guards, journal, metrics, clock=time.time):
        self.cfg = cfg
        self.executor = executor
        self.context = context
        self.guards = guards
        self.journal = journal
        self.metrics = metrics
        self.clock = clock
        self.queue = queue.Queue(maxsize=500)
        self.pending = []            # vérifications à faire : (échéance, id, alerte, nœud, action)
        self.seen = {}               # incident:décision sans action -> horodatage (évite les doublons du journal)
        self.last_probe = 0.0
        metrics.set("pfe_agent_mode", 1 if cfg.mode == "actif" else 0)
        metrics.set("pfe_agent_suspended", 1 if guards.suspended else 0)
        self._restore_last_actions()

    def _restore_last_actions(self):
        """Après un redémarrage, la dernière action de chaque nœud reste visible (relue dans le journal)."""
        for r in self.journal.tail(1000):
            if r.get("type") != "decision" or r.get("decision") not in ("executee", "simulee"):
                continue
            try:
                t = datetime.strptime(r["horodatage"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
                code = int(r["code"]) if r.get("code") else None
            except (ValueError, KeyError):
                continue
            outcome = "simulee" if r["decision"] == "simulee" else OUTCOMES.get(code, "echec")
            self.metrics.set("pfe_agent_last_action_timestamp_seconds", t,
                             node=r["noeud"], action=r["action"], outcome=outcome)

    # ------------------------------------------------------------ entrée
    def submit(self, payload):
        """Alertes d'une notification Alertmanager. Renvoie le nombre d'alertes acceptées."""
        n = 0
        for alert in payload.get("alerts", []):
            if alert.get("status") == "firing":
                try:
                    self.queue.put_nowait(alert)
                    n += 1
                except queue.Full:
                    log.error("File d'attente pleine : alerte ignorée")
        return n

    # ------------------------------------------------------------ traitement
    def _log(self, kind, alert_name, plan, decision, detail, **extra):
        self.metrics.inc("pfe_agent_decisions_total", alertname=alert_name, node=plan.node,
                         action=plan.label, decision=decision)
        self.journal.write(type=kind, mode=self.cfg.mode, alerte=alert_name, noeud=plan.node,
                           action=plan.label, decision=decision, detail=detail, **extra)
        log.info("[%s] %s -> %s : %s (%s)", plan.node, alert_name, plan.label, decision, detail)

    def handle(self, alert):
        labels = alert.get("labels", {})
        name = labels.get("alertname", "?")
        incident = alert.get("fingerprint") or f"{name}:{labels.get('node', '')}"
        incident += ":" + alert.get("startsAt", "")
        plan = decide(alert, self.cfg, self.context.top_feature)

        if plan.action is None:
            self._once(incident, "aucune_action", lambda: self._log("decision", name, plan, "aucune_action", plan.reason))
            return
        maintenance = self.context.in_maintenance(plan.node)
        if maintenance is None:
            self._log("decision", name, plan, "contexte_indisponible",
                      "Prometheus ne répond pas : impossible de vérifier la maintenance, abstention")
            return
        if maintenance:
            self._once(incident, "maintenance", lambda: self._log(
                "decision", name, plan, "maintenance", "fenêtre de maintenance annoncée : aucune action"))
            return
        blocked = self.guards.check(plan, incident)
        if blocked:
            self._log("decision", name, plan, blocked[0], blocked[1])
            self.metrics.set("pfe_agent_suspended", 1 if self.guards.suspended else 0)
            return

        action_id = uuid.uuid4().hex[:8]
        self.guards.record(plan, incident)
        node = self.cfg.node(plan.node)
        if self.cfg.mode != "actif":
            self._log("decision", name, plan, "simulee", f"mode conseil : {plan.reason}", id=action_id)
            self._count_action(plan, "simulee")
            self._schedule(action_id, name, plan)
            return

        result = self.executor.execute(node, plan.action, plan.arg)
        self._log("decision", name, plan, "executee", f"{plan.reason} | {result.output}", id=action_id,
                  code=result.code, duree_s=f"{result.duration:.1f}")
        self._count_action(plan, result.outcome)
        self.guards.record_result(result.outcome != "echec")
        self.metrics.set("pfe_agent_suspended", 1 if self.guards.suspended else 0)
        if result.outcome == "succes":
            self._schedule(action_id, name, plan)

    def _once(self, incident, decision, fn):
        key = f"{incident}:{decision}"
        now = self.clock()
        self.seen = {k: t for k, t in self.seen.items() if now - t < 86400}
        if key not in self.seen:
            self.seen[key] = now
            fn()

    def _count_action(self, plan, outcome):
        self.metrics.inc("pfe_agent_actions_total", node=plan.node, action=plan.label, outcome=outcome)
        self.metrics.set("pfe_agent_last_action_timestamp_seconds", self.clock(),
                         node=plan.node, action=plan.label, outcome=outcome)

    def _schedule(self, action_id, alert_name, plan):
        self.pending.append((self.clock() + self.cfg.verify_delay_seconds, action_id, alert_name, plan))

    # ------------------------------------------------------------ tâches périodiques
    def verify_due(self):
        """Après une action, l'alerte a-t-elle disparu ? (en mode conseil : s'est-elle résolue seule ?)"""
        now = self.clock()
        due = [p for p in self.pending if p[0] <= now]
        self.pending = [p for p in self.pending if p[0] > now]
        for _, action_id, alert_name, plan in due:
            firing = self.context.still_firing(alert_name, plan.node)
            result = "inconnu" if firing is None else ("persistant" if firing else "resolu")
            self.metrics.inc("pfe_agent_verifications_total", action=plan.label, result=result)
            self.journal.write(type="verification", id=action_id, mode=self.cfg.mode, alerte=alert_name,
                               noeud=plan.node, action=plan.label, verification=result,
                               detail=f"{self.cfg.verify_delay_seconds // 60} min après l'action")
            log.info("[%s] vérification %s (%s) : %s", plan.node, action_id, plan.label, result)

    def probe(self, force=False):
        """Test de la chaîne SSH -> pfe-runbook sur chaque nœud (action « status », sans effet)."""
        now = self.clock()
        if not force and now - self.last_probe < self.cfg.probe_interval_seconds:
            return
        self.last_probe = now
        for node in self.cfg.nodes:
            res = self.executor.execute(node, "status")
            self.metrics.set("pfe_agent_node_reachable", 1 if res.code == 0 else 0, node=node.name)
            if res.code != 0:
                log.warning("[%s] nœud injoignable par l'agent : %s", node.name, res.output)

    def run_forever(self, stop=None):
        while stop is None or not stop.is_set():
            try:
                alert = self.queue.get(timeout=5)
            except queue.Empty:
                alert = None
            try:
                if alert is not None:
                    self.handle(alert)
                self.verify_due()
                self.probe()
            except Exception:   # une erreur sur une alerte ne doit jamais arrêter l'agent
                log.exception("Erreur pendant le traitement")
            self.metrics.set("pfe_agent_worker_heartbeat_timestamp_seconds", self.clock())
