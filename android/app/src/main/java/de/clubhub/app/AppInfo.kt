package de.clubhub.app

import android.app.ActivityManager
import android.app.NotificationManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.PowerManager
import android.webkit.WebView
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Geraete-/App-Informationen fuer Fehlersuche: User-Agent der App-Anfragen und der "Diagnose senden"-Bericht. */
object AppInfo {
    /** "ClubHUBApp/1.6.1 (Android 16; Xiaomi 2407FPN8EG)" - steht in Anfragen des Hintergrunddienstes. */
    fun userAgent(): String =
        "ClubHUBApp/${BuildConfig.VERSION_NAME} (Android ${Build.VERSION.RELEASE}; ${Build.MANUFACTURER} ${Build.MODEL})"

    fun markPoll(ctx: Context) {
        ctx.getSharedPreferences("clubhub", Context.MODE_PRIVATE).edit().putLong("last_poll", System.currentTimeMillis()).apply()
    }

    private fun yn(b: Boolean) = if (b) "ja" else "nein"

    private fun ago(ms: Long): String {
        if (ms <= 0) return "nie"
        val min = (System.currentTimeMillis() - ms) / 60_000
        return if (min < 120) "vor $min Min." else "vor ${min / 60} Std."
    }

    /** Kompakter Bericht (<= ca. 1300 Zeichen): alles, was bei "App tut X nicht" typischerweise relevant ist. */
    fun diagnostics(ctx: Context): String {
        val sb = StringBuilder()
        fun line(k: String, v: String) { sb.append(k).append(": ").append(v).append("\n") }
        val prefs = ctx.getSharedPreferences("clubhub", Context.MODE_PRIVATE)
        try {
            line("App", "${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})")
            line("Geraet", "${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})")
            line("WebView", WebView.getCurrentWebViewPackage()?.versionName ?: "?")
            val am = ctx.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
            val mi = ActivityManager.MemoryInfo().also { am.getMemoryInfo(it) }
            line("Speicher", "frei ${mi.availMem / 1048576} von ${mi.totalMem / 1048576} MB, knapp: ${yn(mi.lowMemory)}")
            val pm = ctx.getSystemService(Context.POWER_SERVICE) as PowerManager
            line("Akku-Optimierung", if (pm.isIgnoringBatteryOptimizations(ctx.packageName)) "ausgenommen (gut)" else "aktiv (App kann beendet werden)")
            line("Hintergrund eingeschraenkt", yn(am.isBackgroundRestricted))
            runCatching {
                val b = (ctx.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager).appStandbyBucket
                line("Standby-Bucket", b.toString() + " (10 aktiv, 20 oft, 30 selten, 40 eingeschraenkt)")
            }
            val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            line("Mitteilungen erlaubt", yn(nm.areNotificationsEnabled()))
            fun granted(p: String) = ctx.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED
            line("Kamera", yn(granted(android.Manifest.permission.CAMERA)))
            line("Standort", "genau ${yn(granted(android.Manifest.permission.ACCESS_FINE_LOCATION))}, immer ${yn(Places.hasBackgroundPermission(ctx))}")
            val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val caps = cm.getNetworkCapabilities(cm.activeNetwork)
            line("Netz", when {
                caps == null -> "keins"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "WLAN"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "Mobilfunk"
                else -> "sonstiges"
            } + if (caps?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true) " + VPN" else "")
            line("Dienst letzter Abruf", ago(prefs.getLong("last_poll", 0)))
            line("Server", ServerStore.load(ctx).joinToString(", ") { s ->
                val snap = Snapshot.savedAt(ctx, s.id)
                s.name + (if (snap > 0) " (Stand ${SimpleDateFormat("dd.MM. HH:mm", Locale.GERMANY).format(Date(snap))})" else "")
            })
            line("Orte", "Zuhause ${yn(Places.get(ctx, Places.HOME) != null)}, Arbeit ${yn(Places.get(ctx, Places.WORK) != null)}")
            Places.lastDecision(ctx)?.let { line("Letzte Orte-Entscheidung", it) }
            line("Design", ThemeMode.mode(ctx))
        } catch (e: Exception) {
            line("Fehler beim Sammeln", e.toString())
        }
        return sb.toString().trim()
    }
}
