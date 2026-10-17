package de.clubhub.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.IBinder
import android.webkit.CookieManager
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Dauerhafter Vordergrunddienst: haelt je hinterlegtem Server eine Long-Polling-Verbindung
 * zu /api/app/inbox offen und zeigt neue Nachrichten als System-Benachrichtigung.
 * Anmeldung = Session-Cookie des WebViews (kein zweites Login noetig).
 */
class NotificationService : Service() {
    private var generation = 0
    private val lock = Any()

    companion object {
        private const val CH_SERVICE = "service"
        private const val CH_MESSAGES = "messages"
        private const val ID_SERVICE = 1
        private val PALETTE = intArrayOf(
            0xFF2E7D5B.toInt(), 0xFF1E6FD9.toInt(), 0xFFD9822B.toInt(),
            0xFF8E44AD.toInt(), 0xFFC0392B.toInt(), 0xFF16A0A8.toInt()
        )

        private fun channelId(server: Server) = "srv_" + server.id
        private fun colorFor(server: Server) = PALETTE[Math.floorMod(server.id.hashCode(), PALETTE.size)]

        fun start(ctx: Context) {
            if (!ServerStore.notificationsEnabled(ctx) || ServerStore.load(ctx).isEmpty()) return
            try {
                ctx.startForegroundService(Intent(ctx, NotificationService::class.java))
            } catch (e: Exception) {
                // z.B. Start aus dem Hintergrund nicht erlaubt - naechster App-Start holt es nach
            }
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, NotificationService::class.java))
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH_SERVICE, "Verbindung", NotificationManager.IMPORTANCE_MIN)
        )
        nm.deleteNotificationChannel(CH_MESSAGES)
        val all = ServerStore.load(this)
        // Eigener Kanal je Server: in den Android-Einstellungen mit Servernamen sichtbar und einzeln einstellbar
        all.forEach {
            nm.createNotificationChannel(
                NotificationChannel(channelId(it), "Mitteilungen: ${it.name}", NotificationManager.IMPORTANCE_HIGH)
            )
        }
        val n = Notification.Builder(this, CH_SERVICE)
            .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
            .setContentTitle("ClubHUB")
            .setContentText("Empfang aktiv: " + all.joinToString(", ") { it.name })
            .setOngoing(true)
            .build()
        startForeground(ID_SERVICE, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)

        // Alle bestehenden Poll-Threads beenden und fuer die aktuelle Serverliste neu starten
        val gen = synchronized(lock) { ++generation }
        ServerStore.load(this).forEach { server ->
            Thread { pollLoop(server, gen) }.apply { isDaemon = true }.start()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        synchronized(lock) { generation++ }
        super.onDestroy()
    }

    private fun alive(gen: Int) = synchronized(lock) { gen == generation }

    private fun pollLoop(server: Server, gen: Int) {
        val device = ServerStore.deviceId(this)
        var after = ServerStore.lastInboxId(this, server.id)
        var backoff = 10_000L
        while (alive(gen)) {
            try {
                val cookie = CookieManager.getInstance().getCookie(server.url)
                if (cookie.isNullOrEmpty()) { // noch nicht angemeldet
                    sleep(gen, 60_000)
                    continue
                }
                val conn = URL("${server.url}/api/app/inbox?device=$device&after=$after&wait=25")
                    .openConnection() as HttpURLConnection
                conn.connectTimeout = 15_000
                conn.readTimeout = 40_000
                conn.setRequestProperty("Cookie", cookie)
                conn.setRequestProperty("User-Agent", "ClubHUBApp")
                val code = conn.responseCode
                if (code == 401) { // Sitzung abgelaufen / abgemeldet
                    conn.disconnect()
                    sleep(gen, 60_000)
                    continue
                }
                if (code != 200) {
                    conn.disconnect()
                    throw RuntimeException("HTTP $code")
                }
                // Der Server verlaengert die Sitzung bei jeder Antwort (Set-Cookie) - ins WebView
                // uebernehmen, damit man auch ohne App-Nutzung angemeldet bleibt.
                conn.headerFields["Set-Cookie"]?.forEach { CookieManager.getInstance().setCookie(server.url, it) }
                val body = conn.inputStream.bufferedReader().use { it.readText() }
                conn.disconnect()
                val json = JSONObject(body)
                val msgs = json.getJSONArray("messages")
                for (i in 0 until msgs.length()) {
                    val m = msgs.getJSONObject(i)
                    show(server, m.getInt("id"), m.getString("title"), m.getString("message"), m.optString("url", "/"))
                }
                after = json.getInt("last_id")
                ServerStore.setLastInboxId(this, server.id, after)
                backoff = 10_000L
            } catch (e: Exception) {
                sleep(gen, backoff)
                backoff = minOf(backoff * 2, 300_000L)
            }
        }
    }

    private fun sleep(gen: Int, ms: Long) {
        var left = ms
        while (left > 0 && alive(gen)) {
            Thread.sleep(minOf(left, 1000))
            left -= 1000
        }
    }

    private fun show(server: Server, id: Int, title: String, message: String, path: String) {
        // Orte-Regel (Zu Hause / Arbeit): verpasste Mitteilungen werden verworfen
        if (!Places.shouldNotify(this)) return
        val target = Uri.parse(server.url + (if (path.startsWith("/")) path else "/$path"))
        val open = Intent(this, MainActivity::class.java).apply {
            action = Intent.ACTION_VIEW
            data = target
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pi = PendingIntent.getActivity(
            this, (server.id.hashCode() xor id), open,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val color = colorFor(server)
        val n = Notification.Builder(this, channelId(server))
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setColor(color)
            .setLargeIcon(avatar(server.name, color))
            .setContentTitle("${server.name} · $title")
            .setContentText(message)
            .setStyle(Notification.BigTextStyle().bigText(message))
            .setSubText(server.name)
            .setGroup("srv_" + server.id)
            .setContentIntent(pi)
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java).notify(server.id.hashCode() xor id, n)
    }

    /** Runder Buchstaben-Avatar in der Farbe des Servers (grosses Symbol der Mitteilung). */
    private fun avatar(name: String, color: Int): android.graphics.Bitmap {
        val size = 128
        val bmp = android.graphics.Bitmap.createBitmap(size, size, android.graphics.Bitmap.Config.ARGB_8888)
        val c = android.graphics.Canvas(bmp)
        val p = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG)
        p.color = color
        c.drawCircle(size / 2f, size / 2f, size / 2f, p)
        p.color = android.graphics.Color.WHITE
        p.textSize = size * 0.5f
        p.typeface = android.graphics.Typeface.DEFAULT_BOLD
        p.textAlign = android.graphics.Paint.Align.CENTER
        val letter = name.trim().firstOrNull()?.uppercase() ?: "?"
        c.drawText(letter, size / 2f, size / 2f - (p.ascent() + p.descent()) / 2f, p)
        return bmp
    }
}
