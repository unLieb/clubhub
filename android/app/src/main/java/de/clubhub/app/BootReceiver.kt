package de.clubhub.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Startet den Benachrichtigungsdienst nach Neustart des Handys bzw. nach einem App-Update. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        NotificationService.start(context)
    }
}
