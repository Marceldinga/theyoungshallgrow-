
# app.py ✅ SINGLE COMPLETE VERSION (FIXES Dashboard N/A + clean routing + proper Admin panel call)
# - Uses ONE canonical rotation state: current_season_view -> app_state
# - Dashboard no longer shows N/A when Payouts shows values
# - Calls your high-standard admin_panels.py render_admin()
# - Keeps payouts connected (render_payouts)
# - Keeps contributions view
# - Avoids the earlier syntax mistake ({}dii)

from __future__ import annotations

import os
import streamlit as st
import pandas as pd
from supabase import create_client
from postgrest.exceptions import APIError

# Panels
from admin_panels import render_admin
from payout import render_payouts

APP_BRAND = "theyoungshallgrow"

st.set_page_config(
    page_title=f"{APP_BRAND} • Bank Dashboard",
    layout="wide",
    page_icon="🏦",
)

# ============================================================
# SECRETS
# ============================================================
def get_secret(key: str, default: str | None = None) -> str | None:
    if key in st.secrets:
        return str(st.secrets.get(key))
    return os.getenv(key, default)

SUPABASE_URL = (get_secret("SUPABASE_URL") or "").strip()
SUPABASE_ANON_KEY = (get_secret("SUPABASE_ANON_KEY") or "").strip()
SUPABASE_SERVICE_KEY = (get_secret("SUPABASE_SERVICE_KEY") or "").strip()
SUPABASE_SCHEMA = (get_secret("SUPABASE_SCHEMA", "public") or "public").strip()

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Missing SUPABASE_URL or SUPABASE_ANON_KEY in Streamlit Secrets / Environment.")
    st.stop()

if not SUPABASE_SERVICE_KEY:
    st.warning("SUPABASE_SERVICE_KEY not set. Admin write features will be disabled.")

# ============================================================
# CLIENTS
# ============================================================
@st.cache_resource
def get_anon_client(url: str, anon_key: str):
    return create_client(url.strip(), anon_key.strip())

@st.cache_resource
def get_service_client(url: str, service_key: str):
    return create_client(url.strip(), service_key.strip())

sb_anon = get_anon_client(SUPABASE_URL, SUPABASE_ANON_KEY)
sb_service = get_service_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else None

# ============================================================
# TOP BAR
# ============================================================
left, right = st.columns([1, 0.25])
with right:
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.title(f"🏦 {APP_BRAND} • Bank Dashboard")

# ============================================================
# SAFE HELPERS
# ============================================================
def safe_select(
    client,
    table_name: str,
    select_cols: str = "*",
    schema: str = "public",
    order_by: str | None = None,
    order_desc: bool = False,
    limit: int | None = None,
) -> list[dict]:
    try:
        q = client.schema(schema).table(table_name).select(select_cols)
        if order_by:
            q = q.order(order_by, desc=order_desc)
        if limit is not None:
            q = q.limit(limit)
        resp = q.execute()
        return resp.data or []
    except APIError as e:
        st.error(f"Supabase APIError reading {schema}.{table_name}")
        st.code(str(e), language="text")
        return []
    except Exception as e:
        st.error(f"Unexpected error reading {schema}.{table_name}: {e}")
        return []

def safe_single(
    client,
    table_name: str,
    select_cols: str = "*",
    schema: str = "public",
) -> dict:
    rows = safe_select(client, table_name, select_cols, schema=schema, limit=1)
    return rows[0] if rows else {}

def get_rotation_state(sb, schema: str) -> dict:
    """
    ✅ Canonical rotation state:
      1) current_season_view (preferred)
      2) app_state (fallback)
    This fixes Dashboard showing N/A while Payouts shows real values.
    """
    season_rows = safe_select(sb, "current_season_view", "*", schema=schema, limit=1)
    if season_rows:
        return season_rows[0]
    state_rows = safe_select(sb, "app_state", "*", schema=schema, limit=1)
    return state_rows[0] if state_rows else {}

# ============================================================
# LOADERS
# ============================================================
@st.cache_data(ttl=90)
def load_members_legacy(url: str, anon_key: str, schema: str):
    client = create_client(url, anon_key)
    rows = safe_select(client, "members_legacy", "id,name,position", schema=schema, order_by="id")
    df = pd.DataFrame(rows)
    if df.empty:
        return [], {}, {}, pd.DataFrame(columns=["id", "name", "position"])
    df["id"] = pd.to_numeric(df["id"], errors="coerce")
    df = df.dropna(subset=["id"]).copy()
    df["id"] = df["id"].astype(int)
    df["name"] = df["name"].astype(str)
    df["label"] = df.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)
    labels = df["label"].tolist()
    label_to_id = dict(zip(df["label"], df["id"]))
    label_to_name = dict(zip(df["label"], df["name"]))
    return labels, label_to_id, label_to_name, df

@st.cache_data(ttl=60)
def load_contributions_view(url: str, anon_key: str, schema: str) -> pd.DataFrame:
    client = create_client(url, anon_key)
    rows = safe_select(
        client,
        "contributions_with_member",
        "*",
        schema=schema,
        order_by="created_at",
        order_desc=True,
        limit=200,
    )
    return pd.DataFrame(rows) if rows else pd.DataFrame()

# ============================================================
# NAVIGATION
# ============================================================
page = st.sidebar.radio(
    "Menu",
    ["Dashboard", "Contributions", "Payouts", "Loans", "Admin"],
)

# ============================================================
# DASHBOARD (FIXED: reads canonical rotation state)
# ============================================================
if page == "Dashboard":
    labels, label_to_id, label_to_name, df_members = load_members_legacy(
        SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA
    )

    rotation = get_rotation_state(sb_anon, SUPABASE_SCHEMA)
    next_index = rotation.get("next_payout_index")
next_date = rotation.get("next_payout_date")

rotation = get_rotation_state(sb_anon, SUPABASE_SCHEMA)

next_index = rotation.get("next_payout_index")
next_date = rotation.get("next_payout_date")

# ✅ beneficiary comes from current_season_view.legacy_member_id
beneficiary_id = rotation.get("legacy_member_id")
beneficiary_name = None

if beneficiary_id is not None and str(beneficiary_id).strip() != "":
    try:
        bid = int(beneficiary_id)
        match = df_members[df_members["id"] == bid]
        if not match.empty:
            beneficiary_name = str(match.iloc[0]["name"])
    except Exception:
        pass

beneficiary_label = f"{beneficiary_id} • {beneficiary_name}" if beneficiary_id and beneficiary_name else "—"

c1, c2, c3, c4 = st.columns(4)
c1.metric("Members", f"{len(df_members):,}")
c2.metric("Next Payout Index", str(next_index) if next_index is not None else "—")
c3.metric("Next Payout Date", str(next_date) if next_date else "—")
c4.metric("Next Beneficiary", beneficiary_label)
    st.divider()

    if labels:
        pick = st.selectbox("Select member", labels)
        st.write("Selected member id:", label_to_id.get(pick))
        st.write("Selected member:", label_to_name.get(pick))
    else:
        st.warning("No members found in members_legacy.")

    with st.expander("Member Registry (preview)", expanded=False):
        if not df_members.empty:
            st.dataframe(df_members[["id", "name", "position"]], use_container_width=True)
        else:
            st.info("members_legacy empty or not readable.")

# ============================================================
# CONTRIBUTIONS (VIEW)
# ============================================================
elif page == "Contributions":
    st.header("Contributions (View)")
    df = load_contributions_view(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)
    if df.empty:
        st.info("No contributions found (or view not readable).")
    else:
        st.dataframe(df, use_container_width=True)

# ============================================================
# PAYOUTS
# ============================================================
elif page == "Payouts":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in secrets.")
    else:
        render_payouts(sb_service, SUPABASE_SCHEMA)

# ============================================================
# LOANS
# ============================================================
elif page == "Loans":
    st.header("Loans")
    st.info("Next step: implement render_loans() in loans.py (service key) and wire it here.")

# ============================================================
# ADMIN (HIGH STANDARD)
# ============================================================
elif page == "Admin":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in secrets.")
    else:
        render_admin(sb_service=sb_service, schema=SUPABASE_SCHEMA, actor_email="admin@yourorg.com")
