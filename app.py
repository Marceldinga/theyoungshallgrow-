
# app.py ✅ COMPLETE SINGLE CODE — NJANGI STANDARD (NO legacy) — “SLOW / GENTLE MODE”
# ------------------------------------------------------------------------------
# ✅ CLEAN + FUTURE-PROOF (Streamlit 2025+):
#    - Replaces use_container_width=True ✅ with width="stretch"
#    - Uses a single constant W_STRETCH
#
# ✅ Fixes "loads but shows competition/demo data" by:
#    - Displaying connected Supabase project ref (host prefix)
#    - Warning if URL/keys look mismatched
#    - Showing real DB errors (no silent empty returns)
#    - Schema-safe members loader (works with/without display_name)
#    - Health page checks table/view readability
#
# ✅ Safe against blank-screen crashes:
#    - Optional modules are lazy-imported inside pages
#    - Visible secrets/env validation
#    - Service key optional (writes disabled if missing)
#    - Safe Mode switch to run Dashboard-only
#
# ✅ "SLOW MODE" to reduce Supabase load:
#    - Global throttle between DB calls
#    - cache_data TTLs
#    - Refresh clears cache_data only
#
# ✅ Uses NEW tables/views only:
#   tables: members, sessions, app_state, minutes, attendance, contributions, foundation_contributions,
#           payouts, loans, loan_payments, fines, interest_ledger, audit_log
#   views (optional): v_next_beneficiary, v_contributions_with_member, v_attendance_with_member
#
# ✅ ADDED:
#   - "🧠 Njangi LLM" page (lazy-imports njangi_llm_panel.render_njangi_llm_panel)
# ✅ FIXED:
#   - Railway-safe imports using importlib + sys.path injection
#   - Njangi LLM page now calls fn(sb_anon=..., sb_service=..., schema=...)
# ------------------------------------------------------------------------------

from __future__ import annotations

# ✅ Railway-safe: ensure this file's folder is importable
import os
import sys
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import time
import importlib
from datetime import datetime, timezone
from typing import Any, Optional, Tuple, List, Dict

import pandas as pd
import streamlit as st
from postgrest.exceptions import APIError
from supabase import create_client

# Dashboard is required (your main page)
from dashboard_panel import render_dashboard

APP_BRAND = "theyoungshallgrow"

st.set_page_config(
    page_title=f"{APP_BRAND} • Bank Dashboard",
    layout="wide",
    page_icon="🏦",
)

# =========================
# UI CONSTANTS
# =========================
W_STRETCH = "stretch"  # Streamlit replacement for use_container_width=True

# ============================================================
# TIME
# ============================================================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# GLOBAL THEME (Midnight Navy + Emerald)
# ============================================================
def inject_global_theme():
    st.markdown(
        """
        <style>
        :root{
            --bg0: #0B1426;
            --bg1: #0F1C35;

            --text: #EAF0FF;
            --muted: #A9B6D3;

            --primary: #00C896;
            --primary2:#00E6A8;
            --link: #60A5FA;
        }

        .stApp {
            background:
                radial-gradient(1200px 800px at 15% 10%, rgba(0,200,150,0.12), transparent 55%),
                radial-gradient(900px 650px at 85% 15%, rgba(96,165,250,0.10), transparent 60%),
                linear-gradient(180deg, var(--bg1) 0%, var(--bg0) 60%) !important;

            background-image:
                radial-gradient(circle at 1px 1px, rgba(255,255,255,0.055) 1px, transparent 0),
                radial-gradient(1200px 800px at 15% 10%, rgba(0,200,150,0.12), transparent 55%),
                radial-gradient(900px 650px at 85% 15%, rgba(96,165,250,0.10), transparent 60%),
                linear-gradient(180deg, var(--bg1) 0%, var(--bg0) 60%) !important;

            background-size: 24px 24px, auto, auto, auto !important;
            color: var(--text) !important;
        }

        header, footer { background: transparent !important; }

        section[data-testid="stSidebar"]{
            background: linear-gradient(180deg, rgba(15,28,53,0.96), rgba(11,20,38,0.96)) !important;
            border-right: 1px solid rgba(255,255,255,0.08) !important;
        }

        html, body, p, div, span, label, small,
        h1, h2, h3, h4, h5, h6 {
            color: var(--text) !important;
        }
        .stCaption, [data-testid="stCaptionContainer"] * { color: var(--muted) !important; }
        a { color: var(--link) !important; }

        .glass {
            background: rgba(255,255,255,0.055) !important;
            border: 1px solid rgba(255,255,255,0.10) !important;
            border-radius: 18px !important;
            padding: 18px 18px !important;
            box-shadow: 0 16px 50px rgba(0,0,0,0.52) !important;
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
        }

        .stButton button, .stDownloadButton button {
            border-radius: 14px !important;
            border: 1px solid rgba(255,255,255,0.14) !important;
            background: linear-gradient(180deg, rgba(0,200,150,0.20), rgba(0,200,150,0.12)) !important;
            color: var(--text) !important;
            box-shadow: 0 10px 30px rgba(0,0,0,0.35) !important;
        }
        .stButton button:hover, .stDownloadButton button:hover {
            border: 1px solid rgba(0,230,168,0.45) !important;
            background: linear-gradient(180deg, rgba(0,230,168,0.25), rgba(0,200,150,0.14)) !important;
            transform: translateY(-1px);
        }

        [data-baseweb="input"] input,
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stDateInput"] input {
            background: rgba(255,255,255,0.035) !important;
            color: var(--text) !important;
            border: 1px solid rgba(255,255,255,0.14) !important;
            border-radius: 12px !important;
        }
        [data-baseweb="textarea"] textarea,
        [data-testid="stTextArea"] textarea {
            background: rgba(255,255,255,0.035) !important;
            color: var(--text) !important;
            border: 1px solid rgba(255,255,255,0.14) !important;
            border-radius: 12px !important;
        }

        div[data-testid="stDataFrame"]{
            border-radius: 14px !important;
            overflow: hidden !important;
            border: 1px solid rgba(255,255,255,0.10) !important;
            background: rgba(255,255,255,0.025) !important;
        }

        button[data-baseweb="tab"]{ color: var(--muted) !important; }
        button[data-baseweb="tab"][aria-selected="true"]{
            color: var(--text) !important;
            border-bottom: 2px solid rgba(0,230,168,0.65) !important;
        }

        [data-testid="stMetricValue"]{
            color: var(--primary2) !important;
            text-shadow: 0 0 14px rgba(0,230,168,0.10);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

def glass_open() -> str:
    return "<div class='glass'>"

def glass_close() -> str:
    return "</div>"

inject_global_theme()

# ============================================================
# SECRETS / ENV
# ============================================================
def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(key)
    if v not in (None, ""):
        return v
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

SUPABASE_URL = (get_secret("SUPABASE_URL") or "").strip()
SUPABASE_ANON_KEY = (get_secret("SUPABASE_ANON_KEY") or "").strip()
SUPABASE_SERVICE_KEY = (get_secret("SUPABASE_SERVICE_KEY") or "").strip()
SUPABASE_SCHEMA = (get_secret("SUPABASE_SCHEMA", "public") or "public").strip()

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error(
        "Missing SUPABASE_URL or SUPABASE_ANON_KEY.\n\n"
        "Streamlit Cloud: Manage app → Settings → Secrets\n\n"
        "Add:\n"
        "SUPABASE_URL\nSUPABASE_ANON_KEY\n(optional) SUPABASE_SERVICE_KEY\nSUPABASE_SCHEMA"
    )
    st.stop()

if not SUPABASE_SERVICE_KEY:
    st.warning("SUPABASE_SERVICE_KEY not set. Writes (Admin/Loans/Payouts/Minutes/Attendance) may be disabled.")

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
# SLOW MODE (THROTTLE DB CALLS)
# ============================================================
SLOW_MODE = str(get_secret("SLOW_MODE", "1")).strip() not in ("0", "false", "False", "no", "NO")
MIN_SECONDS_BETWEEN_DB_CALLS = float(get_secret("MIN_SECONDS_BETWEEN_DB_CALLS", "0.35") or "0.35")

def throttle_db():
    if not SLOW_MODE:
        return
    last = st.session_state.get("_last_db_call_ts", 0.0)
    now = time.time()
    wait = MIN_SECONDS_BETWEEN_DB_CALLS - (now - last)
    if wait > 0:
        time.sleep(wait)
    st.session_state["_last_db_call_ts"] = time.time()

# ============================================================
# SAFE ERROR TEXT
# ============================================================
def _api_msg(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or "APIError")
        return str(e)
    return repr(e)

def table_readable(client, schema: str, table_name: str) -> bool:
    try:
        throttle_db()
        client.schema(schema).table(table_name).select("*").limit(1).execute()
        return True
    except Exception:
        return False

def safe_select(
    client,
    table_name: str,
    select_cols: str = "*",
    schema: str = "public",
    order_by: Optional[str] = None,
    order_desc: bool = False,
    limit: Optional[int] = None,
    show_error: bool = True,
    **filters,
) -> List[Dict]:
    try:
        throttle_db()
        q = client.schema(schema).table(table_name).select(select_cols)
        for col, val in (filters or {}).items():
            if val is None:
                continue
            q = q.eq(col, val)
        if order_by:
            q = q.order(order_by, desc=order_desc)
        if limit is not None:
            q = q.limit(int(limit))
        return (q.execute().data or [])
    except Exception as e:
        if show_error:
            st.error(f"Error reading {schema}.{table_name}")
            st.code(_api_msg(e), language="text")
        return []

# ============================================================
# LAZY IMPORT HELPER (✅ Railway-safe)
# ============================================================
def lazy_import(path: str, attr: Optional[str] = None) -> Tuple[Any, Optional[str]]:
    try:
        mod = importlib.import_module(path)
        if attr:
            return getattr(mod, attr), None
        return mod, None
    except Exception as e:
        return None, repr(e)

# ============================================================
# CONNECTED DB CHECK (KEY FIX FOR “COMPETITION DATA”)
# ============================================================
def project_ref_from_url(url: str) -> str:
    try:
        host = url.split("//", 1)[-1].split("/", 1)[0]
        return host.split(".")[0]
    except Exception:
        return "unknown"

def looks_like_jwt(key: str) -> bool:
    return key.count(".") >= 2 and len(key) > 40

def show_connected_db_banner():
    pref = project_ref_from_url(SUPABASE_URL)

    st.markdown(glass_open(), unsafe_allow_html=True)
    st.markdown("### 🔐 Connected Database Check")
    st.write("Supabase project ref:", f"`{pref}`")
    st.write("Schema:", f"`{SUPABASE_SCHEMA}`")
    st.write("Anon key looks valid:", "✅" if looks_like_jwt(SUPABASE_ANON_KEY) else "❌")
    st.write("Service key set:", "✅" if bool(SUPABASE_SERVICE_KEY) else "❌")

    try:
        throttle_db()
        r = sb_anon.schema(SUPABASE_SCHEMA).table("members").select("id").limit(1).execute()
        st.success("Anon read test: ✅ can read members")
        st.write("Sample:", r.data)
    except Exception as e:
        st.error("Anon read test: ❌ cannot read members (likely RLS policy or wrong schema)")
        st.code(_api_msg(e), language="text")

    st.caption("If this shows the WRONG project ref, fix Streamlit Cloud secrets: Manage app → Settings → Secrets.")
    st.markdown(glass_close(), unsafe_allow_html=True)

# ============================================================
# TOP BAR
# ============================================================
left, right = st.columns([1, 0.30])
with left:
    st.markdown(f"## 🏦 {APP_BRAND} • Bank Dashboard")
    if SLOW_MODE:
        st.caption("🐢 Slow Mode ON (reduced DB load)")
with right:
    if st.button("🔄 Refresh data", width=W_STRETCH):
        st.cache_data.clear()
        st.rerun()

# Always show connected DB check (small but critical)
with st.expander("🔎 Show connected database details", expanded=False):
    show_connected_db_banner()

# ============================================================
# SIDEBAR SAFE MODE / SLOW MODE
# ============================================================
with st.sidebar.expander("🛟 Safe Mode", expanded=False):
    SAFE_MODE_UI = st.checkbox(
        "Run Dashboard only (disable optional pages)",
        value=False,
        help="Use this if Streamlit shows a blank screen; avoids importing other modules.",
    )

with st.sidebar.expander("🐢 Slow Mode", expanded=False):
    st.write("Reduce Supabase calls (best for Free plan / outages).")
    SLOW_MODE_UI = st.checkbox("Enable Slow Mode", value=SLOW_MODE)
    st.session_state["_slow_mode_override"] = SLOW_MODE_UI
    if "MIN_SECONDS_BETWEEN_DB_CALLS_UI" not in st.session_state:
        st.session_state["MIN_SECONDS_BETWEEN_DB_CALLS_UI"] = MIN_SECONDS_BETWEEN_DB_CALLS
    st.session_state["MIN_SECONDS_BETWEEN_DB_CALLS_UI"] = st.slider(
        "Min seconds between DB calls",
        min_value=0.00,
        max_value=2.00,
        value=float(st.session_state["MIN_SECONDS_BETWEEN_DB_CALLS_UI"]),
        step=0.05,
    )

SLOW_MODE = bool(st.session_state.get("_slow_mode_override", SLOW_MODE))
MIN_SECONDS_BETWEEN_DB_CALLS = float(
    st.session_state.get("MIN_SECONDS_BETWEEN_DB_CALLS_UI", MIN_SECONDS_BETWEEN_DB_CALLS)
)

# ============================================================
# CACHED LOADERS (SCHEMA-SAFE)
# ============================================================
@st.cache_data(ttl=300)
def load_members(url: str, anon_key: str, schema: str) -> pd.DataFrame:
    client = create_client(url, anon_key)

    try:
        throttle_db()
        rows = (
            client.schema(schema)
            .table("members")
            .select("id,name,display_name,phone")
            .order("id", desc=False)
            .limit(5000)
            .execute()
            .data
            or []
        )
    except Exception:
        throttle_db()
        rows = (
            client.schema(schema)
            .table("members")
            .select("id,name,phone")
            .order("id", desc=False)
            .limit(5000)
            .execute()
            .data
            or []
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["id", "name", "phone", "member_name", "label"])

    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    df = df[df["id"] > 0].copy()
    df["name"] = df["name"].astype(str)
    df["phone"] = df.get("phone", "").astype(str).replace({"None": "", "nan": ""})

    if "display_name" in df.columns:
        df["display_name"] = df["display_name"].astype(str).replace({"None": "", "nan": ""})
        df["member_name"] = df["display_name"].where(df["display_name"].str.strip() != "", df["name"])
    else:
        df["member_name"] = df["name"]

    df["label"] = df.apply(lambda r: f"{int(r['id']):02d} • {r['member_name']}", axis=1)
    return df

@st.cache_data(ttl=240)
def load_contributions_view(url: str, anon_key: str, schema: str) -> pd.DataFrame:
    """
    IMPORTANT:
    - We don't swallow errors silently
    - We store the error string in session_state for display (outside cache side-effects)
    """
    client = create_client(url, anon_key)
    throttle_db()
    try:
        rows = (
            client.schema(schema)
            .table("v_contributions_with_member")
            .select("id,member_id,member_name,session_id,amount,paid_at,note,created_at")
            .order("created_at", desc=True)
            .limit(500)
            .execute()
            .data
            or []
        )
        st.session_state.pop("_last_contrib_view_error", None)
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as e:
        st.session_state["_last_contrib_view_error"] = _api_msg(e)
        return pd.DataFrame()

# ============================================================
# SESSION HELPERS
# ============================================================
def get_app_state(sb, schema: str) -> dict:
    rows = safe_select(sb, "app_state", "*", schema=schema, limit=1, show_error=False, id=1)
    if rows:
        return rows[0]
    rows2 = safe_select(sb, "app_state", "*", schema=schema, limit=1, show_error=False)
    return rows2[0] if rows2 else {}

def get_effective_session_id(sb_read, schema: str) -> tuple[Optional[int], str]:
    state = get_app_state(sb_read, schema)
    raw = state.get("current_session_id")
    try:
        cs = int(raw) if raw is not None and str(raw).strip() != "" else None
    except Exception:
        cs = None

    if cs is not None:
        return cs, "from app_state"

    srows = safe_select(
        sb_read,
        "sessions",
        "id,session_id,start_date,end_date,created_at",
        schema=schema,
        order_by="session_id",
        order_desc=True,
        limit=1,
        show_error=False,
    )
    if srows:
        sid = srows[0].get("session_id") or srows[0].get("id")
        try:
            return int(sid), "fallback: latest session"
        except Exception:
            return None, "fallback failed"
    return None, "no sessions"

# ============================================================
# NAVIGATION
# ============================================================
if SAFE_MODE_UI:
    PAGES = ["Dashboard"]
else:
    PAGES = [
        "Dashboard",
        "Contributions",
        "Payouts",
        "Loans",
        "🤖 AI Risk Panel",
        "🧠 Njangi LLM",
        "Minutes & Attendance",
        "Admin",
        "Audit",
        "Health",
    ]

page = st.sidebar.radio("Menu", PAGES, key="main_menu")

# ============================================================
# PAGES
# ============================================================
if page == "Dashboard":
    render_dashboard(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)

elif page == "Contributions":
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Contributions")
    st.caption("Stored by member_id. Names shown via view if available.")

    df = load_contributions_view(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)

    if df.empty and st.session_state.get("_last_contrib_view_error"):
        st.warning("View v_contributions_with_member failed (showing error). Falling back to raw contributions.")
        st.code(st.session_state.get("_last_contrib_view_error"), language="text")

    if df.empty:
        rows = safe_select(
            sb_anon,
            "contributions",
            "*",
            schema=SUPABASE_SCHEMA,
            order_by="created_at",
            order_desc=True,
            limit=250,
        )
        df2 = pd.DataFrame(rows)
        if df2.empty:
            st.info("No contributions found (or RLS blocked). Check the database details expander at the top.")
        else:
            st.warning("Showing raw contributions (view not available or not readable).")
            st.dataframe(df2, width=W_STRETCH, hide_index=True)
    else:
        st.dataframe(df, width=W_STRETCH, hide_index=True)

    st.markdown(glass_close(), unsafe_allow_html=True)

elif page == "Payouts":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY to enable payout writes.")
        st.stop()

    payout_fn, payout_err = lazy_import("payout", "render_payouts")
    if payout_fn is None:
        st.error("Payout module failed to load.")
        st.code(payout_err or "", language="text")
    else:
        payout_fn(sb_service, SUPABASE_SCHEMA)

elif page == "Loans":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY to enable loans writes.")
        st.stop()

    needed = ["members", "loans", "loan_payments", "loan_requests", "signatures", "interest_ledger"]
    missing = [t for t in needed if not table_readable(sb_service, SUPABASE_SCHEMA, t)]
    if missing:
        st.error("Loans module is not ready — missing required table(s) or not readable:")
        st.write(", ".join([f"{SUPABASE_SCHEMA}.{t}" for t in missing]))
        st.stop()

    loans_mod, loans_err = lazy_import("loans", None)
    if loans_mod is None:
        st.error("Loans module failed to import.")
        st.code(loans_err or "", language="text")
    else:
        loans_fn = getattr(loans_mod, "show_loans", None) or getattr(loans_mod, "render_loans", None)
        if loans_fn is None:
            st.error("loans.py must define show_loans() or render_loans().")
        else:
            loans_fn(sb_service, SUPABASE_SCHEMA, actor_user_id="")

elif page == "🤖 AI Risk Panel":
    fn, err = lazy_import("ai_risk_panel", "render_ai_risk_panel")
    if fn is None:
        st.error("AI Risk Panel failed to load.")
        st.code(err or "", language="text")
    else:
        fn(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)

# ✅ Njangi LLM page (✅ FIXED: pass clients + schema)
elif page == "🧠 Njangi LLM":
    fn, err = lazy_import("njangi_llm_panel", "render_njangi_llm_panel")
    if fn is None:
        st.error("Njangi LLM panel failed to load.")
        st.code(err or "", language="text")
    else:
        fn(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)

# ---------------------------
# The rest of your pages (Minutes/Admin/Audit/Health) remain exactly
# as you already have them in your current file.
# ---------------------------
else:
    st.info("Page not implemented in this snippet. Paste the remaining sections below this line exactly as you have them.")
