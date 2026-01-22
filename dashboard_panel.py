# dashboard_panel.py ✅ COMPLETE FIXED VERSION
# Fixes: TypeError safe_select() got unexpected keyword 'meeting_date'
# - safe_select now supports **filters (eq filters)
# - Works with sb_anon / sb_service and any schema
# - Keeps "fail silently" behavior so dashboard doesn't crash

from __future__ import annotations

from datetime import date
import streamlit as st
import pandas as pd


# ============================================================
# SAFE SELECT (dashboard helper)
# ============================================================
def safe_select(
    sb_client,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 500,
    order_by: str | None = None,
    order_desc: bool = True,
    **filters,  # ✅ allows meeting_date=..., legacy_member_id=..., session_number=...
) -> list[dict]:
    """
    Safe Supabase select that supports equality filters.
    Returns [] on any error (so dashboard never crashes).
    """
    try:
        q = sb_client.schema(schema).table(table).select(cols)

        # ✅ apply equality filters
        for k, v in (filters or {}).items():
            if v is None:
                continue
            q = q.eq(k, v)

        if order_by:
            q = q.order(order_by, desc=order_desc)

        if limit is not None:
            q = q.limit(int(limit))

        res = q.execute()
        return res.data or []
    except Exception:
        # fail silently by design (dashboard should not crash)
        return []


def _money(x) -> str:
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)


def _to_df(rows) -> pd.DataFrame:
    return pd.DataFrame(rows or [])


# ============================================================
# DASHBOARD RENDER
# ============================================================
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    st.header("📊 Dashboard")

    # -----------------------------
    # Pull next session snapshot (view)
    # -----------------------------
    dash_rows = safe_select(sb_anon, schema, "dashboard_next_view", "*", limit=1)
    dash = dash_rows[0] if dash_rows else {}

    session_number = dash.get("session_number")
    next_payout_index = dash.get("next_payout_index")
    next_payout_name = dash.get("next_payout_name")
    session_start = dash.get("session_start")
    session_end = dash.get("session_end")

    # -----------------------------
    # KPI row
    # -----------------------------
    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Session #", str(session_number) if session_number is not None else "—")
    c2.metric("Next Payout Index", str(next_payout_index) if next_payout_index is not None else "—")
    c3.metric("Next Beneficiary", str(next_payout_name) if next_payout_name else "—")
    if session_start and session_end:
        c4.metric("Session Window", f"{session_start} → {session_end}")
    else:
        c4.metric("Session Window", "—")

    st.divider()

    # -----------------------------
    # Attendance quick check (today)
    # -----------------------------
    today_str = str(date.today())
    att_today = safe_select(
        sb_anon,
        schema,
        "meeting_attendance_legacy",
        "id",
        limit=1,
        meeting_date=today_str,  # ✅ NOW SUPPORTED
    )

    left, right = st.columns([1, 1])
    with left:
        st.subheader("✅ Meeting / Attendance")
        if att_today:
            st.success(f"Attendance exists for today ({today_str}).")
        else:
            st.info(f"No attendance record found for today ({today_str}).")

        # Show last attendance date (optional)
        last_att = safe_select(
            sb_anon,
            schema,
            "meeting_attendance_legacy",
            "meeting_date,session_number,legacy_member_id,status",
            limit=50,
            order_by="meeting_date",
            order_desc=True,
        )
        dfa = _to_df(last_att)
        if not dfa.empty:
            st.caption("Latest attendance marks (sample)")
            st.dataframe(dfa, use_container_width=True, hide_index=True)

    # -----------------------------
    # Contributions snapshot
    # -----------------------------
    with right:
        st.subheader("💰 Contributions")
        # If you have a view that already aggregates, use it:
        # e.g. dashboard_contrib_kpis_view or similar.
        # Otherwise we show latest contributions rows.
        contrib_rows = safe_select(
            sb_anon,
            schema,
            "contributions_with_member",
            "*",
            limit=25,
            order_by="created_at",
            order_desc=True,
        )
        dfc = _to_df(contrib_rows)
        if dfc.empty:
            st.info("No contributions rows found (or view not readable).")
            st.caption("Confirm contributions_with_member exists + anon has SELECT grants.")
        else:
            st.dataframe(dfc, use_container_width=True, hide_index=True)

    st.divider()

    # -----------------------------
    # Admin-only / Service key checks
    # -----------------------------
    st.subheader("🔐 Admin / Service Key Status")
    if sb_service is None:
        st.warning("Service client is not configured (SUPABASE_SERVICE_KEY missing). Admin write features disabled.")
    else:
        st.success("Service client is configured. Admin write features enabled.")
