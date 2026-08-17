# Changelog

Alle nennenswerten Änderungen an ClubHUB, neueste zuerst. Format angelehnt an
[Keep a Changelog](https://keepachangelog.com/), Versionierung nach
[SemVer](https://semver.org/lang/de/). Jeder Eintrag entspricht einem
Versions-Bump in `VERSION` und einem eigenen Commit in der Git-Historie
(`git log` für den vollen Diff).

## [0.59.0] - 2026-08-17
- Neu: internes Feedback-System. Schwebender "💬 Feedback / Bug melden"-Button (für jeden eingeloggten Nutzer) öffnet ein Modal für Bug-Meldungen und Funktionswünsche mit Titel, Beschreibung und Priorität - Seite, Nutzer und Zeitpunkt werden automatisch erfasst
- Verwaltung > Feedback: Listenansicht aller Meldungen mit änderbarem Status (Offen/In Bearbeitung/Erledigt) sowie einem Button, der die Ticket-Details als fertigen Claude-Prompt in die Zwischenablage kopiert

## [0.58.0] - 2026-08-17
- Bereichs-Detailseite (/room/{id}): Admins/Schichtleiter/Entwickler sehen dort jetzt "Aufgabe hinzufügen" sowie Bearbeiten-Icons je Aufgabe, Verwaltung läuft über einen Slide-Over-Drawer direkt auf der Seite - kein Wechsel mehr in die Verwaltung nötig
- Verwaltung > Bereiche wieder schlank: nur noch Bereiche anlegen/löschen und Gruppenzuweisung, keine Aufgabenverwaltung mehr dort

## [0.57.0] - 2026-08-17
- Verwaltung > Inventar überarbeitet: Anlegen/Bearbeiten läuft jetzt über einen Slide-Over-Drawer statt Inline-Aufklapp-Formularen, gegliedert in Stammdaten / Bestände / Gebinde & Einheiten / Einkauf
- Artikelliste ist jetzt eine kompakte Tabelle statt einer Akkordeon-Liste

## [0.56.0] - 2026-08-17
- Aufgabenverwaltung auf raumzentrierte Logik umgestellt (Kopieren statt Verlinken): Verwaltung > Bereiche zeigt jetzt links eine klickbare Bereichsliste, rechts direkt die eigene Aufgabenliste des ausgewählten Bereichs zum Anlegen/Bearbeiten/Löschen
- Neue Aufgaben in mehreren Bereichen gleichzeitig erzeugen ab sofort unabhängige, unverknüpfte Kopien statt gemeinsam bearbeitbarer Aufgaben - Bearbeiten und Löschen betreffen immer nur die Aufgabe des jeweiligen Bereichs
- Einmalige Migration löst bestehende bereichsübergreifende Verknüpfungen auf (keine Datenänderung, nur Entkopplung)
- Alte globale Aufgaben-Seite samt Kachel in der Verwaltungsübersicht entfernt

## [0.55.1] - 2026-08-17
- Fix: Sammel-Buttons ("Tägliche erledigen" / "Tägliche abhaken") erledigen jetzt nur noch Aufgaben mit Turnus "Täglich" statt aller fälligen/überfälligen Aufgaben unabhängig vom Turnus - wöchentliche/monatliche Aufgaben wurden dabei bisher versehentlich mit abgehakt

## [0.55.0] - 2026-08-17
- Aufgaben: Sammel-Button "Alle erledigen" auf jeder Bereichs-Card markiert alle aktuell fälligen/überfälligen Aufgaben des Bereichs auf einmal als erledigt
- Dashboard: sekundärer Button "Alle abhaken" im Header von "Überfällige Aufgaben" erledigt bereichsübergreifend alle überfälligen Aufgaben auf einmal (mit Sicherheitsabfrage)
- Beide Sammel-Aktionen zeigen danach eine kurze Toast-Bestätigung mit Anzahl und Bereich

## [0.54.0] - 2026-08-15
- Neues Logo (logo.svg) ersetzt das Platzhalter-Icon in der Sidebar-Kopfzeile, Icon/"ClubHUB"/Versionsnummer stehen jetzt sauber in einer Reihe
- Favicon (inkl. Apple-Touch-Icon und PWA-Icon) auf das neue Logo umgestellt

## [0.53.0] - 2026-08-15
- Aufgaben: Turnus lässt sich jetzt auf bestimmte Wochentage einschränken ("Aktive Wochentage"). Damit lassen sich geteilte Zuständigkeiten (z.B. Hausmeisterei Mo-Fr, Toilettenbetreuung Sa+So) oder reine Wochenend-Aufgaben sauber abbilden - die Fälligkeit überspringt inaktive Tage, statt übers Wochenende hinweg fälschlich fällig zu werden

## [0.52.1] - 2026-08-14
- Fix: nie erledigte Aufgaben zeigten dauerhaft "Überfällig seit unter 1 Minute" statt der tatsächlichen (unbekannten) Wartezeit - due_at wurde bei jeder Anzeige neu auf "jetzt" gesetzt. Zeigt jetzt korrekt "Noch nie erledigt"

## [0.52.0] - 2026-08-14
- Dashboard: doppelte "Aktuelle Meldungen"-Sektion entfernt, Meldungen-Kachel zeigt die obersten offenen Meldungen jetzt direkt an
- Dashboard: KPI-Kacheln "Erledigt heute" (Vergleich zu gestern) und "Bereiche gesamt" (Anzahl Gruppen) mit zusätzlichem Kontext

## [0.51.2] - 2026-08-13
- Aufgaben und Historie: Pfeil-Buttons zum Vor-/Zurückblättern bei den Seiten ergänzt (zusätzlich zu den anklickbaren Seitenzahlen)

## [0.51.1] - 2026-08-13
- Historie: Suche/Filter/Seiten laufen jetzt als echte Datenbank-Abfrage statt clientseitig - bei ca. 30-40 Erledigungen/Tag über alle Bereiche wäre die vorherige Variante (alles auf einmal laden, im Browser filtern) binnen ein bis zwei Jahren spürbar langsam geworden. Jede Seite bleibt jetzt unabhängig von der Gesamtgröße der Historie gleich leicht

## [0.51.0] - 2026-08-13
- Historie: starres 200-Einträge-Limit entfernt, stattdessen Suche + Filter nach Bereich/Nutzer/Zeitraum + Seiten (25/50/100/Alle) - kein Eintrag geht mehr verloren, egal wie alt

## [0.50.3] - 2026-08-13
- Erledigungs-Datum zeigt jetzt "heute"/"gestern" statt des Datums, wenn zutreffend (Historie, Bereichsseite, Dashboard, Bereiche-Übersicht) - ältere Erledigungen weiterhin als TT.MM.JJJJ

## [0.50.2] - 2026-08-13
- Historie, Bereichsseite, "Letzte Reinigungen" auf dem Dashboard und die Bereiche-Übersicht zeigen jetzt nur noch das Datum statt Datum+Uhrzeit - Turnus-Berechnung und Benachrichtigungen nutzen intern weiterhin die volle Uhrzeit

## [0.50.1] - 2026-08-13
- Bereichsseite: erledigte Aufgaben verschwinden jetzt aus der Liste, statt grün stehen zu bleiben - tauchen erst beim nächsten Turnus-Alarm (gelb/rot) wieder auf, "Nach Bedarf"-Aufgaben bleiben immer sichtbar. Bereits Erledigtes bleibt über einen einklappbaren Abschnitt einsehbar

## [0.50.0] - 2026-08-13
- Nutzerverwaltung: Pauschalkraft ist jetzt eine eigene Rolle im "Rolle"-Auswahlmenü statt einer separaten Checkbox
- Inventar: jeder kann für sich selbst Gruppen ausblenden, die ihn nicht interessieren ("Sichtbarkeit anpassen") - rein persönliche Einstellung, ändert nichts für andere und erweitert nicht die ohnehin erlaubte Sicht

## [0.49.1] - 2026-08-13
- Aufgaben: baugleiche Aufgabe kann jetzt "aus der Gruppe gelöst" werden, um Turnus/Name/Notiz nur für einen einzelnen Bereich abweichend einzustellen, ohne die anderen Bereiche oder die Erledigungs-Historie zu beeinflussen

## [0.49.0] - 2026-08-12
- Aufgaben: optional zuständige Gruppen zuweisbar (zusätzlich zum Bereich) – ohne Auswahl gelten automatisch alle Gruppen des Bereichs wie bisher; neue Spalte + Filter "Gruppen" in der Aufgaben-Tabelle
- Fix: Überfällig-Benachrichtigungen gingen bislang an alle Gruppen eines Bereichs, auch wenn eine Aufgabe nur eine davon betrifft (z.B. Gastronomie bei einer reinen Hausmeister-Aufgabe im gemeinsam genutzten Ausschank) – berücksichtigt jetzt die explizite Gruppen-Zuordnung, falls gesetzt

## [0.48.0] - 2026-08-12
- Aufgaben: Verwaltung auf kompakte, filterbare Tabelle umgestellt (Suche, Bereich/Turnus/Status-Filter, Seiten) statt langer Aufklapp-Liste – skaliert für 100+ Aufgaben, Bearbeiten bleibt weiterhin inline

## [0.47.2] - 2026-08-12
- Aufgaben: Bereiche im Bearbeiten-Formular jetzt einfach an-/abwählbar (statt separater "hinzufügen"-Liste) – Abwählen entfernt die Aufgabe aus dem Bereich (mit Bestätigungsabfrage, da die Erledigungs-Historie dabei verloren geht)

## [0.47.1] - 2026-08-12
- Aufgaben: beim Bearbeiten nachträglich weitere Bereiche zuweisen möglich, statt nur beim Anlegen

## [0.47.0] - 2026-08-12
- Aufgaben: mehrere Bereiche auf einmal auswählbar beim Anlegen – für baugleiche Aufgaben (z.B. "Lüftung reinigen" in mehreren Räumen) reicht ein Formular; jeder Bereich behält eigene Erledigungen, Name/Turnus/Notiz bleiben beim Bearbeiten für alle gemeinsam änderbar

## [0.46.0] - 2026-08-12
- Aufgaben: neuer Turnus "Nach Bedarf" für Aufgaben ohne festen Rhythmus – wird nie automatisch gelb/rot, löst keine Erinnerung aus

## [0.45.0] - 2026-08-12
- Aufgaben: optionale Notiz (Stichpunkte) ergänzen, sichtbar direkt auf der Bereichsseite

## [0.44.0] - 2026-08-12
- Aufgaben: neuer Turnus "Alle 2 Wochen" ergänzt

## [0.43.1] - 2026-08-12
- Fix: "Erledigt"-Button gab keine sichtbare Rückmeldung, was zu versehentlichem Mehrfach-Abhaken führte – Button sperrt jetzt beim Klick, erledigte Aufgabe wird kurz hervorgehoben

## [0.43.0] - 2026-08-10
- Aufbauten können jetzt mehrere Bereiche gleichzeitig betreffen (z.B. eine Party in mehreren Räumen) – gemeinsamer Name, aber jeder Bereich behält eigene Fotos/Notiz; bestehende Aufbauten werden automatisch migriert

## [0.42.1] - 2026-08-09
- Aufbauten: Bearbeiten-Möglichkeit ergänzt, Notizen als Stichpunktliste statt Fließtext
- Löschbestätigung für "Meine Geräte" (Push-Abos) ergänzt, die bislang fehlte

## [0.42.0] - 2026-08-09
- Neue Funktion "Aufbauten": Referenzfotos je Bereich, wie für bestimmte Veranstaltungen umgebaut werden soll; Pauschalkräfte dürfen keine anlegen (neues Nutzer-Flag)

## [0.41.0] - 2026-08-09
- Neue Rolle "Entwickler": voller Zugriff auf Bereiche/Aufgaben/Inventar/Meldungen/Termine/Gruppen/System-Status, aber explizit kein Zugriff auf Benutzerverwaltung oder Zeiterfassung (weder Einsicht noch Bearbeitung)

## [0.40.0] - 2026-08-08
- Zeiterfassung: Änderungsprotokoll per Hash-Kette verkettet (erkennt nachträgliche Manipulation direkt an der Datenbank) + PDF-Export je Nutzer
- Neuer selektiver Struktur-Export/-Import (JSON): Gruppen/Bereiche/Aufgaben/Inventar/Termine gezielt auf eine neue Instanz übernehmen, ohne Test-Nutzer mitzuschleppen

## [0.39.0] - 2026-08-08
- Zeiterfassung: Änderungsprotokoll für nachträgliche Korrekturen (wer hat wann welche Buchung hinzugefügt, geändert oder gelöscht)

## [0.38.0] - 2026-08-08
- Neue Funktion "Urlaub": Selbsteintragung + Eintragung für andere durch Admin/Schichtleiter, "Wer hat gerade Urlaub?"-Kachel im Dashboard

## [0.37.0] - 2026-08-07
- Neue Funktion "Termine": wiederkehrende/einmalige Termine (z.B. Mülltonnen-Abholung) mit Erinnerung, Dashboard-Widget und eigener Verwaltungsseite

## [0.36.0] - 2026-08-03
- Changelog in der App sichtbar unter /changelog, Versionsnummer in der Sidebar verlinkt dorthin

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
