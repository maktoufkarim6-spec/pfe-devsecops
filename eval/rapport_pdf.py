"""Chapitre PDF « Évaluation par injection de pannes réelles » à partir de resultats/synthese.json.

    python eval/analyse.py && python eval/rapport_pdf.py [sortie.pdf]     (nécessite reportlab)
"""
import json
import os
import statistics
import sys
from datetime import datetime, timezone

from reportlab.graphics.shapes import Circle, Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import CondPageBreak, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

RESULTATS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultats")

# ------------------------------------------------------------------ polices (accents, œ, flèches)
POLICES = [("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeuii.ttf"),
           ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf")]
TEXTE, GRAS, ITAL = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
for r, b, i in POLICES:
    if all(os.path.exists(p) for p in (r, b, i)):
        pdfmetrics.registerFont(TTFont("Corps", r))
        pdfmetrics.registerFont(TTFont("Corps-Gras", b))
        pdfmetrics.registerFont(TTFont("Corps-Ital", i))
        pdfmetrics.registerFontFamily("Corps", normal="Corps", bold="Corps-Gras", italic="Corps-Ital")
        TEXTE, GRAS, ITAL = "Corps", "Corps-Gras", "Corps-Ital"
        break

ENCRE = colors.HexColor("#17211e")
DOUX = colors.HexColor("#5b6863")
TRAIT = colors.HexColor("#d5dcd9")
FOND = colors.HexColor("#f2f4f3")
ACCENT = colors.HexColor("#0e7468")
ALERTE = colors.HexColor("#b26b12")
REPARE = colors.HexColor("#2b7a3d")
PANNE = colors.HexColor("#c9d3cf")
TRAINEE = colors.HexColor("#e6c9a3")
GITOPS = colors.HexColor("#7d8a86")
DECISION = {"executee": "exécutée", "simulee": "simulée", "pause": "bloquée : pause", "quota": "bloquée : quota",
            "maintenance": "bloquée : maintenance", "escalade": "escalade humaine", "aucune_action": "aucune action"}

S = {
    "titre": ParagraphStyle("titre", fontName=GRAS, fontSize=20, leading=25, textColor=ENCRE, spaceAfter=4),
    "sur": ParagraphStyle("sur", fontName=GRAS, fontSize=8.5, leading=11, textColor=ACCENT, spaceAfter=6),
    "h1": ParagraphStyle("h1", fontName=GRAS, fontSize=14, leading=18, textColor=ENCRE, spaceBefore=12, spaceAfter=6),
    "h2": ParagraphStyle("h2", fontName=GRAS, fontSize=11, leading=14, textColor=ENCRE, spaceBefore=8, spaceAfter=4),
    "p": ParagraphStyle("p", fontName=TEXTE, fontSize=9.5, leading=13.5, textColor=ENCRE, alignment=TA_LEFT, spaceAfter=5),
    "puce": ParagraphStyle("puce", fontName=TEXTE, fontSize=9.5, leading=13.5, textColor=ENCRE, leftIndent=12,
                           bulletIndent=2, spaceAfter=3),
    "petit": ParagraphStyle("petit", fontName=TEXTE, fontSize=7.8, leading=10, textColor=DOUX),
    "cell": ParagraphStyle("cell", fontName=TEXTE, fontSize=8, leading=10, textColor=ENCRE),
    "cellg": ParagraphStyle("cellg", fontName=GRAS, fontSize=8, leading=10, textColor=ENCRE),
    "mini": ParagraphStyle("mini", fontName=TEXTE, fontSize=7.3, leading=9, textColor=ENCRE),
    "minig": ParagraphStyle("minig", fontName=GRAS, fontSize=7.3, leading=9, textColor=DOUX),
    "tuile_v": ParagraphStyle("tv", fontName=GRAS, fontSize=17, leading=21, textColor=ENCRE),
    "tuile_l": ParagraphStyle("tl", fontName=TEXTE, fontSize=8, leading=10, textColor=DOUX),
}


def P(t, st="p"):
    return Paragraph(t, S[st])


def puce(t):
    return Paragraph(t, S["puce"], bulletText="•")


def sec(x):
    if x is None:
        return "—"
    x = float(x)
    return f"{x:.0f} s" if x < 120 else f"{int(x // 60)} min {int(x % 60):02d} s"


def med(xs):
    return statistics.median(xs) if xs else None


def hm(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M")


def tableau(lignes, largeurs, entete=True, zebre=True):
    t = Table(lignes, colWidths=largeurs, repeatRows=1 if entete else 0)
    st = [("FONTNAME", (0, 0), (-1, -1), TEXTE), ("FONTSIZE", (0, 0), (-1, -1), 8),
          ("TEXTCOLOR", (0, 0), (-1, -1), ENCRE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("LINEBELOW", (0, 0), (-1, -1), 0.4, TRAIT), ("TOPPADDING", (0, 0), (-1, -1), 4),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 5)]
    if entete:
        st += [("FONTNAME", (0, 0), (-1, 0), GRAS), ("TEXTCOLOR", (0, 0), (-1, 0), DOUX),
               ("FONTSIZE", (0, 0), (-1, 0), 7.5), ("LINEBELOW", (0, 0), (-1, 0), 0.8, DOUX)]
    if zebre:
        for r in range(1, len(lignes)):
            if r % 2 == 0:
                st.append(("BACKGROUND", (0, r), (-1, r), FOND))
    t.setStyle(TableStyle(st))
    return t


# ------------------------------------------------------------------ chronogramme d'une panne
ECHELLE, LARG, X0 = 900.0, 118 * mm, 2


def chrono(i, haut=13):
    d = Drawing(LARG + 2 * X0, haut)
    px = lambda t: X0 + LARG * min(max(float(t), 0), ECHELLE) / ECHELLE  # noqa: E731
    duree = float(i.get("duree_panne") or 0)
    d.add(Rect(px(0), 4, px(duree) - px(0), 5, fillColor=PANNE, strokeColor=None))
    calme = i.get("retour_calme_s")
    if calme and float(calme) > duree:
        d.add(Rect(px(duree), 5, px(calme) - px(duree), 3, fillColor=TRAINEE, strokeColor=None))
    det = i["detections"]
    marques = []
    if "ia" in det:
        marques.append((det["ia"], ACCENT))
    for k in ("alerte_ia", "alerte", "seuil"):
        if k in det:
            marques.append((det[k], ALERTE))
            break
    if i.get("repare_par_agent_s") is not None:
        # Rétablissement sans l'agent (ex. Argo CD qui remet le pod) : couleur distincte
        marques.append((i["repare_par_agent_s"], GITOPS if i.get("action_attendue") == "aucune" else REPARE))
    for t, c in marques:
        d.add(Line(px(t), 1, px(t), haut - 1, strokeColor=c, strokeWidth=0.8))
        d.add(Circle(px(t), 6.5, 2.4, fillColor=c, strokeColor=None))
    return d


def axe():
    d = Drawing(LARG + 2 * X0, 12)
    for m in range(0, 16, 3):
        x = X0 + LARG * m * 60 / ECHELLE
        d.add(Line(x, 9, x, 12, strokeColor=DOUX, strokeWidth=0.5))
        d.add(String(x, 1, f"{m} min", fontName=TEXTE, fontSize=6.5, fillColor=DOUX, textAnchor="middle"))
    return d


def legende():
    d = Drawing(174 * mm, 10)
    x = 0
    for lib, c, forme in (("panne active", PANNE, "r"), ("anomalie encore ouverte", TRAINEE, "r"),
                          ("détection IA", ACCENT, "c"), ("alerte", ALERTE, "c"), ("réparée par l'agent", REPARE, "c"),
                          ("rétablie sans l'agent (Argo CD)", GITOPS, "c")):
        if forme == "r":
            d.add(Rect(x, 3, 12, 4, fillColor=c, strokeColor=None))
        else:
            d.add(Circle(x + 4, 5, 2.6, fillColor=c, strokeColor=None))
        d.add(String(x + 16, 2.5, lib, fontName=TEXTE, fontSize=7.5, fillColor=DOUX))
        x += 16 + pdfmetrics.stringWidth(lib, TEXTE, 7.5) + 8
    return d


def pied(canvas, doc):
    canvas.saveState()
    canvas.setFont(TEXTE, 7.5)
    canvas.setFillColor(DOUX)
    canvas.drawString(18 * mm, 10 * mm, "PFE DevSecOps + AIOps — Évaluation par injection de pannes réelles")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"page {doc.page}")
    canvas.restoreState()


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RESULTATS, "evaluation-pannes.pdf")
    s = json.load(open(os.path.join(RESULTATS, "synthese.json"), encoding="utf-8"))
    inj, ag, pr = s["injections"], s["agent"], s["precision"]
    det = {d["id"]: d for d in s["detecteurs"]}
    ia = det["ia"]
    tot = pr["vrais"] + pr["faux"]
    jour = datetime.fromtimestamp(s["debut"], timezone.utc).strftime("%d/%m/%Y")
    reparables = ag["reparables"]

    st = []
    st += [P("PFE DEVSECOPS + AIOPS · CHAPITRE D'ÉVALUATION", "sur"),
           P("Évaluation par injection de pannes réelles", "titre"),
           P(f"Campagne du {jour}, de {hm(s['debut'])} à {hm(s['fin'])} UTC ({s['duree_h']:.1f} h) : {len(inj)} pannes "
             "provoquées sur l'infrastructure de production du projet (deux VPS), une à la fois, séparées par des "
             "périodes calmes. Pour chaque panne : délai de détection par l'IA et par les seuils classiques, alerte, "
             "décision de l'agent de remédiation et délai de rétablissement.", "p"), Spacer(1, 6)]

    # --- chiffres clés
    tuiles = [("Pannes vues par l'IA", f"{ia['vus']}/{ia['total']}", f"délai médian {sec(med(ia['delais']))}"),
              ("Précision de l'IA", f"{100 * pr['vrais'] / tot:.0f} %" if tot else "—",
               f"{pr['faux']} fausse alerte en {s['duree_h']:.1f} h"),
              ("Réparées par l'agent", f"{ag['reparees']}/{reparables}", f"décisions justes {ag['correctes']}/{ag['total']}"),
              ("Rétablissement médian", sec(med(ag["retablissement"])), f"réaction de l'agent {sec(med(ag['reaction']))}")]
    cell = [[Table([[P(v, "tuile_v")], [P(lib, "tuile_l")], [P(sub, "petit")]], colWidths=[40 * mm],
                   style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1),
                          ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]) for lib, v, sub in tuiles]]
    t = Table(cell, colWidths=[43.5 * mm] * 4)
    t.setStyle(TableStyle([("BOX", (0, 0), (0, 0), 0.6, TRAIT), ("BOX", (1, 0), (1, 0), 0.6, TRAIT),
                           ("BOX", (2, 0), (2, 0), 0.6, TRAIT), ("BOX", (3, 0), (3, 0), 0.6, TRAIT),
                           ("BACKGROUND", (0, 0), (-1, -1), FOND), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    st += [t, Spacer(1, 8)]

    # --- 1. Protocole
    st += [P("1. Protocole", "h1"),
           P("La campagne est pilotée par <font name='Courier'>eval/campagne.py</font>. Avant chaque panne, l'état doit "
             "être calme : modèle d'IA prêt, aucune maintenance annoncée, aucune anomalie ni alerte en cours, hors créneaux "
             "des sauvegardes, et au moins 35 min depuis une panne appelant la même action (pause de 30 min de l'agent). "
             "La panne est ensuite injectée par SSH et horodatée avec l'horloge de Prometheus. Chaque détecteur est "
             "observé, la panne est réparée par l'agent ou annulée par le script à la fin de sa durée, puis le système "
             "doit revenir au calme ; une période calme de 8 min sépare deux pannes et sert à compter les fausses "
             "alertes. Les délais sont relus dans l'historique Prometheus au pas de 5 s."),
           P("Pannes injectées", "h2")]
    lignes = [["Panne", "Moyen", "Détection attendue", "Action attendue de l'agent"],
              ["Collecteur cAdvisor arrêté", "docker stop", "alerte de disponibilité", "redémarrer le collecteur"],
              ["node-exporter arrêté", "docker stop", "alerte « nœud injoignable »", "redémarrer le collecteur"],
              ["Moteur AIOps arrêté", "docker stop", "alerte « moteur arrêté »", "redémarrer le moteur"],
              ["SonarQube hors build (+2 Go)", "docker start", "IA (CPU ou RAM du serveur)", "arrêter SonarQube"],
              ["Surcharge CPU (3 cœurs sur 4)", "conteneur de calcul", "IA (cpu_host)", "arrêter SonarQube (rien à faire)"],
              ["Pression mémoire (+2 Go)", "tmpfs", "IA (ram_host)", "arrêter SonarQube (rien à faire)"],
              ["Boucle CPU dans l'API", "processus dans le pod", "IA (cpu_server)", "redémarrage progressif de l'API"],
              ["Fuite mémoire dans l'API (+150 Mo)", "processus dans le pod", "IA (ram_server_mb)", "redémarrage progressif de l'API"],
              ["Fichier de 6 Go", "fallocate", "IA (disk_host)", "aucune (nettoyage désactivé)"],
              ["Pod client à 0", "kubectl scale", "IA ; remise par Argo CD", "aucune"]]
    st.append(tableau([[P(c, "cellg" if r == 0 else "cell") for c in row] for r, row in enumerate(lignes)],
                      [48 * mm, 30 * mm, 44 * mm, 52 * mm]))
    st.append(P("Indicateurs : <b>rappel</b> = pannes détectées / pannes où le détecteur est attendu ; <b>précision</b> = "
                "épisodes d'anomalie liés à une panne / tous les épisodes ; <b>délai</b> depuis l'injection ; <b>décision "
                "correcte</b> = action choisie par l'agent égale à l'action attendue, même si un garde-fou l'a bloquée ; "
                "<b>rétablissement</b> = début de la panne → panne disparue.", "petit"))

    # --- 2. Résultats
    st += [PageBreak(), P("2. Résultats", "h1"), P("2.1 Détection", "h2")]
    lignes = [["Détecteur", "Pannes détectées", "Délai médian", "Min – max"]]
    for d in s["detecteurs"]:
        rappel = f"{100 * d['vus'] / d['total']:.0f} % ({d['vus']}/{d['total']})" if d["total"] else "—"
        mm_ = f"{sec(min(d['delais']))} – {sec(max(d['delais']))}" if d["delais"] else "—"
        lignes.append([d["libelle"], rappel, sec(med(d["delais"])), mm_])
    st.append(tableau([[P(c, "cellg" if r == 0 else "cell") for c in row] for r, row in enumerate(lignes)],
                      [78 * mm, 34 * mm, 26 * mm, 36 * mm]))
    st.append(Spacer(1, 4))
    fp = ", ".join(pr["faux_liste"]) or "aucune"
    st += [P(f"<b>Précision du moteur AIOps</b> : {100 * pr['vrais'] / tot:.0f} % ({pr['vrais']} épisodes liés à une panne "
             f"sur {tot}) ; fausses alertes hors pannes : {fp}, soit {pr['faux'] / s['duree_h']:.2f} par heure."),
           P(f"<b>Diagnostic</b> : la variable désignée par l'IA comme responsable est la bonne dans "
             f"{s['diagnostic']['justes']} cas sur {s['diagnostic']['total']}."),
           P("2.2 Agent de remédiation", "h2")]
    lignes = [["Indicateur", "Résultat"],
              ["Décisions correctes (action choisie = action attendue)", f"{ag['correctes']}/{ag['total']}"],
              ["Pannes réparables effectivement réparées par l'agent", f"{ag['reparees']}/{reparables}"],
              ["Délai de rétablissement (médiane)", sec(med(ag["retablissement"]))],
              ["Réaction de l'agent après l'alerte (médiane)", sec(med(ag["reaction"]))]]
    st.append(tableau([[P(c, "cellg" if r == 0 else "cell") for c in row] for r, row in enumerate(lignes)],
                      [120 * mm, 54 * mm]))

    st += [P("2.3 Chronogramme de chaque panne", "h2"), legende(), Spacer(1, 2)]
    entete = [P("<b>#</b>", "cell"), P("<b>Panne</b>", "cell"), axe()]
    lignes = [entete]
    for i in inj:
        lignes.append([P(str(i["numero"]), "cell"), P(i["nom"], "cell"), chrono(i)])
    t = Table(lignes, colWidths=[7 * mm, 46 * mm, 122 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, TRAIT),
                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                           ("LEFTPADDING", (0, 0), (-1, -1), 3)]))
    st.append(t)

    st += [CondPageBreak(60 * mm), P("2.4 Détail des injections", "h2"),
           P("Délais en secondes depuis l'injection. « Rétablie » : panne disparue (par l'agent, ou par Argo CD pour la "
             "panne 9).", "petit"), Spacer(1, 3)]
    lignes = [["#", "Panne", "Heure", "IA", "Alerte", "Variable IA", "Décision de l'agent", "OK", "Rétablie"]]
    s_ = lambda v: f"{float(v):.0f} s" if v is not None else "—"  # noqa: E731
    for i in inj:
        d = i["detections"]
        decs = ", ".join(f"{x['action']} ({DECISION.get(x['decision'], x['decision'])})"
                         for x in i.get("decisions", []) if x["action"] != "aucune") or "aucune action"
        alerte = d.get("alerte_ia", d.get("alerte", d.get("seuil")))
        lignes.append([str(i["numero"]), i["nom"], hm(i["t0"]), s_(d.get("ia")), s_(alerte), i.get("variable_ia") or "—",
                       decs, "oui" if i.get("correct") else "non", s_(i.get("repare_par_agent_s"))])
    t = tableau([[P(c, "minig" if r == 0 else "mini") for c in row] for r, row in enumerate(lignes)],
                [7 * mm, 37 * mm, 12 * mm, 11 * mm, 13 * mm, 22 * mm, 46 * mm, 9 * mm, 17 * mm])
    st.append(t)

    # --- 3. Discussion
    st += [P("3. Discussion", "h1")]
    for txt in [
        f"<b>L'IA détecte ce que les seuils classiques ne voient pas.</b> Aucune panne n'a franchi les seuils fixes "
        f"(CPU &gt; 80 %, RAM &gt; 85 %, disque &gt; 85 %), alors que le moteur AIOps en a vu {ia['vus']} sur {ia['total']} "
        f"en {sec(med(ia['delais']))} (médiane), en désignant la bonne variable. Seule la suppression du pod client n'a pas été "
        "vue : Argo CD l'a remis en une trentaine de secondes, avant la fenêtre d'observation du modèle.",
        "<b>Seul le z-score robuste a détecté.</b> L'Isolation Forest, appris sur deux heures d'historique puis suspendu après "
        "un changement de régime, n'a rien signalé. Sa contribution reste à mesurer après 24 h d'historique et calibration.",
        f"<b>L'agent prend des décisions justes et répare vite.</b> {ag['correctes']} décisions justes sur {ag['total']} et "
        f"{ag['reparees']} pannes réparables sur {reparables} réparées, avec un rétablissement médian de "
        f"{sec(med(ag['retablissement']))}, dont une réaction de {sec(med(ag['reaction']))} après l'alerte. Le délai est "
        "dominé par le délai de confirmation des alertes (1 à 2 min), choisi pour éviter les fausses alertes.",
        "<b>La réparation manquée l'a été par prudence.</b> Le quota de 3 actions par heure était atteint, en partie à cause "
        "d'une action sans effet déclenchée par une anomalie apparue juste après le redémarrage d'un collecteur. "
        "Correction appliquée : une action sans effet ne compte plus dans le quota.",
        "<b>Un effet de bord a été identifié et corrigé.</b> Le démarrage de SonarQube a fait libérer par le noyau le cache "
        "mémoire de l'API : sa RAM a baissé durablement et l'anomalie est restée ouverte 30 min, jusqu'à ce que le moteur "
        "accepte ce nouveau régime. L'agent ne traite plus une baisse comme une surcharge.",
    ]:
        st.append(puce(txt))

    # --- 4. Limites
    st += [P("4. Limites et suites", "h1")]
    for txt in [
        "Effectif réduit (14 pannes, 10 types) : les pourcentages sont indicatifs, pas statistiquement robustes.",
        "Modèle appris sur quelques heures, de nuit, sans calibration : refaire la campagne après 24 h calmes et "
        "<font name='Courier'>aiops.calibrate</font> pour comparer avant et après.",
        "Pannes simples et isolées : pas de pannes simultanées, pas de dégradation lente hormis le disque.",
        "Une panne a été interrompue par une coupure SSH du poste de pilotage ; elle est exclue des statistiques et sa "
        "fenêtre est seulement utilisée pour ne pas compter son anomalie comme fausse alerte.",
    ]:
        st.append(puce(txt))
    st.append(Spacer(1, 6))
    st.append(P("Données brutes et méthode : dépôt du projet, dossier <font name='Courier'>eval/</font> "
                "(campagne.py, analyse.py, resultats/injections.jsonl, README.md).", "petit"))

    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title="Évaluation par injection de pannes réelles",
                            author="PFE DevSecOps + AIOps", subject="Campagne d'injection de pannes")
    doc.build(st, onFirstPage=pied, onLaterPages=pied)
    print(out)


if __name__ == "__main__":
    main()
