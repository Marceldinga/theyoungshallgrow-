# ============================================================
# ✅ NEW PDFs: Minutes + Attendance (Legacy)
# Add BELOW your existing make_loan_statements_zip(...)
# ============================================================

def make_minutes_pdf(brand: str, minutes_row: dict) -> bytes:
    """
    Generates a simple PDF for a minutes record.

    Expected keys (best effort; missing is ok):
      - meeting_date (date or str)
      - session_number (int)
      - title (str)
      - content (str)
      - tags (str)
      - created_at (str)
      - created_by (str)
    """
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    left = 1 * inch
    y = height - 0.9 * inch

    # Header
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(left, y, f"{brand} — Meeting Minutes")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - left, y, _utc_now_str())
    y -= 0.35 * inch

    # Meta block
    meeting_date = str(minutes_row.get("meeting_date") or "")[:10]
    session_number = minutes_row.get("session_number")
    title = str(minutes_row.get("title") or "")
    tags = str(minutes_row.get("tags") or "")
    created_by = str(minutes_row.get("created_by") or "")
    created_at = str(minutes_row.get("created_at") or "")[:19]

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, "Meeting Info")
    y -= 0.20 * inch

    pdf.setFont("Helvetica", 10)
    pdf.drawString(left, y, f"Date: {meeting_date or '—'}")
    y -= 0.16 * inch
    pdf.drawString(left, y, f"Session #: {session_number if session_number is not None else '—'}")
    y -= 0.16 * inch
    pdf.drawString(left, y, f"Title: {title or '—'}")
    y -= 0.16 * inch
    if tags:
        pdf.drawString(left, y, f"Tags: {tags}")
        y -= 0.16 * inch
    if created_by or created_at:
        pdf.drawString(left, y, f"Recorded by: {created_by or '—'}    At: {created_at or '—'}")
        y -= 0.16 * inch

    y -= 0.10 * inch
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y, "Minutes / Documentation")
    y -= 0.20 * inch

    # Content (wrapped)
    pdf.setFont("Helvetica", 10)
    content = str(minutes_row.get("content") or "").strip()
    if not content:
        pdf.drawString(left, y, "—")
        y -= 0.16 * inch
    else:
        # simple line wrap
        for raw_line in content.splitlines():
            line = raw_line.rstrip()
            if line == "":
                y -= 0.14 * inch
                if y < 1.0 * inch:
                    pdf.showPage()
                    y = height - 1.0 * inch
                    pdf.setFont("Helvetica", 10)
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


def make_attendance_pdf(
    brand: str,
    meeting_date: str,
    session_number: int | None,
    attendance_rows: list[dict] | None = None,
    currency: str = "$",  # kept for consistent signature; not used
    logo_path: str = "assets/logo.png",
) -> bytes:
    """
    Generates an attendance sheet PDF.

    attendance_rows expected keys:
      - legacy_member_id
      - member_name
      - status
      - note
    """
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    left = 1 * inch
    y = height - 0.9 * inch

    # Logo (optional)
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
    pdf.drawString(2.0 * inch, y, f"{brand} — Attendance Sheet")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - left, y, _utc_now_str())
    y -= 0.35 * inch

    # Meta
    pdf.setFont("Helvetica", 10)
    pdf.drawString(left, y, f"Date: {str(meeting_date)[:10] or '—'}")
    y -= 0.16 * inch
    pdf.drawString(left, y, f"Session #: {session_number if session_number is not None else '—'}")
    y -= 0.25 * inch

    # Table header
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(left, y, "ID")
    pdf.drawString(left + 0.7 * inch, y, "Member")
    pdf.drawString(left + 3.8 * inch, y, "Status")
    pdf.drawString(left + 4.9 * inch, y, "Note")
    y -= 0.16 * inch
    pdf.setFont("Helvetica", 9)

    rows = attendance_rows or []
    if not rows:
        pdf.drawString(left, y, "No attendance recorded.")
        y -= 0.16 * inch
    else:
        # Sort by member id for clean sheet
        def _mid(r):
            try:
                return int(r.get("legacy_member_id") or 0)
            except Exception:
                return 0

        rows_sorted = sorted(rows, key=_mid)

        for r in rows_sorted:
            if y < 1.0 * inch:
                pdf.showPage()
                y = height - 1.0 * inch
                pdf.setFont("Helvetica-Bold", 9)
                pdf.drawString(left, y, "ID")
                pdf.drawString(left + 0.7 * inch, y, "Member")
                pdf.drawString(left + 3.8 * inch, y, "Status")
                pdf.drawString(left + 4.9 * inch, y, "Note")
                y -= 0.16 * inch
                pdf.setFont("Helvetica", 9)

            mid = str(r.get("legacy_member_id") or "")
            mname = str(r.get("member_name") or "")[:35]
            status = str(r.get("status") or "")[:10]
            note = str(r.get("note") or "")[:35]

            pdf.drawString(left, y, mid)
            pdf.drawString(left + 0.7 * inch, y, mname)
            pdf.drawString(left + 3.8 * inch, y, status)
            pdf.drawString(left + 4.9 * inch, y, note)
            y -= 0.14 * inch

    pdf.showPage()
    pdf.save()
    buf.seek(0)
    return buf.getvalue()
