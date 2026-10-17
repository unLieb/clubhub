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

    /** Blockierend (nicht auf dem UI-Thread aufrufen): aktueller Standort oder null. */
    @Suppress("DEPRECATION", "MissingPermission")
    fun currentLocation(ctx: Context, timeoutMs: Long = 12_000): Location? {
        if (!hasForegroundPermission(ctx)) return null
        val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val providers = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
            .filter { runCatching { lm.isProviderEnabled(it) }.getOrDefault(false) }
        if (providers.isEmpty()) return null

        // Frische letzte Position (max. 3 Minuten alt) reicht
        val fresh = providers.mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }
            .filter { System.currentTimeMillis() - it.time < 180_000 }
            .minByOrNull { it.accuracy }
        if (fresh != null) return fresh

        val latch = CountDownLatch(1)
        var result: Location? = null
        val thread = HandlerThread("clubhub-loc").apply { start() }
        val listener = object : LocationListener {
            override fun onLocationChanged(location: Location) {
                if (result == null) result = location
                latch.countDown()
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
        return result
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

    /** Ist der Standort auf Arbeit? (grosszuegig: Genauigkeit wird zugunsten von "ja" gewertet) */
    fun atWork(ctx: Context, loc: Location): Boolean {
        val work = get(ctx, WORK) ?: return false
        return distance(loc, work) - loc.accuracy / 2f <= work.radius
    }

    enum class Decision { SHOW, HOLD, DROP }

    /**
     * Blockierend (nicht auf dem UI-Thread). Im Zweifel (kein Standort, keine Berechtigung) wird angezeigt -
     * lieber eine Mitteilung zu viel als eine verpasste.
     *  - nichts festgelegt: anzeigen
     *  - "Arbeit" gesetzt: auf Arbeit anzeigen, sonst zurueckhalten und beim Ankommen gebuendelt zeigen;
     *    ausserhalb der Arbeitszeit wird gar nicht erst nach dem Standort gefragt
     *  - nur "Zu Hause": zu Hause verwerfen, sonst anzeigen
     */
    fun decide(ctx: Context): Decision {
        val home = get(ctx, HOME)
        val work = get(ctx, WORK)
        if (home == null && work == null) return Decision.SHOW
        if (work != null) {
            if (!inWindow(ctx)) return Decision.HOLD
            val loc = currentLocation(ctx) ?: return Decision.SHOW
            return if (atWork(ctx, loc)) Decision.SHOW else Decision.HOLD
        }
        val loc = currentLocation(ctx) ?: return Decision.SHOW
        return if (distance(loc, home!!) + loc.accuracy / 2f > home.radius) Decision.SHOW else Decision.DROP
    }
}
