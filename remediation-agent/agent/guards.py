"""Garde-fous de l'agent, conservés sur disque : un redémarrage de l'agent ne les remet pas à zéro."""
import json
import logging
import os
import time

log = logging.getLogger("agent")
HOUR = 3600


class Guards:
    def __init__(self, cfg, path=None, clock=time.time):
        self.cfg = cfg
        self.clock = clock
        self.path = path or os.path.join(cfg.data_dir, "state.json")
        self.last_action = {}       # clé nœud:action:argument -> horodatage
        self.recent = []            # horodatages des actions (exécutées ou simulées), fenêtre d'une heure
        self.attempts = {}          # incident -> [nombre de tentatives, horodatage]
        self.consecutive_failures = 0
        self.suspended = ""         # raison de l'auto-suspension ("" = active)
        self._load()

    # ------------------------------------------------------------ persistance
    def _load(self):
        try:
            with open(self.path) as f:
                s = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            # État illisible : on repart prudemment en suspension plutôt que sans garde-fous
            log.error("État %s illisible (%s) : agent suspendu par sécurité", self.path, exc)
            self.suspended = "état des garde-fous illisible au démarrage"
            return
        self.last_action = {k: float(v) for k, v in s.get("last_action", {}).items()}
        self.recent = [float(t) for t in s.get("recent", [])]
        self.attempts = {k: [int(v[0]), float(v[1])] for k, v in s.get("attempts", {}).items()}
        self.consecutive_failures = int(s.get("consecutive_failures", 0))
        self.suspended = str(s.get("suspended", ""))

    def save(self):
        tmp = self.path + ".tmp"
        state = {"last_action": self.last_action, "recent": self.recent, "attempts": self.attempts,
                 "consecutive_failures": self.consecutive_failures, "suspended": self.suspended}
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, self.path)

    def _prune(self, now):
        self.recent = [t for t in self.recent if now - t < HOUR]
        keep = max(self.cfg.cooldown_minutes * 60, HOUR)
        self.last_action = {k: t for k, t in self.last_action.items() if now - t < keep}
        self.attempts = {k: v for k, v in self.attempts.items() if now - v[1] < 24 * HOUR}

    # ------------------------------------------------------------ décisions
    def check(self, plan, incident):
        """Raison du blocage, ou None si l'action est autorisée."""
        now = self.clock()
        self._prune(now)
        if self.suspended:
            return "suspendu", f"agent suspendu : {self.suspended}"
        tries = self.attempts.get(incident, [0, now])[0]
        if tries >= self.cfg.max_attempts_per_incident:
            return "escalade", f"{tries} tentative(s) déjà faites pour cet incident : escalade humaine"
        last = self.last_action.get(plan.key)
        if last is not None and now - last < self.cfg.cooldown_minutes * 60:
            return "pause", f"même action il y a {(now - last) / 60:.0f} min (pause de {self.cfg.cooldown_minutes:.0f} min)"
        if len(self.recent) >= self.cfg.max_actions_per_hour:
            return "quota", f"quota atteint : {len(self.recent)} actions dans l'heure"
        return None

    def record(self, plan, incident):
        now = self.clock()
        self.last_action[plan.key] = now
        self.recent.append(now)
        tries = self.attempts.get(incident, [0, now])[0]
        self.attempts[incident] = [tries + 1, now]
        self.save()

    def record_result(self, success):
        """Disjoncteur : après N échecs consécutifs, l'agent se suspend jusqu'à intervention humaine."""
        if success:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.cfg.failure_breaker:
                self.suspended = f"{self.consecutive_failures} échecs consécutifs"
                log.error("Agent suspendu : %s", self.suspended)
        self.save()

    def resume(self):
        """Levée manuelle de la suspension (commande : python -m agent reprendre)."""
        self.suspended = ""
        self.consecutive_failures = 0
        self.save()
