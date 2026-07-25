#!/usr/bin/env bash
# Synct den aktuellen Code-Stand per SSH zum NAS und baut/startet ClubHUB
# dort neu. Nutzt tar-über-SSH statt rsync, da auf diesem Windows/Git-Bash-
# Rechner kein rsync verfuegbar ist - fuer die Groesse dieses Repos macht
# das keinen spuerbaren Unterschied.
#
# docker-compose.yml wird bewusst NICHT mitsynct: die NAS-Kopie enthaelt den
# echten SECRET_KEY, waehrend im Repo nur ein Platzhalter steht. Aenderungen
# an der docker-compose.yml (neue Env-Variablen etc.) muessen daher bei
# Bedarf manuell auf dem NAS nachgezogen werden.
#
# Voraussetzung: SSH-Alias "nas-clubhub" in ~/.ssh/config (Host, Port, User,
# IdentityFile) - siehe README fuer Details.
set -euo pipefail

NAS_HOST="nas-clubhub"
NAS_DIR="/volume6/docker/clubhub"
DOCKER="/usr/local/bin/docker"

cd "$(dirname "$0")"

echo "==> Synce Code nach ${NAS_HOST}:${NAS_DIR} ..."
tar czf - \
  --exclude='.git' \
  --exclude='.claude' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='docker-compose.yml' \
  . | ssh "${NAS_HOST}" "mkdir -p ${NAS_DIR} && tar xzf - -C ${NAS_DIR}"

# .git wird separat mitgeschickt, da es fuer den BUILD_HASH im Dockerfile
# gebraucht wird, aber aus Konsistenzgruenden nicht Teil des Haupt-Excludes
# oben sein soll (sonst muesste man es leicht vergessen koennen zu syncen).
echo "==> Synce .git (fuer BUILD_HASH) ..."
tar czf - .git | ssh "${NAS_HOST}" "tar xzf - -C ${NAS_DIR}"

echo "==> Baue und starte auf dem NAS ..."
ssh "${NAS_HOST}" "cd ${NAS_DIR} && ${DOCKER} compose build && ${DOCKER} compose up -d"

echo "==> Fertig. Build-Hash auf dem NAS:"
ssh "${NAS_HOST}" "${DOCKER} exec ClubHUB cat BUILD_HASH"
echo
echo "==> Lokaler HEAD:"
git rev-parse --short HEAD
