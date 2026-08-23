import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


def generate_timeclock_pdf(user_name: str, personnel_number: str | None, entries: list, generated_at_local) -> bytes:
    """Erzeugt eine PDF-Übersicht der Zeiterfassungs-Buchungen eines Nutzers
    (Datum, Kommen, Gehen, Dauer je Buchung + Gesamtsumme) für Lohnbuchhaltung/
    Prüfungen - ergänzt das Änderungsprotokoll (siehe TimeEntryAudit) um eine
    weitergabefähige, unterschreibbare Zusammenfassung. `entries` erwartet
    dieselbe Struktur wie in admin_timeclock_page/timeclock_view
    (clock_in_local/clock_out_local/open), älteste zuerst."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Zeiterfassung – ClubHUB", styles["Title"]),
        Paragraph(f"{user_name}" + (f" (Personalnr. {personnel_number})" if personnel_number else ""), styles["Heading2"]),
        Paragraph(f"Erstellt am {generated_at_local.strftime('%d.%m.%Y, %H:%M')} Uhr", styles["Normal"]),
        Spacer(1, 0.6 * cm),
    ]

    table_data = [["Datum", "Kommen", "Gehen", "Dauer"]]
    total_minutes = 0
    for e in entries:
        clock_in = e["clock_in_local"]
        clock_out = e["clock_out_local"]
        if clock_out:
            minutes = int((clock_out - clock_in).total_seconds() // 60)
            total_minutes += minutes
            duration = f"{minutes // 60} Std. {minutes % 60} Min."
            out_str = clock_out.strftime("%H:%M")
        else:
            duration = "läuft noch"
            out_str = "-"
        table_data.append([
            clock_in.strftime("%d.%m.%Y"),
            clock_in.strftime("%H:%M"),
            out_str,
            duration,
        ])

    table = Table(table_data, colWidths=[4 * cm, 3 * cm, 3 * cm, 4 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)

    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph(
        f"Gesamt: {total_minutes // 60} Std. {total_minutes % 60} Min. über {len(entries)} Buchung(en)",
        styles["Heading3"],
    ))

    doc.build(story)
    return buffer.getvalue()


def generate_cooling_report_pdf(
    device_name: str, location: str | None, target_temp: float, max_temp: float,
    month_label: str, readings: list, generated_at_local,
) -> bytes:
    """Monats-Protokoll der Temperatur-Erfassungen einer Kühlzelle für
    HACCP-Kontrollen. `readings` erwartet bereits lokalisierte, einfache
    Dicts (date/time/value/over_limit/user_name als Strings/Zahlen, siehe
    cooling_device_export_pdf in main.py) statt ORM-Objekten, damit dieses
    Modul frei von DB-/Zeitzonen-Belangen bleibt (gleiche Trennung wie bei
    generate_timeclock_pdf)."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    subtitle = device_name + (f" – {location}" if location else "")
    story = [
        Paragraph("Temperaturprotokoll – ClubHUB", styles["Title"]),
        Paragraph(subtitle, styles["Heading2"]),
        Paragraph(f"Zeitraum {month_label} · Soll {target_temp:g}°C · Grenzwert {max_temp:g}°C", styles["Normal"]),
        Paragraph(f"Erstellt am {generated_at_local.strftime('%d.%m.%Y, %H:%M')} Uhr", styles["Normal"]),
        Spacer(1, 0.6 * cm),
    ]

    table_data = [["Datum", "Uhrzeit", "Temperatur", "Grenzwert überschritten", "Erfasst von"]]
    over_limit_rows = []
    for i, r in enumerate(readings, start=1):
        if r["over_limit"]:
            over_limit_rows.append(i)
        table_data.append([
            r["date"], r["time"], f"{r['value']:g}°C",
            "JA" if r["over_limit"] else "Nein", r["user_name"],
        ])

    table = Table(table_data, colWidths=[3 * cm, 2.5 * cm, 3 * cm, 4.5 * cm, 4 * cm], repeatRows=1)
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    # Ueberschreitungen zusaetzlich rot hervorheben - eigene TableStyle-Regel
    # je betroffener Zeile, da reportlab kein bedingtes Zell-Styling anhand
    # von Werten kennt (siehe pdf_export.py-Kommentar-Konvention).
    for row_idx in over_limit_rows:
        style_commands.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#fecaca")))
        style_commands.append(("TEXTCOLOR", (3, row_idx), (3, row_idx), colors.HexColor("#991b1b")))
        style_commands.append(("FONTNAME", (3, row_idx), (3, row_idx), "Helvetica-Bold"))
    table.setStyle(TableStyle(style_commands))
    story.append(table)

    story.append(Spacer(1, 0.6 * cm))
    breach_count = len(over_limit_rows)
    summary = f"{len(readings)} Erfassung(en) im Zeitraum"
    if breach_count:
        summary += f", davon {breach_count} über dem Grenzwert"
    story.append(Paragraph(summary, styles["Heading3"]))

    doc.build(story)
    return buffer.getvalue()
