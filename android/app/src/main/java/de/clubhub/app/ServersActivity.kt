package de.clubhub.app

import android.app.Activity
import android.graphics.Typeface
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView
import android.widget.Toast
import com.google.android.material.dialog.MaterialAlertDialogBuilder

/** Serververwaltung (Material 3): Karte antippen = Server auswaehlen, Stift = bearbeiten/loeschen. */
class ServersActivity : Activity() {
    private lateinit var list: ListView
    private lateinit var empty: TextView
    private var servers: List<Server> = emptyList()
    private var currentId: String? = null

    private fun dp(v: Int) = Ui.dp(this, v)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = Ui.root(this)
        root.addView(Ui.toolbar(this, "Server", "Instanzen, zwischen denen du wechseln kannst"))

        val body = FrameLayout(this)
        list = ListView(this).apply {
            divider = null
            dividerHeight = 0
            setPadding(dp(16), dp(8), dp(16), dp(8))
            clipToPadding = false
            selector = android.graphics.drawable.ColorDrawable(android.graphics.Color.TRANSPARENT)
        }
        empty = TextView(this).apply {
            text = "Noch kein Server hinterlegt.\nTippe unten auf „Server hinzufügen“."
            textSize = 15f
            gravity = Gravity.CENTER
            setTextColor(Ui.color(context, Ui.onSurfaceVariant))
            visibility = View.GONE
        }
        body.addView(list, FrameLayout.LayoutParams(-1, -1))
        body.addView(empty, FrameLayout.LayoutParams(-1, -1))
        root.addView(body, LinearLayout.LayoutParams(-1, 0, 1f))

        val add = Ui.filledButton(this, "Server hinzufügen") { showEditDialog(null) }.apply {
            setIconResource(R.drawable.ic_add)
            iconTint = android.content.res.ColorStateList.valueOf(Ui.color(context, Ui.onPrimary))
        }
        root.addView(add, LinearLayout.LayoutParams(-1, dp(56)).apply { setMargins(dp(16), dp(8), dp(16), dp(16)) })

        setContentView(root)
        list.adapter = adapter
        refresh()
        if (servers.isEmpty()) showEditDialog(null)
    }

    private fun refresh() {
        servers = ServerStore.load(this)
        currentId = ServerStore.current(this)?.id
        adapter.notifyDataSetChanged()
        empty.visibility = if (servers.isEmpty()) View.VISIBLE else View.GONE
    }

    private val adapter = object : BaseAdapter() {
        override fun getCount() = servers.size
        override fun getItem(position: Int) = servers[position]
        override fun getItemId(position: Int) = position.toLong()
        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val ctx = this@ServersActivity
            val s = servers[position]
            val isCurrent = s.id == currentId

            val row = LinearLayout(ctx).apply {
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(14), dp(12), dp(6), dp(12))
            }
            row.addView(TextView(ctx).apply {
                text = s.name.trim().firstOrNull()?.uppercase() ?: "?"
                textSize = 20f
                setTypeface(null, Typeface.BOLD)
                gravity = Gravity.CENTER
                setTextColor(Ui.color(ctx, if (isCurrent) Ui.onPrimary else Ui.onSurface))
                background = Ui.circle(Ui.color(ctx, if (isCurrent) Ui.primary else Ui.outlineVariant))
            }, LinearLayout.LayoutParams(dp(44), dp(44)))

            val col = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
            val nameRow = LinearLayout(ctx).apply { gravity = Gravity.CENTER_VERTICAL }
            nameRow.addView(TextView(ctx).apply {
                text = s.name
                textSize = 17f
                setTypeface(null, Typeface.BOLD)
                setTextColor(Ui.color(ctx, Ui.onSurface))
                maxLines = 1
                ellipsize = android.text.TextUtils.TruncateAt.END
            })
            if (isCurrent) {
                nameRow.addView(TextView(ctx).apply {
                    text = "AKTIV"
                    textSize = 10f
                    setTypeface(null, Typeface.BOLD)
                    setTextColor(Ui.color(ctx, Ui.onPrimaryContainer))
                    setPadding(dp(8), dp(2), dp(8), dp(2))
                    background = android.graphics.drawable.GradientDrawable().apply {
                        setColor(Ui.color(ctx, Ui.primaryContainer))
                        cornerRadius = dp(8).toFloat()
                    }
                }, LinearLayout.LayoutParams(-2, -2).apply { leftMargin = dp(8) })
            }
            col.addView(nameRow)
            col.addView(TextView(ctx).apply {
                text = s.url.substringAfter("://")
                textSize = 13f
                setTextColor(Ui.color(ctx, Ui.onSurfaceVariant))
                maxLines = 1
                ellipsize = android.text.TextUtils.TruncateAt.END
            })
            row.addView(col, LinearLayout.LayoutParams(0, -2, 1f).apply { leftMargin = dp(14) })
            row.addView(ImageButton(ctx).apply {
                setImageResource(R.drawable.ic_edit)
                setBackgroundResource(Ui.ripple(ctx))
                contentDescription = "Bearbeiten"
                setOnClickListener { showEditDialog(s) }
            }, LinearLayout.LayoutParams(dp(48), dp(48)))

            val card = Ui.card(ctx, isCurrent).apply {
                addView(row)
                setOnClickListener {
                    ServerStore.setCurrent(ctx, s.id)
                    setResult(RESULT_OK)
                    finish()
                }
            }
            return FrameLayout(ctx).apply {
                setPadding(0, dp(5), 0, dp(5))
                addView(card, FrameLayout.LayoutParams(-1, -2))
            }
        }
    }

    private fun showEditDialog(existing: Server?) {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(8), dp(24), 0)
        }
        val (nameLayout, nameIn) = Ui.input(
            this, "Name (z.B. Alte Kantine)", existing?.name ?: "",
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_WORDS
        )
        val (urlLayout, urlIn) = Ui.input(
            this, "Adresse (https://server.example:4447)", existing?.url ?: "",
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        )
        box.addView(nameLayout, Ui.margins(box, b = 12))
        box.addView(urlLayout, Ui.margins(box))

        val b = MaterialAlertDialogBuilder(this)
            .setTitle(if (existing == null) "Server hinzufügen" else "Server bearbeiten")
            .setView(box)
            .setPositiveButton("Speichern", null)
            .setNegativeButton("Abbrechen", null)
        if (existing != null) {
            b.setNeutralButton("Löschen") { _, _ ->
                MaterialAlertDialogBuilder(this)
                    .setMessage("Server „${existing.name}“ wirklich entfernen?")
                    .setPositiveButton("Entfernen") { _, _ ->
                        ServerStore.delete(this, existing.id)
                        refresh()
                    }
                    .setNegativeButton("Abbrechen", null)
                    .show()
            }
        }
        val dialog = b.create()
        dialog.setOnShowListener {
            dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val raw = urlIn.text.toString()
                val url = ServerStore.normalizeUrl(raw)
                val name = nameIn.text.toString().trim().ifEmpty { url.substringAfter("://") }
                if (raw.isBlank() || !android.util.Patterns.WEB_URL.matcher(url).matches()) {
                    urlLayout.error = "Bitte eine gültige Adresse eingeben"
                    return@setOnClickListener
                }
                ServerStore.upsert(this, existing?.id, name, url)
                dialog.dismiss()
                refresh()
            }
        }
        dialog.show()
    }
}
