"""Journal des décisions de l'agent (CSV, une ligne par décision ou vérification)."""
import csv
import os
import time

FIELDS = ["horodatage", "id", "type", "mode", "alerte", "noeud", "action", "decision", "detail",
          "code", "duree_s", "verification"]


class Journal:
    def __init__(self, data_dir, max_bytes=20_000_000, clock=time.time):
        self.path = os.path.join(data_dir, "actions.csv")
        self.max_bytes = max_bytes
        self.clock = clock

    def write(self, **row):
        if os.path.exists(self.path) and os.path.getsize(self.path) > self.max_bytes:
            os.replace(self.path, self.path + ".1")
        new = not os.path.exists(self.path)
        row.setdefault("horodatage", time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.clock())))
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow({k: str(row.get(k, "")).replace("\n", " ")[:500] for k in FIELDS})

    def tail(self, n=20):
        if not os.path.exists(self.path):
            return []
        with open(self.path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))[-n:]
