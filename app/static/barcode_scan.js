// Duenner Wrapper um die Html5Qrcode-Bibliothek (siehe html5-qrcode.min.js)
// fuers Starten/Stoppen der Rueckkamera - von beiden Barcode-Scanner-Modalen
// genutzt (Wareneingang in inventory.html, Barcode-Feld im Artikel-Formular
// in admin_inventory.html), damit die Lifecycle-Handhabung nicht doppelt
// gepflegt werden muss.

function startBarcodeScanner(readerId, onDecoded, onStarted, onFailed) {
  if (typeof Html5Qrcode === 'undefined') {
    onFailed();
    return null;
  }
  var scanner = new Html5Qrcode(readerId);
  scanner.start(
    { facingMode: 'environment' },
    { fps: 10, qrbox: { width: 250, height: 150 } },
    function (decodedText) { onDecoded(decodedText); },
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
