# dashboard_panel.py ✅ BEST MODERN DASHBOARD + 🤖 "YOUNG" AI HELPER — NJANGI STANDARD (NO legacy, NO SQL)
# ------------------------------------------------------------------------------
# ✅ Modern "Banking-grade" dashboard UI (2025+)
# ✅ Embedded AI helper: "Young" (grounded answers from your Supabase snapshots)
# ✅ FIXED: Streamlit cache cannot hash supabase client -> cached fns use `_sb`
# ✅ NJANGI STANDARD tables only (no legacy, no SQL)
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

TTL_STATE = 12
TTL_SMALL = 25
TTL_MED = 45

# ============================================================
# THEME
# ============================================================
def inject_dashboard_theme():
    st.markdown(
        """
        <style>
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
# SAFE HELPERS
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


# ============================================================
# AUTO-REFRESH on app_state stamp change
# ✅ FIX: cached function uses `_sb` (supabase client is NOT hashed)
# ============================================================
@st.cache_data(ttl=TTL_STATE, show_spinner=False)
def _read_state_stamp_cached(url_key: str, schema: str, _sb) -> str:
    r = safe_single(_sb, schema, "app_state", "*", id=1) or {}
    return "|".join(
        [
            str(r.get("current_session_id") or ""),
            str(r.get("next_member_id") or ""),
            str(r.get("next_payout_date") or ""),
            str(r.get("updated_at") or ""),
        ]
    )


def _auto_refresh_if_state_changed(sb, schema: str):
    stamp = _read_state_stamp_cached(url_key=f"{schema}", schema=schema, _sb=sb)
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
# ATTENDANCE + PDF
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
        sb, schema, "attendance", "*", limit=limit, order_candidates=["created_at", "id"], desc=True, session_id=int(session_id)
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
        out_rows.append(
            {
                "member_id": mid,
                "member_name": name_map.get(mid, f"Member {mid:02d}"),
                "status": "Present" if is_present else "Absent",
                "note": str(r.get("note") or "").strip(),
                "recorded_at": str(r.get("created_at") or "").strip() or "—",
            }
        )
    df = pd.DataFrame(out_rows)
    if df.empty:
        return df
    df["member_id"] = pd.to_numeric(df["member_id"], errors="coerce").fillna(0).astype(int)
    return df.sort_values("member_id", ascending=True).reset_index(drop=True)


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

        c.drawString(col1, y, str(r.get("member_id") or ""))
        c.drawString(col2, y, str(r.get("member_name") or "")[:28])
        c.drawString(col3, y, str(r.get("status") or ""))

        y_note_start = y
        y_after = _pdf_draw_wrapped(c, str(r.get("note") or ""), col4, y_note_start, max_width=max_note_w, line_height=11)
        y = min(y_note_start - 14, y_after - 2)

    c.setFont("Helvetica-Oblique", 9)
    c.drawString(x, max(margin - 10, 20), "theyoungshallgrow • Attendance PDF")
    c.save()
    return buf.getvalue()


# ============================================================
# CACHED LOADERS (cache-safe: `_sb`)
# ============================================================
@st.cache_data(ttl=TTL_SMALL, show_spinner=False)
def _load_members(schema: str, _sb) -> list[dict]:
    rows = safe_table(_sb, schema, "members", "id,name,display_name,phone", limit=5000, order_by="id", desc=False)
    if not rows:
        rows = safe_table(_sb, schema, "members", "id,name,phone", limit=5000, order_by="id", desc=False)
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
def _load_app_state(schema: str, _sb) -> dict:
    stt = safe_single(_sb, schema, "app_state", "*", id=1)
    if stt:
        return stt
    rows = safe_table(_sb, schema, "app_state", "*", limit=1)
    return rows[0] if rows else {}


@st.cache_data(ttl=TTL_SMALL, show_spinner=False)
def _load_latest_session(schema: str, _sb) -> dict:
    srows = safe_table_order_fallback(
        _sb,
        schema,
        "sessions",
        "id,session_id,start_date,end_date,session_date,created_at",
        limit=1,
        order_candidates=["session_id", "id", "start_date", "session_date", "created_at"],
        desc=True,
    )
    return srows[0] if srows else {}


@st.cache_data(ttl=TTL_MED, show_spinner=False)
def _load_cycle_contribs(schema: str, _sb, session_id: int) -> pd.DataFrame:
    if not session_id or not _table_exists(_sb, schema, "contributions"):
        return pd.DataFrame()
    rows = safe_table(
        _sb,
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
def _load_loans(schema: str, _sb) -> pd.DataFrame:
    if not _table_exists(_sb, schema, "loans"):
        return pd.DataFrame()
    rows = safe_table(_sb, schema, "loans", "*", limit=2500)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=TTL_MED, show_spinner=False)
def _load_fines(schema: str, _sb) -> pd.DataFrame:
    if not _table_exists(_sb, schema, "fines"):
        return pd.DataFrame()
    rows = safe_table_order_fallback(_sb, schema, "fines", "*", limit=2500, order_candidates=["paid_at", "created_at", "id"], desc=True)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ============================================================
# "YOUNG" — GROUNDED DASHBOARD AI
# ============================================================
def _normalize_text(s: str) -> str:
    return " ".join(str(s or "").lower().strip().split())


def _money(x: float) -> str:
    try:
        return f"${float(x):,.0f}"
    except Exception:
        return "$0"


def _safe_sum_df(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())


def _assistant_intro_young() -> str:
    return (
        "Hi 👋🏾 I’m **Young** — your dashboard AI helper for **theyoungshallgrow**.\n\n"
        "✅ I can answer using **live Supabase numbers** (grounded):\n"
        "• Pot & participation this cycle\n"
        "• Loans totals + who is overdue risk\n"
        "• Fines + repayments + interest totals\n"
        "• Attendance summary\n\n"
        "Try:\n"
        "• **How much is the pot this session?**\n"
        "• **Who has active loans and how much is due?**\n"
        "• **Show fines total and repayments total**\n"
        "• **Attendance summary**\n"
    )


def _answer_young(question: str, *, session_id: int | None, members_rows: list[dict], dfc: pd.DataFrame, loans_df: pd.DataFrame, fines_df: pd.DataFrame, att_df: pd.DataFrame) -> str:
    q = question.strip()
    if not q:
        return "Type a question."

    qn = _normalize_text(q)

    # quick stats available on dashboard snapshots
    total_members = len(members_rows or [])
    pot = _safe_sum_df(dfc, "amount") if dfc is not None and not dfc.empty else 0.0
    members_paid = int(dfc["member_id"].nunique()) if dfc is not None and not dfc.empty and "member_id" in dfc.columns else 0

    # loans
    principal_current = _safe_sum_df(loans_df, "principal_current")
    unpaid_interest = _safe_sum_df(loans_df, "unpaid_interest")
    total_due = _safe_sum_df(loans_df, "total_due")
    active_like = 0
    overdue_like = 0
    if loans_df is not None and not loans_df.empty and "status" in loans_df.columns:
        s = loans_df["status"].astype(str).str.lower().fillna("")
        active_like = int(s.isin(["active", "open"]).sum() + s.isin(["overdue", "late", "delinquent", "default"]).sum())
        overdue_like = int(s.isin(["overdue", "late", "delinquent", "default"]).sum())

    # fines
    fines_total = _safe_sum_df(fines_df, "amount")

    # attendance
    present = 0
    absent = 0
    if att_df is not None and not att_df.empty and "status" in att_df.columns:
        present = int((att_df["status"] == "Present").sum())
        absent = int((att_df["status"] == "Absent").sum())

    # intent
    if any(k in qn for k in ["hi", "hello", "introduce", "who are you", "your name"]):
        return _assistant_intro_young()

    if any(k in qn for k in ["pot", "this session", "cycle contribution", "cycle"]):
        sid = session_id if session_id else "—"
        return (
            f"**Cycle snapshot (session {sid})**:\n"
            f"• Pot so far: **{_money(pot)}**\n"
            f"• Members paid: **{members_paid}/{total_members}**\n"
            f"Tip: If pot looks low, confirm contributions are being recorded with the correct `session_id`."
        )

    if any(k in qn for k in ["loan", "due", "balance", "principal", "interest", "overdue"]):
        return (
            "**Loans snapshot (limited to recent rows loaded on dashboard):**\n"
            f"• Active-like loans: **{active_like}**\n"
            f"• Overdue-like loans: **{overdue_like}**\n"
            f"• Principal current total: **{_money(principal_current)}**\n"
            f"• Unpaid interest total: **{_money(unpaid_interest)}**\n"
            f"• Total due total: **{_money(total_due)}**\n\n"
            "If you want the exact list of overdue members, open the **Loans** page for full detail."
        )

    if any(k in qn for k in ["fine", "penalty"]):
        return (
            "**Fines snapshot:**\n"
            f"• Total fines (loaded): **{_money(fines_total)}**\n"
            "If fines are missing, it may be RLS blocking SELECT or the table is empty."
        )

    if any(k in qn for k in ["attendance", "present", "absent"]):
        sid = session_id if session_id else "—"
        recorded = int(len(att_df)) if att_df is not None and not att_df.empty else 0
        return (
            f"**Attendance snapshot (session {sid})**:\n"
            f"• Records: **{recorded}**\n"
            f"• Present: **{present}**\n"
            f"• Absent: **{absent}**\n"
            "You can generate the PDF in the Attendance section."
        )

    if any(k in qn for k in ["help", "what can you do", "commands"]):
        return (
            "Ask me things like:\n"
            "• **How much is the pot this session?**\n"
            "• **Loans summary / total due / overdue count**\n"
            "• **Fines total**\n"
            "• **Attendance summary**\n"
            "• **Introduce yourself**"
        )

    return (
        "I can answer using dashboard numbers. Try:\n"
        "• **Pot this session**\n"
        "• **Loans summary**\n"
        "• **Fines total**\n"
        "• **Attendance summary**\n"
        "• **help**"
    )


# ============================================================
# MAIN DASHBOARD
# ============================================================
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    inject_dashboard_theme()
    read_sb = sb_service if sb_service is not None else sb_anon

    _auto_refresh_if_state_changed(read_sb, schema)

    # HERO + refresh
    topL, topR = st.columns([1, 0.22])
    with topL:
        st.markdown(
            """
            <div class="tysg-hero">
              <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-end;flex-wrap:wrap;">
                <div>
                  <div style="font-size:22px;font-weight:850;">🏦 theyoungshallgrow • Bank Dashboard</div>
                  <div class="tysg-sub">Modern Njangi analytics + AI helper • no legacy • no SQL</div>
                </div>
                <div class="tysg-sub">Status: <b style="color:#00E6A8;">LIVE</b></div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with topR:
        if st.button("🔄 Refresh", width=W_STRETCH):
            st.cache_data.clear()
            st.rerun()

    # Load state/session
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

    # Members + cycle snapshots
    members_rows = _load_members(schema, read_sb)
    dfc = _load_cycle_contribs(schema, read_sb, int(current_session_id)) if isinstance(current_session_id, int) else pd.DataFrame()
    loans_df = _load_loans(schema, read_sb)
    fines_df = _load_fines(schema, read_sb)

    # Attendance snapshot (light)
    att_df = (
        build_attendance_df(read_sb, schema, int(current_session_id), members_rows, limit=2500)
        if isinstance(current_session_id, int) and current_session_id > 0
        else pd.DataFrame()
    )

    total_members = len(members_rows or [])
    pot = float(pd.to_numeric(dfc["amount"], errors="coerce").fillna(0).sum()) if not dfc.empty and "amount" in dfc.columns else 0.0
    members_paid = int(dfc["member_id"].nunique()) if not dfc.empty and "member_id" in dfc.columns else 0

    # KPI top
    st.divider()
    st.markdown(glass_open(), unsafe_allow_html=True)
    a, b, c, d = st.columns(4)
    with a:
        st.markdown(_kpi_card("Session ID", str(current_session_id or "—"), "#60A5FA"), unsafe_allow_html=True)
    with b:
        st.markdown(_kpi_card("Total Members", str(total_members), "#A78BFA"), unsafe_allow_html=True)
    with c:
        st.markdown(_kpi_card("Current Pot", _fmt_money(pot, 0), "#00E6A8", sub="cycle contributions"), unsafe_allow_html=True)
    with d:
        st.markdown(_kpi_card("Members Paid", f"{members_paid}/{total_members}", "#FB923C"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # --- Attendance block ---
    left, right = st.columns([1.05, 0.95])
    with left:
        st.markdown("### 🧾 Attendance (session)")
        if att_df.empty:
            st.info("No attendance records for this session yet.")
        else:
            st.markdown(glass_open(), unsafe_allow_html=True)
            st.dataframe(att_df, width=W_STRETCH, hide_index=True)
            st.markdown(glass_close(), unsafe_allow_html=True)

        if isinstance(current_session_id, int) and current_session_id > 0 and st.button("🧾 Generate Attendance PDF", width=W_STRETCH):
            pdf_bytes = generate_attendance_pdf_bytes(
                session_id=int(current_session_id),
                session_window=str(current_session_id),
                df=att_df,
                total_members=int(total_members),
            )
            st.download_button(
                "⬇️ Download Attendance PDF",
                data=pdf_bytes,
                file_name=f"attendance_session_{int(current_session_id)}.pdf",
                mime="application/pdf",
                width=W_STRETCH,
            )

    # --- 🤖 YOUNG AI Helper block ---
    with right:
        st.markdown("### 🤖 Young — Dashboard AI Helper")
        st.caption("Young answers using the live dashboard snapshot (no guessing).")

        if "young_chat" not in st.session_state:
            st.session_state["young_chat"] = [
                {"role": "assistant", "content": _assistant_intro_young()}
            ]

        for m in st.session_state["young_chat"][-12:]:
            with st.chat_message(m["role"]):
                st.write(m["content"])

        user_q = st.chat_input("Ask Young… (e.g., 'pot this session', 'loans summary', 'attendance summary')")
        if user_q:
            st.session_state["young_chat"].append({"role": "user", "content": user_q})
            ans = _answer_young(
                user_q,
                session_id=current_session_id,
                members_rows=members_rows,
                dfc=dfc,
                loans_df=loans_df,
                fines_df=fines_df,
                att_df=att_df,
            )
            st.session_state["young_chat"].append({"role": "assistant", "content": ans})
            st.rerun()

        # quick buttons
        q1, q2, q3 = st.columns(3)
        with q1:
            if st.button("💰 Pot?", width=W_STRETCH):
                st.session_state["young_chat"].append({"role": "user", "content": "How much is the pot this session?"})
                st.session_state["young_chat"].append({"role": "assistant", "content": _answer_young("pot this session", session_id=current_session_id, members_rows=members_rows, dfc=dfc, loans_df=loans_df, fines_df=fines_df, att_df=att_df)})
                st.rerun()
        with q2:
            if st.button("💳 Loans?", width=W_STRETCH):
                st.session_state["young_chat"].append({"role": "user", "content": "Loans summary"})
                st.session_state["young_chat"].append({"role": "assistant", "content": _answer_young("loans summary", session_id=current_session_id, members_rows=members_rows, dfc=dfc, loans_df=loans_df, fines_df=fines_df, att_df=att_df)})
                st.rerun()
        with q3:
            if st.button("🧾 Attendance?", width=W_STRETCH):
                st.session_state["young_chat"].append({"role": "user", "content": "Attendance summary"})
                st.session_state["young_chat"].append({"role": "assistant", "content": _answer_young("attendance summary", session_id=current_session_id, members_rows=members_rows, dfc=dfc, loans_df=loans_df, fines_df=fines_df, att_df=att_df)})
                st.rerun()

    with st.expander("🔎 Debug", expanded=False):
        st.write("schema:", schema)
        st.write("current_session_id:", current_session_id)
        st.write("state:", state)
        st.write("members:", len(members_rows or []))
        st.write("dfc rows:", 0 if dfc is None else len(dfc))
        st.write("loans rows:", 0 if loans_df is None else len(loans_df))
        st.write("fines rows:", 0 if fines_df is None else len(fines_df))
        st.write("attendance rows:", 0 if att_df is None else len(att_df))


# ============================================================
# COMPATIBILITY EXPORTS
# ============================================================
def render_dashbaord(sb_anon, sb_service, schema: str = "public"):
    return render_dashboard(sb_anon, sb_service, schema=schema)


__all__ = ["render_dashboard", "render_dashbaord"]
