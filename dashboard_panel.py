# dashboard_panel.py ✅ COMPLETE FULL FILE (SAFE + FIXED)
# Fixes: TypeError safe_select() got unexpected keyword 'meeting_date'
# Self-contained: does NOT import safe_select from db/app.
# Dashboard sections are optional: missing tables/views won't crash.

from __future__ import annotations

from datetime import date
import streamlit as st
import pandas as pd


# ============================================================
# SAFE SELECT (supports filters!)
# ============================================================
def safe_select(
    sb_client,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int | None = 500,
    order_by: str | None = None,
    order_desc: bool = True,
    **filters,  # ✅ meeting_date=..., legacy_member_id=..., etc.
) -> list[dict]:
    """
    Safe Supabase select with optional equality filters.
    Returns [] on any error so dashboard never crashes.
    """
    try:
        q = sb_client.schema(schema).table(table).select(cols)

        # equality filters
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
        return []


def _to_df(rows) -> pd.DataFrame:
    return pd.DataFrame(rows or [])


def _money(x) -> str:
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)


def _safe_int(x, default=None):
    try:
        if x is None or x == "":
            return default
        return int(x)
    except Exception:
        return default


# ============================================================
# MAIN DASHBOARD
# ============================================================
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    st.markdown("## 📊 Dashboard")
    st.caption("Safe KPIs and quick snapshots. Missing tables/views won’t crash the app.")

    # ---------------------------------------------------------
    # 1) Session snapshot view (optional)
    # ---------------------------------------------------------
    dash_rows = safe_select(sb_anon, schema, "dashboard_next_view", "*", limit=1)
    dash = dash_rows[0] if dash_rows else {}

    session_number = dash.get("session_number")
    next_payout_index = dash.get("next_payout_index")
    next_payout_name = dash.get("next_payout_name")
    session_start = dash.get("session_start") or dash.get("start_date")
    session_end = dash.get("session_end") or dash.get("end_date")

    # ---------------------------------------------------------
    # 2) Member count (optional)
    # ---------------------------------------------------------
    members = safe_select(sb_anon, schema, "members_legacy", "id", limit=2000, order_by="id", order_desc=False)
    member_count = len(members) if members else None

    # ---------------------------------------------------------
    # 3) Attendance check (today) ✅ fixes your crash
    # ---------------------------------------------------------
    today_str = str(date.today())
    att_today = safe_select(
        sb_anon,
        schema,
        "meeting_attendance_legacy",
        "id",
        limit=1,
        meeting_date=today_str,  # ✅ supported
    )

    # ---------------------------------------------------------
    # KPI CARDS
    # ---------------------------------------------------------
    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Session #", str(session_number) if session_number is not None else "—")
    c2.metric("Members", str(member_count) if member_count is not None else "—")
    c3.metric("Next Payout Index", str(next_payout_index) if next_payout_index is not None else "—")
    c4.metric("Next Beneficiary", str(next_payout_name) if next_payout_name else "—")

    if session_start and session_end:
        st.caption(f"🗓️ Session Window: {session_start} → {session_end}")

    st.divider()

    # ---------------------------------------------------------
    # ATTENDANCE + CONTRIBUTIONS SNAPSHOTS
    # ---------------------------------------------------------
    left, right = st.columns([1, 1])

    with left:
        st.markdown("### ✅ Attendance")
        if att_today:
            st.success(f"Attendance exists for today: {today_str}")
        else:
            st.info(f"No attendance record found for today: {today_str}")

        rows_att = safe_select(
            sb_anon,
            schema,
            "meeting_attendance_legacy",
            "meeting_date,session_number,legacy_member_id,member_name,status,note,created_by",
            limit=80,
            order_by="meeting_date",
            order_desc=True,
        )
        dfa = _to_df(rows_att)
        if dfa.empty:
            st.caption("No attendance rows (or table not readable).")
        else:
            st.dataframe(dfa, use_container_width=True, hide_index=True)

    with right:
        st.markdown("### 💰 Latest Contributions")
        rows_contrib = safe_select(
            sb_anon,
            schema,
            "contributions_with_member",
            "*",
            limit=60,
            order_by="created_at",
            order_desc=True,
        )
        dfc = _to_df(rows_contrib)
        if dfc.empty:
            st.info("No contributions rows found (or view not readable).")
            st.caption("Confirm contributions_with_member exists + anon has SELECT grants.")
        else:
            if "amount" in dfc.columns:
                try:
                    total = pd.to_numeric(dfc["amount"], errors="coerce").fillna(0).sum()
                    st.metric("Total (latest rows)", _money(total))
                except Exception:
                    pass
            st.dataframe(dfc, use_container_width=True, hide_index=True)

    st.divider()

    # ---------------------------------------------------------
    # RECENT MINUTES (optional)
    # ---------------------------------------------------------
    st.markdown("### 📝 Recent Minutes")
    rows_min = safe_select(
        sb_anon,
        schema,
        "meeting_minutes_legacy",
        "id,meeting_date,session_number,title,tags,created_by",
        limit=20,
        order_by="meeting_date",
        order_desc=True,
    )
    dfm = _to_df(rows_min)
    if dfm.empty:
        st.caption("No minutes found (or meeting_minutes_legacy not readable).")
    else:
        st.dataframe(dfm, use_container_width=True, hide_index=True)

    st.divider()

    # ---------------------------------------------------------
    # ADMIN / SERVICE KEY STATUS
    # ---------------------------------------------------------
    st.markdown("### 🔐 Admin / Service Key")
    if sb_service is None:
        st.warning("SUPABASE_SERVICE_KEY missing → Admin/Write features disabled.")
    else:
        st.success("Service client available → Admin/Write features enabled.")
