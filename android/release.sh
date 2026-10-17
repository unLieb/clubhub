#!/usr/bin/env bash
# Baut die signierte Release-APK und legt sie samt Versionsdatei in den Server-Ordner
# (app/static/app/), damit sie mit dem naechsten Server-Release ausgeliefert wird:
#   - /static/app/clubhub.apk   (Download)
#   - /static/app/latest.json   (Versionspruefung der App)
# Voraussetzung: android/keystore.properties (Release-Schluessel, nicht im Repo).
# Danach: Server-VERSION erhoehen, committen, deploy-staging.sh, nach Freigabe nach GHCR.
set -euo pipefail

cd "$(dirname "$0")"
export JAVA_HOME="${JAVA_HOME:-/c/Program Files/Android/Android Studio/jbr}"

if [ ! -f keystore.properties ]; then
  echo "keystore.properties fehlt - ohne Release-Schluessel kein signierter Build." >&2
  exit 1
fi

./gradlew assembleRelease --no-daemon -q

DEST="../app/static/app"
mkdir -p "$DEST"
cp app/build/outputs/apk/release/app-release.apk "$DEST/clubhub.apk"

CODE=$(sed -n 's/.*versionCode = \([0-9]*\).*/\1/p' app/build.gradle.kts)
NAME=$(sed -n 's/.*versionName = "\([^"]*\)".*/\1/p' app/build.gradle.kts)
printf '{"versionCode": %s, "versionName": "%s", "apk": "/static/app/clubhub.apk"}\n' "$CODE" "$NAME" > "$DEST/latest.json"

echo "==> App ${NAME} (Code ${CODE}) liegt in app/static/app/ ($(du -h "$DEST/clubhub.apk" | cut -f1))"
