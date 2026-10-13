"""Client Prometheus minimal (bibliothèque standard seulement)."""
import json
import urllib.parse
import urllib.request


class PromError(Exception):
    pass


class PromClient:
    def __init__(self, url, timeout=10):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def query(self, expr):
        """Résultat instantané : liste de (étiquettes, valeur)."""
        url = f"{self.url}/api/v1/query?{urllib.parse.urlencode({'query': expr})}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:
                body = json.load(r)
        except (OSError, ValueError) as exc:
            raise PromError(f"Prometheus injoignable : {exc}") from exc
        if body.get("status") != "success":
            raise PromError(f"Requête refusée : {body.get('error', body)}")
        return [(s["metric"], float(s["value"][1])) for s in body["data"]["result"]]


class Context:
    """Questions que l'agent pose à Prometheus avant d'agir."""

    def __init__(self, prom):
        self.prom = prom

    def top_feature(self, node):
        """(variable la plus déviante, z-score signé) selon le moteur AIOps, ou None."""
        try:
            res = self.prom.query(f'max by (feature) (aiops_top_feature{{node="{node}"}} == 1)')
            if not res:
                return None
            feature = res[0][0].get("feature")
            z = self.prom.query(f'aiops_zscore{{node="{node}",feature="{feature}"}}')
        except PromError:
            return None
        return feature, (z[0][1] if z else None)

    def in_maintenance(self, node):
        """True/False, ou None si Prometheus ne peut pas répondre (l'agent s'abstient alors)."""
        try:
            announced = self.prom.query(f'pfe_maintenance_until_seconds{{node="{node}"}} > time()')
            engine = self.prom.query(f'aiops_maintenance{{node="{node}"}} == 1')
        except PromError:
            return None
        return bool(announced or engine)

    def still_firing(self, alertname, node):
        """True/False, ou None si inconnu."""
        selector = f'alertname="{alertname}",alertstate="firing"'
        if node and alertname != "MoteurAIOpsArrete":
            selector += f',node="{node}"'
        try:
            return bool(self.prom.query(f"ALERTS{{{selector}}}"))
        except PromError:
            return None
