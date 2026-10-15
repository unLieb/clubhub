package de.clubhub.app

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.EditText
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView
import android.widget.Toast

/** Serververwaltung: antippen = auswaehlen, Stift = bearbeiten/loeschen, "+" = hinzufuegen. */
class ServersActivity : Activity() {
    private lateinit var list: ListView
    private var servers: List<Server> = emptyList()
    private var currentId: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = "Server"
        MainActivity.styleActionBar(this)
        actionBar?.setDisplayHomeAsUpEnabled(true)

        list = ListView(this).apply {
            setBackgroundColor(Color.parseColor("#12161F"))
            divider = null
        }
        // Edge-to-Edge: Liste unterhalb von Statusleiste + Action Bar beginnen lassen
        val barH = obtainStyledAttributes(intArrayOf(android.R.attr.actionBarSize)).let {
            val h = it.getDimensionPixelSize(0, 0); it.recycle(); h
        }
        val root = android.widget.FrameLayout(this)
        root.setBackgroundColor(Color.parseColor("#12161F"))
        root.addView(list, android.widget.FrameLayout.LayoutParams(-1, -1))
        root.setOnApplyWindowInsetsListener { v, insets ->
            val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars())
            v.setPadding(bars.left, bars.top + barH, bars.right, bars.bottom)
            android.view.WindowInsets.CONSUMED
        }
        setContentView(root)
        list.adapter = adapter
        refresh()
        if (servers.isEmpty()) showEditDialog(null)
    }

    private fun refresh() {
        servers = ServerStore.load(this)
        currentId = ServerStore.current(this)?.id
        adapter.notifyDataSetChanged()
    }

    private val adapter = object : BaseAdapter() {
        override fun getCount() = servers.size
        override fun getItem(position: Int) = servers[position]
        override fun getItemId(position: Int) = position.toLong()
        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val s = servers[position]
            val d = resources.displayMetrics.density
            val row = LinearLayout(this@ServersActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding((16 * d).toInt(), (10 * d).toInt(), (8 * d).toInt(), (10 * d).toInt())
                setOnClickListener {
                    ServerStore.setCurrent(this@ServersActivity, s.id)
                    setResult(RESULT_OK)
                    finish()
                }
            }
            val col = LinearLayout(this@ServersActivity).apply { orientation = LinearLayout.VERTICAL }
            val isCurrent = s.id == currentId
            col.addView(TextView(this@ServersActivity).apply {
                text = (if (isCurrent) "✓ " else "") + s.name
                textSize = 17f
                setTextColor(Color.WHITE)
                setTypeface(null, if (isCurrent) Typeface.BOLD else Typeface.NORMAL)
            })
            col.addView(TextView(this@ServersActivity).apply {
                text = s.url
                textSize = 13f
                setTextColor(Color.parseColor("#9AA3B2"))
            })
            row.addView(col, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            row.addView(ImageButton(this@ServersActivity).apply {
                setImageResource(android.R.drawable.ic_menu_edit)
                setBackgroundColor(Color.TRANSPARENT)
                contentDescription = "Bearbeiten"
                setOnClickListener { showEditDialog(s) }
            })
            return row
        }
    }

    private fun showEditDialog(existing: Server?) {
        val d = resources.displayMetrics.density
        val pad = (20 * d).toInt()
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

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, "Hinzufügen").setIcon(android.R.drawable.ic_menu_add)
            .setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            1 -> showEditDialog(null)
            android.R.id.home -> finish()
        }
        return true
    }
}
