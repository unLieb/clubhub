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
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.webkit.CookieManager
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
import android.widget.ProgressBar
import android.widget.Toast

class MainActivity : Activity() {
    private lateinit var web: WebView
    private lateinit var progress: ProgressBar
    private var loadedServerId: String? = null

    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var pendingPermission: PermissionRequest? = null

    companion object {
        private const val REQ_SERVERS = 1
        private const val REQ_FILE = 2
        private const val REQ_CAMERA = 3

        fun styleActionBar(a: Activity) {
            a.actionBar?.setBackgroundDrawable(ColorDrawable(Color.parseColor("#1B2130")))
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        styleActionBar(this)

        web = WebView(this)
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            visibility = View.GONE
        }
        val root = FrameLayout(this)
        root.setBackgroundColor(Color.parseColor("#12161F"))
        root.addView(web, FrameLayout.LayoutParams(-1, -1))
        root.addView(progress, FrameLayout.LayoutParams(-1, 8))
        setContentView(root)

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
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false)
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
        }
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

            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
                progress.visibility = View.VISIBLE
            }

            override fun onPageFinished(view: WebView, url: String) {
                progress.visibility = View.GONE
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: android.webkit.WebResourceError
            ) {
                if (request.isForMainFrame) {
                    val html = "<html><body style='background:#12161F;color:#e5e7eb;font-family:sans-serif;" +
                        "padding:32px;text-align:center'><h2>Server nicht erreichbar</h2><p>" +
                        "${error.description}</p><p>Menü → Neu laden oder anderen Server wählen.</p></body></html>"
                    view.loadDataWithBaseURL(null, html, "text/html", "utf-8", request.url.toString())
                }
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                progress.progress = newProgress
            }

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
                title = match.name
                web.loadUrl(data.toString())
            } else {
                Toast.makeText(this, "Dieser Server ist in der App nicht hinterlegt", Toast.LENGTH_LONG).show()
                if (loadedServerId == null && !restored) loadCurrent()
            }
            return
        }
        if (restored && web.url != null) {
            loadedServerId = ServerStore.current(this)?.id
            title = ServerStore.current(this)?.name
        } else {
            loadCurrent()
        }
    }

    private fun loadCurrent() {
        val server = ServerStore.current(this) ?: return
        loadedServerId = server.id
        title = server.name
        web.loadUrl(server.url)
    }

    override fun onResume() {
        super.onResume()
        // Nach dem Zurueckkehren aus der Serververwaltung evtl. Server gewechselt/geloescht
        val cur = ServerStore.current(this)
        if (cur != null && cur.id != loadedServerId) loadCurrent()
    }

    private fun showSwitcher() {
        val servers = ServerStore.load(this)
        val labels = servers.map { it.name } + "Server verwalten…"
        AlertDialog.Builder(this)
            .setTitle("Server wechseln")
            .setItems(labels.toTypedArray()) { _, i ->
                if (i < servers.size) {
                    ServerStore.setCurrent(this, servers[i].id)
                    loadCurrent()
                } else {
                    startActivityForResult(Intent(this, ServersActivity::class.java), REQ_SERVERS)
                }
            }
            .show()
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, "Server wechseln")
        menu.add(0, 2, 1, "Neu laden")
        menu.add(0, 3, 2, "Startseite")
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            1 -> showSwitcher()
            2 -> web.reload()
            3 -> loadCurrent()
        }
        return true
    }

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
