package de.clubhub.app

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import com.google.android.material.dialog.MaterialAlertDialogBuilder

/** "Orte" (Material 3): Zu Hause / Arbeit festlegen; Mitteilungen werden dann nur an passenden Orten angezeigt. */
class PlacesActivity : Activity() {
    private lateinit var content: LinearLayout
    private var pendingKey: String? = null

    private fun dp(v: Int) = Ui.dp(this, v)

    override fun onCreate(savedInstanceState: Bundle?) {
        ThemeMode.apply(this)
        super.onCreate(savedInstanceState)
        val root = Ui.root(this)
        root.addView(Ui.toolbar(this, "Orte", "Wo du Mitteilungen bekommen möchtest"))
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

    private fun render() {
        content.removeAllViews()
        val variant = Ui.color(this, Ui.onSurfaceVariant)
        content.addView(
            text(
                "Mitteilungen werden nur angezeigt, wenn du auf Arbeit bist. Ist nur „Zu Hause“ festgelegt, " +
                    "bekommst du sie überall außer zu Hause. Verpasste Mitteilungen werden nicht nachgeholt. " +
                    "Dein Standort bleibt auf diesem Handy.", 13f, variant
            ), Ui.margins(content, b = 12)
        )

        card("home", "Zu Hause", "Hier gibt es keine Mitteilungen.")
        card("work", "Arbeit", "Nur hier gibt es Mitteilungen.")

        if (Places.get(this, Places.HOME) != null || Places.get(this, Places.WORK) != null) {
            if (!Places.hasForegroundPermission(this) || !Places.hasBackgroundPermission(this)) {
                content.addView(
                    text(
                        "Damit die Regel im Hintergrund funktioniert, muss der Standort für ClubHUB auf " +
                            "„Immer zulassen“ stehen.", 13f, Ui.color(this, Ui.onSurface)
                    ), Ui.margins(content, t = 8)
                )
                content.addView(
                    Ui.filledButton(this, "Berechtigung öffnen") { askBackground() },
                    LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(8) }
                )
            } else {
                content.addView(
                    text("✓ Standort-Berechtigung vollständig", 13f, Ui.color(this, Ui.primary)),
                    Ui.margins(content, t = 8)
                )
            }
        }
    }

    private fun card(key: String, title: String, sub: String) {
        val place = Places.get(this, key)
        val inner = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(14))
        }
        inner.addView(text(title, 18f, Ui.color(this, Ui.onSurface), true))
        inner.addView(text(sub, 13f, Ui.color(this, Ui.onSurfaceVariant)))
        inner.addView(
            text(
                if (place == null) "Nicht festgelegt"
                else "%.4f, %.4f  ·  Radius %d m".format(place.lat, place.lon, place.radius),
                14f, Ui.color(this, if (place == null) Ui.onSurfaceVariant else Ui.primary)
            ), Ui.margins(inner, t = 8)
        )

        val row = LinearLayout(this)
        row.addView(Ui.filledButton(this, if (place == null) "Standort verwenden" else "Neu setzen") { capture(key) })
        if (place != null) {
            row.addView(Ui.tonalButton(this, "Radius") { askRadius(key, place) },
                LinearLayout.LayoutParams(-2, -2).apply { leftMargin = dp(8) })
            row.addView(Ui.tonalButton(this, "Entfernen") { Places.set(this, key, null); render() },
                LinearLayout.LayoutParams(-2, -2).apply { leftMargin = dp(8) })
        }
        inner.addView(row, Ui.margins(inner, t = 12))

        val card = Ui.card(this, place != null).apply { addView(inner) }
        content.addView(card, Ui.margins(content, b = 10))
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
                    Toast.makeText(
                        this,
                        "Kein Standort verfügbar - bitte Standort am Handy einschalten und erneut versuchen",
                        Toast.LENGTH_LONG
                    ).show()
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
        val box = LinearLayout(this).apply { setPadding(dp(24), dp(8), dp(24), 0) }
        val (layout, edit) = Ui.input(this, "Radius in Metern (50–5000)", place.radius.toString(), InputType.TYPE_CLASS_NUMBER)
        box.addView(layout, LinearLayout.LayoutParams(-1, -2))
        MaterialAlertDialogBuilder(this)
            .setTitle("Radius")
            .setView(box)
            .setPositiveButton("Speichern") { _, _ ->
                val r = edit.text.toString().toIntOrNull()?.coerceIn(50, 5000) ?: place.radius
                Places.set(this, key, place.copy(radius = r))
                render()
            }
            .setNegativeButton("Abbrechen", null)
            .show()
    }

    /** Hintergrund-Standort: ab Android 11 nur ueber die App-Einstellungen ("Immer zulassen"). */
    private fun askBackground() {
        if (!Places.hasForegroundPermission(this)) { capture(Places.HOME); return }
        MaterialAlertDialogBuilder(this)
            .setTitle("Standort „Immer zulassen“")
            .setMessage(
                "Damit ClubHUB auch bei geschlossener App prüfen kann, wo du bist, öffne die " +
                    "App-Einstellungen → Berechtigungen → Standort und wähle „Immer zulassen“."
            )
            .setPositiveButton("Einstellungen") { _, _ ->
                startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
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
