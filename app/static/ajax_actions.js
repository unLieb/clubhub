/* Gemeinsame Fetch-Handler fuer einfache Verwaltungs-Aktionen (Loeschen/
   Toggle/Status-Wechsel) ohne Page-Reload. Ergaenzt die bereits pro Seite
   vorhandenen eigenen AJAX-Handler (siehe room.html, dashboard.html,
   inventory.html, history.html, kuehlungen.html - deren Reaktionen zu
   individuell sind fuer ein generisches Modul) um die vielen einfacheren
   Formulare der Verwaltungsseiten, Meldungen, Termine, Urlaub und
   Zeiterfassung, die bisher nur klassischer Formular-POST mit Redirect
   waren. Ein Formular wird von hier behandelt, sobald es eine der drei
   Klassen trägt - alles rein deklarativ per data-*-Attribut im Markup,
   kein JS pro Seite noetig fuer den Standardfall:

   .ajax-delete-form
     - optional data-confirm="Text" -> confirm() vor dem Absenden
       (ersetzt das bisherige onsubmit="return confirm(...)")
     - entfernt bei Erfolg die naechste Vorfahren-Zeile/-Karte mit
       [data-ajax-row] (kurze Fade-Animation statt hartem display:none)
     - optional data-counter="<CSS-Selektor>" (+ optional
       data-counter-delta, Standard -1) um einen Zaehler im Header
       mitzuaktualisieren (z.B. "X offene Meldungen")
     - loest nach dem Entfernen ein CustomEvent "ajax-row-removed" auf
       document mit {parent: <Elternelement der entfernten Zeile>} aus -
       fuer Seiten, die z.B. einen "Liste leer"-Platzhalter einblenden
       wollen, sobald die Liste dadurch leer wurde (siehe reports.html)

   .ajax-toggle-form
   .ajax-status-form
     - erwarten vom Server {"ok": true, ...} JSON
     - loesen ein CustomEvent "ajax-form-success" (bubbles) mit dem JSON
       als event.detail auf dem Formular aus - jede Seite reagiert selbst
       darauf (Button-Text/Badge/Tab wechseln), da diese Reaktionen zu
       unterschiedlich sind fuer ein generisches DOM-Update hier.

   Serverseitig antworten die betroffenen Routen bei
   "X-Requested-With: fetch" mit JSON statt einem Redirect (siehe
   main.py) - ohne den Header (kein JS, z.B. deaktiviert) bleibt das
   bisherige Redirect-Verhalten unveraendert als Fallback erhalten. */
(function () {
  function findRow(form) {
    return form.closest('[data-ajax-row]');
  }

  function fadeOutAndRemove(el) {
    if (!el) return;
    var parent = el.parentElement;
    el.style.transition = 'opacity 200ms ease, max-height 250ms ease';
    el.style.opacity = '0';
    setTimeout(function () {
      el.remove();
      // Erlaubt Seiten, nach dem Entfernen z.B. einen Leer-Zustand
      // einzublenden, falls die Liste dadurch leer wurde (siehe reports.html).
      if (parent) {
        document.dispatchEvent(new CustomEvent('ajax-row-removed', { detail: { parent: parent } }));
      }
    }, 220);
  }

  function updateCounter(form) {
    var sel = form.dataset.counter;
    if (!sel) return;
    var delta = parseInt(form.dataset.counterDelta || '-1', 10);
    // Kommagetrennte Liste erlaubt, falls mehrere Anzeigen denselben Wert
    // zeigen (z.B. Seiten-Zaehler + Navigations-Badge, siehe reports.html).
    sel.split(',').forEach(function (part) {
      var el = document.querySelector(part.trim());
      if (!el) return;
      var current = parseInt(el.textContent, 10);
      if (isNaN(current)) return;
      var next = current + delta;
      el.textContent = String(next);
      // Badges dieser Art werden serverseitig beim Rendern nur angezeigt,
      // wenn die Anzahl > 0 ist (siehe reports.html) - beim Erreichen von 0
      // rein clientseitig ebenso ausblenden statt eine "0" stehen zu lassen.
      el.classList.toggle('hidden', next <= 0);
    });
  }

  document.addEventListener('submit', function (e) {
    var delForm = e.target.closest('.ajax-delete-form');
    if (delForm) {
      e.preventDefault();
      var confirmMsg = delForm.dataset.confirm;
      if (confirmMsg && !confirm(confirmMsg)) return;
      var row = findRow(delForm);
      fetch(delForm.action, { method: 'POST', headers: { 'X-Requested-With': 'fetch' } })
        .then(function (resp) {
          if (!resp.ok) throw new Error('http ' + resp.status);
          return resp.json();
        })
        .then(function () {
          updateCounter(delForm);
          fadeOutAndRemove(row);
        })
        .catch(function () {
          alert('Löschen fehlgeschlagen. Bitte Seite neu laden und erneut versuchen.');
        });
      return;
    }

    var actionForm = e.target.closest('.ajax-toggle-form, .ajax-status-form');
    if (actionForm) {
      e.preventDefault();
      var msg = actionForm.dataset.confirm;
      if (msg && !confirm(msg)) return;
      fetch(actionForm.action, {
        method: 'POST',
        headers: { 'X-Requested-With': 'fetch' },
        body: new FormData(actionForm),
      })
        .then(function (resp) {
          if (!resp.ok) throw new Error('http ' + resp.status);
          return resp.json();
        })
        .then(function (data) {
          actionForm.dispatchEvent(new CustomEvent('ajax-form-success', { detail: data, bubbles: true }));
        })
        .catch(function () {
          alert('Aktion fehlgeschlagen. Bitte Seite neu laden und erneut versuchen.');
        });
    }
  });
})();
