"""Calibration de la couche décision sur les données réelles du serveur.

Principe : les paramètres (lambda, k/n, seuil z) ne sont plus choisis à la main.
On rejoue le journal réel (detections.csv) à travers une grille de paramètres et
on retient le réglage qui MINIMISE les alertes/jour observées, SOUS CONTRAINTE
que les pannes synthétiques (surcharge CPU, fuite mémoire, crash) restent
détectées dans les délais du cahier des charges.

Usage (dans le conteneur) :
    python -m aiops.calibrate /data/detections.csv            # tableau seulement
    python -m aiops.calibrate /data/detections.csv --apply    # écrit /data/tuning.json
"""
import argparse
import csv
import itertools
import json
import os

import numpy as np

from .detector import HysteresisDetector
from .features import MIN_EFFECTS, NAMES
from .model import AnomalyModel
from .synthetic import inject, normal

CYCLE = 15
GRID = {
    "ewma_lambda": [0.2, 0.3, 0.4],
    "kn": [(3, 4), (4, 6), (5, 8)],
    "z_high": [3.5, 4.0, 4.5, 5.0],
}
# Contraintes (cahier des charges : détection < 2 min ; fuite lente : < 10 min)
MAX_DELAY = {"surcharge_cpu": 120, "fuite_memoire_server": 600, "crash_postgres": 120}
GRACE = 8


def replay(series, high, low, k, n, lam):
    det = HysteresisDetector(high, low, k, n, lam)
    return [i for i, v in enumerate(series) if det.update(v).opened]


def load_real_z(path):
    epochs, z = [], []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                epochs.append(float(row["epoch"]))
                z.append(float(row["zscore_max"]))
            except (KeyError, ValueError):
                continue
    if len(z) < 100:
        raise SystemExit(f"Journal trop court ({len(z)} cycles) : laisser tourner au moins quelques heures.")
    return np.array(epochs), np.array(z)


def synthetic_streams(seed=7):
    """Modèle + flux de pannes : les scores ne dépendent pas de la grille, calculés une fois."""
    rng = np.random.default_rng(seed)
    model = AnomalyModel(NAMES, min_effects=MIN_EFFECTS).fit(normal(rng, 1440), np.arange(1440) * 30.0)
    stream, events = inject(normal(rng, 1100))
    z = np.max(np.abs(model.directional_z(stream)), axis=1)
    s = model.score(stream, np.zeros(len(stream)))
    relevant = model.is_relevant(stream)
    return model, z, s, relevant, events


def synthetic_delays(model, z, s, relevant, events, high, k, n, lam):
    zdet = HysteresisDetector(high, high - 1.0, k, n, lam)
    idet = HysteresisDetector(model.threshold, model.low_threshold, k, n, lam)
    votes, opens, active = [], [], False
    for i in range(len(z)):
        zs = zdet.update(z[i])
        ifs = idet.update(s[i])
        votes.append(bool(relevant[i]))
        ok = sum(votes[-n:]) >= k
        now = ifs.active and ok or zs.active
        if now and not active:
            opens.append(i)
        active = now
    delays, false_alarms, matched = {}, 0, set()
    for name, start, end in events:
        hits = [o for o in opens if start <= o <= end + GRACE]
        delays[name] = (hits[0] - start) * CYCLE if hits else None
        matched.update(hits)
    false_alarms = len([o for o in opens if o not in matched])
    return delays, false_alarms


def search(real_epochs, real_z, quiet=False):
    days = max((real_epochs[-1] - real_epochs[0]) / 86400.0, 1e-6)
    model, z, s, rel, events = synthetic_streams()
    rows = []
    for lam, (k, n), high in itertools.product(GRID["ewma_lambda"], GRID["kn"], GRID["z_high"]):
        alerts = len(replay(real_z, high, high - 1.0, k, n, lam))
        delays, fa = synthetic_delays(model, z, s, rel, events, high, k, n, lam)
        feasible = fa == 0 and all(delays[e] is not None and delays[e] <= MAX_DELAY[e] for e in MAX_DELAY)
        rows.append({"ewma_lambda": lam, "vote_k": k, "vote_n": n, "z_high": high, "z_low": high - 1.0,
                     "alerts_per_day": alerts / days, "delays": delays, "feasible": feasible})
    rows.sort(key=lambda r: (not r["feasible"], r["alerts_per_day"],
                             sum(d or 9999 for d in r["delays"].values())))
    if not quiet:
        print(f"Journal réel : {len(real_z)} cycles sur {days:.2f} j | grille : {len(rows)} réglages")
        print(f"{'faisable':9}{'alertes/j':>10}{'lambda':>8}{'k/n':>6}{'z':>5}   délais synthétiques (s)")
        for r in rows[:10]:
            d = " ".join(f"{k.split('_')[0]}={v}" for k, v in r["delays"].items())
            kn = f"{r['vote_k']}/{r['vote_n']}"
            print(f"{str(r['feasible']):9}{r['alerts_per_day']:>10.2f}{r['ewma_lambda']:>8}{kn:>6}{r['z_high']:>5}   {d}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--apply", action="store_true", help="écrit tuning.json à côté du journal")
    args = ap.parse_args()
    epochs, z = load_real_z(args.csv_path)
    rows = search(epochs, z)
    best = rows[0]
    if not best["feasible"]:
        raise SystemExit("Aucun réglage ne satisfait les contraintes de détection : calibration refusée.")
    print(f"\nRéglage retenu : lambda={best['ewma_lambda']} k/n={best['vote_k']}/{best['vote_n']} "
          f"z={best['z_high']} -> {best['alerts_per_day']:.2f} alerte(s)/jour sur le journal réel")
    if args.apply:
        out = {k: best[k] for k in ("z_high", "z_low", "ewma_lambda", "vote_k", "vote_n")}
        path = os.path.join(os.path.dirname(os.path.abspath(args.csv_path)), "tuning.json")
        with open(path, "w") as f:
            json.dump(out, f, indent=2)
        print(f"Écrit : {path} (redémarrer le conteneur pour appliquer)")


if __name__ == "__main__":
    main()
