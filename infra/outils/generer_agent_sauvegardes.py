"""Génère un tableau de bord Grafana du dépôt (python infra/outils/generer_agent_sauvegardes.py).
Les marqueurs __APP_IP__, __REPO_WEB__… sont remplacés par Ansible au déploiement (group_vars/all.yml)."""
import json
import os
RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DS = {"type": "prometheus", "uid": "efx9ykc7u57nkf"}
pid = [0]


def nid():
    pid[0] += 1
    return pid[0]


def tgt(expr, ref="A", legend=None, instant=False, table=False):
    t = {"refId": ref, "datasource": DS, "expr": expr}
    if legend:
        t["legendFormat"] = legend
    if instant:
        t["instant"] = True
        t["range"] = False
    if table:
        t["format"] = "table"
    return t


def stat(title, x, y, w, h, targets, desc, unit="none", steps=None, mappings=None, decimals=0):
    return {"type": "stat", "title": title, "id": nid(), "gridPos": {"h": h, "w": w, "x": x, "y": y}, "datasource": DS,
            "targets": targets, "description": desc,
            "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "mappings": mappings or [],
                                         "thresholds": {"mode": "absolute", "steps": steps or [{"color": "green", "value": None}]}},
                            "overrides": []},
            "options": {"colorMode": "background", "graphMode": "none", "textMode": "auto", "justifyMode": "center",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}


def row(title, y):
    return {"type": "row", "title": title, "id": nid(), "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "collapsed": False, "panels": []}


def table(title, x, y, w, h, targets, desc, transformations, overrides=None):
    return {"type": "table", "title": title, "id": nid(), "gridPos": {"h": h, "w": w, "x": x, "y": y}, "datasource": DS,
            "targets": targets, "description": desc, "transformations": transformations,
            "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}}, "overrides": overrides or []},
            "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}}


def ov(name, props):
    return {"matcher": {"id": "byName", "options": name}, "properties": props}


def colored(name, steps, mappings=None):
    p = [{"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
         {"id": "thresholds", "value": {"mode": "absolute", "steps": steps}}]
    if mappings:
        p.append({"id": "mappings", "value": mappings})
    return ov(name, p)


def when(name):
    return ov(name, [{"id": "unit", "value": "dateTimeAsLocal"}])


RG = [{"color": "red", "value": None}, {"color": "green", "value": 1}]
ETAT = [{"type": "value", "options": {"0": {"text": "ÉCHEC", "color": "red"}, "1": {"text": "OK", "color": "green"}}}]
HIDE = {"Time": True, "__name__": True, "instance": True, "job": True}
panels = []
y = 0
panels.append(row("Agent de remédiation", y))
y += 1
panels.append(stat("Mode", 0, y, 4, 4, [tgt('max(pfe_agent_mode) + 2 * max(pfe_agent_suspended) or vector(-1)')],
    "Conseil : simule et journalise. Actif : exécute. Suspendu : arrêt après des échecs répétés (python -m agent reprendre).",
    mappings=[{"type": "value", "options": {"-1": {"text": "ARRÊTÉ", "color": "red"}, "0": {"text": "CONSEIL", "color": "blue"},
                                             "1": {"text": "ACTIF", "color": "green"}, "2": {"text": "SUSPENDU", "color": "red"},
                                             "3": {"text": "SUSPENDU", "color": "red"}}}]))
panels.append(stat("Réparations réussies (période)", 4, y, 4, 4,
    [tgt('sum(increase(pfe_agent_actions_total{outcome="succes"}[$__range])) or vector(0)')],
    "Actions exécutées avec succès sur la période affichée."))
panels.append(stat("Échecs (période)", 8, y, 4, 4,
    [tgt('sum(increase(pfe_agent_actions_total{outcome="echec"}[$__range])) or vector(0)')],
    "Actions dont l'exécution a échoué (3 échecs consécutifs suspendent l'agent).",
    steps=[{"color": "green", "value": None}, {"color": "red", "value": 1}]))
panels.append(stat("Alerte disparue 5 min après", 12, y, 4, 4,
    [tgt('sum(increase(pfe_agent_verifications_total{result="resolu"}[$__range])) / sum(increase(pfe_agent_verifications_total[$__range]))')],
    "Part des actions (ou simulations) suivies de la disparition de l'alerte 5 min après.", unit="percentunit",
    steps=[{"color": "red", "value": None}, {"color": "orange", "value": 0.5}, {"color": "green", "value": 0.8}]))
panels.append(stat("Nœuds joignables par l'agent", 16, y, 4, 4, [tgt('sum(pfe_agent_node_reachable) or vector(0)')],
    "Test SSH toutes les 5 min (action status, sans effet)."))
panels.append(stat("Bloquées par un garde-fou (période)", 20, y, 4, 4,
    [tgt('sum(increase(pfe_agent_decisions_total{decision=~"pause|quota|escalade|maintenance|suspendu|contexte_indisponible"}[$__range])) or vector(0)')],
    "Pause entre actions identiques, quota horaire, escalade, maintenance, suspension, Prometheus muet.",
    steps=[{"color": "blue", "value": None}]))
y += 4
panels.append(table("Dernières actions (par nœud et action)", 0, y, 12, 8,
    [tgt('pfe_agent_last_action_timestamp_seconds * 1000', instant=True, table=True)],
    "Heure de la dernière action pour chaque couple nœud/action, avec son résultat. Journal complet : docker exec mon-remediation python -m agent journal",
    [{"id": "organize", "options": {"excludeByName": HIDE,
                                    "renameByName": {"node": "Nœud", "action": "Action", "outcome": "Résultat", "Value": "Heure"},
                                    "indexByName": {"Value": 0, "node": 1, "action": 2, "outcome": 3}}},
     {"id": "sortBy", "options": {"sort": [{"field": "Heure", "desc": True}]}}],
    [when("Heure"),
     colored("Résultat", [{"color": "blue", "value": None}],
             [{"type": "value", "options": {"succes": {"color": "green", "text": "succès"}, "echec": {"color": "red", "text": "échec"},
                                            "simulee": {"color": "blue", "text": "simulée (conseil)"},
                                            "refusee": {"color": "orange", "text": "refusée par le nœud"},
                                            "rien_a_faire": {"color": "text", "text": "rien à faire"}}}])]))
panels.append(table("Décisions par alerte (période)", 12, y, 12, 8,
    [tgt('sum by (alertname, node, action, decision) (increase(pfe_agent_decisions_total[$__range])) > 0', instant=True, table=True)],
    "Pour chaque alerte reçue : action choisie et issue (exécutée, simulée, ou bloquée par un garde-fou).",
    [{"id": "organize", "options": {"excludeByName": {"Time": True},
                                    "renameByName": {"alertname": "Alerte", "node": "Nœud", "action": "Action choisie",
                                                     "decision": "Décision", "Value": "Nombre"}}},
     {"id": "sortBy", "options": {"sort": [{"field": "Nombre", "desc": True}]}}],
    [ov("Nombre", [{"id": "decimals", "value": 0}])]))
y += 8
panels.append({"type": "timeseries", "title": "Actions de l'agent dans le temps", "id": nid(),
               "gridPos": {"h": 7, "w": 24, "x": 0, "y": y}, "datasource": DS,
               "description": "Actions exécutées ou simulées, par résultat.",
               "targets": [tgt('sum by (outcome) (increase(pfe_agent_actions_total[$__rate_interval]))', legend="{{outcome}}")],
               "fieldConfig": {"defaults": {"unit": "none", "custom": {"drawStyle": "bars", "fillOpacity": 80, "stacking": {"mode": "normal"}}},
                               "overrides": []},
               "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}})
y += 7
panels.append(row("Sauvegardes", y))
y += 1


def bk(e, r):
    return tgt(e, ref=r, instant=True, table=True)


panels.append(table("État des sauvegardes par serveur", 0, y, 24, 6,
    [bk('pfe_backup_last_success_timestamp_seconds * 1000 > 0', "A"), bk('pfe_backup_last_status', "B"),
     bk('pfe_backup_last_size_bytes', "C"), bk('pfe_backup_last_duration_seconds', "D"),
     bk('pfe_backup_offsite_last_success_timestamp_seconds * 1000 > 0', "E"), bk('pfe_backup_local_sets', "F"),
     bk('pfe_backup_restore_test_status', "G"), bk('pfe_backup_restore_test_last_success_timestamp_seconds * 1000 > 0', "H")],
    "Une ligne par serveur : dernière sauvegarde, copie sur l'autre serveur, jeux gardés, dernier test de restauration réel.",
    [{"id": "merge", "options": {}},
     {"id": "organize", "options": {"excludeByName": HIDE,
        "renameByName": {"node": "Serveur", "Value #A": "Dernière sauvegarde", "Value #B": "Statut", "Value #C": "Taille",
                         "Value #D": "Durée", "Value #E": "Copie distante", "Value #F": "Jeux gardés",
                         "Value #G": "Test de restauration", "Value #H": "Dernier test réussi"},
        "indexByName": {"node": 0, "Value #B": 1, "Value #A": 2, "Value #C": 3, "Value #D": 4, "Value #E": 5,
                        "Value #F": 6, "Value #G": 7, "Value #H": 8}}}],
    [when("Dernière sauvegarde"), when("Copie distante"), when("Dernier test réussi"),
     ov("Taille", [{"id": "unit", "value": "bytes"}]), ov("Durée", [{"id": "unit", "value": "s"}]),
     colored("Statut", RG, ETAT), colored("Test de restauration", RG, ETAT)]))
y += 6
panels.append(table("Sauvegardes reçues des autres serveurs", 0, y, 12, 6,
    [bk('pfe_backup_received_last_timestamp_seconds * 1000 > 0', "A"), bk('pfe_backup_received_valid', "B"),
     bk('pfe_backup_received_sets', "C")],
    "Jeux déposés par l'autre serveur (compte en écriture seule, sans suppression possible), empreintes vérifiées chaque nuit.",
    [{"id": "merge", "options": {}},
     {"id": "organize", "options": {"excludeByName": HIDE,
        "renameByName": {"node": "Stocké sur", "source": "Envoyé par", "Value #A": "Dernier jeu reçu",
                         "Value #B": "Empreintes", "Value #C": "Jeux gardés"},
        "indexByName": {"source": 0, "node": 1, "Value #A": 2, "Value #B": 3, "Value #C": 4}}}],
    [when("Dernier jeu reçu"),
     colored("Empreintes", RG, [{"type": "value", "options": {"0": {"text": "CORROMPU", "color": "red"},
                                                              "1": {"text": "valides", "color": "green"}}}])]))
panels.append({"type": "timeseries", "title": "Taille des sauvegardes", "id": nid(), "gridPos": {"h": 6, "w": 12, "x": 12, "y": y},
               "datasource": DS,
               "description": "Taille du dernier jeu de chaque serveur : une hausse brutale ou une chute à zéro mérite un coup d'œil.",
               "targets": [tgt('pfe_backup_last_size_bytes', legend="{{node}}")],
               "fieldConfig": {"defaults": {"unit": "bytes", "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10,
                                                                     "showPoints": "always"}}, "overrides": []},
               "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}})
y += 6
panels.append(row("Alertes en cours", y))
y += 1
panels.append(table("Alertes actives sur le parc", 0, y, 24, 7, [tgt('ALERTS{alertstate="firing"}', instant=True, table=True)],
    "Toutes les alertes en cours. Source agent ou backup : alertes sur l'agent ou les sauvegardes (l'agent n'agit jamais dessus).",
    [{"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True, "alertstate": True, "Value": True, "instance": True},
        "renameByName": {"alertname": "Alerte", "node": "Nœud", "severity": "Gravité", "job": "Collecteur", "source": "Source"},
        "indexByName": {"alertname": 0, "node": 1, "severity": 2}}}],
    [colored("Gravité", [{"color": "orange", "value": None}],
             [{"type": "value", "options": {"critical": {"color": "red", "text": "critique"},
                                            "warning": {"color": "orange", "text": "attention"}}}])]))
d = {"uid": "pfe-agent-sauvegardes", "title": "PFE - Agent de remédiation et sauvegardes", "tags": ["pfe", "aiops", "sauvegardes"],
     "timezone": "browser", "schemaVersion": 39, "version": 1, "refresh": "30s", "time": {"from": "now-24h", "to": "now"},
     "templating": {"list": []}, "annotations": {"list": []}, "panels": panels,
     "links": [{"title": "Supervision multi-nœuds", "type": "link", "url": "/d/pfe-supervision", "icon": "dashboard"}]}
with open(os.path.join(RACINE, 'infra/ansible/roles/monitoring/files/dashboards/pfe-agent-sauvegardes.json'), 'w', encoding='utf-8', newline='\n') as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
print(len(panels), "panneaux")
