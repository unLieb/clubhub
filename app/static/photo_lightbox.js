/* Stellt window.openPhotoLightbox(urls, startIndex) bereit - ein an den
   Bildschirm angepasstes Vollbild-Modal fuer Fotos (aktuell nur Meldungen,
   siehe reports.html), mit Vor/Zurueck bei mehreren Bildern sowie einem Link
   auf die Originaldatei (die Anzeige selbst skaliert das Bild ja auf die
   Fensterhoehe/-breite herunter). Analog zu window.customConfirm() aus
   confirm_modal.js: einmal global in base.html eingebunden
   (_photo_lightbox.html), danach ueberall aufrufbar. */
(function () {
  var modal = document.getElementById('photo-lightbox');
  if (!modal) return;

  var imgEl = document.getElementById('photo-lightbox-img');
  var countEl = document.getElementById('photo-lightbox-count');
  var originalLink = document.getElementById('photo-lightbox-original');
  var closeBtn = document.getElementById('photo-lightbox-close');
  var prevBtn = document.getElementById('photo-lightbox-prev');
  var nextBtn = document.getElementById('photo-lightbox-next');
  var urls = [];
  var index = 0;
  var lastFocused = null;

  function render() {
    imgEl.src = urls[index];
    originalLink.href = urls[index];
    var multi = urls.length > 1;
    prevBtn.classList.toggle('hidden', !multi);
    nextBtn.classList.toggle('hidden', !multi);
    countEl.classList.toggle('hidden', !multi);
    if (multi) countEl.textContent = (index + 1) + ' / ' + urls.length;
  }

  function close() {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    document.removeEventListener('keydown', onKeydown);
    imgEl.src = ''; // laufenden Bild-Download abbrechen, falls das Bild noch laedt
    if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
  }

  function step(delta) {
    if (urls.length < 2) return;
    index = (index + delta + urls.length) % urls.length;
    render();
  }

  function onKeydown(e) {
    if (e.key === 'Escape') close();
    else if (e.key === 'ArrowLeft') step(-1);
    else if (e.key === 'ArrowRight') step(1);
  }

  closeBtn.addEventListener('click', close);
  prevBtn.addEventListener('click', function () { step(-1); });
  nextBtn.addEventListener('click', function () { step(1); });
  // Klick auf den abgedunkelten Hintergrund selbst (nicht aufs Bild oder
  // die Buttons) schliesst - modal ist das aeusserste, fullscreen Element.
  modal.addEventListener('click', function (e) {
    if (e.target === modal) close();
  });

  window.openPhotoLightbox = function (photoUrls, startIndex) {
    urls = (photoUrls || []).filter(Boolean);
    if (!urls.length) return;
    index = Math.min(Math.max(startIndex || 0, 0), urls.length - 1);
    render();
    lastFocused = document.activeElement;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    document.addEventListener('keydown', onKeydown);
    closeBtn.focus();
  };
})();
