"""Découverte du parc : explore les adresses de infra/parc.yml et classe chaque serveur (LECTURE SEULE).

    python decouvrir_parc.py                    rapport lisible
    python decouvrir_parc.py --json             rapport JSON
    python decouvrir_parc.py --metriques F      + métriques Prometheus (collecteur textfile de node-exporter)

États : vide (proposé comme nouveau nœud), supervision, controle (nœud de contrôle k3s), applicatif (agent k3s),
occupe (autre chose y tourne : jamais touché), incompatible (système, RAM, disque ou sudo), cle_refusee (port SSH
ouvert mais clé du parc pas encore posée : bash deploy.sh --cles), inaccessible (port SSH fermé), erreur.
Rien n'est jamais installé par ce script : l'équipement d'un serveur vide passe par deploy.sh --ajouter.
"""
import argparse
import concurrent.futures as cf
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys

import yaml

ICI = os.path.dirname(os.path.abspath(__file__))
INFRA = os.path.dirname(ICI)
MAX_ADRESSES = 1024              # garde-fou : jamais de balayage massif
PORTS_D_UN_SERVEUR_VIDE = {22, 53}   # SSH et résolveur local (systemd-resolved) : rien d'autre ne doit écouter
ETATS = ("vide", "supervision", "controle", "applicatif", "occupe", "incompatible", "cle_refusee",
         "inaccessible", "erreur")


def lire_conf(chemin):
    with open(chemin, encoding="utf-8") as f:
        conf = yaml.safe_load(f) or {}
    plages = conf.get("plages_autorisees") or []
    if not plages:
        raise ValueError("plages_autorisees vide : aucune adresse à explorer")
    reseaux = [ipaddress.ip_network(str(p), strict=False) for p in plages]
    for r in reseaux:
        if r.version != 4 or r.prefixlen < 22:
            raise ValueError(f"plage refusée : {r} (IPv4, /22 au plus large)")
    user = str(conf.get("utilisateur_ssh", "debian"))
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
        raise ValueError(f"utilisateur_ssh invalide : {user!r}")
    conf["_reseaux"], conf["utilisateur_ssh"] = reseaux, user
    conf["port_ssh"] = int(conf.get("port_ssh", 22))
    return conf


def adresses(reseaux):
    vues, out = set(), []
    for r in reseaux:
        for ip in ([r.network_address] if r.num_addresses == 1 else r.hosts()):
            if str(ip) not in vues:
                vues.add(str(ip))
                out.append(str(ip))
            if len(out) > MAX_ADRESSES:
                raise ValueError(f"plus de {MAX_ADRESSES} adresses : réduire plages_autorisees")
    return out


def inventaire_connu(*chemins):
    """Adresses déjà dans l'inventaire Ansible (ansible_host=...) -> nom."""
    connus = {}
    for chemin in chemins:
        if chemin and os.path.exists(chemin):
            for ligne in open(chemin, encoding="utf-8"):
                m = re.match(r"^\s*([A-Za-z0-9_.-]+)\s.*\bansible_host=([0-9.]+)", ligne)
                if m and not ligne.lstrip().startswith("#"):
                    connus[m.group(2)] = m.group(1)
    return connus


def port_ouvert(ip, port, delai=2.0):
    try:
        with socket.create_connection((ip, port), timeout=delai):
            return True
    except OSError:
        return False


def sonder(ip, conf, cle, known_hosts, executer=subprocess.run):
    """Sonde lecture seule par la clé du parc. Renvoie (dict sonde, erreur)."""
    cmd = ["ssh", "-i", cle, "-p", str(conf["port_ssh"]), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
           "-o", "PasswordAuthentication=no", "-o", "ConnectTimeout=8",
           "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={known_hosts}",
           f"{conf['utilisateur_ssh']}@{ip}", "bash -s"]
    with open(os.path.join(ICI, "sonde_serveur.sh"), "rb") as f:
        script = f.read()
    try:
        p = executer(cmd, input=script, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None, "délai dépassé"
    if p.returncode == 255:
        err = p.stderr.decode(errors="replace")
        return None, "cle_refusee" if "Permission denied" in err else f"ssh : {err.strip()[-200:]}"
    try:
        return json.loads(p.stdout.decode(errors="replace").strip().splitlines()[-1]), None
    except (ValueError, IndexError):
        return None, "réponse de la sonde illisible"


def classer(s, conf):
    """Fonction pure : état et raison à partir de la sonde."""
    conteneurs = set((s.get("conteneurs") or "").split())
    if "mon-prometheus" in conteneurs:
        return "supervision", "supervision de la plateforme (mon-prometheus)"
    if s.get("k3s_serveur"):
        return "controle", f"nœud de contrôle k3s {s.get('k3s_version', '')}".strip()
    if s.get("k3s_agent"):
        return "applicatif", f"agent k3s {s.get('k3s_version', '')}".strip()
    systeme = (s.get("systeme") or "").lower()
    if systeme not in [x.lower() for x in conf.get("systemes", [])]:
        return "incompatible", f"système {systeme or 'inconnu'} (attendus : {', '.join(conf.get('systemes', []))})"
    if int(s.get("ram_mo") or 0) < int(conf.get("ram_min_mo", 0)):
        return "incompatible", f"{s.get('ram_mo')} Mo de RAM (minimum {conf.get('ram_min_mo')})"
    if int(s.get("disque_libre_go") or 0) < int(conf.get("disque_libre_min_go", 0)):
        return "incompatible", f"{s.get('disque_libre_go')} Go libres (minimum {conf.get('disque_libre_min_go')})"
    if not s.get("sudo"):
        return "incompatible", "sudo sans mot de passe indisponible pour le compte SSH"
    if conteneurs:
        return "occupe", f"conteneurs en cours : {', '.join(sorted(conteneurs))}"
    if s.get("pfe_runbook"):
        return "occupe", "déjà équipé par la plateforme (pfe-runbook) mais sans k3s"
    ports = {int(p) for p in (s.get("services_ecoute") or "").split() if p.isdigit()}
    autres = sorted(ports - PORTS_D_UN_SERVEUR_VIDE)
    if autres:
        return "occupe", f"services en écoute : ports {', '.join(map(str, autres))}"
    return "vide", f"{s.get('systeme')}, {s.get('cpu')} CPU, {s.get('ram_mo')} Mo, {s.get('disque_libre_go')} Go libres"


def empreinte(ip, known_hosts):
    try:
        p = subprocess.run(["ssh-keygen", "-l", "-F", ip, "-f", known_hosts], capture_output=True, text=True, timeout=10)
        m = re.search(r"(SHA256:\S+)", p.stdout)
        return m.group(1) if m else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def explorer(conf, cle, known_hosts, connus, executer=subprocess.run, ouvert=port_ouvert):
    def un(ip):
        r = {"adresse": ip, "inventaire": connus.get(ip, "")}
        if not ouvert(ip, conf["port_ssh"]):
            return {**r, "etat": "inaccessible", "raison": f"port {conf['port_ssh']} fermé"}
        s, err = sonder(ip, conf, cle, known_hosts, executer)
        if err == "cle_refusee":
            return {**r, "etat": "cle_refusee", "raison": "clé du parc pas encore posée (bash deploy.sh --cles)"}
        if err:
            return {**r, "etat": "erreur", "raison": err}
        etat, raison = classer(s, conf)
        return {**r, "etat": etat, "raison": raison, "sonde": s, "empreinte": empreinte(ip, known_hosts)}

    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        return list(pool.map(un, adresses(conf["_reseaux"])))


def prochain_nom(connus_noms, deja):
    n = 1
    while f"app-{n}" in connus_noms or f"app-{n}" in deja:
        n += 1
    return f"app-{n}"


def propositions(resultats, connus):
    """Lignes d'inventaire proposées pour les serveurs vides qui n'y sont pas encore."""
    noms, out = set(connus.values()), []
    for r in resultats:
        if r["etat"] == "vide" and not r["inventaire"]:
            nom = prochain_nom(noms, set())
            noms.add(nom)
            out.append(f"{nom} ansible_host={r['adresse']} node_name={nom} node_app=true")
    return out


def plan_parc_neuf(resultats, soi_ip, soi_sonde, conf, date=""):
    """Parc ENTIÈREMENT NEUF : rôles attribués automatiquement. Ce serveur (soi) devient la supervision ; parmi les
    autres serveurs vides, celui qui a le plus de RAM devient le nœud de contrôle, les autres des nœuds applicatifs.
    Renvoie {statut: pret | cles | erreur, message, inventaire, plan}. Fonction pure (aucun accès réseau)."""
    etat_soi, raison_soi = classer(soi_sonde, conf)
    if etat_soi != "vide":
        return {"statut": "erreur", "message": f"ce serveur ({soi_ip}) n'est pas vide : {raison_soi}", "inventaire": "", "plan": []}
    autres = [r for r in resultats if r["adresse"] != soi_ip]
    sans_cle = [r["adresse"] for r in autres if r["etat"] == "cle_refusee"]
    vides = [r for r in autres if r["etat"] == "vide"]
    ignores = [f"{r['adresse']} ({r['etat']} : {r['raison']})" for r in autres if r["etat"] not in ("vide", "cle_refusee")]
    entete = (f"# parc-neuf{'-provisoire' if sans_cle else ''} : généré par deploy.sh --nouveau-parc {date}\n"
              "# Inventaire propre à ce serveur de supervision : il remplace infra/ansible/inventory.ini (premier parc).\n")
    sup = f"[monitoring]\nsupervision ansible_host={soi_ip} ansible_connection=local node_name=monitoring node_app=false\n"
    pied = f"\n[all:vars]\nansible_user={conf.get('utilisateur_ssh', 'debian')}\n"
    if sans_cle:
        noeuds = [f"app-{i} ansible_host={ip} node_name=app-{i} node_app=true" for i, ip in enumerate(sans_cle + [r["adresse"] for r in vides], 1)]
        return {"statut": "cles", "plan": [], "inventaire": entete + sup + "\n[nodes]\n" + "\n".join(noeuds) + "\n" + pied,
                "message": f"clé du parc à poser d'abord sur : {', '.join(sans_cle)} (bash deploy.sh --cles, mot de passe demandé "
                           "une fois), puis relancer bash deploy.sh --nouveau-parc"}
    if not vides:
        return {"statut": "erreur", "message": "aucun autre serveur vide dans plages_autorisees : il faut au moins un nœud de contrôle"
                + (f" (ignorés : {'; '.join(ignores)})" if ignores else ""), "inventaire": "", "plan": []}
    ordre = sorted(vides, key=lambda r: (-int(r["sonde"].get("ram_mo") or 0), [int(x) for x in r["adresse"].split(".")]))
    plan = [{"nom": "supervision", "adresse": soi_ip, "role": "supervision (ce serveur)"}]
    lignes = []
    for i, r in enumerate(ordre, 1):
        controle = i == 1
        lignes.append(f"app-{i} ansible_host={r['adresse']} node_name=app-{i} node_app=true" + (" k3s_server=true" if controle else ""))
        plan.append({"nom": f"app-{i}", "adresse": r["adresse"],
                     "role": ("nœud de contrôle : k3s, Argo CD, Jenkins, SonarQube" if controle else "nœud applicatif")
                     + f" ({r['sonde'].get('ram_mo')} Mo)"})
    return {"statut": "pret", "plan": plan, "message": ("ignorés : " + "; ".join(ignores)) if ignores else "",
            "inventaire": entete + sup + "\n[nodes]\n" + "\n".join(lignes) + "\n" + pied}


def metriques(resultats):
    lignes = ["# HELP pfe_parc_serveur 1 par adresse explorée ; etat : vide, applicatif, occupe, inaccessible...",
              "# TYPE pfe_parc_serveur gauge"]
    for r in resultats:
        lignes.append(f'pfe_parc_serveur{{adresse="{r["adresse"]}",etat="{r["etat"]}",inventaire="{r["inventaire"]}"}} 1')
    lignes += ["# HELP pfe_parc_serveurs_vides Serveurs vides proposés comme nouveaux nœuds", "# TYPE pfe_parc_serveurs_vides gauge",
               f"pfe_parc_serveurs_vides {sum(r['etat'] == 'vide' and not r['inventaire'] for r in resultats)}"]
    return "\n".join(lignes) + "\n"


def ecrire_atomique(chemin, contenu):
    tmp = f"{chemin}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(contenu)
    os.chmod(tmp, 0o644)
    os.replace(tmp, chemin)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conf", default=os.path.join(INFRA, "parc.yml"))
    ap.add_argument("--cle", default=os.path.expanduser("~/.ssh/pfe_parc"))
    ap.add_argument("--known-hosts", default=os.path.expanduser("~/.pfe-parc/known_hosts"))
    ap.add_argument("--inventaire", action="append",
                    default=[os.path.join(INFRA, "ansible", "inventory.ini"), os.path.expanduser("~/.pfe-parc/inventaire.ini")])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--metriques")
    ap.add_argument("--parc-neuf", metavar="ADRESSE_DE_CE_SERVEUR", help="plan d'un parc entièrement neuf (JSON)")
    ap.add_argument("--sonde-locale", help="résultat de sonde_serveur.sh exécutée sur ce serveur (avec --parc-neuf)")
    a = ap.parse_args(argv)
    conf = lire_conf(a.conf)
    os.makedirs(os.path.dirname(a.known_hosts), mode=0o700, exist_ok=True)
    connus = inventaire_connu(*a.inventaire)
    if a.parc_neuf:
        ipaddress.ip_address(a.parc_neuf)
        if not any(ipaddress.ip_address(a.parc_neuf) in r for r in conf["_reseaux"]):
            raise ValueError(f"{a.parc_neuf} (ce serveur) n'est pas dans plages_autorisees")
        res = explorer(conf, a.cle, a.known_hosts, {})
        with open(a.sonde_locale, encoding="utf-8") as f:
            soi = json.load(f)
        import datetime
        print(json.dumps(plan_parc_neuf(res, a.parc_neuf, soi, conf, datetime.date.today().isoformat()), ensure_ascii=False))
        return 0
    res = explorer(conf, a.cle, a.known_hosts, connus)
    if a.metriques:
        ecrire_atomique(a.metriques, metriques(res))
    if a.json:
        print(json.dumps({"serveurs": res, "propositions": propositions(res, connus)}, ensure_ascii=False, indent=2))
        return 0
    print(f"{'ADRESSE':16} {'INVENTAIRE':12} {'ÉTAT':13} DÉTAIL")
    for r in res:
        print(f"{r['adresse']:16} {r['inventaire'] or '-':12} {r['etat']:13} {r['raison']}")
    prop = propositions(res, connus)
    if prop:
        print("\nServeurs vides proposés comme nouveaux nœuds applicatifs (rien n'est installé sans votre accord) :")
        for p, r in zip(prop, [r for r in res if r["etat"] == "vide" and not r["inventaire"]]):
            print(f"  {p}\n    empreinte SSH : {r.get('empreinte') or 'inconnue'}  ->  bash deploy.sh --ajouter {r['adresse']}")
    else:
        print("\nAucun serveur vide à ajouter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
