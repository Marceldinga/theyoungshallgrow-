
# app.py  (Single updated code — Railway safe + fixes UnhashableParamError)
from __future__ import annotations

import streamlit as st
import pandas as pd
from supabase import create_client

from db import (
    get_secret,
    authed_client,
    current_session_id,
    get_app_state,
    load_member_registry,
    # schema_check_or_stop,  # run ONLY after login with authed client
)

APP_BRAND = "theyoungshallgrow"

st.set_page_config(
    page_title=f"{APP_BRAND} • Bank Dashboard",
    layout="wide",
    page_icon="🏦",
)

# -------------------------
# SECRETS (single source of truth)
# -------------------------
SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_ANON_KEY = get_secret("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY")

# -------------------------
# CLIENTS
# -------------------------
@st.cache_resource
def get_public_client():
    # safe to cache (not user-specific)
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

sb_public = get_public_client()

# -------------------------
# HELPERS
# -------------------------
def money(x):
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)

@st.cache_data(ttl=90)
def cached_current_session_id() -> str | None:
    return current_session_id(sb_public)

@st.cache_data(ttl=90)
def cached_app_state() -> dict:
    return get_app_state(sb_public)

@st.cache_data(ttl=90)
def cached_member_registry_rows():
    labels, label_to_id, label_to_name, df_members = load_member_registry(sb_public)
    rows = df_members.to_dict("records") if isinstance(df_members, pd.DataFrame) and not df_members.empty else []
    return labels, label_to_id, label_to_name, rows

# -------------------------
# AUTH placeholder
# -------------------------
def get_authed_client_after_login(session_obj):
    # no caching: token/user-specific
    return authed_client(SUPABASE_URL, SUPABASE_ANON_KEY, session_obj)

# -------------------------
# MAIN UI
# -------------------------
st.title(f"🏦 {APP_BRAND} • Bank Dashboard")

sid = cached_current_session_id()
app_state = cached_app_state()
labels, label_to_id, label_to_name, member_rows = cached_member_registry_rows()
df_members = pd.DataFrame(member_rows)

c1, c2, c3 = st.columns(3)
c1.metric("Current Session ID", sid or "N/A")
c2.metric("Members", f"{len(df_members):,}" if not df_members.empty else "0")
c3.metric("Next Payout Index", str(app_state.get("next_payout_index", "N/A")))

st.divider()

if labels:
    pick = st.selectbox("Select member", labels)
    mid = label_to_id.get(pick)
    st.write("Selected legacy_member_id:", mid)
else:
    st.warning("No members found in member_registry.")

with st.expander("Member Registry (preview)", expanded=False):
    if not df_members.empty:
        st.dataframe(df_members, use_container_width=True, hide_index=True)
    else:
        st.info("member_registry is empty or could not be loaded.")
