"""Appels à l'API GitHub pour l'installation d'un parc pour un autre compte (bibliothèque standard seulement).

    python github_parc.py utilisateur                       compte du jeton (GITHUB_TOKEN)
    python github_parc.py fork <propriétaire>/<dépôt>       fork dans le compte du jeton (rien si déjà là) -> URL
    python github_parc.py webhook <compte>/<dépôt> <url>    webhook « push » vers Jenkins (rien s'il existe déjà)

Jeton lu dans la variable GITHUB_TOKEN, jamais en argument ni dans un journal. PFE_GITHUB_API : autre adresse
de l'API (tests).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("PFE_GITHUB_API", "https://api.github.com").rstrip("/")
DEPOT = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
URL_JENKINS = re.compile(r"^http://[0-9]{1,3}(\.[0-9]{1,3}){3}:[0-9]{2,5}/github-webhook/$")


class Erreur(Exception):
    pass


def appel(methode, chemin, corps=None, attendre=(200,)):
    jeton = os.environ.get("GITHUB_TOKEN", "")
    if not jeton:
        raise Erreur("GITHUB_TOKEN absent")
    req = urllib.request.Request(f"{API}{chemin}", method=methode,
                                 data=json.dumps(corps).encode() if corps is not None else None,
                                 headers={"Authorization": f"Bearer {jeton}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pfe-plateforme"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code, texte = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, texte = e.code, e.read().decode(errors="replace")
    except OSError as e:
        raise Erreur(f"GitHub injoignable : {e}") from e
    if code not in attendre:
        message = ""
        try:
            message = json.loads(texte).get("message", "")
        except ValueError:
            pass
        aide = {401: "jeton refusé", 403: "droits insuffisants (jeton classique avec repo et admin:repo_hook)",
                404: "introuvable ou droits insuffisants"}.get(code, "")
        raise Erreur(f"GitHub {methode} {chemin} : HTTP {code} {aide} {message}".strip())
    return code, (json.loads(texte) if texte else {})


def utilisateur():
    return appel("GET", "/user")[1]["login"]


def fork(source, delai=180, pause=5):
    if not DEPOT.match(source):
        raise Erreur(f"dépôt source invalide : {source!r}")
    moi = utilisateur()
    nom = source.split("/")[1]
    if source.split("/")[0].lower() == moi.lower():
        return f"https://github.com/{source}"          # c'est déjà son dépôt
    try:
        appel("GET", f"/repos/{moi}/{nom}")
        return f"https://github.com/{moi}/{nom}"        # fork déjà fait lors d'un passage précédent
    except Erreur:
        pass
    appel("POST", f"/repos/{source}/forks", {"default_branch_only": True}, attendre=(202,))
    fin = time.time() + delai
    while time.time() < fin:                             # GitHub crée le fork de façon asynchrone
        try:
            appel("GET", f"/repos/{moi}/{nom}/branches/main")
            return f"https://github.com/{moi}/{nom}"
        except Erreur:
            time.sleep(pause)
    raise Erreur(f"fork {moi}/{nom} pas prêt après {delai} s")


def webhook(depot, url):
    if not DEPOT.match(depot) or not URL_JENKINS.match(url):
        raise Erreur(f"paramètres invalides : {depot!r} {url!r}")
    _, hooks = appel("GET", f"/repos/{depot}/hooks")
    if any(h.get("config", {}).get("url") == url for h in hooks):
        return "existe déjà"
    appel("POST", f"/repos/{depot}/hooks", {"name": "web", "active": True, "events": ["push"],
                                             "config": {"url": url, "content_type": "json", "insecure_ssl": "0"}},
          attendre=(201,))
    return "créé"


def main(argv):
    try:
        if argv[:1] == ["utilisateur"]:
            print(utilisateur())
        elif argv[:1] == ["fork"] and len(argv) == 2:
            print(fork(argv[1]))
        elif argv[:1] == ["webhook"] and len(argv) == 3:
            print(webhook(argv[1], argv[2]))
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except Erreur as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
