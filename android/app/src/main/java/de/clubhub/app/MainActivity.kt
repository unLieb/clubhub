package de.clubhub.app

import android.Manifest
import android.app.Activity
import android.app.DownloadManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.view.View
import android.view.WindowInsets
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.JsResult
import android.webkit.PermissionRequest
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import android.widget.Toast

class MainActivity : Activity() {
    private lateinit var web: WebView
    private var loadedServerId: String? = null

    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var pendingPermission: PermissionRequest? = null

    companion object {
        private const val REQ_SERVERS = 1
        private const val REQ_FILE = 2
        private const val REQ_CAMERA = 3
        private const val REQ_NOTIFY = 4

    }

    override fun onCreate(savedInstanceState: Bundle?) {
        ThemeMode.apply(this)
        super.onCreate(savedInstanceState)

        web = WebView(this)
        val root = FrameLayout(this)
        root.setBackgroundColor(Ui.color(this, Ui.surface))
        root.addView(web, FrameLayout.LayoutParams(-1, -1))
        setContentView(root)
        // Edge-to-Edge (targetSdk 35): Platz fuer Statusleiste, Navigation und Tastatur selbst freihalten
        root.setOnApplyWindowInsetsListener { v, insets ->
            val bars = insets.getInsets(WindowInsets.Type.systemBars())
            val ime = insets.getInsets(WindowInsets.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, ime.bottom))
            WindowInsets.CONSUMED
        }

        setupWebView()
        if (savedInstanceState != null) web.restoreState(savedInstanceState)
        handleIntent(intent, savedInstanceState != null)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent, false)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    private fun setupWebView() {
        // Dunkler Untergrund, sonst blitzt beim Laden/Serverwechsel der weisse WebView-Standard auf
        web.setBackgroundColor(Ui.color(this, Ui.surface))
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false)
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = false
            allowContentAccess = false
            userAgentString = userAgentString + " ClubHUBApp/" + BuildConfig.VERSION_NAME
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
        }
        // Kopfzeile schon beim Dokumentstart ausblenden, damit das Logo nicht kurz aufblitzt
        if (androidx.webkit.WebViewFeature.isFeatureSupported(androidx.webkit.WebViewFeature.DOCUMENT_START_SCRIPT)) {
            androidx.webkit.WebViewCompat.addDocumentStartJavaScript(
                web,
                "(function(){var s=document.createElement('style');s.id='ch-app-css';" +
                    "s.textContent='header[class~=\"lg:hidden\"]{display:none !important}" +
                    // Druck-Effekt fuer die Server-Symbole: leicht verkleinert + Hintergrund, solange gedrueckt
                    ".ch-press{transition:transform .12s,background-color .12s;-webkit-tap-highlight-color:transparent}" +
                    ".ch-press:active{transform:scale(.86);background-color:rgba(128,128,128,.32) !important}';" +
                    "(document.head||document.documentElement).appendChild(s);})();",
                setOf("*")
            )
        }
        web.addJavascriptInterface(Bridge(), "ClubHubApp")
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                val server = ServerStore.current(this@MainActivity)
                val base = server?.let { Uri.parse(it.url) }
                // Eigener Server bleibt in der App, alles andere (tel:, mailto:, fremde Seiten) extern
                if (base != null && url.host == base.host && url.port == base.port) return false
                return try {
                    startActivity(Intent(Intent.ACTION_VIEW, url))
                    true
                } catch (e: Exception) {
                    false
                }
            }

            override fun onPageFinished(view: WebView, url: String) {
                val cur = ServerStore.current(this@MainActivity)?.let { Uri.parse(it.url) }
                val u = Uri.parse(url)
                if (cur != null && u.host == cur.host && u.port == cur.port) injectHeading(view)
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: android.webkit.WebResourceError
            ) {
                if (request.isForMainFrame) {
                    val html = "<html><body style='background:#12161F;color:#e5e7eb;font-family:sans-serif;" +
                        "padding:32px;text-align:center'><h2>Server nicht erreichbar</h2><p>" +
                        "${error.description}</p><p>Seite neu laden oder über die App-Verknüpfung (Icon lange gedrückt halten) „Server verwalten“ öffnen.</p></body></html>"
                    view.loadDataWithBaseURL(null, html, "text/html", "utf-8", request.url.toString())
                }
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            // Kamera (Barcode-Scan): erst Android-Berechtigung holen, dann an die Seite weitergeben
            override fun onPermissionRequest(request: PermissionRequest) {
                runOnUiThread {
                    if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                        request.grant(request.resources)
                    } else {
                        pendingPermission = request
                        requestPermissions(arrayOf(Manifest.permission.CAMERA), REQ_CAMERA)
                    }
                }
            }

            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams
            ): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                return try {
                    startActivityForResult(params.createIntent(), REQ_FILE)
                    true
                } catch (e: Exception) {
                    fileCallback = null
                    false
                }
            }

            // Ohne diese Overrides werden alert()/confirm() der Seite stillschweigend abgebrochen
            override fun onJsAlert(view: WebView, url: String, message: String, result: JsResult): Boolean {
                MaterialAlertDialogBuilder(this@MainActivity).setMessage(message)
                    .setPositiveButton("OK") { _, _ -> result.confirm() }
                    .setOnCancelListener { result.cancel() }.show()
                return true
            }

            override fun onJsConfirm(view: WebView, url: String, message: String, result: JsResult): Boolean {
                MaterialAlertDialogBuilder(this@MainActivity).setMessage(message)
                    .setPositiveButton("OK") { _, _ -> result.confirm() }
                    .setNegativeButton("Abbrechen") { _, _ -> result.cancel() }
                    .setOnCancelListener { result.cancel() }.show()
                return true
            }
        }
        web.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            val req = DownloadManager.Request(Uri.parse(url))
                .setMimeType(mimeType)
                .addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url) ?: "")
                .addRequestHeader("User-Agent", userAgent)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(
                    Environment.DIRECTORY_DOWNLOADS,
                    URLUtil.guessFileName(url, contentDisposition, mimeType)
                )
            (getSystemService(DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
            Toast.makeText(this, "Download gestartet", Toast.LENGTH_SHORT).show()
        }
    }

    /** Oeffnet den aktuellen Server bzw. (bei NFC-/Link-Intent) die passende Adresse auf einem bekannten Server. */
    private fun handleIntent(intent: Intent, restored: Boolean) {
        val servers = ServerStore.load(this)
        if (servers.isEmpty()) {
            startActivityForResult(Intent(this, ServersActivity::class.java), REQ_SERVERS)
            return
        }
        val data = intent.data
        if (intent.action == Intent.ACTION_VIEW && data != null) {
            val match = servers.firstOrNull {
                val b = Uri.parse(it.url)
                b.host == data.host && b.port == data.port
            }
            if (match != null) {
                ServerStore.setCurrent(this, match.id)
                loadedServerId = match.id
                web.loadUrl(data.toString())
            } else {
                Toast.makeText(this, "Dieser Server ist in der App nicht hinterlegt", Toast.LENGTH_LONG).show()
                if (loadedServerId == null && !restored) loadCurrent()
            }
            return
        }
        if (restored && web.url != null) {
            loadedServerId = ServerStore.current(this)?.id
        } else {
            loadCurrent()
        }
    }

    private fun loadCurrent() {
        val server = ServerStore.current(this) ?: return
        loadedServerId = server.id
        web.loadUrl(server.url)
    }

    private fun askNotificationPermission() {
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFY)
    }

    override fun onResume() {
        super.onResume()
        if (ServerStore.notificationsEnabled(this)) {
            askNotificationPermission()
            NotificationService.start(this) // gleicht Threads mit evtl. geaenderter Serverliste ab
        }
        // Nach dem Zurueckkehren aus der Serververwaltung evtl. Server gewechselt/geloescht
        val cur = ServerStore.current(this)
        if (cur != null && cur.id != loadedServerId) loadCurrent()
        checkForUpdate()
    }

    /**
     * Fragt hoechstens alle 12 Stunden (force = sofort) bei allen hinterlegten Servern nach einer neueren
     * App-Version (static/app/latest.json) und bietet die neueste an. notify = auch "aktuell" melden.
     */
    private fun checkForUpdate(force: Boolean = false, notify: Boolean = false) {
        val servers = ServerStore.load(this)
        if (servers.isEmpty()) return
        val prefs = getSharedPreferences("clubhub", MODE_PRIVATE)
        if (!force && System.currentTimeMillis() - prefs.getLong("update_check", 0) < 12 * 3600 * 1000L) return
        prefs.edit().putLong("update_check", System.currentTimeMillis()).apply()
        Thread {
            val info = servers.mapNotNull { runCatching { Updater.fetchInfo(it) }.getOrNull() }.maxByOrNull { it.code }
            runOnUiThread {
                if (isFinishing || isDestroyed) return@runOnUiThread
                if (info == null) {
                    if (notify) Toast.makeText(this, "Die App ist aktuell (Version ${BuildConfig.VERSION_NAME})", Toast.LENGTH_LONG).show()
                    return@runOnUiThread
                }
                MaterialAlertDialogBuilder(this)
                    .setTitle("Neue App-Version")
                    .setMessage("Version ${info.name} ist verfügbar (installiert: ${BuildConfig.VERSION_NAME}).")
                    .setPositiveButton("Jetzt aktualisieren") { _, _ -> startUpdate(info) }
                    .setNegativeButton("Später", null)
                    .show()
            }
        }.start()
    }

    /** Laedt die neue APK mit Fortschrittsanzeige und startet die Installation. */
    private fun startUpdate(info: UpdateInfo) {
        if (!packageManager.canRequestPackageInstalls()) {
            MaterialAlertDialogBuilder(this)
                .setTitle("Erlaubnis nötig")
                .setMessage("Damit sich ClubHUB selbst aktualisieren kann, muss einmalig „Installation aus dieser Quelle zulassen“ aktiviert werden. Danach das Update erneut starten.")
                .setPositiveButton("Einstellungen") { _, _ ->
                    startActivity(Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
                }
                .setNegativeButton("Abbrechen", null)
                .show()
            return
        }
        val bar = com.google.android.material.progressindicator.LinearProgressIndicator(this).apply {
            max = 100
            isIndeterminate = false
        }
        val box = LinearLayout(this).apply {
            setPadding(Ui.dp(context, 24), Ui.dp(context, 16), Ui.dp(context, 24), Ui.dp(context, 8))
            addView(bar, LinearLayout.LayoutParams(-1, -2))
        }
        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle("Lade Version ${info.name} …")
            .setView(box)
            .setCancelable(false)
            .show()
        Thread {
            try {
                val file = Updater.download(this, info) { pct -> runOnUiThread { bar.setProgressCompat(pct, true) } }
                runOnUiThread { dialog.dismiss() }
                Updater.install(this, file)
            } catch (e: Exception) {
                runOnUiThread {
                    dialog.dismiss()
                    Toast.makeText(this, "Update fehlgeschlagen: ${e.message}", Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }

    /** Material-3-Bottom-Sheet: nur die Server (aktiver mit Haken) und "Server verwalten...". */
    private fun showSwitcher() {
        val servers = ServerStore.load(this)
        val cur = ServerStore.current(this)?.id
        val sheet = BottomSheetDialog(this)
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, Ui.dp(this@MainActivity, 8), 0, Ui.dp(this@MainActivity, 16))
        }
        fun dp(v: Int) = Ui.dp(this, v)
        col.addView(TextView(this).apply {
            text = "Server wechseln"
            setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_TitleMedium)
            setTextColor(Ui.color(context, Ui.onSurface))
            setPadding(dp(24), dp(8), dp(24), dp(12))
        })

        fun row(title: String, subtitle: String?, icon: Int?, checked: Boolean, onClick: () -> Unit) = LinearLayout(this).apply {
            gravity = android.view.Gravity.CENTER_VERTICAL
            setPadding(dp(24), 0, dp(24), 0)
            minimumHeight = dp(if (subtitle != null) 64 else 56)
            setBackgroundResource(Ui.ripple(context))
            val tc = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
            tc.addView(TextView(context).apply {
                text = title
                textSize = 16f
                setTextColor(Ui.color(context, Ui.onSurface))
                if (checked) setTypeface(null, android.graphics.Typeface.BOLD)
            })
            if (subtitle != null) tc.addView(TextView(context).apply {
                text = subtitle
                textSize = 13f
                setTextColor(Ui.color(context, Ui.onSurfaceVariant))
            })
            addView(tc, LinearLayout.LayoutParams(0, -2, 1f))
            if (icon != null) addView(android.widget.ImageView(context).apply {
                setImageResource(icon)
                if (checked) setColorFilter(Ui.color(context, Ui.primary))
            }, LinearLayout.LayoutParams(dp(24), dp(24)))
            setOnClickListener { sheet.dismiss(); onClick() }
        }

        servers.forEach { sv ->
            col.addView(row(sv.name, sv.url.substringAfter("://"), if (sv.id == cur) R.drawable.ic_check else null, sv.id == cur) {
                if (sv.id != cur) {
                    ServerStore.setCurrent(this, sv.id)
                    loadCurrent()
                }
            }, LinearLayout.LayoutParams(-1, -2))
        }
        col.addView(com.google.android.material.divider.MaterialDivider(this).apply {
            dividerInsetStart = dp(24)
            dividerInsetEnd = dp(24)
        }, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8); bottomMargin = dp(8) })
        col.addView(row("Server verwalten…", null, R.drawable.ic_settings, false) {
            startActivityForResult(Intent(this, ServersActivity::class.java), REQ_SERVERS)
        }, LinearLayout.LayoutParams(-1, -2))

        sheet.setContentView(ScrollView(this).apply { addView(col) })
        // Auf Tablets/im Querformat sonst nur ein schmaler Streifen: immer ganz aufklappen
        sheet.behavior.skipCollapsed = true
        sheet.behavior.state = com.google.android.material.bottomsheet.BottomSheetBehavior.STATE_EXPANDED
        sheet.show()
    }

    /** Bruecke fuer die Ueberschrift auf der Dashboard-Seite (Tippen/Langdruck oeffnet das Servermenue). */
    inner class Bridge {
        @JavascriptInterface
        fun openSwitcher() {
            runOnUiThread { showSwitcher() }
        }

        @JavascriptInterface
        fun setThemeMode(mode: String) {
            runOnUiThread {
                val before = ThemeMode.isDark(this@MainActivity)
                ThemeMode.setMode(this@MainActivity, mode)
                if (ThemeMode.isDark(this@MainActivity) != before) recreate()
            }
        }

        @JavascriptInterface
        fun checkUpdate() {
            runOnUiThread { checkForUpdate(force = true, notify = true) }
        }

        @JavascriptInterface
        fun appVersion(): String = BuildConfig.VERSION_NAME

        @JavascriptInterface
        fun serverName(): String = ServerStore.current(this@MainActivity)?.name ?: ""

        @JavascriptInterface
        fun openPlaces() {
            runOnUiThread { startActivity(Intent(this@MainActivity, PlacesActivity::class.java)) }
        }

        @JavascriptInterface
        fun notificationsEnabled(): Boolean = ServerStore.notificationsEnabled(this@MainActivity)

        @JavascriptInterface
        fun toggleNotifications() {
            runOnUiThread {
                val on = !ServerStore.notificationsEnabled(this@MainActivity)
                ServerStore.setNotificationsEnabled(this@MainActivity, on)
                if (on) {
                    askNotificationPermission()
                    NotificationService.start(this@MainActivity)
                } else {
                    NotificationService.stop(this@MainActivity)
                }
                Toast.makeText(this@MainActivity, if (on) "Mitteilungen eingeschaltet" else "Mitteilungen ausgeschaltet", Toast.LENGTH_SHORT).show()
            }
        }
    }

    /** Ersetzt auf dem Dashboard die Ueberschrift durch den Servernamen (nur bei mehreren Servern). */
    private fun injectHeading(view: WebView) {
        val servers = ServerStore.load(this)
        val cur = ServerStore.current(this) ?: return
        val name = org.json.JSONObject.quote(cur.name)
        val multi = servers.size > 1
        val js = """
            (function () {
              var h = document.querySelector('h1');
              if (!h || h.dataset.chApp || h.textContent.trim() !== 'Dashboard') return;
              h.dataset.chApp = '1';
              if (!$multi) return;
              // Nur das Symbol ist klickbar (Handy); auf Tablet/Desktop sitzt es in der Seitenleiste
              if (!document.getElementById('ch-app-css2')) {
                var st = document.createElement('style');
                st.id = 'ch-app-css2';
                st.textContent = '@media (min-width:1024px){.ch-srv-icon{display:none !important}}';
                document.head.appendChild(st);
              }
              var w = document.createElement('span');
              w.style.cssText = 'display:inline-flex;align-items:center;gap:6px';
              var ib = document.createElement('button');
              ib.type = 'button';
              ib.className = 'ch-srv-icon ch-press';
              ib.setAttribute('aria-label', 'Server wechseln');
              ib.style.cssText = 'display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;margin-left:-10px;border-radius:12px;background:none;border:0;color:inherit';
              ib.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="8" rx="2"/><rect x="3" y="13" width="18" height="8" rx="2"/><line x1="7" y1="7" x2="7.01" y2="7"/><line x1="7" y1="17" x2="7.01" y2="17"/></svg>';
              ib.addEventListener('click', function () { ClubHubApp.openSwitcher(); });
              var nm = document.createElement('span');
              nm.textContent = $name;
              w.appendChild(ib);
              w.appendChild(nm);
              h.textContent = '';
              h.appendChild(w);
            })();
        """.trimIndent()
        view.evaluateJavascript(js, null)
        view.evaluateJavascript(CHROME_JS, null)
    }

    /**
     * Blendet die mobile Kopfzeile der Seite aus (Platz!) und legt ihre Funktionen
     * (Profil, Mitteilungen, Design, Abmelden, Version) als Eintraege ins "Mehr"-Menue.
     * Rein app-seitig - funktioniert mit jeder Serverversion, im Browser aendert sich nichts.
     */
    private val CHROME_JS = """
        (function () {
          try { ClubHubApp.setThemeMode(localStorage.getItem('theme') || 'auto'); } catch (e) {}
          var hdr = document.querySelector('header[class~="lg:hidden"]');
          if (hdr && !document.getElementById('ch-app-css')) {
            var st = document.createElement('style');
            st.id = 'ch-app-css';
            st.textContent = 'header[class~="lg:hidden"]{display:none !important}';
            document.head.appendChild(st);
          }
          var SRV = '<svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="8" rx="2"/><rect x="3" y="13" width="18" height="8" rx="2"/><line x1="7" y1="7" x2="7.01" y2="7"/><line x1="7" y1="17" x2="7.01" y2="17"/></svg>';
          // Tablet/Desktop: Server-Symbol neben dem Logo in der (mitscrollenden) Seitenleiste
          var side = document.querySelector('aside > div');
          if (side && !side.dataset.chSrv) {
            side.dataset.chSrv = '1';
            var sb = document.createElement('button');
            sb.type = 'button';
            sb.className = 'ml-auto p-2 rounded-lg text-sidebar-text hover:bg-white/10 hover:text-sidebar-text-strong shrink-0 ch-press';
            sb.title = 'Server wechseln (' + ClubHubApp.serverName() + ')';
            sb.setAttribute('aria-label', 'Server wechseln');
            sb.innerHTML = SRV;
            sb.addEventListener('click', function () { ClubHubApp.openSwitcher(); });
            side.appendChild(sb);
            var sv = side.querySelector('a[href="/changelog"]');
            if (sv) sv.textContent = ClubHubApp.serverName() + ' · ' + sv.textContent;
          }
          var grid = document.querySelector('#mobile-more-sheet .grid');
          if (!grid || grid.dataset.chApp) return;
          grid.dataset.chApp = '1';
          var cls = 'flex flex-col items-center gap-1 py-3 rounded-lg text-slate-300 hover:bg-panel2 text-xs ch-press';
          var ico = function (p) { return '<svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + p + '</svg>'; };
          function cell(tag, icon, label, fn, href) {
            var e = document.createElement(tag);
            e.className = cls;
            if (href) e.href = href; else e.type = 'button';
            e.innerHTML = icon + '<span>' + label + '</span>';
            if (fn) e.addEventListener('click', fn);
            grid.appendChild(e);
            return e;
          }
          cell('button', SRV, ClubHubApp.serverName(), function () { ClubHubApp.openSwitcher(); });
          cell('a', ico('<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>'), 'Profil', null, '/profile');
          var n = cell('button', ico('<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>'), '', function () {
            ClubHubApp.toggleNotifications();
            setTimeout(label, 400);
          });
          function label() { n.querySelector('span').textContent = 'Mitteilungen ' + (ClubHubApp.notificationsEnabled() ? 'an' : 'aus'); }
          label();
          cell('button', ico('<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>'), 'Orte', function () { ClubHubApp.openPlaces(); });
          cell('button', ico('<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>'), 'Design', function () {
            var b = hdr && hdr.querySelector('.theme-toggle-btn');
            if (b) b.click();
            setTimeout(function () { try { ClubHubApp.setThemeMode(localStorage.getItem('theme') || 'auto'); } catch (e) {} }, 300);
          });
          cell('button', ico('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>'), 'Update', function () { ClubHubApp.checkUpdate(); });
          cell('a', ico('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>'), 'Abmelden', null, '/logout');
          var v = hdr && hdr.querySelector('a[href="/changelog"]');
          if (v) {
            var f = document.createElement('div');
            f.className = 'px-4 pb-3 text-center';
            f.innerHTML = '<a href="/changelog" class="text-[11px] text-slate-500 hover:underline">ClubHUB ' + v.textContent.trim() + ' · App ' + ClubHubApp.appVersion() + '</a>';
            grid.parentNode.appendChild(f);
          }
        })();
    """.trimIndent()

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == REQ_FILE) {
            fileCallback?.onReceiveValue(
                if (resultCode == RESULT_OK) WebChromeClient.FileChooserParams.parseResult(resultCode, data) else null
            )
            fileCallback = null
            return
        }
        super.onActivityResult(requestCode, resultCode, data)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        if (requestCode == REQ_CAMERA) {
            val req = pendingPermission
            pendingPermission = null
            if (req != null) {
                if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) req.grant(req.resources)
                else req.deny()
            }
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }
}
