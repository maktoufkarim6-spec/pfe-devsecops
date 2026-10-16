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

# ------------------------------------------------------------------ nouvelle application (formulaire Jenkins)
# Variables NOUVEAU_* (champs du formulaire) : DEPOT (obligatoire), BRANCHE, PORT, DOSSIER, DOCKERFILE, SANTE,
# DOMAINE, BASE (true/false), SONAR. Le values.yaml est écrit ici, contrôlé comme les autres, et n'est commité
# qu'après un build réussi (étape GitOps) : une application en échec ne laisse aucune trace dans le dépôt.
creer() {
  local app=${1:-}
  [[ "$app" =~ ^[a-z][a-z0-9-]{1,30}[a-z0-9]$ ]] || erreur "nom d'application invalide : '$app' (minuscules, chiffres, tirets, 3 à 32 caractères)"
  case "$app" in default|kube-system|kube-public|kube-node-lease|argocd|plateforme) erreur "nom réservé : $app" ;; esac
  [ ! -e "$RACINE/applications/$app" ] || erreur "l'application $app existe déjà : laisser DEPOT vide pour la reconstruire"
  local depot=${NOUVEAU_DEPOT:-} branche=${NOUVEAU_BRANCHE:-main} port=${NOUVEAU_PORT:-} dossier=${NOUVEAU_DOSSIER:-.}
  local dockerfile=${NOUVEAU_DOCKERFILE:-Dockerfile} sante=${NOUVEAU_SANTE:-} domaine=${NOUVEAU_DOMAINE:-}
  local base=${NOUVEAU_BASE:-false} sonar=${NOUVEAU_SONAR:-bloquant}
  sur "$depot" "DEPOT (adresse https du dépôt Git)" '^https://[a-z0-9.-]+(:[0-9]{1,5})?/[A-Za-z0-9_.~/-]+$'
  sur "$branche" "BRANCHE" '^[A-Za-z0-9._/-]{1,100}$'
  [ -z "$port" ] || { [[ "$port" =~ ^[0-9]{2,5}$ ]] && (( port >= 80 && port <= 65535 )); } || erreur "PORT invalide : '$port'"
  [ "$dossier" = "." ] || sur "$dossier" "DOSSIER" '^[A-Za-z0-9._][A-Za-z0-9._/-]*$'
  sur "$dockerfile" "DOCKERFILE" '^[A-Za-z0-9._][A-Za-z0-9._/-]*$'
  [ -z "$sante" ] || sur "$sante" "SANTE (chemin de la page de santé)" '^/[A-Za-z0-9._~/-]{0,200}$'
  [ -z "$domaine" ] || sur "$domaine" "DOMAINE" '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'
  [[ "$base" =~ ^(true|false)$ ]] || erreur "BASE : true ou false"
  [[ "$sonar" =~ ^(bloquant|informatif)$ ]] || erreur "SONAR : bloquant ou informatif"
  mkdir -p "$RACINE/applications/$app"
  {
    echo "# Application $app : créée par le formulaire du job Jenkins plateforme-applications."
    echo "# Construite depuis son code source ; la ligne image est écrite par le pipeline à chaque build."
    [ -z "$domaine" ] || echo "domaine: $domaine"
    echo "source:"
    echo "  depot: $depot"
    echo "  branche: $branche"
    echo "  sonar: $sonar"
    echo "composants:"
    echo "  - nom: web"
    echo "    image: docker.io/$REGISTRE/$app:0-0000000"
    echo "    construire:"
    echo "      contexte: $dossier"
    echo "      dockerfile: $dockerfile"
    echo "      image: $REGISTRE/$app"
    if [ -n "$port" ]; then echo "    port: $port"
    else echo "    port: 0            # lu dans l'image (EXPOSE) après la construction"; fi
    echo "    utilisateur: 0            # lu dans l'image (USER) après la construction ; root refusé"
    echo "    public: true"
    [ -z "$sante" ] || echo "    sante: $sante"
    if [ "$base" = true ]; then
      echo "    env: {PGHOST: base, PGPORT: \"5432\", PGDATABASE: app}"
      echo "    secrets: {PGUSER: postgres-user, PGPASSWORD: postgres-password}"
      echo "base:"
      echo "  active: true"
    fi
  } > "$RACINE/applications/$app/values.yaml"
  mkdir -p "$RACINE/.ci" && echo "$app" > "$RACINE/.ci/nouvelle"
  local extra=""; [ "$base" = true ] && extra=", base PostgreSQL"
  echo "$app : values.yaml créé (dépôt $depot, branche $branche${domaine:+, domaine $domaine}$extra)"
}

completer() {   # nouvelle application : port et utilisateur lus dans l'image construite
  lire_ci
  [ -s "$RACINE/.ci/nouvelle" ] || { echo "application existante : rien à compléter"; return 0; }
  local app nom img cfg user uid port ligne
  app=$(cat "$RACINE/.ci/nouvelle"); FICHIER=$RACINE/applications/$app/values.yaml
  IFS='|' read -r nom _ _ img _ < "$RACINE/.ci/composants.txt"
  cfg=$(docker image inspect --format '{{.Config.User}}|{{range $p, $v := .Config.ExposedPorts}}{{$p}} {{end}}' "docker.io/$img:$(tag)")
  user=${cfg%%|*}; user=${user%%:*}
  if [[ "$user" =~ ^[0-9]+$ ]]; then uid=$user
  elif [ -n "$user" ]; then
    uid=$(docker run --rm --entrypoint id --user "$user" "docker.io/$img:$(tag)" -u 2>/dev/null) \
      || erreur "utilisateur « $user » de l'image illisible : mettre un UID numérique (USER 1000) dans le Dockerfile"
  fi
  [[ "${uid:-}" =~ ^[0-9]+$ ]] && [ "$uid" -gt 0 ] \
    || erreur "l'image tourne en root : ajouter « USER 1000 » (un utilisateur non root) dans le Dockerfile"
  port=$(sed -n 's/^ *port: \([0-9]*\).*/\1/p' "$FICHIER" | head -1)
  if [ "${port:-0}" = 0 ]; then
    port=$(tr ' ' '\n' <<< "${cfg#*|}" | sed -n 's#^\([0-9]*\)/tcp$#\1#p' | head -1)
    [ -n "$port" ] || erreur "port inconnu : ajouter « EXPOSE <port> » dans le Dockerfile, ou remplir le champ PORT"
    sed -i -E "s/^(    port: )0( .*)?$/\1$port/" "$FICHIER"
  fi
  sed -i -E "s/^(    utilisateur: )0( .*)?$/\1$uid/" "$FICHIER"
  grep -qE "^    port: $port$|^    port: $port " "$FICHIER" && grep -qE "^    utilisateur: $uid$" "$FICHIER" \
    || erreur "port ou utilisateur non écrit dans values.yaml"
  echo "$app : port $port et utilisateur $uid lus dans l'image"
}

case "${1:-}" in
  creer) creer "${2:-}" ;;
  completer) completer ;;
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
