"""Métriques au format texte Prometheus (sans dépendance externe)."""
import threading


def _escape(v):
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class Metrics:
    HELP = {
        "pfe_agent_info": ("gauge", "Version et mode de l'agent"),
        "pfe_agent_mode": ("gauge", "1 = actif (exécute), 0 = conseil (simule)"),
        "pfe_agent_suspended": ("gauge", "1 si l'agent s'est suspendu (disjoncteur)"),
        "pfe_agent_decisions_total": ("counter", "Décisions prises, par alerte et par issue"),
        "pfe_agent_actions_total": ("counter", "Actions exécutées ou simulées, par résultat"),
        "pfe_agent_last_action_timestamp_seconds": ("gauge", "Horodatage de la dernière action par nœud, action et résultat"),
        "pfe_agent_verifications_total": ("counter", "Vérifications après action : resolu, persistant, inconnu"),
        "pfe_agent_node_reachable": ("gauge", "1 si l'agent joint le nœud en SSH (test périodique)"),
        "pfe_agent_webhooks_total": ("counter", "Notifications reçues d'Alertmanager"),
        "pfe_agent_worker_heartbeat_timestamp_seconds": ("gauge", "Dernier tour de boucle du travailleur"),
    }

    def __init__(self):
        self._lock = threading.Lock()
        self._values = {}

    def set(self, name, value, **labels):
        with self._lock:
            self._values[(name, tuple(sorted(labels.items())))] = float(value)

    def inc(self, name, amount=1.0, **labels):
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def get(self, name, **labels):
        return self._values.get((name, tuple(sorted(labels.items()))), 0.0)

    def render(self):
        with self._lock:
            items = sorted(self._values.items())
        lines, seen = [], set()
        for (name, labels), value in items:
            if name not in seen:
                kind, text = self.HELP.get(name, ("gauge", name))
                lines += [f"# HELP {name} {text}", f"# TYPE {name} {kind}"]
                seen.add(name)
            lab = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
            num = repr(float(value))   # précision complète : un horodatage ne doit jamais être arrondi
            lines.append(f"{name}{{{lab}}} {num}" if lab else f"{name} {num}")
        return "\n".join(lines) + "\n"
