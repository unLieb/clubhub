package de.clubhub.app

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.widget.Toast
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** Update-Info vom Server (static/app/latest.json). */
data class UpdateInfo(val name: String, val code: Int, val apkUrl: String, val sha256: String?, val size: Long)

/**
 * In-App-Updater: laedt die APK vom Server, prueft die Pruefsumme und uebergibt sie dem
 * Android-Paketinstaller. Eine Bestaetigung am Handy bleibt (Android-Vorgabe); Android prueft
 * ausserdem selbst, dass die neue APK mit demselben Schluessel signiert ist.
 */
object Updater {
    private fun apkFile(ctx: Context) = File(ctx.cacheDir, "update.apk")

    /** Blockierend; null = kein Update (oder Server ohne App-Datei). */
    fun fetchInfo(server: Server): UpdateInfo? {
        val conn = URL("${server.url}/static/app/latest.json").openConnection() as HttpURLConnection
        conn.connectTimeout = 10_000
        conn.readTimeout = 10_000
        try {
            if (conn.responseCode != 200) return null
            val j = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
            val code = j.getInt("versionCode")
            if (code <= BuildConfig.VERSION_CODE) return null
            return UpdateInfo(
                j.getString("versionName"), code,
                server.url + j.optString("apk", "/static/app/clubhub.apk"),
                j.optString("sha256").ifEmpty { null }, j.optLong("size", -1)
            )
        } finally {
            conn.disconnect()
        }
    }

    /** Blockierend; meldet den Fortschritt in Prozent. Wirft bei Fehlern. */
    fun download(ctx: Context, info: UpdateInfo, onProgress: (Int) -> Unit): File {
        val file = apkFile(ctx)
        val conn = URL(info.apkUrl).openConnection() as HttpURLConnection
        conn.connectTimeout = 15_000
        conn.readTimeout = 30_000
        try {
            if (conn.responseCode != 200) throw RuntimeException("HTTP ${conn.responseCode}")
            val total = conn.contentLengthLong
            val md = MessageDigest.getInstance("SHA-256")
            var done = 0L
            conn.inputStream.use { input ->
                file.outputStream().use { out ->
                    val buf = ByteArray(32 * 1024)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        md.update(buf, 0, n)
                        done += n
                        if (total > 0) onProgress((done * 100 / total).toInt())
                    }
                }
            }
            val hex = md.digest().joinToString("") { "%02x".format(it) }
            if (info.sha256 != null && !hex.equals(info.sha256, ignoreCase = true)) {
                file.delete()
                throw RuntimeException("Prüfsumme stimmt nicht - Download unvollständig oder beschädigt")
            }
            return file
        } finally {
            conn.disconnect()
        }
    }

    /** Uebergibt die APK dem Paketinstaller; das Ergebnis meldet InstallReceiver. */
    fun install(ctx: Context, file: File) {
        val installer = ctx.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        if (Build.VERSION.SDK_INT >= 31) {
            // Ab Android 12 ohne extra Rueckfrage, wenn die App selbst Installer der Vorversion ist
            params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            file.inputStream().use { input ->
                session.openWrite("clubhub.apk", 0, file.length()).use { out ->
                    input.copyTo(out)
                    session.fsync(out)
                }
            }
            val pi = PendingIntent.getBroadcast(
                ctx, id, Intent(ctx, InstallReceiver::class.java).setPackage(ctx.packageName),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
            )
            session.commit(pi.intentSender)
        }
    }

    /** Raeumt eine liegengebliebene Update-Datei auf (z.B. nach erfolgreicher Installation). */
    fun cleanup(ctx: Context) {
        runCatching { apkFile(ctx).delete() }
    }
}

/** Empfaengt das Ergebnis der Installation: Bestaetigungsdialog durchreichen oder Fehler melden. */
class InstallReceiver : BroadcastReceiver() {
    @Suppress("DEPRECATION")
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, -1)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirm: Intent? = if (Build.VERSION.SDK_INT >= 33)
                    intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                else intent.getParcelableExtra(Intent.EXTRA_INTENT)
                confirm?.let {
                    it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    context.startActivity(it)
                }
            }
            PackageInstaller.STATUS_SUCCESS -> Updater.cleanup(context)
            else -> {
                val msg = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "unbekannter Fehler"
                Toast.makeText(context, "Update fehlgeschlagen: $msg", Toast.LENGTH_LONG).show()
            }
        }
    }
}
