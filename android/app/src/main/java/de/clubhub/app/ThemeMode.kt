package de.clubhub.app

import android.app.Activity
import android.content.Context
import android.content.res.Configuration

/**
 * Hell/Dunkel der nativen Bildschirme folgt der Design-Einstellung der Webseite (Hell/Dunkel/Automatisch),
 * nicht nur dem Android-System. "Automatisch" = Systemeinstellung.
 */
object ThemeMode {
    private fun prefs(ctx: Context) = ctx.getSharedPreferences("clubhub", Context.MODE_PRIVATE)

    fun mode(ctx: Context): String = prefs(ctx).getString("theme_mode", "auto") ?: "auto"

    fun setMode(ctx: Context, mode: String) {
        prefs(ctx).edit().putString("theme_mode", if (mode == "light" || mode == "dark") mode else "auto").apply()
    }

    fun isDark(ctx: Context): Boolean = when (mode(ctx)) {
        "dark" -> true
        "light" -> false
        else -> (ctx.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
    }

    /** Vor super.onCreate() jeder Activity aufrufen. */
    fun apply(a: Activity) {
        a.setTheme(if (isDark(a)) R.style.AppThemeDark else R.style.AppThemeLight)
    }
}
