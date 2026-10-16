#!/bin/bash
# Sonde envoyée par decouvrir_parc.py (ssh ... bash -s < sonde_serveur.sh) : LECTURE SEULE.
# Rien n'est installé, modifié ni démarré ; aucune donnée sensible n'est lue. Sortie : une ligne JSON.
export LC_ALL=C PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

json() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; printf '"%s"' "${s//[$'\t\r\n']/ }"; }
oui() { "$@" >/dev/null 2>&1 && echo true || echo false; }

. /etc/os-release 2>/dev/null
ram=$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)
disque=$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc 0-9)
docker_ok=$(oui command -v docker)
conteneurs=""
if [ "$docker_ok" = true ]; then
  conteneurs=$( (docker ps --format '{{.Names}}' 2>/dev/null || sudo -n docker ps --format '{{.Names}}' 2>/dev/null) | sort | tr '\n' ' ')
fi
k3s_version=$(k3s --version 2>/dev/null | awk 'NR==1 {print $3}')

printf '{'
printf '"nom_hote":%s,' "$(json "$(hostname)")"
printf '"systeme":%s,' "$(json "${ID:-inconnu} ${VERSION_ID:-}")"
printf '"cpu":%s,' "$(nproc 2>/dev/null || echo 0)"
printf '"ram_mo":%s,' "${ram:-0}"
printf '"disque_libre_go":%s,' "${disque:-0}"
printf '"sudo":%s,' "$(oui sudo -n true)"
printf '"docker":%s,' "$docker_ok"
printf '"conteneurs":%s,' "$(json "$conteneurs")"
printf '"k3s_version":%s,' "$(json "$k3s_version")"
printf '"k3s_serveur":%s,' "$(oui systemctl is-active --quiet k3s)"
printf '"k3s_agent":%s,' "$(oui systemctl is-active --quiet k3s-agent)"
printf '"pfe_runbook":%s,' "$(oui test -x /usr/local/sbin/pfe-runbook)"
printf '"services_ecoute":%s' "$(json "$(ss -Htln 2>/dev/null | awk '{print $4}' | sed -E 's/.*:([0-9]+)$/\1/' | sort -un | tr '\n' ' ')")"
printf '}\n'
