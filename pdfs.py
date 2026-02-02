
# pdfs.py ✅ NJANGI STANDARD (NO "legacy" anywhere) — COMPLETE FILE
# Includes:
# - Loan Statement PDF (with optional digital signature block)
# - ZIP export for all loan statements
# - Minutes PDF (minutes table)
# - Attendance PDF (attendance table)
# - Payout Receipt PDF WITH contributions list + totals + signatures
#
# ✅ Tables expected by your new code:
# - minutes: session_id, title, body, created_by, created_at, updated_at
# - attendance: session_id, member_id, present(bool), note, created_at
# - loans: id, member_id, status, principal_current/principal, unpaid_interest/accrued_interest, total_due
# - loan_payments: paid_at, loan_id, amount
# - contributions: member_id, amount
# - signatures: role, signer_name, signer_member_id, signed_at

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


# ============================================================
# LOAN STATEMENT PDF (Member)
# ============================================================
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
    Loans Summary shows: Principal Current, Interest (Unpaid/Accrued), Total Due
    Includes optional digital signature block (statement_signature)
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

    # Cycle block (session)
    y -= 0.10 * inch
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(left, y, "Cycle")
    y -= 0.22 * inch
    pdf.setFont("Helvetica", 10)
    pdf.drawString(left, y, f"Session ID: {cycle_info.get('session_id') or cycle_info.get('session_number') or '—'}")
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
        # Table header
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

            loan_id = ln.get("id") or ln.get("loan_id") or ""
            status = str(ln.get("status") or "")[:10]

            principal = float(ln.get("principal_current") or ln.get("principal") or 0)

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
            loan_id = p.get("loan_id") or p.get("id") or "—"
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


# ============================================================
# ZIP EXPORT: All members loan statements
# ============================================================
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


# ============================================================
# Minutes PDF (NEW: minutes table)
# ============================================================
def make_minutes_pdf(brand: str, minutes_row: dict) -> bytes:
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    left = 1 * inch
    y = height - 0.9 * inch

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(left, y, f"{brand} — Meeting Minutes")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - left, y, _utc_now_str())
    y -= 0.35 * inch

    session_id = minutes_row.get("session_id")
    title = str(minutes_row.get("title") or "")
    body = str(minutes_row.get("body") or "")
    created_by = str(minutes_row.get("created_by") or "")
    created_at = str(minutes_row.get("created_at") or "")[:19]

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, "Meeting Info")
    y -= 0.20 * inch

    pdf.setFont("Helvetica", 10)
    pdf.drawString(left, y, f"Session ID: {session_id if session_id is not None else '—'}"); y -= 0.16 * inch
    pdf.drawString(left, y, f"Title: {title or '—'}"); y -= 0.16 * inch
    if created_by or created_at:
        pdf.drawString(left, y, f"Recorded by: {created_by or '—'}    At: {created_at or '—'}"); y -= 0.16 * inch

    y -= 0.10 * inch
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, "Minutes / Documentation")
    y -= 0.20 * inch

    pdf.setFont("Helvetica", 10)
    content = body.strip()
    if not content:
        pdf.drawString(left, y, "—")
    else:
        for raw_line in content.splitlines():
            line = raw_line.rstrip()
            if line == "":
                y -= 0.14 * inch
                continue
            while len(line) > 110:
                pdf.drawString(left, y, line[:110])
                line = line[110:]
                y -= 0.14 * inch
                if y < 1.0 * inch:
                    pdf.showPage()
                    y = height - 1.0 * inch
                    pdf.setFont("Helvetica", 10)
            pdf.drawString(left, y, line)
            y -= 0.14 * inch
            if y < 1.0 * inch:
                pdf.showPage()
                y = height - 1.0 * inch
                pdf.setFont("Helvetica", 10)

    pdf.showPage()
    pdf.save()
    buf.seek(0)
    return buf.getvalue()


# ============================================================
# Attendance PDF (NEW: attendance table)
# ============================================================
def make_attendance_pdf(
    brand: str,
    session_id: int | None,
    attendance_rows: list[dict] | None = None,
    currency: str = "$",
    logo_path: str = "assets/logo.png",
) -> bytes:
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    left = 1 * inch
    y = height - 0.9 * inch

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

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(2.0 * inch, y, f"{brand} — Attendance Sheet")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - left, y, _utc_now_str())
    y -= 0.35 * inch

    pdf.setFont("Helvetica", 10)
    pdf.drawString(left, y, f"Session ID: {session_id if session_id is not None else '—'}"); y -= 0.25 * inch

    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(left, y, "ID")
    pdf.drawString(left + 0.7 * inch, y, "Status")
    pdf.drawString(left + 1.8 * inch, y, "Note")
    y -= 0.16 * inch
    pdf.setFont("Helvetica", 9)

    rows = attendance_rows or []
    if not rows:
        pdf.drawString(left, y, "No attendance recorded.")
    else:
        def _mid(r):
            try:
                return int(r.get("member_id") or 0)
            except Exception:
                return 0

        for r in sorted(rows, key=_mid):
            if y < 1.0 * inch:
                pdf.showPage()
                y = height - 1.0 * inch
                pdf.setFont("Helvetica", 9)

            mid = str(r.get("member_id") or "")
            status = "present" if bool(r.get("present")) else "absent"
            note = str(r.get("note") or "")[:70]
            pdf.drawString(left, y, mid)
            pdf.drawString(left + 0.7 * inch, y, status)
            pdf.drawString(left + 1.8 * inch, y, note)
            y -= 0.14 * inch

    pdf.showPage()
    pdf.save()
    buf.seek(0)
    return buf.getvalue()


# ============================================================
# Payout Receipt PDF (WITH contributions + totals + signatures)
# ============================================================
def make_payout_receipt_pdf(
    brand: str,
    beneficiary: dict,
    payout_info: dict,
    contributions_breakdown: list[dict] | None = None,
    signatures: list[dict] | None = None,
    currency: str = "$",
    logo_path: str = "assets/logo.png",
) -> bytes:
    """
    Payout receipt for ONE beneficiary (WITH contributions list).
    contributions_breakdown items (best effort keys):
      - member_id
      - member_name/name
      - amount
    """
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    left = 1 * inch

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

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(2.0 * inch, height - 0.9 * inch, f"{brand} — Payout Receipt")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - 1 * inch, height - 0.9 * inch, _utc_now_str())

    y = height - 1.5 * inch

    session_id = payout_info.get("session_id")
    payout_date = payout_info.get("payout_date") or payout_info.get("date") or ""
    amount_paid = payout_info.get("payout_amount", payout_info.get("amount"))
    pot_amount = payout_info.get("pot_amount")
    notes = payout_info.get("notes") or ""

    bid = beneficiary.get("member_id") or beneficiary.get("id") or ""
    bname = beneficiary.get("member_name") or beneficiary.get("name") or ""

    rows = contributions_breakdown or []

    def _amt(r):
        try:
            return float(r.get("amount") or 0)
        except Exception:
            return 0.0

    computed_total = sum(_amt(r) for r in rows)
    if pot_amount is None:
        pot_amount = computed_total
    if amount_paid is None:
        amount_paid = pot_amount

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(left, y, "Beneficiary")
    y -= 0.22 * inch
    pdf.setFont("Helvetica", 10)
    pdf.drawString(left, y, f"ID: {bid}    Name: {bname}")
    y -= 0.30 * inch

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(left, y, "Payout Details")
    y -= 0.22 * inch
    pdf.setFont("Helvetica", 10)
    pdf.drawString(left, y, f"Session ID: {session_id if session_id is not None else '—'}"); y -= 0.16 * inch
    pdf.drawString(left, y, f"Payout Date: {str(payout_date)[:10] if payout_date else '—'}"); y -= 0.22 * inch

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, "Totals")
    y -= 0.18 * inch
    pdf.setFont("Helvetica", 10)
    pdf.drawString(left, y, f"Total amount received (pot): {_money(pot_amount, currency)}"); y -= 0.16 * inch
    pdf.drawString(left, y, f"Total amount paid to beneficiary: {_money(amount_paid, currency)}"); y -= 0.24 * inch

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(left, y, "Member Contributions")
    y -= 0.20 * inch

    if not rows:
        pdf.setFont("Helvetica", 10)
        pdf.drawString(left, y, "No contribution breakdown provided.")
        y -= 0.18 * inch
    else:
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(left, y, "#")
        pdf.drawString(left + 0.35 * inch, y, "ID")
        pdf.drawString(left + 1.05 * inch, y, "Member Name")
        pdf.drawRightString(left + 5.8 * inch, y, "Amount")
        y -= 0.16 * inch
        pdf.setFont("Helvetica", 9)

        def _mid(r):
            try:
                return int(r.get("member_id") or r.get("id") or 0)
            except Exception:
                return 0

        for i, r in enumerate(sorted(rows, key=_mid), start=1):
            if y < 1.2 * inch:
                pdf.showPage()
                y = height - 1.0 * inch
                pdf.setFont("Helvetica-Bold", 9)
                pdf.drawString(left, y, "#")
                pdf.drawString(left + 0.35 * inch, y, "ID")
                pdf.drawString(left + 1.05 * inch, y, "Member Name")
                pdf.drawRightString(left + 5.8 * inch, y, "Amount")
                y -= 0.16 * inch
                pdf.setFont("Helvetica", 9)

            mid = r.get("member_id") or r.get("id") or ""
            mname = str(r.get("member_name") or r.get("name") or "")[:40]
            amt = _amt(r)

            pdf.drawString(left, y, str(i))
            pdf.drawString(left + 0.35 * inch, y, str(mid))
            pdf.drawString(left + 1.05 * inch, y, mname)
            pdf.drawRightString(left + 5.8 * inch, y, _money(amt, currency))
            y -= 0.14 * inch

        y -= 0.06 * inch
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(left + 1.05 * inch, y, "TOTAL")
        pdf.drawRightString(left + 5.8 * inch, y, _money(computed_total, currency))
        pdf.setFont("Helvetica", 9)
        y -= 0.22 * inch

    if notes:
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(left, y, "Notes")
        y -= 0.18 * inch
        pdf.setFont("Helvetica", 10)
        for line in str(notes).splitlines():
            pdf.drawString(left, y, line[:110])
            y -= 0.14 * inch
            if y < 1.1 * inch:
                pdf.showPage()
                y = height - 1.0 * inch
                pdf.setFont("Helvetica", 10)

    y -= 0.10 * inch
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(left, y, "Signatures")
    y -= 0.20 * inch

    sigs = signatures or []
    if not sigs:
        pdf.setFont("Helvetica", 10)
        pdf.drawString(left, y, "No signatures attached.")
        y -= 0.16 * inch
    else:
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(left, y, "Role")
        pdf.drawString(left + 1.3 * inch, y, "Signer")
        pdf.drawString(left + 4.3 * inch, y, "Signed At")
        y -= 0.16 * inch
        pdf.setFont("Helvetica", 9)

        for s in sigs[:20]:
            if y < 1.1 * inch:
                pdf.showPage()
                y = height - 1.0 * inch
                pdf.setFont("Helvetica", 9)

            role = str(s.get("role") or "")[:18]
            signer = str(s.get("signer_name") or "")[:35]
            signed_at = str(s.get("signed_at") or "")[:19]

            pdf.drawString(left, y, role)
            pdf.drawString(left + 1.3 * inch, y, signer)
            pdf.drawString(left + 4.3 * inch, y, signed_at)
            y -= 0.14 * inch

    pdf.showPage()
    pdf.save()
    buf.seek(0)
    return buf.getvalue()
