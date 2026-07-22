# Reinigungsplan

Selbstgehostete App zur Verwaltung von Reinigungsplänen mit NFC-Abhaken,
Gruppen-Benachrichtigungen (ntfy/Gotify) und Ampel-Dashboard.

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
  ein **Login per PIN** ist nur nötig, um eine Aufgabe abzuhaken.
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
(Name/PIN aus `INITIAL_ADMIN_NAME` / `INITIAL_ADMIN_PIN` in der
`docker-compose.yml`, Standard: `Admin` / `0000`). Damit direkt einloggen
und unter **Verwaltung** eigene Gruppen, Nutzer, Bereiche und Aufgaben anlegen.

**Wichtig:** `SECRET_KEY` in der `docker-compose.yml` vor dem produktiven
Einsatz auf einen zufälligen Wert setzen (z.B. `openssl rand -hex 32`),
sonst sind Login-Sessions nicht sicher.

## NFC-Tags beschreiben

Jeder Bereich bekommt eine eigene URL: `http://<server>:8000/room/<bereich-id>`
(die ID ist in der Verwaltung ersichtlich). Diese URL auf einen
NFC-Tag (z.B. NTAG213-Sticker) schreiben – z.B. mit der kostenlosen App
"NFC Tools" (Android/iOS). Tag am Bereich anbringen (z.B. neben der Tür).

Scan → Handy öffnet automatisch die Bereichsseite → Aufgaben mit Ampel-Status
werden angezeigt → nach Login (PIN) lässt sich eine Aufgabe abhaken.

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
  notifications.py  ntfy-/Gotify-Versand
  auth.py           PIN-Login, Session-Handling
  templates/        Jinja2-Templates (responsives Tailwind-UI)
```
