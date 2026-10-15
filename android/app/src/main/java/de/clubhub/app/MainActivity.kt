package de.clubhub.app

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.app.DownloadManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
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

        fun styleActionBar(a: Activity) {
            a.actionBar?.setBackgroundDrawable(ColorDrawable(Color.parseColor("#1B2130")))
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        web = WebView(this)
        val root = FrameLayout(this)
        root.setBackgroundColor(Color.parseColor("#12161F"))
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
        web.setBackgroundColor(Color.parseColor("#12161F"))
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
                    "s.textContent='header[class~=\"lg:hidden\"]{display:none !important}';" +
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
                AlertDialog.Builder(this@MainActivity).setMessage(message)
                    .setPositiveButton("OK") { _, _ -> result.confirm() }
                    .setOnCancelListener { result.cancel() }.show()
                return true
            }

            override fun onJsConfirm(view: WebView, url: String, message: String, result: JsResult): Boolean {
                AlertDialog.Builder(this@MainActivity).setMessage(message)
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
    }

    private fun showSwitcher() {
        val servers = ServerStore.load(this)
        val cur = ServerStore.current(this)?.id
        val labels = servers.map { (if (it.id == cur) "✓ " else "") + it.name } +
            "Neu laden" + "Orte (Standort-Regel)…" + "Server verwalten…"
        AlertDialog.Builder(this)
            .setTitle("Server")
            .setItems(labels.toTypedArray()) { _, i ->
                when {
                    i < servers.size -> {
                        ServerStore.setCurrent(this, servers[i].id)
                        loadCurrent()
                    }
                    i == servers.size -> web.reload()
                    i == servers.size + 1 -> startActivity(Intent(this, PlacesActivity::class.java))
                    else -> startActivityForResult(Intent(this, ServersActivity::class.java), REQ_SERVERS)
                }
            }
            .show()
    }

    /** Bruecke fuer die Ueberschrift auf der Dashboard-Seite (Tippen/Langdruck oeffnet das Servermenue). */
    inner class Bridge {
        @JavascriptInterface
        fun openSwitcher() {
            runOnUiThread { showSwitcher() }
        }

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
              if ($multi) {
                h.textContent = $name + ' ▾';
                h.style.cursor = 'pointer';
                h.addEventListener('click', function () { ClubHubApp.openSwitcher(); });
              }
              var t;
              h.addEventListener('touchstart', function () { t = setTimeout(function () { ClubHubApp.openSwitcher(); }, 600); });
              ['touchend', 'touchmove', 'touchcancel'].forEach(function (e) { h.addEventListener(e, function () { clearTimeout(t); }); });
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
          var hdr = document.querySelector('header[class~="lg:hidden"]');
          if (hdr && !document.getElementById('ch-app-css')) {
            var st = document.createElement('style');
            st.id = 'ch-app-css';
            st.textContent = 'header[class~="lg:hidden"]{display:none !important}';
            document.head.appendChild(st);
          }
          var grid = document.querySelector('#mobile-more-sheet .grid');
          if (!grid || grid.dataset.chApp) return;
          grid.dataset.chApp = '1';
          var cls = 'flex flex-col items-center gap-1 py-3 rounded-lg text-slate-300 hover:bg-panel2 text-xs';
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
          });
          cell('a', ico('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>'), 'Abmelden', null, '/logout');
          var v = hdr && hdr.querySelector('a[href="/changelog"]');
          if (v) {
            var f = document.createElement('div');
            f.className = 'px-4 pb-3 text-center';
            f.innerHTML = '<a href="/changelog" class="text-[11px] text-slate-500 hover:underline">ClubHUB ' + v.textContent.trim() + '</a>';
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
