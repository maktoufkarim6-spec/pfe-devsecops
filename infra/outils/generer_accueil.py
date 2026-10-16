"""Génère un tableau de bord Grafana du dépôt (python infra/outils/generer_accueil.py).
Les marqueurs __APP_IP__, __REPO_WEB__… sont remplacés par Ansible au déploiement (group_vars/all.yml)."""
import json
import os
RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import statistics

DS = {"type": "prometheus", "uid": "efx9ykc7u57nkf"}
APP = "__APP_IP__"
RAPPORT = "__EVALUATION_URL__"
DEPOT = "__REPO_WEB__"
DEPLOYER = "__JENKINS_JOB_URL__"
DEPLOYER_APPS = "__JENKINS_APPS_URL__"
pid = [0]


def nid():
    pid[0] += 1
    return pid[0]


def tgt(expr, legend=None, ref="A"):
    t = {"refId": ref, "datasource": DS, "expr": expr, "instant": True, "range": False}
    if legend:
        t["legendFormat"] = legend
    return t


def stat(title, x, y, w, h, targets, desc="", unit="none", steps=None, mappings=None, decimals=0, overrides=None,
         text_mode="value_and_name", links=None):
    p = {"type": "stat", "title": title, "id": nid(), "gridPos": {"h": h, "w": w, "x": x, "y": y}, "datasource": DS,
         "targets": targets, "description": desc,
         "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "mappings": mappings or [],
                                      "thresholds": {"mode": "absolute", "steps": steps or [{"color": "green", "value": None}]},
                                      "links": links or []},
                         "overrides": overrides or []},
         "options": {"colorMode": "background", "graphMode": "none", "textMode": text_mode, "justifyMode": "center",
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


RG = [{"color": "red", "value": None}, {"color": "green", "value": 1}]
panels = []
y = 0
panels.append(text("", 0, y, 24, 3,
    "## Projet PFE DevSecOps + AIOps\n"
    "Chaîne CI/CD sécurisée (Jenkins, SonarQube, Trivy, Argo CD), supervision multi-nœuds avec détection d'anomalies par IA, "
    "agent d'auto-réparation et sauvegardes croisées. **Tout l'état du projet sur une page** ; les liens en haut ouvrent chaque outil."))
y += 3
panels.append(row("Déploiement en un clic", y))
y += 1
panels.append(text("", 0, y, 16, 7,
    f"### [🚀 Déployer la dernière version]({DEPLOYER})\n"
    "Lance la chaîne complète sur la branche `main` du dépôt : tests, SonarQube, construction des images, scan Trivy, "
    "publication sur Docker Hub, puis déploiement par Argo CD sur Kubernetes. Jenkins demande de se connecter ; "
    "le champ **NOEUDS** choisit les serveurs où l'application tourne (noms de la section « Serveurs » ci-dessous, "
    "ex. `app-1,app-2` ; `tous` : n'importe lequel ; vide : garder les serveurs actuels), puis **Build**. "
    "Compter environ 10 minutes ; l'avancement s'affiche étape par étape dans Jenkins. "
    "Si une étape de sécurité échoue, rien n'est déployé et la version en ligne reste en place.\n\n"
    f"### [🌐 Voir l'application en ligne](http://{APP}__APP_URL_PATH__)\n\n"
    f"### [📦 Construire et déployer une autre application]({DEPLOYER_APPS})\n"
    "Applications du dossier `applications/` : champ **APPLICATION** (ex. `demo-podinfo`). Même chaîne de "
    "sécurité, depuis le code source déclaré dans son `values.yaml`."))
panels.append(stat("Application en ligne", 16, y, 8, 7,
    [tgt('probe_success{job="services",service="Application"}')],
    "Page de l'application testée toutes les 15 s depuis le serveur de supervision. Cliquer pour l'ouvrir.",
    text_mode="value", mappings=val({"1": ("EN LIGNE", "green"), "0": ("HORS LIGNE", "red")}), steps=RG,
    links=[{"title": "Ouvrir l'application", "url": f"http://{APP}__APP_URL_PATH__", "targetBlank": True}]))
y += 7
panels.append(row("Applications de la plateforme", y))
y += 1
APPLI = 'aiops_target_info{kind="application"}'
panels.append(stat("En ligne", 0, y, 6, 4,
    [tgt('probe_success{job="applications"}', "{{application}}")],
    "Adresse web de chaque application (dossiers applications/<nom>/), testée toutes les 15 s. Cliquer pour l'ouvrir.",
    mappings=val({"1": ("en ligne", "green"), "0": ("HORS LIGNE", "red")}), steps=RG,
    links=[{"title": "Ouvrir ${__field.labels.application}", "url": "${__field.labels.instance}", "targetBlank": True}]))
panels.append(stat("Détection par l'IA", 6, y, 6, 4,
    [tgt(f'max by (application) (aiops_anomaly_active * on (node) group_left (application) {APPLI})', "{{application}}")],
    "Un modèle par application : CPU et RAM de chaque composant, conteneurs, disponibilité et temps de réponse.",
    mappings=val({"0": ("normal", "green"), "1": ("ANOMALIE", "orange")}), steps=[{"color": "green", "value": None}]))
panels.append(stat("Mémoire", 12, y, 6, 4,
    [tgt('sum by (application) (label_replace(container_memory_working_set_bytes{job="cadvisor-k3s",'
         'container_label_io_kubernetes_container_name!="",container_label_io_kubernetes_container_name!="POD"},'
         ' "application", "$1", "container_label_io_kubernetes_pod_namespace", "(.+)"))'
         f' and on (application) {APPLI}', "{{application}}")],
    "Mémoire utilisée par tous les conteneurs de l'application (plafonnée par son quota).", unit="bytes", decimals=0))
panels.append(stat("Sauvegarde de la base", 18, y, 6, 4,
    [tgt('(time() - pfe_backup_app_last_success_timestamp_seconds) / 3600 and pfe_backup_app_last_success_timestamp_seconds > 0',
         "{{application}}")],
    "Heures depuis la dernière sauvegarde réussie de la base de chaque application (restaurée chaque semaine pour test).",
    unit="h", decimals=1, steps=[{"color": "green", "value": None}, {"color": "orange", "value": 26}, {"color": "red", "value": 50}]))
y += 4
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

links = [
    {"title": "Déployer", "type": "link", "url": DEPLOYER, "icon": "cloud", "targetBlank": True},
    {"title": "Supervision détaillée", "type": "link", "url": "/d/pfe-supervision", "icon": "dashboard"},
    {"title": "Agent et sauvegardes", "type": "link", "url": "/d/pfe-agent-sauvegardes", "icon": "dashboard"},
    {"title": "Application", "type": "link", "url": f"http://{APP}__APP_URL_PATH__", "icon": "external link", "targetBlank": True},
    {"title": "Jenkins", "type": "link", "url": f"http://{APP}:8080/", "icon": "external link", "targetBlank": True},
    {"title": "Argo CD", "type": "link", "url": f"https://{APP}:32252/", "icon": "external link", "targetBlank": True},
    {"title": "SonarQube", "type": "link", "url": f"http://{APP}:9000/", "icon": "external link", "targetBlank": True},
    {"title": "Dépôt GitHub", "type": "link", "url": DEPOT, "icon": "doc", "targetBlank": True},
    {"title": "Résultats de la campagne", "type": "link", "url": RAPPORT, "icon": "info", "targetBlank": True},
]
d = {"uid": "pfe-accueil", "title": "PFE - Accueil", "tags": ["pfe"], "timezone": "browser", "schemaVersion": 39, "version": 1,
     "refresh": "30s", "time": {"from": "now-1h", "to": "now"}, "templating": {"list": []}, "annotations": {"list": []},
     "links": links, "panels": panels}
with open(os.path.join(RACINE, "infra/ansible/roles/monitoring/files/dashboards/pfe-accueil.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
print(len(panels), "panneaux")
