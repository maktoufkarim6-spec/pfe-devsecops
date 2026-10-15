#!/usr/bin/env bash
# Déploie la supervision multi-nœuds. À lancer sur le SERVEUR DE SUPERVISION (54.37.13.100) :
#   curl -fsSL https://raw.githubusercontent.com/maktoufkarim6-spec/pfe-devsecops/main/infra/deploy.sh -o deploy.sh && bash deploy.sh
# Options Ansible en plus (ex. agent en mode actif) : bash deploy.sh -e remediation_mode=actif
# Connexion par clé SSH (une fois, voir docs/CLES-SSH.md) :
#   bash deploy.sh --cles                  pose la clé du parc et celle de votre PC (mot de passe encore accepté)
#   bash deploy.sh --fermer-mots-de-passe  refuse ensuite les mots de passe SSH sur tout le parc
# Relançable sans risque : chaque étape ne change que ce qui diffère de l'état voulu.
set -euo pipefail
MODE=deploiement
case "${1:-}" in
  --cles) MODE=cles; shift ;;
  --fermer-mots-de-passe) MODE=fermer; shift ;;
esac
REPO=${PFE_REPO:-https://github.com/maktoufkarim6-spec/pfe-devsecops.git}   # autre projet : PFE_REPO=<dépôt> bash deploy.sh
SRC="$HOME/pfe-devsecops"
VENV="$HOME/.pfe-ansible"

echo "== 1/5 Outils (git, Python, sshpass) =="
sudo apt-get update -q
sudo apt-get install -y -q git python3-venv sshpass >/dev/null

echo "== 2/5 Code du dépôt =="
if [ -d "$SRC/.git" ]; then git -C "$SRC" pull -q --ff-only; else git clone -q --depth 1 "$REPO" "$SRC"; fi
cd "$SRC/infra/ansible"

echo "== 3/5 Ansible (environnement isolé dans $VENV) =="
[ -x "$VENV/bin/ansible-playbook" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q "ansible-core==2.18.*"
"$VENV/bin/ansible-galaxy" collection install -r requirements.yml >/dev/null

echo "== 4/5 État actuel du serveur de supervision =="
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || echo "(Docker absent : il sera installé)"
free -h | head -2
df -h / | tail -1
mkdir -p ~/.ssh && chmod 700 ~/.ssh
for h in $(grep -v 'ansible_connection=local' inventory.ini | grep -o 'ansible_host=[0-9.]*' | cut -d= -f2); do
  ssh-keygen -F "$h" >/dev/null || ssh-keyscan -H "$h" >> ~/.ssh/known_hosts 2>/dev/null
done
MONITORING_IP=$(grep 'ansible_connection=local' inventory.ini | grep -o 'ansible_host=[0-9.]*' | cut -d= -f2)

# Connexion aux nœuds : par la clé du parc si elle est acceptée partout, sinon par mot de passe
CLE="$HOME/.ssh/pfe_parc"
CONNEXION=(--ask-pass)
if [ -f "$CLE" ]; then
  tous_ok=1
  for h in $(grep -v 'ansible_connection=local' inventory.ini | grep -v '^[[:space:]]*#' | grep -o 'ansible_host=[0-9.]*' | cut -d= -f2); do
    if ssh -i "$CLE" -o BatchMode=yes -o PasswordAuthentication=no -o IdentitiesOnly=yes -o ConnectTimeout=10 "debian@$h" true 2>/dev/null; then echo "  $h : connexion par clé OK"
    else echo "  $h : clé refusée, mot de passe nécessaire"; tous_ok=0; fi
  done
  [ "$tous_ok" = 1 ] && CONNEXION=()
fi
# sudo sans mot de passe (cas des VPS OVH) : rien à demander. -k : ignore le sudo de l'étape 1 encore en cache.
# Un nœud qui exigerait un mot de passe sudo : PFE_ASK_BECOME=1 bash deploy.sh
BECOME=(--ask-become-pass)
[ "${PFE_ASK_BECOME:-0}" != 1 ] && sudo -k && sudo -n true 2>/dev/null && BECOME=()

EXTRA=()
case "$MODE" in
  cles)
    ADMIN="$HOME/.pfe-secrets/cles-admin.pub"
    mkdir -p "$HOME/.pfe-secrets" && chmod 700 "$HOME/.pfe-secrets"
    echo "Clé publique de votre PC : sur le PC, « type \$env:USERPROFILE\.ssh\id_ed25519.pub » (PowerShell)."
    [ -s "$ADMIN" ] && { echo "Déjà enregistrée(s) :"; cut -d' ' -f3- "$ADMIN"; }
    read -rp "Coller une clé publique (ssh-ed25519 AAAA...), ou Entrée pour ne rien ajouter : " K
    if [ -n "$K" ]; then
      if printf '%s
' "$K" | ssh-keygen -l -f - >/dev/null 2>&1; then
        grep -qxF "$K" "$ADMIN" 2>/dev/null || printf '%s
' "$K" >> "$ADMIN"; chmod 600 "$ADMIN"
        echo "Clé enregistrée."
      else echo "Ce n'est pas une clé publique SSH valide : rien n'a été ajouté."; exit 1; fi
    fi
    [ -s "$ADMIN" ] || echo "Attention : aucune clé de PC. La fermeture des mots de passe sera refusée."
    PLAYBOOK=cles-ssh.yml ;;
  fermer)
    echo "Les mots de passe SSH vont être REFUSÉS sur tous les serveurs où la clé fonctionne."
    echo "Avant : vérifiez depuis votre PC que « ssh debian@<IP> » se connecte SANS demander de mot de passe,"
    echo "pour CHAQUE serveur. Gardez cette session ouverte jusqu'à la fin."
    read -rp "Tapez FERMER pour continuer : " OK
    [ "$OK" = "FERMER" ] || { echo "Abandon, rien n'a été modifié."; exit 1; }
    EXTRA=(-e fermer_mots_de_passe=true)
    PLAYBOOK=cles-ssh.yml ;;
  deploiement)
    read -rp "Adresse e-mail qui recevra les alertes (Entrée = pas d'e-mail pour l'instant) : " MAIL
    if [ -n "$MAIL" ]; then
      read -rp "Adresse Gmail d'envoi : " FROM
      read -rsp "Mot de passe d'application Gmail (non affiché) : " PFE_SMTP_PASSWORD; echo
      export PFE_SMTP_PASSWORD
      EXTRA=(-e "alert_email_to=$MAIL" -e "alert_email_from=$FROM")
    fi
    PLAYBOOK=site.yml ;;
esac
read -rp "Lancer ($PLAYBOOK) ? (oui/non) " OK
[ "$OK" = "oui" ] || { echo "Abandon, rien n'a été modifié."; exit 1; }

echo "== 5/5 Déploiement ${CONNEXION:+(Ansible demande le mot de passe SSH)} ${BECOME:+(et celui de sudo)} =="
"$VENV/bin/ansible-playbook" "$PLAYBOOK" "${CONNEXION[@]}" "${BECOME[@]}" "${EXTRA[@]}" "$@"

echo
echo "Grafana : http://$MONITORING_IP:3000   (utilisateur : admin)"
echo "Mot de passe admin Grafana : cat ~/.pfe-secrets/grafana-admin"
echo "Une fois Grafana vérifié, libérer la RAM du VPS applicatif :"
echo "  cd $SRC/infra/ansible && $VENV/bin/ansible-playbook retirer-ancienne-supervision.yml --ask-pass --ask-become-pass"
