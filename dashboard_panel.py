# dashboard_panel.py ✅ FINAL (MATCHES YOUR DB 1:1)

from __future__ import annotations
import streamlit as st
import pandas as pd


def safe_view(sb, schema: str, name: str, limit: int = 1):
    try:
        q = sb.schema(schema).table(name).select("*")
        if limit:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception:
        return []


def money(x):
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return "—"


def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    st.markdown("## 📊 Dashboard")

    # =========================================================
    # SESSION / ROTATION
    # =========================================================
    season = safe_view(sb_anon, schema, "current_season_view")
    season = season[0] if season else {}

    rotation = safe_view(sb_anon, schema, "v_dashboard_rotation")
    rotation = rotation[0] if rotation else {}

    beneficiary = safe_view(sb_anon, schema, "v_next_beneficiary")
    beneficiary = beneficiary[0] if beneficiary else {}

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Session #", season.get("session_id", "—"))
    c2.metric("Next Payout Index", rotation.get("next_payout_index", "—"))
    c3.metric("Next Beneficiary", beneficiary.get("beneficiary_name", "—"))

    if season.get("start_date") and season.get("end_date"):
        c4.metric("Session Window", f"{season['start_date']} → {season['end_date']}")
    else:
        c4.metric("Session Window", "—")

    st.divider()

    # =========================================================
    # POT / CONTRIBUTIONS
    # =========================================================
    pot = safe_view(sb_anon, schema, "v_current_pot")
    pot = pot[0] if pot else {}

    cycle = safe_view(sb_anon, schema, "v_current_cycle_contributions")
    cycle = cycle[0] if cycle else {}

    p1, p2, p3 = st.columns(3)
    p1.metric("Current Pot", money(pot.get("current_pot")))
    p2.metric("Cycle Contributions", money(cycle.get("cycle_total")))
    p3.metric("Members Paid", cycle.get("members_paid", "—"))

    st.divider()

    # =========================================================
    # PAYOUT STATUS
    # =========================================================
    is_day = safe_view(sb_anon, schema, "v_is_payout_day")
    is_day = is_day[0] if is_day else {}

    payout = safe_view(sb_anon, schema, "v_payout_status_current_session")
    payout = payout[0] if payout else {}

    s1, s2, s3 = st.columns(3)
    s1.metric("Is Payout Day", "YES" if is_day.get("is_payout_day") else "NO")
    s2.metric("Payout Ready", payout.get("ready", "—"))
    s3.metric("Missing Signatures", payout.get("missing_signatures", "—"))

    st.divider()

    # =========================================================
    # KPIs
    # =========================================================
    st.markdown("### 📈 KPIs — Current Cycle")
    df_kpi = pd.DataFrame(safe_view(sb_anon, schema, "v_kpi_current_cycle", limit=200))
    if df_kpi.empty:
        st.info("No KPI data.")
    else:
        st.dataframe(df_kpi, use_container_width=True, hide_index=True)

    st.markdown("### 👤 KPIs — Member Cycle")
    df_mem = pd.DataFrame(safe_view(sb_anon, schema, "v_kpi_member_cycle", limit=2000))
    if df_mem.empty:
        st.info("No member KPI data.")
    else:
        st.dataframe(df_mem, use_container_width=True, hide_index=True)

    # =========================================================
    # SERVICE KEY STATUS
    # =========================================================
    if sb_service is None:
        st.warning("Admin/write features disabled (no service key).")
    else:
        st.success("Admin/write features enabled.")
