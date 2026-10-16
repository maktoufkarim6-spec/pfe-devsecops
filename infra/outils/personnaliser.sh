#!/usr/bin/env bash
# Adapte une COPIE (fork) du dépôt à un autre propriétaire : son espace Docker Hub, ses serveurs, l'adresse de
# son dépôt. Lancé par deploy.sh --nouveau-parc (installation pour un autre compte), puis commité dans son fork.
#
#   personnaliser.sh <espace_docker_hub> <adresse_du_depot> <ip1> [ip2 ...]
#
# Ce qui change : espace Docker Hub où Jenkins publie (cobaye et applications), adresses explorées par la
# découverte (infra/parc.yml), adresse du dépôt (liens, clonage, Argo CD, Jenkins). Les lignes image actuelles
# restent celles du premier parc (images publiques) : l'application tourne tout de suite, et son premier build
# les remplace par les images de son propre compte.
set -euo pipefail
RACINE=$(cd "$(dirname "$0")/../.." && pwd)
erreur() { echo "ERREUR : $*" >&2; exit 2; }

REG=${1:-}; DEPOT=${2:-}; shift 2 || true
[[ "$REG" =~ ^[a-z0-9]([a-z0-9_-]{2,28}[a-z0-9])?$ ]] || erreur "espace Docker Hub invalide : '$REG'"
[[ "$DEPOT" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || erreur "adresse du dépôt invalide : '$DEPOT' (https://github.com/<compte>/<dépôt>)"
[ $# -ge 1 ] || erreur "au moins une adresse de serveur"
for ip in "$@"; do
  [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || erreur "adresse IPv4 invalide : '$ip'"
  for o in "${BASH_REMATCH[@]:1}"; do (( o <= 255 )) || erreur "adresse IPv4 invalide : '$ip'"; done
done
ANCIEN=$(sed -n "s/^ *IMAGE_SERVER *= *'\([^/]*\)\/.*/\1/p" "$RACINE/Jenkinsfile")
[ -n "$ANCIEN" ] || erreur "espace Docker Hub actuel introuvable dans le Jenkinsfile"

cd "$RACINE"
# 1. Espace Docker Hub où Jenkins publie
sed -i -E "s#(IMAGE_(SERVER|CLIENT) *= *')$ANCIEN/#\1$REG/#" Jenkinsfile
sed -i -E "s#(REGISTRE *= *')$ANCIEN'#\1$REG'#" plateforme/Jenkinsfile.applications
sed -i -E "s#^REGISTRE=\\\$\{REGISTRE:-$ANCIEN\}#REGISTRE=\${REGISTRE:-$REG}#" plateforme/ci/construire.sh
for f in applications/*/values.yaml; do
  [ -f "$f" ] && sed -i -E "s#^(      image: )$ANCIEN/#\1$REG/#" "$f"   # construire.image seulement (destination des builds)
done
# 2. Adresse du dépôt
sed -i -E "s#^repo_web: [^ ]+#repo_web: $DEPOT#" infra/ansible/group_vars/all.yml
sed -i -E "s#^evaluation_url: [^ ]+#evaluation_url: \"\"#" infra/ansible/group_vars/all.yml
# 3. Serveurs explorés par la découverte
{
  sed -n '1,/^plages_autorisees:/p' infra/parc.yml
  for ip in "$@"; do echo "  - $ip/32"; done
  echo
  sed -n '/^utilisateur_ssh:/,$p' infra/parc.yml
} > infra/parc.yml.nouveau && mv infra/parc.yml.nouveau infra/parc.yml

# Contrôles : tout est bien écrit
grep -q "IMAGE_SERVER = '$REG/" Jenkinsfile && grep -q "IMAGE_CLIENT = '$REG/" Jenkinsfile \
  && grep -q "REGISTRE     = '$REG'" plateforme/Jenkinsfile.applications \
  && grep -q "^REGISTRE=\${REGISTRE:-$REG}" plateforme/ci/construire.sh \
  && grep -Eq "^repo_web: $DEPOT( |\$)" infra/ansible/group_vars/all.yml \
  || erreur "personnalisation incomplète"
for ip in "$@"; do grep -q "^  - $ip/32\$" infra/parc.yml || erreur "parc.yml : $ip absente"; done
! grep -q "^  - .*/32 *#" infra/parc.yml || erreur "parc.yml : anciennes adresses restantes"
echo "personnalisé : Docker Hub $REG, dépôt $DEPOT, serveurs $*"
