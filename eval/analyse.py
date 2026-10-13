"""Analyse de la campagne : précision, rappel, délais de détection, décisions et réparations de l'agent.

    python eval/analyse.py     ->  eval/resultats/RESULTATS.md et eval/resultats/injections.csv

Définitions
- Fenêtre d'une injection : du début de la panne à sa fin + 10 min (retour au calme).
- Épisode d'anomalie : période continue où aiops_anomaly_active vaut 1 sur un nœud (pas de 15 s).
- Vrai positif : épisode qui commence dans la fenêtre d'une injection du même nœud ; sinon faux positif
  (y compris pendant les périodes calmes entre deux pannes et hors campagne si --depuis est donné).
- Rappel d'un détecteur : injections détectées / injections où une détection est attendue.
- Décision de l'agent correcte : l'action choisie (même si un garde-fou l'a bloquée) est l'action attendue.
"""
import argparse
import csv
import json
import os
import shlex
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from campagne import MON, RESULTATS, S, signaux, ssh  # noqa: E402

MARGE = 600
PAS = 15


def plage(expr, debut, fin, pas=PAS):
    q = " ".join("--data-urlencode " + shlex.quote(f"{k}={v}") for k, v in
                 (("query", expr), ("start", debut), ("end", fin), ("step", pas)))
    rc, out = ssh(MON, f"curl -s -G localhost:9090/api/v1/query_range {q}")
    return json.loads(out)["data"]["result"]


def episodes(serie):
    """[(début, fin)] des périodes à 1, à partir des points (t, valeur) ; un trou de données ferme l'épisode."""
    eps, debut, prec = [], None, None
    for t, v in ((float(t), float(v)) for t, v in serie):
        if debut is not None and t - prec > 3 * PAS:
            eps.append((debut, prec))
            debut = None
        if v >= 1 and debut is None:
            debut = t
        elif v < 1 and debut is not None:
            eps.append((debut, t))
            debut = None
        prec = t
    if debut is not None:
        eps.append((debut, prec))
    return eps


def detections_precises(i):
    """Premier instant de chaque signal, relu dans l'historique Prometheus (pas de 5 s) : rien n'échappe
    au relevé, même une alerte éteinte en quelques secondes par l'agent. Repli : relevé de la campagne."""
    sc = S.get(i["scenario"])
    if sc is None:
        return i["detections"]
    res = {}
    for nom, expr in signaux(sc.noeud, sc.alerte).items():
        try:
            series = plage(expr, i["t0"], i["t_fin_panne"] + 120, pas=5)
        except Exception:
            if nom in i["detections"]:
                res[nom] = i["detections"][nom]
            continue
        ts = [float(t) for s in series for t, _ in s["values"]]
        if ts:
            res[nom] = round(min(ts) - i["t0"], 1)
    return res


def hm(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M")


def med(xs):
    return f"{statistics.median(xs):.0f} s" if xs else "—"


def pct(a, b):
    return f"{100 * a / b:.0f} % ({a}/{b})" if b else "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fichier", default=os.path.join(RESULTATS, "injections.jsonl"))
    a = ap.parse_args()
    inj = [json.loads(l) for l in open(a.fichier, encoding="utf-8") if l.strip()]
    if not inj:
        sys.exit("aucune injection enregistrée")
    for i in inj:
        i["detections_releve"] = i["detections"]
        i["detections"] = detections_precises(i)
    debut = min(i["t0"] for i in inj) - 60
    fin = max(i["t_fin_panne"] for i in inj) + MARGE
    duree_h = (fin - debut) / 3600

    # --- épisodes d'anomalie du moteur et classement vrai / faux positif
    tp_ep, fp_ep, fp_liste = 0, 0, []
    for nom_det, metrique in (("ia", "aiops_anomaly_active"),):
        for s in plage(f'{metrique}', debut, fin):
            node = s["metric"].get("node")
            for (d, f) in episodes(s["values"]):
                lie = any(i["noeud"] == node and i["t0"] - PAS <= d <= i["t_fin_panne"] + MARGE for i in inj)
                if lie:
                    tp_ep += 1
                else:
                    fp_ep += 1
                    fp_liste.append(f"{node} {hm(d)}–{hm(f)}")

    # --- rappel et délais par détecteur
    attendues = [i for i in inj if i["ia_attendue"]]
    lignes_det = []
    for det, libelle in (("ia", "Moteur AIOps (z-score ou Isolation Forest)"), ("zscore", "z-score robuste seul"),
                         ("iforest", "Isolation Forest seul"), ("alerte_ia", "Alerte AnomalieAIOps (après 1 min)"),
                         ("seuil", "Seuils fixes (CPU > 80 %, RAM > 85 %, disque > 85 %)")):
        vus = [i["detections"][det] for i in attendues if det in i["detections"]]
        lignes_det.append(f"| {libelle} | {pct(len(vus), len(attendues))} | {med(vus)} | "
                          f"{(f'{min(vus):.0f}–{max(vus):.0f} s') if vus else '—'} |")
    dispo = [i for i in inj if S.get(i["scenario"]) and S[i["scenario"]].alerte]
    vus = [i["detections"]["alerte"] for i in dispo if "alerte" in i["detections"]]
    lignes_det.append(f"| Alertes de disponibilité (collecteur, nœud, moteur muets) | {pct(len(vus), len(dispo))} | "
                      f"{med(vus)} | {(f'{min(vus):.0f}–{max(vus):.0f} s') if vus else '—'} |")
    var_ok = sum(1 for i in attendues if i.get("variable_ia") in i["variables_attendues"])
    var_vus = sum(1 for i in attendues if i.get("variable_ia"))

    # --- agent
    agent_lignes, correct, executees, reparees, mttr = [], 0, 0, 0, []
    for i in inj:
        dec = [r for r in i["agent"] if r.get("type") == "decision"]
        actions = [r["action"] for r in dec]
        ok = (i["action_attendue"] in actions) if i["action_attendue"] != "aucune" else \
             all(x == "aucune" for x in actions)
        correct += ok
        execu = [r for r in dec if r.get("decision") == "executee"]
        executees += bool(execu)
        if i.get("repare_par_agent_s") is not None and execu:
            reparees += 1
            mttr.append(i["repare_par_agent_s"])
        verif = [r["verification"] for r in i["agent"] if r.get("type") == "verification"]
        agent_lignes.append(
            f"| {i['numero']} | {i['nom']} | {hm(i['t0'])} | "
            + " / ".join(f"{k} {v:.0f} s" for k, v in sorted(i["detections"].items(), key=lambda kv: kv[1]))
            + f" | {i.get('variable_ia') or '—'} | {i['action_attendue']} | "
            + (", ".join(f"{r['action']} ({r['decision']})" for r in dec) or "aucune décision")
            + f" | {'oui' if ok else 'NON'} | "
            + (f"{i['repare_par_agent_s']:.0f} s" if i.get("repare_par_agent_s") is not None else "—")
            + f" | {', '.join(verif) or '—'} |")
    reaction = []
    for i in inj:
        alerte = min([v for k, v in i["detections"].items() if k in ("alerte", "alerte_ia", "seuil")], default=None)
        if alerte is not None and i.get("repare_par_agent_s") is not None:
            reaction.append(max(0.0, i["repare_par_agent_s"] - alerte))
    reparables = [i for i in inj if i["action_attendue"] not in ("aucune",) and i["scenario"] not in ("cpu_hote", "ram_hote")]

    # --- rapport
    precision = pct(tp_ep, tp_ep + fp_ep)
    md = f"""# Résultats de la campagne d'injection de pannes

Campagne du {datetime.fromtimestamp(debut, timezone.utc):%d/%m/%Y} de {hm(debut)} à {hm(fin)} UTC ({duree_h:.1f} h),
{len(inj)} injections réelles sur les VPS (script `eval/campagne.py`, données brutes `injections.jsonl`).
Le moteur AIOps n'était **pas encore calibré** (modèle appris sur quelques heures d'historique) : ces chiffres sont
une base de départ, à refaire après la calibration (`aiops.calibrate`).

## Détection

| Détecteur | Rappel (pannes détectées) | Délai médian | Délai min–max |
|---|---|---|---|
{chr(10).join(lignes_det)}

- **Précision du moteur AIOps** (épisodes d'anomalie liés à une panne réelle) : {precision}.
  Faux positifs : {fp_ep} en {duree_h:.1f} h, soit {fp_ep / duree_h:.2f} par heure{(' — ' + ', '.join(fp_liste)) if fp_liste else ''}.
- **Diagnostic** : la variable désignée par l'IA est la bonne dans {pct(var_ok, var_vus)} des détections.

## Agent de remédiation

- Décisions correctes (action choisie = action attendue) : {pct(correct, len(inj))}.
- Pannes réparables par l'agent effectivement réparées par lui : {pct(reparees, len(reparables))}.
- Délai de rétablissement (début de la panne → panne disparue) : médiane {med(mttr)}.
- Réaction de l'agent (alerte déclenchée → panne disparue) : médiane {med(reaction)}.

## Détail des injections

| # | Panne | Heure (UTC) | Détections (délai) | Variable IA | Action attendue | Décisions de l'agent | Correct | Réparée en | Vérification 5 min après |
|---|---|---|---|---|---|---|---|---|---|
{chr(10).join(agent_lignes)}

## Lecture

- Rappel : part des pannes vues par le détecteur, pendant la panne. Délai : depuis l'injection.
- Les pannes de collecteurs (cAdvisor, node-exporter, moteur) sont vues par les alertes de disponibilité, pas par
  le modèle (le modèle ne voit justement plus les données) : elles sont exclues du rappel de l'IA.
- Pour les surcharges du serveur (CPU, RAM), l'action attendue est l'arrêt de SonarQube ; il était déjà arrêté :
  l'agent répond « rien à faire », ce qui est correct mais ne répare pas (escalade humaine).
"""
    os.makedirs(RESULTATS, exist_ok=True)
    with open(os.path.join(RESULTATS, "RESULTATS.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    with open(os.path.join(RESULTATS, "injections.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["numero", "scenario", "debut_utc", "duree_panne_s", "ia_s", "zscore_s", "iforest_s", "alerte_ia_s",
                    "seuil_s", "alerte_dispo_s", "variable_ia", "action_attendue", "actions_agent", "repare_s", "calme_s"])
        for i in inj:
            d = i["detections"]
            w.writerow([i["numero"], i["scenario"], hm(i["t0"]), i["duree_panne"], d.get("ia"), d.get("zscore"),
                        d.get("iforest"), d.get("alerte_ia"), d.get("seuil"), d.get("alerte"), i.get("variable_ia"),
                        i["action_attendue"], ";".join(f"{r['action']}:{r['decision']}" for r in i["agent"] if r.get("type") == "decision"),
                        i.get("repare_par_agent_s"), i.get("retour_calme_s")])
    print(md)


if __name__ == "__main__":
    main()
