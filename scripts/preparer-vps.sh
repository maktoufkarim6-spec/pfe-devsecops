#!/usr/bin/env bash
# Preparation du VPS pour la chaine CI/CD durcie (a lancer une fois).
# 1) Cree le Secret Kubernetes app-cobaye-secrets (copie dans Vault), sans afficher les valeurs.
# 2) Construit les nouvelles images et liste leurs failles CRITIQUES (base de .trivyignore).
# 3) Verifie les prerequis (SonarQube, Trivy, Jenkins).
set -euo pipefail
BRANCH=main
REPO=https://github.com/maktoufkarim6-spec/pfe-devsecops.git
NS=default

echo "== 0/3 Fenetre de maintenance AIOps (builds a venir) =="
sudo touch ~/aiops-engine/data/maintenance 2>/dev/null || echo "(moteur AIOps absent, ignore)"

echo "== 1/3 Secret Kubernetes app-cobaye-secrets =="
if kubectl get secret app-cobaye-secrets -n "$NS" >/dev/null 2>&1; then
  echo "Deja present : conserve tel quel (pas de rotation implicite)."
else
  umask 077
  TMP=$(mktemp)
  PGP=$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | cut -c1-28)
  JWT=$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | cut -c1-48)
  printf 'postgres-user=appcobaye\npostgres-password=%s\njwt-secret=%s\n' "$PGP" "$JWT" > "$TMP"
  kubectl create secret generic app-cobaye-secrets -n "$NS" --from-env-file="$TMP"
  # Copie de reference dans Vault (lue depuis stdin : rien dans la ligne de commande)
  if printf '{"postgres-user":"appcobaye","postgres-password":"%s","jwt-secret":"%s"}' "$PGP" "$JWT" \
     | docker exec -i -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN=root vault \
       vault kv put secret/app-cobaye-k8s - >/dev/null 2>&1; then
    echo "Copie enregistree dans Vault : secret/app-cobaye-k8s"
  else
    echo "ATTENTION : copie Vault impossible (le Secret Kubernetes est bien cree)."
  fi
  shred -u "$TMP" 2>/dev/null || rm -f "$TMP"
  unset PGP JWT
  echo "Secret cree (valeurs non affichees)."
fi
kubectl get secret app-cobaye-secrets -n "$NS" -o jsonpath='{.data}' | python3 -c 'import json,sys; print("Cles :", ", ".join(sorted(json.load(sys.stdin))))'

echo "== 2/3 Construction des nouvelles images et inventaire des failles CRITIQUES =="
WORK=$(mktemp -d)
git clone -q --depth 1 -b "$BRANCH" "$REPO" "$WORK/src"
docker build -q -t baseline/app-cobaye-server:test "$WORK/src/server" >/dev/null
docker build -q -t baseline/app-cobaye-client:test "$WORK/src/client" >/dev/null
EXP=$(date -d "+90 days" +%F)
OUT="$WORK/trivyignore.txt"
: > "$OUT"
for img in baseline/app-cobaye-server:test baseline/app-cobaye-client:test; do
  docker exec jenkins trivy image --quiet --scanners vuln --severity CRITICAL --ignore-unfixed \
    --format json "$img" > "$WORK/scan.json"
  python3 - "$WORK/scan.json" "$img" "$EXP" >> "$OUT" << 'PY'
import json, sys
data, img, exp = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
seen = set()
for r in data.get("Results") or []:
    for v in r.get("Vulnerabilities") or []:
        if v["VulnerabilityID"] in seen:
            continue
        seen.add(v["VulnerabilityID"])
        print(f'{v["VulnerabilityID"]} exp:{exp}   # {v["PkgName"]} {v.get("InstalledVersion","")} -> {v.get("FixedVersion","")} ({img.split("/")[1].split(":")[0]})')
PY
done
docker rmi -f baseline/app-cobaye-server:test baseline/app-cobaye-client:test >/dev/null 2>&1 || true
echo "Failles CRITIQUES corrigeables trouvees : $(grep -c . "$OUT" || true)"
echo "----- debut (a envoyer a Claude) -----"
cat "$OUT"
echo "----- fin -----"
rm -rf "$WORK"

echo "== 3/3 Prerequis =="
docker inspect sonarqube >/dev/null 2>&1 && echo "SonarQube : conteneur present" || echo "ERREUR : conteneur sonarqube absent"
docker exec jenkins sh -c 'command -v trivy >/dev/null && command -v curl >/dev/null && command -v git >/dev/null' \
  && echo "Jenkins : trivy, curl et git disponibles" || echo "ERREUR : trivy, curl ou git absent du conteneur Jenkins"
docker exec jenkins docker exec aiops-engine true 2>/dev/null \
  && echo "Jenkins : peut poser la fenetre de maintenance AIOps" || echo "Info : Jenkins ne joint pas aiops-engine (etape ignoree sans erreur)"
echo
echo "Reste a faire a la main : creer le credential Jenkins 'github-token' (voir docs/CI-CD.md)."
