"""Évaluation hors ligne par rejeu de l'historique de production.

Complète la campagne d'injection réelle (campagne.py) par un grand nombre de pannes contrôlées :
  1. l'historique RÉEL des variables de chaque nœud est relu dans Prometheus (pas de 15 s, ~14 jours) ;
  2. validation glissante : pour chaque jour J, un modèle est appris sur les 72 h précédentes exactement
     comme le fait le moteur (train_champion_challenger, mêmes paramètres), puis le jour J est rejoué ;
  3. jour rejoué sans panne : toute ouverture d'anomalie est une fausse alerte (borne haute : les vrais
     incidents survenus en production sont comptés comme fausses alertes) ;
  4. pannes ajoutées aux mesures réelles toutes les 50 min (une à la fois, 10 min chacune), à trois
     intensités ; une panne n'est comptée que si le détecteur est calme au moment de l'injection
     (même règle que la campagne réelle) ;
  5. même chaîne de décision que le moteur (z-score robuste + Isolation Forest, EWMA, vote k/n, hystérésis,
     suppression pendant les maintenances annoncées) et même référence de seuils fixes que les alertes.

Lancement (image du moteur, réseau de la supervision, lecture seule) :
    docker run --rm --network pfe-monitoring -v $PWD/rejeu.py:/app/rejeu.py:ro -v $PWD/out:/out \
        --cpus 1 --memory 900m <image aiops-engine> python /app/rejeu.py --prom http://mon-prometheus:9090 --out /out
"""
import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

import numpy as np

sys.path.insert(0, "/app")
from aiops.detector import HysteresisDetector  # noqa: E402
from aiops.features import build_features  # noqa: E402
from aiops.model import train_champion_challenger  # noqa: E402

STEP = 15                    # cycle du moteur (s)
DAY = 86400
TRAIN_H = 72                 # fenêtre d'apprentissage du moteur
Z_HIGH, Z_LOW, K, N, LAM = 3.5, 2.5, 3, 4, 0.4
WARM = 120                   # 30 min rejouées avant chaque panne (détecteurs « chauds »)
DUREE = 40                   # 10 min de panne
GRACE = 8                    # 2 min de tolérance après la fin
ESPACE = 200                 # une panne toutes les 50 min
INTENSITES = (0.25, 0.5, 1.0)

# Caractéristiques des nœuds (mémoire en Mo, disque en Go) pour traduire les pannes de la campagne réelle
NOEUDS = {"app-1": {"app": True, "ram_mb": 7758, "disk_gb": 74},
          "monitoring": {"app": False, "ram_mb": 3825, "disk_gb": 40}}


def pannes(noeud):
    """(nom, variable, effet à intensité 1) : mêmes pannes que la campagne réelle."""
    c = NOEUDS[noeud]
    p = [("surcharge_cpu", "cpu_host", 40.0),                                  # 3 cœurs sur 4 occupés
         ("pression_memoire", "ram_host", 2048 / c["ram_mb"] * 100 if c["app"] else 1024 / c["ram_mb"] * 100),
         ("remplissage_disque", "disk_host", 6 / c["disk_gb"] * 100)]          # fichier de 6 Go
    if c["app"]:
        p += [("boucle_cpu_api", "cpu_server", 100.0),                        # un cœur dans le pod de l'API
              ("fuite_memoire_api", "ram_server_mb", 150.0),                  # +150 Mo dans le pod
              ("conteneur_arrete", "app_containers", -1.0)]                   # un conteneur en moins
    return p


# ------------------------------------------------------------------ lecture de Prometheus
def query_range(prom, expr, start, end):
    out = {}
    t = start
    while t < end:
        fin = min(end, t + 10000 * STEP)
        q = urllib.parse.urlencode({"query": expr, "start": t, "end": fin, "step": STEP})
        with urllib.request.urlopen(f"{prom}/api/v1/query_range?{q}", timeout=120) as r:
            res = json.load(r)["data"]["result"]
        for serie in res:
            for ts, v in serie["values"]:
                out[int(float(ts))] = float(v)
        t = fin + STEP
    return out


def charger(prom, noeud, jours):
    feats = build_features(noeud, NOEUDS[noeud]["app"])
    fin = int(time.time()) // STEP * STEP
    debut = fin - int(jours * DAY)
    cols = [query_range(prom, f.expr, debut, fin) for f in feats]
    maint = query_range(prom, f'max(pfe_maintenance_until_seconds{{node="{noeud}"}})', debut, fin)
    ts = sorted(set.intersection(*(set(c) for c in cols)))
    X = np.array([[c[t] for c in cols] for t in ts])
    m = np.array([maint.get(t, float("nan")) for t in ts])
    return feats, np.array(ts, dtype=float), X, m


# ------------------------------------------------------------------ chaîne de décision du moteur
def rejouer(model, X, ts, maint):
    """Rejoue une séquence ; renvoie la liste des ouvertures (indice, variable désignée, détecteurs)."""
    zdet = HysteresisDetector(Z_HIGH, Z_LOW, K, N, LAM)
    idet = HysteresisDetector(model.threshold, model.low_threshold, K, N, LAM)
    s = model.score(X, ts)
    zdir = model.directional_z(X)
    zmax = np.max(np.abs(zdir), axis=1)
    rel = model.combined_effect(X) >= model.relevance_norm
    votes, actif, ouvertures, etats = [], False, [], []
    for i in range(len(X)):
        st_i = idet.update(s[i])
        st_z = zdet.update(zmax[i])
        votes.append(bool(rel[i]))
        if_eff = st_i.active and sum(votes[-N:]) >= K and not model.if_suspended
        voulu = if_eff or st_z.active
        en_maint = math.isfinite(maint[i]) and 0 < maint[i] - ts[i] <= 31 * 60
        if voulu and not actif and en_maint:
            voulu = False
        if voulu and not actif:
            j = int(np.argmax(np.abs(zdir[i])))
            ouvertures.append((i, j, ("iforest" if if_eff else "") + ("+" if if_eff and st_z.active else "")
                               + ("zscore" if st_z.active else "")))
        actif = voulu
        etats.append((actif, if_eff, st_z.active))
    return ouvertures, etats


def seuils_fixes(feats, X):
    """Alertes de seuil de la plateforme : CPU > 80 % 2 min, RAM > 85 % 2 min, disque > 85 % 5 min."""
    idx = {f.name: k for k, f in enumerate(feats)}
    regles = [("cpu_host", 80, 8), ("ram_host", 85, 8), ("disk_host", 85, 20)]
    premier = None
    for nom, seuil, duree in regles:
        au_dessus = X[:, idx[nom]] > seuil
        run = 0
        for i, v in enumerate(au_dessus):
            run = run + 1 if v else 0
            if run >= duree:
                premier = i if premier is None else min(premier, i)
                break
    return premier


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


# ------------------------------------------------------------------ campagne rejouée
def evaluer(prom, jours, sortie):
    lignes, fausses = [], []
    rng = np.random.default_rng(2026)
    for noeud in NOEUDS:
        feats, ts, X, maint = charger(prom, noeud, jours)
        noms = [f.name for f in feats]
        lo = np.array([f.lo for f in feats])
        hi = np.array([f.hi for f in feats])
        print(f"[{noeud}] {len(ts)} cycles réels du {time.strftime('%d/%m %H:%M', time.gmtime(ts[0]))} "
              f"au {time.strftime('%d/%m %H:%M', time.gmtime(ts[-1]))}", flush=True)
        t0 = ts[0]
        nb_jours = int((ts[-1] - t0) // DAY)
        for d in range(TRAIN_H // 24, nb_jours):
            a_deb, a_fin = t0 + (d - TRAIN_H // 24) * DAY, t0 + d * DAY
            sel_a = (ts >= a_deb) & (ts < a_fin)
            sel_a &= (((ts - a_deb) // STEP) % 2 == 0)                         # pas de 30 s, comme le moteur
            sel_t = (ts >= a_fin) & (ts < a_fin + DAY)
            if sel_a.sum() < 2000 or sel_t.sum() < 2000:
                continue
            model, info = train_champion_challenger(
                X[sel_a], ts[sel_a], noms, champion=None, seasonal_min_span_hours=36, fpr_tolerance=0.005,
                z_high=Z_HIGH, percentile=99.5, min_effects=[f.min_effect for f in feats],
                directions=[f.direction for f in feats])
            Xt, tt, mt = X[sel_t], ts[sel_t], maint[sel_t]
            # (a) jour rejoué sans panne : fausses alertes
            ouv, _ = rejouer(model, Xt, tt, mt)
            heures = len(Xt) * STEP / 3600
            fausses.append({"noeud": noeud, "jour": time.strftime("%Y-%m-%d", time.gmtime(a_fin)),
                            "heures": round(heures, 2), "ouvertures": len(ouv),
                            "seuils_fixes": int(seuils_fixes(feats, Xt) is not None),
                            "variables": ",".join(sorted({noms[j] for _, j, _ in ouv}))})
            # (b) pannes ajoutées aux mesures réelles
            catalogue = pannes(noeud)
            n_slot = 0
            for debut in range(WARM, len(Xt) - DUREE - GRACE, ESPACE):
                nom, var, effet = catalogue[n_slot % len(catalogue)]
                intensite = 1.0 if var == "app_containers" else INTENSITES[(n_slot // len(catalogue)) % 3]
                n_slot += 1
                a, b = debut - WARM, debut + DUREE + GRACE
                Xw = Xt[a:b].copy()
                j = noms.index(var)
                ramp = np.ones(DUREE)
                if var in ("ram_server_mb", "cpu_host", "cpu_server"):
                    # fuite : 2 min de montée ; CPU : la variable est un rate() sur 2 min, une hausse
                    # brutale n'y apparaît que progressivement (8 cycles de 15 s)
                    ramp = np.minimum(1.0, np.arange(1, DUREE + 1) / 8)
                Xw[WARM:WARM + DUREE, j] += effet * intensite * ramp
                if var == "ram_server_mb" and "ram_host" in noms:                # la fuite pèse aussi sur l'hôte
                    Xw[WARM:WARM + DUREE, noms.index("ram_host")] += effet * intensite * ramp / NOEUDS[noeud]["ram_mb"] * 100
                if var == "cpu_server" and "cpu_host" in noms:                   # un cœur sur 4 de l'hôte
                    Xw[WARM:WARM + DUREE, noms.index("cpu_host")] += 25 * intensite * ramp
                Xw = np.clip(Xw, lo, hi)
                ouv_w, etats = rejouer(model, Xw, tt[a:b], mt[a:b])
                if etats[WARM - 1][0]:
                    continue                                                     # détecteur déjà en alerte : panne écartée
                vues = [(i, jj, det) for i, jj, det in ouv_w if WARM <= i < WARM + DUREE + GRACE]
                seuil = seuils_fixes(feats, Xw[WARM:])
                lignes.append({
                    "noeud": noeud, "jour": fausses[-1]["jour"], "panne": nom, "variable": var,
                    "intensite": intensite, "effet": round(effet * intensite, 2),
                    "detectee": int(bool(vues)), "delai_s": (vues[0][0] - WARM) * STEP if vues else "",
                    "variable_designee": noms[vues[0][1]] if vues else "",
                    "diagnostic_correct": int(bool(vues) and noms[vues[0][1]] == var),
                    "detecteurs": vues[0][2] if vues else "",
                    "seuils_fixes": int(seuil is not None and seuil < DUREE + GRACE),
                    "delai_seuil_s": seuil * STEP if seuil is not None and seuil < DUREE + GRACE else "",
                    "modele_saisonnier": int(info["seasonal"]),
                })
            print(f"[{noeud}] jour {fausses[-1]['jour']} : {fausses[-1]['ouvertures']} fausse(s) alerte(s), "
                  f"{n_slot} créneaux de panne", flush=True)

    os.makedirs(sortie, exist_ok=True)
    with open(os.path.join(sortie, "rejeu-injections.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(lignes[0]))
        w.writeheader()
        w.writerows(lignes)
    with open(os.path.join(sortie, "rejeu-jours.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fausses[0]))
        w.writeheader()
        w.writerows(fausses)
    synthese(lignes, fausses, sortie)


def synthese(lignes, fausses, sortie):
    def bloc(sel):
        n = len(sel)
        k = sum(r["detectee"] for r in sel)
        ks = sum(r["seuils_fixes"] for r in sel)
        d = sorted(r["delai_s"] for r in sel if r["detectee"])
        diag = sum(r["diagnostic_correct"] for r in sel)
        q = (lambda p: float(np.percentile(d, p)) if d else None)
        return {"n": n, "detectees": k, "rappel": wilson(k, n), "seuils_detectees": ks, "rappel_seuils": wilson(ks, n),
                "diagnostic": wilson(diag, k) if k else None,
                "delai_q1": q(25), "delai_mediane": q(50), "delai_q3": q(75),
                "discordantes_ia_seules": sum(1 for r in sel if r["detectee"] and not r["seuils_fixes"]),
                "discordantes_seuils_seuls": sum(1 for r in sel if r["seuils_fixes"] and not r["detectee"])}

    res = {"total": bloc(lignes), "nominal": bloc([r for r in lignes if r["intensite"] == 1.0]),
           "par_intensite": {str(i): bloc([r for r in lignes if r["intensite"] == i]) for i in INTENSITES},
           "par_panne": {p: bloc([r for r in lignes if r["panne"] == p and r["intensite"] == 1.0])
                         for p in sorted({r["panne"] for r in lignes})},
           "par_noeud": {n: bloc([r for r in lignes if r["noeud"] == n and r["intensite"] == 1.0])
                         for n in NOEUDS}}
    h = sum(f["heures"] for f in fausses)
    o = sum(f["ouvertures"] for f in fausses)
    res["fausses_alertes"] = {"heures": round(h, 1), "jours_noeud": len(fausses), "ouvertures": o,
                              "par_jour": round(o / (h / 24), 3) if h else None,
                              "jours_avec_seuil_fixe": sum(f["seuils_fixes"] for f in fausses)}
    with open(os.path.join(sortie, "rejeu-synthese.json"), "w") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prom", default="http://mon-prometheus:9090")
    ap.add_argument("--jours", type=float, default=14.5)
    ap.add_argument("--out", default="/out")
    a = ap.parse_args()
    evaluer(a.prom, a.jours, a.out)
