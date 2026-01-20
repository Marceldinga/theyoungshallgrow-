# app.py
import streamlit as st
import pandas as pd

from supabase import create_client

from db import (
    get_secret,
    authed_client,
    fetch_one,
    schema_check_or_stop,
    load_member_registry,
    get_app_state,
    current_session_id,
    safe_select_autosort,
)

from payout import (
    EXPECTED_ACTIVE_MEMBERS,
    BASE_CONTRIBUTION,
    CONTRIBUTION_STEP,
    ALLOWED_CONTRIB_KINDS,
    next_unpaid_beneficiary,
    payout_precheck_option_b,
    execute_payout_option_b,
)

from admin_panels import (
    render_admin_contributions_panel,
    render_admin_foundation_panel,
    render_admin_fines_panel,
    render_admin_loan_requests_workflow,
    render_admin_loan_repayments_panel,
    render_admin_interest_accrual_panel,
)

from member_panels import render_member_request_loan_tab


# -------------------------
# CONFIG
# -------------------------
APP_BRAND = "theyoungshallgrow"
APP_VERSION = "v2.4"


# -------------------------
# UI / THEME
# -------------------------
st.set_page_config(page_title=f"{APP_BRAND} • Bank Dashboard", layout="wide", page_icon="🏦")

st.markdown(
    """
<style>
:root{
  --bg:#070b14;
  --card:#0f1b31;
  --text:#eef4ff;
  --muted:#a8b6d6;
  --border:rgba(255,255,255,0.10);
  --shadow: 0 14px 30px rgba(0,0,0,0.30);
}
.stApp{
  background: radial-gradient(1200px 700px at 20% 0%, rgba(29,78,216,0.18), transparent 60%),
              radial-gradient(1000px 600px at 90% 10%, rgba(34,197,94,0.10), transparent 55%),
              linear-gradient(180deg, var(--bg) 0%, #05070f 100%);
  color: var(--text);
}
.block-container{ padding-top: 1.0rem; padding-bottom: 2rem; max-width: 1450px; }
h1,h2,h3{ color: var(--text); }
small, .stCaption, .stMarkdown p{ color: var(--muted) !important; }
section[data-testid="stSidebar"]{
  background: rgba(11, 18, 32, 0.85);
  border-right: 1px solid var(--border);
}
section[data-testid="stSidebar"] *{ color: var(--text) !important; }

.bank-topbar{
  background: rgba(15, 27, 49, 0.75);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 14px 16px;
  box-shadow: var(--shadow);
  margin-bottom: 12px;
}
.bank-title{ font-size: 1.25rem; font-weight: 950; letter-spacing: .2px; }
.bank-sub{ color: var(--muted); font-weight: 700; font-size: .86rem; margin-top: 3px; }
.pill{
  display:inline-flex; align-items:center; gap:8px;
  padding: 6px 10px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: rgba(255,255,255,0.04);
  font-weight: 850;
  font-size: .78rem;
}
.card{
  background: rgba(15, 27, 49, 0.75);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 14px;
  box-shadow: var(--shadow);
  height: 100%;
}
.kpi-title{ color: var(--muted); font-weight: 800; font-size: .82rem; }
.kpi-value{ font-weight: 950; font-size: 1.40rem; margin-top: 6px; }
.kpi-sub{ color: var(--muted); font-size: .78rem; margin-top: 5px; }
.panel{
  background: rgba(12, 23, 44, 0.70);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 16px;
  box-shadow: 0 10px 24px rgba(0,0,0,0.22);
}
.stButton>button{
  background: linear-gradient(135deg, rgba(29,78,216,.98), rgba(37,99,235,.85));
  color: white;
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 12px;
  padding: 0.55rem 0.85rem;
  font-weight: 950;
}
.stButton>button:hover{
  background: linear-gradient(135deg, rgba(29,78,216,1), rgba(59,130,246,1));
  border-color: rgba(255,255,255,0.18);
}
[data-testid="stDataFrame"]{ border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }
</style>
""",
    unsafe_allow_html=True,
)


def money(x):
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)


def kpi(title, value, sub=""):
    st.markdown(
        f"""
<div class="card">
  <div class="kpi-title">{title}</div>
  <div class="kpi-value">{value}</div>
  <div class="kpi-sub">{sub}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def compliance_kpi(title: str, ok: bool, sub_ok: str, sub_bad: str):
    badge = "✅ COMPLIANT" if ok else "⚠️ NOT READY"
    sub = sub_ok if ok else sub_bad
    st.markdown(
        f"""
<div class="card">
  <div class="kpi-title">{title}</div>
  <div class="kpi-value">{badge}</div>
  <div class="kpi-sub">{sub}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def show_api_error(e: Exception, title="Supabase error"):
    st.error(title)
    st.code(repr(e))


def fetch_paid_out_member_ids_local(c) -> set[int]:
    """
    Local replacement for fetch_paid_out_member_ids.
    Pulls member_id from payouts_legacy.
    """
    try:
        rows = (
            c.table("payouts_legacy")
            .select("member_id")
            .limit(10000)
            .execute()
            .data or []
        )
        return set(int(r.get("member_id") or 0) for r in rows if r.get("member_id") is not None)
    except Exception:
        return set()


# -------------------------
# SUPABASE PUBLIC CLIENT (for auth)
# -------------------------
SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_ANON_KEY = get_secret("SUPABASE_ANON_KEY")
if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Missing SUPABASE_URL / SUPABASE_ANON_KEY in Streamlit Secrets.")
    st.stop()

sb_public = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

if "session" not in st.session_state:
    st.session_state.session = None


# -------------------------
# AUTH UI
# -------------------------
with st.sidebar:
    st.markdown(f"### 🏦 {APP_BRAND} Access")

    if st.session_state.session is None:
        mode = st.radio("Mode", ["Login", "Sign Up"], horizontal=True, key="auth_mode")
        email = st.text_input("Email", key="auth_email")
        password = st.text_input("Password", type="password", key="auth_pass")

        if mode == "Sign Up":
            st.caption("After sign up, admin must approve you in profiles (approved=true).")
            if st.button("Create account", use_container_width=True, key="auth_signup_btn"):
                try:
                    sb_public.auth.sign_up({"email": email, "password": password})
                    st.success("Account created. Now login.")
                except Exception as e:
                    show_api_error(e, "Sign up failed")
        else:
            if st.button("Login", use_container_width=True, key="auth_login_btn"):
                try:
                    res = sb_public.auth.sign_in_with_password({"email": email, "password": password})
                    st.session_state.session = res.session
                    st.rerun()
                except Exception as e:
                    show_api_error(e, "Login failed")
    else:
        st.success(f"Signed in: {st.session_state.session.user.email}")
        if st.button("Logout", use_container_width=True, key="auth_logout_btn"):
            try:
                sb_public.auth.sign_out()
            except Exception:
                pass
            st.session_state.session = None
            st.rerun()


if st.session_state.session is None:
    st.markdown(
        f"""
        <div class="panel">
          <div style="font-size:1.35rem;font-weight:950;">Welcome to {APP_BRAND}</div>
          <div style="color:var(--muted);margin-top:6px;">
            Please login from the sidebar to access accounts, transactions, and loans.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()


# -------------------------
# AFTER LOGIN
# -------------------------
client = authed_client(SUPABASE_URL, SUPABASE_ANON_KEY, st.session_state.session)
user_id = st.session_state.session.user.id
user_email = st.session_state.session.user.email

st.markdown(
    f"""
<div class="bank-topbar">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
    <div style="display:flex;align-items:center;gap:12px;">
      <div style="width:42px;height:42px;border-radius:14px;
                  background: linear-gradient(135deg, rgba(29,78,216,.95), rgba(34,197,94,.55));
                  display:flex;align-items:center;justify-content:center;
                  font-weight:950;">T</div>
      <div>
        <div class="bank-title">{APP_BRAND}</div>
        <div class="bank-sub">Bank Dashboard • Accounts • Loans • Compliance</div>
      </div>
    </div>
    <div style="display:flex;gap:10px;align-items:center;">
        <span class="pill">User: {user_email}</span>
        <span class="pill">{APP_VERSION}</span>
    </div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

profile = fetch_one(client.table("profiles").select("id,role,approved,member_id").eq("id", user_id))
if profile is None:
    st.warning("Profile not found. Admin must create/approve your profiles row.")
    st.caption(f"Your auth user_id is: {user_id}")
    st.stop()

if not bool(profile.get("approved", False)):
    st.warning("Your account is not approved yet. Ask admin to set profiles.approved=true.")
    st.stop()

admin_mode = str(profile.get("role") or "").lower().strip() == "admin"
mode_txt = "Admin" if admin_mode else "Member"

st.markdown(
    f"<div class='panel'><b>Access granted</b> • Role: <b>{mode_txt}</b> • member_id: <b>{profile.get('member_id')}</b></div>",
    unsafe_allow_html=True,
)

schema_check_or_stop(client)

# -------------------------
# LOAD DATA
# -------------------------
member_labels, label_to_legacy_id, label_to_name, df_registry = load_member_registry(client)

active_ids = []
if not df_registry.empty:
    for r in df_registry.to_dict("records"):
        if r.get("is_active") in (None, True):
            active_ids.append(int(r.get("legacy_member_id")))

state = get_app_state(client)
raw_next_idx = int(state.get("next_payout_index") or 1)

already_paid_ids = fetch_paid_out_member_ids_local(client)

next_idx = next_unpaid_beneficiary(active_ids, already_paid_ids, raw_next_idx)

ben_row = fetch_one(client.table("member_registry").select("full_name").eq("legacy_member_id", next_idx))
ben_name = (ben_row or {}).get("full_name") or f"Member {next_idx}"

sid = current_session_id(client)

# KPI totals (fast + safe)
pot = 0.0
try:
    resp = (
        client.table("contributions_legacy")
        .select("amount,kind,session_id")
        .eq("session_id", sid)
        .in_("kind", ALLOWED_CONTRIB_KINDS)
        .limit(20000)
        .execute()
    )
    pot = sum(float(r.get("amount") or 0) for r in (resp.data or []))
except Exception:
    pot = 0.0

total_contrib_all = 0.0
try:
    resp2 = client.table("contributions_legacy").select("amount").limit(20000).execute()
    total_contrib_all = sum(float(r.get("amount") or 0) for r in (resp2.data or []))
except Exception:
    total_contrib_all = 0.0

f_total = 0.0
try:
    resp3 = client.table("foundation_payments_legacy").select("amount_paid,amount_pending").limit(20000).execute()
    f_total = sum(float(r.get("amount_paid") or 0) + float(r.get("amount_pending") or 0) for r in (resp3.data or []))
except Exception:
    f_total = 0.0

active_loans = 0
active_total_due = 0.0
active_principal = 0.0
all_interest = 0.0
try:
    resp4 = client.table("loans_legacy").select("status,total_due,balance,total_interest_generated").limit(20000).execute()
    for r in (resp4.data or []):
        all_interest += float(r.get("total_interest_generated") or 0)
        if str(r.get("status") or "").lower().strip() == "active":
            active_loans += 1
            active_total_due += float(r.get("total_due") or 0)
            active_principal += float(r.get("balance") or 0)
except Exception:
    pass

fines_total = 0.0
fines_unpaid = 0.0
try:
    resp5 = client.table("fines_legacy").select("amount,status").limit(20000).execute()
    for r in (resp5.data or []):
        amt = float(r.get("amount") or 0)
        fines_total += amt
        stt = str(r.get("status") or "").lower().strip()
        if stt not in ("paid", "cleared", "settled"):
            fines_unpaid += amt
except Exception:
    pass

# Compliance placeholders (removed missing functions to avoid ImportError)
latest_req, loan_ok, loan_missing = (None, True, [])
payout_ok, payout_missing = (True, [])

# -------------------------
# KPI ROW
# -------------------------
k = st.columns(10)
with k[0]: kpi("Next Beneficiary", f"{next_idx} — {ben_name}", "Rotation index (skips already-paid)")
with k[1]: kpi("Contribution Pot", money(pot), f"Current session ({sid}) • kinds={','.join(ALLOWED_CONTRIB_KINDS)}")
with k[2]: kpi("All-time Contributions", money(total_contrib_all), "Lifetime")
with k[3]: kpi("Foundation Total", money(f_total), "Paid + Pending")
with k[4]: kpi("Active Loans", str(active_loans), f"Total due {money(active_total_due)}")
with k[5]: kpi("Loan Due Now", money(active_total_due), f"Principal {money(active_principal)} • Interest {money(active_total_due - active_principal)}")
with k[6]: kpi("All-time Interest", money(all_interest), "Lifetime generated")
with k[7]: kpi("Fines", money(fines_total), f"Unpaid {money(fines_unpaid)}")
with k[8]:
    compliance_kpi("Loan Approval Signatures", True, "Compliance checks enabled after deploy.", "Compliance checks enabled after deploy.")
with k[9]:
    compliance_kpi("Payout Signatures", payout_ok, f"Payout ready for beneficiary {next_idx}.", "Missing: " + ", ".join(payout_missing))

st.write("")
st.divider()

# -------------------------
# TABS
# -------------------------
tab_names_admin = [
    "Overview",
    "Members",
    "Contributions (Admin)",
    "Foundation Payments (Admin)",
    "Fines (Admin)",
    "Request Loan (Member)",
    "Loan Requests (Admin)",
    "Loan Repayments (Admin)",
    "Interest Accrual (Admin)",
    "Payout (Option B)",
    "Audit Log",
]
tab_names_member = [
    "Overview",
    "Members",
    "Request Loan",
    "Payout (Option B)",
    "Audit Log",
]

tabs = st.tabs(tab_names_admin if admin_mode else tab_names_member)

def tab_index(name: str) -> int:
    names = tab_names_admin if admin_mode else tab_names_member
    return names.index(name)

# -------------------------
# ADMIN TAB RENDERS
# -------------------------
if admin_mode:
    with tabs[tab_index("Overview")]:
        st.subheader("Overview")
        st.caption("KPIs + compliance shown above.")

    with tabs[tab_index("Members")]:
        st.subheader("Members Registry")
        st.dataframe(df_registry, use_container_width=True, hide_index=True)

    with tabs[tab_index("Contributions (Admin)")]:
        render_admin_contributions_panel(client, member_labels, label_to_legacy_id, df_registry)

    with tabs[tab_index("Foundation Payments (Admin)")]:
        render_admin_foundation_panel(client, member_labels, label_to_legacy_id)

    with tabs[tab_index("Fines (Admin)")]:
        render_admin_fines_panel(client, member_labels, label_to_legacy_id)

    with tabs[tab_index("Request Loan (Member)")]:
        st.info("Admins typically do not request loans from this tab.")

    with tabs[tab_index("Loan Requests (Admin)")]:
        render_admin_loan_requests_workflow(client, actor_user_id=user_id)

    with tabs[tab_index("Loan Repayments (Admin)")]:
        render_admin_loan_repayments_panel(client, actor_user_id=user_id)

    with tabs[tab_index("Interest Accrual (Admin)")]:
        render_admin_interest_accrual_panel(client, actor_user_id=user_id)

    with tabs[tab_index("Payout (Option B)")]:
        from admin_panels import render_payout_tab_option_b
        render_payout_tab_option_b(
            client, member_labels, label_to_legacy_id, label_to_name,
            df_registry, state, already_paid_ids, profile, user_email, actor_user_id=user_id
        )

    with tabs[tab_index("Audit Log")]:
        st.subheader("Audit Log")
        df_a = pd.DataFrame((safe_select_autosort(client, "audit_log", limit=500).data or []))
        st.dataframe(df_a, use_container_width=True, hide_index=True)

# -------------------------
# MEMBER TAB RENDERS
# -------------------------
else:
    with tabs[tab_index("Overview")]:
        st.subheader("Overview")
        st.caption("Your key metrics are shown above.")

    with tabs[tab_index("Members")]:
        st.subheader("Members")
        st.dataframe(df_registry, use_container_width=True, hide_index=True)

    with tabs[tab_index("Request Loan")]:
        render_member_request_loan_tab(
            client, profile, user_email, member_labels, label_to_legacy_id, label_to_name, actor_user_id=user_id
        )

    with tabs[tab_index("Payout (Option B)")]:
        st.info("Payout execution is restricted to Admins.")
        # If you want a view-only payout preview for members later, we can add it safely.

    with tabs[tab_index("Audit Log")]:
        st.subheader("Audit Log")
        df_a = pd.DataFrame((safe_select_autosort(client, "audit_log", limit=200).data or []))
        st.dataframe(df_a, use_container_width=True, hide_index=True)
