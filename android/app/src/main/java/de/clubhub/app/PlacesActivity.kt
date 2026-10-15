package de.clubhub.app

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.widget.EditText
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/** "Orte": Zu Hause / Arbeit festlegen; Mitteilungen werden dann nur an passenden Orten angezeigt. */
class PlacesActivity : Activity() {
    private lateinit var content: LinearLayout
    private var pendingKey: String? = null

    private val bg = Color.parseColor("#12161F")
    private val panel = Color.parseColor("#1B2130")
    private val line = Color.parseColor("#2A3245")
    private val accent = Color.parseColor("#5BB381")
    private val muted = Color.parseColor("#9AA3B2")

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun round(fill: Int, radius: Int, stroke: Int? = null) = GradientDrawable().apply {
        setColor(fill)
        cornerRadius = dp(radius).toFloat()
        if (stroke != null) setStroke(dp(1), stroke)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(bg)
            setOnApplyWindowInsetsListener { v, insets ->
                val b = insets.getInsets(WindowInsets.Type.systemBars())
                v.setPadding(b.left, b.top, b.right, b.bottom)
                WindowInsets.CONSUMED
            }
        }
        val head = LinearLayout(this).apply {
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(8), dp(12), dp(16), dp(8))
        }
        head.addView(ImageButton(this).apply {
            setImageResource(android.R.drawable.ic_media_previous)
            setColorFilter(Color.WHITE)
            background = null
            setOnClickListener { finish() }
        }, LinearLayout.LayoutParams(dp(48), dp(48)))
        val titles = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        titles.addView(text("Orte", 24f, Color.WHITE, true))
        titles.addView(text("Wo du Mitteilungen bekommen möchtest", 13f, muted))
        head.addView(titles, LinearLayout.LayoutParams(0, -2, 1f).apply { leftMargin = dp(4) })
        root.addView(head)

        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(8), dp(16), dp(24))
        }
        root.addView(ScrollView(this).apply { addView(content) }, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        render()
    }

    override fun onResume() {
        super.onResume()
        if (::content.isInitialized) render()
    }

    private fun text(t: String, size: Float, color: Int, bold: Boolean = false) = TextView(this).apply {
        text = t
        textSize = size
        setTextColor(color)
        if (bold) setTypeface(null, Typeface.BOLD)
    }

    private fun button(label: String, primary: Boolean, fn: () -> Unit) = TextView(this).apply {
        text = label
        textSize = 14f
        setTypeface(null, Typeface.BOLD)
        gravity = Gravity.CENTER
        setTextColor(if (primary) Color.parseColor("#0B1410") else Color.WHITE)
        background = round(if (primary) accent else Color.parseColor("#2A3245"), 10)
        setPadding(dp(14), dp(10), dp(14), dp(10))
        setOnClickListener { fn() }
    }

    private fun render() {
        content.removeAllViews()
        content.addView(text(
            "Mitteilungen werden nur angezeigt, wenn du auf Arbeit bist. Ist nur „Zu Hause“ festgelegt, " +
                "bekommst du sie überall außer zu Hause. Verpasste Mitteilungen werden nicht nachgeholt. " +
                "Dein Standort bleibt auf diesem Handy.", 13f, muted
        ), LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(12) })

        card("home", "Zu Hause", "Hier gibt es keine Mitteilungen.")
        card("work", "Arbeit", "Nur hier gibt es Mitteilungen.")

        if (Places.get(this, Places.HOME) != null || Places.get(this, Places.WORK) != null) {
            val ok = Places.hasForegroundPermission(this) && Places.hasBackgroundPermission(this)
            if (!ok) {
                content.addView(text(
                    "Damit die Regel im Hintergrund funktioniert, muss der Standort für ClubHUB auf " +
                        "„Immer zulassen“ stehen.", 13f, Color.parseColor("#F0B35A")
                ), LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
                content.addView(button("Berechtigung öffnen", true) { askBackground() },
                    LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(8) })
            } else {
                content.addView(text("✓ Standort-Berechtigung vollständig", 13f, accent),
                    LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
            }
        }
    }

    private fun card(key: String, title: String, sub: String) {
        val place = Places.get(this, key)
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(14))
            background = round(panel, 16, if (place != null) accent else line)
        }
        card.addView(text(title, 18f, Color.WHITE, true))
        card.addView(text(sub, 13f, muted))
        card.addView(text(
            if (place == null) "Nicht festgelegt"
            else "%.4f, %.4f  ·  Radius %d m".format(place.lat, place.lon, place.radius),
            14f, if (place == null) muted else accent
        ), LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })

        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(button(if (place == null) "Aktuellen Standort verwenden" else "Neu setzen", true) { capture(key) })
        if (place != null) {
            row.addView(button("Radius", false) { askRadius(key, place) },
                LinearLayout.LayoutParams(-2, -2).apply { leftMargin = dp(8) })
            row.addView(button("Entfernen", false) { Places.set(this, key, null); render() },
                LinearLayout.LayoutParams(-2, -2).apply { leftMargin = dp(8) })
        }
        card.addView(row, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(12) })
        content.addView(card, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(10) })
    }

    private fun capture(key: String) {
        if (!Places.hasForegroundPermission(this)) {
            pendingKey = key
            requestPermissions(
                arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION), 10
            )
            return
        }
        Toast.makeText(this, "Standort wird bestimmt …", Toast.LENGTH_SHORT).show()
        Thread {
            val loc = Places.currentLocation(this, 20_000)
            runOnUiThread {
                if (loc == null) {
                    Toast.makeText(this, "Kein Standort verfügbar - bitte Standort am Handy einschalten und im Freien/Fensternähe erneut versuchen", Toast.LENGTH_LONG).show()
                } else {
                    val old = Places.get(this, key)
                    Places.set(this, key, Place(loc.latitude, loc.longitude, old?.radius ?: Places.DEFAULT_RADIUS))
                    render()
                    if (!Places.hasBackgroundPermission(this)) askBackground()
                }
            }
        }.start()
    }

    private fun askRadius(key: String, place: Place) {
        val input = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_NUMBER
            setText(place.radius.toString())
        }
        AlertDialog.Builder(this)
            .setTitle("Radius in Metern")
            .setView(LinearLayout(this).apply {
                setPadding(dp(20), dp(8), dp(20), 0)
                addView(input, LinearLayout.LayoutParams(-1, -2))
            })
            .setPositiveButton("Speichern") { _, _ ->
                val r = input.text.toString().toIntOrNull()?.coerceIn(50, 5000) ?: place.radius
                Places.set(this, key, place.copy(radius = r))
                render()
            }
            .setNegativeButton("Abbrechen", null)
            .show()
    }

    /** Hintergrund-Standort: ab Android 11 nur ueber die App-Einstellungen ("Immer zulassen"). */
    private fun askBackground() {
        if (!Places.hasForegroundPermission(this)) { capture(Places.HOME); return }
        AlertDialog.Builder(this)
            .setTitle("Standort „Immer zulassen“")
            .setMessage(
                "Damit ClubHUB auch bei geschlossener App prüfen kann, wo du bist, öffne die " +
                    "App-Einstellungen → Berechtigungen → Standort und wähle „Immer zulassen“."
            )
            .setPositiveButton("Einstellungen") { _, _ ->
                startActivity(
                    Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName"))
                )
            }
            .setNegativeButton("Später", null)
            .show()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        if (requestCode == 10) {
            val key = pendingKey
            pendingKey = null
            if (key != null && Places.hasForegroundPermission(this)) capture(key)
        }
    }
}
