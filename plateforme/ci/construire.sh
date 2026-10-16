#!/usr/bin/env bash
# Préparation et mise à jour GitOps du pipeline commun des applications (plateforme/Jenkinsfile.applications).
# Toute la lecture de applications/<nom>/values.yaml et tous les contrôles sont ici, testables hors Jenkins.
#
#   construire.sh verifier <application>          contrôle la section « source » et les composants à construire ;
#                                                 écrit .ci/source.env et .ci/composants.txt
#   construire.sh maj <application> <tag>         écrit <image>:<tag> pour chaque composant construit
#
# YAML lu par yq (mikefarah, version figée). Jenkins n'a pas yq : on passe par son image Docker, le fichier
# étant transmis sur l'entrée standard (aucun dossier partagé entre Jenkins et Docker n'est nécessaire).
# Variables : YQ (commande yq, défaut : image Docker), REGISTRE (espace Docker Hub autorisé, défaut kariimm557).
set -euo pipefail

YQ=${YQ:-"docker run --rm -i mikefarah/yq:4.44.3"}
REGISTRE=${REGISTRE:-kariimm557}
RACINE=$(cd "$(dirname "$0")/../.." && pwd)

erreur() { echo "ERREUR : $*" >&2; exit 2; }
yq_lire() { $YQ "$1" < "$FICHIER"; }       # $1 : expression yq, résultat sur la sortie standard

charger() {
  APP=${1:-}
  [[ "$APP" =~ ^[a-z][a-z0-9-]{1,30}[a-z0-9]$ ]] || erreur "nom d'application invalide : '$APP'"
  FICHIER=$RACINE/applications/$APP/values.yaml
  [ -f "$FICHIER" ] || erreur "application inconnue : applications/$APP/values.yaml absent"
}

sur() {   # $1 valeur, $2 description, $3 expression régulière ; refus de « .. » et d'un « - » initial
  [[ "$1" =~ $3 ]] && [[ "$1" != *..* ]] && [[ "$1" != -* ]] || erreur "$2 invalide : '$1'"
}

verifier() {
  charger "$1"
  local depot branche sonar
  depot=$(yq_lire '.source.depot // ""')
  branche=$(yq_lire '.source.branche // "main"')
  sonar=$(yq_lire '.source.sonar // "bloquant"')
  [ -n "$depot" ] || erreur "$APP : pas de section source.depot (application à image déjà publiée : rien à construire)"
  sur "$depot" "source.depot" '^https://[a-z0-9.-]+(:[0-9]{1,5})?/[A-Za-z0-9_.~/-]+$'
  sur "$branche" "source.branche" '^[A-Za-z0-9._/-]{1,100}$'
  [[ "$sonar" =~ ^(bloquant|informatif)$ ]] || erreur "source.sonar : bloquant ou informatif (reçu '$sonar')"

  # Une ligne par composant à construire : nom|contexte|dockerfile|image|cible_tests
  local lignes
  lignes=$(yq_lire '.composants[] | select(has("construire")) | [.nom, (.construire.contexte // "."), (.construire.dockerfile // "Dockerfile"), (.construire.image // ""), (.construire.cible_tests // "")] | join("|")')
  [ -n "$lignes" ] || erreur "$APP : aucun composant avec « construire »"
  mkdir -p "$RACINE/.ci"
  : > "$RACINE/.ci/composants.txt"
  local nom ctx df img tests vus=" "
  while IFS='|' read -r nom ctx df img tests; do
    sur "$nom" "nom de composant" '^[a-z][a-z0-9-]{0,30}[a-z0-9]$'
    [[ "$vus" != *" $nom "* ]] || erreur "composant en double : $nom"; vus+="$nom "
    [ "$ctx" = "." ] || sur "$ctx" "$nom : construire.contexte" '^[A-Za-z0-9._][A-Za-z0-9._/-]*$'
    sur "$df" "$nom : construire.dockerfile" '^[A-Za-z0-9._][A-Za-z0-9._/-]*$'
    # Image poussée seulement dans l'espace Docker Hub de la plateforme, sans tag (le pipeline le pose)
    img=${img#docker.io/}
    sur "$img" "$nom : construire.image (espace $REGISTRE, sans tag)" "^$REGISTRE/[a-z0-9]([a-z0-9._-]{0,126}[a-z0-9])?$"
    [ -z "$tests" ] || sur "$tests" "$nom : construire.cible_tests" '^[A-Za-z0-9._-]{1,64}$'
    echo "$nom|$ctx|$df|$img|$tests" >> "$RACINE/.ci/composants.txt"
  done <<< "$lignes"

  # Valeurs déjà contrôlées : écrites entre apostrophes pour être relues par « . » sans interprétation
  {
    echo "SOURCE_DEPOT='$depot'"
    echo "SOURCE_BRANCHE='$branche'"
    echo "SONAR_MODE='$sonar'"
  } > "$RACINE/.ci/source.env"
  echo "$APP : source $depot ($branche), SonarQube $sonar"
  sed 's/|/  /g; s/^/  à construire : /' "$RACINE/.ci/composants.txt"
}

maj() {
  charger "$1"
  local tag=${2:-}
  [[ "$tag" =~ ^[0-9]+-[0-9a-f]{7,40}$ ]] || erreur "tag invalide : '$tag' (attendu <build>-<commit>)"
  [ -s "$RACINE/.ci/composants.txt" ] || erreur "lancer d'abord : construire.sh verifier $APP"
  local nom img tmp
  tmp=$(mktemp)
  cp "$FICHIER" "$tmp"
  while IFS='|' read -r nom _ _ img _; do
    sur "$nom" "nom de composant" '^[a-z][a-z0-9-]{0,30}[a-z0-9]$'
    sur "$img" "$nom : image" "^$REGISTRE/[a-z0-9]([a-z0-9._-]{0,126}[a-z0-9])?$"
    # Seule la ligne « image: » du composant change (indentation et commentaire conservés : historique Git
    # lisible). yq donne son numéro de ligne ; valeurs déjà contrôlées (aucun caractère spécial possible).
    local n
    # --header-preprocess=false : sinon les commentaires d'en-tête du fichier décalent les numéros de ligne
    n=$($YQ --header-preprocess=false ".composants[] | select(.nom == \"$nom\") | .image | line" < "$tmp")
    [[ "$n" =~ ^[1-9][0-9]*$ ]] || erreur "$nom : clé image introuvable dans values.yaml"
    sed -n "${n}p" "$tmp" | grep -Eq '^[[:space:]]*(- )?image:[[:space:]]' \
      || erreur "$nom : la clé image doit être seule sur sa ligne (ligne $n : $(sed -n "${n}p" "$tmp"))"
    sed -E -i "${n}s#^([[:space:]]*(- )?image:[[:space:]]*)[^[:space:]\#]+#\1docker.io/$img:$tag#" "$tmp"
    grep -qF "docker.io/$img:$tag" "$tmp" || erreur "$nom : image non écrite dans values.yaml"
  done < "$RACINE/.ci/composants.txt"
  mv "$tmp" "$FICHIER"
  echo "$APP : images $tag écrites dans applications/$APP/values.yaml"
}

# ------------------------------------------------------------------ étapes Docker (après « verifier »)
# Lisent .ci/source.env et .ci/composants.txt ; le code source est cloné dans .ci/src.
lire_ci() {
  [ -s "$RACINE/.ci/composants.txt" ] && [ -s "$RACINE/.ci/source.env" ] || erreur "lancer d'abord : construire.sh verifier <application>"
  # shellcheck source=/dev/null
  . "$RACINE/.ci/source.env"
  SRC=$RACINE/.ci/src
}
tag() { cat "$RACINE/.ci/tag"; }

source_() {   # clone de la branche, tag <n° de build>-<commit court>
  lire_ci
  local build=${BUILD_NUMBER:-0}
  [[ "$build" =~ ^[0-9]+$ ]] || erreur "BUILD_NUMBER invalide"
  rm -rf "$SRC"
  git clone --quiet --depth 1 --branch "$SOURCE_BRANCHE" -- "$SOURCE_DEPOT" "$SRC"
  echo "$build-$(git -C "$SRC" rev-parse --short=7 HEAD)" > "$RACINE/.ci/tag"
  echo "code source : $SOURCE_DEPOT ($SOURCE_BRANCHE), commit $(git -C "$SRC" rev-parse --short=7 HEAD), tag $(tag)"
  local nom ctx df
  while IFS='|' read -r nom ctx df _ _; do
    [ -f "$SRC/$ctx/$df" ] || erreur "$nom : $ctx/$df introuvable dans le dépôt source"
  done < "$RACINE/.ci/composants.txt"
}

tests() {   # étape du Dockerfile qui lance les tests : un test en échec arrête le pipeline
  lire_ci
  local nom ctx df tests n=0
  while IFS='|' read -r nom ctx df _ tests; do
    [ -n "$tests" ] || continue
    echo "== tests de $nom (étape $tests du Dockerfile)"
    docker build --target "$tests" -f "$SRC/$ctx/$df" "$SRC/$ctx"
    n=$((n + 1))
  done < "$RACINE/.ci/composants.txt"
  [ "$n" -gt 0 ] || echo "aucune étape de tests déclarée (construire.cible_tests) : recommandé"
}

images() {
  lire_ci
  local nom ctx df img
  while IFS='|' read -r nom ctx df img _; do
    echo "== image de $nom : docker.io/$img:$(tag)"
    docker build --pull -t "docker.io/$img:$(tag)" -f "$SRC/$ctx/$df" "$SRC/$ctx"
  done < "$RACINE/.ci/composants.txt"
}

trivy_() {   # bloquant : faille CRITIQUE corrigeable (exceptions : .trivyignore du dépôt source)
  lire_ci
  local img ignore=()
  [ -f "$SRC/.trivyignore" ] && ignore=(--ignorefile "$SRC/.trivyignore")
  while IFS='|' read -r _ _ _ img _; do
    trivy image --scanners vuln --severity CRITICAL --ignore-unfixed "${ignore[@]}" --exit-code 1 --no-progress "docker.io/$img:$(tag)"
    trivy image --scanners vuln --severity HIGH --ignore-unfixed --exit-code 0 --no-progress "docker.io/$img:$(tag)"
  done < "$RACINE/.ci/composants.txt"
}

pousser() {
  lire_ci
  local img
  while IFS='|' read -r _ _ _ img _; do docker push "docker.io/$img:$(tag)"; done < "$RACINE/.ci/composants.txt"
}

nettoyer() {   # images locales retirées (déjà sur Docker Hub) : le disque du serveur ne se remplit pas
  [ -s "$RACINE/.ci/composants.txt" ] && [ -s "$RACINE/.ci/tag" ] || return 0
  local img
  while IFS='|' read -r _ _ _ img _; do docker rmi "docker.io/$img:$(tag)" >/dev/null 2>&1 || true; done < "$RACINE/.ci/composants.txt"
}

case "${1:-}" in
  verifier) verifier "${2:-}" ;;
  maj) maj "${2:-}" "${3:-}" ;;
  source) source_ ;;
  tests) tests ;;
  images) images ;;
  trivy) trivy_ ;;
  pousser) pousser ;;
  nettoyer) nettoyer ;;
  *) echo "usage : $0 verifier <application> | source | tests | images | trivy | pousser | maj <application> <tag> | nettoyer" >&2
     exit 2 ;;
esac
