# dashboard_panel.py ✅ COMPLETE + COMPATIBLE (old + new views)
# Fix goals:
# - Dashboard should not show "—" when views already have data
# - Works whether you use dashboard_next_view/dashboard_finance_view OR v_* views
# - Robust column-name mapping (pick() helper)
# - Safe reads (dashboard never crashes)

from __future__ import annotations

import streamlit as st
import pandas as pd


# ============================================================
# Helpers
# ============================================================
def pick(row: dict, *keys, default=None):
    """Return first non-empty row[key] among keys."""
    for k in keys:
        if not row:
            break
        if k in row and row.get(k) not in (None, "", "null"):
            return row.get(k)
    return default


def safe_view(sb, schema: str, name: str, limit: int = 1, order_by: str | None = None, desc: bool = True):
    """Safe SELECT * from a table/view. Returns [] on any error."""
    try:
        q = sb.schema(schema).table(name).select("*")
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit is not None:
            q = q.limit(int(limit))
        return q.execute().data or []
    except Exception:
        return []


def money(x):
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)


# ============================================================
# Dashboard
# ============================================================
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    st.markdown("## 📊 Dashboard")

    # ---------------------------------------------------------
    # Pull possible sources (old + new)
    # ---------------------------------------------------------
    # NEW
    rot_rows = safe_view(sb_anon, schema, "v_dashboard_rotation", limit=1)
    rot = rot_rows[0] if rot_rows else {}

    next_ben_rows = safe_view(sb_anon, schema, "v_next_beneficiary", limit=1)
    next_ben = next_ben_rows[0] if next_ben_rows else {}

    pot_rows = safe_view(sb_anon, schema, "v_current_pot", limit=1)
    pot = pot_rows[0] if pot_rows else {}

    cyc_rows = safe_view(sb_anon, schema, "v_current_cycle_contributions", limit=1)
    cyc = cyc_rows[0] if cyc_rows else {}

    payout_day_rows = safe_view(sb_anon, schema, "v_is_payout_day", limit=1)
    payout_day = payout_day_rows[0] if payout_day_rows else {}

    payout_status_rows = safe_view(sb_anon, schema, "v_payout_status_current", limit=1)
    payout_status = payout_status_rows[0] if payout_status_rows else {}

    # OLD (fallback)
    dash_next_rows = safe_view(sb_anon, schema, "dashboard_next_view", limit=1)
    dash_next = dash_next_rows[0] if dash_next_rows else {}

    dash_fin_rows = safe_view(sb_anon, schema, "dashboard_finance_view", limit=1)
    dash_fin = dash_fin_rows[0] if dash_fin_rows else {}

    current_season_rows = safe_view(sb_anon, schema, "current_season_view", limit=1)
    current_season = current_season_rows[0] if current_season_rows else {}

    # ---------------------------------------------------------
    # Choose best sources (prefer new, fallback to old)
    # ---------------------------------------------------------
    header_src = rot or dash_next or current_season or {}
    ben_src = next_ben or rot or dash_next or {}
    pot_src = pot or dash_fin or dash_next or {}
    cyc_src = cyc or dash_fin or dash_next or {}

    # ---------------------------------------------------------
    # Map values (support many possible key names)
    # ---------------------------------------------------------
    session_number = pick(
        header_src,
        "session_number", "biweekly_session_id", "session_id", "current_session_id", "current_session",
        default=None
    )

    next_idx = pick(
        header_src,
        "next_payout_index", "rotation_pointer", "next_index", "next_payout", "next_rotation_index",
        default=None
    )

    next_name = pick(
        ben_src,
        "next_beneficiary", "next_payout_name", "beneficiary_name", "member_name", "full_name",
        default=None
    )

    win_start = pick(header_src, "session_start", "start_date", "window_start", default=None)
    win_end = pick(header_src, "session_end", "end_date", "window_end", default=None)

    current_pot = pick(
        pot_src,
        "current_pot", "pot_this_session", "pot_session", "pot", "amount", "total_pot", "pot_amount",
        default=None
    )

    cycle_total = pick(
        cyc_src,
        "cycle_total", "total_cycle", "cycle_contributions", "cycle_total_amount", "current_cycle_total", "total",
        default=None
    )

    members_paid = pick(
        cyc_src,
        "members_paid", "paid_members", "count_paid", "members_contributed", "contributors", "paid_count",
        default=None
    )

    is_payout_day = bool(pick(payout_day, "is_payout_day", "payout_day", default=False))

    payout_ready = pick(
        payout_status,
        "ready", "is_ready", "payout_ready", "ready_to_pay",
        default=None
    )

    missing_sigs = pick(
        payout_status,
        "missing_signatures", "missing_roles", "missing", "missing_required_signatures",
        default=None
    )

    # ---------------------------------------------------------
    # TOP KPI ROW (matches your UI)
    # ---------------------------------------------------------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Session #", session_number if session_number is not None else "—")
    c2.metric("Next Payout Index", next_idx if next_idx is not None else "—")
    c3.metric("Next Beneficiary", next_name if next_name else "—")
    c4.metric("Session Window", f"{win_start} → {win_end}" if win_start and win_end else "—")

    st.divider()

    # ---------------------------------------------------------
    # POT / CYCLE KPIs
    # ---------------------------------------------------------
    p1, p2, p3 = st.columns(3)
    p1.metric("Current Pot", money(current_pot) if current_pot is not None else "—")
    p2.metric("Cycle Contributions", money(cycle_total) if cycle_total is not None else "—")
    p3.metric("Members Paid", members_paid if members_paid is not None else "—")

    st.divider()

    # ---------------------------------------------------------
    # PAYOUT STATUS KPIs
    # ---------------------------------------------------------
    s1, s2, s3 = st.columns(3)
    s1.metric("Is Payout Day", "YES" if is_payout_day else "NO")
    s2.metric("Payout Ready", payout_ready if payout_ready is not None else "—")
    s3.metric("Missing Signatures", missing_sigs if missing_sigs is not None else "—")

    st.divider()

    # ---------------------------------------------------------
    # Tables (optional, but useful)
    # ---------------------------------------------------------
    st.markdown("### KPIs — Current Cycle")
    kpi_cycle = pd.DataFrame(safe_view(sb_anon, schema, "v_kpi_current_cycle", limit=250))
    if kpi_cycle.empty:
        st.caption("No data in v_kpi_current_cycle.")
    else:
        st.dataframe(kpi_cycle, use_container_width=True, hide_index=True)

    st.markdown("### KPIs — Member Cycle")
    kpi_member = pd.DataFrame(safe_view(sb_anon, schema, "v_kpi_member_cycle", limit=2000))
    if kpi_member.empty:
        st.caption("No data in v_kpi_member_cycle.")
    else:
        st.dataframe(kpi_member, use_container_width=True, hide_index=True)

    # ---------------------------------------------------------
    # Debug expander (turn off later)
    # ---------------------------------------------------------
    with st.expander("🔎 Debug (raw view rows)", expanded=False):
        st.write("v_dashboard_rotation", rot_rows[0] if rot_rows else "NO ROWS")
        st.write("v_next_beneficiary", next_ben_rows[0] if next_ben_rows else "NO ROWS")
        st.write("v_current_pot", pot_rows[0] if pot_rows else "NO ROWS")
        st.write("v_current_cycle_contributions", cyc_rows[0] if cyc_rows else "NO ROWS")
        st.write("v_is_payout_day", payout_day_rows[0] if payout_day_rows else "NO ROWS")
        st.write("v_payout_status_current", payout_status_rows[0] if payout_status_rows else "NO ROWS")
        st.write("dashboard_next_view", dash_next_rows[0] if dash_next_rows else "NO ROWS")
        st.write("dashboard_finance_view", dash_fin_rows[0] if dash_fin_rows else "NO ROWS")
        st.write("current_season_view", current_season_rows[0] if current_season_rows else "NO ROWS")

    # ---------------------------------------------------------
    # Service key status
    # ---------------------------------------------------------
    if sb_service is None:
        st.warning("SUPABASE_SERVICE_KEY missing → Admin/write features disabled.")
    else:
        st.success("Service client available → Admin/write features enabled.")
