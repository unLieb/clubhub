package de.clubhub.app

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Offline-Ansicht (nur lesen): letzter Schnappschuss des Servers - Aufgaben, Meldungen, Bereiche.
 * Wird aus der Fehleransicht geoeffnet, wenn der Server nicht erreichbar ist.
 */
class OfflineActivity : Activity() {
    private lateinit var content: LinearLayout
    private lateinit var tabs: LinearLayout
    private var snap: JSONObject = JSONObject()
    private var tab = 0

    private val red = Color.parseColor("#D64545")
    private val yellow = Color.parseColor("#C98A12")

    private fun dp(v: Int) = Ui.dp(this, v)

    override fun onCreate(savedInstanceState: Bundle?) {
        ThemeMode.apply(this)
        super.onCreate(savedInstanceState)
        val server = ServerStore.load(this).firstOrNull { it.id == intent.getStringExtra("server_id") }
            ?: ServerStore.current(this)
        val s = server?.let { Snapshot.load(this, it.id) }
        if (server == null || s == null) {
            finish()
            return
        }
        snap = s
        val stand = SimpleDateFormat("dd.MM. HH:mm", Locale.GERMANY).format(Date(s.optLong("generated_ms")))

        val root = Ui.root(this)
        root.addView(Ui.toolbar(this, "Offline – ${server.name}", "Stand: $stand Uhr · nur Ansicht"))

        val ageH = (System.currentTimeMillis() - s.optLong("generated_ms")) / 3_600_000
        if (ageH >= 6) {
            root.addView(TextView(this).apply {
                text = "Dieser Stand ist älter als $ageH Stunden – inzwischen kann sich vieles geändert haben."
                textSize = 13f
                setTextColor(Color.WHITE)
                setBackgroundColor(yellow)
                setPadding(dp(16), dp(8), dp(16), dp(8))
            }, LinearLayout.LayoutParams(-1, -2))
        }

        tabs = LinearLayout(this).apply { setPadding(dp(12), dp(4), dp(12), dp(4)) }
        root.addView(tabs, LinearLayout.LayoutParams(-1, -2))
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(8), dp(16), dp(24))
        }
        root.addView(ScrollView(this).apply { addView(content) }, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        render()
    }

    private fun text(t: String, size: Float, color: Int, bold: Boolean = false) = TextView(this).apply {
        text = t
        textSize = size
        setTextColor(color)
        if (bold) setTypeface(null, Typeface.BOLD)
    }

    private fun statusColor(st: String) = when (st) {
        "red" -> red
        "yellow" -> yellow
        else -> Ui.color(this, Ui.primary)
    }

    private fun render() {
        val todoN = snap.optJSONArray("todo")?.length() ?: 0
        val repN = snap.optJSONArray("reports")?.length() ?: 0
        val roomN = snap.optJSONArray("rooms")?.length() ?: 0
        val names = listOf("Aufgaben ($todoN)", "Meldungen ($repN)", "Bereiche ($roomN)")
        tabs.removeAllViews()
        names.forEachIndexed { i, n ->
            val b = if (i == tab) Ui.filledButton(this, n) { tab = i; render() } else Ui.tonalButton(this, n) { tab = i; render() }
            b.textSize = 12f
            b.setPadding(dp(4), 0, dp(4), 0)
            tabs.addView(b, LinearLayout.LayoutParams(0, dp(44), 1f).apply { if (i > 0) leftMargin = dp(6) })
        }
        content.removeAllViews()
        when (tab) {
            0 -> renderTodo()
            1 -> renderReports()
            else -> renderRooms()
        }
    }

    private fun addCard(inner: View, highlightColor: Int? = null) {
        val card = Ui.card(this, false).apply {
            addView(inner)
            if (highlightColor != null) strokeColor = highlightColor
        }
        content.addView(card, Ui.margins(content, t = 4, b = 4))
    }

    private fun box(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(14), dp(10), dp(14), dp(10))
    }

    private fun renderTodo() {
        val appts = snap.optJSONArray("appointments") ?: JSONArray()
        if (appts.length() > 0) {
            val b = box()
            b.addView(text("Termine heute", 13f, Ui.color(this, Ui.onSurfaceVariant), true))
            for (i in 0 until appts.length()) b.addView(text(appts.getString(i), 15f, Ui.color(this, Ui.onSurface)))
            addCard(b)
        }
        val todo = snap.optJSONArray("todo") ?: JSONArray()
        if (todo.length() == 0) {
            content.addView(text("Zum Zeitpunkt des Standes war nichts offen.", 15f, Ui.color(this, Ui.onSurfaceVariant)),
                Ui.margins(content, t = 16))
            return
        }
        for (i in 0 until todo.length()) {
            val t = todo.getJSONObject(i)
            val b = box()
            b.addView(text(t.getString("room"), 16f, Ui.color(this, Ui.onSurface), true))
            b.addView(text(t.getString("task"), 14f, Ui.color(this, Ui.onSurfaceVariant)))
            b.addView(text(t.getString("text"), 13f, statusColor(t.getString("status")), true))
            addCard(b, statusColor(t.getString("status")))
        }
    }

    private fun renderReports() {
        val reports = snap.optJSONArray("reports") ?: JSONArray()
        if (reports.length() == 0) {
            content.addView(text("Zum Zeitpunkt des Standes gab es keine offenen Meldungen.", 15f, Ui.color(this, Ui.onSurfaceVariant)),
                Ui.margins(content, t = 16))
            return
        }
        for (i in 0 until reports.length()) {
            val r = reports.getJSONObject(i)
            val b = box()
            b.addView(text(r.getString("title"), 16f, Ui.color(this, Ui.onSurface), true))
            val meta = listOfNotNull(
                r.optString("room").takeIf { it.isNotEmpty() && it != "null" },
                priorityLabel(r.optString("priority")),
                if (r.optString("status") == "in_progress") "in Bearbeitung" else "offen",
                r.optString("created")
            ).joinToString(" · ")
            b.addView(text(meta, 12f, Ui.color(this, Ui.onSurfaceVariant)))
            val body = r.optString("body")
            if (body.isNotEmpty()) b.addView(text(body, 14f, Ui.color(this, Ui.onSurface)), Ui.margins(b, t = 4))
            addCard(b, if (r.optString("priority") == "high" || r.optString("priority") == "critical") red else null)
        }
    }

    private fun priorityLabel(p: String) = when (p) {
        "critical" -> "kritisch"
        "high" -> "hoch"
        "low" -> "niedrig"
        else -> null
    }

    private fun renderRooms() {
        val rooms = snap.optJSONArray("rooms") ?: JSONArray()
        if (rooms.length() == 0) {
            content.addView(text("Keine Bereiche im Schnappschuss.", 15f, Ui.color(this, Ui.onSurfaceVariant)), Ui.margins(content, t = 16))
            return
        }
        for (i in 0 until rooms.length()) {
            val r = rooms.getJSONObject(i)
            val tasks = r.optJSONArray("tasks") ?: JSONArray()
            val b = box()
            val head = LinearLayout(this).apply { gravity = android.view.Gravity.CENTER_VERTICAL }
            head.addView(View(this).apply { background = Ui.circle(statusColor(r.getString("status"))) },
                LinearLayout.LayoutParams(dp(12), dp(12)).apply { rightMargin = dp(10) })
            head.addView(text(r.getString("name"), 16f, Ui.color(this, Ui.onSurface), true),
                LinearLayout.LayoutParams(0, -2, 1f))
            head.addView(text("${tasks.length()} ▾", 13f, Ui.color(this, Ui.onSurfaceVariant)))
            b.addView(head)
            val sub = r.optString("text").takeIf { it.isNotEmpty() && it != "null" } ?: "alles erledigt"
            b.addView(text(sub, 13f, statusColor(r.getString("status"))), Ui.margins(b, l = 22))

            val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; visibility = View.GONE }
            for (j in 0 until tasks.length()) {
                val t = tasks.getJSONObject(j)
                val row = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(22), dp(6), 0, dp(2)) }
                row.addView(text(t.getString("name"), 14f, Ui.color(this, Ui.onSurface)))
                val last = t.optString("last").takeIf { it.isNotEmpty() && it != "null" }
                row.addView(text(t.getString("text") + (if (last != null) " · zuletzt $last" else ""), 12f, statusColor(t.getString("status"))))
                list.addView(row)
            }
            b.addView(list)
            val card = Ui.card(this, false).apply {
                addView(b)
                setOnClickListener { list.visibility = if (list.visibility == View.VISIBLE) View.GONE else View.VISIBLE }
            }
            content.addView(card, Ui.margins(content, t = 4, b = 4))
        }
    }
}
