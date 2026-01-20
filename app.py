
# app.py  (Single updated code — fixes UnhashableParamError + adds refresh + safer unpack)
from __future__ import annotations

import streamlit as st
import pandas as pd
from supabase import create_client

# Import your helpers/loaders from db.py
from db import (
    get_secret,
    authed_client,
    schema_check_or_stop,
    current_session_id,
    get_app_state,
    load_member_registry,
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
def get_public_client(url: str, anon_key: str):
    # ✅ safe to cache (resource), not user-specific
    return create_client(url, anon_key)

sb_public = get_public_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# -------------------------
# (OPTIONAL) Run schema check once
# -------------------------
@st.cache_resource
def _run_schema_check_once(url: str, anon_key: str):
    sb = get_public_client(url, anon_key)
    schema_check_or_stop(sb)

_run_schema_check_once(SUPABASE_URL, SUPABASE_ANON_KEY)

# -------------------------
# HELPERS
# -------------------------
def money(x):
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)

# ============================================================
# ✅ FIX: CACHE WRAPPERS MUST NOT TAKE "client" AS A PARAM
#     (only primitives like url/key are OK)
# ============================================================

@st.cache_data(ttl=90)
def cached_current_session_id(url: str, anon_key: str) -> str | None:
    sb = get_public_client(url, anon_key)
    return current_session_id(sb)

@st.cache_data(ttl=90)
def cached_app_state(url: str, anon_key: str) -> dict:
    sb = get_public_client(url, anon_key)
    return get_app_state(sb)

@st.cache_data(ttl=90)
def cached_member_registry(url: str, anon_key: str):
    """
    Returns:
      labels, label_to_id, label_to_name, df_members
    """
    sb = get_public_client(url, anon_key)
    return load_member_registry(sb)

# -------------------------
# AUTH (example placeholder)
# -------------------------
# If you already have login working, keep it.
# The key rule:
#   ✅ DO NOT cache authed_client()
#   ✅ DO NOT pass authed client into @st.cache_data
def get_authed_client_after_login(session_obj):
    # ✅ no caching: token/user-specific
    return authed_client(SUPABASE_URL, SUPABASE_ANON_KEY, session_obj)

# -------------------------
# TOP BAR ACTIONS
# -------------------------
bar1, bar2 = st.columns([1, 0.25])
with bar2:
    if st.button("🔄 Refresh data", use_container_width=True):
        # Clears ONLY data cache; keep resource cache (client) intact
        st.cache_data.clear()
        st.rerun()

# -------------------------
# MAIN UI
# -------------------------
st.title(f"🏦 {APP_BRAND} • Bank Dashboard")

# Load cached public data (safe)
sid = cached_current_session_id(SUPABASE_URL, SUPABASE_ANON_KEY)
app_state = cached_app_state(SUPABASE_URL, SUPABASE_ANON_KEY)

res = cached_member_registry(SUPABASE_URL, SUPABASE_ANON_KEY)
if not res:
    labels, label_to_id, label_to_name, df_members = [], {}, {}, pd.DataFrame()
else:
    labels, label_to_id, label_to_name, df_members = res

# Example top KPIs
c1, c2, c3 = st.columns(3)
c1.metric("Current Session ID", sid or "N/A")
c2.metric("Members", f"{len(df_members):,}" if isinstance(df_members, pd.DataFrame) else "0")
c3.metric("Next Payout Index", str(app_state.get("next_payout_index", "N/A")))

st.divider()

# Example member selector
if labels:
    pick = st.selectbox("Select member", labels)
    mid = label_to_id.get(pick)
    st.write("Selected legacy_member_id:", mid)
else:
    st.warning("No members found in member_registry.")

# Show members table
with st.expander("Member Registry (preview)", expanded=False):
    if isinstance(df_members, pd.DataFrame) and not df_members.empty:
        st.dataframe(df_members, use_container_width=True)
    else:
        st.info("member_registry is empty or could not be loaded.")

# ------------------------------------------------------------
# IMPORTANT NOTES (already applied in this file)
# ------------------------------------------------------------
# ✅ @st.cache_resource: OK for public supabase client
# ✅ @st.cache_data: cached_* functions do NOT take a client param
# ✅ cached_* functions only accept primitives (url/key) and create/get client internally
# ❌ Do not wrap schema_check_or_stop inside @st.cache_data (it uses st.error + raises)
