"""Page de résultats de la campagne (HTML autonome) à partir de resultats/synthese.json.

    python eval/rapport_html.py   ->  eval/resultats/rapport.html

Chronogramme de chaque panne (injection, détection par l'IA, alerte, réparation, retour au calme), chiffres clés,
comparaison des détecteurs et décisions de l'agent. Aucune dépendance, aucun script : la page est complète au repos.
"""
import html
import json
import os
import statistics
from datetime import datetime, timezone

RESULTATS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultats")
ECHELLE = 900          # axe du chronogramme : 15 min
W, X0 = 640, 4         # largeur utile du dessin (unités SVG)

LIBELLE_DECISION = {"executee": "exécutée", "simulee": "simulée", "aucune_action": "sans action", "pause": "pause",
                    "quota": "quota", "escalade": "escalade", "maintenance": "maintenance", "suspendu": "suspendu",
                    "contexte_indisponible": "abstention"}


def e(x):
    return html.escape(str(x), quote=True)


def hm(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M")


def sec(x):
    if x is None:
        return "—"
    x = float(x)
    return f"{x:.0f} s" if x < 120 else f"{int(x // 60)} min {int(x % 60):02d} s"


def med(xs):
    return statistics.median(xs) if xs else None


def pos(t):
    return X0 + W * min(max(float(t), 0), ECHELLE) / ECHELLE


def chronogramme(i):
    d = i["detections"]
    duree = float(i.get("duree_panne") or 0)
    calme = i.get("retour_calme_s")
    repare = i.get("repare_par_agent_s")
    parts = [f'<rect class="panne" x="{pos(0):.1f}" y="13" width="{pos(duree) - pos(0):.1f}" height="10" rx="2"/>']
    if calme and float(calme) > duree:
        parts.append(f'<rect class="trainee" x="{pos(duree):.1f}" y="15" width="{pos(calme) - pos(duree):.1f}" height="6" rx="2"/>')
    marques = []
    if "ia" in d:
        marques.append(("ia", d["ia"], "IA"))
    for k in ("alerte_ia", "alerte", "seuil"):
        if k in d:
            marques.append(("alerte", d[k], "alerte" if k != "seuil" else "seuil"))
            break
    if repare is not None:
        marques.append(("repare", repare, "réparée"))
    for cls, t, lib in marques:
        x = pos(t)
        parts.append(f'<g class="m-{cls}"><line x1="{x:.1f}" y1="6" x2="{x:.1f}" y2="30"/>'
                     f'<circle cx="{x:.1f}" cy="18" r="4.5"/><title>{e(lib)} : {e(sec(t))}</title></g>')
    return (f'<svg class="chrono" viewBox="0 0 {W + 2 * X0} 36" role="img" '
            f'aria-label="Chronogramme : panne {e(sec(duree))}, ' + ", ".join(f"{lib} {sec(t)}" for _, t, lib in marques)
            + f'">{"".join(parts)}</svg>')


def axe():
    ticks = []
    for m in range(0, 16, 3):
        x = pos(m * 60)
        ticks.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="5"/><text x="{x:.1f}" y="17">{m} min</text>')
    return f'<svg class="axe" viewBox="0 0 {W + 2 * X0} 20" aria-hidden="true">{"".join(ticks)}</svg>'


def barre(vus, total):
    p = 100 * vus / total if total else 0
    return (f'<div class="jauge" role="img" aria-label="{vus} sur {total}"><span style="width:{p:.0f}%"></span></div>'
            f'<span class="num">{vus}/{total}</span>')


def main():
    s = json.load(open(os.path.join(RESULTATS, "synthese.json"), encoding="utf-8"))
    inj = s["injections"]
    ag = s["agent"]
    det = {d["id"]: d for d in s["detecteurs"]}
    ia = det.get("ia", {"vus": 0, "total": 0, "delais": []})
    prec = s["precision"]
    tot_ep = prec["vrais"] + prec["faux"]
    jour = datetime.fromtimestamp(s["debut"], timezone.utc).strftime("%d/%m/%Y")

    chiffres = [
        ("Pannes vues par l'IA", f"{ia['vus']}/{ia['total']}", f"délai médian {sec(med(ia['delais']))}"),
        ("Précision de l'IA", f"{100 * prec['vrais'] / tot_ep:.0f} %" if tot_ep else "—",
         f"{prec['faux']} fausse(s) alerte(s) en {s['duree_h']:.1f} h"),
        ("Réparées par l'agent", f"{ag['reparees']}/{ag['reparables']}", f"décisions justes : {ag['correctes']}/{ag['total']}"),
        ("Rétablissement", sec(med(ag["retablissement"])), f"dont réaction de l'agent {sec(med(ag['reaction']))}"),
    ]
    tuiles = "".join(f'<div class="tuile"><div class="t-lib">{e(a)}</div><div class="t-val">{e(b)}</div>'
                     f'<div class="t-sub">{e(c)}</div></div>' for a, b, c in chiffres)

    lignes = []
    for i in inj:
        decs = ", ".join(f"{d['action']} ({LIBELLE_DECISION.get(d['decision'], d['decision'])})" for d in i.get("decisions", []) if d["action"] != "aucune") \
            or ("aucune action (escalade humaine)" if i.get("decisions") else "aucune décision")
        etat = "ok" if i.get("correct") else "ko"
        var = i.get("variable_ia") or "—"
        var_ok = i.get("variable_ia") in (i.get("variables_attendues") or [])
        lignes.append(f'''<li class="panne-l">
  <div class="p-tete"><span class="p-num">{e(i["numero"])}</span><span class="p-nom">{e(i["nom"])}</span>
    <span class="p-heure">{e(hm(i["t0"]))} UTC · {e(i["noeud"])}</span></div>
  {chronogramme(i)}
  <dl class="p-faits">
    <div><dt>IA</dt><dd>{e(sec(i["detections"].get("ia")))}{(' · <span class="' + ('bon' if var_ok else 'neutre') + '">' + e(var) + '</span>') if var != '—' else ''}</dd></div>
    <div><dt>Alerte</dt><dd>{e(sec(i["detections"].get("alerte_ia", i["detections"].get("alerte", i["detections"].get("seuil")))))}</dd></div>
    <div><dt>Agent</dt><dd><span class="pastille {etat}">{'juste' if i.get("correct") else 'à revoir'}</span> {e(decs)}</dd></div>
    <div><dt>Réparée</dt><dd>{e(sec(i.get("repare_par_agent_s")))}</dd></div>
  </dl>
</li>''')

    rang_det = "".join(
        f'<tr><th scope="row">{e(d["libelle"])}</th><td class="j">{barre(d["vus"], d["total"])}</td>'
        f'<td class="num">{e(sec(med(d["delais"])))}</td>'
        f'<td class="num">{e((sec(min(d["delais"])) + " – " + sec(max(d["delais"]))) if d["delais"] else "—")}</td></tr>'
        for d in s["detecteurs"])
    fp = ", ".join(prec["faux_liste"]) or "aucune"

    page = f'''<title>Campagne de pannes PFE</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;700;800&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
/* Mise en page : rapport d'essai d'exploitation, chiffres clés puis chronogramme de chaque panne sur un axe commun de 15 min */
:root {{
  --fond: #f2f4f3; --papier: #fbfcfb; --encre: #17211e; --doux: #5b6863; --trait: #d5dcd9;
  --accent: #0e7468; --alerte: #b26b12; --repare: #2b7a3d; --panne: #c9d3cf; --trainee: #e6c9a3; --ko: #b4372f;
  --f-titre: "Archivo", "Helvetica Neue", Arial, sans-serif; --f-texte: "Archivo", "Helvetica Neue", Arial, sans-serif;
  --f-data: "JetBrains Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --fond: #111816; --papier: #18211e; --encre: #e4ebe8; --doux: #9aa8a3; --trait: #2c3935;
  --accent: #3fb8a6; --alerte: #e0a249; --repare: #62c27a; --panne: #3a4a45; --trainee: #6b5332; --ko: #ef7a70; color-scheme: dark }} }}
:root[data-theme="dark"] {{
  --fond: #111816; --papier: #18211e; --encre: #e4ebe8; --doux: #9aa8a3; --trait: #2c3935;
  --accent: #3fb8a6; --alerte: #e0a249; --repare: #62c27a; --panne: #3a4a45; --trainee: #6b5332; --ko: #ef7a70; color-scheme: dark }}
body {{ background: var(--fond); color: var(--encre); font: 15px/1.55 var(--f-texte); }}
.page {{ max-width: 980px; margin: 0 auto; padding-inline: 20px; padding-block: 36px 56px; display: grid; gap: 36px; }}
h1 {{ font: 800 clamp(26px, 4vw, 38px)/1.1 var(--f-titre); margin: 0; letter-spacing: -0.01em; text-wrap: balance; }}
h2 {{ font: 700 19px/1.25 var(--f-titre); margin: 0 0 12px; text-wrap: balance; }}
.sur {{ font: 600 12px/1 var(--f-data); letter-spacing: .08em; text-transform: uppercase; color: var(--accent); margin: 0 0 10px; }}
.chapo {{ color: var(--doux); max-width: 68ch; margin: 12px 0 0; }}
.tuiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }}
.tuile {{ background: var(--papier); border: 1px solid var(--trait); border-radius: 6px; padding: 14px 16px; display: grid; gap: 4px; }}
.t-lib {{ font-size: 13px; color: var(--doux); }}
.t-val {{ font: 700 30px/1.1 var(--f-data); font-variant-numeric: tabular-nums; }}
.t-sub {{ font-size: 13px; color: var(--doux); }}
.legende {{ display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 13px; color: var(--doux); margin-bottom: 8px; }}
.legende i {{ display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }}
.legende .l-panne {{ border-radius: 2px; background: var(--panne); width: 18px; }}
.legende .l-trainee {{ border-radius: 2px; background: var(--trainee); width: 18px; }}
.pannes {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 4px; }}
.panne-l {{ background: var(--papier); border: 1px solid var(--trait); border-radius: 6px; padding: 12px 14px; display: grid; gap: 6px; min-width: 0; }}
.p-tete {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; }}
.p-num {{ font: 600 13px var(--f-data); color: var(--doux); }}
.p-nom {{ font-weight: 700; }}
.p-heure {{ font: 13px var(--f-data); color: var(--doux); margin-left: auto; }}
.chrono, .axe {{ width: 100%; height: auto; display: block; }}
.axe line {{ stroke: var(--doux); }}
.axe text {{ fill: var(--doux); font: 10px var(--f-data); text-anchor: middle; }}
.axe-l {{ padding-inline: 14px; }}
.chrono .panne {{ fill: var(--panne); }}
.chrono .trainee {{ fill: var(--trainee); }}
.chrono line {{ stroke-width: 1.5; }}
.m-ia line, .m-ia circle {{ stroke: var(--accent); fill: var(--accent); }}
.m-alerte line, .m-alerte circle {{ stroke: var(--alerte); fill: var(--alerte); }}
.m-repare line, .m-repare circle {{ stroke: var(--repare); fill: var(--repare); }}
.p-faits {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 4px 16px; margin: 0; font-size: 13px; }}
.p-faits div {{ display: flex; gap: 6px; min-width: 0; }}
.p-faits dt {{ color: var(--doux); flex: none; }}
.p-faits dd {{ margin: 0; font-family: var(--f-data); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }}
.bon {{ color: var(--repare); }} .neutre {{ color: var(--alerte); }}
.pastille {{ font: 600 11px var(--f-data); padding: 1px 6px; border-radius: 9px; border: 1px solid currentColor; }}
.pastille.ok {{ color: var(--repare); }} .pastille.ko {{ color: var(--ko); }}
.tableau {{ overflow-x: auto; background: var(--papier); border: 1px solid var(--trait); border-radius: 6px; }}
table {{ border-collapse: collapse; width: 100%; min-width: 560px; font-size: 14px; }}
th, td {{ padding: 9px 12px; border-bottom: 1px solid var(--trait); text-align: left; vertical-align: middle; }}
thead th {{ font: 600 12px var(--f-data); color: var(--doux); text-transform: uppercase; letter-spacing: .05em; }}
tbody tr:last-child th, tbody tr:last-child td {{ border-bottom: 0; }}
tbody th {{ font-weight: 500; }}
td.num, .num {{ font-family: var(--f-data); font-variant-numeric: tabular-nums; white-space: nowrap; }}
td.j {{ display: flex; align-items: center; gap: 10px; }}
.jauge {{ flex: 1; min-width: 80px; height: 8px; background: var(--trait); border-radius: 4px; overflow: hidden; }}
.jauge span {{ display: block; height: 100%; background: var(--accent); }}
.texte {{ max-width: 72ch; display: grid; gap: 10px; }}
.texte p, .texte ul {{ margin: 0; }}
.texte li + li {{ margin-top: 6px; }}
code {{ font: 13px var(--f-data); }}
</style>
<main class="page">
<header>
  <p class="sur">PFE DevSecOps · AIOps · évaluation du {e(jour)}</p>
  <h1>Campagne d'injection de pannes réelles</h1>
  <p class="chapo">{len(inj)} pannes provoquées sur les deux VPS entre {e(hm(s["debut"]))} et {e(hm(s["fin"]))} UTC, une à la fois,
  séparées par des périodes calmes. Pour chacune : quand l'IA l'a vue, quand l'alerte est partie, ce qu'a décidé
  l'agent et quand le service a été rétabli. Moteur AIOps non calibré (quelques heures d'apprentissage).</p>
</header>

<section aria-label="Chiffres clés"><div class="tuiles">{tuiles}</div></section>

<section>
  <h2>Chronogramme de chaque panne</h2>
  <div class="legende"><span><i class="l-panne"></i>panne active</span><span><i class="l-trainee"></i>anomalie encore ouverte</span>
    <span><i style="background:var(--accent)"></i>détection par l'IA</span><span><i style="background:var(--alerte)"></i>alerte</span>
    <span><i style="background:var(--repare)"></i>réparée par l'agent</span></div>
  <div class="axe-l">{axe()}</div>
  <ol class="pannes">{"".join(lignes)}</ol>
</section>

<section>
  <h2>Détecteurs comparés</h2>
  <div class="tableau"><table>
    <thead><tr><th>Détecteur</th><th>Pannes détectées</th><th>Délai médian</th><th>Min – max</th></tr></thead>
    <tbody>{rang_det}</tbody>
  </table></div>
</section>

<section class="texte">
  <h2>Ce qu'il faut retenir</h2>
  <ul>
    <li><strong>L'IA voit ce que les seuils fixes ne voient pas.</strong> Aucune panne n'a franchi les seuils classiques
      (CPU &gt; 80 %, RAM &gt; 85 %, disque &gt; 85 %) ; le moteur en a vu {det.get("ia", {}).get("vus", 0)} sur
      {det.get("ia", {}).get("total", 0)}, avec la bonne variable dans {s["diagnostic"]["justes"]} cas sur {s["diagnostic"]["total"]}.</li>
    <li><strong>Seul le z-score robuste a détecté.</strong> L'Isolation Forest, appris sur 2 h seulement puis suspendu
      après un changement de régime, n'a rien signalé : à réévaluer après 24 h d'historique et calibration.</li>
    <li>Fausses alertes de l'IA hors des pannes : {e(fp)}. Une anomalie RAM est aussi apparue 2 min après le
      redémarrage d'un collecteur (comptée dans la fenêtre de cette panne).</li>
    <li>La seule panne réparable non réparée l'a été par prudence : le quota de 3 actions par heure était atteint, en
      partie par une action sans effet. Correction faite : une action sans effet ne compte plus dans le quota.</li>
    <li>Un démarrage de SonarQube a fait libérer par le noyau le cache mémoire de l'API : sa RAM a baissé durablement et
      l'anomalie est restée ouverte 30 min, jusqu'à ce que le moteur accepte ce nouveau régime. Correction faite dans
      l'agent : une baisse n'est plus traitée comme une surcharge.</li>
    <li>Le pod client supprimé a été remis par Argo CD en une trentaine de secondes, avant même que l'IA ne le voie :
      l'auto-réparation GitOps couvre ce cas sans l'agent.</li>
  </ul>
  <p>Méthode, définitions et limites : <code>eval/README.md</code>. Données brutes : <code>eval/resultats/injections.jsonl</code>.</p>
</section>
</main>
'''
    out = os.path.join(RESULTATS, "rapport.html")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(page)
    print(out)


if __name__ == "__main__":
    main()
