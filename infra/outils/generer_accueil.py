"""Génère l'accueil Grafana simple et la page Détails (python infra/outils/generer_accueil.py).
Les marqueurs __APP_IP__, __REPO_WEB__… sont remplacés par Ansible au déploiement (group_vars/all.yml)."""
import json
import os
RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import statistics

DS = {"type": "prometheus", "uid": "efx9ykc7u57nkf"}
APP = "__APP_IP__"
JENKINS = "__JENKINS_URL__"   # http://<ip>:8080, ou https://jenkins.<domaine> avec le HTTPS
ARGOCD = "__ARGOCD_URL__"     # https://<ip>:32252, ou https://argocd.<domaine>
RAPPORT = "__EVALUATION_URL__"
DEPOT = "__REPO_WEB__"
DEPLOYER = "__JENKINS_JOB_URL__"
DEPLOYER_APPS = "__JENKINS_APPS_URL__"
DEPLOYER_UNE_APP = "__JENKINS_APPS_PARAM_URL__"   # formulaire du job commun, APPLICATION= à compléter
pid = [0]


def nid():
    pid[0] += 1
    return pid[0]


def tgt(expr, legend=None, ref="A", table=False):
    t = {"refId": ref, "datasource": DS, "expr": expr, "instant": True, "range": False}
    if table:
        t["format"] = "table"
    if legend:
        t["legendFormat"] = legend
    return t


def stat(title, x, y, w, h, targets, desc="", unit="none", steps=None, mappings=None, decimals=0, overrides=None,
         text_mode="value_and_name", links=None, color_mode="background"):
    p = {"type": "stat", "title": title, "id": nid(), "gridPos": {"h": h, "w": w, "x": x, "y": y}, "datasource": DS,
         "targets": targets, "description": desc,
         "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "mappings": mappings or [],
                                      "thresholds": {"mode": "absolute", "steps": steps or [{"color": "green", "value": None}]},
                                      "links": links or []},
                         "overrides": overrides or []},
         "options": {"colorMode": color_mode, "graphMode": "none", "textMode": text_mode, "justifyMode": "center",
                     "orientation": "auto", "wideLayout": True,
                     "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}
    return p


def row(title, y):
    return {"type": "row", "title": title, "id": nid(), "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "collapsed": False, "panels": []}


def text(title, x, y, w, h, md):
    return {"type": "text", "title": title, "id": nid(), "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "options": {"mode": "markdown", "content": md}}


def val(maps):
    return [{"type": "value", "options": {k: {"text": t, "color": c} for k, (t, c) in maps.items()}}]


def table(title, x, y, w, h, targets, desc="", garder=None, vide="", jointure=None, overrides=None, links=None):
    """Tableau Grafana : requêtes au format table (jointes sur une étiquette si besoin), colonnes de `garder`
    seulement, dans cet ordre et sous ces noms."""
    trans = []
    if jointure:
        trans.append({"id": "joinByField", "options": {"byField": jointure, "mode": "outer"}})
    garder = garder or {}
    trans.append({"id": "filterFieldsByName", "options": {"include": {"names": list(garder)}}})
    trans.append({"id": "organize", "options": {"indexByName": {k: i for i, k in enumerate(garder)},
                                                "renameByName": garder}})
    return {"type": "table", "title": title, "id": nid(), "gridPos": {"h": h, "w": w, "x": x, "y": y}, "datasource": DS,
            "targets": targets, "description": desc, "transformations": trans,
            "fieldConfig": {"defaults": {"noValue": vide, "links": links or [],
                                         "custom": {"align": "left", "cellOptions": {"type": "auto"}}},
                            "overrides": overrides or []},
            "options": {"showHeader": True, "cellHeight": "md", "footer": {"show": False}}}


# Problèmes en cours : toutes les alertes, sauf la simple notification d'une action réussie de l'agent
ALERTES_EN_COURS = 'ALERTS{alertstate="firing",alertname!="ActionAutomatique"}'


RG = [{"color": "red", "value": None}, {"color": "green", "value": 1}]
panels = []
y = 0
# ============================== Page « PFE - Détails » : tout le détail technique
panels.append(row("Alertes en cours", y))
y += 1
panels.append(table("Alertes en cours", 0, y, 24, 5, [tgt(ALERTES_EN_COURS, ref="A", table=True)],
    "Alertes actives du parc (hors simple notification d'une action de l'agent). Le bandeau de l'accueil les compte.",
    garder={"alertname": "Alerte", "severity": "Gravité", "node": "Serveur", "application": "Application", "source": "Origine"},
    vide="Rien à signaler"))
y += 5
panels.append(row("Applications de la plateforme", y))
y += 1
APPLI = 'aiops_target_info{kind="application"}'
# Application cobaye : gérée par k8s/ (espace __APP_NAMESPACE__), suivie par le modèle IA du nœud de contrôle et
# sauvegardée avec lui ; affichée ici avec les autres sous son nom (__APP_NAME__)
COB = lambda expr: f'label_replace({expr}, "application", "__APP_NAME__", "", "")'
COB_IA = ('(max(aiops_anomaly_active{node="__CTL_NODE__"}) * max(aiops_top_feature{node="__CTL_NODE__",'
          'feature=~"cpu_server|ram_server_mb|ram_postgres_mb|app_containers"})) or vector(0)')
panels.append(stat("En ligne", 0, y, 6, 4,
    [tgt('probe_success{job="applications"}', "{{application}}"),
     tgt(COB('probe_success{job="services",service="Application"}'), "{{application}}", "B")],
    "Adresse web de chaque application, testée toutes les 15 s. Cliquer pour l'ouvrir.",
    mappings=val({"1": ("en ligne", "green"), "0": ("HORS LIGNE", "red")}), steps=RG,
    links=[{"title": "Ouvrir ${__field.labels.application}", "url": "${__field.labels.instance}", "targetBlank": True}]))
panels.append(stat("Détection par l'IA", 6, y, 6, 4,
    [tgt(f'max by (application) (aiops_anomaly_active * on (node) group_left (application) {APPLI})', "{{application}}"),
     tgt(COB(COB_IA), "{{application}}", "B")],
    "Un modèle par application : CPU et RAM de chaque composant, conteneurs, disponibilité et temps de réponse. "
    "Application cobaye : modèle de son serveur, quand la variable en cause est la sienne (API, base, conteneurs).",
    mappings=val({"0": ("normal", "green"), "1": ("ANOMALIE", "orange")}), steps=[{"color": "green", "value": None}]))
panels.append(stat("Mémoire", 12, y, 6, 4,
    [tgt('sum by (application) (label_replace(container_memory_working_set_bytes{job="cadvisor-k3s",'
         'container_label_io_kubernetes_container_name!="",container_label_io_kubernetes_container_name!="POD"},'
         ' "application", "$1", "container_label_io_kubernetes_pod_namespace", "(.+)"))'
         f' and on (application) {APPLI}', "{{application}}"),
     tgt(COB('sum(container_memory_working_set_bytes{job="cadvisor-k3s",container_label_io_kubernetes_pod_namespace="__APP_NAMESPACE__",'
             'container_label_io_kubernetes_container_name!="",container_label_io_kubernetes_container_name!="POD"})'), "{{application}}", "B")],
    "Mémoire utilisée par tous les conteneurs de l'application (plafonnée par son quota).", unit="bytes", decimals=0))
panels.append(stat("Sauvegarde de la base", 18, y, 6, 4,
    [tgt('(time() - pfe_backup_app_last_success_timestamp_seconds) / 3600 and pfe_backup_app_last_success_timestamp_seconds > 0',
         "{{application}}"),
     tgt(COB('(time() - max(pfe_backup_last_success_timestamp_seconds{node="__CTL_NODE__"})) / 3600'), "{{application}}", "B")],
    "Heures depuis la dernière sauvegarde réussie de la base de chaque application (restaurée chaque semaine pour test). "
    "Application cobaye : sauvegarde de son serveur, qui contient sa base.",
    unit="h", decimals=1, steps=[{"color": "green", "value": None}, {"color": "orange", "value": 26}, {"color": "red", "value": 50}]))
y += 4
panels.append(row("Déploiements", y))
y += 1
# Lus chaque minute sur le nœud de contrôle (pfe-etat-cluster) et écrits par les pipelines Jenkins en fin de build
panels.append(stat("Version en ligne", 0, y, 9, 5,
    [tgt('max by (application, composant, version) (pfe_deploiement_version)', "{{application}} / {{composant}} : {{version}}")],
    "Image qui tourne réellement dans Kubernetes pour chaque composant (lue chaque minute). "
    "Vert : toutes les copies sont prêtes ; orange : une partie seulement ; rouge : aucune.",
    text_mode="name", steps=[{"color": "red", "value": None}, {"color": "orange", "value": 0.01}, {"color": "green", "value": 1}]))
panels.append(stat("Argo CD", 9, y, 5, 5,
    [tgt('max by (application, sync, sante) (pfe_argocd_app)', "{{application}} : {{sync}} / {{sante}}")],
    "Synced : le cluster correspond au dépôt Git. Healthy : tout démarre correctement. "
    "Bleu : synchronisation ou démarrage en cours ; rouge : à regarder dans Argo CD.",
    text_mode="name", steps=[{"color": "red", "value": None}, {"color": "blue", "value": 0.5}, {"color": "green", "value": 1}],
    links=[{"title": "Ouvrir Argo CD", "url": f"{ARGOCD}/applications", "targetBlank": True}]))
panels.append(stat("Dernier build Jenkins", 14, y, 6, 5,
    [tgt('max by (application, pipeline, version) (pfe_ci_dernier_build)', "{{application}} ({{version}})")],
    "Résultat du dernier build de chaque application (réussi, instable : Quality Gate non bloquante, ou échec). "
    "En cas d'échec rien n'est déployé : la version en ligne reste en place. Cliquer pour ouvrir le job.",
    mappings=val({"1": ("réussi", "green"), "0.5": ("instable", "orange"), "0": ("ÉCHEC", "red")}),
    steps=[{"color": "red", "value": None}, {"color": "orange", "value": 0.5}, {"color": "green", "value": 1}],
    links=[{"title": "Ouvrir le job ${__field.labels.pipeline}", "url": f"{JENKINS}/job/${{__field.labels.pipeline}}/",
            "targetBlank": True}]))
panels.append(stat("Synchronisation auto", 20, y, 4, 5,
    [tgt('max(pfe_sync_statut) or vector(-1)', "État"),
     tgt('max(pfe_sync_derniere_reussite_timestamp_seconds) * 1000', "Dernière", "B")],
    "pfe-sync vérifie GitHub toutes les 5 min : quand une application est ajoutée ou ses réglages modifiés, le parc "
    "est resynchronisé automatiquement (supervision, IA, sauvegardes). « Dernière » : dernière resynchronisation réussie.",
    mappings=val({"1": ("OK", "green"), "0": ("ÉCHEC", "red"), "-1": ("ABSENT", "red")}), steps=RG,
    overrides=[{"matcher": {"id": "byFrameRefID", "options": "B"},
                "properties": [{"id": "unit", "value": "dateTimeFromNow"}, {"id": "mappings", "value": []},
                               {"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}]}]))
y += 5
panels.append(row("Services", y))
y += 1
panels.append(stat("État des services", 0, y, 24, 5,
    [tgt('probe_success{job="services"}', "{{service}}")],
    "Chaque service est testé toutes les 15 s depuis le serveur de supervision. Cliquer sur un service pour l'ouvrir. "
    "SonarQube ne tourne que pendant les builds Jenkins (arrêté le reste du temps pour libérer 2 Go de RAM).",
    mappings=val({"1": ("en ligne", "green"), "0": ("HORS LIGNE", "red")}), steps=RG,
    overrides=[{"matcher": {"id": "byName", "options": "SonarQube"},
                "properties": [{"id": "mappings", "value": val({"1": ("en ligne (build)", "green"), "0": ("arrêté hors build", "blue")})}]}],
    links=[{"title": "Ouvrir ${__field.labels.service}", "url": "${__field.labels.lien}", "targetBlank": True}]))
y += 5
panels.append(stat("Services découverts automatiquement", 0, y, 24, 4,
    [tgt('probe_success{job="services-decouverts"}', "{{service}}")],
    "Ports publiés par Docker, services NodePort et Ingress de Kubernetes, découverts sur chaque serveur au "
    "déploiement puis chaque minute par l'agent (hors services déclarés ci-dessus). Test : le port répond.",
    mappings=val({"1": ("répond", "green"), "0": ("NE RÉPOND PAS", "red")}), steps=RG,
    links=[{"title": "Ouvrir ${__field.labels.service}", "url": "${__field.labels.lien}", "targetBlank": True}]))
y += 4
panels.append(row("Serveurs", y))
y += 1
pct = [{"color": "green", "value": None}, {"color": "orange", "value": 75}, {"color": "red", "value": 90}]
panels.append(stat("CPU", 0, y, 6, 4, [tgt('100 - avg by (node) (rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100', "{{node}}")],
                   "Charge processeur moyenne de chaque serveur.", unit="percent", steps=pct))
panels.append(stat("Mémoire", 6, y, 6, 4,
                   [tgt('(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100', "{{node}}")],
                   "Mémoire utilisée (hors cache récupérable).", unit="percent", steps=pct))
panels.append(stat("Disque", 12, y, 6, 4,
                   [tgt('100 - node_filesystem_avail_bytes{mountpoint="/",fstype="ext4"} / node_filesystem_size_bytes{mountpoint="/",fstype="ext4"} * 100', "{{node}}")],
                   "Occupation du disque système.", unit="percent", steps=pct))
panels.append(stat("Détection par l'IA", 18, y, 6, 4, [tgt('max by (node) (aiops_anomaly_active)', "{{node}}")],
                   "Moteur AIOps (z-score robuste + Isolation Forest), un modèle par serveur.",
                   mappings=val({"0": ("normal", "green"), "1": ("ANOMALIE", "orange")}), steps=[{"color": "green", "value": None}]))
y += 4
panels.append(stat("Parc (découverte toutes les heures)", 0, y, 24, 3,
    [tgt('pfe_parc_serveur', "{{adresse}} : {{etat}}")],
    "Adresses de infra/parc.yml explorées en lecture seule. « vide » : serveur disponible, à ajouter avec "
    "bash deploy.sh --ajouter <adresse> sur le serveur de supervision (voir docs/PARC.md).", text_mode="name",
    overrides=[{"matcher": {"id": "byRegexp", "options": ".*: vide"},
                "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}]}]))
y += 3
panels.append(row("Auto-réparation, sauvegardes et alertes", y))
y += 1
panels.append(stat("Agent", 0, y, 4, 4, [tgt('max(pfe_agent_mode) + 2 * max(pfe_agent_suspended) or vector(-1)')],
                   "Conseil : simule. Actif : répare. Suspendu : arrêté après des échecs répétés.", text_mode="value",
                   mappings=val({"-1": ("ARRÊTÉ", "red"), "0": ("CONSEIL", "blue"), "1": ("ACTIF", "green"),
                                 "2": ("SUSPENDU", "red"), "3": ("SUSPENDU", "red")})))
panels.append(stat("Réparations (24 h)", 4, y, 4, 4,
                   [tgt('sum(increase(pfe_agent_actions_total{outcome="succes"}[24h])) or vector(0)')],
                   "Actions de l'agent exécutées avec succès sur les dernières 24 h.", text_mode="value",
                   links=[{"title": "Détail de l'agent", "url": "/d/pfe-agent-sauvegardes"}]))
panels.append(stat("Dernière sauvegarde", 8, y, 6, 4,
                   [tgt('(time() - pfe_backup_last_success_timestamp_seconds) / 3600 and pfe_backup_last_success_timestamp_seconds > 0', "{{node}}")],
                   "Heures depuis la dernière sauvegarde réussie (copiée chaque nuit sur l'autre serveur).", unit="h", decimals=1,
                   steps=[{"color": "green", "value": None}, {"color": "orange", "value": 26}, {"color": "red", "value": 50}],
                   links=[{"title": "Détail des sauvegardes", "url": "/d/pfe-agent-sauvegardes"}]))
panels.append(stat("Test de restauration", 14, y, 5, 4, [tgt('pfe_backup_restore_test_status', "{{node}}")],
                   "Restauration réelle du dernier jeu dans un espace de test, chaque semaine.",
                   mappings=val({"1": ("réussi", "green"), "0": ("ÉCHEC", "red")}), steps=RG))
panels.append(stat("Alertes en cours", 19, y, 5, 4,
                   [tgt('count(ALERTS{alertstate="firing"}) or vector(0)')],
                   "Toutes les alertes actives du parc (détail dans « Agent de remédiation et sauvegardes »).", text_mode="value",
                   steps=[{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 3}],
                   links=[{"title": "Voir les alertes", "url": "/d/pfe-agent-sauvegardes"}]))
y += 4

# --- Évaluation : chiffres de la dernière campagne d'injection de pannes
synth = os.path.join(RACINE, "eval", "resultats", "synthese.json")
md = "Aucune campagne analysée pour l'instant."
if os.path.exists(synth):
    s = json.load(open(synth, encoding="utf-8"))
    det = {d["id"]: d for d in s["detecteurs"]}
    ia, ag, pr = det["ia"], s["agent"], s["precision"]
    tot = pr["vrais"] + pr["faux"]
    m = lambda xs: f"{statistics.median(xs):.0f} s" if xs else "—"
    md = (f"**{len(s['injections'])} pannes réelles injectées** sur les serveurs (campagne du "
          f"{__import__('datetime').datetime.fromtimestamp(s['debut'], __import__('datetime').timezone.utc).strftime('%d/%m/%Y')}).\n\n"
          f"| IA : pannes vues | Délai médian | Précision | Agent : réparées | Rétablissement médian |\n|---|---|---|---|---|\n"
          f"| {ia['vus']}/{ia['total']} | {m(ia['delais'])} | {(f'{100 * pr['vrais'] / tot:.0f} %') if tot else '—'} "
          f"| {ag['reparees']}/{ag['reparables']} | {m(ag['retablissement'])} |\n\n"
          f"[Chronogramme de chaque panne et méthode]({RAPPORT})")
panels.append(row("Évaluation par injection de pannes", y))
y += 1
panels.append(text("", 0, y, 24, 6, md))

ACCUEIL_LIEN = {"title": "Accueil", "type": "link", "url": "/d/pfe-accueil", "icon": "dashboard"}
DETAILS_LIEN = {"title": "Détails techniques", "type": "link", "url": "/d/pfe-details", "icon": "dashboard"}
OUTILS = [
    {"title": "Jenkins", "type": "link", "url": f"{JENKINS}/", "icon": "external link", "targetBlank": True},
    {"title": "Argo CD", "type": "link", "url": f"{ARGOCD}/", "icon": "external link", "targetBlank": True},
    {"title": "Dépôt GitHub", "type": "link", "url": DEPOT, "icon": "doc", "targetBlank": True},
]


def ecrire(uid, titre, liens, contenu):
    d = {"uid": uid, "title": titre, "tags": ["pfe"], "timezone": "browser", "schemaVersion": 39, "version": 1,
         "refresh": "30s", "time": {"from": "now-1h", "to": "now"}, "templating": {"list": []},
         "annotations": {"list": []}, "links": liens, "panels": contenu}
    with open(os.path.join(RACINE, f"infra/ansible/roles/monitoring/files/dashboards/{uid}.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(uid, len(contenu), "panneaux")


ecrire("pfe-details", "PFE - Détails", [ACCUEIL_LIEN,
    {"title": "Supervision détaillée", "type": "link", "url": "/d/pfe-supervision", "icon": "dashboard"},
    {"title": "Agent et sauvegardes", "type": "link", "url": "/d/pfe-agent-sauvegardes", "icon": "dashboard"},
    *OUTILS,
    {"title": "SonarQube", "type": "link", "url": f"http://{APP}:9000/", "icon": "external link", "targetBlank": True},
    {"title": "Résultats de la campagne", "type": "link", "url": RAPPORT, "icon": "info", "targetBlank": True}], panels)

# ============================== Page d'accueil « PFE - Accueil » : l'essentiel, lisible en quelques secondes
pid[0] = 0
panels = []
y = 0
ROUGE_SI_PLUS = [{"type": "range", "options": {"from": 1, "to": 1e9, "result": {"color": "red"}}}]
rouge_si = [{"color": "green", "value": None}, {"color": "red", "value": 1}]
# 1. Bandeau : tout va bien, ou combien de points sont à regarder (liste en haut de la page Détails)
panels.append(stat("", 0, y, 24, 3, [tgt(f'count({ALERTES_EN_COURS}) or vector(0)')],
    "Alertes actives sur tout le parc. Leur liste est en haut de la page « Détails techniques ».",
    unit="suffix: point(s) à regarder", text_mode="value",
    mappings=val({"0": ("Tout fonctionne", "green")}) + ROUGE_SI_PLUS, steps=rouge_si,
    links=[{"title": "Voir les alertes", "url": "/d/pfe-details"}]))
y += 3
# 2. Les deux actions
panels.append(text("", 0, y, 12, 4,
    f"### 🚀 Déployer : [__APP_NAME__]({DEPLOYER}) · [une autre application]({DEPLOYER_APPS})\n"
    "Ou le bouton **Déployer** sur sa ligne ci-dessous (nom déjà rempli). La dernière version de son dépôt GitHub "
    "est testée, contrôlée (SonarQube, Trivy) puis mise en ligne, en 10 minutes environ. Si un contrôle échoue, rien ne change."))
panels.append(text("", 12, y, 12, 4,
    f"### [📦 Ajouter une application]({DEPLOYER_APPS})\n"
    "Son nom et l'adresse de son dépôt Git : la plateforme la construit, la contrôle, la met en ligne puis la surveille."))
y += 4
# 3. Une ligne par application
etat_app = val({"1": ("en ligne", "green"), "0": ("HORS LIGNE", "red")})
build = val({"SUCCESS": ("réussi", "green"), "UNSTABLE": ("instable", "orange"), "FAILURE": ("ÉCHEC", "red"),
             "ABORTED": ("interrompu", "text")})
ia = val({"0": ("normal", "green"), "1": ("anomalie", "orange")})
couleur = lambda m: [{"id": "mappings", "value": m}, {"id": "custom.cellOptions", "value": {"type": "color-text"}}]
panels.append(table("Applications", 0, y, 24, 6, [
        tgt('max by (application, instance) (probe_success{job="applications"}) or '
            + COB('max by (instance) (probe_success{job="services",service="Application"})'), ref="A", table=True),
        tgt('max by (application, version) (pfe_deploiement_version{version=~".+-[0-9a-f]{7}"}) or on (application) '
            'max by (application, version) (pfe_deploiement_version)', ref="B", table=True),
        tgt('max by (application, resultat) (pfe_ci_dernier_build)', ref="C", table=True),
        tgt(f'max by (application) (aiops_anomaly_active * on (node) group_left (application) {APPLI}) or {COB(COB_IA)}',
            ref="D", table=True),
        # Lien « Déployer » : job de l'application cobaye, ou job commun avec le nom de l'application déjà rempli
        tgt('label_replace(max by (application) (probe_success{job="applications"}), "deployer", '
            f'"{DEPLOYER_UNE_APP}$1", "application", "(.+)") or '
            'label_replace(' + COB('max(probe_success{job="services",service="Application"})')
            + f', "deployer", "{DEPLOYER}", "", "")',
            ref="E", table=True)],
    "État : adresse web testée toutes les 15 s. Version en ligne : celle qui tourne vraiment. Dernier build : "
    "résultat du dernier passage dans Jenkins. IA : comportement anormal détecté. Cliquer sur un nom pour ouvrir l'application ; sur « Déployer » pour "
    "la reconstruire et la remettre en ligne depuis son dépôt (formulaire Jenkins prérempli, puis Build).",
    garder={"application": "Application", "Value #A": "État", "version": "Version en ligne",
            "resultat": "Dernier build", "Value #D": "IA", "Value #E": "Action", "instance": "instance", "deployer": "deployer"},
    jointure="application", vide="—",
    overrides=[{"matcher": {"id": "byName", "options": "État"}, "properties": couleur(etat_app)},
               {"matcher": {"id": "byName", "options": "Dernier build"}, "properties": couleur(build)},
               {"matcher": {"id": "byName", "options": "IA"}, "properties": couleur(ia)},
               {"matcher": {"id": "byName", "options": "instance"}, "properties": [{"id": "custom.hidden", "value": True}]},
               {"matcher": {"id": "byName", "options": "deployer"}, "properties": [{"id": "custom.hidden", "value": True}]},
               {"matcher": {"id": "byName", "options": "Action"}, "properties": [
                   {"id": "mappings", "value": [{"type": "range", "options": {"from": -1, "to": 2,
                                                  "result": {"text": "🚀 Déployer", "color": "blue"}}}]},
                   {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                   {"id": "links", "value": [{"title": "Déployer ${__data.fields.Application}",
                                              "url": "${__data.fields.deployer}", "targetBlank": True}]}]},
               {"matcher": {"id": "byName", "options": "Application"}, "properties": [
                   {"id": "links", "value": [{"title": "Ouvrir ${__value.text}", "url": "${__data.fields.instance}",
                                              "targetBlank": True}]}]}]))
y += 6
# 4. Quatre voyants pour la plateforme
tous = lambda txt: val({"0": (txt, "green")}) + ROUGE_SI_PLUS
panels.append(stat("Serveurs", 0, y, 6, 4, [tgt('count(up{job="node-exporter"} == 0) or vector(0)')],
    "Chaque serveur du parc répond-il ? Sinon : nombre de serveurs injoignables.", unit="suffix: hors ligne",
    text_mode="value", color_mode="value", mappings=tous("tous en ligne"), steps=rouge_si))
panels.append(stat("Services", 6, y, 6, 4, [tgt('count(probe_success{job="services",hors_build="non"} == 0) or vector(0)')],
    "Jenkins, Argo CD, Vault, Grafana, Prometheus, IA, agent… testés toutes les 15 s. Sinon : nombre de services "
    "arrêtés. SonarQube ne tourne que pendant les builds.", unit="suffix: arrêté(s)",
    text_mode="value", color_mode="value", mappings=tous("tous en ligne"), steps=rouge_si))
panels.append(stat("Sauvegardes", 12, y, 6, 4,
    [tgt('(time() - min(pfe_backup_last_success_timestamp_seconds > 0)) / 3600')],
    "Âge, en heures, de la plus ancienne des dernières sauvegardes (une par nuit et par serveur, copiée sur l'autre "
    "serveur, restaurée chaque semaine pour test).", unit="suffix: h", decimals=0, text_mode="value", color_mode="value",
    steps=[{"color": "green", "value": None}, {"color": "orange", "value": 26}, {"color": "red", "value": 50}]))
panels.append(stat("Surveillance par l'IA", 18, y, 6, 4, [tgt('max(aiops_anomaly_active) or vector(0)')],
    "Un modèle par serveur et par application apprend le comportement normal et signale ce qui s'en écarte ; "
    "l'agent d'auto-réparation peut alors agir.", text_mode="value", color_mode="value",
    mappings=val({"0": ("normal", "green"), "1": ("anomalie détectée", "orange")}), steps=[{"color": "green", "value": None}]))
y += 4
ecrire("pfe-accueil", "PFE - Accueil", [DETAILS_LIEN, *OUTILS], panels)
