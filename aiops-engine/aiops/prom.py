"""Client minimal de l'API HTTP Prometheus."""
import math

import requests


class PromClient:
    def __init__(self, base_url, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def instant(self, expr, at=None):
        params = {"query": expr}
        if at is not None:
            params["time"] = at
        r = self.session.get(f"{self.base_url}/api/v1/query", params=params, timeout=self.timeout)
        r.raise_for_status()
        result = r.json()["data"]["result"]
        return float(result[0]["value"][1]) if result else math.nan

    def range(self, expr, start, end, step):
        r = self.session.get(f"{self.base_url}/api/v1/query_range",
                             params={"query": expr, "start": start, "end": end, "step": step},
                             timeout=max(self.timeout, 60))
        r.raise_for_status()
        result = r.json()["data"]["result"]
        return {int(float(t)): float(v) for t, v in result[0]["values"]} if result else {}
