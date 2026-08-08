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
