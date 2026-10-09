#!/usr/bin/env bash
# Déploie la supervision multi-nœuds. À lancer sur le SERVEUR DE SUPERVISION (54.37.13.100) :
#   curl -fsSL https://raw.githubusercontent.com/maktoufkarim6-spec/pfe-devsecops/main/infra/deploy.sh -o deploy.sh && bash deploy.sh
# Relançable sans risque : chaque étape ne change que ce qui diffère de l'état voulu.
set -euo pipefail
REPO=https://github.com/maktoufkarim6-spec/pfe-devsecops.git
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

EXTRA=()
read -rp "Adresse e-mail qui recevra les alertes (Entrée = pas d'e-mail pour l'instant) : " MAIL
if [ -n "$MAIL" ]; then
  read -rp "Adresse Gmail d'envoi : " FROM
  read -rsp "Mot de passe d'application Gmail (non affiché) : " PFE_SMTP_PASSWORD; echo
  export PFE_SMTP_PASSWORD
  EXTRA=(-e "alert_email_to=$MAIL" -e "alert_email_from=$FROM")
fi
read -rp "Lancer le déploiement ? (oui/non) " OK
[ "$OK" = "oui" ] || { echo "Abandon, rien n'a été modifié."; exit 1; }

echo "== 5/5 Déploiement : Ansible demande le mot de passe SSH puis celui de sudo =="
"$VENV/bin/ansible-playbook" site.yml --ask-pass --ask-become-pass "${EXTRA[@]}"

echo
echo "Grafana : http://$MONITORING_IP:3000   (utilisateur : admin)"
echo "Mot de passe admin Grafana : cat ~/.pfe-secrets/grafana-admin"
echo "Une fois Grafana vérifié, libérer la RAM du VPS applicatif :"
echo "  cd $SRC/infra/ansible && $VENV/bin/ansible-playbook retirer-ancienne-supervision.yml --ask-pass --ask-become-pass"
