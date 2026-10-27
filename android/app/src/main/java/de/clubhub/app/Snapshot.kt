package de.clubhub.app

import android.content.Context
import android.webkit.CookieManager
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Schreibgeschuetzter Schnappschuss je Server (heute anstehende Aufgaben, offene Meldungen, Bereiche) fuer die
 * Offline-Ansicht. Quelle: GET /api/app/snapshot (Session-Cookie des WebViews). Jeder Server hat seine eigene Datei.
 */
object Snapshot {
    private const val MIN_INTERVAL_MS = 15 * 60_000L

    private fun file(ctx: Context, serverId: String) = File(ctx.filesDir, "snapshot_$serverId.json")

    fun load(ctx: Context, serverId: String): JSONObject? = try {
        val f = file(ctx, serverId)
        if (f.exists()) JSONObject(f.readText()) else null
    } catch (e: Exception) {
        null
    }

    fun savedAt(ctx: Context, serverId: String): Long = load(ctx, serverId)?.optLong("generated_ms", 0L) ?: 0L

    fun delete(ctx: Context, serverId: String) {
        runCatching { file(ctx, serverId).delete() }
    }

    /** Blockierend (nicht auf dem UI-Thread). Holt den Schnappschuss hoechstens alle 15 Minuten (force = sofort). */
    fun refresh(ctx: Context, server: Server, force: Boolean = false) {
        val last = file(ctx, server.id).let { if (it.exists()) it.lastModified() else 0L }
        if (!force && System.currentTimeMillis() - last < MIN_INTERVAL_MS) return
        val cookie = CookieManager.getInstance().getCookie(server.url)
        if (cookie.isNullOrEmpty()) return
        try {
            val conn = URL("${server.url}/api/app/snapshot").openConnection() as HttpURLConnection
            conn.connectTimeout = 10_000
            conn.readTimeout = 20_000
            conn.setRequestProperty("Cookie", cookie)
            conn.setRequestProperty("User-Agent", "ClubHUBApp")
            try {
                if (conn.responseCode != 200) return // z.B. 401 (abgemeldet) oder aelterer Server ohne Endpunkt
                val body = conn.inputStream.bufferedReader().use { it.readText() }
                if (JSONObject(body).optBoolean("ok", false)) file(ctx, server.id).writeText(body)
            } finally {
                conn.disconnect()
            }
        } catch (e: Exception) {
            // Server nicht erreichbar - der alte Schnappschuss bleibt erhalten
        }
    }
}
