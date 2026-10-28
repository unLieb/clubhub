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
        const val ACTION_TEST = "de.clubhub.app.TEST_PLACES"
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
        Thread { arrivalLoop(gen) }.apply { isDaemon = true }.start()
        if (intent?.action == ACTION_TEST) {
            // Orte-Test aus dem Hintergrund: nach 20 s entscheiden und das Ergebnis als Mitteilung zeigen
            Thread {
                Thread.sleep(20_000)
                val (_, why) = Places.evaluate(this)
                Places.decide(this)
                val n = Notification.Builder(this, CH_SERVICE)
                    .setSmallIcon(android.R.drawable.stat_notify_chat)
                    .setContentTitle("Orte-Test (Hintergrund)")
                    .setContentText(why)
                    .setStyle(Notification.BigTextStyle().bigText(why))
                    .setAutoCancel(true)
                    .build()
                nm.notify(0x7E57, n)
            }.start()
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
                conn.setRequestProperty("User-Agent", AppInfo.userAgent())
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
                if (msgs.length() > 0) {
                    val list = (0 until msgs.length()).map {
                        val m = msgs.getJSONObject(it)
                        Incoming(m.getInt("id"), m.getString("title"), m.getString("message"), m.optString("url", "/"))
                    }
                    handleBatch(server, list)
                }
                AppInfo.markPoll(this)
                json.optJSONArray("work_hours")?.let { ServerStore.setWorkHours(this, server.id, it.toString()) }
                Snapshot.refresh(this, server) // haelt die Offline-Ansicht auch ohne geoeffnete App frisch (max. alle 15 Min.)
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

    private class Incoming(val id: Int, val title: String, val message: String, val path: String)
    private class Burst(var count: Int, val lines: MutableList<String>, var last: Long)

    private val recent = HashMap<String, java.util.ArrayDeque<Long>>()
    private val bursts = HashMap<String, Burst>()

    /** Eine Entscheidung (Orte-Regel) je Abruf statt je Mitteilung - spart Standortabfragen und Zeit. */
    private fun handleBatch(server: Server, list: List<Incoming>) {
        when (Places.decide(this)) {
            Places.Decision.DROP -> return
            Places.Decision.HOLD -> list.forEach {
                Held.add(this, HeldMessage(server.id, it.title, it.message, it.path, System.currentTimeMillis()))
            }
            Places.Decision.SHOW -> {
                flushHeld() // evtl. Zurueckgehaltenes zuerst als Sammelmitteilung
                list.forEach { deliver(server, it) }
            }
        }
    }

    /**
     * Flutschutz: kommen mehr als 4 Mitteilungen binnen 2 Minuten (z.B. morgens, wenn viele Aufgaben auf
     * einmal faellig werden), werden die weiteren zu EINER leise aktualisierten Sammelmitteilung zusammengefasst.
     */
    private fun deliver(server: Server, m: Incoming) {
        val now = System.currentTimeMillis()
        synchronized(lock) {
            val q = recent.getOrPut(server.id) { java.util.ArrayDeque() }
            while (q.isNotEmpty() && now - q.first() > 120_000) q.removeFirst()
            var b = bursts[server.id]
            if (b != null && now - b.last >= 10 * 60_000L) { bursts.remove(server.id); b = null }
            if (b == null && q.size < 4) {
                q.addLast(now)
                post(server, m.id, m.title, m.message, m.path)
                return
            }
            if (b == null) { b = Burst(0, mutableListOf(), now); bursts[server.id] = b }
            b.count++
            b.last = now
            b.lines.add(m.title + ": " + m.message.lineSequence().first())
            postBurst(server, b)
        }
    }

    private fun postBurst(server: Server, b: Burst) {
        val color = colorFor(server)
        val open = Intent(this, MainActivity::class.java).apply {
            action = Intent.ACTION_VIEW
            data = Uri.parse(server.url + "/")
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pi = PendingIntent.getActivity(
            this, server.id.hashCode() xor 0xB00, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val style = Notification.InboxStyle()
        b.lines.takeLast(7).forEach { style.addLine(it) }
        if (b.count > 7) style.setSummaryText("+${b.count - 7} weitere")
        val n = Notification.Builder(this, channelId(server))
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setColor(color)
            .setLargeIcon(avatar(server.name, color))
            .setContentTitle("${server.name} \u00b7 ${b.count} weitere Mitteilungen")
            .setContentText(b.lines.last())
            .setStyle(style)
            .setSubText(server.name)
            .setGroup("srv_" + server.id)
            .setOnlyAlertOnce(true)
            .setContentIntent(pi)
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java).notify(server.id.hashCode() xor 0xB00, n)
    }

    /** Solange etwas zurueckgehalten wird und Arbeitszeit ist: alle 10 Min. pruefen, ob man auf Arbeit angekommen ist. */
    private fun arrivalLoop(gen: Int) {
        while (alive(gen)) {
            sleep(gen, 5 * 60_000L)
            if (!alive(gen)) return
            try {
                if (Held.count(this) > 0 && Places.get(this, Places.WORK) != null && Places.inWindow(this)) {
                    val loc = Places.currentLocation(this)
                    if (loc != null && Places.atWork(this, loc)) flushHeld()
                    // Notausgang: laesst sich gar kein Standort bestimmen, nach 30 Min. trotzdem gesammelt zeigen
                    else if (loc == null && System.currentTimeMillis() - (Held.all(this).minOfOrNull { it.time } ?: Long.MAX_VALUE) >= 30 * 60_000L) flushHeld()
                }
            } catch (e: Exception) {
                // naechster Durchlauf
            }
        }
    }

    /** Zeigt zurueckgehaltene Mitteilungen je Server als eine Sammelmitteilung und leert die Liste. */
    private fun flushHeld() {
        val items = Held.all(this)
        if (items.isEmpty()) return
        Held.clear(this)
        val servers = ServerStore.load(this).associateBy { it.id }
        items.groupBy { it.serverId }.forEach { (sid, list) ->
            val server = servers[sid] ?: return@forEach
            if (list.size == 1) {
                val m = list[0]
                post(server, (m.time and 0x7FFFFFFF).toInt(), m.title, m.message, m.path)
                return@forEach
            }
            val color = colorFor(server)
            val open = Intent(this, MainActivity::class.java).apply {
                action = Intent.ACTION_VIEW
                data = Uri.parse(server.url + "/")
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val pi = PendingIntent.getActivity(
                this, server.id.hashCode() xor 0x5EED, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            val style = Notification.InboxStyle()
            list.takeLast(7).forEach { style.addLine(it.title + ": " + it.message.lineSequence().first()) }
            if (list.size > 7) style.setSummaryText("+${list.size - 7} weitere")
            val n = Notification.Builder(this, channelId(server))
                .setSmallIcon(android.R.drawable.stat_notify_chat)
                .setColor(color)
                .setLargeIcon(avatar(server.name, color))
                .setContentTitle("${server.name} \u00b7 ${list.size} Mitteilungen verpasst")
                .setContentText(list.last().title)
                .setStyle(style)
                .setSubText(server.name)
                .setGroup("srv_" + server.id)
                .setContentIntent(pi)
                .setAutoCancel(true)
                .build()
            getSystemService(NotificationManager::class.java).notify(server.id.hashCode() xor 0x5EED, n)
        }
    }

    private fun post(server: Server, id: Int, title: String, message: String, path: String) {
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
