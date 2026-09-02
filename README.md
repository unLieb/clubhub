# ClubHUB

Selbstgehostete App zur Verwaltung von Reinigungsplänen mit NFC-Abhaken,
Gruppen-Benachrichtigungen (ntfy/Gotify) und Ampel-Dashboard.

Änderungshistorie: siehe [CHANGELOG.md](CHANGELOG.md). Übergabe-Checkliste für
eine Erstinstallation auf einem fremdadministrierten Server: siehe
[HANDOFF.md](HANDOFF.md).

## Konzept

- **Gruppen** (z.B. Hausmeister, Küche, Gastronomie) – Nutzer und Bereiche gehören
  jeweils n:m zu Gruppen (ein Bereich kann also mehreren Gruppen zugeordnet sein).
- **Bereiche** (Räume, aber auch Außenbereiche wie ein Biergarten) haben einen
  frei wählbaren NFC-Tag-Code und enthalten **Aufgaben**. Intern (URLs,
  Datenbank) heißen sie weiterhin "Room" bzw. `/room/<id>` – nur die Anzeige in
  der App spricht von "Bereich", damit bereits beschriebene NFC-Tags gültig bleiben.
- Jede Aufgabe hat ein **rollierendes Intervall** (Stunden seit letzter Erledigung)
  und eine **Warnzeit in Stunden** (wie viele Stunden vor Fälligkeit sie gelb wird,
  bevor sie rot/überfällig ist).
- Das **Dashboard** ist öffentlich einsehbar (z.B. auf einem Tablet an der Wand),
  ein **Login** (Benutzername oder Personalnummer + Passwort) ist nur nötig, um
  eine Aufgabe abzuhaken.
- Ein Hintergrund-Job prüft alle 15 Minuten den Status aller Aufgaben und schickt
  bei einem Wechsel auf Gelb/Rot eine Push-Nachricht an alle Mitglieder der
  betroffenen Gruppe(n) – per ntfy, Gotify und/oder Signal. Gruppen können eine
  **Arbeitszeit** (Start-/Endstunde) hinterlegen: außerhalb dieses Fensters wird
  nichts zugestellt, die Nachricht kommt automatisch nach, sobald die Arbeitszeit
  beginnt (Zeitzone: `APP_TIMEZONE`, Standard `Europe/Berlin`).

## Start

```bash
docker compose up -d --build
```

Die App läuft danach unter `http://<server>:8000`.

Beim allerersten Start wird automatisch ein Admin-Account angelegt
(Name/Passwort aus `INITIAL_ADMIN_NAME` / `INITIAL_ADMIN_PASSWORD` in der
`docker-compose.yml`, Standard: `Admin` / `0000`). Damit direkt einloggen
und unter **Verwaltung** eigene Gruppen, Nutzer, Bereiche und Aufgaben anlegen.

**Wichtig:** `SECRET_KEY` in der `docker-compose.yml` vor dem produktiven
Einsatz auf einen zufälligen Wert setzen (z.B. `openssl rand -hex 32`),
sonst sind Login-Sessions nicht sicher.

## Als App installieren (PWA)

ClubHUB lässt sich als installierbare Web-App (PWA) nutzen, ohne separaten
Play-Store-Eintrag - Icon auf dem Homescreen, eigenes Fenster ohne
Browser-Adressleiste, Push-Benachrichtigungen funktionieren normal weiter:

- **Android (Chrome):** Seite öffnen → Chrome zeigt meist automatisch einen
  "Zur Startseite hinzufügen"/"App installieren"-Hinweis, sonst über das
  Drei-Punkte-Menü → "App installieren".
- **iOS (Safari):** Seite öffnen → Teilen-Symbol → "Zum Home-Bildschirm".

Bewusst ohne Offline-Cache: die App zeigt live Aufgabenstatus, Bestände usw.
- veraltete gecachte Daten wären hier irreführend statt hilfreich, daher
  braucht die installierte App weiterhin eine Internetverbindung zum Server.

## Deployment aufs NAS

`deploy-nas.sh` synct den aktuellen Code-Stand per SSH (tar-Stream statt
rsync, da hier keins verfügbar ist) auf ein Synology-NAS und baut/startet
den Container dort neu. Voraussetzung ist ein SSH-Alias in `~/.ssh/config`:

```
Host nas-clubhub
    HostName <NAS-IP>
    Port <SSH-Port>
    User <NAS-Benutzer>
    IdentityFile ~/.ssh/<privater-key>
    IdentitiesOnly yes
```

Der zugehörige öffentliche Schlüssel muss vorher in
`~/.ssh/authorized_keys` des NAS-Benutzers eingetragen sein (z.B. über
File Station, versteckte Dateien einblenden).

```bash
./deploy-nas.sh
```

`docker-compose.yml` wird bewusst **nicht** mitsynct, da die NAS-Kopie den
echten `SECRET_KEY` enthält (im Repo steht nur ein Platzhalter). Änderungen
an der `docker-compose.yml` (neue Env-Variablen o.ä.) müssen daher bei
Bedarf einmalig manuell auf dem NAS nachgezogen werden.

### Dockhand-Integration

`/volume6/docker/clubhub/docker-compose.yml` ist auf dem NAS ein **Symlink**
auf `/volume6/docker/dockhand/stacks/Compose/ClubHUB/docker-compose.yml` -
Dockhand (die auf dem NAS genutzte Compose-Verwaltungs-GUI) trackt Stacks
grundsätzlich über eine eigene Kopie unter diesem Pfad, unabhängig vom
Projektordner. Ohne den Symlink würden Änderungen über Dockhand nie beim
tatsächlich laufenden Container ankommen (ist bereits so passiert - z.B.
Env-Variablen, die über Dockhand gesetzt wurden, kamen nie im Container an).
Durch den Symlink ist Dockhands Kopie jetzt die einzige, echte Datei.

Damit funktioniert über Dockhand: Env-Variablen ändern (Tokens, URLs, ...)
und den Container neu starten/erzeugen (`docker compose up -d` braucht kein
Rebuild, solange nur Env-Variablen sich geändert haben und das zuletzt
gebaute Image noch existiert). **Nicht** über Dockhand möglich: ein echtes
Rebuild nach Code-Änderungen - Dockhands Verzeichnis enthält nur die
Compose-Datei, keinen Quellcode/Dockerfile. Dafür weiterhin `deploy-nas.sh`
nutzen (das die `docker-compose.yml` wie gehabt ausspart und den Symlink
dadurch unangetastet lässt).

Backup der vorherigen eigenständigen Datei liegt unter
`docker-compose.yml.bak` im selben Ordner, falls der Symlink jemals
rückgängig gemacht werden muss.

## Deployment ohne eigenen Server-Zugriff (fertiges Image)

Für einen Server, den nicht du selbst administrierst (z.B. eine Firmen-IT
oder ein Kollege), eignet sich `deploy-nas.sh` nicht – das setzt eigenen
SSH-Zugriff voraus. Stattdessen landet bei jedem Release zusätzlich ein
fertig gebautes Image in der **privaten** GitHub Container Registry
(`ghcr.io/unlieb/clubhub`), das jeder mit Docker und einem Zugriffs-Token
ohne Quellcode/Build-Toolchain starten kann.

**Einmalig auf dem Zielserver:**

```bash
# Einmalig einloggen (Token braucht mindestens read:packages, von dir als
# Repo-Besitzer über GitHub → Settings → Developer settings → Personal
# access tokens vergeben und dem jeweiligen Account Lesezugriff aufs
# private Package "clubhub" gewähren).
echo "<TOKEN>" | docker login ghcr.io -u <github-nutzername> --password-stdin
```

`docker-compose.yml` wie gewohnt, nur `build: .` durch `image:` ersetzt:

```yaml
services:
  clubhub:
    image: ghcr.io/unlieb/clubhub:latest   # oder eine feste Version, z.B. :0.42.0
    container_name: ClubHUB
    restart: unless-stopped
    ports:
      - "8055:8000"
    volumes:
      - clubhub_data:/data
    environment:
      SECRET_KEY: "..."
      # ... (Rest wie in der Haupt-docker-compose.yml)
volumes:
  clubhub_data:
```

```bash
docker compose up -d
```

**Ein Update einspielen** (durch wen auch immer den Server betreut):

```bash
docker compose pull && docker compose up -d
```

Neue Versionen lande ich (der Entwickler) selbst per `docker push` in der
Registry – dafür braucht es keinen Zugriff auf den Zielserver, nur auf die
eigene lokale Docker-Umgebung. Wer den Server betreut, entscheidet dann
selbst, wann er `docker compose pull` ausführt.

## NFC-Tags beschreiben

Jeder Bereich bekommt eine eigene URL: `http://<server>:8000/room/<bereich-id>`
(die ID ist in der Verwaltung ersichtlich). Diese URL auf einen
NFC-Tag (z.B. NTAG213-Sticker) schreiben – z.B. mit der kostenlosen App
"NFC Tools" (Android/iOS). Tag am Bereich anbringen (z.B. neben der Tür).

Scan → Handy öffnet automatisch die Bereichsseite → Aufgaben mit Ampel-Status
werden angezeigt → nach Login lässt sich eine Aufgabe abhaken.

### Tag-Verwaltung (Verwaltung → NFC-Tags)

Alternativ zur Dritt-App lassen sich Tags direkt im Browser beschreiben und
gleichzeitig in einer Übersicht registrieren (welcher Tag gehört zu welchem
Bereich, inkl. Seriennummer und letztem Prüfzeitpunkt).
Das nutzt die [Web-NFC-API](https://developer.mozilla.org/en-US/docs/Web/API/Web_NFC_API)
und funktioniert daher nur:

- in **Chrome auf Android** (andere Browser/Betriebssysteme unterstützen Web NFC nicht),
- über einen **sicheren Kontext (HTTPS)** – reines HTTP wie im Standard-Setup reicht nicht.
  Dafür braucht es einen Reverse Proxy mit TLS-Zertifikat vor der App (z.B. Caddy,
  Nginx Proxy Manager oder Tailscale-HTTPS). Der Container selbst läuft bereits mit
  `--proxy-headers`, erkennt also ein vorgeschaltetes HTTPS korrekt.

Ohne HTTPS/Chrome-Android zeigt die Seite einen Hinweis und lässt Tags weiterhin ohne
Scan anlegen (URL manuell mit einer Dritt-App aufschreiben, UID optional von Hand eintragen).
Die Registrierung ist reine Verwaltungs-Übersicht – die Scan-URLs selbst funktionieren
immer unabhängig davon, ob ein Tag hier eingetragen ist.

## Zeiterfassung

Jeder Nutzer kann unter **Zeiterfassung** seinen Stundensatz-Verdienst (heute
und diesen Monat bisher) sowie seine Überstunden einsehen. Stundensatz und
Soll-Arbeitszeit/Monat werden pro Nutzer unter **Verwaltung → Nutzer** von
einem Admin hinterlegt (beides optional; ohne Stundensatz werden nur die
Stunden angezeigt, ohne Sollzeit keine Überstunden). Pausen werden nicht
automatisch abgezogen.

Ein- und Ausstempeln funktioniert bewusst nur über ein einziges, von der
Verwaltung **autorisiertes Gerät** (z.B. ein Tablet am Empfang) – kein NFC-Tag,
da ein solcher Tag beliebig kopierbar wäre und sich damit von überall aus
(mit dem eigenen Handy) stempeln ließe. Stattdessen unter
**Verwaltung → Zeiterfassung** genau das Gerät autorisieren, auf dem gestempelt
werden soll (Button "Dieses Gerät autorisieren", ausgeführt direkt auf diesem
Gerät). Ein neu autorisiertes Gerät ersetzt automatisch ein zuvor autorisiertes
– es kann immer nur eines gleichzeitig aktiv sein.

Auf dem autorisierten Gerät läuft unter `http://<server>:8000/timeclock/kiosk`
ein einfaches Terminal: Benutzername/Personalnummer und Passwort eingeben,
stempeln – ohne dass dabei ein Login stattfindet (kein bleibender
Session-Zustand auf dem gemeinsam genutzten Gerät). Auf jedem anderen, nicht
autorisierten Gerät zeigt diese URL nur einen Hinweis und lässt sich nicht
zum Stempeln nutzen.

Ist man auf dem autorisierten Gerät zusätzlich mit dem eigenen Account
eingeloggt, erscheint im **Dashboard** direkt ein Ein-/Ausstempeln-Button -
praktisch, wenn das autorisierte Gerät z.B. ein gemeinsam genutzter PC ist,
an dem sich jeder mit seinem eigenen Account anmeldet. Der Button erscheint
ausschließlich auf dem autorisierten Gerät, auf jedem anderen bleibt er
unsichtbar.

## Backups

Automatische Sicherungen laufen im Hintergrund, standardmäßig um 0, 6, 12 und
18 Uhr (lokale Zeit), aufbewahrt werden die letzten 3 Tage – ältere werden
automatisch gelöscht. Landen unter `backups/` im Datenverzeichnis (also im
selben Docker-Volume wie die Datenbank), Status einsehbar unter
**Verwaltung → System**. Über `BACKUP_SCHEDULE_HOURS` (kommagetrennte
Stunden) und `BACKUP_RETENTION_DAYS` in der `docker-compose.yml` anpassbar,
z.B. auf ein Backup pro Tag reduzieren, wenn sich wenig ändert.

**Wiederherstellen:** Unter Verwaltung → System → Automatische Sicherungen →
„Einzelne Sicherungen" den gewünschten Zeitpunkt aufklappen und
„Wiederherstellen" wählen (nur Admins) – ersetzt die laufende Datenbank direkt
mit diesem Stand, ohne Umweg über Herunterladen/Hochladen. Vorher wird
automatisch eine Sicherheitskopie der aktuellen Datenbank angelegt, danach
startet die Anwendung neu. Alternativ lässt sich jede automatische Sicherung
dort auch einzeln herunterladen.

**Wichtig:** Das schützt vor Bedienfehlern, Bugs oder einer fehlgeschlagenen
Migration, aber nicht vor Verlust des kompletten Docker-Volumes bzw. der
Festplatte selbst. Dafür weiterhin regelmäßig manuell unter Verwaltung →
System → Backup herunterladen und die Datei an einem anderen Ort (eigener
Rechner, NAS, Cloud-Speicher) ablegen.

## Benachrichtigungskanäle einrichten

Kanäle werden in der Verwaltung unter **Benachrichtigungskanäle** unabhängig
von Gruppen angelegt (Name, Typ, Ziel) und dann einer oder mehreren Gruppen
zugeordnet. Ein Kanal kann so auch für mehrere Gruppen wiederverwendet werden.

- **ntfy:** Kanal vom Typ `ntfy` mit dem Topic-Namen anlegen
  (z.B. `putzplan-hausmeister`). Mitglieder abonnieren dieses Topic in
  ihrer ntfy-App.
- **Gotify:** Kanal vom Typ `gotify` mit dem App-Token aus Gotify anlegen.
- **Signal:** Kanal vom Typ `signal` mit der Empfängernummer (E.164, z.B.
  `+49151…`) anlegen. Signal hat bewusst keine offizielle Bot-API – der
  gängige Weg ist ein selbst betriebener [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api)-
  Container mit einer eigenen, per SMS/Anruf registrierten Absendernummer.
  `SIGNAL_BASE_URL` in der `docker-compose.yml` zeigt auf diesen Dienst,
  `SIGNAL_SENDER_NUMBER` ist die dort registrierte Absendernummer. Ohne
  diese beiden Variablen werden Signal-Kanäle beim Versand einfach übersprungen.

### Browser-Push (Web Push)

Zusätzlich zu den obigen Kanälen bekommt jedes Gruppenmitglied automatisch
eine echte Browser-Benachrichtigung (System-Popup), sobald es einmalig im
Glocken-Symbol oben rechts (bzw. in der mobilen Kopfzeile) zustimmt – ganz
ohne zusätzliche App oder Konfiguration in der Verwaltung. Technisch per
[Web Push](https://developer.mozilla.org/de/docs/Web/API/Push_API): der
benötigte VAPID-Schlüssel wird beim ersten Start automatisch erzeugt und liegt
danach dauerhaft im Datenverzeichnis (`vapid_private_key.pem`) – nicht löschen,
sonst müssen alle Nutzer erneut zustimmen.

**Wichtig:** Web Push funktioniert nur über HTTPS (oder `localhost`). Läuft
ClubHUB nur über die lokale IP per HTTP, bleibt das Glocken-Symbol unsichtbar.
Ein Reverse Proxy mit TLS (z.B. Nginx Proxy Manager, Traefik, Caddy – notfalls
auch mit selbstsigniertem Zertifikat) davor genügt.

## Versionierung

Die Version steht in der Datei `VERSION` (Format [SemVer](https://semver.org/lang/de/),
z.B. `0.1.0`) und wird beim `docker build` zusammen mit dem aktuellen Git-Kurz-Hash
(`git rev-parse --short HEAD`) fest ins Image gebacken – sichtbar in der Sidebar
und unter **Verwaltung → System**. Bei jeder spürbaren Änderung `VERSION` von Hand
hochzählen und committen (Patch für Bugfixes, Minor für neue Features, Major für
Breaking Changes wie z.B. eine nicht automatisch migrierbare Datenbankänderung).

## Aufbau

```
app/
  main.py          Routen (Dashboard, Scan, Login, Verwaltung)
  models.py        Datenmodell (Gruppen, Nutzer, Bereiche, Aufgaben, Erledigungen)
  status.py        Ampel-Logik (rollierendes Intervall)
  scheduler.py      Hintergrund-Job für Benachrichtigungen
  notifications.py  ntfy-/Gotify-/Signal-Versand + Browser-Push an Gruppenmitglieder
  push.py           Web-Push: VAPID-Schlüssel, Versand einzelner Subscriptions
  auth.py           Login (Benutzername/Personalnummer + Passwort), Session-Handling
  static/sw.js      Service Worker (nimmt Push-Nachrichten entgegen)
  templates/        Jinja2-Templates (responsives Tailwind-UI)
```
