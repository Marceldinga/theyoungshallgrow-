# dashboard_panel.py ✅ COMPLETE UPDATED (SESSION WINDOW FIX + NO MORE BLANKS)
# Fixes:
# - Uses sessions_legacy.session_number (NOT session_id)
# - Pulls Session Window directly from sessions_legacy if dashboard_next_view is missing dates
# - Current Pot / Cycle Contributions / Members Paid: robust fallbacks and numeric coercion
# - All-time finance from dashboard_finance_view
# - Interest (All-Time) from v_interest_total.total_interest_generated
#
# Requires:
# - sb_anon: supabase client (anon)
# - sb_service: supabase client (service) or None
# - schema: your schema (default "public")

from __future__ import annotations

import streamlit as st
import pandas as pd


# ------------------------------------------------------------
# Safe helpers
# ------------------------------------------------------------
def safe_view(sb, schema: str, name: str, limit: int = 1):
    """Safe SELECT * from a view/table. Returns [] on error."""
    try:
        q = sb.schema(schema).table(name).select("*")
        if limit is not None:
            q = q.limit(int(limit))
        return q.execute().data or []
    except Exception:
        return []


def safe_select_where(sb, schema: str, table: str, cols: str, where_col: str, where_val, limit: int = 1):
    """Safe SELECT cols FROM table WHERE where_col = where_val."""
    try:
        q = (
            sb.schema(schema)
            .table(table)
            .select(cols)
            .eq(where_col, where_val)
        )
        if limit is not None:
            q = q.limit(int(limit))
        return q.execute().data or []
    except Exception:
        return []


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


def _pick(row: dict, *keys, default=None):
    """Pick first existing key with non-null value."""
    for k in keys:
        if row and k in row and row.get(k) not in (None, "", "null"):
            return row.get(k)
    return default


def _s(x) -> str | None:
    """Safe stringify for dates/values."""
    if x is None:
        return None
    s = str(x).strip()
    return s if s else None


# ------------------------------------------------------------
# Dashboard renderer
# ------------------------------------------------------------
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    st.markdown("## 📊 Dashboard")

    # =========================================================
    # 1) SESSION / ROTATION (dashboard_next_view)
    # =========================================================
    dash = (safe_view(sb_anon, schema, "dashboard_next_view", limit=1) or [{}])[0]

    # Session index / payout index
    session_number = _pick(dash, "session_number", "payout_index", "next_payout_index", default=None)
    next_idx = _pick(dash, "payout_index", "next_payout_index", default=session_number)
    beneficiary_name = _pick(dash, "beneficiary_name", "next_beneficiary", default="—")

    # Try read window from dashboard_next_view first
    start_date = _s(_pick(dash, "start_date"))
    end_date = _s(_pick(dash, "end_date"))

    # ✅ If missing, pull from sessions_legacy using session_number
    # Your table: sessions_legacy(id uuid, start_date date, end_date date, status text, session_number int)
    if (not start_date or not end_date) and session_number not in (None, "—", ""):
        try:
            sid_int = int(session_number)
        except Exception:
            sid_int = None

        if sid_int is not None:
            sess = (safe_select_where(
                sb_anon,
                schema,
                "sessions_legacy",
                "start_date,end_date,session_number",
                "session_number",
                sid_int,
                limit=1
            ) or [{}])[0]
            start_date = start_date or _s(_pick(sess, "start_date"))
            end_date = end_date or _s(_pick(sess, "end_date"))

    window = f"{start_date} → {end_date}" if start_date and end_date else "—"

    # ✅ session-scoped pot amount (if your dashboard_next_view exposes it)
    pot_amount = _pick(dash, "pot_amount", "current_pot", default=None)
    pot_amount_num = _num(pot_amount, default=0.0) if pot_amount not in (None, "") else None

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Session #", str(session_number) if session_number not in (None, "") else "—")
    c2.metric("Next Payout Index", str(next_idx) if next_idx not in (None, "") else "—")
    c3.metric("Next Beneficiary", beneficiary_name if beneficiary_name else "—")
    c4.metric("Session Window", window)

    st.divider()

    # =========================================================
    # 2) SESSION POT / CYCLE TOTALS
    # =========================================================
    cyc = (safe_view(sb_anon, schema, "v_current_cycle_contributions", limit=1) or [{}])[0]
    cycle_total = _pick(cyc, "cycle_total", "total", "sum_amount")
    members_paid = _pick(cyc, "members_paid", "paid_members", "contributors")

    # Finance view fallback
    fin_session = (safe_view(sb_anon, schema, "dashboard_finance_view", limit=1) or [{}])[0]
    pot_total = _pick(fin_session, "pot_total", "current_pot_total", "pot_amount")
    contributors = _pick(fin_session, "contributors", "members_paid", "paid_members")

    # Numeric fallbacks
    cycle_total_num = _num(cycle_total, default=0.0) if cycle_total not in (None, "") else 0.0
    pot_total_num = _num(pot_total, default=0.0) if pot_total not in (None, "") else 0.0

    # If cycle_total is missing/0, use pot_amount (session) else pot_total
    if cycle_total_num == 0.0:
        if pot_amount_num is not None and pot_amount_num > 0:
            cycle_total_num = pot_amount_num
        elif pot_total_num > 0:
            cycle_total_num = pot_total_num
        else:
            cycle_total_num = 0.0

    # Members paid fallback: use contributors
    members_paid_val = members_paid
    if members_paid_val in (None, "", 0):
        members_paid_val = contributors

    p1, p2, p3 = st.columns(3)
    # Current Pot: prefer pot_amount_num, fallback pot_total_num
    if pot_amount_num is not None and pot_amount_num > 0:
        p1.metric("Current Pot", _fmt_money(pot_amount_num))
    elif pot_total_num > 0:
        p1.metric("Current Pot", _fmt_money(pot_total_num))
    else:
        p1.metric("Current Pot", "—")

    p2.metric("Cycle Contributions", _fmt_money(cycle_total_num) if cycle_total_num > 0 else "—")
    p3.metric("Members Paid", str(members_paid_val) if members_paid_val not in (None, "") else "—")

    st.divider()

    # =========================================================
    # 3) PAYOUT STATUS
    # =========================================================
    is_day = (safe_view(sb_anon, schema, "v_is_payout_day", limit=1) or [{}])[0]
    payout_status = (safe_view(sb_anon, schema, "v_payout_status_current_session", limit=1) or [{}])[0]

    is_payout_day = bool(_pick(is_day, "is_payout_day", default=False))
    ready = _pick(payout_status, "ready")
    missing = _pick(payout_status, "missing_signatures")

    s1, s2, s3 = st.columns(3)
    s1.metric("Is Payout Day", "YES" if is_payout_day else "NO")
    s2.metric("Payout Ready", "YES" if ready is True else ("NO" if ready is False else "—"))
    s3.metric("Missing Signatures", missing if missing else "—")

    st.divider()

    # =========================================================
    # 4) ALL-TIME FINANCE + Interest (v_interest_total)
    # =========================================================
    fin = fin_session

    total_foundation_paid = _pick(fin, "total_foundation_paid", "foundation_paid", "total_foundation")
    total_fines_paid = _pick(fin, "total_fines_paid", "fines_paid")
    total_fines_unpaid = _pick(fin, "total_fines_unpaid", "fines_unpaid")

    interest_row = (safe_view(sb_anon, schema, "v_interest_total", limit=1) or [{}])[0]
    total_interest_generated = _pick(interest_row, "total_interest_generated", "total_interest", "interest_total")

    st.markdown("### 🧾 All-Time Finance Summary")

    f1, f2, f3, f4, f5 = st.columns(5)
    f1.metric("Foundation (All-Time)", _fmt_money(_num(total_foundation_paid), 0) if total_foundation_paid is not None else "—")
    f2.metric("Foundation Paid", _fmt_money(_num(total_foundation_paid), 0) if total_foundation_paid is not None else "—")
    f3.metric("Fines Paid", _fmt_money(_num(total_fines_paid), 0) if total_fines_paid is not None else "—")
    f4.metric("Fines Unpaid", _fmt_money(_num(total_fines_unpaid), 0) if total_fines_unpaid is not None else "—")
    f5.metric("Interest (All-Time)", _fmt_money(_num(total_interest_generated), 2) if total_interest_generated is not None else "—")

    st.divider()

    # =========================================================
    # 5) OPTIONAL: KPI TABLES
    # =========================================================
    kpi_cycle = pd.DataFrame(safe_view(sb_anon, schema, "v_kpi_current_cycle", limit=200))
    if not kpi_cycle.empty:
        st.markdown("### 📈 KPIs — Current Cycle")
        st.dataframe(kpi_cycle, use_container_width=True, hide_index=True)

    kpi_member = pd.DataFrame(safe_view(sb_anon, schema, "v_kpi_member_cycle", limit=2000))
    if not kpi_member.empty:
        st.markdown("### 👤 KPIs — Member Cycle")
        st.dataframe(kpi_member, use_container_width=True, hide_index=True)

    # =========================================================
    # DEBUG
    # =========================================================
    with st.expander("🔎 Debug (raw rows)", expanded=False):
        st.write("dashboard_next_view", dash)
        st.write("sessions_legacy (resolved window)", {"start_date": start_date, "end_date": end_date, "session_number": session_number})
        st.write("v_current_cycle_contributions", cyc)
        st.write("dashboard_finance_view", fin)
        st.write("v_is_payout_day", is_day)
        st.write("v_payout_status_current_session", payout_status)
        st.write("v_interest_total", interest_row)

    # Service key status
    if sb_service is None:
        st.warning("Admin/write features disabled (no service key).")
    else:
        st.success("Admin/write features enabled.")
