
from __future__ import annotations

from io import BytesIO
from datetime import datetime, timezone
import os
import zipfile
from typing import Dict, List, Any, Optional

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader


def _money(x, currency="$"):
    try:
        return f"{currency}{float(x):,.2f}"
    except Exception:
        return f"{currency}{x}"


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def make_member_loan_statement_pdf(
    brand: str,
    member: dict,
    cycle_info: dict,
    loans: List[dict],
    payments: List[dict],
    currency: str = "$",
    logo_path: str = "assets/logo.png",
) -> bytes:
    """
    member: {
      "member_id": int, "member_name": str, "position": int|None
    }
    cycle_info: {
      "payout_index": int|None, "payout_date": str|None,
      "cycle_start": str|None, "cycle_end": str|None
    }
    loans: list of dicts, each ideally contains:
      - id (loan_id)
      - status
      - principal (or amount/issued_amount)
      - balance (or principal_current)
      - total_due (optional)
      - issued_at (optional)
      - interest_percent (optional; if not present assume 5)
    payments: list of dicts, each ideally contains:
      - loan_id (or loan_legacy_id)
      - amount
      - paid_at/paid_on
      - note (optional)
    """
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER

    # Logo
    if logo_path and os.path.exists(logo_path):
        try:
            logo = ImageReader(logo_path)
            pdf.drawImage(
                logo,
                0.7 * inch,
                height - 1.2 * inch,
                width=1.0 * inch,
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception:
            pass

    # Header
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(2.0 * inch, height - 0.9 * inch, f"{brand} — Loan Statement")

    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - 1 * inch, height - 0.9 * inch, _utc_now_str())

    # Member block
    y = height - 1.5 * inch
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(1 * inch, y, "Member")
    y -= 0.22 * inch
    pdf.setFont("Helvetica", 10)
    pdf.drawString(1 * inch, y, f"ID: {member.get('member_id')}    Name: {member.get('member_name')}")
    y -= 0.18 * inch
    if member.get("position") is not None:
        pdf.drawString(1 * inch, y, f"Position: {member.get('position')}")
        y -= 0.18 * inch

    # Cycle block
    y -= 0.10 * inch
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(1 * inch, y, "Cycle")
    y -= 0.22 * inch
    pdf.setFont("Helvetica", 10)
    pdf.drawString(1 * inch, y, f"Payout Index: {cycle_info.get('payout_index')}")
    y -= 0.18 * inch
    pdf.drawString(1 * inch, y, f"Payout Date: {cycle_info.get('payout_date') or 'N/A'}")
    y -= 0.18 * inch

    # Loans summary
    y -= 0.10 * inch
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(1 * inch, y, "Loans Summary")
    y -= 0.22 * inch
    pdf.setFont("Helvetica", 10)

    if not loans:
        pdf.drawString(1 * inch, y, "No active loans on record for this member.")
        y -= 0.18 * inch
    else:
        # Table header
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(1 * inch, y, "Loan ID")
        pdf.drawString(2.1 * inch, y, "Status")
        pdf.drawRightString(4.7 * inch, y, "Principal")
        pdf.drawRightString(6.8 * inch, y, "Balance")
        y -= 0.18 * inch
        pdf.setFont("Helvetica", 9)

        for ln in loans:
            if y < 1.3 * inch:
                pdf.showPage()
                y = height - 1.0 * inch
                pdf.setFont("Helvetica-Bold", 9)
                pdf.drawString(1 * inch, y, "Loan ID")
                pdf.drawString(2.1 * inch, y, "Status")
                pdf.drawRightString(4.7 * inch, y, "Principal")
                pdf.drawRightString(6.8 * inch, y, "Balance")
                y -= 0.18 * inch
                pdf.setFont("Helvetica", 9)

            loan_id = ln.get("id") or ln.get("loan_id") or ln.get("loan_legacy_id")
            status = str(ln.get("status") or "")
            principal = ln.get("principal") or ln.get("amount") or ln.get("issued_amount") or 0
            balance = ln.get("balance") or ln.get("principal_current") or ln.get("total_due") or 0

            pdf.drawString(1 * inch, y, str(loan_id))
            pdf.drawString(2.1 * inch, y, status[:14])
            pdf.drawRightString(4.7 * inch, y, _money(principal, currency))
            pdf.drawRightString(6.8 * inch, y, _money(balance, currency))
            y -= 0.16 * inch

    # Payments section
    y -= 0.20 * inch
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(1 * inch, y, "Payments (Recent)")
    y -= 0.22 * inch
    pdf.setFont("Helvetica", 9)

    if not payments:
        pdf.drawString(1 * inch, y, "No payments recorded.")
        y -= 0.18 * inch
    else:
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(1 * inch, y, "Date")
        pdf.drawString(2.2 * inch, y, "Loan")
        pdf.drawRightString(6.8 * inch, y, "Amount")
        y -= 0.18 * inch
        pdf.setFont("Helvetica", 9)

        for p in payments[:40]:
            if y < 1.3 * inch:
                pdf.showPage()
                y = height - 1.0 * inch
                pdf.setFont("Helvetica-Bold", 9)
                pdf.drawString(1 * inch, y, "Date")
                pdf.drawString(2.2 * inch, y, "Loan")
                pdf.drawRightString(6.8 * inch, y, "Amount")
                y -= 0.18 * inch
                pdf.setFont("Helvetica", 9)

            dt = str(p.get("paid_at") or p.get("paid_on") or "")[:10] or "—"
            loan_id = p.get("loan_id") or p.get("loan_legacy_id") or "—"
            amt = p.get("amount") or 0

            pdf.drawString(1 * inch, y, dt)
            pdf.drawString(2.2 * inch, y, str(loan_id))
            pdf.drawRightString(6.8 * inch, y, _money(amt, currency))
            y -= 0.16 * inch

    # Signature lines (optional for member statements)
    y -= 0.35 * inch
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(1 * inch, y, "Acknowledgement (Optional)")
    y -= 0.35 * inch
    pdf.setFont("Helvetica", 10)
    pdf.line(1 * inch, y, 3.7 * inch, y)
    pdf.drawString(1 * inch, y - 0.18 * inch, "Member Signature")
    pdf.line(4.1 * inch, y, 6.8 * inch, y)
    pdf.drawString(4.1 * inch, y - 0.18 * inch, "Date")

    pdf.showPage()
    pdf.save()
    buf.seek(0)
    return buf.getvalue()


def make_loan_statements_zip(
    brand: str,
    cycle_info: dict,
    member_statements: List[dict],
    currency: str = "$",
    logo_path: str = "assets/logo.png",
) -> bytes:
    """
    member_statements: list of dicts like:
      {
        "member": {"member_id":..., "member_name":..., "position":...},
        "loans": [...],
        "payments": [...]
      }
    Returns ZIP bytes containing one PDF per member with loans/payments.
    """
    zbuf = BytesIO()
    with zipfile.ZipFile(zbuf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for ms in member_statements:
            member = ms.get("member") or {}
            mid = member.get("member_id")
            mname = str(member.get("member_name") or "Member").replace("/", "-").replace("\\", "-")

            pdf_bytes = make_member_loan_statement_pdf(
                brand=brand,
                member=member,
                cycle_info=cycle_info,
                loans=ms.get("loans") or [],
                payments=ms.get("payments") or [],
                currency=currency,
                logo_path=logo_path,
            )

            filename = f"loan_statement_{int(mid):02d}_{mname[:30]}.pdf" if mid is not None else f"loan_statement_{mname[:30]}.pdf"
            zf.writestr(filename, pdf_bytes)

    zbuf.seek(0)
    return zbuf.getvalue()
