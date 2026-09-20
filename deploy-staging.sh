#!/usr/bin/env bash
# Staging-Deploy auf den Docker-Host (Proxmox-VM "dockerhost", 192.168.1.26).
# Dort laeuft ClubHUB als Dockhand-Stack mit dem GHCR-Image
# ghcr.io/unlieb/clubhub:latest, nicht aus dem Quellcode gebaut. Dieses Skript
# baut das Image deshalb lokal, ueberspielt genau dieses Image per
# `docker save | docker load` und startet den bestehenden Stack neu - so laeuft
# auf dem Staging-System exakt das Image, das danach nach GHCR gepusht wird,
# und die Reihenfolge "erst Staging testen, dann veroeffentlichen" bleibt.
#
# Vorher committen: der Build-Hash im Image stammt aus dem .git-Stand.
# Die docker-compose.yml des Stacks (mit dem echten SECRET_KEY) bleibt
# unangetastet; Aenderungen daran pflegt man in Dockhand.
#
# Voraussetzung: SSH-Alias "dockerhost" in ~/.ssh/config (Host, Port, User,
# IdentityFile) - siehe README fuer Details.
set -euo pipefail

HOST="dockerhost"
IMAGE="ghcr.io/unlieb/clubhub:latest"
STACK_FILE="/opt/docker/appdata/dockhand/stacks/Compose/ClubHUB/docker-compose.yml"

cd "$(dirname "$0")"

echo "==> Baue Image lokal ..."
docker compose build
# Compose-Projekt "clubhub", Dienst "clubhub" -> lokaler Image-Name clubhub-clubhub.
docker tag clubhub-clubhub:latest "${IMAGE}"

echo "==> Ueberspiele Image nach ${HOST} ..."
docker save "${IMAGE}" | gzip | ssh "${HOST}" "gunzip | docker load"

echo "==> Starte ClubHUB-Stack auf ${HOST} neu ..."
ssh "${HOST}" "docker compose -p clubhub -f ${STACK_FILE} up -d"

echo "==> Fertig. Build-Hash auf dem Docker-Host:"
ssh "${HOST}" "docker exec ClubHUB cat BUILD_HASH"
echo
echo "==> Lokaler HEAD:"
git rev-parse --short HEAD
