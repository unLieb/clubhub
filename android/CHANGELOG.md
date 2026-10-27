# Changelog – ClubHUB Android-App

Änderungen an der Android-App (Ordner `android/`), neueste zuerst. Die Versionsnummer
entspricht `versionName` in `app/build.gradle.kts`. Server-Änderungen stehen im
[CHANGELOG.md](../CHANGELOG.md) im Projektstamm (App und Server werden getrennt versioniert).

Jeder Eintrag nennt Datum, Commit-Uhrzeiten und Commit-Hashes (`git show <hash>` zeigt den
vollständigen Diff). Die Uhrzeiten sind Zeitpunkte der Commits und geben den Arbeitsverlauf
wieder – sie sind keine gemessene Arbeitszeit (Abstimmung, Tests am Handy und Wartezeiten
sind darin nicht abgebildet).

## [1.5.1] - 2026-10-27
- Ist ein Server nicht erreichbar, bleibt die App nicht mehr leer/weiß: Sie zeigt eine eigene Fehleransicht mit Erklärung („Frannz ist nicht erreichbar – Das Handy hat gerade keine Internetverbindung" bzw. Servername, Fehlerursache), „Erneut versuchen" und je einem Knopf „Zu <Server> wechseln" für die anderen Server sowie „Server verwalten…". Die Ansicht gehört zur App und nicht zur Webseite und ist deshalb auch erreichbar, wenn die Seite selbst nicht lädt.
- Antwortet ein Server gar nicht (hängt), erscheint die Fehleransicht nach 25 Sekunden statt nach minutenlangem leerem Bildschirm; Serverfehler (5xx, z. B. 502 vom Proxy) werden ebenfalls so angezeigt. Beides geht zusätzlich ins Fehlerprotokoll des Servers.
- Die Fehleransicht verschwindet von selbst, sobald die Seite doch lädt; beim Wechsel auf einen anderen Server wird sie zurückgesetzt.

## [1.5.0] - 2026-10-26
- Fehlerprotokoll: Scheitert das Laden einer Seite im WebView (Server nicht erreichbar, Verbindung zurückgesetzt, Zeitüberschreitung) oder antwortet der Server mit einem Fehler (5xx), meldet die App das mit dem genauen Android-Fehlercode (z. B. `net::ERR_CONNECTION_RESET`), der betroffenen Seite, der Verbindungsart (WLAN/Mobilfunk/kein Netz) und der App-Version an den Server (Verwaltung → Fehlerprotokoll, Quelle „Android-App“). Weil der Server in dem Moment meist nicht erreichbar ist, wird der Eintrag zwischengespeichert und beim nächsten erfolgreich geladenen Seitenaufruf bzw. beim Zurückkehren in die App nachgesendet (höchstens 20 Einträge, nicht älter als 6 Tage). Neue Berechtigung: Netzwerkstatus abfragen (keine Rückfrage am Handy).

## [1.4.3] - 2026-10-19
- Orte: „Test aus dem Hintergrund (20 s)" – bestimmt nach 20 Sekunden (App geschlossen, Handy gesperrt) den Standort aus dem Dienst heraus und zeigt das Ergebnis als Mitteilung. Damit lässt sich prüfen, ob die Ortung im Hintergrund funktioniert, nicht nur bei geöffneter App.
- Ist „Arbeit" gesetzt und lässt sich kein Standort bestimmen, werden Mitteilungen jetzt **zurückgehalten** statt angezeigt (vorher: angezeigt). Der Dienst prüft alle 5 Minuten (vorher 10) und zeigt Zurückgehaltenes spätestens nach 30 Minuten als eine Sammelmitteilung, auch wenn der Standort weiter unbekannt bleibt – nichts geht verloren, es gibt aber keine Flut.

## [1.4.2] - 2026-10-19
- Flutschutz bei Mitteilungen: Kommen mehr als 4 Mitteilungen eines Servers binnen 2 Minuten (z. B. morgens, wenn viele Aufgaben gleichzeitig fällig werden), werden die weiteren zu **einer** leise aktualisierten Sammelmitteilung („Kantine · 12 weitere Mitteilungen") zusammengefasst statt einzeln anzuzeigen.
- Orte-Regel zuverlässiger: pro Abruf wird nur noch **einmal** entschieden (vorher je Mitteilung eine eigene Standortabfrage mit bis zu 12 s Wartezeit), und wenn gerade kein neuer Standort zu bekommen ist, gilt der zuletzt bestimmte (bis 6 Stunden alt) – bisher wurde in dem Fall zur Sicherheit angezeigt, auch wenn man zu Hause war.
- Orte-Seite zeigt die **letzte Entscheidung mit Begründung** („zu Hause (40 m, aktuell) – verworfen", „kein Standort verfügbar – angezeigt") und hat den Knopf „Jetzt prüfen", der den Standort sofort bestimmt und das Ergebnis anzeigt. So lässt sich nachvollziehen, warum eine Mitteilung angezeigt wurde.
- Server-Symbol, „Orte", „Update" usw. verschwinden nicht mehr nach der Live-Aktualisierung der Seite: die App baut ihre Elemente automatisch wieder ein, sobald die Seite sich ändert.

## [1.4.1] - 2026-10-17
- Server-Wechsel-Fenster klappt auf Tablets und im Querformat jetzt immer ganz auf (vorher nur ein schmaler Streifen, den man hochziehen musste).
- Server-Wechsel als dauerhaftes Symbol (Server-Icon wie in Mattermost) statt nur über den Namen in der Dashboard-Überschrift: auf Tablet/Desktop neben dem Logo in der Seitenleiste (scrollt nicht weg, darunter steht „Servername · Version"), auf dem Handy als Eintrag „Servername" im „Mehr"-Menü auf jeder Seite; die Dashboard-Überschrift zeigt auf dem Handy Symbol und Name – nur das Symbol ist klickbar, auf dem Tablet steht dort nur der Name (das Symbol sitzt in der Seitenleiste). Die Symbole geben beim Antippen sichtbar Rückmeldung (kurz verkleinert mit Hintergrund). Auch mit nur einem Server vorhanden, damit weitere Server hinzugefügt werden können.
- Update-Prüfung schaut bei allen hinterlegten Servern nach und bietet die neueste Version an (nicht nur beim gerade aktiven Server).
- „Mehr"-Menü: neuer Eintrag „Update" prüft sofort auf eine neue App-Version und meldet auch „App ist aktuell"; im Fuß steht jetzt neben der Server- auch die App-Version.

## [1.4.0] - 2026-10-17
- Verteilung vom eigenen Server: signierte Release-APK (eigener Schlüssel außerhalb des Repos, `android/release.sh` baut sie und legt sie samt `latest.json` in den Server-Ordner `app/static/app/`). Der Download-Link steht in der Hilfe der Serveroberfläche, Frage „Gibt es auch eine native App?" (Server 1.53.0).
- Integrierter Updater: die App fragt höchstens alle 12 Stunden beim aktuellen Server nach einer neueren Version, lädt die APK mit Fortschrittsanzeige, prüft die SHA-256-Prüfsumme und übergibt sie dem Android-Installer. Einmalig muss „Installation aus dieser Quelle zulassen" erlaubt werden, bei jedem Update bleibt eine Bestätigung am Handy (Android-Vorgabe; ab Android 12 entfällt sie, wenn die App selbst die Vorversion installiert hat). Android prüft zusätzlich, dass die neue APK denselben Signaturschlüssel hat. (Commit am Abend, kein Hash - siehe `git log`)
- Hinweis: Die Release-Signatur unterscheidet sich von der bisherigen Debug-Installation. Beim ersten Wechsel muss die Debug-Version einmal deinstalliert werden (Server, Orte und Anmeldung danach neu eintragen). Ab dann laufen Updates ohne Deinstallation.

## [1.3.0] - 2026-10-17
- Orte: Verpasste Mitteilungen gehen nicht mehr verloren. Wer „Arbeit" festgelegt hat und gerade woanders ist, bekommt sie gesammelt als **eine** Mitteilung je Server („Kantine · 3 Mitteilungen verpasst"), sobald er auf Arbeit ankommt (höchstens 24 Stunden alt). Nur mit „Zu Hause" festgelegt bleibt es beim Verwerfen.
- Standortprüfung nur in der Arbeitszeit: die App nutzt die Arbeitszeit der Gruppen des Nutzers vom Server (keine zweite Einstellung in der App), prüft beim Zurückhalten alle 10 Minuten, ob man angekommen ist, und fragt außerhalb der Arbeitszeit gar nicht nach dem Standort (spart Akku). Benötigt Server 1.52.1.

## [1.2.1] - 2026-10-17
- Mitteilungen zeigen den Server deutlich: Servername im Titel („Kantine · Titel"), runder
  Avatar mit Anfangsbuchstabe in fester Serverfarbe, Gruppierung je Server, eigener
  Android-Benachrichtigungskanal je Server („Mitteilungen: Kantine", einzeln einstellbar),
  die Dauer-Meldung des Dienstes listet die aktiven Server. (20:11, `476ad22`)

## [1.2.0] - 2026-10-15
- Material 3: Server-Verwaltung und Orte neu mit M3-Komponenten (Leiste, Karten, Buttons,
  Eingabefelder, Dialoge), Farben als Theme-Rollen, Hell und Dunkel. (16:18, `363b7cc`)
- Server-Wechsel als Bottom Sheet (von unten, abgerundet, per Wischen schließbar): nur die
  Server mit Häkchen am aktiven und „Server verwalten…". „Neu laden" und „Orte" sind nicht
  mehr im Server-Menü („Orte" bleibt unter „Mehr"). (16:18, `363b7cc`)
- Hell/Dunkel der App-Bildschirme folgt der Design-Einstellung der Webseite (Hell/Dunkel/
  Automatisch), nicht nur dem Android-System. (16:26, `c556136`)
- Orte: Buttons einzeilig und gleich breit, kürzere Untertitel in den Kopfzeilen. (16:28, `1504f9c`)

## [1.1.0] - 2026-10-15
- Server-Wechsel über die Dashboard-Überschrift (Servername ▾ statt „Dashboard", sobald mehr
  als ein Server hinterlegt ist). (14:05, `3b8a462`)
- Mobile Kopfzeile der Seite nur in der App ausgeblendet (Platz), ihre Funktionen (Profil,
  Mitteilungen an/aus, Design, Abmelden, Version) liegen im „Mehr"-Menü; beim Dokumentstart
  ausgeblendet, damit das Logo nicht aufblitzt. (14:05 `3b8a462`, 14:13 `1c1f125`, 14:44 `040b1a6`)
- Eigener Benachrichtigungsdienst (Vordergrunddienst mit Long-Polling gegen `/api/app/inbox`,
  Start nach Neustart/Update, Mitteilungen an/aus). Kommt ohne Gotify/ntfy/Google aus und
  empfängt von mehreren Servern gleichzeitig. Gegenstück am Server: v1.51.0. (14:05, `3b8a462`)
- Sitzungs-Cookie aus dem Hintergrunddienst wird übernommen – Anmeldung bleibt bestehen,
  auch wenn die App länger nicht geöffnet wird. (14:26, `48ac61b`)
- Server-Verwaltung als Karten (Avatar, „AKTIV"-Markierung, „Server hinzufügen"). (14:28, `3b921aa`)
- Fehlerbehebungen: Serverliste unter der Action Bar verdeckt (14:17, `e66b21d`), weißes
  Aufblitzen beim Serverwechsel durch dunklen WebView-Untergrund (14:33, `c007987`), Ladebalken
  eingeführt und wieder entfernt (14:35 `8cda92a`, 14:41 `ace6d8a`).
- Orte „Zu Hause" / „Arbeit": Mitteilungen werden nur auf Arbeit bzw. nicht zu Hause angezeigt;
  Standort wird nur beim Eintreffen einer Mitteilung geprüft und bleibt auf dem Gerät. (14:50, `9f6c207`)

## [1.0.0] - 2026-10-15
- Erste Version: native Kotlin-App als WebView-Wrapper. Beliebig viele Server frei hinterlegbar
  (nichts fest eingebaut), Kamera für den Barcode-Scan, Datei-Upload, Downloads,
  alert()/confirm()-Dialoge der Seite, NFC-/Link-Aufruf von `/room/<id>`. (13:47, `ca08286`)
