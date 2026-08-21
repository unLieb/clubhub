# Changelog

Alle nennenswerten Änderungen an ClubHUB, neueste zuerst. Format angelehnt an
[Keep a Changelog](https://keepachangelog.com/), Versionierung nach
[SemVer](https://semver.org/lang/de/). Jeder Eintrag entspricht einem
Versions-Bump in `VERSION` und einem eigenen Commit in der Git-Historie
(`git log` für den vollen Diff).

## [0.90.2] - 2026-08-21
- Barcode-/QR-Scanner: Genauigkeit verbessert, um Fehllesungen durch Unschärfe zu vermeiden. Ein Code wird jetzt erst als gültig übernommen, wenn er zweimal hintereinander innerhalb von 500ms identisch gelesen wurde (Mehrfach-Bestätigung), statt sofort beim ersten – noch unscharfen – Frame. Zusätzlich wird kontinuierlicher Autofokus angefordert (sofern Kamera/Browser das unterstützen) und die Auslesefrequenz auf 12 FPS gesetzt. Betrifft beide Scanner (Wareneingang im Inventar und den neuen Kamera-Button am Barcode-Feld), da beide denselben gemeinsamen Helfer nutzen.

## [0.90.1] - 2026-08-20
- Inventar-Verwaltung: Direkt neben dem Barcode-Feld im Artikel-Formular ("Neuer Artikel" / "Artikel bearbeiten") gibt es jetzt einen kleinen Kamera-Button – öffnet ein Scanner-Modal, der erkannte Barcode wird automatisch ins Feld übernommen, ganz ohne ihn abtippen zu müssen.

## [0.90.0] - 2026-08-20
- Inventar: Neue Barcode-Scan-Funktion für den Wareneingang ("Wareneingang / Scannen") – öffnet ein Kamera-Scanner-Modal (html5-qrcode), erkennt einen Barcode und bucht automatisch die eingestellte Menge auf den verknüpften Artikel, inkl. Erfolgston und Eintrag im Inventar-Log. Unbekannte Barcodes lassen sich direkt im selben Schritt per Dropdown einem bestehenden Artikel zuweisen. Barcodes können auch manuell in der Artikelverwaltung hinterlegt werden.

## [0.89.0] - 2026-08-20
- Eigenständige Login-Seite (`/login`) entfernt: Das Login-Formular (inkl. Passkey-Login) lebt jetzt ausschließlich im Dashboard (`/`), wohin bisher separate "Anmelden"-Links auch schon verwiesen haben. Nicht angemeldete Aufrufe geschützter Seiten leiten weiterhin mit `next`-Rücksprung dorthin um, damit man nach dem Login automatisch bei der eigentlich gewünschten Seite landet.

## [0.88.2] - 2026-08-20
- Passkey-Login: Der "Mit Passkey anmelden"-Button samt Autofill (Conditional UI) erscheint jetzt auch im ins Dashboard eingebetteten Mini-Login-Formular (`/`), nicht nur auf der eigenständigen `/login`-Seite – das ist für die meisten Nutzer der tatsächliche erste Anmelde-Ort. Ein Fehlschlag der unauffälligen Hintergrund-Autofill-Abfrage zeigt außerdem korrekterweise keine Fehlermeldung mehr an, da der Nutzer diesen Vorgang nie aktiv gestartet hat.

## [0.88.1] - 2026-08-20
- Passkey-Login: Autofill (Conditional UI) im Benutzername-Feld auf der Login-Seite – unterstützende Browser schlagen gespeicherte Passkeys direkt in der Tastatur-/Autofill-Leiste vor, ganz ohne den separaten "Mit Passkey anmelden"-Button antippen zu müssen.

## [0.88.0] - 2026-08-20
- Passkey-Login (WebAuthn/FIDO2): Im Profil lassen sich jetzt Passkeys (Fingerabdruck, Gesichtserkennung, Geräte-PIN oder Sicherheitsschlüssel) hinzufügen, benennen und entfernen. Auf der Login-Seite (`/login`) steht dafür ein neuer "Mit Passkey anmelden"-Button bereit, der ganz ohne vorherige Benutzernamen-Eingabe funktioniert. Der Terminal-Modus für die Zeiterfassung bleibt davon unberührt und nutzt weiterhin ausschließlich die PIN-Eingabe.

## [0.87.0] - 2026-08-20
- Meldungen: Produkt-/Shop-Links werden jetzt in der Detail-Karte angezeigt – ein gespeicherter Produkt-Link erscheint als anklickbarer "Zum Shop / Produkt"-Button, und URLs, die direkt im Beschreibungstext getippt wurden, werden beim Anzeigen automatisch in klickbare Links umgewandelt.

## [0.86.0] - 2026-08-20
- Dashboard/Bereichsübersicht: Der grüne Haken-Quick-Button auf einer Bereichs-Kachel ("Tägliche erledigen") läuft jetzt per fetch() ohne Full-Page-Reload – nur diese eine Kachel (Status-Punkt, Rahmenfarbe, Text) aktualisiert sich, die Scroll-Position bleibt exakt erhalten, egal ob mobil oder am Desktop. Betrifft `/` und `/rooms`, da beide dasselbe Bereichs-Grid nutzen.

## [0.85.2] - 2026-08-20
- Historie (`/history`): "Rückgängig" läuft jetzt per fetch() ohne Full-Page-Reload – die betroffene Zeile verschwindet direkt aus der Tabelle, Seitenzahl/Filter und Scroll-Position bleiben dabei exakt erhalten statt wie bisher auf Seite 1 zurückzuspringen. Klassischer Formular-Fallback (ohne JS) respektiert jetzt ebenfalls den aktuellen Filter-/Seiten-Zustand statt immer auf die unfilterte erste Seite umzuleiten.

## [0.85.1] - 2026-08-20
- Historie (`/history`): Gruppen-Badges in der Spalte „Erledigt von" springen nicht mehr je nach Namenslänge hin und her – Name steht links, Badges sind rechtsbündig auf einer festen vertikalen Linie ausgerichtet (`flex justify-between`). Nebenbei behoben: Bei mehreren/langen Badges liefen sie vorher über den Zellenrand hinaus statt sauber umzubrechen.

## [0.85.0] - 2026-08-19
- Neu: Nutzer deaktivieren statt löschen (Soft-Delete). Der "Löschen"-Button in der Nutzerverwaltung ist jetzt ein "Deaktivieren"/"Aktivieren"-Umschalter – deaktivierte Konten können sich nicht mehr einloggen oder einstempeln (auch eine bereits offene Sitzung wird sofort beendet), tauchen aber weiterhin mit vollem Namen und Gruppen-Badge in Historie und Zeiterfassung auf sowie in der "Urlaub für jemand anderen eintragen"-Auswahl bereits bestehender Einträge. Neu angelegt werden können sie dort nicht mehr, bis das Konto reaktiviert wird.
- Der letzte verbleibende aktive Admin sowie das eigene Konto lassen sich nicht deaktivieren, damit niemand versehentlich alle aussperrt.

## [0.84.0] - 2026-08-19
- Meldungen (`/reports`, Kategorie „Materialwunsch / Anschaffung"): neues optionales Feld „Produkt-Link / Shop-URL". Sobald eine gültige URL eingefügt wird, ruft die App im Hintergrund die Open-Graph-Daten der Seite ab und befüllt die Beschreibung automatisch mit Titel/Beschreibung (nur falls das Feld noch leer ist) sowie das Produktbild als Vorschau, das beim Absenden automatisch als Foto an die Meldung angehängt wird.

## [0.83.2] - 2026-08-19
- Meldungen (`/reports`, Formular „Neue Meldung"): Der Bild-Upload nutzt jetzt dieselbe Dropzone-Komponente wie an anderen Stellen der App (gestrichelter Rahmen, Icon, „Fotos hierher ziehen oder klicken / antippen", Drag&Drop, Einfügen per Strg+V) statt des nackten Datei-Auswahl-Felds – inklusive Vorschau-Thumbnails mit „✕"-Button zum Entfernen einzelner Fotos vor dem Absenden.

## [0.83.1] - 2026-08-19
- Meldungen (`/reports`, Formular „Neue Meldung"): Der Placeholder im Beschreibungsfeld passt sich jetzt der Kategorie an – bei „Materialwunsch / Anschaffung" erscheint „Was wird benötigt? Z.B. Akku-Schrauber für die Werkstatt oder neue Kaffeemaschine…" statt des Standard-Placeholders.

## [0.83.0] - 2026-08-19
- Meldungen (`/reports`, Formular „Neue Meldung"): Das „Gruppe"-Dropdown ist jetzt wie bei Terminen eine anklickbare Chip-Auswahl (Automatisch (Bereich), Alle (Betriebsweit), einzelne Gruppen) statt eines Auswahlmenüs. Anders als bei Terminen bleibt es dabei Single-Select (Radio-Verhalten) für eine eindeutige Zuständigkeit – der aktive Chip ist grün/dunkel hervorgehoben.

## [0.82.1] - 2026-08-19
- Historie (`/history`): Der farbige Gruppen-Badge sitzt jetzt in der Spalte „Erledigt von" direkt neben dem Namen des ausführenden Mitarbeiters (z.B. Sebastian `Hausmeister`) statt in der Spalte „Bereich" – zeigt damit die Gruppenzugehörigkeit der Person statt der Bereichsgruppen. Spalte „Datum" unverändert.

## [0.82.0] - 2026-08-19
- Nutzer-Verwaltung: Beim Anlegen und Bearbeiten ist jetzt mindestens eine Gruppe Pflicht – ohne Gruppe war ein Nutzer bisher versehentlich von Bereichen/Meldungen/Terminen ausgesperrt, sobald die gruppenbasierte Sichtbarkeit griff.
- Neu: Gruppenfarben. Jede Gruppe bekommt in der Gruppen-Verwaltung eine eigene Farbe aus einer festen Palette (10 Farben) zugewiesen – bestehende Gruppen automatisch reihum vorbelegt. Namens-Badges nutzen diese Farbe jetzt systemweit: Historie (neu), Termine, Meldungen, Inventar-Artikel sowie die Nutzer-/Bereichsverwaltung zeigen auf einen Blick farblich, zu welcher Gruppe ein Eintrag oder Nutzer gehört.

## [0.81.0] - 2026-08-19
- Gruppen-Auswahl bei Meldungen und Terminen vereinheitlicht: Auswahlfeld heißt jetzt in beiden Modulen "Gruppe", beide bieten neu "Alle (Betriebsweit)" für eine bewusste betriebsweite Freigabe (bei Meldungen zusätzlich zu "Automatisch (Bereich)" und einer einzelnen Gruppe).
- Termine erlauben jetzt die Auswahl mehrerer Gruppen gleichzeitig (z.B. Gastro + Küche) statt nur einer einzigen - "- Nur ich (Privat) -" und "Alle (Betriebsweit)" bleiben als eigene, sich gegenseitig ausschließende Optionen erhalten.
- Neu: Gruppenbasierte Sichtbarkeit für Bereiche, Meldungen und Termine (analog zum bereits bestehenden Verhalten im Inventar) - Schichtleiter/Mitarbeiter ohne Admin-Rechte sehen nur noch Bereiche/Meldungen/Termine ihrer eigenen Gruppe(n) sowie ungruppierte bzw. "Alle (Betriebsweit)"-Einträge; eigene private Termine bleiben wie bisher nur für sich selbst sichtbar. Admins behalten uneingeschränkten Zugriff auf alle Module und Gruppen.
- Technisch: neue m:n-Tabelle für Termin-Gruppen (Altdaten aus der bisherigen Einzel-Gruppe automatisch übernommen), Gruppen-Benachrichtigungen bei mehreren Zielgruppen bzw. "Alle (Betriebsweit)" jetzt dedupliziert (kein doppelter Push mehr, wenn jemand in mehreren Zielgruppen ist oder ein Kanal mehrfach betroffen wäre).

## [0.80.1] - 2026-08-19
- Die "±X gegenüber gestern"-Anzeige bei der Dashboard-Kachel „Erledigt heute“ entfernt: Bei „Nach Bedarf“-Aufgaben (an einem Tag nötig, am nächsten nicht) erweckte der Vergleich fälschlich den Eindruck, es sei weniger gearbeitet worden.

## [0.80.0] - 2026-08-19
- Aufgaben-Sortierung in der Bereichsansicht (`/room/{id}`) an die praktische Arbeitsweise angepasst: Turnus kurz vor lang (Täglich/Wöchentlich zuerst, Monatlich/Quartalsweise darunter), „Nach Bedarf“-Aufgaben jetzt bewusst am Ende statt am ursprünglichen Anlage-Zeitpunkt. Innerhalb desselben Turnus alphabetisch nach Name, damit die Liste eine feste Reihenfolge behält statt bei jedem Aufruf in Anlage-Reihenfolge zu erscheinen. Bereits erledigte Aufgaben bleiben wie bisher vollständig ausgeblendet, bis sie erneut fällig werden.

## [0.79.1] - 2026-08-19
- Fix: Das Buchen von Inventar-Bestand (`/inventar`, „Buchen“-Button je Artikel) lief per echtem Formular-POST und sprang danach an den Seitenanfang zurück. Läuft jetzt per fetch() ohne Full-Page-Reload: Scroll-Position bleibt exakt erhalten, nur die betroffene Artikel-Karte (Ist-Bestand, Fortschrittsbalken, Status-Badge „Im Soll“/„Niedriger Bestand“) wird ausgetauscht statt des gesamten Grids – eine aufgeklappte „Details“-Ansicht bleibt dabei offen.

## [0.79.0] - 2026-08-19
- Monats-/Freier-Zeitraum-Filter und PDF-Export in der Zeiterfassung sind jetzt in Mitarbeiter- (`/timeclock`) und Admin-Ansicht (`/admin/timeclock`) identisch implementiert (gemeinsame Filterleiste + gemeinsames JS-Modul statt zweier Parallel-Implementierungen).
- Admin kann jetzt ebenfalls zwischen „Monat“ und „Freier Zeitraum“ (Von/Bis) umschalten, um flexible Abrechnungszeiträume oder Berichte einzusehen – vorher war die Admin-Ansicht fest auf Monate beschränkt.
- PDF-Export (Mitarbeiter & Admin) und der neue CSV-Export der Admin-Ansicht folgen jetzt strikt dem gerade aktiven Filter (Monat oder Von/Bis) statt PDF immer die komplette Historie bzw. CSV immer nur den Monat zu exportieren.
- Buchung hinzufügen/bearbeiten/löschen sowie der App-Import kehren in der Admin-Ansicht jetzt ebenfalls in den zuvor gewählten Zeitraum zurück (Monat oder Von/Bis) statt immer auf den aktuellen Monat zurückzuspringen.

## [0.78.0] - 2026-08-19
- Neue Meldungs-Kategorie „Materialwunsch / Anschaffung“ unter `/reports`: für einmalige Anschaffungen, die an keinen Bereich gebunden sind (z.B. "neue Kaffeemaschine fürs Personal"), sondern nur an eine zuständige Gruppe – man bestellt für seine Gruppe, nicht für einen Raum. Beim Erstellen werden Bereich und Priorität automatisch ausgeblendet/nicht verlangt, die zuständige Gruppe wird stattdessen zur Pflichtangabe.
- Kennzeichnung mit blauem "Anschaffung"-Badge; über den Status-Regler wie jede andere Meldung auf "Erledigt" setzbar – wandert dann ins bestehende Archiv ("Erledigte Meldungen"), ohne das Inventar zu berühren.
- Technisch: `reports.room_id` ist jetzt nullable (SQLite-Tabellen-Rebuild-Migration beim Start, Altdaten bleiben unverändert erhalten); alle Anzeigen mit Bereichs-/Prioritätsbezug (Meldungskarten, Dashboard, Benachrichtigungen) sind entsprechend gegen einen fehlenden Bereich abgesichert.

## [0.77.0] - 2026-08-19
- Mitarbeiter-Zeiterfassung (`/timeclock`) auf das UI-Muster der Admin-Zeiterfassung umgestellt: gleiche Whitecard mit Monats-Pfeilen `<`/`>` und Datepicker, jetzt zusätzlich umschaltbar auf einen freien Von/Bis-Zeitraum. KPI-Kacheln ("Heute verdient", "Verdient im Monat/Zeitraum", "Überstunden" bzw. "Erfasste Buchungen" im freien Zeitraum) sitzen wie im Admin-Bereich direkt in der Whitecard.
- Verlauf ist jetzt eine Tabelle (Datum/Kommen/Gehen/Dauer/Aktionen) statt einer Liste, zeigt den kompletten gewählten Zeitraum statt nur der letzten 20 Buchungen, kein Mitarbeiter-Dropdown (nur eigene Daten). "Als PDF exportieren" sitzt oben rechts in der Tabellenleiste, an der Position des "Daten"-Buttons im Admin-Bereich.
- Monats-/Zeitraum-/Moduswechsel läuft komplett ohne Full-Page-Reload (fetch()), inkl. URL-Synchronisation; Bearbeiten/Löschen einer eigenen Buchung kehrt danach in den zuvor gewählten Zeitraum zurück statt auf den aktuellen Monat zurückzuspringen.

## [0.76.1] - 2026-08-19
- Fix: Der 2-Farben-Inventarstatus aus 0.76.0 verglich den Ist-Bestand fälschlich mit dem Soll-Bestand statt mit dem Mindestbestand – dadurch wurde z.B. „3/3 Kanister“ fälschlich rot als „Niedriger Bestand“ angezeigt. Vergleich läuft jetzt korrekt gegen den Mindestbestand (mit Fallback auf die Hälfte des Soll-Bestands, falls nicht gesetzt); die Detail-Ansicht zeigt Soll-Bestand und Mindestbestand wieder als getrennte Werte.

## [0.76.0] - 2026-08-19
- Inventar-Statusanzeige auf klares 2-Farben-System vereinfacht: nur noch grün „Im Soll“ (Bestand über Mindestbestand) oder rot „Niedriger Bestand“ (Bestand auf oder unter Mindestbestand) – die bisherigen Zwischenstufen „Kritisch“ (orange) und „Bestand leer“ sowie die Prozentanzeige im Detailbereich entfallen. Badge, Fortschrittsbalken und „Nachbestellen“-Button sind jetzt einheitlich rot eingefärbt und der Button ist bei „Im Soll“ ausgeblendet.
- Fix: Die Bestands-Benachrichtigung an Gruppen (vorher an die inzwischen entfernte „Kritisch“-Stufe gekoppelt) löst jetzt korrekt bei „Niedriger Bestand“ aus.

## [0.75.0] - 2026-08-18
- Inventar-Verwaltung (`/admin/inventory`): Anlegen, Bearbeiten und Löschen von Artikeln laufen jetzt per fetch() ohne Full-Page-Reload – nur die Tabelle wird aktualisiert, die aktuell gewählte Sortierung bleibt dabei erhalten (auch der Sortierungs-Wechsel selbst läuft jetzt ohne Reload). Bei einem fehlgeschlagenen Speichern bleibt der Bearbeiten-Drawer offen und zeigt eine Fehlermeldung statt Datenverlust.
- Fix: Das ⋮-Kebab-Menü einer Artikelzeile klappt jetzt automatisch nach oben auf (`bottom-full`), wenn am Tabellenende nicht mehr genug Platz nach unten ist – vorher wurde es vom horizontal scrollbaren Tabellen-Wrapper abgeschnitten.

## [0.74.1] - 2026-08-18
- Termine ohne Gruppe ("– nur ich –") sind jetzt privat: sichtbar nur für den/die Ersteller:in selbst und Admins, nicht mehr für andere Kolleg:innen oder Schichtleiter – weder unter `/appointments` noch im Dashboard. Termine mit Gruppe bleiben wie bisher für alle sichtbar.

## [0.74.0] - 2026-08-18
- Aufgaben abhaken (Bereichsansicht) läuft jetzt ohne Full-Page-Reload: "Erledigt" sendet per fetch() an den Server, die Karte zeigt sofort ein Haken-Icon + grüne Hervorhebung und blendet danach sanft aus (Höhe/Deckkraft-Animation, kein Layout-Sprung).
- Neuer Toast am unteren Bildschirmrand bestätigt "„Aufgabe“ als erledigt markiert" mit "Rückgängig"-Option (macht die Buchung serverseitig innerhalb weniger Minuten wieder rückgängig); bei einem Fehler erscheint die Karte automatisch wieder und ein roter Fehler-Toast informiert.
- Fix: "Nach Bedarf"-Aufgaben verschwinden nach dem Abhaken jetzt ebenfalls aus der offenen Liste (vorher blieben sie stehen, was wie ein kaputter Button wirkte) und tauchen automatisch am nächsten Tag wieder auf.

## [0.73.1] - 2026-08-18
- Fix: Aufgaben-Karten auf der Bereichs-Detailseite liefen auf schmalen Bildschirmen über den rechten Rand hinaus, sobald zusätzlich zum Aufgabennamen ein "Nach Bedarf"- oder Wochentags-Badge angezeigt wurde – dabei wurden "Erledigt"-Button und Kebab-Menü teilweise oder komplett aus dem sichtbaren Bereich gedrängt. Die Titelzeile bricht jetzt sauber um.

## [0.73.0] - 2026-08-18
- Mobiles Dashboard neu priorisiert: ganz oben direkt unter dem Header steht jetzt ein "Heute anstehend"-Block mit Terminen für heute und überfälligen Reinigungen, danach folgen die KPI-Kacheln (2x2), Bereiche-Schnellzugriff, eine Termine-Vorschau samt offenen Meldungen und ganz unten die letzten Reinigungen. Desktop-Ansicht unverändert.
- Sidebar: Versionsnummer (`v{{ app_version }}`) steht jetzt als kleiner, dezenter Subtext direkt unter dem "ClubHUB"-Schriftzug statt rechts daneben.

## [0.72.0] - 2026-08-18
- Zeiterfassung (Verwaltung): Monatswechsel (Pfeile, Monats-Picker, "Aktueller Monat") läuft jetzt ohne Full-Page-Reload – die Tabelle samt KPIs wird per fetch() nachgeladen, während des Ladens dezent abgedunkelt (opacity-50, kein Layout-Sprung), die URL wird dabei lautlos per history.replaceState synchron gehalten (Link bleibt teilbar). Pilot-Umsetzung für ein Muster, das bei Bedarf auf weitere Seiten (Historie, Inventar, Bereiche) übertragen werden kann.

## [0.71.2] - 2026-08-18
- "Daten"-Dropdown (Zeiterfassung): Beschriftungen vereinheitlicht ("Importieren (.txt / .csv)" / "Exportieren (.txt / .csv)")
- CSV-Export ist jetzt strukturgleich mit dem Import und direkt re-importierbar: Kopfzeile beginnt mit "#" und benennt die Zeit-Spalten CHECKIN/CHECKOUT statt Kommen/Gehen, damit der Import-Parser die exportierte Datei unverändert wieder einlesen kann

## [0.71.1] - 2026-08-18
- Feinschliff am "Daten"-Dropdown (Zeiterfassung): Emoji-Icons durch saubere System-Icons ersetzt (Cloud-Upload, Tabellenblatt, Dokument) und das Menü explizit rechtsbündig unter dem Button ausgerichtet

## [0.71.0] - 2026-08-18
- Steuerungselemente unter Verwaltung > Zeiterfassung aufgeräumt: "App-Import" und "Buchung hinzufügen" aus dem Seiten-Header entfernt, alle Aktionen jetzt in einer gemeinsamen Leiste direkt über der Tabelle (Mitarbeiter-Filter links, Dropdown "Daten" mit Importieren/CSV-Export/PDF-Export sowie der grüne "Buchung hinzufügen"-Button rechts)

## [0.70.0] - 2026-08-18
- Neu: CSV-Export unter Verwaltung > Zeiterfassung, im selben Pipe-getrennten Format wie der App-Import (Spalten: Datum|Mitarbeiter|Kommen|Gehen|Dauer_Minuten|Dauer_Formatiert|Notizen), respektiert den gewählten Monat und optional den Mitarbeiter-Filter, UTF-8 mit BOM für saubere Umlaut-Darstellung in Excel

## [0.69.0] - 2026-08-18
- Zeiterfassungs-Import um intelligente Duplikat-Erkennung und Konflikt-Lösung erweitert: 100% identische Zeilen werden automatisch übersprungen, neue Tage automatisch übernommen; bei abweichenden Zeiten am selben Tag öffnet sich vor dem Speichern ein Konflikt-Modal mit Gegenüberstellung (Bestehend vs. Import), Einzelentscheidung (Bestehenden behalten / Durch Import ersetzen / Beide behalten) und globalen Sammel-Aktionen für alle Konflikte
- Nichts wird gespeichert, solange ungelöste Konflikte offen sind - erst nach der Entscheidung landet der komplette Import (inkl. eindeutig neuer Zeilen) gesammelt in der Datenbank
- Abschluss-Toast fasst jetzt auch gelöste Konflikte mit auf (z.B. "1 Konflikt gelöst (1 ersetzt)")

## [0.68.0] - 2026-08-18
- Neue einheitliche Dropzone-Upload-Komponente (gestrichelter Rahmen, Drag & Drop, Screenshot-Einfügen per Strg+V, Miniaturvorschau mit Dateiname/Größe, ✕ zum Entfernen) app-weit auf allen Datei-Uploads eingeführt: Feedback-/Bug-Modal, Artikelbild-Upload (Inventar), Aufbau-Fotos (Bereichs-Detailseite) und Zeiterfassungs-Import

## [0.67.0] - 2026-08-18
- Feedback-/Bug-Modal: Screenshots lassen sich jetzt optional anhängen (mehrere möglich), erscheinen als Thumbnails in Verwaltung > Feedback und werden im "Als Claude-Prompt kopieren"-Text erwähnt
- Entwickler sehen jetzt wie Admins die komplette Backup/Wiederherstellung-Sektion unter Verwaltung > System (automatische Sicherungen, manuelles Backup, Struktur-Export/-Import) inkl. aller zugehörigen Aktionen - Schichtleiter weiterhin ausgeschlossen
- Fix: Kebab-Button bei Urlaub/Terminen hatte einen dauerhaft sichtbaren Rahmen statt wie überall sonst nur beim Hover farblich hervorzutreten

## [0.66.1] - 2026-08-18
- Fix: Kebab-Menü bei Urlaub und Terminen war oben statt vertikal mittig ausgerichtet, wenn die Karte durch Datum/Status-Badges zweizeilig wurde

## [0.66.0] - 2026-08-18
- Schwebender Feedback-Button verkleinert: statt einer Pille mit Dauertext jetzt ein kompaktes rundes 💬-Icon unten rechts, der Text "Feedback / Bug melden" erscheint nur noch als Tooltip-Label beim Hover - verdeckt dadurch keine Tabellen-Paginierung oder andere Interaktionselemente mehr

## [0.65.0] - 2026-08-18
- Kebab-Menü (⋮) als einheitlicher Standard für Bearbeiten/Löschen in der ganzen App etabliert: Bereiche, Nutzer, Gruppen, Benachrichtigungen, Inventar, Zeiterfassung (Verwaltung + Selbstbedienung), Urlaub und Termine zeigen jetzt statt direkter Stift-/Mülleimer-Icons ein dezentes Menü mit "Bearbeiten" (Stift-Icon) und "Löschen" (rot, Mülleimer-Icon)
- Menü bleibt weiterhin nur für Nutzer mit den jeweils passenden Bearbeitungs-/Admin-Rechten sichtbar, unverändert gegenüber der bisherigen Berechtigungslogik je Seite
- Bereichs-Detailseite (Aufgaben): Kebab-Einträge um dieselben Icons ergänzt für volle Konsistenz mit den anderen Seiten

## [0.64.1] - 2026-08-17
- Fix: Umami-Tracking läuft nicht mehr auf localhost/bei rohen Datei-Vorschauen mit (verfälschte sonst die echte Statistik)

## [0.64.0] - 2026-08-17
- Umami-Analytics-Tracking eingebunden (self-hosted auf stats.nifflheim.de)

## [0.63.0] - 2026-08-17
- Bereichs-Detailseite: Stift-Icon direkt neben dem "Erledigt"-Button entfernt (verhindert versehentliche Klicks beim Abhaken), stattdessen dezentes Kebab-Menü (⋮) am rechten Rand mit "Bearbeiten"/"Löschen"
- Kebab-Menü nur für Admins/Schichtleiter/Entwickler sichtbar - normale Mitarbeiter sehen an der Aufgaben-Karte ausschließlich den "Erledigt"-Button

## [0.62.0] - 2026-08-17
- Änderungsprotokoll unter Verwaltung > Zeiterfassung für lange Historien optimiert: schlankere Kopfzeile mit Info-Icon (Tooltip statt Fließtext-Erklärung), "Kette prüfen"-Button + Status-Badge ("Kette intakt") jetzt direkt oben rechts in der Kopfzeile
- Textliste durch strukturierte Tabelle ersetzt (Zeitpunkt, Aktion, Bearbeiter, Mitarbeiter, betroffene Schicht inkl. "vorher"-Angabe bei Bearbeitungen)
- Anzeige initial auf die 10 neuesten Einträge begrenzt, "Ältere Einträge laden (+10)"-Button blendet weitere bei Bedarf ein

## [0.61.0] - 2026-08-17
- Neu: App-Import unter Verwaltung > Zeiterfassung. Buchungen aus dem Export der "Zeiterfassung Pro" App (DynamicG, z.B. `timerec-workunits-pro.txt`) lassen sich per Datei-Upload einem Mitarbeiter zuordnen und importieren
- Bereits vorhandene Buchungen (gleicher Mitarbeiter + exakt gleiche Kommen-Zeit) werden automatisch als Duplikat übersprungen, ungültige/leere Zeilen werden ignoriert
- Rückmeldung per Toast (z.B. "14 Buchungen für Sebastian importiert, 2 Duplikate übersprungen"), bei fehlerhafter Datei erscheint ein roter Fehler-Toast

## [0.60.1] - 2026-08-17
- Zeiterfassung skaliert jetzt für viele Mitarbeiter: Einzelkacheln pro Person oben ersetzt durch 3 kompakte Monats-KPIs (Gesamtstunden, Buchungen, aktive Mitarbeiter); die Monatsstunden je Person stehen stattdessen direkt im "Alle Mitarbeiter"-Dropdown (z.B. "Sebastian (16 Std. 42 Min.)")
- Alle Arbeitszeiten (Tabelle, KPIs, Dropdown) als "X Std. Y Min." statt Dezimalwert
- Tabellenspalte "Datum" zeigt jetzt den Wochentag (z.B. "Do, 23.07.2026")
- PDF-Export folgt jetzt der Mitarbeiter-Auswahl im Filter-Dropdown

## [0.60.0] - 2026-08-17
- Verwaltung > Zeiterfassung neu strukturiert für bessere Skalierbarkeit: Monatsfilter (Standard aktueller Monat, mit Vor/Zurück-Navigation) ersetzt die per-Nutzer-Akkordeons durch eine zentrale, nach Mitarbeiter filterbare Tabelle aller Buchungen des gewählten Monats
- Neue Übersicht der Monats-Gesamtstunden pro Mitarbeiter oben auf der Seite
- Bearbeiten und "Buchung hinzufügen" laufen jetzt über einen Slide-Over-Drawer statt Inline-Datumsfeldern in der Liste; der gewählte Monat bleibt nach dem Speichern/Löschen erhalten

## [0.59.1] - 2026-08-17
- Verwaltung > Nutzer: Rollen-Auswahl (Anlegen + Bearbeiten) alphabetisch sortiert (Admin, Entwickler, Mitarbeiter, Pauschalkraft, Schichtleiter)

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
