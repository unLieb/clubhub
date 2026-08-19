/* Wiederverwendbare Monat/Freier-Zeitraum-Filterlogik fuer die Zeiterfassung
   (Mitarbeiter: /timeclock, Admin: /admin/timeclock) - identisch fuer beide
   Ansichten, damit "Monat" vs. "Freier Zeitraum" sich ueberall gleich
   verhaelt. Zugehoerige Server-Logik: _resolve_timeclock_period() in
   app/main.py. Zugehoerige Markup-Komponente: _timeclock_period_macros.html
   (Klassen tp-month-nav / tp-month-input / tp-date-from / tp-date-to /
   tp-mode-btn, an die dieses Skript bindet).

   Nicht mit "defer" eingebunden (siehe base.html) - die Seiten rufen
   initTimeclockPeriod() synchron aus ihrem eigenen <script>-Block auf,
   das muss also bereits vor dessen Ausfuehrung definiert sein. */
function initTimeclockPeriod(fragmentEl, baseUrl, onBind) {
  function bind() {
    fragmentEl.querySelectorAll('.tp-month-nav').forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        load(a.getAttribute('href'));
      });
    });
    var monthInput = fragmentEl.querySelector('.tp-month-input');
    if (monthInput) {
      monthInput.addEventListener('change', function () {
        load(baseUrl + '?mode=month&month=' + monthInput.value);
      });
    }
    var fromInput = fragmentEl.querySelector('.tp-date-from');
    var toInput = fragmentEl.querySelector('.tp-date-to');
    if (fromInput && toInput) {
      var onRangeChange = function () {
        load(baseUrl + '?mode=range&from=' + fromInput.value + '&to=' + toInput.value);
      };
      fromInput.addEventListener('change', onRangeChange);
      toInput.addEventListener('change', onRangeChange);
    }
    fragmentEl.querySelectorAll('.tp-mode-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        load(baseUrl + '?mode=' + btn.dataset.mode);
      });
    });
    if (typeof onBind === 'function') onBind(fragmentEl);
  }

  // Ein dezentes opacity-50 waehrend des Ladens gibt sofortiges Feedback,
  // ohne dass das Layout springt. Bei Netzwerkfehler/Serverausfall faellt
  // der Code auf eine normale Navigation zurueck, damit die Seite in jedem
  // Fall bedienbar bleibt.
  function load(url) {
    fragmentEl.classList.add('opacity-50', 'pointer-events-none');
    fetch(url, { headers: { 'X-Requested-With': 'fetch' } })
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.text();
      })
      .then(function (html) {
        fragmentEl.innerHTML = html;
        fragmentEl.classList.remove('opacity-50', 'pointer-events-none');
        var target = new URL(url, window.location.origin);
        history.replaceState(null, '', target.pathname + target.search);
        bind();
      })
      .catch(function () {
        window.location.href = url;
      });
  }

  bind();
  return { reload: load };
}
