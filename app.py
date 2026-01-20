import streamlit as st
import time

st.write("BOOTING APP ON RAILWAY...")
time.sleep(1)

from db import get_secret

# 🔑 DEFINE FIRST
SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_ANON_KEY = get_secret("SUPABASE_ANON_KEY")

# ✅ THEN CHECK
if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY")
# -------------------------
# CONFIG
# -------------------------
APP_BRAND = "theyoungshallgrow"
APP_VERSION = "v2.5-fast"
if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY")
st.set_page_config(
    page_title=f"{APP_BRAND} • Bank Dashboard",
    layout="wide",
    page_icon="🏦"
)

# -------------------------
# SECRETS
# -------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = st.secrets.get("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Missing Supabase secrets.")
    st.stop()

# -------------------------
# CLIENTS (CACHED)
# -------------------------
@st.cache_resource
def get_public_client():
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

sb_public = get_public_client()

@st.cache_resource
def get_authed_client(access_token: str):
    return create_client(
        SUPABASE_URL,
        SUPABASE_ANON_KEY,
        options={"global": {"headers": {"Authorization": f"Bearer {access_token}"}}},
    )

# -------------------------
# HELPERS
# -------------------------
def money(x):
    return f"{float(x):,.0f}"

@st.cache_data(ttl=90)
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
                    st.code(e)
        else:
            if st.button("Create account", use_container_width=True):
                sb_public.auth.sign_up({"email": email, "password": password})
                st.success("Account created. Login now.")
    else:
        st.success(st.session_state.session.user.email)
        if st.button("Logout", use_container_width=True):
            sb_public.auth.sign_out()
            st.session_state.session = None
            st.rerun()

if st.session_state.session is None:
    st.info("Please log in from the sidebar.")
    st.stop()

# -------------------------
# AUTHED CLIENT
# -------------------------
client = get_authed_client(st.session_state.session.access_token)
user_id = st.session_state.session.user.id
user_email = st.session_state.session.user.email

# -------------------------
# PROFILE CHECK (CACHED)
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

if not profile["approved"]:
    st.warning("Account not approved yet.")
    st.stop()

admin_mode = profile["role"] == "admin"

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

cols[0].metric("Contribution Pot", money(kpis["pot_amount"]))
cols[1].metric("All-time Contributions", money(kpis["total_contributions"]))
cols[2].metric("Foundation Total", money(kpis["foundation_total"]))

loan = kpis["loan_stats"]
cols[3].metric("Active Loans", loan["active_count"])
cols[4].metric("Total Due", money(loan["total_due"]))
cols[5].metric("Principal", money(loan["principal"]))
cols[6].metric("Interest", money(loan["interest"]))

fines = kpis["fines"]
cols[7].metric("Unpaid Fines", money(fines["unpaid"]))

st.divider()

# -------------------------
# TABS
# -------------------------
tabs = st.tabs(
    [
        "Overview",
        "Members",
        "Audit Log",
    ]
)

# -------------------------
# OVERVIEW
# -------------------------
with tabs[0]:
    st.success("Dashboard loaded from cached KPIs (fast).")

# -------------------------
# MEMBERS (CACHED)
# -------------------------
with tabs[1]:
    df_members = load_registry(client)
    st.dataframe(df_members, use_container_width=True, hide_index=True)

# -------------------------
# AUDIT LOG (LAZY)
# -------------------------
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
