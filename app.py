
# app.py  (Single updated code — fixes UnhashableParamError)
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
def get_public_client():
    # ✅ safe to cache (resource), not user-specific
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

sb_public = get_public_client()

# -------------------------
# (OPTIONAL) Run schema check once
# -------------------------
@st.cache_resource
def _run_schema_check_once():
    schema_check_or_stop(sb_public)

_run_schema_check_once()

# -------------------------
# HELPERS
# -------------------------
def money(x):
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)

# ============================================================
# ✅ FIX: CACHE WRAPPERS MUST NOT TAKE "c" (client) AS A PARAM
# ============================================================

@st.cache_data(ttl=90)
def cached_current_session_id() -> str | None:
    return current_session_id(sb_public)

@st.cache_data(ttl=90)
def cached_app_state() -> dict:
    return get_app_state(sb_public)

@st.cache_data(ttl=90)
def cached_member_registry():
    """
    Returns:
      labels, label_to_id, label_to_name, df_members
    """
    return load_member_registry(sb_public)

# -------------------------
# AUTH (example placeholder)
# -------------------------
# If you already have login working, keep it.
# The key rule: DO NOT cache authed_client() and DO NOT pass authed client into @st.cache_data.
def get_authed_client_after_login(session_obj):
    # ✅ no caching: token/user-specific
    return authed_client(SUPABASE_URL, SUPABASE_ANON_KEY, session_obj)

# -------------------------
# MAIN UI (example)
# -------------------------
st.title(f"🏦 {APP_BRAND} • Bank Dashboard")

# Load cached public data (safe)
sid = cached_current_session_id()
app_state = cached_app_state()
labels, label_to_id, label_to_name, df_members = cached_member_registry()

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
# ✅ @st.cache_resource: OK for sb_public client
# ✅ @st.cache_data: NEVER accepts sb_public / authed client as a function parameter
# ✅ cached_* functions call db.py loaders internally using the global sb_public
# ❌ Do not wrap schema_check_or_stop inside @st.cache_data (it uses st.error + raises)
