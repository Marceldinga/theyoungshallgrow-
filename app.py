
# app.py (fixed for Railway + Streamlit Cloud)

import os
import streamlit as st
import pandas as pd
from supabase import create_client

from db import get_secret, authed_client  # ✅ use db.py functions only

# -------------------------
# CONFIG
# -------------------------
APP_BRAND = "theyoungshallgrow"
APP_VERSION = "v2.5-fast"

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
    # ✅ safe to cache (not user-specific)
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

sb_public = get_public_client()

# ❌ DO NOT create/cache get_authed_client() here
# ✅ use authed_client(...) from db.py AFTER login

# -------------------------
# HELPERS
# -------------------------
def money(x):
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)

@st.cache_data(ttl=90 I'm)
def load_kpis(client):
    return client.rpc("dashboard_kpis").execute().data

@st.cache_data(ttl=300)
def load_registry(client):
    return pd.DataFrame(
        client.table("member_registry")
        .select("*")
        .order("legacy_member_id")
        .execute()
        .data
        or []
    )

# -------------------------
# AUTH STATE
# -------------------------
if "session" not in st.session_state:
    st.session_state.session = None

# -------------------------
# SIDEBAR AUTH
# -------------------------
with st.sidebar:
    st.markdown(f"### 🏦 {APP_BRAND}")

    if st.session_state.session is None:
        mode = st.radio("Mode", ["Login", "Sign Up"], horizontal=True)
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")

        if mode == "Login":
            if st.button("Login", use_container_width=True):
                try:
                    res = sb_public.auth.sign_in_with_password(
                        {"email": email, "password": password}
                    )
                    st.session_state.session = res.session
                    st.rerun()
                except Exception as e:
                    st.error("Login failed")
                    st.code(repr(e))
        else:
            if st.button("Create account", use_container_width=True):
                try:
                    sb_public.auth.sign_up({"email": email, "password": password})
                    st.success("Account created. Login now.")
                except Exception as e:
                    st.error("Sign up failed")
                    st.code(repr(e))
    else:
        st.success(st.session_state.session.user.email)
        if st.button("Logout", use_container_width=True):
            try:
                sb_public.auth.sign_out()
            except Exception:
                pass
            st.session_state.session = None
            st.rerun()

if st.session_state.session is None:
    st.info("Please log in from the sidebar.")
    st.stop()

# -------------------------
# AUTHED CLIENT (NO CACHE)
# -------------------------
client = authed_client(SUPABASE_URL, SUPABASE_ANON_KEY, st.session_state.session)

user_id = st.session_state.session.user.id
user_email = st.session_state.session.user.email

# -------------------------
# PROFILE CHECK
# -------------------------
@st.cache_data(ttl=300)
def get_profile(client, user_id):
    return (
        client.table("profiles")
        .select("role,approved,member_id")
        .eq("id", user_id)
        .single()
        .execute()
        .data
    )

profile = get_profile(client, user_id)

if not profile:
    st.error("Profile missing. Admin approval required.")
    st.stop()

if not profile.get("approved", False):
    st.warning("Account not approved yet.")
    st.stop()

admin_mode = str(profile.get("role", "")).lower().strip() == "admin"

# -------------------------
# TOP BAR
# -------------------------
st.markdown(
    f"""
<div style="padding:14px;border-radius:16px;background:#0f1b31;margin-bottom:12px">
<b>{APP_BRAND}</b><br>
<small>Bank Dashboard • {APP_VERSION}</small><br>
User: {user_email} • Role: {profile['role']}
</div>
""",
    unsafe_allow_html=True,
)

# -------------------------
# LOAD KPI DATA (ONE CALL)
# -------------------------
kpis = load_kpis(client)

# -------------------------
# KPI ROW
# -------------------------
cols = st.columns(8)

cols[0].metric("Contribution Pot", money(kpis.get("pot_amount", 0)))
cols[1].metric("All-time Contributions", money(kpis.get("total_contributions", 0)))
cols[2].metric("Foundation Total", money(kpis.get("foundation_total", 0)))

loan = kpis.get("loan_stats", {}) or {}
cols[3].metric("Active Loans", str(loan.get("active_count", 0)))
cols[4].metric("Total Due", money(loan.get("total_due", 0)))
cols[5].metric("Principal", money(loan.get("principal", 0)))
cols[6].metric("Interest", money(loan.get("interest", 0)))

fines = kpis.get("fines", {}) or {}
cols[7].metric("Unpaid Fines", money(fines.get("unpaid", 0)))

st.divider()

# -------------------------
# TABS
# -------------------------
tabs = st.tabs(["Overview", "Members", "Audit Log"])

with tabs[0]:
    st.success("Dashboard loaded from cached KPIs (fast).")

with tabs[1]:
    df_members = load_registry(client)
    st.dataframe(df_members, use_container_width=True, hide_index=True)

with tabs[2]:
    if st.checkbox("Load audit log"):
        df_audit = pd.DataFrame(
            client.table("audit_log")
            .select("*")
            .order("created_at", desc=True)
            .limit(500)
            .execute()
            .data
            or []
        )
        st.dataframe(df_audit, use_container_width=True, hide_index=True)
