from __future__ import annotations

from io import BytesIO
from datetime import datetime, timezone
import os
import zipfile
from typing import List, Optional, Dict, Any

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
    statement_signature: Optional[Dict[str, Any]] = None,
) -> bytes:
    """
    Updated:
    - Loans Summary now shows: Principal, Interest (Unpaid/Accrued), Total Due
    - Keeps digital signature block
    """
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    left = 1 * inch

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
    pdf.drawString(left, y, "Member")
    y -= 0.22 * inch
    pdf.setFont("Helvetica", 10)
    pdf.drawString(left, y, f"ID: {member.get('member_id')}    Name: {member.get('member_name')}")
    y -= 0.18 * inch
    if member.get("position") is not None:
        pdf.drawString(left, y, f"Position: {member.get('position')}")
        y -= 0.18 * inch

    # Cycle block
    y -= 0.10 * inch
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(left, y, "Cycle")
    y -= 0.22 * inch
    pdf.setFont("Helvetica", 10)
    pdf.drawString(left, y, f"Payout Index: {cycle_info.get('payout_index')}")
    y -= 0.18 * inch
    pdf.drawString(left, y, f"Payout Date: {cycle_info.get('payout_date') or 'N/A'}")
    y -= 0.18 * inch

    # Loans summary
    y -= 0.10 * inch
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(left, y, "Loans Summary")
    y -= 0.22 * inch

    if not loans:
        pdf.setFont("Helvetica", 10)
        pdf.drawString(left, y, "No loans on record for this member.")
        y -= 0.18 * inch
    else:
        # Table header (WITH interest)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(left, y, "Loan ID")
        pdf.drawString(left + 0.9 * inch, y, "Status")
        pdf.drawRightString(left + 3.2 * inch, y, "Principal")
        pdf.drawRightString(left + 4.5 * inch, y, "Interest")
        pdf.drawRightString(left + 5.8 * inch, y, "Total Due")
        y -= 0.18 * inch
        pdf.setFont("Helvetica", 9)

        total_principal = 0.0
        total_interest = 0.0
        total_due_all = 0.0

        for ln in loans:
            if y < 1.5 * inch:
                pdf.showPage()
                y = height - 1.0 * inch
                pdf.setFont("Helvetica-Bold", 9)
                pdf.drawString(left, y, "Loan ID")
                pdf.drawString(left + 0.9 * inch, y, "Status")
                pdf.drawRightString(left + 3.2 * inch, y, "Principal")
                pdf.drawRightString(left + 4.5 * inch, y, "Interest")
                pdf.drawRightString(left + 5.8 * inch, y, "Total Due")
                y -= 0.18 * inch
                pdf.setFont("Helvetica", 9)

            loan_id = ln.get("id") or ln.get("loan_id") or ln.get("loan_legacy_id")
            status = str(ln.get("status") or "")[:10]

            principal = float(ln.get("principal") or 0)

            unpaid_interest = float(ln.get("unpaid_interest") or 0)
            accrued_interest = float(ln.get("accrued_interest") or 0)
            interest_val = unpaid_interest if unpaid_interest > 0 else accrued_interest

            total_due = ln.get("total_due")
            if total_due is None:
                total_due = principal + interest_val
            total_due = float(total_due or 0)

            total_principal += principal
            total_interest += interest_val
            total_due_all += total_due

            pdf.drawString(left, y, str(loan_id))
            pdf.drawString(left + 0.9 * inch, y, status)
            pdf.drawRightString(left + 3.2 * inch, y, _money(principal, currency))
            pdf.drawRightString(left + 4.5 * inch, y, _money(interest_val, currency))
            pdf.drawRightString(left + 5.8 * inch, y, _money(total_due, currency))
            y -= 0.16 * inch

        # Totals row
        y -= 0.06 * inch
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(left, y, "Totals")
        pdf.drawRightString(left + 3.2 * inch, y, _money(total_principal, currency))
        pdf.drawRightString(left + 4.5 * inch, y, _money(total_interest, currency))
        pdf.drawRightString(left + 5.8 * inch, y, _money(total_due_all, currency))
        pdf.setFont("Helvetica", 9)
        y -= 0.18 * inch

    # Payments section
    y -= 0.20 * inch
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(left, y, "Payments (Recent)")
    y -= 0.22 * inch

    if not payments:
        pdf.setFont("Helvetica", 10)
        pdf.drawString(left, y, "No payments recorded.")
        y -= 0.18 * inch
    else:
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(left, y, "Date")
        pdf.drawString(left + 1.2 * inch, y, "Loan")
        pdf.drawRightString(left + 5.8 * inch, y, "Amount")
        y -= 0.18 * inch
        pdf.setFont("Helvetica", 9)

        for p in payments[:40]:
            if y < 1.3 * inch:
                pdf.showPage()
                y = height - 1.0 * inch
                pdf.setFont("Helvetica-Bold", 9)
                pdf.drawString(left, y, "Date")
                pdf.drawString(left + 1.2 * inch, y, "Loan")
                pdf.drawRightString(left + 5.8 * inch, y, "Amount")
                y -= 0.18 * inch
                pdf.setFont("Helvetica", 9)

            dt = str(p.get("paid_at") or p.get("paid_on") or p.get("created_at") or "")[:10] or "—"
            loan_id = p.get("loan_id") or p.get("loan_legacy_id") or "—"
            amt = p.get("amount") or 0

            pdf.drawString(left, y, dt)
            pdf.drawString(left + 1.2 * inch, y, str(loan_id))
            pdf.drawRightString(left + 5.8 * inch, y, _money(amt, currency))
            y -= 0.16 * inch

    # Acknowledgement / Signature
    y -= 0.35 * inch
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(left, y, "Acknowledgement (Optional)")
    y -= 0.28 * inch

    if statement_signature:
        signer = str(statement_signature.get("signer_name", "") or "")
        signed_at = str(statement_signature.get("signed_at", "") or "")[:19]

        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(left, y, "Digitally Signed")
        y -= 0.18 * inch

        pdf.setFont("Helvetica", 10)
        pdf.drawString(left, y, f"Signer: {signer}")
        y -= 0.18 * inch
        pdf.drawString(left, y, f"Signed at: {signed_at} UTC")
        y -= 0.18 * inch
    else:
        pdf.setFont("Helvetica", 10)
        pdf.line(left, y, left + 2.7 * inch, y)
        pdf.drawString(left, y - 0.18 * inch, "Member Signature")
        pdf.line(left + 3.1 * inch, y, left + 5.8 * inch, y)
        pdf.drawString(left + 3.1 * inch, y - 0.18 * inch, "Date")
        y -= 0.35 * inch

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
    member_statements may include "statement_signature" optionally.
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
                statement_signature=ms.get("statement_signature"),
            )

            filename = (
                f"loan_statement_{int(mid):02d}_{mname[:30]}.pdf"
                if mid is not None
                else f"loan_statement_{mname[:30]}.pdf"
            )
            zf.writestr(filename, pdf_bytes)

    zbuf.seek(0)
    return zbuf.getvalue()
