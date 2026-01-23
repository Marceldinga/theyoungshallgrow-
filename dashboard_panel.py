# dashboard_panel.py ✅ FINAL COMPLETE (UPDATED + Interest fix)
# Fixes:
# - Session Window uses dashboard_next_view.start_date/end_date
# - Current Pot uses dashboard_next_view.pot_amount (session-scoped)
# - Cycle totals use v_current_cycle_contributions, fallback to dashboard_next_view.pot_amount
# - Members Paid uses v_current_cycle_contributions.members_paid, fallback to dashboard_finance_view.contributors
# - All-time finance uses dashboard_finance_view:
#     total_foundation_paid, total_fines_paid, total_fines_unpaid
# - Interest (All-Time) comes from v_interest_total:
#     total_interest_generated  ✅ (existing column name; no renaming)

from __future__ import annotations

import streamlit as st
import pandas as pd


def safe_view(sb, schema: str, name: str, limit: int = 1):
    """Safe SELECT * from a view/table. Returns [] on error."""
    try:
        q = sb.schema(schema).table(name).select("*")
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


def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    st.markdown("## 📊 Dashboard")

    # =========================================================
    # 1) SESSION / ROTATION (dashboard_next_view)
    # =========================================================
    dash = (safe_view(sb_anon, schema, "dashboard_next_view", limit=1) or [{}])[0]

    # dashboard_next_view (confirmed by your DB):
    # session_id (uuid), payout_index, start_date, end_date, beneficiary_id, beneficiary_name, pot_amount
    session_number = _pick(dash, "payout_index", "session_number", default="—")
    next_idx = _pick(dash, "payout_index", "next_payout_index", default="—")
    beneficiary_name = _pick(dash, "beneficiary_name", "next_beneficiary", default="—")

    start_date = _pick(dash, "start_date")
    end_date = _pick(dash, "end_date")
    window = f"{start_date} → {end_date}" if start_date and end_date else "—"

    # ✅ session-scoped pot amount
    pot_amount = _pick(dash, "pot_amount")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Session #", session_number if session_number is not None else "—")
    c2.metric("Next Payout Index", next_idx if next_idx is not None else "—")
    c3.metric("Next Beneficiary", beneficiary_name if beneficiary_name else "—")
    c4.metric("Session Window", window)

    st.divider()

    # =========================================================
    # 2) SESSION POT / CYCLE TOTALS
    # =========================================================
    cyc = (safe_view(sb_anon, schema, "v_current_cycle_contributions", limit=1) or [{}])[0]
    cycle_total = _pick(cyc, "cycle_total")
    members_paid = _pick(cyc, "members_paid")

    # finance view (contains pot_total + contributors)
    fin_session = (safe_view(sb_anon, schema, "dashboard_finance_view", limit=1) or [{}])[0]
    pot_total = _pick(fin_session, "pot_total")
    contributors = _pick(fin_session, "contributors")

    # Strong fallbacks
    cycle_total = cycle_total or pot_amount or pot_total
    members_paid = members_paid or contributors

    p1, p2, p3 = st.columns(3)
    p1.metric(
        "Current Pot",
        _fmt_money(pot_amount) if pot_amount is not None else (_fmt_money(pot_total) if pot_total is not None else "—"),
    )
    p2.metric("Cycle Contributions", _fmt_money(cycle_total) if cycle_total is not None else "—")
    p3.metric("Members Paid", str(members_paid) if members_paid is not None else "—")

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
    # 4) ALL-TIME FINANCE (dashboard_finance_view) + Interest (v_interest_total)
    # =========================================================
    fin = fin_session  # reuse

    total_foundation_paid = _pick(fin, "total_foundation_paid")
    total_fines_paid = _pick(fin, "total_fines_paid")
    total_fines_unpaid = _pick(fin, "total_fines_unpaid")

    # Interest is not reliably in dashboard_finance_view; use v_interest_total
    interest_row = (safe_view(sb_anon, schema, "v_interest_total", limit=1) or [{}])[0]
    total_interest_generated = _pick(interest_row, "total_interest_generated")

    st.markdown("### 🧾 All-Time Finance Summary")

    f1, f2, f3, f4, f5 = st.columns(5)

    # You don't have total_foundation -> use total_foundation_paid as all-time foundation
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
    # DEBUG (keep for now; remove later)
    # =========================================================
    with st.expander("🔎 Debug (raw rows)", expanded=False):
        st.write("dashboard_next_view", dash)
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
