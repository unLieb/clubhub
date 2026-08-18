/* Einheitliche Dropzone-Upload-Komponente fuer die ganze App (siehe
   app/templates/_dropzone_macros.html fuer das zugehoerige Markup).
   Ein einziges Skript, ueberall per <script src="/static/dropzone.js">
   eingebunden (base.html) - initialisiert sich selbst fuer jedes
   ".dropzone"-Element im DOM, auch fuer solche, die erst spaeter per JS
   sichtbar gemacht werden (Drawer/Modal), da die Elemente bereits beim
   initialen Server-Render im DOM stehen. */
(function () {
  function humanFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    var units = ['KB', 'MB', 'GB'];
    var i = -1;
    do {
      bytes /= 1024;
      i++;
    } while (bytes >= 1024 && i < units.length - 1);
    return bytes.toFixed(1) + ' ' + units[i];
  }

  var FILE_ICON_SVG =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>';

  function initDropzone(root) {
    if (root._dropzoneInit) return;
    root._dropzoneInit = true;

    var input = root.querySelector('input[type="file"]');
    var area = root.querySelector('.dropzone-area');
    var previews = root.querySelector('.dropzone-previews');
    if (!input || !area) return;
    var multiple = input.multiple;

    function renderPreviews() {
      previews.innerHTML = '';
      var files = Array.from(input.files || []);
      previews.classList.toggle('hidden', files.length === 0);
      files.forEach(function (file, idx) {
        var row = document.createElement('div');
        row.className = 'flex items-center gap-2 rounded-md border border-line bg-ink/40 px-2.5 py-1.5 text-xs';

        if (file.type && file.type.indexOf('image/') === 0) {
          var img = document.createElement('img');
          img.className = 'w-8 h-8 rounded object-cover shrink-0';
          img.src = URL.createObjectURL(file);
          row.appendChild(img);
        } else {
          var iconWrap = document.createElement('span');
          iconWrap.className = 'w-8 h-8 rounded bg-panel2 flex items-center justify-center shrink-0 text-slate-400';
          iconWrap.innerHTML = FILE_ICON_SVG;
          row.appendChild(iconWrap);
        }

        var info = document.createElement('span');
        info.className = 'flex-1 min-w-0 truncate text-slate-300';
        info.textContent = file.name + ' (' + humanFileSize(file.size) + ')';
        row.appendChild(info);

        var removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-slate-500 hover:text-late hover:bg-late/10';
        removeBtn.setAttribute('aria-label', 'Datei entfernen');
        removeBtn.textContent = '✕';
        removeBtn.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          removeFile(idx);
        });
        row.appendChild(removeBtn);

        previews.appendChild(row);
      });
    }

    function setFiles(fileList) {
      var incoming = Array.from(fileList || []).filter(function (f) { return f && f.size > 0; });
      if (!incoming.length) return;
      var dt = new DataTransfer();
      if (multiple) {
        Array.from(input.files || []).forEach(function (f) { dt.items.add(f); });
        incoming.forEach(function (f) { dt.items.add(f); });
      } else {
        dt.items.add(incoming[0]);
      }
      input.files = dt.files;
      renderPreviews();
    }

    function removeFile(idx) {
      var dt = new DataTransfer();
      Array.from(input.files).forEach(function (f, i) {
        if (i !== idx) dt.items.add(f);
      });
      input.files = dt.files;
      renderPreviews();
    }

    function handlePaste(e) {
      var clipboard = e.clipboardData || window.clipboardData;
      if (!clipboard || !clipboard.items) return;
      var pasted = [];
      for (var i = 0; i < clipboard.items.length; i++) {
        if (clipboard.items[i].kind === 'file') {
          var f = clipboard.items[i].getAsFile();
          if (f) pasted.push(f);
        }
      }
      if (pasted.length) {
        e.preventDefault();
        setFiles(pasted);
      }
    }

    area.addEventListener('click', function () {
      input.click();
    });
    area.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        input.click();
      }
    });
    input.addEventListener('click', function (e) {
      // Klick auf den (unsichtbaren) input wuerde sonst durch das Klick-
      // Handling der Area erneut geoeffnet werden - hier nicht relevant,
      // da der input selbst keine sichtbare Flaeche hat, aber zur
      // Sicherheit ein doppeltes Bubble verhindern.
      e.stopPropagation();
    });
    input.addEventListener('change', function () {
      setFiles(input.files);
    });

    ['dragenter', 'dragover'].forEach(function (evt) {
      area.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        area.classList.add('dropzone-drag-over');
      });
    });
    ['dragleave', 'drop'].forEach(function (evt) {
      area.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        area.classList.remove('dropzone-drag-over');
      });
    });
    area.addEventListener('drop', function (e) {
      if (e.dataTransfer && e.dataTransfer.files) {
        setFiles(e.dataTransfer.files);
      }
    });

    area.addEventListener('paste', handlePaste);
    renderPreviews();
  }

  // Modul-weites Paste-Fallback: greift, wenn der Nutzer irgendwo im
  // sichtbaren Modal/Formular einfuegt (nicht exakt in der Dropzone-
  // Flaeche fokussiert) - sucht die erste tatsaechlich sichtbare Dropzone
  // im Dokument. In dieser App ist praktisch nie mehr als eine Dropzone
  // gleichzeitig sichtbar (Modal/Drawer verdeckt den Rest der Seite).
  document.addEventListener('paste', function (e) {
    if (e.defaultPrevented) return;
    var active = document.activeElement;
    if (active && active.closest && active.closest('.dropzone-area')) return; // schon von der Area selbst behandelt
    var visibleArea = Array.from(document.querySelectorAll('.dropzone-area')).find(function (el) {
      return el.offsetParent !== null;
    });
    if (!visibleArea) return;
    var clipboard = e.clipboardData || window.clipboardData;
    if (!clipboard || !clipboard.items) return;
    var hasFile = Array.from(clipboard.items).some(function (item) { return item.kind === 'file'; });
    if (hasFile) {
      visibleArea.dispatchEvent(new ClipboardEvent('paste', { clipboardData: clipboard }));
    }
  });

  function initAll() {
    document.querySelectorAll('.dropzone').forEach(initDropzone);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }

  // Fuer Dropzones, die erst nachtraeglich in den DOM kommen (z.B. falls
  // eine Seite sowas mal per innerHTML nachlaedt) - einfacher Re-Scan,
  // von Templates bei Bedarf aufrufbar.
  window.initDropzones = initAll;
})();
