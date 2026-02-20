# dashboard_panel.py ✅ BEST MODERN DASHBOARD — NJANGI STANDARD (NO legacy, NO SQL)
# ------------------------------------------------------------------------------
# ✅ Modern "Banking-grade" dashboard UI (2025+):
#   - Modern HERO header + KPI grid + alert chips + sections
#   - Mobile friendly (Railway / Streamlit Cloud)
#   - Fast Mode + Slow Mode aware (respects app.py throttle externally)
#   - Uses sb_service for reads when available (RLS-safe), sb_anon fallback
#
# ✅ Performance:
#   - API-level session filters (no "download all then filter")
#   - Low limits, progressive loading, cached reads (TTL)
#   - Attendance PDF generated ON DEMAND
#   - Optional "Refresh snapshot" button clears cache
#
# ✅ Data safety / schema-safe:
#   - members.display_name optional
#   - sessions.session_id OR sessions.id
#   - interest_ledger: NO aggregates (PGRST123 safe), Python sums
#   - payouts: payout_amount/payout_date or amount/created_at
#   - loan_payments: uses paid_at or created_at
#
# TABLES (NEW ONLY):
#   app_state, sessions, members, contributions, foundation_contributions,
#   loans, loan_payments, interest_ledger, payouts, fines, attendance
#   v_next_beneficiary (optional)
# ------------------------------------------------------------------------------

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Optional

import pandas as pd
import streamlit as st

# PDF
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

# =========================
# CONSTANTS
# =========================
W_STRETCH = "stretch"
DUE_DAYS = 28

# cache TTLs (fast enough + stable)
TTL_STATE = 12
TTL_SMALL = 25
TTL_MED = 45
TTL_BIG = 75


# ============================================================
# THEME (inherits app.py theme if present; dashboard adds components only)
# ============================================================
def inject_dashboard_theme():
    st.markdown(
        """
        <style>
        /* --- dashboard scoped helpers --- */
        .tysg-hero {
            border-radius: 22px;
            padding: 18px 18px;
            border: 1px solid rgba(255,255,255,0.10);
            background:
                radial-gradient(900px 520px at 10% 0%, rgba(0,230,168,0.15), transparent 55%),
                radial-gradient(800px 500px at 85% 10%, rgba(96,165,250,0.12), transparent 55%),
                rgba(255,255,255,0.05);
            box-shadow: 0 16px 55px rgba(0,0,0,0.45);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
        }
        .tysg-sub { opacity: 0.75; font-size: 12px; }

        .tysg-kpi {
            border-radius: 18px;
            padding: 14px 14px;
            border: 1px solid rgba(255,255,255,0.10);
            background: rgba(255,255,255,0.05);
            box-shadow: 0 12px 36px rgba(0,0,0,0.35);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            height: 100%;
        }
        .tysg-kpi-label {
            font-size: 11px;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            opacity: 0.70;
        }
        .tysg-kpi-value {
            font-size: 28px;
            font-weight: 800;
            margin-top: 8px;
            line-height: 1.08;
        }
        .tysg-kpi-sub {
            margin-top: 6px;
            font-size: 12px;
            opacity: 0.65;
            word-break: break-word;
        }

        .chip {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 10px;
            border-radius: 999px;
            border: 1px solid rgba(255,255,255,0.12);
            background: rgba(255,255,255,0.05);
            font-size: 12px;
            opacity: 0.92;
        }
        .chip-dot { width: 8px; height: 8px; border-radius: 999px; background: rgba(0,230,168,0.9); }

        .glass {
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 20px;
            padding: 16px 16px;
            box-shadow: 0 16px 55px rgba(0,0,0,0.40);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
        }

        div[data-testid="stDataFrame"]{
            border-radius: 16px !important;
            overflow: hidden !important;
            border: 1px solid rgba(255,255,255,0.10) !important;
            background: rgba(255,255,255,0.03) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _kpi_card(label: str, value: str, color_css: str, sub: str | None = None) -> str:
    sub_html = f"<div class='tysg-kpi-sub'>{sub}</div>" if sub else ""
    return f"""
    <div class="tysg-kpi">
        <div class="tysg-kpi-label">{label}</div>
        <div class="tysg-kpi-value" style="color:{color_css};">{value}</div>
        {sub_html}
    </div>
    """


def glass_open() -> str:
    return "<div class='glass'>"


def glass_close() -> str:
    return "</div>"


# ============================================================
# SAFE HELPERS (NO SQL) — FAST + FILTERED
# ============================================================
def _api_msg(e: Exception) -> str:
    return repr(e)


def _table_exists(sb, schema: str, table: str) -> bool:
    try:
        sb.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def safe_table(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int | None = 2000,
    order_by: str | None = None,
    desc: bool = True,
    silent: bool = True,
    **eq_filters,
) -> list[dict]:
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
    except Exception as e:
        if not silent:
            st.error(f"Failed reading {schema}.{table}")
            st.code(_api_msg(e), language="text")
        return []


def safe_table_order_fallback(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int | None = 2000,
    order_candidates: list[str] | None = None,
    desc: bool = True,
    silent: bool = True,
    **eq_filters,
) -> list[dict]:
    order_candidates = order_candidates or []
    for c in order_candidates:
        try:
            q = sb.schema(schema).table(table).select(cols)
            for k, v in (eq_filters or {}).items():
                if v is None:
                    continue
                q = q.eq(k, v)
            if limit is not None:
                q = q.limit(int(limit))
            q = q.order(c, desc=desc)
            res = q.execute()
            return res.data or []
        except Exception:
            continue
    return safe_table(sb, schema, table, cols=cols, limit=limit, order_by=None, desc=desc, silent=silent, **eq_filters)


def safe_single(sb, schema: str, table: str, cols: str = "*", silent: bool = True, **eq_filters) -> dict:
    try:
        q = sb.schema(schema).table(table).select(cols)
        for k, v in (eq_filters or {}).items():
            q = q.eq(k, v)
        q = q.limit(1)
        rows = q.execute().data or []
        return rows[0] if rows else {}
    except Exception as e:
        if not silent:
            st.error(f"Failed reading {schema}.{table} (single)")
            st.code(_api_msg(e), language="text")
        return {}


def _num(x, default: float = 0.0) -> float:
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
# AUTO-REFRESH on app_state stamp change (includes updated_at)
# ============================================================
@st.cache_data(ttl=TTL_STATE, show_spinner=False)
def _read_state_stamp_cached(url_key: str, schema: str, sb) -> str:
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
    # cache key = schema + a stable marker; sb itself is not hashed in cache_data signature here (passed but ok)
    stamp = _read_state_stamp_cached(url_key=f"{schema}", schema=schema, sb=sb)
    prev = st.session_state.get("_state_stamp_dashboard")
    st.session_state["_state_stamp_dashboard"] = stamp
    if prev is None:
        return
    if stamp != prev:
        try:
            st.cache_data.clear()
        except Exception:
            pass
        st.rerun()


# ============================================================
# INTEREST LEDGER (NO aggregates) — PGRST123 safe
# ============================================================
def compute_interest_ledger(sb, schema: str, limit: int = 5000) -> tuple[float, float]:
    st.session_state.pop("_interest_ledger_error", None)

    if not _table_exists(sb, schema, "interest_ledger"):
        st.session_state["_interest_ledger_error"] = "interest_ledger not readable (missing table OR RLS blocks SELECT)."
        return 0.0, 0.0

    month_prefix = date.today().strftime("%Y-%m")

    cols_try = [
        "id,amount,interest_month,created_at",
        "id,interest_amount,interest_month,created_at",
        "id,amount,created_at",
        "id,interest_amount,created_at",
        "id,amount",
        "id,interest_amount",
        "*",
    ]

    rows: list[dict] = []
    last_err: str | None = None

    for cols in cols_try:
        try:
            rows = safe_table_order_fallback(
                sb,
                schema,
                "interest_ledger",
                cols,
                limit=limit,
                order_candidates=["created_at", "id"],
                desc=True,
            )
            last_err = None
            break
        except Exception as e:
            last_err = repr(e)
            rows = []

    if not rows:
        st.session_state["_interest_ledger_error"] = f"interest_ledger returned 0 rows. {('Last error: ' + last_err) if last_err else ''}"
        return 0.0, 0.0

    all_time = 0.0
    this_month = 0.0

    for r in rows:
        v = _num(r.get("amount") if r.get("amount") is not None else r.get("interest_amount"), 0.0)
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


# ============================================================
# KPIs + data builders (limited reads)
# ============================================================
def compute_loan_payments(sb, schema: str, limit: int = 2500) -> tuple[float, dict[int, date]]:
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
        if d is not None and (lid not in last_by_loan or d > last_by_loan[lid]):
            last_by_loan[lid] = d

    return float(total), last_by_loan


def compute_loans_kpis(sb, schema: str, limit: int = 2500) -> dict[str, Any]:
    if not _table_exists(sb, schema, "loans"):
        return {"active_loans": 0, "principal_active": 0.0, "total_due_active": 0.0, "overdue_active": 0}

    rows = safe_table(sb, schema, "loans", "*", limit=limit)
    active = 0
    overdue = 0
    principal_sum = 0.0
    total_due_sum = 0.0

    bad_tokens = ["delinquent", "default", "overdue", "late", "arrears", "past due", "past_due", "unpaid"]

    for r in rows or []:
        status = str(r.get("status") or "").lower().strip()
        if status in ("active", "open"):
            active += 1
            if any(t in status for t in bad_tokens):
                overdue += 1

            pc = _num(r.get("principal_current") or r.get("principal"), 0.0)
            principal_sum += pc

            td = _num(r.get("total_due"), pc + _num(r.get("unpaid_interest"), 0.0))
            total_due_sum += td

        # Some DBs store overdue in status while still active; above token handles that.

    return {
        "active_loans": int(active),
        "overdue_active": int(overdue),
        "principal_active": float(principal_sum),
        "total_due_active": float(total_due_sum),
    }


def sum_table_amount(sb, schema: str, table: str, amount_cols: list[str], limit: int = 2500, **eq_filters) -> float:
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


def compute_fines_paid_total(sb, schema: str, limit: int = 2500) -> float:
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
        if str(r.get("status") or "").lower().strip() == "paid":
            total += _num(r.get("amount"), 0.0)
    return float(total)


def get_session_window(sb, schema: str, session_id: int) -> str:
    if not session_id:
        return "—"
    srow = safe_single(sb, schema, "sessions", "*", session_id=int(session_id))
    if not srow:
        srow = safe_single(sb, schema, "sessions", "*", id=int(session_id))
    sd = srow.get("start_date") or srow.get("session_date")
    ed = srow.get("end_date")
    if sd and ed:
        return f"{sd} → {ed}"
    if sd:
        return f"{sd}"
    return "—"


def build_repayment_plan(sb, schema: str, last_payment_dates: dict[int, date], limit: int = 2500) -> pd.DataFrame:
    loans = safe_table(sb, schema, "loans", "*", limit=limit)
    out: list[dict[str, Any]] = []

    today = date.today()
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

        borrow_date = _to_date(r.get("borrow_date")) or _to_date(r.get("created_at")) or today

        if lid in last_payment_dates:
            last_paid = last_payment_dates[lid]
            next_due = last_paid + timedelta(days=DUE_DAYS)
        else:
            last_paid = None
            next_due = borrow_date + timedelta(days=DUE_DAYS)

        days_to_due = (next_due - today).days

        out.append(
            {
                "loan_id": lid,
                "member_id": r.get("member_id"),
                "principal_current": principal,
                "unpaid_interest": unpaid_interest,
                "total_due": total_due,
                "last_paid": last_paid.isoformat() if isinstance(last_paid, date) else "—",
                "next_due_date": next_due.isoformat(),
                "days_to_due": int(days_to_due),
            }
        )

    df = pd.DataFrame(out)
    if df.empty:
        return df
    return df.sort_values(["days_to_due", "next_due_date"], ascending=True).reset_index(drop=True)


# ============================================================
# Attendance helpers + PDF export (on demand)
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

    sort_cols = [c for c in ("created_at", "id") if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, ascending=[False] * len(sort_cols))

    if "member_id" in df.columns and "session_id" in df.columns:
        df = df.drop_duplicates(subset=["member_id", "session_id"], keep="first")

    return df.to_dict("records")


def build_attendance_df(sb, schema: str, session_id: int, members_rows: list[dict[str, Any]], limit: int = 2500) -> pd.DataFrame:
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
        is_present = bool(present) if isinstance(present, bool) else str(present).lower().strip() in ("true", "1", "yes")
        note = str(r.get("note") or "").strip()
        created_at = str(r.get("created_at") or "").strip()

        out_rows.append(
            {
                "member_id": mid,
                "member_name": name_map.get(mid, f"Member {mid:02d}"),
                "status": "Present" if is_present else "Absent",
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


def generate_attendance_pdf_bytes(session_id: int, session_window: str, df: pd.DataFrame, total_members: int) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    page_w, page_h = LETTER

    margin = 0.75 * inch
    x = margin
    y = page_h - margin

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
    col2 = x + 1.15 * inch
    col3 = x + 3.35 * inch
    col4 = x + 4.55 * inch
    max_note_w = (page_w - margin) - col4

    def draw_header(y0: float) -> float:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(col1, y0, "ID")
        c.drawString(col2, y0, "Name")
        c.drawString(col3, y0, "Status")
        c.drawString(col4, y0, "Note")
        y0 -= 10
        c.line(x, y0, page_w - margin, y0)
        y0 -= 12
        c.setFont("Helvetica", 10)
        return y0

    y = draw_header(y)

    for _, r in df.iterrows():
        if y < margin + 80:
            c.showPage()
            y = page_h - margin
            y = draw_header(y)

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

    c.setFont("Helvetica-Oblique", 9)
    c.drawString(x, max(margin - 10, 20), "theyoungshallgrow • Attendance PDF")

    c.save()
    return buf.getvalue()


# ============================================================
# Cached loaders (fast + safe)
# ============================================================
@st.cache_data(ttl=TTL_SMALL, show_spinner=False)
def _load_members(schema: str, sb) -> list[dict]:
    rows = safe_table(sb, schema, "members", "id,name,display_name,phone", limit=5000, order_by="id", desc=False)
    if not rows:
        rows = safe_table(sb, schema, "members", "id,name,phone", limit=5000, order_by="id", desc=False)
    # dedupe
    seen = set()
    out = []
    for r in rows or []:
        i = r.get("id")
        if i in seen:
            continue
        seen.add(i)
        out.append(r)
    return out


@st.cache_data(ttl=TTL_SMALL, show_spinner=False)
def _load_app_state(schema: str, sb) -> dict:
    stt = safe_single(sb, schema, "app_state", "*", id=1)
    if stt:
        return stt
    rows = safe_table(sb, schema, "app_state", "*", limit=1)
    return rows[0] if rows else {}


@st.cache_data(ttl=TTL_SMALL, show_spinner=False)
def _load_latest_session(schema: str, sb) -> dict:
    srows = safe_table_order_fallback(
        sb,
        schema,
        "sessions",
        "id,session_id,start_date,end_date,session_date,created_at",
        limit=1,
        order_candidates=["session_id", "id", "start_date", "session_date", "created_at"],
        desc=True,
    )
    return srows[0] if srows else {}


@st.cache_data(ttl=TTL_MED, show_spinner=False)
def _load_cycle_contribs(schema: str, sb, session_id: int) -> pd.DataFrame:
    if not session_id or not _table_exists(sb, schema, "contributions"):
        return pd.DataFrame()
    rows = safe_table(
        sb,
        schema,
        "contributions",
        "member_id,session_id,amount,paid_at,created_at",
        limit=2500,
        order_by="paid_at",
        desc=True,
        session_id=int(session_id),
    )
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=TTL_MED, show_spinner=False)
def _load_active_loans(schema: str, sb) -> pd.DataFrame:
    if not _table_exists(sb, schema, "loans"):
        return pd.DataFrame()
    # we keep limit small for speed, kpis cover sums, this table is for the "alerts" and plan
    rows = safe_table(sb, schema, "loans", "*", limit=2500)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ============================================================
# ALERTS (bank-style)
# ============================================================
def _build_alerts(*, cash_available_raw: float, pot: float, loans_kpis: dict, members_paid: int, total_members: int) -> list[dict]:
    alerts = []

    # Liquidity
    if cash_available_raw < 0:
        alerts.append({"sev": "high", "msg": "Liquidity tight: outstanding principal is greater than cash inflows."})
    elif cash_available_raw < 1000:
        alerts.append({"sev": "med", "msg": "Liquidity low: consider limiting new loan approvals."})

    # Participation
    if total_members > 0:
        rate = members_paid / max(total_members, 1)
        if rate < 0.60:
            alerts.append({"sev": "med", "msg": f"Low participation this cycle: {members_paid}/{total_members} members paid."})
        elif rate < 0.85:
            alerts.append({"sev": "low", "msg": f"Participation moderate: {members_paid}/{total_members} members paid."})

    # Loans
    overdue = int(loans_kpis.get("overdue_active", 0))
    active = int(loans_kpis.get("active_loans", 0))
    if overdue > 0:
        alerts.append({"sev": "high", "msg": f"Overdue signals detected on active loans: {overdue} flagged."})
    elif active >= 5:
        alerts.append({"sev": "low", "msg": f"{active} active loans — monitor repayment cadence."})

    # Pot health
    if pot <= 0 and total_members > 0:
        alerts.append({"sev": "low", "msg": "Current pot is 0 — confirm contributions are being recorded for this session."})

    return alerts[:6]


def _chip(sev: str, msg: str) -> str:
    dot = {"high": "#f87171", "med": "#fb923c", "low": "#60a5fa"}.get(sev, "#34d399")
    label = {"high": "HIGH", "med": "MED", "low": "INFO"}.get(sev, "INFO")
    return f"""
    <span class="chip">
        <span class="chip-dot" style="background:{dot};"></span>
        <b style="letter-spacing:0.08em;">{label}</b>
        <span style="opacity:0.85;">{msg}</span>
    </span>
    """


# ============================================================
# MAIN DASHBOARD
# ============================================================
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    inject_dashboard_theme()

    read_sb = sb_service if sb_service is not None else sb_anon
    finance_sb = sb_service if sb_service is not None else sb_anon

    _auto_refresh_if_state_changed(read_sb, schema)

    # Top controls
    topL, topR = st.columns([1, 0.22])
    with topL:
        st.markdown(
            """
            <div class="tysg-hero">
              <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-end;flex-wrap:wrap;">
                <div>
                  <div style="font-size:22px;font-weight:850;">🏦 Bank Dashboard</div>
                  <div class="tysg-sub">Modern Njangi analytics • fast snapshot • no legacy • no SQL</div>
                </div>
                <div class="tysg-sub">Status: <b style="color:#00E6A8;">LIVE</b></div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with topR:
        if st.button("🔄 Refresh snapshot", width=W_STRETCH):
            st.cache_data.clear()
            st.rerun()

    # State + session
    state = _load_app_state(schema, read_sb)
    raw_cs = state.get("current_session_id")

    try:
        current_session_id = int(raw_cs) if raw_cs is not None and str(raw_cs).strip() != "" else None
    except Exception:
        current_session_id = None

    if current_session_id is None:
        srow = _load_latest_session(schema, read_sb)
        sid = srow.get("session_id") or srow.get("id")
        try:
            current_session_id = int(sid) if sid is not None else None
        except Exception:
            current_session_id = None

    session_note = "from app_state" if state.get("current_session_id") else "fallback: latest session"
    next_member_id = state.get("next_member_id")

    # Members
    members_rows = _load_members(schema, read_sb)
    total_members = int(len(members_rows or []))

    # Beneficiary
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

    # Session window
    window = "—"
    if isinstance(current_session_id, int) and current_session_id > 0:
        window = get_session_window(read_sb, schema, int(current_session_id))

    # Contributions (cycle)
    pot = 0.0
    members_paid = 0
    if isinstance(current_session_id, int) and current_session_id > 0:
        dfc = _load_cycle_contribs(schema, finance_sb, int(current_session_id))
        if not dfc.empty:
            if "amount" in dfc.columns:
                dfc["amount"] = pd.to_numeric(dfc["amount"], errors="coerce").fillna(0.0)
                pot = float(dfc["amount"].sum())
            if "member_id" in dfc.columns:
                members_paid = int(dfc["member_id"].nunique())
    else:
        dfc = pd.DataFrame()

    # Financial totals (fast, limited)
    foundation_total = sum_table_amount(finance_sb, schema, "foundation_contributions", ["amount"], limit=2500)
    payouts_total = sum_table_amount(finance_sb, schema, "payouts", ["payout_amount", "amount"], limit=2500)

    interest_this_month, interest_all_time = compute_interest_ledger(finance_sb, schema, limit=5000)
    repayments_total, last_payment_dates = compute_loan_payments(finance_sb, schema, limit=2500)
    fines_paid_total = compute_fines_paid_total(finance_sb, schema, limit=2500)
    loan_kpis = compute_loans_kpis(finance_sb, schema, limit=2500)
    loans_outstanding = float(loan_kpis.get("principal_active", 0.0))

    cash_available_raw = foundation_total + repayments_total + interest_all_time + fines_paid_total - loans_outstanding
    cash_available = max(cash_available_raw, 0.0)
    net_available = cash_available + float(pot)

    # Alerts chips
    alerts = _build_alerts(
        cash_available_raw=cash_available_raw,
        pot=pot,
        loans_kpis=loan_kpis,
        members_paid=members_paid,
        total_members=total_members,
    )

    if alerts:
        st.markdown("#### 🚨 Live signals")
        chips_html = " ".join([_chip(a["sev"], a["msg"]) for a in alerts])
        st.markdown(chips_html, unsafe_allow_html=True)

    st.divider()

    # KPI GRID (top)
    st.markdown(glass_open(), unsafe_allow_html=True)
    r1c1, r1c2, r1c3, r1c4 = st.columns(4)
    with r1c1:
        st.markdown(_kpi_card("Session ID", str(current_session_id or "—"), "#60A5FA", sub=session_note), unsafe_allow_html=True)
    with r1c2:
        st.markdown(_kpi_card("Session Window", window, "#FB923C"), unsafe_allow_html=True)
    with r1c3:
        st.markdown(_kpi_card("Total Members", str(total_members), "#A78BFA", sub="members registry"), unsafe_allow_html=True)
    with r1c4:
        st.markdown(
            _kpi_card(
                "Current Beneficiary",
                beneficiary_name,
                "#00E6A8",
                sub=f"member_id: {beneficiary_id if beneficiary_id is not None else '—'}",
            ),
            unsafe_allow_html=True,
        )

    st.divider()
    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
    with r2c1:
        st.markdown(_kpi_card("Current Pot", _fmt_money(pot, 0), "#00E6A8", sub="cycle contributions"), unsafe_allow_html=True)
    with r2c2:
        st.markdown(_kpi_card("Members Paid", f"{members_paid}/{total_members}", "#A78BFA", sub="this cycle"), unsafe_allow_html=True)
    with r2c3:
        st.markdown(_kpi_card("Cash Available", _fmt_money(cash_available, 0), "#00E6A8", sub="cash-in minus principal"), unsafe_allow_html=True)
    with r2c4:
        st.markdown(_kpi_card("Net Available", _fmt_money(net_available, 0), "#60A5FA", sub="Cash Available + Pot"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    if cash_available_raw < 0:
        st.warning(
            f"⚠️ Cash Available RAW is negative ({cash_available_raw:,.0f}) before flooring to 0. "
            "Outstanding loans exceed inflows."
        )

    st.divider()

    # Two-column layout: Attendance + Loans overview
    left, right = st.columns([1.05, 0.95])

    with left:
        st.markdown("### 🧾 Attendance (session)")
        st.caption("Preview is light; PDF is generated only when you click the button.")

        if not (isinstance(current_session_id, int) and current_session_id > 0):
            st.info("No current_session_id available yet.")
            att_df = pd.DataFrame()
        else:
            att_df = build_attendance_df(read_sb, schema, int(current_session_id), members_rows, limit=2500)

            if att_df.empty:
                st.warning("No attendance recorded for this session yet.")
            else:
                st.markdown(glass_open(), unsafe_allow_html=True)
                st.dataframe(att_df, width=W_STRETCH, hide_index=True)
                st.markdown(glass_close(), unsafe_allow_html=True)

            if st.button("🧾 Generate Attendance PDF", width=W_STRETCH):
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
                    width=W_STRETCH,
                )

    with right:
        st.markdown("### 💳 Loans overview")
        st.caption("Quick KPIs + repayment plan based on last payment date (or borrow date).")

        st.markdown(glass_open(), unsafe_allow_html=True)
        l1, l2 = st.columns(2)
        with l1:
            st.markdown(_kpi_card("Active Loans", str(int(loan_kpis.get("active_loans", 0))), "#A78BFA"), unsafe_allow_html=True)
        with l2:
            st.markdown(_kpi_card("Total Due (active)", _fmt_money(loan_kpis.get("total_due_active", 0.0), 0), "#F87171"), unsafe_allow_html=True)

        l3, l4 = st.columns(2)
        with l3:
            st.markdown(_kpi_card("Outstanding Principal", _fmt_money(loans_outstanding, 0), "#FB923C"), unsafe_allow_html=True)
        with l4:
            st.markdown(_kpi_card("Interest (this month)", _fmt_money(interest_this_month, 2), "#00E6A8"), unsafe_allow_html=True)
        st.markdown(glass_close(), unsafe_allow_html=True)

        plan_df = build_repayment_plan(finance_sb, schema, last_payment_dates, limit=2500)
        if plan_df.empty:
            st.info("No active/open loans found.")
        else:
            st.markdown(glass_open(), unsafe_allow_html=True)
            st.markdown("#### 🗓️ Repayment plan")
            st.caption(f"Due = {DUE_DAYS} days from last payment (or borrow_date if never paid).")
            # Add simple urgency tag
            if "days_to_due" in plan_df.columns:
                plan_df2 = plan_df.copy()
                plan_df2["urgency"] = plan_df2["days_to_due"].apply(
                    lambda d: "OVERDUE" if d < 0 else ("DUE SOON" if d <= 7 else ("UPCOMING" if d <= 21 else "OK"))
                )
                plan_df2 = plan_df2[["loan_id", "member_id", "total_due", "last_paid", "next_due_date", "days_to_due", "urgency"]]
                st.dataframe(plan_df2, width=W_STRETCH, hide_index=True)
            else:
                st.dataframe(plan_df, width=W_STRETCH, hide_index=True)
            st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # Financial summary section
    st.markdown("### 🏦 Financial Summary")
    st.caption("Built from NJANGI STANDARD tables (no aggregates on PostgREST).")

    _interest_err = st.session_state.get("_interest_ledger_error")
    if _interest_err:
        st.warning("Interest might show 0 because interest_ledger isn't readable (RLS / missing table / schema mismatch).")
        st.code(_interest_err, language="text")

    st.markdown(glass_open(), unsafe_allow_html=True)
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        st.markdown(_kpi_card("Foundation Total", _fmt_money(foundation_total, 0), "#60A5FA", sub="foundation_contributions"), unsafe_allow_html=True)
    with f2:
        st.markdown(_kpi_card("Loan Payments", _fmt_money(repayments_total, 0), "#00E6A8", sub="loan_payments"), unsafe_allow_html=True)
    with f3:
        st.markdown(_kpi_card("Fines Paid", _fmt_money(fines_paid_total, 0), "#A78BFA", sub="fines.status='paid'"), unsafe_allow_html=True)
    with f4:
        st.markdown(_kpi_card("Payouts Total", _fmt_money(payouts_total, 0), "#FB923C", sub="informational"), unsafe_allow_html=True)

    st.divider()
    g1, g2, g3 = st.columns(3)
    with g1:
        st.markdown(_kpi_card("Interest (All-time)", _fmt_money(interest_all_time, 2), "#00E6A8", sub="interest_ledger"), unsafe_allow_html=True)
    with g2:
        st.markdown(_kpi_card("Cash Available", _fmt_money(cash_available, 0), "#00E6A8", sub="inflows − principal"), unsafe_allow_html=True)
    with g3:
        st.markdown(_kpi_card("Net Available", _fmt_money(net_available, 0), "#60A5FA", sub="Cash Available + Pot"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    # Optional: show top contributors this cycle (lightweight)
    st.divider()
    st.markdown("### 💰 Cycle insights")
    if dfc is None or dfc.empty:
        st.info("No cycle contributions found for this session.")
    else:
        dfc2 = dfc.copy()
        dfc2["amount"] = pd.to_numeric(dfc2.get("amount", 0), errors="coerce").fillna(0.0)
        # join member names (in python, no view required)
        name_map = _member_name_map(members_rows)
        dfc2["member_name"] = dfc2["member_id"].apply(lambda x: name_map.get(int(x), f"Member {int(x):02d}") if str(x).isdigit() else "—")

        top = (
            dfc2.groupby(["member_id", "member_name"], dropna=False)["amount"]
            .sum()
            .sort_values(ascending=False)
            .head(10)
            .reset_index()
        )

        st.markdown(glass_open(), unsafe_allow_html=True)
        c1, c2 = st.columns([0.52, 0.48])
        with c1:
            st.markdown("#### Top contributors (this session)")
            st.dataframe(top, width=W_STRETCH, hide_index=True)
        with c2:
            # simple sparkline-style chart using Streamlit native
            daily = dfc2.copy()
            # bucket by day
            dt_col = "paid_at" if "paid_at" in daily.columns else "created_at"
            daily[dt_col] = pd.to_datetime(daily.get(dt_col), errors="coerce")
            daily["day"] = daily[dt_col].dt.date
            series = daily.groupby("day")["amount"].sum().reset_index()
            if not series.empty:
                series = series.sort_values("day")
                st.markdown("#### Daily contributions")
                st.line_chart(series.set_index("day"))
            else:
                st.info("Not enough timestamped rows for daily chart.")
        st.markdown(glass_close(), unsafe_allow_html=True)

    # Debug
    with st.expander("🔎 Debug", expanded=False):
        st.write("Using read client:", "service" if sb_service is not None else "anon")
        st.write("schema:", schema)
        st.write("state:", state)
        st.write("current_session_id:", current_session_id)
        st.write("session_note:", session_note)
        st.write("next_member_id:", next_member_id)
        st.write("beneficiary_id:", beneficiary_id)
        st.write("beneficiary_name:", beneficiary_name)
        st.write("window:", window)
        st.write("pot:", pot)
        st.write("members_paid:", members_paid)
        st.write("total_members:", total_members)
        st.write("foundation_total:", foundation_total)
        st.write("payouts_total:", payouts_total)
        st.write("interest_this_month:", interest_this_month)
        st.write("interest_all_time:", interest_all_time)
        st.write("interest_ledger_error:", st.session_state.get("_interest_ledger_error"))
        st.write("loan_payments_total:", repayments_total)
        st.write("fines_paid_total:", fines_paid_total)
        st.write("loan_kpis:", loan_kpis)
        st.write("loans_outstanding:", loans_outstanding)
        st.write("cash_available_raw:", cash_available_raw)
        st.write("cash_available:", cash_available)
        st.write("net_available:", net_available)


# ============================================================
# COMPATIBILITY EXPORTS (prevents import errors)
# ============================================================
def render_dashbaord(sb_anon, sb_service, schema: str = "public"):
    return render_dashboard(sb_anon, sb_service, schema=schema)


__all__ = ["render_dashboard", "render_dashbaord"]
