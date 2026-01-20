from io import BytesIO
from datetime import datetime
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch


def _money(x):
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)


def make_payout_receipt_pdf(brand: str, receipt: dict):
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(1 * inch, height - 1 * inch, f"{brand} — Payout Receipt")

    pdf.setFont("Helvetica", 10)
    pdf.drawString(1 * inch, height - 1.3 * inch,
                   f"Beneficiary: {receipt.get('beneficiary_legacy_member_id')} — {receipt.get('beneficiary_name')}")
    pdf.drawString(1 * inch, height - 1.5 * inch, f"Executed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    y = height - 1.9 * inch
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(1 * inch, y, "Summary")
    y -= 0.22 * inch
    pdf.setFont("Helvetica", 10)
    pdf.drawString(1 * inch, y, f"Total pot paid out: {_money(receipt.get('pot_paid_out',0))}"); y -= 0.18 * inch
    pdf.drawString(1 * inch, y, f"Next beneficiary index: {receipt.get('next_payout_index')}"); y -= 0.18 * inch
    pdf.drawString(1 * inch, y, f"Next payout date: {receipt.get('next_payout_date')}"); y -= 0.18 * inch
    pdf.drawString(1 * inch, y, f"Payout logged: {'YES' if receipt.get('payout_logged') else 'NO'}")

    y -= 0.35 * inch
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(1 * inch, y, "Member")
    pdf.drawString(4.6 * inch, y, "Contributed")
    y -= 0.18 * inch
    pdf.setFont("Helvetica", 9)

    total_check = 0.0
    for row in (receipt.get("contribution_summary") or []):
        if y < 1.0 * inch:
            pdf.showPage()
            y = height - 1 * inch
            pdf.setFont("Helvetica-Bold", 10)
            pdf.drawString(1 * inch, y, "Member")
            pdf.drawString(4.6 * inch, y, "Contributed")
            y -= 0.18 * inch
            pdf.setFont("Helvetica", 9)

        name = f'{row.get("member_id")} — {row.get("member_name")}'
        amt = float(row.get("contributed") or 0)
        total_check += amt

        pdf.drawString(1 * inch, y, name[:52])
        pdf.drawRightString(6.8 * inch, y, _money(amt))
        y -= 0.16 * inch

    y -= 0.2 * inch
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(1 * inch, y, "Grand Total")
    pdf.drawRightString(6.8 * inch, y, _money(total_check))

    pdf.showPage()
    pdf.save()
    buf.seek(0)
    return buf.getvalue()
