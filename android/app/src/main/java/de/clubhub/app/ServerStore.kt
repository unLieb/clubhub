package de.clubhub.app

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class Server(val id: String, val name: String, val url: String)

/** Speichert die vom Nutzer hinterlegten Server (JSON in SharedPreferences). */
object ServerStore {
    private const val PREFS = "clubhub"
    private const val KEY_SERVERS = "servers"
    private const val KEY_CURRENT = "current"

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun load(ctx: Context): List<Server> {
        val raw = prefs(ctx).getString(KEY_SERVERS, null) ?: return emptyList()
        return try {
            val arr = JSONArray(raw)
            (0 until arr.length()).map {
                val o = arr.getJSONObject(it)
                Server(o.getString("id"), o.getString("name"), o.getString("url"))
            }
        } catch (e: Exception) {
            emptyList()
        }
    }

    private fun save(ctx: Context, list: List<Server>) {
        val arr = JSONArray()
        list.forEach {
            arr.put(JSONObject().put("id", it.id).put("name", it.name).put("url", it.url))
        }
        prefs(ctx).edit().putString(KEY_SERVERS, arr.toString()).apply()
    }

    fun current(ctx: Context): Server? {
        val list = load(ctx)
        val id = prefs(ctx).getString(KEY_CURRENT, null)
        return list.firstOrNull { it.id == id } ?: list.firstOrNull()
    }

    fun setCurrent(ctx: Context, id: String) {
        prefs(ctx).edit().putString(KEY_CURRENT, id).apply()
    }

    /** id == null legt einen neuen Server an; der erste angelegte wird automatisch ausgewaehlt. */
    fun upsert(ctx: Context, id: String?, name: String, url: String) {
        val list = load(ctx).toMutableList()
        if (id == null) {
            val s = Server(UUID.randomUUID().toString(), name, url)
            list.add(s)
            if (list.size == 1) setCurrent(ctx, s.id)
        } else {
            val i = list.indexOfFirst { it.id == id }
            if (i >= 0) list[i] = Server(id, name, url)
        }
        save(ctx, list)
    }

    fun delete(ctx: Context, id: String) {
        save(ctx, load(ctx).filter { it.id != id })
    }

    /** Ergaenzt fehlendes Schema (https) und entfernt abschliessende Schraegstriche. */
    fun normalizeUrl(input: String): String {
        var u = input.trim()
        if (!u.contains("://")) u = "https://$u"
        return u.trimEnd('/')
    }
}
