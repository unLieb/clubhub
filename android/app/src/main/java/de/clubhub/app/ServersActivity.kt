package de.clubhub.app

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.widget.BaseAdapter
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView
import android.widget.Toast

/** Serververwaltung: Karte antippen = Server auswaehlen, Stift = bearbeiten/loeschen. */
class ServersActivity : Activity() {
    private lateinit var list: ListView
    private lateinit var empty: TextView
    private var servers: List<Server> = emptyList()
    private var currentId: String? = null

    private val bg = Color.parseColor("#12161F")
    private val panel = Color.parseColor("#1B2130")
    private val line = Color.parseColor("#2A3245")
    private val accent = Color.parseColor("#5BB381")
    private val muted = Color.parseColor("#9AA3B2")

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun round(fill: Int, radius: Int, stroke: Int? = null, strokeW: Int = 1) =
        GradientDrawable().apply {
            setColor(fill)
            cornerRadius = dp(radius).toFloat()
            if (stroke != null) setStroke(dp(strokeW), stroke)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(bg)
            setOnApplyWindowInsetsListener { v, insets ->
                val bars = insets.getInsets(WindowInsets.Type.systemBars())
                v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
                WindowInsets.CONSUMED
            }
        }

        // Kopf: Zurueck, Titel + Untertitel
        val head = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(8), dp(12), dp(16), dp(8))
        }
        head.addView(ImageButton(this).apply {
            setImageResource(android.R.drawable.ic_media_previous)
            setColorFilter(Color.WHITE)
            background = null
            contentDescription = "Zurück"
            setOnClickListener { finish() }
        }, LinearLayout.LayoutParams(dp(48), dp(48)))
        val titles = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        titles.addView(TextView(this).apply {
            text = "Server"
            textSize = 24f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.WHITE)
        })
        titles.addView(TextView(this).apply {
            text = "Instanzen, zwischen denen du wechseln kannst"
            textSize = 13f
            setTextColor(muted)
        })
        head.addView(titles, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { leftMargin = dp(4) })
        root.addView(head)

        // Liste (+ Leerzustand)
        val body = FrameLayout(this)
        list = ListView(this).apply {
            divider = null
            dividerHeight = 0
            setPadding(dp(16), dp(8), dp(16), dp(8))
            clipToPadding = false
            selector = android.graphics.drawable.ColorDrawable(Color.TRANSPARENT)
        }
        empty = TextView(this).apply {
            text = "Noch kein Server hinterlegt.\nTippe unten auf „Server hinzufügen“."
            textSize = 15f
            gravity = Gravity.CENTER
            setTextColor(muted)
            visibility = View.GONE
        }
        body.addView(list, FrameLayout.LayoutParams(-1, -1))
        body.addView(empty, FrameLayout.LayoutParams(-1, -1))
        root.addView(body, LinearLayout.LayoutParams(-1, 0, 1f))

        // Hinzufuegen-Button unten
        root.addView(TextView(this).apply {
            text = "+  Server hinzufügen"
            textSize = 16f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#0B1410"))
            gravity = Gravity.CENTER
            background = round(accent, 12)
            setOnClickListener { showEditDialog(null) }
        }, LinearLayout.LayoutParams(-1, dp(52)).apply {
            setMargins(dp(16), dp(8), dp(16), dp(16))
        })

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
            val s = servers[position]
            val isCurrent = s.id == currentId

            val card = LinearLayout(this@ServersActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(14), dp(14), dp(6), dp(14))
                background = round(panel, 16, if (isCurrent) accent else line, if (isCurrent) 2 else 1)
                setOnClickListener {
                    ServerStore.setCurrent(this@ServersActivity, s.id)
                    setResult(RESULT_OK)
                    finish()
                }
            }
            // Avatar mit Anfangsbuchstaben
            card.addView(TextView(this@ServersActivity).apply {
                text = s.name.trim().firstOrNull()?.uppercase() ?: "?"
                textSize = 20f
                setTypeface(null, Typeface.BOLD)
                gravity = Gravity.CENTER
                setTextColor(if (isCurrent) Color.parseColor("#0B1410") else Color.WHITE)
                background = GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(if (isCurrent) accent else Color.parseColor("#2A3245"))
                }
            }, LinearLayout.LayoutParams(dp(44), dp(44)))

            val col = LinearLayout(this@ServersActivity).apply { orientation = LinearLayout.VERTICAL }
            val nameRow = LinearLayout(this@ServersActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            nameRow.addView(TextView(this@ServersActivity).apply {
                text = s.name
                textSize = 17f
                setTypeface(null, Typeface.BOLD)
                setTextColor(Color.WHITE)
                maxLines = 1
                ellipsize = android.text.TextUtils.TruncateAt.END
            })
            if (isCurrent) {
                nameRow.addView(TextView(this@ServersActivity).apply {
                    text = "AKTIV"
                    textSize = 10f
                    setTypeface(null, Typeface.BOLD)
                    setTextColor(accent)
                    setPadding(dp(8), dp(2), dp(8), dp(2))
                    background = round(Color.parseColor("#1F3A2C"), 8)
                }, LinearLayout.LayoutParams(-2, -2).apply { leftMargin = dp(8) })
            }
            col.addView(nameRow)
            col.addView(TextView(this@ServersActivity).apply {
                text = s.url.substringAfter("://")
                textSize = 13f
                setTextColor(muted)
                maxLines = 1
                ellipsize = android.text.TextUtils.TruncateAt.END
            })
            card.addView(col, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                leftMargin = dp(14)
            })
            card.addView(ImageButton(this@ServersActivity).apply {
                setImageResource(android.R.drawable.ic_menu_edit)
                setColorFilter(muted)
                background = null
                contentDescription = "Bearbeiten"
                setOnClickListener { showEditDialog(s) }
            }, LinearLayout.LayoutParams(dp(48), dp(48)))

            // Abstand zwischen den Karten
            return FrameLayout(this@ServersActivity).apply {
                setPadding(0, dp(5), 0, dp(5))
                addView(card, FrameLayout.LayoutParams(-1, -2))
            }
        }
    }

    private fun showEditDialog(existing: Server?) {
        val pad = dp(20)
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad / 2, pad, 0)
        }
        val nameIn = EditText(this).apply {
            hint = "Name (z.B. Alte Kantine)"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_WORDS
            setText(existing?.name ?: "")
        }
        val urlIn = EditText(this).apply {
            hint = "https://server.example:4447"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setText(existing?.url ?: "")
        }
        box.addView(nameIn)
        box.addView(urlIn)

        val b = AlertDialog.Builder(this)
            .setTitle(if (existing == null) "Server hinzufügen" else "Server bearbeiten")
            .setView(box)
            .setPositiveButton("Speichern", null)
            .setNegativeButton("Abbrechen", null)
        if (existing != null) {
            b.setNeutralButton("Löschen") { _, _ ->
                AlertDialog.Builder(this)
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
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val url = ServerStore.normalizeUrl(urlIn.text.toString())
                val name = nameIn.text.toString().trim().ifEmpty { url.substringAfter("://") }
                if (urlIn.text.isBlank() || !android.util.Patterns.WEB_URL.matcher(url).matches()) {
                    Toast.makeText(this, "Bitte eine gültige URL eingeben", Toast.LENGTH_SHORT).show()
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
