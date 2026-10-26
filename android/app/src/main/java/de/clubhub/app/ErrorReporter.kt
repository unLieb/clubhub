package de.clubhub.app

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.webkit.CookieManager
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Meldet WebView-Fehler (Seite nicht erreichbar, Verbindung abgebrochen, Serverfehler) ans
 * Fehlerprotokoll des Servers (POST /client-log, Anzeige unter Verwaltung -> Fehlerprotokoll).
 *
 * Genau die Faelle, bei denen die Anfrage den Server nie erreicht, kann nur die App selbst
 * sehen - und dann ist der Server meist auch gerade nicht erreichbar. Deshalb landet jeder
 * Eintrag zuerst in einer kleinen Warteschlange (SharedPreferences) und wird gesendet, sobald
 * es klappt: beim naechsten erfolgreich geladenen Seitenaufruf bzw. beim Zurueckkehren in die App.
 * Die Anmeldung kommt aus dem Cookie des WebViews (der Endpunkt verlangt einen angemeldeten
 * Nutzer); ohne Anmeldung (z.B. Login-Seite nicht erreichbar) wird der Eintrag verworfen.
 */
object ErrorReporter {
    private const val PREFS = "clubhub_errlog"
    private const val KEY_QUEUE = "queue"
    private const val MAX_QUEUE = 20
    private const val MAX_AGE_MS = 6L * 24 * 3600 * 1000 // Server nimmt nur Eintraege der letzten 7 Tage
    private val lock = Any()
    @Volatile private var flushing = false

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun isoNow(): String =
        SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
            .apply { timeZone = TimeZone.getTimeZone("UTC") }.format(Date())

    /** "WLAN" / "Mobilfunk" / "kein Netz" - hilft einzuordnen, wo die Verbindung scheiterte. */
    private fun networkInfo(ctx: Context): String {
        return try {
            val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return "kein Netz"
            when {
                caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "WLAN"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "Mobilfunk"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "VPN"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "Ethernet"
                else -> "anderes Netz"
            }
        } catch (e: Exception) {
            "Netz unbekannt"
        }
    }

    /** Legt einen Eintrag in die Warteschlange und versucht sofort zu senden. Wirft nie. */
    fun report(ctx: Context, server: Server, kind: String, path: String, method: String?, status: Int?, details: String) {
        try {
            val entry = JSONObject()
                .put("server_url", server.url)
                .put("kind", kind)
                .put("path", path)
                .put("method", method ?: "")
                .put("details", "$details | ${networkInfo(ctx)}, App ${BuildConfig.VERSION_NAME}")
                .put("occurred_at", isoNow())
                .put("queued_at", System.currentTimeMillis())
            if (status != null) entry.put("status", status)
            synchronized(lock) {
                val arr = load(ctx)
                arr.put(entry)
                save(ctx, JSONArray((maxOf(0, arr.length() - MAX_QUEUE) until arr.length()).map { arr.get(it) }))
            }
            flush(ctx)
        } catch (e: Exception) {
            // Protokollieren darf die App nie stoeren
        }
    }

    private fun load(ctx: Context): JSONArray =
        try { JSONArray(prefs(ctx).getString(KEY_QUEUE, "[]")) } catch (e: Exception) { JSONArray() }

    private fun save(ctx: Context, arr: JSONArray) {
        prefs(ctx).edit().putString(KEY_QUEUE, arr.toString()).apply()
    }

    /** Sendet die Warteschlange im Hintergrund der Reihe nach; bei der ersten Panne Abbruch bis zum naechsten Mal. */
    fun flush(ctx: Context) {
        if (flushing) return
        flushing = true
        val app = ctx.applicationContext
        Thread {
            try {
                while (true) {
                    val entry = synchronized(lock) { load(app).optJSONObject(0) } ?: break
                    val outcome = send(app, entry)
                    if (outcome == Outcome.RETRY) break
                    synchronized(lock) {
                        val arr = load(app)
                        save(app, JSONArray((1 until arr.length()).map { arr.get(it) }))
                    }
                }
            } catch (e: Exception) {
                // naechster Versuch beim naechsten Aufruf
            } finally {
                flushing = false
            }
        }.start()
    }

    private enum class Outcome { DONE, DROP, RETRY }

    private fun send(ctx: Context, entry: JSONObject): Outcome {
        if (System.currentTimeMillis() - entry.optLong("queued_at") > MAX_AGE_MS) return Outcome.DROP
        val base = entry.optString("server_url")
        if (base.isEmpty()) return Outcome.DROP
        val cookie = CookieManager.getInstance().getCookie(base)
        if (cookie.isNullOrEmpty()) return Outcome.RETRY // evtl. noch nicht angemeldet - spaeter nochmal
        val body = JSONObject(entry.toString()).apply { remove("server_url"); remove("queued_at") }
        val conn = URL("$base/client-log").openConnection() as HttpURLConnection
        return try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 8_000
            conn.readTimeout = 8_000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("Cookie", cookie)
            // "ClubHUBApp/" im User-Agent laesst den Server den Eintrag als "App" einordnen
            conn.setRequestProperty(
                "User-Agent",
                "ClubHUBApp/${BuildConfig.VERSION_NAME} (Android ${Build.VERSION.RELEASE}; ${Build.MANUFACTURER} ${Build.MODEL})"
            )
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
            when (val code = conn.responseCode) {
                in 200..299 -> Outcome.DONE
                in 400..499 -> Outcome.DROP // z.B. nicht angemeldet: Wiederholen bringt nichts
                else -> if (code >= 500) Outcome.RETRY else Outcome.DROP
            }
        } catch (e: Exception) {
            Outcome.RETRY
        } finally {
            conn.disconnect()
        }
    }
}
