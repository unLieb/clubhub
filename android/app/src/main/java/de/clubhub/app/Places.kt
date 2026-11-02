package de.clubhub.app

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
import android.os.HandlerThread
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

data class Place(val lat: Double, val lon: Double, val radius: Int)

/**
 * "Zu Hause" / "Arbeit": Regel fuer die Anzeige von Mitteilungen. Standortdaten bleiben auf dem
 * Geraet und werden nie an einen Server geschickt.
 *  - "Arbeit" gesetzt: Mitteilungen nur dort.
 *  - nur "Zu Hause" gesetzt: Mitteilungen ueberall ausser zu Hause.
 *  - nichts gesetzt, kein Standort verfuegbar oder keine Berechtigung: immer anzeigen.
 */
object Places {
    const val HOME = "home"
    const val WORK = "work"
    const val DEFAULT_RADIUS = 150

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("clubhub", Context.MODE_PRIVATE)

    fun get(ctx: Context, key: String): Place? {
        val p = prefs(ctx)
        if (!p.contains("place_${key}_lat")) return null
        return Place(
            Double.fromBits(p.getLong("place_${key}_lat", 0)),
            Double.fromBits(p.getLong("place_${key}_lon", 0)),
            p.getInt("place_${key}_r", DEFAULT_RADIUS)
        )
    }

    fun set(ctx: Context, key: String, place: Place?) {
        val e = prefs(ctx).edit()
        if (place == null) {
            e.remove("place_${key}_lat").remove("place_${key}_lon").remove("place_${key}_r")
        } else {
            e.putLong("place_${key}_lat", place.lat.toBits())
                .putLong("place_${key}_lon", place.lon.toBits())
                .putInt("place_${key}_r", place.radius)
        }
        e.apply()
    }

    fun hasForegroundPermission(ctx: Context) =
        ctx.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            ctx.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    fun hasBackgroundPermission(ctx: Context) =
        android.os.Build.VERSION.SDK_INT < 29 ||
            ctx.checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION) == PackageManager.PERMISSION_GRANTED

    /** Standorte ungenauer als das (z.B. grobe Funkzellen-Position im Ruhezustand) gelten als "unbekannt". */
    const val MAX_ACCURACY = 500f

    private fun ageMs(l: Location): Long =
        (android.os.SystemClock.elapsedRealtimeNanos() - l.elapsedRealtimeNanos) / 1_000_000

    private fun usable(l: Location) = l.accuracy <= MAX_ACCURACY

    /** Blockierend (nicht auf dem UI-Thread aufrufen): aktueller, genauer Standort oder null. */
    @Suppress("DEPRECATION", "MissingPermission")
    fun currentLocation(ctx: Context, timeoutMs: Long = 12_000): Location? {
        if (!hasForegroundPermission(ctx)) return null
        val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val providers = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
            .filter { runCatching { lm.isProviderEnabled(it) }.getOrDefault(false) }
        if (providers.isEmpty()) return null
        val known = providers.mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }.filter { usable(it) }

        // 1. Frische letzte Position (max. 3 Minuten alt, genau genug)
        known.filter { ageMs(it) < 180_000 }.minByOrNull { it.accuracy }?.let { saveFix(ctx, it); return it }

        // 2. Neue Messung - nur genaue Ergebnisse zaehlen, grobe werden ignoriert
        val latch = CountDownLatch(1)
        var result: Location? = null
        val thread = HandlerThread("clubhub-loc").apply { start() }
        val listener = object : LocationListener {
            override fun onLocationChanged(location: Location) {
                if (usable(location) && result == null) {
                    result = location
                    latch.countDown()
                }
            }
            @Deprecated("Deprecated in Java")
            override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
            override fun onProviderEnabled(provider: String) {}
            override fun onProviderDisabled(provider: String) {}
        }
        try {
            providers.forEach { runCatching { lm.requestSingleUpdate(it, listener, thread.looper) } }
            latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        } finally {
            runCatching { lm.removeUpdates(listener) }
            thread.quitSafely()
        }
        result?.let { saveFix(ctx, it); return it }

        // 3. Letzte bekannte genaue Position, bis 60 Minuten alt (z.B. von einer anderen App ermittelt)
        known.filter { ageMs(it) < 60 * 60_000L }.minByOrNull { it.accuracy }?.let { saveFix(ctx, it); return it }
        return null
    }

    /** Letzte gute Standortbestimmung merken: Rueckfall, wenn gerade keine neue gelingt (z.B. im Ruhezustand). */
    private fun saveFix(ctx: Context, l: Location) {
        prefs(ctx).edit()
            .putLong("fix_lat", l.latitude.toBits()).putLong("fix_lon", l.longitude.toBits())
            .putFloat("fix_acc", l.accuracy).putLong("fix_time", System.currentTimeMillis()).apply()
    }

    private fun lastFix(ctx: Context, maxAgeMs: Long): Location? {
        val p = prefs(ctx)
        val t = p.getLong("fix_time", 0)
        if (t == 0L || System.currentTimeMillis() - t > maxAgeMs) return null
        return Location("saved").apply {
            latitude = Double.fromBits(p.getLong("fix_lat", 0))
            longitude = Double.fromBits(p.getLong("fix_lon", 0))
            accuracy = p.getFloat("fix_acc", 100f)
        }
    }

    /** Genauer aktueller Standort, sonst der zuletzt bestimmte (max. 2 Stunden alt); null = gar keiner. */
    fun locate(ctx: Context): Pair<Location, String>? {
        currentLocation(ctx)?.let { return it to "aktuell" }
        val p = prefs(ctx)
        lastFix(ctx, 2 * 3600_000L)?.let {
            val min = (System.currentTimeMillis() - p.getLong("fix_time", 0)) / 60_000
            return it to "gespeichert, vor $min Min."
        }
        return null
    }

    fun distance(a: Location, p: Place): Float {
        val out = FloatArray(1)
        Location.distanceBetween(a.latitude, a.longitude, p.lat, p.lon, out)
        return out[0]
    }

    /**
     * Ist gerade Arbeitszeit? Quelle: die Arbeitszeiten der Gruppen des Nutzers (vom Server mitgeliefert,
     * Stunden). Ausserhalb wird nicht nach dem Standort gefragt (Akku). Ist irgendwo keine Arbeitszeit
     * hinterlegt, gilt das als "immer"; ohne bekannte Daten greift 06-20 Uhr.
     */
    fun inWindow(ctx: Context): Boolean {
        val hour = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY)
        var known = false
        for (s in ServerStore.load(ctx)) {
            val raw = ServerStore.workHours(ctx, s.id) ?: continue
            known = true
            val arr = try { org.json.JSONArray(raw) } catch (e: Exception) { continue }
            if (arr.length() == 0) return true // keine Einschraenkung
            for (i in 0 until arr.length()) {
                val w = arr.getJSONArray(i)
                if (hour >= w.getInt(0) && hour < w.getInt(1)) return true
            }
        }
        return if (known) false else hour in 6 until 20
    }

    enum class Where { WORK, HOME, ELSEWHERE }

    /** Wo ist der Standort? Beide Orte werden geprueft; liegt er an beiden, gewinnt der naehere. Zu Hause hat ein Veto. */
    fun whereIs(ctx: Context, loc: Location): Where {
        val work = get(ctx, WORK)
        val home = get(ctx, HOME)
        val acc = loc.accuracy.coerceAtMost(MAX_ACCURACY)
        val dW = work?.let { distance(loc, it) }
        val dH = home?.let { distance(loc, it) }
        val inW = work != null && dW!! <= work.radius + acc / 2
        val inH = home != null && dH!! <= home.radius + acc / 2
        return when {
            inW && inH -> if (dW!! <= dH!!) Where.WORK else Where.HOME
            inW -> Where.WORK
            inH -> Where.HOME
            else -> Where.ELSEWHERE
        }
    }

    fun atWork(ctx: Context, loc: Location) = whereIs(ctx, loc) == Where.WORK

    enum class Decision { SHOW, HOLD, DROP }

    /**
     * Blockierend (nicht auf dem UI-Thread). Streng: Mitteilungen erscheinen nur, wenn der Standort sicher passt.
     *  - nichts festgelegt: anzeigen
     *  - "Arbeit" gesetzt: nur auf Arbeit anzeigen; sonst (auch ohne genauen Standort) zurueckhalten und beim Ankommen
     *    gebuendelt zeigen; ausserhalb der Arbeitszeit wird gar nicht erst nach dem Standort gefragt
     *  - nur "Zu Hause": zu Hause verwerfen, sonst anzeigen
     * Standorte ungenauer als MAX_ACCURACY zaehlen als unbekannt.
     */
    fun decide(ctx: Context): Decision {
        val (d, why) = evaluate(ctx)
        log(ctx, why)
        return d
    }

    private fun log(ctx: Context, text: String) {
        val p = prefs(ctx)
        val t = java.text.SimpleDateFormat("dd.MM. HH:mm", java.util.Locale.GERMANY).format(java.util.Date())
        val lines = (p.getString("decision_log", "") ?: "").split("\n").filter { it.isNotBlank() }.takeLast(11) + "$t \u2013 $text"
        p.edit().putString("decision_log", lines.joinToString("\n")).apply()
    }

    /** Vorgang ins Orte-Protokoll schreiben (z.B. Ankunft auf Arbeit). */
    fun note(ctx: Context, text: String) = log(ctx, text)

    /** Die letzten Entscheidungen, neueste zuletzt (fuer Orte-Seite und Diagnosebericht). */
    fun decisionLog(ctx: Context): List<String> =
        (prefs(ctx).getString("decision_log", "") ?: "").split("\n").filter { it.isNotBlank() }

    fun lastDecision(ctx: Context): String? = decisionLog(ctx).lastOrNull()

    /** Die Entscheidung samt Begruendung (ohne sie zu speichern), auch fuer "Jetzt pruefen". */
    fun evaluate(ctx: Context): Pair<Decision, String> {
        val home = get(ctx, HOME)
        val work = get(ctx, WORK)
        if (home == null && work == null) return Decision.SHOW to "keine Orte festgelegt \u2013 angezeigt"
        if (work != null && !inWindow(ctx)) return Decision.HOLD to "au\u00dferhalb der Arbeitszeit \u2013 zur\u00fcckgehalten"
        val located = locate(ctx)
        if (located == null) {
            return if (work != null) Decision.HOLD to "kein genauer Standort \u2013 zur\u00fcckgehalten"
            else Decision.SHOW to "kein genauer Standort \u2013 angezeigt"
        }
        val (loc, src) = located
        val info = listOfNotNull(
            work?.let { "Arbeit ${distance(loc, it).toInt()} m" },
            home?.let { "Zuhause ${distance(loc, it).toInt()} m" }
        ).joinToString(", ") + ", \u00b1${loc.accuracy.toInt()} m, $src"
        val w = whereIs(ctx, loc)
        return if (work != null) {
            if (w == Where.WORK) Decision.SHOW to "auf Arbeit ($info) \u2013 angezeigt"
            else Decision.HOLD to (if (w == Where.HOME) "zu Hause" else "nicht auf Arbeit") + " ($info) \u2013 zur\u00fcckgehalten"
        } else {
            if (w == Where.HOME) Decision.DROP to "zu Hause ($info) \u2013 verworfen"
            else Decision.SHOW to "nicht zu Hause ($info) \u2013 angezeigt"
        }
    }
}
