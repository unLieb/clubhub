package de.clubhub.app

import android.app.Activity
import android.content.Context
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.util.TypedValue
import android.view.View
import android.view.WindowInsets
import android.widget.LinearLayout
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.color.MaterialColors
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.google.android.material.R as M3

/** Kleine Helfer fuer den programmatischen Material-3-Aufbau (Farben immer aus dem Theme, Hell/Dunkel automatisch). */
object Ui {
    fun dp(ctx: Context, v: Int) = (v * ctx.resources.displayMetrics.density).toInt()

    fun color(ctx: Context, attr: Int): Int = MaterialColors.getColor(ctx, attr, Color.MAGENTA)

    val surface get() = M3.attr.colorSurface
    val onSurface get() = M3.attr.colorOnSurface
    val onSurfaceVariant get() = M3.attr.colorOnSurfaceVariant
    val container get() = M3.attr.colorSurfaceContainer
    val primary get() = androidx.appcompat.R.attr.colorPrimary
    val onPrimary get() = M3.attr.colorOnPrimary
    val primaryContainer get() = M3.attr.colorPrimaryContainer
    val onPrimaryContainer get() = M3.attr.colorOnPrimaryContainer
    val outlineVariant get() = M3.attr.colorOutlineVariant

    /** Wurzel-Layout mit Platz fuer System-Leisten (Edge-to-Edge) und Theme-Hintergrund. */
    fun root(a: Activity): LinearLayout = LinearLayout(a).apply {
        orientation = LinearLayout.VERTICAL
        setBackgroundColor(color(a, surface))
        setOnApplyWindowInsetsListener { v, insets ->
            val b = insets.getInsets(WindowInsets.Type.systemBars())
            v.setPadding(b.left, b.top, b.right, b.bottom)
            WindowInsets.CONSUMED
        }
    }

    fun toolbar(a: Activity, title: String, subtitle: String?): MaterialToolbar = MaterialToolbar(a).apply {
        this.title = title
        this.subtitle = subtitle
        setNavigationIcon(R.drawable.ic_arrow_back)
        setNavigationContentDescription("Zurück")
        setNavigationOnClickListener { a.finish() }
        setBackgroundColor(color(a, surface))
        setTitleTextColor(color(a, onSurface))
        setSubtitleTextColor(color(a, onSurfaceVariant))
    }

    fun card(ctx: Context, highlighted: Boolean): MaterialCardView = MaterialCardView(ctx).apply {
        radius = dp(ctx, 16).toFloat()
        cardElevation = 0f
        setCardBackgroundColor(color(ctx, container))
        strokeColor = color(ctx, if (highlighted) primary else outlineVariant)
        strokeWidth = dp(ctx, if (highlighted) 2 else 1)
    }

    fun filledButton(ctx: Context, label: String, fn: () -> Unit) = MaterialButton(ctx).apply {
        text = label
        isAllCaps = false
        setOnClickListener { fn() }
    }

    fun tonalButton(ctx: Context, label: String, fn: () -> Unit) = MaterialButton(
        ctx, null, M3.attr.materialButtonOutlinedStyle
    ).apply {
        text = label
        isAllCaps = false
        setOnClickListener { fn() }
    }

    fun input(ctx: Context, hint: String, value: String, inputType: Int): Pair<TextInputLayout, TextInputEditText> {
        val layout = TextInputLayout(ctx, null, M3.attr.textInputOutlinedStyle).apply { this.hint = hint }
        val edit = TextInputEditText(layout.context).apply {
            setText(value)
            this.inputType = inputType
        }
        layout.addView(edit)
        return layout to edit
    }

    fun circle(fill: Int) = GradientDrawable().apply {
        shape = GradientDrawable.OVAL
        setColor(fill)
    }

    fun ripple(ctx: Context): Int {
        val tv = TypedValue()
        ctx.theme.resolveAttribute(android.R.attr.selectableItemBackground, tv, true)
        return tv.resourceId
    }

    fun margins(v: View, l: Int = 0, t: Int = 0, r: Int = 0, b: Int = 0, ctx: Context = v.context): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(-1, -2).apply { setMargins(dp(ctx, l), dp(ctx, t), dp(ctx, r), dp(ctx, b)) }
}
