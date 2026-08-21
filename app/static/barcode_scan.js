// Duenner Wrapper um die Html5Qrcode-Bibliothek (siehe html5-qrcode.min.js)
// fuers Starten/Stoppen der Rueckkamera - von beiden Barcode-Scanner-Modalen
// genutzt (Wareneingang in inventory.html, Barcode-Feld im Artikel-Formular
// in admin_inventory.html), damit die Lifecycle-Handhabung nicht doppelt
// gepflegt werden muss.

// Zwei identische Lesungen desselben Codes innerhalb dieses Zeitfensters
// gelten als bestaetigt - eine einzelne unscharfe Fehllesung (Standbild-
// Artefakt, Bewegungsunschaerfe) taucht so gut wie nie zweimal hintereinander
// mit demselben (falschen) Ergebnis auf, ein tatsaechlich vor der Kamera
// liegender Code dagegen schon.
var BARCODE_CONFIRM_WINDOW_MS = 500;

// Stabiler Wert im empfohlenen Bereich (10-15 FPS) - hoch genug fuer eine
// zuegige Bestaetigungs-Erkennung (siehe oben), nicht so hoch, dass
// leistungsschwaechere Geraete/Browser ins Stocken geraten.
var BARCODE_SCAN_FPS = 12;

function startBarcodeScanner(readerId, onDecoded, onStarted, onFailed) {
  if (typeof Html5Qrcode === 'undefined') {
    onFailed();
    return null;
  }

  // Mehrfach-Bestaetigung (Debounce): der rohe Kamera-Callback feuert pro
  // erkanntem Frame, potenziell mehrmals pro Sekunde und auch bei einer
  // einzelnen unscharfen Fehllesung. Erst wenn derselbe Text zweimal in
  // Folge innerhalb von BARCODE_CONFIRM_WINDOW_MS gelesen wurde, gilt der
  // Scan als valide und wird an onDecoded weitergereicht - danach wird der
  // Zwischenstand zurueckgesetzt, ein erneuter Scan (auch desselben Codes)
  // braucht wieder zwei Bestaetigungen.
  var pendingText = null;
  var pendingAt = 0;
  function confirmedDecodeHandler(decodedText) {
    var now = Date.now();
    if (decodedText === pendingText && (now - pendingAt) <= BARCODE_CONFIRM_WINDOW_MS) {
      pendingText = null;
      pendingAt = 0;
      onDecoded(decodedText);
    } else {
      pendingText = decodedText;
      pendingAt = now;
    }
  }

  var scanner = new Html5Qrcode(readerId);
  scanner.start(
    // advanced: kontinuierlicher Autofokus, sofern Kamera/Browser das
    // unterstuetzen - nicht unterstuetzte "advanced"-Constraints werden von
    // getUserMedia nach Spezifikation ignoriert statt den Kamera-Start
    // scheitern zu lassen, daher hier gefahrlos immer mitgeben.
    { facingMode: 'environment', advanced: [{ focusMode: 'continuous' }] },
    { fps: BARCODE_SCAN_FPS, qrbox: { width: 250, height: 150 } },
    confirmedDecodeHandler,
    function () { /* einzelner Frame ohne erkannten Code - kein Fehler */ }
  ).then(function () {
    if (onStarted) onStarted();
  }).catch(function () {
    onFailed();
  });
  return scanner;
}

function stopBarcodeScanner(scanner) {
  if (!scanner) return;
  scanner.stop().then(function () { scanner.clear(); }).catch(function () {});
}
