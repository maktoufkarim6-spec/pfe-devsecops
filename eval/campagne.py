"""Campagne d'injection de pannes réelles (évaluation du moteur AIOps et de l'agent de remédiation).

Lancée depuis le PC du propriétaire (accès SSH par clé aux deux VPS, utilisateur debian) :
    python eval/campagne.py                 # campagne complète (ordre défini dans ORDRE)
    python eval/campagne.py --seulement cadvisor,cpu_api
    python eval/campagne.py --liste

Pour chaque panne : attente d'un état calme (modèle prêt, pas de maintenance ni d'alerte, hors créneaux de
sauvegarde), injection, chronométrage de chaque détecteur, lecture de la décision de l'agent, réparation par
l'agent ou annulation par le script, attente du retour au calme. Chaque injection est ajoutée à
eval/resultats/injections.jsonl ; eval/analyse.py calcule ensuite précision, rappel, délais et taux de réparation.
Toute panne est annulée par le script (même en cas d'erreur ou d'interruption) : l'état final est l'état initial.
"""
import argparse
import csv
import io
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

APP, MON = "57.129.142.176", "54.37.13.100"
RESULTATS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultats")
POLL = 15
K = "sudo -n k3s kubectl -n default"


# ------------------------------------------------------------------ accès aux serveurs
def ssh(host, cmd, timeout=180):
    try:
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=10",
                            f"debian@{host}", cmd], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "délai SSH dépassé"
    return p.returncode, (p.stdout + p.stderr).strip()


def prom(expr, essais=5):
    """Requête instantanée ; un raté réseau passager est retenté (5 essais, attente croissante)."""
    for n in range(essais):
        rc, out = ssh(MON, "curl -s -m 20 -G localhost:9090/api/v1/query --data-urlencode " + shlex.quote("query=" + expr))
        try:
            body = json.loads(out)
            if body.get("status") == "success":
                return body["data"]
            raise RuntimeError(f"PromQL refusée : {expr} -> {body}")
        except ValueError:
            log(f"   réponse illisible de Prometheus (essai {n + 1}/{essais}) : {out[:80]!r}")
            time.sleep(5 * (n + 1))
    raise RuntimeError(f"Prometheus injoignable après {essais} essais : {expr}")


def now():
    return float(prom("time()")["result"][0])


def active(expr):
    return bool(prom(expr)["result"])


def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


# ------------------------------------------------------------------ scénarios
def signaux(node, extra=None):
    s = {
        "ia": f'aiops_anomaly_active{{node="{node}"}} == 1',
        "zscore": f'aiops_zscore_anomaly{{node="{node}"}} == 1',
        "iforest": f'aiops_iforest_anomaly{{node="{node}"}} == 1',
        "alerte_ia": f'ALERTS{{alertname="AnomalieAIOps",node="{node}",alertstate="firing"}}',
        "seuil": f'ALERTS{{alertname=~"CpuEleve|RamElevee|DisquePlein",node="{node}",alertstate="firing"}}',
    }
    s.update(extra or {})
    return s


def running(name):
    return f"docker inspect -f '{{{{.State.Running}}}}' {name} 2>/dev/null"


@dataclass
class Scenario:
    id: str
    nom: str
    noeud: str                 # nœud supervisé concerné (étiquette node)
    hote: str                  # adresse où injecter
    injecter: str
    annuler: str
    duree: int                 # durée maximale de la panne (s)
    action_attendue: str       # décision attendue de l'agent ("aucune" = escalade humaine)
    ia_attendue: bool          # le moteur AIOps doit-il la voir ?
    variables: tuple = ()      # variables AIOps attendues comme responsables
    reparation: str = ""       # commande qui affiche "ok" quand la panne a disparu (réparation par l'agent)
    alerte: dict = field(default_factory=dict)


S = {s.id: s for s in [
    Scenario("cadvisor", "Arrêt du collecteur cAdvisor", "app-1", APP,
             "docker stop cadvisor", "docker start cadvisor", 480,
             "restart-container cadvisor", False,
             reparation=f'[ "$({running("cadvisor")})" = true ] && echo ok',
             alerte={"alerte": 'ALERTS{alertname="CollecteurArrete",node="app-1",job="cadvisor",alertstate="firing"}'}),
    Scenario("node_exporter", "Arrêt de node-exporter (nœud muet)", "app-1", APP,
             "docker stop node-exporter", "docker start node-exporter", 480,
             "restart-container node-exporter", False,
             reparation=f'[ "$({running("node-exporter")})" = true ] && echo ok',
             alerte={"alerte": 'ALERTS{alertname="NoeudInjoignable",node="app-1",alertstate="firing"}'}),
    Scenario("moteur", "Arrêt du moteur AIOps", "monitoring", MON,
             "docker stop mon-aiops-engine", "docker start mon-aiops-engine", 480,
             "restart-container mon-aiops-engine", False,
             reparation=f'[ "$({running("mon-aiops-engine")})" = true ] && echo ok',
             alerte={"alerte": 'ALERTS{alertname="MoteurAIOpsArrete",alertstate="firing"}'}),
    Scenario("sonarqube", "SonarQube démarré hors build (+2 Go de RAM)", "app-1", APP,
             "docker start sonarqube", "docker stop sonarqube", 900,
             "stop-sonarqube", True, ("ram_host", "cpu_host"),   # démarrage de SonarQube : CPU puis RAM
             reparation=f'[ "$({running("sonarqube")})" != true ] && echo ok'),
    Scenario("cpu_hote", "Surcharge CPU du serveur (3 cœurs sur 4)", "app-1", APP,
             "docker run -d --rm --name pfe-eval-cpu --cpus 3 alpine:3.20 sh -c "
             "'for i in 1 2 3; do (while :; do :; done) & done; sleep 600'",
             "docker rm -f pfe-eval-cpu", 600, "stop-sonarqube", True, ("cpu_host",)),
    Scenario("ram_hote", "Pression mémoire du serveur (+2 Go)", "app-1", APP,
             "docker run -d --rm --name pfe-eval-ram --tmpfs /m:size=2100m alpine:3.20 sh -c "
             "'dd if=/dev/zero of=/m/x bs=1M count=2000 2>/dev/null && sleep 600'",
             "docker rm -f pfe-eval-ram", 600, "stop-sonarqube", True, ("ram_host",)),
    Scenario("cpu_api", "Boucle CPU dans le pod de l'API", "app-1", APP,
             f"{K} exec deploy/server -- sh -c 'nohup node -e \"for(;;){{}}\" >/dev/null 2>&1 &'",
             f"{K} exec deploy/server -- pkill -f 'node -e for' || true", 900,
             "rollout-restart server", True, ("cpu_server",),
             reparation=f"n=$({K} exec deploy/server -- ps -o args 2>/dev/null | grep -c '[f]or(;;)'); "
                        "[ \"$n\" = 0 ] && echo ok"),
    Scenario("ram_api", "Fuite mémoire dans le pod de l'API (+150 Mo)", "app-1", APP,
             f"{K} exec deploy/server -- sh -c 'nohup node -e \"const a=[];for(let i=0;i<15;i++)"
             f"a.push(Buffer.alloc(10485760,1));setInterval(()=>{{}},1e6)\" >/dev/null 2>&1 &'",
             f"{K} exec deploy/server -- pkill -f Buffer.alloc || true", 900,
             "rollout-restart server", True, ("ram_server_mb",),
             reparation=f"n=$({K} exec deploy/server -- ps -o args 2>/dev/null | grep -c '[B]uffer.alloc'); "
                        "[ \"$n\" = 0 ] && echo ok"),
    Scenario("disque", "Fichier de 6 Go sur le disque", "app-1", APP,
             "fallocate -l 6G /var/tmp/pfe-eval-disque", "rm -f /var/tmp/pfe-eval-disque", 900,
             "aucune", True, ("disk_host",)),
    Scenario("client_zero", "Pod client supprimé (mise à 0, Argo CD doit le remettre)", "app-1", APP,
             f"{K} scale deploy/client --replicas=0",
             f"[ \"$({K} get deploy client -o jsonpath='{{.spec.replicas}}')\" = 0 ] && {K} scale deploy/client --replicas=1 || true",
             300, "aucune", True, ("app_containers",),
             reparation=f"[ \"$({K} get deploy client -o jsonpath='{{.status.readyReplicas}}')\" = 1 ] && echo ok"),
]}

# Ordre choisi pour espacer les pannes qui appellent la même action (pause de 30 min de l'agent)
ORDRE = ["cadvisor", "sonarqube", "cpu_api", "disque", "cpu_hote", "ram_api", "node_exporter", "ram_hote",
         "client_zero", "cpu_api", "sonarqube", "ram_api", "cpu_hote", "moteur"]


# ------------------------------------------------------------------ conditions de départ
def creneau_sauvegarde(t):
    h = datetime.fromtimestamp(t, timezone.utc)
    m = h.hour * 60 + h.minute
    return 2 * 60 + 20 <= m <= 3 * 60 + 5 or 4 * 60 + 20 <= m <= 4 * 60 + 55


def calme(sc):
    n = sc.noeud
    raisons = []
    if not active(f'aiops_model_ready{{node="{n}"}} == 1'):
        raisons.append("modèle AIOps pas encore prêt")
    if active(f'aiops_warmup{{node="{n}"}} == 1'):
        raisons.append("moteur en chauffe")
    if active(f'pfe_maintenance_until_seconds{{node="{n}"}} > time()'):
        raisons.append("maintenance annoncée")
    if active(f'aiops_anomaly_active{{node="{n}"}} == 1'):
        raisons.append("anomalie en cours")
    if active(f'ALERTS{{node="{n}",alertstate="firing",source!~"agent|backup"}}'):
        raisons.append("alerte en cours")
    if creneau_sauvegarde(now()):
        raisons.append("créneau des sauvegardes")
    return raisons


def attendre_calme(sc, derniere_action, max_attente=2700):
    debut = time.time()
    while True:
        raisons = calme(sc)
        attente_action = 0
        last = derniere_action.get(sc.action_attendue)
        if sc.action_attendue != "aucune" and last and time.time() - last < 35 * 60:
            attente_action = 35 * 60 - (time.time() - last)
            raisons.append(f"pause de l'agent pour « {sc.action_attendue} » ({attente_action / 60:.0f} min)")
        if not raisons:
            return True
        if "modèle AIOps pas encore prêt" in raisons:
            debut = time.time()      # l'attente du premier apprentissage ne compte pas dans le délai de départ
        if time.time() - debut > max_attente and not attente_action and "modèle AIOps pas encore prêt" not in raisons:
            log(f"départ forcé malgré : {', '.join(raisons)}")
            return False
        log(f"attente : {', '.join(raisons)}")
        time.sleep(60)


# ------------------------------------------------------------------ journal de l'agent
def journal_agent(depuis):
    rc, out = ssh(MON, "docker exec mon-remediation cat /data/actions.csv")
    rows = []
    for r in csv.DictReader(io.StringIO(out)):
        try:
            t = datetime.strptime(r["horodatage"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except (ValueError, KeyError):
            continue
        if t >= depuis - 5:
            r["t"] = t
            rows.append(r)
    return rows


# ------------------------------------------------------------------ une injection
def injecter(sc, numero):
    sig = signaux(sc.noeud, sc.alerte)
    t0 = now()
    log(f"[{numero}] INJECTION {sc.id} : {sc.nom}")
    rc, out = ssh(sc.hote, sc.injecter)
    if rc != 0:
        log(f"injection impossible ({out}) : annulation")
        ssh(sc.hote, sc.annuler)
        return None
    premiers, variable, repare_a = {}, None, None
    try:
        while now() - t0 < sc.duree:
            t = now()
            for nom, expr in sig.items():
                if nom not in premiers and active(expr):
                    premiers[nom] = round(t - t0, 1)
                    log(f"   détecté par {nom} après {premiers[nom]:.0f} s")
                    if nom == "ia" and variable is None:
                        r = prom(f'max by (feature) (aiops_top_feature{{node="{sc.noeud}"}} == 1)')["result"]
                        variable = r[0]["metric"].get("feature") if r else None
                        log(f"   variable responsable selon l'IA : {variable}")
            if sc.reparation and t - t0 > 20:
                rc, out = ssh(sc.hote, sc.reparation)
                if out.strip().endswith("ok"):
                    repare_a = round(t - t0, 1)
                    log(f"   panne disparue après {repare_a:.0f} s")
                    break
            time.sleep(POLL)
    finally:
        t_fin = now()
        ssh(sc.hote, sc.annuler)     # toujours : remise dans l'état initial
    # Retour au calme : plus aucun signal (15 min max)
    t_calme = None
    while now() - t_fin < 900:
        if not any(active(e) for e in sig.values()):
            t_calme = round(now() - t0, 1)
            break
        time.sleep(POLL)
    agent = journal_agent(t0)
    res = {"numero": numero, "scenario": sc.id, "nom": sc.nom, "noeud": sc.noeud, "t0": t0, "t_fin_panne": t_fin,
           "duree_panne": round(t_fin - t0, 1), "detections": premiers, "variable_ia": variable,
           "variables_attendues": list(sc.variables), "ia_attendue": sc.ia_attendue,
           "action_attendue": sc.action_attendue, "repare_par_agent_s": repare_a, "retour_calme_s": t_calme,
           "agent": [{k: r.get(k) for k in ("t", "type", "alerte", "action", "decision", "detail", "code", "verification")}
                     for r in agent]}
    os.makedirs(RESULTATS, exist_ok=True)
    with open(os.path.join(RESULTATS, "injections.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(res, ensure_ascii=False) + "\n")
    decisions = [f"{r['action']}={r['decision']}" for r in agent if r.get("type") == "decision"]
    log(f"   agent : {', '.join(decisions) or 'aucune décision'} ; retour au calme après {t_calme} s")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seulement", help="scénarios séparés par des virgules (ordre conservé)")
    ap.add_argument("--liste", action="store_true")
    ap.add_argument("--pause", type=int, default=480, help="période calme après chaque panne (s)")
    ap.add_argument("--depuis", type=int, default=1, help="reprendre à la panne numéro N de l'ordre")
    a = ap.parse_args()
    if a.liste:
        for s in S.values():
            print(f"{s.id:<14} {s.nom}  (agent attendu : {s.action_attendue})")
        return
    ordre = a.seulement.split(",") if a.seulement else ORDRE
    inconnus = [o for o in ordre if o not in S]
    if inconnus:
        sys.exit(f"scénarios inconnus : {inconnus}")
    mode = prom("max(pfe_agent_mode)")["result"]
    log(f"Campagne de {len(ordre)} injections ; agent en mode {'ACTIF' if mode and mode[0]['value'][1] == '1' else 'CONSEIL'}")
    # Actions récentes de l'agent (reprise après interruption) : sa pause de 30 min est respectée
    derniere_action = {}
    t_serveur, t_local = now(), time.time()
    for r in journal_agent(t_serveur - 35 * 60):
        if r.get("type") == "decision" and r.get("decision") in ("executee", "simulee"):
            derniere_action[r["action"]] = max(derniere_action.get(r["action"], 0), r["t"] - t_serveur + t_local)
    if derniere_action:
        log(f"actions récentes de l'agent prises en compte : {', '.join(derniere_action)}")
    for i, sid in enumerate(ordre, 1):
        if i < a.depuis:
            continue
        sc = S[sid]
        try:
            attendre_calme(sc, derniere_action)
            res = injecter(sc, i)
        except Exception as exc:      # une erreur sur une panne n'arrête pas la campagne (la panne est déjà annulée)
            log(f"   ERREUR sur la panne {i} ({sid}) : {exc!r} ; annulation de sécurité puis suite")
            ssh(sc.hote, sc.annuler)
            res = None
        if res and sc.action_attendue != "aucune":
            derniere_action[sc.action_attendue] = time.time()
        log(f"   période calme de {a.pause // 60} min (sert aussi à mesurer les fausses alertes)")
        time.sleep(a.pause)
    log("Campagne terminée. Analyse : python eval/analyse.py")


if __name__ == "__main__":
    main()
