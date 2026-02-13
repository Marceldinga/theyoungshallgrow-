
# dashboard_panel.py ✅ COMPLETE SINGLE CODE (NO SQL) — NJANGI STANDARD (NO "legacy")
# ------------------------------------------------------------------------------
# ✅ Mobile-speed update (Railway / Supabase friendly)
# ✅ Uses sb_service for reads when available (RLS-safe), sb_anon fallback
# ✅ Auto-refresh on app_state stamp change (includes updated_at)
# ✅ Filters by session_id at the API level (NO "download all then filter")
# ✅ Lower row limits (fast)
# ✅ Attendance PDF generated ON DEMAND (button) to avoid heavy reruns
# ✅ Supports members.display_name OPTIONAL (fallback to id,name,phone)
# ✅ Supports sessions.session_id OR sessions.id (fallback)
# ✅ Removes Attendance chart entirely; keeps PDF + preview
# ✅ Fixes Streamlit params: use_container_width=True (no width="stretch")
# ✅ FIXED interest_ledger: works with (amount only) OR (interest_month) OR (created_at)
# ------------------------------------------------------------------------------
# TABLES (NEW ONLY):
#   app_state, sessions, members, contributions, foundation_contributions,
#   loans, loan_payments, interest_ledger, payouts, fines, attendance
#   v_next_beneficiary (optional)
# ------------------------------------------------------------------------------

from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any

import pandas as pd
import streamlit as st

# PDF
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

DUE_DAYS = 28


# ============================================================
# THEME
# ============================================================
def inject_dashboard_theme():
    st.markdown(
        """
        <style>
        .stApp {
            background-color: #0b0f1a;
            background-image:
                radial-gradient(circle at 1px 1px, rgba(255,255,255,0.06) 1px, transparent 0);
            background-size: 24px 24px;
            color: #e5e7eb;
        }
        section[data-testid="stSidebar"]{
            background: #0b0f1a;
            border-right: 1px solid rgba(255,255,255,0.06);
        }
        header, footer { background: transparent !important; }
        h1, h2, h3, h4, h5, h6, p, div, span, label { color: #e5e7eb; }

        .glass {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 18px;
            padding: 18px 18px;
            box-shadow: 0 14px 45px rgba(0,0,0,0.45);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
        }

        .kpi {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 16px;
            padding: 14px 16px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.35);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
        }

        .kpi-label {
            font-size: 12px;
            letter-spacing: 0.10em;
            text-transform: uppercase;
            opacity: 0.7;
        }
        .kpi-value {
            font-size: 28px;
            font-weight: 750;
            margin-top: 8px;
            line-height: 1.1;
        }
        .kpi-sub {
            margin-top: 6px;
            font-size: 12px;
            opacity: 0.65;
            word-break: break-word;
        }

        .blue { color: #60a5fa; }
        .green { color: #34d399; }
        .purple { color: #a78bfa; }
        .orange { color: #fb923c; }
        .red { color: #f87171; }

        div[data-testid="stDataFrame"]{
            border-radius: 14px;
            overflow: hidden;
            border: 1px solid rgba(255,255,255,0.06);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def kpi_card(label: str, value: str, color: str = "blue", sub: str | None = None) -> str:
    sub_html = f"<div class='kpi-sub'>{sub}</div>" if sub else ""
    return f"""
    <div class="kpi">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value {color}">{value}</div>
        {sub_html}
    </div>
    """


def glass_open() -> str:
    return "<div class='glass'>"


def glass_close() -> str:
    return "</div>"


# ============================================================
# SAFE HELPERS (NO RAW SQL) — FAST + FILTERED
# ============================================================
def safe_table(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int | None = 2000,
    order_by: str | None = None,
    desc: bool = True,
    **eq_filters,
) -> list[dict]:
    """Safe select with optional equality filters (PostgREST .eq())."""
    try:
        q = sb.schema(schema).table(table).select(cols)
        for k, v in (eq_filters or {}).items():
            if v is None:
                continue
            q = q.eq(k, v)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit is not None:
            q = q.limit(int(limit))
        res = q.execute()
        return res.data or []
    except Exception:
        return []


def safe_table_order_fallback(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int | None = 2000,
    order_candidates: list[str] | None = None,
    desc: bool = True,
    **eq_filters,
) -> list[dict]:
    """Try ordering by several candidate columns; if a column doesn't exist, keep trying."""
    order_candidates = order_candidates or []
    for c in order_candidates:
        rows = safe_table(
            sb,
            schema,
            table,
            cols=cols,
            limit=limit,
            order_by=c,
            desc=desc,
            **eq_filters,
        )
        if rows != []:
            return rows
    return safe_table(sb, schema, table, cols=cols, limit=limit, order_by=None, desc=desc, **eq_filters)


def safe_single(sb, schema: str, table: str, cols: str = "*", **eq_filters) -> dict:
    try:
        q = sb.schema(schema).table(table).select(cols)
        for k, v in (eq_filters or {}).items():
            q = q.eq(k, v)
        q = q.limit(1)
        rows = q.execute().data or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def _num(x, default=0.0) -> float:
    try:
        if x is None or x == "":
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _fmt_money(x, decimals: int = 0) -> str:
    try:
        v = float(x)
        if decimals == 0:
            return f"{v:,.0f}"
        return f"{v:,.{decimals}f}"
    except Exception:
        return "—"


def _to_date(x) -> date | None:
    if x is None:
        return None
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    if isinstance(x, datetime):
        return x.date()
    s = str(x).strip()
    if not s:
        return None
    if "T" in s:
        s = s.split("T")[0]
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def _table_exists(sb, schema: str, table: str) -> bool:
    try:
        sb.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


# ============================================================
# AUTO-REFRESH ON app_state CHANGE (includes updated_at)
# ============================================================
def _read_state_stamp(sb, schema: str) -> str:
    r = safe_single(sb, schema, "app_state", "*", id=1) or {}
    return "|".join(
        [
            str(r.get("current_session_id") or ""),
            str(r.get("next_member_id") or ""),
            str(r.get("next_payout_date") or ""),
            str(r.get("updated_at") or ""),
        ]
    )


def _auto_refresh_if_state_changed(sb, schema: str):
    stamp = _read_state_stamp(sb, schema)
    prev = st.session_state.get("_state_stamp_dashboard")
    st.session_state["_state_stamp_dashboard"] = stamp
    if prev is None:
        return
    if stamp != prev:
        try:
            st.cache_data.clear()
            st.cache_resource.clear()
        except Exception:
            pass
        st.rerun()


# ============================================================
# KPI COMPUTATIONS (LIMITED READS)
# ============================================================
def compute_interest_ledger(sb, schema: str, limit: int = 2000) -> tuple[float, float]:
    """
    ✅ Works for any of these schemas:
      - interest_ledger(amount, created_at)
      - interest_ledger(amount, interest_month='YYYY-MM')
      - interest_ledger(interest_amount, created_at)
      - interest_ledger(interest_amount, interest_month)

    ✅ If your table ONLY has (amount) and no dates:
       - All-time will still work
       - This-month will be 0 (because there is no way to know the month)
    """
    if not _table_exists(sb, schema, "interest_ledger"):
        return 0.0, 0.0

    rows = safe_table_order_fallback(
        sb,
        schema,
        "interest_ledger",
        "amount,interest_amount,interest_month,created_at,id",
        limit=limit,
        order_candidates=["created_at", "interest_month", "id"],
        desc=True,
    )
    if not rows:
        return 0.0, 0.0

    month_prefix = date.today().strftime("%Y-%m")
    this_month = 0.0
    all_time = 0.0

    for r in rows:
        if "amount" in r and r.get("amount") is not None:
            v = _num(r.get("amount"), 0.0)
        else:
            v = _num(r.get("interest_amount"), 0.0)

        all_time += v

        im = str(r.get("interest_month") or "").strip()
        if im:
            if im.startswith(month_prefix):
                this_month += v
            continue

        d = _to_date(r.get("created_at"))
        if d and d.strftime("%Y-%m") == month_prefix:
            this_month += v

    return float(this_month), float(all_time)


def compute_loan_payments(sb, schema: str, limit: int = 2000) -> tuple[float, dict[int, date]]:
    if not _table_exists(sb, schema, "loan_payments"):
        return 0.0, {}

    rows = safe_table_order_fallback(
        sb,
        schema,
        "loan_payments",
        "loan_id,amount,paid_at,created_at,id",
        limit=limit,
        order_candidates=["paid_at", "created_at", "id"],
        desc=True,
    )
    total = 0.0
    last_by_loan: dict[int, date] = {}
    for r in rows or []:
        total += _num(r.get("amount"), 0.0)
        try:
            lid = int(r.get("loan_id"))
        except Exception:
            continue
        d = _to_date(r.get("paid_at") or r.get("created_at"))
        if d is not None:
            if lid not in last_by_loan or d > last_by_loan[lid]:
                last_by_loan[lid] = d
    return float(total), last_by_loan


def compute_loans_kpis(sb, schema: str, limit: int = 2000) -> dict[str, Any]:
    if not _table_exists(sb, schema, "loans"):
        return {"active_loans": 0, "principal_active": 0.0, "total_due_active": 0.0}

    rows = safe_table(sb, schema, "loans", "*", limit=limit)
    active = 0
    principal_sum = 0.0
    total_due_sum = 0.0

    for r in rows or []:
        status = str(r.get("status") or "").lower().strip()
        if status not in ("active", "open"):
            continue

        active += 1
        pc = _num(r.get("principal_current") or r.get("principal"), 0.0)
        principal_sum += pc

        td = _num(r.get("total_due"), pc + _num(r.get("unpaid_interest"), 0.0))
        total_due_sum += td

    return {
        "active_loans": int(active),
        "principal_active": float(principal_sum),
        "total_due_active": float(total_due_sum),
    }


def sum_table_amount(
    sb,
    schema: str,
    table: str,
    amount_cols: list[str],
    limit: int = 2000,
    **eq_filters,
) -> float:
    if not _table_exists(sb, schema, table):
        return 0.0
    rows = safe_table(sb, schema, table, "*", limit=limit, **eq_filters)
    total = 0.0
    for r in rows or []:
        val = None
        for c in amount_cols:
            if c in r:
                val = r.get(c)
                break
        total += _num(val, 0.0)
    return float(total)


def compute_fines_paid_total(sb, schema: str, limit: int = 2000) -> float:
    if not _table_exists(sb, schema, "fines"):
        return 0.0

    rows = safe_table_order_fallback(
        sb,
        schema,
        "fines",
        "amount,status,paid_at,created_at,id",
        limit=limit,
        order_candidates=["paid_at", "created_at", "id"],
        desc=True,
    )
    total = 0.0
    for r in rows or []:
        status = str(r.get("status") or "").lower().strip()
        if status != "paid":
            continue
        total += _num(r.get("amount"), 0.0)
    return float(total)


def get_session_window(sb, schema: str, session_id: int) -> str:
    if not session_id:
        return "—"
    srow = safe_single(sb, schema, "sessions", "*", session_id=int(session_id))
    if not srow:
        srow = safe_single(sb, schema, "sessions", "*", id=int(session_id))
    sd = srow.get("start_date")
    ed = srow.get("end_date")
    if sd and ed:
        return f"{sd} → {ed}"
    return "—"


def build_repayment_plan(sb, schema: str, last_payment_dates: dict[int, date], limit: int = 2000) -> pd.DataFrame:
    loans = safe_table(sb, schema, "loans", "*", limit=limit)
    out: list[dict[str, Any]] = []

    for r in loans or []:
        status = str(r.get("status") or "").lower().strip()
        if status not in ("active", "open"):
            continue

        try:
            lid = int(r.get("id"))
        except Exception:
            continue

        principal = _num(r.get("principal_current") or r.get("principal"), 0.0)
        unpaid_interest = _num(r.get("unpaid_interest"), 0.0)
        total_due = _num(r.get("total_due"), principal + unpaid_interest)
        borrow_date = _to_date(r.get("borrow_date")) or _to_date(r.get("created_at")) or date.today()

        if lid in last_payment_dates:
            last_paid = last_payment_dates[lid]
            next_due = last_paid + timedelta(days=DUE_DAYS)
        else:
            last_paid = None
            next_due = borrow_date + timedelta(days=DUE_DAYS)

        out.append(
            {
                "loan_id": lid,
                "member_id": r.get("member_id"),
                "principal_current": principal,
                "unpaid_interest": unpaid_interest,
                "total_due": total_due,
                "last_paid": last_paid.isoformat() if isinstance(last_paid, date) else "—",
                "next_due_date": next_due.isoformat(),
            }
        )

    df = pd.DataFrame(out)
    if df.empty:
        return df
    return df.sort_values("next_due_date", ascending=True)


# ============================================================
# ATTENDANCE PDF EXPORT (NO SQL) — ON DEMAND
# ============================================================
def _member_name_map(members_rows: list[dict[str, Any]]) -> dict[int, str]:
    out: dict[int, str] = {}
    for m in members_rows or []:
        try:
            mid = int(m.get("id"))
        except Exception:
            continue
        dn = str(m.get("display_name") or "").strip()
        nm = str(m.get("name") or "").strip()
        out[mid] = dn or nm or f"Member {mid:02d}"
    return out


def _dedupe_attendance_latest(att_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not att_rows:
        return []
    df = pd.DataFrame(att_rows)
    if df.empty:
        return []

    for col in ("member_id", "session_id", "id"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "created_at" in df.columns:
        df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    else:
        df["created_at"] = pd.NaT

    sort_cols = []
    if "created_at" in df.columns:
        sort_cols.append("created_at")
    if "id" in df.columns:
        sort_cols.append("id")
    if sort_cols:
        df = df.sort_values(sort_cols, ascending=[False] * len(sort_cols))

    if "member_id" in df.columns and "session_id" in df.columns:
        df = df.drop_duplicates(subset=["member_id", "session_id"], keep="first")

    return df.to_dict("records")


def build_attendance_df(
    sb,
    schema: str,
    session_id: int,
    members_rows: list[dict[str, Any]],
    limit: int = 2000,
) -> pd.DataFrame:
    if not session_id or not _table_exists(sb, schema, "attendance"):
        return pd.DataFrame()

    att_rows = safe_table_order_fallback(
        sb,
        schema,
        "attendance",
        "*",
        limit=limit,
        order_candidates=["created_at", "id"],
        desc=True,
        session_id=int(session_id),
    )
    if not att_rows:
        return pd.DataFrame()

    filtered = _dedupe_attendance_latest(att_rows)
    if not filtered:
        return pd.DataFrame()

    name_map = _member_name_map(members_rows)

    out_rows = []
    for r in filtered:
        try:
            mid = int(r.get("member_id"))
        except Exception:
            continue
        present = r.get("present")
        p = str(present).lower().strip() in ("true", "1", "yes") if not isinstance(present, bool) else bool(present)
        note = str(r.get("note") or "").strip()
        created_at = str(r.get("created_at") or "").strip()
        out_rows.append(
            {
                "member_id": mid,
                "member_name": name_map.get(mid, f"Member {mid:02d}"),
                "status": "Present" if p else "Absent",
                "note": note,
                "recorded_at": created_at or "—",
            }
        )

    df = pd.DataFrame(out_rows)
    if df.empty:
        return df

    df["member_id"] = pd.to_numeric(df["member_id"], errors="coerce").fillna(0).astype(int)
    df = df.sort_values("member_id", ascending=True).reset_index(drop=True)
    return df


def _pdf_draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float, max_width: float, line_height: float = 12) -> float:
    if not text:
        return y
    words = text.split()
    line = ""
    for w in words:
        test = (line + " " + w).strip()
        if c.stringWidth(test, "Helvetica", 10) <= max_width:
            line = test
        else:
            c.setFont("Helvetica", 10)
            c.drawString(x, y, line)
            y -= line_height
            line = w
    if line:
        c.setFont("Helvetica", 10)
        c.drawString(x, y, line)
        y -= line_height
    return y


def generate_attendance_pdf_bytes(
    session_id: int,
    session_window: str,
    df: pd.DataFrame,
    total_members: int,
) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER

    margin = 0.75 * inch
    x = margin
    y = height - margin

    c.setFont("Helvetica-Bold", 16)
    c.drawString(x, y, "Attendance Report")
    y -= 18

    c.setFont("Helvetica", 11)
    c.drawString(x, y, f"Session ID: {session_id}")
    y -= 14
    c.drawString(x, y, f"Session Window: {session_window}")
    y -= 14
    c.drawString(x, y, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    y -= 18

    if df.empty:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(x, y, "No attendance records found for this session.")
        c.showPage()
        c.save()
        return buf.getvalue()

    present_count = int((df["status"] == "Present").sum())
    absent_count = int((df["status"] == "Absent").sum())
    recorded = int(len(df))
    denom = total_members if total_members > 0 else recorded
    rate = (present_count / denom * 100.0) if denom > 0 else 0.0

    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, "Summary")
    y -= 14

    c.setFont("Helvetica", 11)
    c.drawString(x, y, f"Total Members (registry): {total_members}")
    y -= 13
    c.drawString(x, y, f"Attendance Records (session): {recorded}")
    y -= 13
    c.drawString(x, y, f"Present: {present_count}")
    y -= 13
    c.drawString(x, y, f"Absent: {absent_count}")
    y -= 13
    c.drawString(x, y, f"Attendance Rate: {rate:.1f}% (Present / Total Members)")
    y -= 18

    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, "Details")
    y -= 14

    col1 = x
    col2 = x + 1.2 * inch
    col3 = x + 3.4 * inch
    col4 = x + 4.6 * inch
    max_note_w = (width - margin) - col4

    c.setFont("Helvetica-Bold", 10)
    c.drawString(col1, y, "ID")
    c.drawString(col2, y, "Name")
    c.drawString(col3, y, "Status")
    c.drawString(col4, y, "Note")
    y -= 10
    c.line(x, y, width - margin, y)
    y -= 12

    c.setFont("Helvetica", 10)

    for _, r in df.iterrows():
        if y < margin + 80:
            c.showPage()
            y = height - margin
            c.setFont("Helvetica-Bold", 10)
            c.drawString(col1, y, "ID")
            c.drawString(col2, y, "Name")
            c.drawString(col3, y, "Status")
            c.drawString(col4, y, "Note")
            y -= 10
            c.line(x, y, width - margin, y)
            y -= 12
            c.setFont("Helvetica", 10)

        mid = str(r.get("member_id") or "")
        name = str(r.get("member_name") or "")
        status = str(r.get("status") or "")
        note = str(r.get("note") or "")

        c.drawString(col1, y, mid)
        c.drawString(col2, y, name[:28])
        c.drawString(col3, y, status)

        y_note_start = y
        y_after = _pdf_draw_wrapped(c, note, col4, y_note_start, max_width=max_note_w, line_height=11)
        y = min(y_note_start - 14, y_after - 2)

    y -= 10
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(x, max(margin - 10, 20), "theyoungshallgrow • Attendance PDF")

    c.save()
    return buf.getvalue()


# ============================================================
# DASHBOARD
# ============================================================
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    inject_dashboard_theme()

    read_sb = sb_service if sb_service is not None else sb_anon
    finance_sb = sb_service if sb_service is not None else sb_anon

    _auto_refresh_if_state_changed(read_sb, schema)

    # --- App state ---
    state = safe_single(read_sb, schema, "app_state", "*", id=1)
    if not state:
        rows = safe_table(read_sb, schema, "app_state", "*", limit=1)
        state = rows[0] if rows else {}

    # --- Current session id ---
    raw_cs = state.get("current_session_id")
    try:
        current_session_id = int(raw_cs) if raw_cs is not None and str(raw_cs).strip() != "" else None
    except Exception:
        current_session_id = None

    # Fallback: latest session (supports session_id OR id)
    if current_session_id is None:
        srows = safe_table_order_fallback(
            read_sb,
            schema,
            "sessions",
            "id,session_id,start_date,end_date,created_at",
            limit=1,
            order_candidates=["session_id", "id", "start_date", "created_at"],
            desc=True,
        )
        if srows:
            sid = srows[0].get("session_id", None)
            if sid is None:
                sid = srows[0].get("id", None)
            try:
                current_session_id = int(sid) if sid is not None else None
            except Exception:
                current_session_id = None

    session_note = "from app_state" if state.get("current_session_id") else "fallback: latest session"
    next_member_id = state.get("next_member_id")

    # --- Members (display_name optional) ---
    members_rows = safe_table(
        read_sb,
        schema,
        "members",
        "id,name,display_name,phone",
        limit=5000,
        order_by="id",
        desc=False,
    )
    if not members_rows:
        members_rows = safe_table(read_sb, schema, "members", "id,name,phone", limit=5000, order_by="id", desc=False)

    seen_ids = set()
    dedup_members = []
    for m in members_rows or []:
        mid = m.get("id")
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        dedup_members.append(m)
    members_rows = dedup_members
    total_members = int(len(members_rows or []))

    # --- Beneficiary ---
    beneficiary_name = "—"
    beneficiary_id = next_member_id

    v = safe_single(read_sb, schema, "v_next_beneficiary", "*")
    if v:
        beneficiary_name = str(v.get("beneficiary_name") or v.get("member_name") or "—")
        beneficiary_id = v.get("beneficiary_id") or v.get("member_id") or beneficiary_id
    else:
        try:
            bid = int(beneficiary_id) if beneficiary_id is not None else None
        except Exception:
            bid = None
        if bid is not None:
            for m in members_rows or []:
                if str(m.get("id")) == str(bid):
                    dn = str(m.get("display_name") or "").strip()
                    nm = str(m.get("name") or "").strip()
                    beneficiary_name = dn or nm or f"Member {bid:02d}"
                    break

    window = "—"
    if isinstance(current_session_id, int) and current_session_id > 0:
        window = get_session_window(read_sb, schema, int(current_session_id))

    # --- Current pot (filtered by session) ---
    pot = 0.0
    members_paid = 0
    if isinstance(current_session_id, int) and current_session_id > 0:
        crows = safe_table(
            finance_sb,
            schema,
            "contributions",
            "member_id,session_id,amount,paid_at,created_at",
            limit=2000,
            order_by="paid_at",
            desc=True,
            session_id=int(current_session_id),
        )
        if crows:
            dfc = pd.DataFrame(crows)
            dfc["amount"] = pd.to_numeric(dfc.get("amount"), errors="coerce").fillna(0.0)
            pot = float(dfc["amount"].sum())
            members_paid = int(dfc["member_id"].nunique()) if "member_id" in dfc.columns else 0

    # --- Top KPIs ---
    st.markdown(glass_open(), unsafe_allow_html=True)
    a1, a2, a3, a4 = st.columns(4)
    with a1:
        st.markdown(kpi_card("Session ID", str(current_session_id or "—"), "blue", sub=session_note), unsafe_allow_html=True)
    with a2:
        st.markdown(kpi_card("Session Window", window, "orange"), unsafe_allow_html=True)
    with a3:
        st.markdown(kpi_card("Total Members", str(total_members), "purple", sub="members"), unsafe_allow_html=True)
    with a4:
        st.markdown(
            kpi_card(
                "Current Beneficiary",
                beneficiary_name,
                "green",
                sub=f"member_id: {beneficiary_id if beneficiary_id is not None else '—'}",
            ),
            unsafe_allow_html=True,
        )
    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # --- Cycle KPIs ---
    st.markdown(glass_open(), unsafe_allow_html=True)
    p1, p2, p3 = st.columns(3)
    with p1:
        st.markdown(kpi_card("Current Pot", _fmt_money(pot, 0), "green"), unsafe_allow_html=True)
    with p2:
        st.markdown(kpi_card("Cycle Contributions", _fmt_money(pot, 0), "blue"), unsafe_allow_html=True)
    with p3:
        st.markdown(kpi_card("Members Paid", str(members_paid), "purple"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # ============================================================
    # Attendance PDF Download (ON DEMAND)
    # ============================================================
    st.markdown("### 🧾 Attendance PDF")
    st.caption("Preview current session attendance and generate PDF on demand (fast).")

    if not (isinstance(current_session_id, int) and current_session_id > 0):
        st.info("No current_session_id available yet.")
        att_df = pd.DataFrame()
    else:
        att_df = build_attendance_df(read_sb, schema, int(current_session_id), members_rows, limit=2000)

        if att_df.empty:
            st.warning("No attendance recorded for this session yet.")
        else:
            st.markdown(glass_open(), unsafe_allow_html=True)
            st.dataframe(att_df, use_container_width=True, hide_index=True)
            st.markdown(glass_close(), unsafe_allow_html=True)

            if st.button("🧾 Generate Attendance PDF", use_container_width=True):
                pdf_bytes = generate_attendance_pdf_bytes(
                    session_id=int(current_session_id),
                    session_window=window,
                    df=att_df,
                    total_members=total_members,
                )
                fname = f"attendance_session_{int(current_session_id)}.pdf"
                st.download_button(
                    "⬇️ Download Attendance PDF",
                    data=pdf_bytes,
                    file_name=fname,
                    mime="application/pdf",
                    use_container_width=True,
                )

    st.divider()

    # --- Financial Totals ---
    foundation_total = sum_table_amount(finance_sb, schema, "foundation_contributions", ["amount"], limit=2000)
    payouts_total = sum_table_amount(finance_sb, schema, "payouts", ["payout_amount", "amount"], limit=2000)  # informational only
    interest_this_month, interest_all_time = compute_interest_ledger(finance_sb, schema, limit=2000)
    repayments_total, last_payment_dates = compute_loan_payments(finance_sb, schema, limit=2000)
    fines_paid_total = compute_fines_paid_total(finance_sb, schema, limit=2000)
    loan_kpis = compute_loans_kpis(finance_sb, schema, limit=2000)
    loans_outstanding = float(loan_kpis.get("principal_active", 0.0))

    cash_available_raw = foundation_total + repayments_total + interest_all_time + fines_paid_total - loans_outstanding
    cash_available = max(cash_available_raw, 0.0)
    net_available = cash_available + float(pot)

    st.markdown("### 🏦 Financial Summary")

    st.markdown(glass_open(), unsafe_allow_html=True)
    f1, f2, f3, f4, f5, f6, f7, f8, f9 = st.columns(9)
    with f1:
        st.markdown(kpi_card("Foundation Total", _fmt_money(foundation_total, 0), "blue", sub="foundation_contributions"), unsafe_allow_html=True)
    with f2:
        st.markdown(kpi_card("Payouts Total", _fmt_money(payouts_total, 0), "orange", sub="pot redistribution (info)"), unsafe_allow_html=True)
    with f3:
        st.markdown(kpi_card("Interest This Month", _fmt_money(interest_this_month, 2), "green", sub="interest_ledger (this month)"), unsafe_allow_html=True)
    with f4:
        st.markdown(kpi_card("Interest All-time", _fmt_money(interest_all_time, 2), "green", sub="interest_ledger (all rows)"), unsafe_allow_html=True)
    with f5:
        st.markdown(kpi_card("Loan Payments", _fmt_money(repayments_total, 0), "green", sub="loan_payments"), unsafe_allow_html=True)
    with f6:
        st.markdown(kpi_card("Total Fines Paid", _fmt_money(fines_paid_total, 0), "purple", sub="fines.status='paid'"), unsafe_allow_html=True)
    with f7:
        st.markdown(kpi_card("Outstanding Principal", _fmt_money(loans_outstanding, 0), "red", sub="loans principal_current"), unsafe_allow_html=True)
    with f8:
        st.markdown(kpi_card("Cash Available", _fmt_money(cash_available, 0), "green", sub="foundation + payments + interest + fines − principal"), unsafe_allow_html=True)
    with f9:
        st.markdown(kpi_card("Net Available", _fmt_money(net_available, 0), "blue", sub="Cash Available + Current Pot"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    if cash_available_raw < 0:
        st.warning(
            f"⚠️ Cash Available RAW is negative ({cash_available_raw:,.0f}) before flooring to 0. "
            "Outstanding loans exceed foundation cash-in."
        )

    st.divider()

    # --- Loans ---
    st.markdown("### 💳 Loans")

    st.markdown(glass_open(), unsafe_allow_html=True)
    l1, l2, l3 = st.columns(3)
    with l1:
        st.markdown(kpi_card("Active Loans", str(int(loan_kpis.get("active_loans", 0))), "purple"), unsafe_allow_html=True)
    with l2:
        st.markdown(kpi_card("Principal Current", _fmt_money(loan_kpis.get("principal_active", 0.0), 0), "orange"), unsafe_allow_html=True)
    with l3:
        st.markdown(kpi_card("Total Due", _fmt_money(loan_kpis.get("total_due_active", 0.0), 0), "red"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    plan_df = build_repayment_plan(finance_sb, schema, last_payment_dates, limit=2000)
    if plan_df.empty:
        st.info("No active/open loans found.")
    else:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.markdown("#### 🗓️ Loan Repayment Plan")
        st.caption(f"Due = {DUE_DAYS} days from last payment (or borrow_date if never paid).")
        st.dataframe(plan_df, use_container_width=True, hide_index=True)
        st.markdown(glass_close(), unsafe_allow_html=True)

    # --- Debug ---
    with st.expander("🔎 Debug", expanded=False):
        st.write("Using read client:", "service" if sb_service is not None else "anon")
        st.write("app_state", state)
        st.write("session_note", session_note)
        st.write("current_session_id", current_session_id)
        st.write("next_member_id", next_member_id)
        st.write("beneficiary_id", beneficiary_id)
        st.write("beneficiary_name", beneficiary_name)
        st.write("current_pot", pot)
        st.write("members_paid", members_paid)
        st.write("total_members", total_members)
        st.write("foundation_total", foundation_total)
        st.write("payouts_total (informational)", payouts_total)
        st.write("interest_this_month", interest_this_month)
        st.write("interest_all_time", interest_all_time)
        st.write("loan_payments_total", repayments_total)
        st.write("fines_paid_total", fines_paid_total)
        st.write("loan_kpis", loan_kpis)
        st.write("cash_available_raw", cash_available_raw)
        st.write("cash_available (floored)", cash_available)
        st.write("net_available", net_available)


# ============================================================
# COMPATIBILITY EXPORTS (prevents import errors)
# ============================================================
def render_dashbaord(sb_anon, sb_service, schema: str = "public"):
    return render_dashboard(sb_anon, sb_service, schema=schema)


__all__ = ["render_dashboard", "render_dashbaord"]
