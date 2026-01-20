# dashboard_panel.py ✅ ORG-STANDARD DASHBOARD MOCK (clean, executive readable)
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import datetime

# -------------------------
# small helpers
# -------------------------
def safe_select(sb, schema: str, table: str, cols: str="*", order_by: str|None=None, desc: bool=False, limit: int|None=None):
    try:
        q = sb.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit is not None:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception:
        return []

def safe_single(sb, schema: str, table: str, cols: str="*", **eq_filters):
    try:
        q = sb.schema(schema).table(table).select(cols)
        for k, v in eq_filters.items():
            q = q.eq(k, v)
        return (q.limit(1).execute().data or [{}])[0]
    except Exception:
        return {}

def get_rotation_state(sb_anon, schema: str) -> dict:
    """
    Single source of truth:
    Prefer current_season_view, fallback to app_state (id=1).
    """
    season = safe_single(sb_anon, schema, "current_season_view", "*")
    if season and any(season.values()):
        return season
    state = safe_single(sb_anon, schema, "app_state", "*", id=1)
    return state or {}

def format_date(x) -> str:
    if not x:
        return "N/A"
    try:
        return str(x)[:10]
    except Exception:
        return str(x)

# -------------------------
# Main dashboard render
# -------------------------
def render_dashboard(sb_anon, sb_service, schema: str):
    st.subheader("Meeting Dashboard")

    # Load core data
    members = safe_select(sb_anon, schema, "members_legacy", "id,name,position", order_by="id")
    df_members = pd.DataFrame(members) if members else pd.DataFrame(columns=["id","name","position"])

    rot = get_rotation_state(sb_anon, schema)
    next_idx = rot.get("next_payout_index") or rot.get("next_payout_index".lower())
    next_date = rot.get("next_payout_date") or rot.get("next_payout_date".lower())
    next_beneficiary = rot.get("next_beneficiary") or rot.get("next_beneficiary".lower()) or rot.get("beneficiary_name")

    # Try contribution pot view if it exists
    pot_row = safe_single(sb_anon, schema, "v_contribution_pot", "*")
    pot_amount = pot_row.get("pot_amount") if pot_row else None

    # KPI strip (exec-friendly)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Members", f"{len(df_members):,}")
    c2.metric("Next Payout Index", str(next_idx or "N/A"))
    c3.metric("Next Payout Date", format_date(next_date))
    c4.metric("Pot Amount", f"{float(pot_amount):,.0f}" if pot_amount not in (None, "") else "N/A")

    st.divider()

    # Primary card: Next Beneficiary + Meeting status
    left, right = st.columns([1.2, 0.8])

    with left:
        st.markdown("### Next Beneficiary")
        if next_beneficiary:
            st.success(str(next_beneficiary))
        else:
            st.info("N/A (will appear when rotation state is initialized)")

        st.markdown("### Rotation Preview (Top 5)")
        if not df_members.empty:
            # show first 5 by position if present, else by id
            if "position" in df_members.columns and df_members["position"].notna().any():
                dfp = df_members.sort_values("position", ascending=True).head(5)
            else:
                dfp = df_members.sort_values("id", ascending=True).head(5)
            st.dataframe(dfp[["id","name","position"]] if "position" in dfp.columns else dfp[["id","name"]],
                         use_container_width=True, hide_index=True)
        else:
            st.warning("No members loaded.")

    with right:
        st.markdown("### Meeting Controls")
        st.caption("Standard controls for meeting flow. (Service key required for write actions.)")

        if not sb_service:
            st.warning("Service key not configured. Admin actions disabled.")
        else:
            # Initialize app_state if missing
            if st.button("✅ Initialize app_state (id=1)", use_container_width=True):
                try:
                    sb_service.schema(schema).table("app_state").upsert({"id": 1, "next_payout_index": 1}).execute()
                    st.success("Initialized. Refresh dashboard.")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Init failed: {e}")

            # Quick set payout index/date
            st.markdown("### Quick Admin Overrides")
            new_idx = st.number_input("Set next_payout_index", min_value=1, step=1, value=int(next_idx or 1))
            if st.button("💾 Save next_payout_index", use_container_width=True):
                try:
                    sb_service.schema(schema).table("app_state").upsert({"id": 1, "next_payout_index": int(new_idx)}).execute()
                    st.success("Saved. Refresh.")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Save failed: {e}")

    st.divider()

    # Operational alerts (organizational standard)
    st.markdown("### Operational Alerts")
    alerts = []

    if not next_idx:
        alerts.append("Rotation state not initialized (next_payout_index is missing).")
    if pot_amount in (None, "", 0, 0.0):
        alerts.append("Pot amount is not available yet (no contributions recorded or pot view not ready).")

    if alerts:
        for a in alerts:
            st.warning(a)
    else:
        st.success("No critical alerts detected.")

    # Member lookup / details (keeps your useful dropdown)
    st.markdown("### Member Lookup")
    if not df_members.empty:
        df_members["label"] = df_members.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
        pick = st.selectbox("Select member", df_members["label"].tolist())
        row = df_members[df_members["label"] == pick].iloc[0].to_dict()
        st.write("Selected member id:", row.get("id"))
        st.write("Selected member:", row.get("name"))
    else:
        st.info("No members available for lookup.")

    with st.expander("Member Registry (preview)"):
        if not df_members.empty:
            st.dataframe(df_members, use_container_width=True, hide_index=True)
        else:
            st.info("No members to show.")
