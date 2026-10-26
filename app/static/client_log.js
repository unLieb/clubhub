/* Meldet Fehler aus dem Browser an das Fehlerprotokoll des Servers (siehe
   error_log.py / ErrorLog in models.py und die Route /client-log).

   Warum im Browser: Scheitert z.B. das Absenden einer Meldung am Netz, hat die
   Anfrage den Server nie erreicht - er kann sie nicht selbst protokollieren.
   Ist der Server (wieder) erreichbar, wird der Eintrag hier nachgereicht.
   Fehlgeschlagene Meldungen werden dafuer in localStorage zwischengespeichert
   und beim naechsten Seitenaufruf bzw. sobald der Browser wieder online ist
   nachgesendet.

   API: window.clubhubLog(kind, fields) - kind: senden_fehlgeschlagen |
   js_fehler | verbindung; fields: path, method, status, duration_ms,
   size_bytes, details. Wirft nie. */
(function () {
  var KEY = 'clubhub_pending_client_log';
  var MAX_QUEUE = 20;
  var flushing = false;

  function loadQueue() {
    try { return JSON.parse(localStorage.getItem(KEY) || '[]') || []; } catch (e) { return []; }
  }
  function saveQueue(queue) {
    try { localStorage.setItem(KEY, JSON.stringify(queue.slice(-MAX_QUEUE))); } catch (e) { /* Speicher gesperrt/voll */ }
  }

  function context() {
    var c = navigator.connection || {};
    var parts = [];
    parts.push(navigator.onLine === false ? 'offline' : 'online');
    if (c.effectiveType) parts.push(c.effectiveType);
    if (c.downlink) parts.push(c.downlink + ' Mbit/s');
    if (c.saveData) parts.push('Datensparmodus');
    var standalone = false;
    try { standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone; } catch (e) { /* egal */ }
    if (standalone) parts.push('installierte App');
    return parts.join(', ');
  }

  function send(entry) {
    return fetch('/client-log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(entry),
      credentials: 'same-origin',
      keepalive: true
    }).then(function (resp) {
      // 4xx (z.B. nicht angemeldet) bringt auch ein spaeterer Versuch nicht
      // durch -> verwerfen; nur Netz-/Serverfehler bleiben in der Warteschlange.
      if (resp.status >= 500) throw new Error('server');
    });
  }

  function flush() {
    if (flushing) return;
    var queue = loadQueue();
    if (!queue.length) return;
    flushing = true;
    var entry = queue[0];
    send(entry).then(function () {
      var rest = loadQueue();
      rest.shift();
      saveQueue(rest);
      flushing = false;
      if (rest.length) flush();
    }).catch(function () {
      flushing = false;
    });
  }

  window.clubhubLog = function (kind, fields) {
    try {
      var entry = Object.assign({}, fields || {}, {
        kind: kind,
        occurred_at: new Date().toISOString(),
        details: ((fields && fields.details) || '') + ((fields && fields.details) ? ' | ' : '') + context()
      });
      var queue = loadQueue();
      queue.push(entry);
      saveQueue(queue);
      flush();
    } catch (e) { /* Protokollieren darf nie selbst stoeren */ }
  };

  // JavaScript-Fehler der eigenen Seiten (nicht von Erweiterungen/Fremdskripten:
  // dort ist filename leer bzw. "Script error."), hoechstens 3 pro Seitenaufruf.
  var jsErrors = 0;
  window.addEventListener('error', function (ev) {
    try {
      if (jsErrors >= 3 || !ev.filename || ev.filename.indexOf(location.origin) !== 0) return;
      if (!ev.message || /ResizeObserver/.test(ev.message)) return;
      jsErrors++;
      window.clubhubLog('js_fehler', {
        path: location.pathname,
        details: ev.message + ' (' + ev.filename.replace(location.origin, '') + ':' + ev.lineno + ')'
      });
    } catch (e) { /* egal */ }
  });

  window.addEventListener('online', flush);
  window.addEventListener('load', function () { setTimeout(flush, 1500); });
})();
