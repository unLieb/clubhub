package de.clubhub.app

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

data class HeldMessage(val serverId: String, val title: String, val message: String, val path: String, val time: Long)

/** Zurueckgehaltene Mitteilungen (nicht auf Arbeit / ausserhalb der Arbeitszeit); werden beim Ankommen gebuendelt gezeigt. */
object Held {
    private const val KEY = "held"
    private const val MAX_AGE_MS = 24L * 60 * 60 * 1000
    private const val MAX_ITEMS = 100
    private val lock = Any()

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("clubhub", Context.MODE_PRIVATE)

    private fun read(ctx: Context): MutableList<HeldMessage> {
        val raw = prefs(ctx).getString(KEY, null) ?: return mutableListOf()
        return try {
            val a = JSONArray(raw)
            (0 until a.length()).map {
                val o = a.getJSONObject(it)
                HeldMessage(o.getString("s"), o.getString("t"), o.getString("m"), o.getString("p"), o.getLong("at"))
            }.toMutableList()
        } catch (e: Exception) {
            mutableListOf()
        }
    }

    private fun write(ctx: Context, list: List<HeldMessage>) {
        val a = JSONArray()
        list.forEach { a.put(JSONObject().put("s", it.serverId).put("t", it.title).put("m", it.message).put("p", it.path).put("at", it.time)) }
        prefs(ctx).edit().putString(KEY, a.toString()).apply()
    }

    fun add(ctx: Context, m: HeldMessage) = synchronized(lock) {
        val l = read(ctx)
        l.add(m)
        write(ctx, l.takeLast(MAX_ITEMS))
    }

    /** Entfernt Eintraege aelter als 24 h und liefert den Rest. */
    fun all(ctx: Context): List<HeldMessage> = synchronized(lock) {
        val now = System.currentTimeMillis()
        val l = read(ctx)
        val kept = l.filter { now - it.time < MAX_AGE_MS }
        if (kept.size != l.size) write(ctx, kept)
        kept
    }

    fun clear(ctx: Context) = synchronized(lock) { write(ctx, emptyList()) }

    fun count(ctx: Context) = all(ctx).size
}
