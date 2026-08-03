# Changelog

Alle nennenswerten Änderungen an ClubHUB, neueste zuerst. Format angelehnt an
[Keep a Changelog](https://keepachangelog.com/), Versionierung nach
[SemVer](https://semver.org/lang/de/). Jeder Eintrag entspricht einem
Versions-Bump in `VERSION` und einem eigenen Commit in der Git-Historie
(`git log` für den vollen Diff).

## [0.35.0] - 2026-08-03
- Neue Aufgaben: Turnus kann rückdatiert werden ("Zuletzt erledigt am"), falls schon vor dem Anlegen geputzt wurde

## [0.34.3] - 2026-08-03
- Cache-Busting für style.css (Build-Hash als Query-Parameter), damit CSS-Änderungen nach einem Update nicht per Hard-Reload erzwungen werden müssen

## [0.34.2] - 2026-08-03
- Sidebar im Desktop-Modus jetzt sticky, scrollt nicht mehr mit dem Seiteninhalt

## [0.34.1] - 2026-08-02
- Login-Formular direkt ins Dashboard eingebettet (kein Umweg mehr über die separate Anmelden-Seite)

## [0.34.0] - 2026-07-26
- Login: PIN+Dropdown durch Benutzername/Personalnummer+Passwort ersetzt

## [0.33.0] - 2026-07-26
- Meldungen löschen (bisher gar nicht möglich)

## [0.32.1] - 2026-07-26
- Fehlerhafte ntfy/Gotify/Signal-Zustellung wird jetzt geloggt

## [0.32.0] - 2026-07-26
- Zeiterfassung: Umschaltbarer Terminal-/Nutzer-Modus

## [0.31.0] - 2026-07-26
- "Meine Geräte"-Übersicht für Push-Benachrichtigungen

## [0.30.1] - 2026-07-26
- Push-Glocke zeigt aktiven Status jetzt farblich klar an

## [0.30.0] - 2026-07-26
- Benachrichtigungen springen direkt zum auslösenden Element

## [0.29.2] - 2026-07-26
- Fix: Browser-Benachrichtigungen aktivieren funktionierte gar nicht mehr

## [0.29.1] - 2026-07-25
- Benachrichtigung bei Statusänderung einer Meldung

## [0.29.0] - 2026-07-25
- ClubHUB als installierbare PWA (Android/iOS Homescreen-Icon)

## [0.28.6] - 2026-07-25
- Bereich-Auswahl bei "Neue Meldung" alphabetisch sortiert

## [0.28.5] - 2026-07-25
- Fix: Datei-Feld im Profil lief über den Kartenrand hinaus (mobil)

## [0.28.4] - 2026-07-25
- Einheitlicher, eingerückter Auswahlpfeil für alle `<select>`-Felder

## [0.28.3] - 2026-07-25
- docker-compose.override.yml wieder in eine einzelne Datei zusammengeführt

## [0.28.2] - 2026-07-25
- Docker HEALTHCHECK hinzugefügt (`/healthz` inkl. DB-Check)

## [0.28.1] - 2026-07-25
- Deployment auf NAS umbenannt: Ordner/Projekt/Container jetzt "clubhub"/"ClubHUB"

## [0.28.0] - 2026-07-25
- Sollzeit jetzt auch im Profil pflegbar; Status-Dropdown-Pfeil eingerückt

## [0.27.2] - 2026-07-25
- ClubHUB-Logo/Titel in Kopfzeile führt jetzt zum Dashboard

## [0.27.1] - 2026-07-25
- Dashboard-Meldungen-Kachel zeigt jetzt auch "In Bearbeitung" an

## [0.27.0] - 2026-07-25
- Neue Profil-Seite: eigene PIN, Profilbild und Stundensatz selbst verwalten

## [0.26.0] - 2026-07-25
- Meldungen: Status "In Bearbeitung" für Reparaturen mit Wartezeit

## [0.25.3] - 2026-07-25
- Fix: "Erledigt"-Button überlagerte Meldungskarte auf dem Smartphone

## [0.25.2] - 2026-07-25
- Fix: Zeitstempel in Historie/Meldungen/Inventar/Bereichen liefen der echten Zeit hinterher

## [0.25.1] - 2026-07-25
- Sichtbare Live-Uhr entfernt, NTP-Sync läuft weiter unsichtbar im Hintergrund

## [0.25.0] - 2026-07-25
- Einheitliche aufklappbare "Neu anlegen"-Formulare in der Verwaltung

## [0.24.1] - 2026-07-24
- Doppelte Anmelden-Buttons für anonyme Besucher entfernt

## [0.24.0] - 2026-07-24
- Login jetzt für alle Seiten außer Dashboard-Kurzansicht erforderlich

## [0.23.0] - 2026-07-24
- Automatische Sicherungen direkt wiederherstellbar (kein Umweg über Upload)

## [0.22.0] - 2026-07-24
- Verwaltung: Seitenbreite an Dashboard/Inventar/Meldungen angeglichen

## [0.21.3] - 2026-07-24
- Inventar-Verwaltung: Label "Gebinde" statt "Gebinde (Verpackung)"

## [0.21.2] - 2026-07-24
- Aufgaben: Turnus-Label ergänzt; Inventar: Gebinde-Felder in einer Zeile

## [0.21.1] - 2026-07-24
- Verpackungseinheit-Formular als Grid statt gequetschter Zeile, Verwaltungs-Suche entfernt

## [0.21.0] - 2026-07-24
- Inventar: eigener Mindestbestand statt nur Soll-Bestand, 4-stufiger Status

## [0.20.4] - 2026-07-24
- Seitenleiste/mobile Kopfzeile in Frannz-Petrol statt Weiß/Schwarz

## [0.20.3] - 2026-07-24
- Light-Mode: Farbgebung an Frannz-Club-Website angelehnt

## [0.20.2] - 2026-07-24
- Einzahl/Mehrzahl in Dashboard-Begrüßung und Verwaltungs-Kacheln korrigiert

## [0.20.1] - 2026-07-24
- Sortierung: Bereiche nach Status/Name, Verwaltungslisten nach Name

## [0.20.0] - 2026-07-24
- Browser-Push (Web Push) für Gruppenbenachrichtigungen

## [0.19.0] - 2026-07-24
- Meldungen: Zuständigkeit (Gruppe) je Meldung zuweisbar

## [0.18.0] - 2026-07-24
- UX-Feinschliff: Nav-Badges, schlankere mobile Nav, Dashboard- und Verwaltungs-Verbesserungen

## [0.17.2] - 2026-07-24
- Inventar: Mehrzahlform für Gebinde hinterlegbar (z.B. Rolle/Rollen)

## [0.17.1] - 2026-07-24
- Inventar: Nachkommastelle bei Mengen nur zeigen, wenn tatsächlich gesetzt

## [0.17.0] - 2026-07-24
- Automatische Sicherungen (4x täglich, 3 Tage Aufbewahrung)

## [0.16.4] - 2026-07-24
- Sidebar-Logo wieder auf ursprünglichen Schriftzug zurückgesetzt

## [0.16.3] - 2026-07-24
- Fix Löschen-Bug, Zurück-Buttons ergänzt, Logo-Hintergrund transparent

## [0.16.2] - 2026-07-24
- Logo und Favicon einbinden

## [0.16.1] - 2026-07-23
- Inventar: Ist-/Soll-Bestand in die Verpackungseinheit-Box integriert

## [0.16.0] - 2026-07-23
- Inventar: Verpackungseinheit als eigener, einklappbarer Abschnitt

## [0.15.0] - 2026-07-23
- Inventar: Gebindegröße anzeigen (z.B. "Kanister à 10 Liter")

## [0.14.0] - 2026-07-23
- Inventar: Sichtbarkeit nach Gruppenzugehörigkeit filtern

## [0.13.3] - 2026-07-23
- Inventar: Hinweis bei fehlgeschlagenem automatischen Produktbild-Abruf

## [0.13.2] - 2026-07-23
- Historie: versehentlich erledigte Aufgaben rückgängig machen (nur Admin)

## [0.13.1] - 2026-07-23
- Aufgaben: Fälligkeit als "Überfällig seit ..." / "Fällig in ..." anzeigen

## [0.13.0] - 2026-07-23
- Zeiterfassung: Ein-/Ausstempeln-Button im Dashboard

## [0.12.2] - 2026-07-23
- Fix: Live-Uhr überlagerte rechtsbündige Kopfzeilen-Buttons (Inventar, Verwaltung)

## [0.12.1] - 2026-07-23
- Fix: Lightmode-Kontrast für slate-200/slate-500 verbessert

## [0.12.0] - 2026-07-23
- Zeiterfassung: NFC-Stempeln durch autorisiertes Geräte-Terminal ersetzt

## [0.11.1] - 2026-07-23
- Inventar: eigene +/- Buttons zum Bestand anpassen (Mobile-Fix)

## [0.11.0] - 2026-07-23
- NFC-Tags: Verwaltungs-Registry mit Web-NFC-Lesen/Schreiben

## [0.10.0] - 2026-07-23
- Inventar: Produktbild automatisch vom Kauflink übernehmen

## [0.9.2] - 2026-07-23
- Fix: Auto-Theme-Icon als "A", "Gute Nacht" durch "Guten Abend" ersetzt

## [0.9.1] - 2026-07-23
- Zeiterfassung: nachträgliche Korrektur durch Admin

## [0.9.0] - 2026-07-23
- Zeiterfassung: NFC-Ein-/Ausstempeln, Verdienst & Überstunden

## [0.8.0] - 2026-07-22
- Inventar: Artikelbild per Klick auf das Icon setzen

## [0.7.1] - 2026-07-22
- Inventar: Nachbestellen-Button im Detailbereich zurückgeholt

## [0.7.0] - 2026-07-22
- Rebrand zu ClubHUB, personalisierte Dashboard-Begrüßung

## [0.6.0] - 2026-07-22
- Meldungen: Aufklapp-Stil wie Inventar, Kommentare, Verlauf, mehr Fotos

## [0.5.0] - 2026-07-22
- Inventar-Feinschliff: Chips, Suche/Filter, Artikel hinzufügen

## [0.4.1] - 2026-07-22
- Inventar: Details klappt inline auf statt als Seiten-Drawer

## [0.4.0] - 2026-07-22
- Redesign Inventar: Kategorie, Lagerort, farbiger Bestandsbalken, Drawer, Nachbestell-Link

## [0.3.0] - 2026-07-22
- Redesign Dashboard: Bereich-Buttons entfernt, Meldungen sichtbar gemacht

## [0.2.0] - 2026-07-22
- Erweiterte Meldungen: mehrere Fotos, Priorität, Kategorie, einklappbares Formular

## [0.1.0] - 2026-07-22
- SemVer-Versionsverfolgung mit Git-Build-Hash eingeführt
