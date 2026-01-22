# dashboard_panel.py ✅ COMPLETE – VIEW DRIVEN DASHBOARD
# Uses ONLY SQL views (no manual calculations)
# Safe: missing views never crash the app

from __future__ import annotations
import streamlit as st
import pandas as pd


# ------------------------------------------------------------
# Safe select (supports filters but dashboard mostly doesn't need them)
# ------------------------------------------------------------
def safe_select(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int | None = 1,
):
    try:
        q = sb.schema(schema).table(table).select(cols)
        if limit:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception:
        return []


def as_df(rows):
    return pd.DataFrame(rows or [])


# ------------------------------------------------------------
# DASHBOARD
# ------------------------------------------------------------
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    st.markdown("## 📊 Dashboard")

    # =========================================================
    # 1️⃣ CORE ROTATION / SESSION INFO
    # =========================================================
    rot = safe_select(sb_anon, schema, "v_dashboard_rotation")
    rot = rot[0] if rot else {}

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Session #", rot.get("session_number", "—"))
    c2.metric("Next Payout Index", rot.get("next_payout_index", "—"))
    c3.metric("Next Beneficiary", rot.get("next_beneficiary", "—"))

    if rot.get("session_start") and rot.get("session_end"):
        c4.metric("Session Window", f"{rot['session_start']} → {rot['session_end']}")
    else:
        c4.metric("Session Window", "—")

    st.divider()

    # =========================================================
    # 2️⃣ POT & CONTRIBUTIONS
    # =========================================================
    pot = safe_select(sb_anon, schema, "v_current_pot")
    pot = pot[0] if pot else {}

    cycle = safe_select(sb_anon, schema, "v_current_cycle_contributions")
    cycle = cycle[0] if cycle else {}

    p1, p2, p3 = st.columns(3)
    p1.metric("Current Pot", pot.get("current_pot", "—"))
    p2.metric("Cycle Contributions", cycle.get("cycle_total", "—"))
    p3.metric("Members Paid", cycle.get("members_paid", "—"))

    st.divider()

    # =========================================================
    # 3️⃣ PAYOUT STATUS
    # =========================================================
    payout_day = safe_select(sb_anon, schema, "v_is_payout_day")
    payout_day = payout_day[0] if payout_day else {}

    payout_status = safe_select(sb_anon, schema, "v_payout_status_current")
    payout_status = payout_status[0] if payout_status else {}

    s1, s2, s3 = st.columns(3)
    s1.metric("Is Payout Day", "YES" if payout_day.get("is_payout_day") else "NO")
    s2.metric("Payout Ready", payout_status.get("ready", "—"))
    s3.metric("Missing Signatures", payout_status.get("missing_signatures", "—"))

    st.divider()

    # =========================================================
    # 4️⃣ KPIs
    # =========================================================
    kpi_cycle = as_df(safe_select(sb_anon, schema, "v_kpi_current_cycle", limit=50))
    kpi_member = as_df(safe_select(sb_anon, schema, "v_kpi_member_cycle", limit=50))

    st.markdown("### 📈 KPIs – Current Cycle")
    if not kpi_cycle.empty:
        st.dataframe(kpi_cycle, use_container_width=True, hide_index=True)
    else:
        st.info("No KPI data for current cycle.")

    st.markdown("### 👤 KPIs – Per Member")
    if not kpi_member.empty:
        st.dataframe(kpi_member, use_container_width=True, hide_index=True)
    else:
        st.info("No member KPI data.")

    st.divider()

    # =========================================================
    # 5️⃣ ATTENDANCE SUMMARY
    # =========================================================
    att_day = as_df(safe_select(sb_anon, schema, "v_attendance_daily_summary", limit=120))
    att_mem = as_df(safe_select(sb_anon, schema, "v_attendance_member_summary", limit=200))

    st.markdown("### ✅ Attendance – Daily Summary")
    if not att_day.empty:
        st.dataframe(att_day, use_container_width=True, hide_index=True)
    else:
        st.info("No attendance summary data.")

    st.markdown("### 👥 Attendance – Member Summary")
    if not att_mem.empty:
        st.dataframe(att_mem, use_container_width=True, hide_index=True)
    else:
        st.info("No member attendance summary.")

    st.divider()

    # =========================================================
    # 6️⃣ LOANS (RISK / DPD)
    # =========================================================
    loan_dpd = as_df(safe_select(sb_anon, schema, "v_loan_dpd", limit=200))
    loan_aging = as_df(safe_select(sb_anon, schema, "v_loan_aging_legacy", limit=200))

    st.markdown("### 💳 Loan Risk (DPD)")
    if not loan_dpd.empty:
        st.dataframe(loan_dpd, use_container_width=True, hide_index=True)
    else:
        st.info("No DPD data.")

    st.markdown("### 📅 Loan Aging")
    if not loan_aging.empty:
        st.dataframe(loan_aging, use_container_width=True, hide_index=True)
    else:
        st.info("No loan aging data.")

    st.divider()

    # =========================================================
    # 7️⃣ SERVICE STATUS
    # =========================================================
    if sb_service is None:
        st.warning("Admin / write actions disabled (SERVICE KEY missing).")
    else:
        st.success("Admin / write actions enabled.")
