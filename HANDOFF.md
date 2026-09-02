# Server-Übergabe

Kurzer Fahrplan für die Erstinstallation auf einem Server, den nicht du selbst
administrierst (z.B. eine Firmen-IT). Was vor dem ersten Start zwingend
passieren muss, was für den echten Betrieb empfohlen ist, und was sich in
Ruhe später klären lässt. Ausführlichere Hintergründe zu einzelnen Punkten
stehen im [README](README.md).

## 1. Vor dem ersten Start (Pflicht)

Ohne diese vier Punkte läuft der Container entweder gar nicht erst, oder er
läuft mit unsicheren Standardwerten. Alle vier gehören in die eigene
`docker-compose.yml` auf dem Zielserver.

### Zugriff auf die Image-Registry einrichten

Das fertige Image liegt in der GitHub Container Registry
(`ghcr.io/unlieb/clubhub`). Falls die Registry (weiterhin) privat ist, braucht
es vorher ein Zugriffs-Token (Scope `read:packages`) für den Account, der den
Server betreut – zu vergeben vom Repo-Besitzer über GitHub → Settings →
Developer settings → Personal access tokens.

```bash
# einmalig, auf dem Zielserver
echo "<TOKEN>" | docker login ghcr.io -u <github-nutzername> --password-stdin
```

### Eigene `docker-compose.yml` anlegen

Kein eigener Build nötig – `image:` statt `build: .` verwenden. Port und
Volume-Name sind frei wählbar, `clubhub_data` ist nur ein Vorschlag.

```yaml
services:
  clubhub:
    image: ghcr.io/unlieb/clubhub:latest
    container_name: ClubHUB
    restart: unless-stopped
    ports:
      - "8055:8000"
    volumes:
      - clubhub_data:/data
    environment:
      SECRET_KEY: "…"              # siehe nächster Punkt
      INITIAL_ADMIN_NAME: "…"      # siehe nächster Punkt
      INITIAL_ADMIN_PASSWORD: "…"  # siehe nächster Punkt
volumes:
  clubhub_data:
```

### `SECRET_KEY` auf einen zufälligen Wert setzen

Signiert die Login-Sessions. Ohne Änderung ist jede Session mit einem
öffentlich bekannten Platzhalter-Schlüssel gesichert – **keine Kopie aus der
Doku übernehmen**, sondern selbst erzeugen.

```bash
openssl rand -hex 32
```

### Ersten Admin-Zugang festlegen

Beim allerersten Start (leere Datenbank) legt ClubHUB automatisch genau einen
Admin-Account an – Name/Passwort aus `INITIAL_ADMIN_NAME` /
`INITIAL_ADMIN_PASSWORD`. Ohne eigene Werte greift `Admin` / `0000`. Entweder
hier direkt ein echtes Passwort vergeben, oder mit dem Standard starten und es
unmittelbar nach dem ersten Login unter *Profil → Passwort ändern* ersetzen.

## 2. Für den produktiven Betrieb (empfohlen)

Läuft ohne diese Punkte, aber mit spürbaren Einschränkungen bzw. Risiken –
vor dem Rollout an alle Mitarbeiter klären.

- [ ] **HTTPS über einen Reverse Proxy.** Ohne HTTPS bleiben zwei Funktionen
  unsichtbar bzw. deaktiviert: Browser-Push (Benachrichtigungs-Glocke) und das
  direkte Beschreiben von NFC-Tags im Browser (Chrome/Android). Passkey-Login
  ist ebenfalls an einen sicheren Kontext gebunden. Reicht z.B. Caddy, Nginx
  Proxy Manager oder Traefik davor – der Container läuft bereits mit
  `--proxy-headers` und erkennt ein vorgeschaltetes HTTPS korrekt.

- [ ] **Watchtower für automatische Updates mitinstallieren.** ClubHUB wird
  häufig weiterentwickelt – ohne Watchtower müsste die IT-Firma für jedes
  Update einzeln `docker compose pull && docker compose up -d` ausführen.
  Watchtower übernimmt das automatisch: ein schlanker Begleit-Container, der
  die Registry in festem Abstand prüft und ClubHUB bei einer neuen Version
  selbstständig neu startet. Braucht denselben Registry-Login wie ClubHUB
  selbst.

  ```yaml
    watchtower:
      image: containrrr/watchtower
      restart: unless-stopped
      volumes:
        - /var/run/docker.sock:/var/run/docker.sock
      command: --interval 300 --cleanup ClubHUB
      # prüft alle 5 Min., nur den ClubHUB-Container (Name s.o.)
  ```

  Wer lieber jedes Update erst bestätigen möchte, statt es vollautomatisch
  laufen zu lassen: eine Portainer-Webhook-Variante ist ebenfalls möglich.

- [ ] **Externe Backup-Kopie einplanen.** Automatische Sicherungen laufen
  bereits intern (4×/Tag, 3 Tage Aufbewahrung, einsehbar unter *Verwaltung →
  System*) – die schützen aber nur vor Bedienfehlern, nicht vor Verlust des
  Volumes oder der Festplatte selbst. Regelmäßig den manuellen Download dort
  in eine externe Sicherung einbeziehen.

- [ ] **Zeitzone prüfen**, falls abweichend. Steuert, wann Aufgaben fällig
  werden und wann Arbeitszeit-Fenster für Benachrichtigungen gelten. Standard
  ist `Europe/Berlin`, änderbar über `APP_TIMEZONE`.

- [ ] **Zeiterfassung & Urlaub sind bewusst deaktiviert** – reine Info, keine
  Aktion nötig. Auf einer frischen Installation ohne bisherige Nutzung starten
  diese beiden Module automatisch aus; die entsprechenden Menüpunkte fehlen
  dann absichtlich. Falls doch gebraucht: *Verwaltung → System → Module &
  Features*.

## 3. Danach, in Ruhe

Nichts hiervon blockiert den Start – lohnt sich aber, sobald ClubHUB läuft.

- **Push-Benachrichtigungen per ntfy / Gotify / Signal**, nur falls gewünscht.
  Zusätzlich zur eingebauten Browser-Push-Benachrichtigung lassen sich Gruppen
  an externe Kanäle koppeln (`NTFY_BASE_URL`, `GOTIFY_BASE_URL`, optional
  Signal über einen eigenen `signal-cli-rest-api`-Container). Ohne Bedarf
  einfach weglassen – die App funktioniert vollständig ohne sie.

---

**Kurzfassung für Eilige:** Registry-Login einrichten, eigene
`docker-compose.yml` mit echtem `SECRET_KEY` und Admin-Zugang anlegen,
starten, HTTPS davorsetzen, Watchtower danebenstellen. Der Rest ergibt sich
beim Einrichten von selbst.
