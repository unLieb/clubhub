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

// qrbox als Funktion statt fester Pixelgroesse: Html5Qrcode ruft sie mit den
// TATSAECHLICHEN Massen des Kamera-Bildes auf (erst nach Start des Streams
// bekannt) und wirft andernfalls "'config.qrbox' dimensions should not be
// greater than the dimensions of the root HTML element", sobald eine fest
// eingestellte Box (z.B. 250x150px) groesser ist als der tatsaechliche
// Video-Stream - genau das war die eigentliche Ursache von "Kamera konnte
// nicht gestartet werden" (der Fehler kam nicht von der Kamera-Berechtigung,
// sondern von dieser Validierung danach). 70% der kuerzeren Kanten-Seite,
// eingegrenzt auf die von der Bibliothek erlaubte Mindestgroesse (50px) -
// dadurch niemals groesser als der tatsaechliche Videostream.
function _computeQrbox(viewfinderWidth, viewfinderHeight) {
  var edge = Math.floor(Math.min(viewfinderWidth, viewfinderHeight) * 0.7);
  edge = Math.max(edge, 50);
  edge = Math.min(edge, 300);
  return { width: edge, height: edge };
}

// Pro Reader-Element (readerId) hoechstens eine laufende Html5Qrcode-Instanz -
// verhindert, dass beim schnellen Schliessen+Wiederoeffnen des Modals zwei
// Instanzen um dieselbe Kamera konkurrieren (typische Ursache fuer "Kamera
// konnte nicht gestartet werden" / NotReadableError).
var _activeBarcodeScanners = {};

function _stopTracksInContainer(readerId) {
  var container = document.getElementById(readerId);
  if (!container) return;
  container.querySelectorAll('video').forEach(function (videoEl) {
    if (videoEl.srcObject) {
      try {
        videoEl.srcObject.getTracks().forEach(function (track) { track.stop(); });
      } catch (e) { /* Track war schon inaktiv o.ae. - ignorieren */ }
      videoEl.srcObject = null;
    }
  });
}

// Stoppt eine fuer readerId noch laufende Scanner-Instanz vollstaendig -
// html5QrCode.stop() zuerst, danach zur Sicherheit zusaetzlich alle
// MediaStreamTracks im Reader-Element direkt stoppen (manche Browser/
// Bibliotheksversionen geben die Kamera sonst nicht zuverlaessig frei,
// erkennbar am weiter leuchtenden Kamera-Indikator). Eigenstaendig aufrufbar
// (z.B. beim Schliessen eines Modals) und wird von startBarcodeScanner selbst
// auch vor jedem Neustart aufgerufen.
//
// Wartet zuerst das Ergebnis von start() ab (siehe entry.startPromise), bevor
// stop() aufgerufen wird: Html5Qrcode wirft einen internen Zustandsfehler
// ("Cannot stop, scanner is not running or paused"), wenn stop() waehrend
// start() noch laeuft oder nachdem start() fehlgeschlagen ist (dann gibt es
// ohnehin nichts zu stoppen) aufgerufen wird.
function stopBarcodeScanner(readerId) {
  var entry = _activeBarcodeScanners[readerId];
  delete _activeBarcodeScanners[readerId];
  if (!entry) {
    _stopTracksInContainer(readerId);
    return Promise.resolve();
  }
  return entry.startPromise.then(function (started) {
    if (!started) {
      _stopTracksInContainer(readerId);
      return;
    }
    var stopPromise;
    try {
      stopPromise = entry.scanner.stop();
    } catch (e) {
      stopPromise = Promise.resolve();
    }
    return Promise.resolve(stopPromise).catch(function () { /* z.B. schon gestoppt - ignorieren */ }).then(function () {
      try { entry.scanner.clear(); } catch (e) { /* ignorieren */ }
    }).then(function () {
      _stopTracksInContainer(readerId);
    });
  });
}

function _classifyCameraError(err) {
  var name = err && err.name;
  if (name === 'NotAllowedError') return 'permission-denied';
  if (name === 'NotFoundError') return 'no-camera';
  if (name === 'NotReadableError') return 'in-use';
  if (name === 'OverconstrainedError') return 'overconstrained';
  return 'unknown';
}

// Zentrale deutsche Fehlertexte, damit beide Scanner-Modale dieselbe
// Formulierung zeigen statt sie doppelt zu pflegen.
function barcodeScanErrorMessage(reason) {
  switch (reason) {
    case 'insecure': return 'Kamerazugriff erfordert eine sichere HTTPS-Verbindung.';
    case 'missing-lib': return 'Scanner-Bibliothek konnte nicht geladen werden.';
    case 'permission-denied': return 'Kamerazugriff wurde verweigert - bitte in den Browser-/Geräteeinstellungen erlauben.';
    case 'no-camera': return 'Keine Kamera gefunden.';
    case 'in-use': return 'Kamera wird bereits von einer anderen Anwendung verwendet.';
    case 'overconstrained': return 'Keine passende Kamera gefunden (z.B. keine Rückkamera verfügbar).';
    default: return 'Kamera konnte nicht gestartet werden.';
  }
}

// onFailed(reason) wird mit einem der obigen Reason-Strings aufgerufen -
// Aufrufer koennen barcodeScanErrorMessage(reason) fuer die Anzeige nutzen,
// oder eigene Texte verwenden.
function startBarcodeScanner(readerId, onDecoded, onStarted, onFailed) {
  // Ohne sicheren Kontext (HTTPS oder localhost) verweigert der Browser
  // getUserMedia grundsaetzlich - eigene, verstaendliche Meldung statt eines
  // erst nach einem fehlgeschlagenen Kamera-Start sichtbaren, kryptischen
  // Bibliotheksfehlers.
  if (!window.isSecureContext) {
    onFailed('insecure');
    return;
  }
  if (typeof Html5Qrcode === 'undefined') {
    onFailed('missing-lib');
    return;
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

  // Vorherige Instanz fuer dieses Reader-Element (falls vorhanden, z.B. durch
  // schnelles Schliessen+Wiederoeffnen) zuerst sauber stoppen, bevor eine
  // neue gestartet wird.
  stopBarcodeScanner(readerId).then(function () {
    // Jeder Versuch (exact:environment, bei Fehlschlag der weichere
    // Fallback) bekommt eine EIGENE frische Html5Qrcode-Instanz statt
    // dieselbe wiederzuverwenden - ein erneuter start()-Aufruf auf einer
    // Instanz, deren vorheriger start() gerade erst fehlgeschlagen ist,
    // wirft bei dieser Bibliothek "Cannot transition to a new state,
    // already under transition", da der interne Zustand nach einem
    // fehlgeschlagenen Start nicht zuverlaessig zurueckgesetzt wird.
    function attempt(cameraConfig, allowFallback) {
      var scanner = new Html5Qrcode(readerId);
      var startPromise = scanner.start(
        cameraConfig,
        { fps: BARCODE_SCAN_FPS, qrbox: _computeQrbox },
        confirmedDecodeHandler,
        function () { /* einzelner Frame ohne erkannten Code - kein Fehler */ }
      ).then(function () {
        if (onStarted) onStarted();
        return true;
      }).catch(function (err) {
        if (_activeBarcodeScanners[readerId] && _activeBarcodeScanners[readerId].scanner === scanner) {
          delete _activeBarcodeScanners[readerId];
        }
        try { scanner.clear(); } catch (e) { /* ignorieren */ }
        if (allowFallback) {
          // "exact: environment" fehlgeschlagen (z.B. Geraet ohne
          // erkennbare Rueckkamera, oder "exact" wird nicht unterstuetzt) -
          // auf die weichere Variante zurueckfallen statt aufzugeben.
          attempt({ facingMode: 'environment', advanced: [{ focusMode: 'continuous' }] }, false);
        } else {
          onFailed(_classifyCameraError(err));
        }
        return false;
      });
      _activeBarcodeScanners[readerId] = { scanner: scanner, startPromise: startPromise };
    }

    attempt({ facingMode: { exact: 'environment' }, advanced: [{ focusMode: 'continuous' }] }, true);
  });
}
