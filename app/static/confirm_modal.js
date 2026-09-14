/* Stellt window.customConfirm() bereit - ein Promise-basierter Ersatz fuer
   natives confirm(), der das gemeinsame Modal aus _confirm_modal.html
   (einmal global in base.html eingebunden) steuert. Aufruf:

     var ok = await customConfirm({
       title: 'Erledigung zurückziehen?',   // optional, Default je nach danger
       message: 'Die Aufgabe gilt danach wieder als nicht erledigt.',
       confirmText: 'Zurückziehen',          // optional, Default 'OK'/'Löschen'
       cancelText: 'Abbrechen',              // optional
       danger: true,                        // optional - rot statt gruen
     });
     // oder kurz: await customConfirm('Wirklich löschen?')

   Muss vor jedem Aufrufer ausgefuehrt sein (kein defer-Problem, da alle
   Skripte mit defer geladen werden und dadurch in Dokumentreihenfolge
   ausgefuehrt werden - siehe base.html, dieses Skript steht vor
   ajax_actions.js). */
(function () {
  var modal = document.getElementById('confirm-modal');
  if (!modal) return;

  var titleEl = document.getElementById('confirm-modal-title');
  var messageEl = document.getElementById('confirm-modal-message');
  var iconWrap = document.getElementById('confirm-modal-icon-wrap');
  var cancelBtn = document.getElementById('confirm-modal-cancel');
  var confirmBtn = document.getElementById('confirm-modal-confirm');
  var activeResolve = null;
  var lastFocused = null;

  function close(result) {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    document.removeEventListener('keydown', onKeydown);
    var resolve = activeResolve;
    activeResolve = null;
    if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
    if (resolve) resolve(result);
  }

  function onKeydown(e) {
    if (e.key === 'Escape') close(false);
  }

  cancelBtn.addEventListener('click', function () { close(false); });
  confirmBtn.addEventListener('click', function () { close(true); });
  // Klick auf den abgedunkelten Hintergrund selbst (nicht auf die Karte
  // darin) zaehlt wie Abbrechen - modal ist das äusserste, fullscreen Element.
  modal.addEventListener('click', function (e) {
    if (e.target === modal) close(false);
  });

  window.customConfirm = function (options) {
    if (typeof options === 'string') options = { message: options };
    options = options || {};

    // Ein noch offenes Modal kann es hier nicht geben (jeder Aufrufer
    // wartet per await, bevor die naechste Aktion folgt), aber defensiv
    // trotzdem die vorherige Promise aufloesen statt sie verwaist liegen
    // zu lassen.
    if (activeResolve) close(false);

    titleEl.textContent = options.title || (options.danger ? 'Wirklich fortfahren?' : 'Bitte bestätigen');
    messageEl.textContent = options.message || '';
    cancelBtn.textContent = options.cancelText || 'Abbrechen';
    confirmBtn.textContent = options.confirmText || (options.danger ? 'Löschen' : 'OK');
    confirmBtn.className = 'px-3 py-1.5 rounded-md text-sm font-medium ' + (
      options.danger ? 'bg-late/90 hover:bg-late text-white' : 'bg-go/90 hover:bg-go text-oncolor'
    );
    iconWrap.className = 'w-9 h-9 rounded-full flex items-center justify-center shrink-0 ' + (
      options.danger ? 'bg-late/15 text-late' : 'bg-warn/15 text-warn'
    );

    lastFocused = document.activeElement;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    document.addEventListener('keydown', onKeydown);
    confirmBtn.focus();

    return new Promise(function (resolve) {
      activeResolve = resolve;
    });
  };

  // Fuer die verbleibenden inline onsubmit="return confirm(...)"- und
  // onclick="return confirm(...)"-Handler, die sich nicht auf
  // .ajax-delete-form/.ajax-status-form umstellen lassen (echter Formular-
  // POST + Redirect, kein fetch): synchron per event.preventDefault()
  // blockieren, danach asynchron das Modal zeigen und bei Bestaetigung
  // form.submit() nachholen (loest kein erneutes "submit"-Event/onsubmit
  // aus, kein Rekursionsrisiko). event.target ist bei einem "submit"-Event
  // bereits das <form> selbst, bei einem "click" auf einen
  // type="submit"-Button dagegen der Button - .form liefert in beiden
  // Faellen zuverlaessig das richtige Formular. Aufruf z.B.
  // onsubmit="return confirmSubmit(event, { message: '...', danger: true })".
  window.confirmSubmit = function (event, options) {
    event.preventDefault();
    var form = event.target.form || event.target;
    window.customConfirm(options).then(function (ok) {
      if (ok) form.submit();
    });
    return false;
  };
})();
