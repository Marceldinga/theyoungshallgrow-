
# app.py ✅ CLEAN ROUTER ONLY
from __future__ import annotations

import os
import streamlit as st
from supabase import create_client

APP_BRAND = "theyoungshallgrow"

st.set_page_config(
    page_title=f"{APP_BRAND} • Bank Dashboard",
    layout="wide",
    page_icon="🏦",
)

# -------------------------
# Secrets
# -------------------------
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

# -------------------------
# Clients
# -------------------------
@st.cache_resource
def get_anon_client(url: str, anon_key: str):
    return create_client(url.strip(), anon_key.strip())

@st.cache_resource
def get_service_client(url: str, service_key: str):
    return create_client(url.strip(), service_key.strip())

sb_anon = get_anon_client(SUPABASE_URL, SUPABASE_ANON_KEY)
sb_service = get_service_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else None

# -------------------------
# Top bar
# -------------------------
left, right = st.columns([1, 0.25])
with right:
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.title(f"🏦 {APP_BRAND} • Bank Dashboard")

# -------------------------
# Import panels (keep router clean)
# -------------------------
# Dashboard panel (new)
from dashboard_panel import render_dashboard  # ✅ you will add this file below

# Existing payout module (already in your repo)
from payout import render_payouts

# Optional: if you haven’t built these yet, they won’t break the app
try:
    from loans import render_loans
except Exception:
    render_loans = None

try:
    from admin_panels import render_admin
except Exception:
    render_admin = None

try:
    from member_panels import render_contributions
except Exception:
    render_contributions = None

try:
    from audit import render_audit
except Exception:
    render_audit = None

# -------------------------
# Navigation (organizational standard: consistent sections)
# -------------------------
page = st.sidebar.radio(
    "Menu",
    ["Dashboard", "Contributions", "Payouts", "Loans", "Admin", "Audit"],
)

if page == "Dashboard":
    render_dashboard(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)

elif page == "Contributions":
    if render_contributions:
        render_contributions(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)
    else:
        st.info("Contributions panel not wired yet. Next step: create render_contributions() in member_panels.py.")

elif page == "Payouts":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in secrets.")
    else:
        render_payouts(sb_service, SUPABASE_SCHEMA)

elif page == "Loans":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in secrets.")
    elif render_loans:
        render_loans(sb_service=sb_service, schema=SUPABASE_SCHEMA)
    else:
        st.info("Loans panel not wired yet. Next step: create render_loans() in loans.py.")

elif page == "Admin":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in secrets.")
    elif render_admin:
        render_admin(sb_service=sb_service, schema=SUPABASE_SCHEMA)
    else:
        st.info("Admin panel not wired yet. Next step: create render_admin() in admin_panels.py.")

elif page == "Audit":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in secrets.")
    elif render_audit:
        render_audit(sb_service=sb_service, schema=SUPABASE_SCHEMA)
    else:
        st.info("Audit panel not wired yet. Next step: create render_audit() in audit.py.")
