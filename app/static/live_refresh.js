/* Live-Update per Polling fuer Seiten, auf denen mehrere Leute gleichzeitig
   draufschauen (Dashboard, Bereichs-Uebersicht, einzelne Bereichs-Seite,
   Meldungen) - siehe <body data-live-refresh="1"> in der jeweiligen
   Seitenvorlage (Jinja-Block "live_refresh" in base.html). Andere Seiten
   setzen dieses Attribut nicht, das Skript tut dann nichts.

   Bewusst kein WebSocket/Server-Sent-Events: ClubHUB laeuft je nach Kunde
   hinter unterschiedlichen, teils nicht selbst kontrollierten Reverse-
   Proxies oder einfachen Portfreigaben - ein periodischer GET-Request sieht
   fuer JEDEN Proxy exakt wie ein normaler Seitenaufruf aus, waehrend ein
   WebSocket-Upgrade oder eine lang offene SSE-Verbindung dort im Zweifel
   unbemerkt haengenbleiben koennte.

   Funktionsweise: alle POLL_MS Millisekunden wird /api/live-version
   abgefragt (ein einzelner, sehr billiger In-Memory-Zaehler, siehe
   main.py). Hat er sich seit dem letzten Abruf geaendert, wird die
   aktuelle Seite (dieselbe URL) per fetch() komplett neu geholt und nur
   der Inhalt von #live-content (siehe base.html) ausgetauscht - kein
   echter Seiten-Reload, Sidebar/Scroll-Position/offene Menues ausserhalb
   des Inhaltsbereichs bleiben unberuehrt. Eingebettete <script>-Bloecke im
   ausgetauschten Bereich werden danach manuell erneut ausgefuehrt (per
   innerHTML eingefuegte <script>-Tags laufen im Browser nie von selbst),
   damit seiteneigene Event-Listener (Kebab-Menues, Tabs, o.ae.) auf den
   neuen Elementen wieder funktionieren.

   Bekannte, bewusst in Kauf genommene Einschraenkungen: pausiert nur, wenn
   der Tab im Hintergrund ist oder gerade ein Text-/Zahlenfeld fokussiert
   ist (z.B. mitten in einem Formular) - andere offene Zustaende (z.B. ein
   aufgeklapptes <details>-Menue) koennten beim Austausch zuklappen. Ein
   in der URL vorhandener "?done=..."-Parameter (Erledigt-Hervorhebung)
   koennte dadurch theoretisch erneut kurz aufblitzen. */
(function () {
  var POLL_MS = 15000;

  function init() {
    if (document.body.dataset.liveRefresh !== '1') return;
    var container = document.getElementById('live-content');
    if (!container) return;

    var lastVersion = null;
    var refreshing = false;

    function reExecuteScripts(root) {
      root.querySelectorAll('script').forEach(function (oldScript) {
        var newScript = document.createElement('script');
        for (var i = 0; i < oldScript.attributes.length; i++) {
          var attr = oldScript.attributes[i];
          newScript.setAttribute(attr.name, attr.value);
        }
        newScript.textContent = oldScript.textContent;
        oldScript.parentNode.replaceChild(newScript, oldScript);
      });
    }

    function isEditing() {
      var el = document.activeElement;
      if (!el) return false;
      var tag = el.tagName;
      return tag === 'INPUT' || tag === 'TEXTAREA' || el.isContentEditable;
    }

    function refresh() {
      if (refreshing || document.hidden || isEditing()) return;
      refreshing = true;
      fetch(location.href)
        .then(function (resp) {
          if (!resp.ok) throw new Error('http ' + resp.status);
          return resp.text();
        })
        .then(function (html) {
          var doc = new DOMParser().parseFromString(html, 'text/html');
          var fresh = doc.getElementById('live-content');
          if (!fresh) return;
          container.innerHTML = fresh.innerHTML;
          reExecuteScripts(container);
          document.dispatchEvent(new CustomEvent('live-content-refreshed'));
        })
        .catch(function () {
          /* Naechster Poll-Zyklus versucht es einfach erneut - kein Alert,
             das waere bei einem rein im Hintergrund laufenden Mechanismus
             fuer die Nutzer:innen nur verwirrend. */
        })
        .finally(function () {
          refreshing = false;
        });
    }

    function poll() {
      if (document.hidden) return;
      fetch('/api/live-version')
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
          if (lastVersion === null) {
            lastVersion = data.v;
            return;
          }
          if (data.v !== lastVersion) {
            lastVersion = data.v;
            refresh();
          }
        })
        .catch(function () {});
    }

    // Beim Zurueckkommen aus dem Hintergrund sofort pruefen statt bis zum
    // naechsten Intervall zu warten - sonst wirkt ein laenger im
    // Hintergrund liegender Tab beim Zurueckwechseln kurz veraltet.
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) poll();
    });

    setInterval(poll, POLL_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
