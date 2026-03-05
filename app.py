# app.py ✅ COMPLETE SINGLE FILE — NJANGI STANDARD (NO legacy)
# FAST VERSION + SLOW/GENTLE MODE + ✅ younchat + SAFE NAV FIX + ✅ DASHBOARD-AI NAV BRIDGE
# ✅ NEW: Smooth floating rotating manifold background (CSS-only, Streamlit-safe)
# ✅ NEW: Minutes PDF + Attendance PDF download buttons (via pdfs.py)
# ✅ FIX: AI Suite signature-adapter (prevents TypeError unexpected keyword args)
# ------------------------------------------------------------------------------
# ✅ Keeps:
#   - Safe navigation + nav bridge (dashboard → other pages)
#   - Fast/Slow mode throttling
#   - Cache-safe loaders (never cache supabase clients)
#   - Admin import fix (admin_panels.py)
#   - Streamlit params use_container_width=True
# ------------------------------------------------------------------------------

from __future__ import annotations

import os
import sys
import time
import importlib
import inspect
from datetime import datetime, timezone
from typing import Any, Optional, Tuple, List, Dict

import pandas as pd
import streamlit as st
from postgrest.exceptions import APIError
from supabase import create_client


# =========================
# PATH FIX (CRITICAL)
# Put APP_DIR on sys.path BEFORE importing local modules.
# =========================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Required local module (after sys.path fix)
from dashboard_panel import render_dashboard  # noqa: E402


APP_BRAND = "theyoungshallgrow"

st.set_page_config(
    page_title=f"{APP_BRAND} • Bank Dashboard",
    layout="wide",
    page_icon="🏦",
)

# =========================
# TIME
# =========================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# =========================
# THEME + MANIFOLD BACKGROUND
# =========================
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

            /* Manifold colors */
            --manifoldA: rgba(0, 230, 168, 0.22);
            --manifoldB: rgba(96, 165, 250, 0.18);
            --manifoldC: rgba(255, 255, 255, 0.06);
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

        /* ================================
           MANIFOLD SHAPE (floating blobs)
           ================================ */
        .stApp::before,
        .stApp::after{
            content: "";
            position: fixed;
            z-index: 0;
            pointer-events: none;
            width: 620px;
            height: 620px;
            border-radius: 42% 58% 60% 40% / 45% 44% 56% 55%;
            filter: blur(28px);
            opacity: 0.95;
            transform: translate3d(0,0,0);
            will-change: transform, border-radius;
        }

        /* Blob 1: emerald → blue */
        .stApp::before{
            left: -180px;
            top: 90px;
            background:
                radial-gradient(circle at 28% 30%, var(--manifoldA), transparent 58%),
                radial-gradient(circle at 70% 65%, var(--manifoldB), transparent 62%),
                radial-gradient(circle at 52% 48%, var(--manifoldC), transparent 65%);
            animation:
                manifold-float-1 16s ease-in-out infinite,
                manifold-morph 10s ease-in-out infinite,
                manifold-rotate 36s linear infinite;
        }

        /* Blob 2: blue → emerald */
        .stApp::after{
            right: -210px;
            bottom: -160px;
            width: 720px;
            height: 720px;
            background:
                radial-gradient(circle at 35% 35%, var(--manifoldB), transparent 58%),
                radial-gradient(circle at 72% 62%, var(--manifoldA), transparent 60%),
                radial-gradient(circle at 48% 52%, var(--manifoldC), transparent 66%);
            animation:
                manifold-float-2 18s ease-in-out infinite,
                manifold-morph 12s ease-in-out infinite reverse,
                manifold-rotate 44s linear infinite reverse;
        }

        /* Ensure content sits above blobs */
        .stApp > div,
        header, footer,
        section[data-testid="stSidebar"]{
            position: relative;
            z-index: 1;
        }

        @keyframes manifold-float-1{
            0%   { transform: translate3d(0px, 0px, 0) scale(1.00); }
            50%  { transform: translate3d(60px, 18px, 0) scale(1.04); }
            100% { transform: translate3d(0px, 0px, 0) scale(1.00); }
        }
        @keyframes manifold-float-2{
            0%   { transform: translate3d(0px, 0px, 0) scale(1.00); }
            50%  { transform: translate3d(-56px, -26px, 0) scale(1.05); }
            100% { transform: translate3d(0px, 0px, 0) scale(1.00); }
        }
        @keyframes manifold-morph{
            0%   { border-radius: 42% 58% 60% 40% / 45% 44% 56% 55%; }
            25%  { border-radius: 50% 50% 45% 55% / 55% 40% 60% 45%; }
            50%  { border-radius: 58% 42% 55% 45% / 45% 60% 40% 55%; }
            75%  { border-radius: 46% 54% 62% 38% / 42% 50% 50% 58%; }
            100% { border-radius: 42% 58% 60% 40% / 45% 44% 56% 55%; }
        }
        @keyframes manifold-rotate{
            0%   { rotate: 0deg; }
            100% { rotate: 360deg; }
        }

        header, footer { background: transparent !important; }

        section[data-testid="stSidebar"]{
            background: linear-gradient(180deg, rgba(15,28,53,0.96), rgba(11,20,38,0.96)) !important;
            border-right: 1px solid rgba(255,255,255,0.08) !important;
        }

        html, body, p, div, span, label, small,
        h1, h2, h3, h4, h5, h6 { color: var(--text) !important; }
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


# =========================
# SAFE CALL ADAPTER (signature-aware) ✅ FIXED (NO RE-CRASH)
# =========================
def _call_with_supported_kwargs(fn, **kwargs):
    """
    Calls fn with only kwargs it supports.
    - If fn has **kwargs, passes everything.
    - Else filters to declared parameters only.

    CRITICAL FIX:
    - We only catch signature-inspection errors (inspect.signature).
    - We do NOT catch exceptions raised by fn() itself,
      otherwise we'd retry with unfiltered kwargs and crash again.
    """
    # Unwrap decorators if any
    try:
        target = inspect.unwrap(fn)
    except Exception:
        target = fn

    # Only protect signature inspection
    try:
        sig = inspect.signature(target)
    except Exception:
        # Can't inspect -> call directly (best effort)
        return fn(**kwargs)

    params = sig.parameters

    # If function accepts **kwargs, pass everything
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(**kwargs)

    supported = {k: v for k, v in kwargs.items() if k in params}
    return fn(**supported)


def _alias_kwargs(kwargs: dict) -> dict:
    """
    Adds common alias names so AI suite panel can accept different parameter names.
    """
    out = dict(kwargs)

    if "members" in out:
        out.setdefault("members_df", out["members"])
        out.setdefault("df_members", out["members"])
        out.setdefault("members_data", out["members"])

    if "contributions" in out:
        out.setdefault("contributions_df", out["contributions"])
        out.setdefault("df_contributions", out["contributions"])

    if "foundation_contributions" in out:
        out.setdefault("foundation_df", out["foundation_contributions"])
        out.setdefault("df_foundation", out["foundation_contributions"])

    if "loans" in out:
        out.setdefault("loans_df", out["loans"])
        out.setdefault("df_loans", out["loans"])

    if "loan_payments" in out:
        out.setdefault("loan_payments_df", out["loan_payments"])
        out.setdefault("df_loan_payments", out["loan_payments"])

    if "payouts" in out:
        out.setdefault("payouts_df", out["payouts"])
        out.setdefault("df_payouts", out["payouts"])

    if "fines" in out:
        out.setdefault("fines_df", out["fines"])
        out.setdefault("df_fines", out["fines"])

    if "sessions" in out:
        out.setdefault("sessions_df", out["sessions"])
        out.setdefault("df_sessions", out["sessions"])

    return out


# =========================
# SECRETS / ENV
# =========================
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

FAST_MODE_DEFAULT = str(get_secret("FAST_MODE", "1")).strip() not in ("0", "false", "False", "no", "NO")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error(
        "Missing SUPABASE_URL or SUPABASE_ANON_KEY.\n\n"
        "Railway: Variables → set SUPABASE_URL and SUPABASE_ANON_KEY\n"
        "(optional) SUPABASE_SERVICE_KEY, SUPABASE_SCHEMA, FAST_MODE, SLOW_MODE"
    )
    st.stop()

if not SUPABASE_SERVICE_KEY:
    st.warning("SUPABASE_SERVICE_KEY not set. Writes (Admin/Loans/Payouts/Minutes/Attendance) may be disabled.")


# =========================
# CLIENTS
# =========================
@st.cache_resource
def get_anon_client(url: str, anon_key: str):
    return create_client(url.strip(), anon_key.strip())


@st.cache_resource
def get_service_client(url: str, service_key: str):
    return create_client(url.strip(), service_key.strip())


sb_anon = get_anon_client(SUPABASE_URL, SUPABASE_ANON_KEY)
sb_service = get_service_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else None


# =========================
# SLOW MODE / THROTTLE
# =========================
SLOW_MODE_DEFAULT = str(get_secret("SLOW_MODE", "1")).strip() not in ("0", "false", "False", "no", "NO")
MIN_SECONDS_BETWEEN_DB_CALLS_DEFAULT = float(get_secret("MIN_SECONDS_BETWEEN_DB_CALLS", "0.35") or "0.35")


def throttle_db():
    if not st.session_state.get("_slow_mode_override", SLOW_MODE_DEFAULT):
        return
    last = st.session_state.get("_last_db_call_ts", 0.0)
    now = time.time()
    wait = float(st.session_state.get("MIN_SECONDS_BETWEEN_DB_CALLS_UI", MIN_SECONDS_BETWEEN_DB_CALLS_DEFAULT)) - (now - last)
    if wait > 0:
        time.sleep(wait)
    st.session_state["_last_db_call_ts"] = time.time()


# =========================
# SAFE ERROR TEXT
# =========================
def _api_msg(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or "APIError")
        return str(e)
    return repr(e)


def table_readable(client, schema: str, table_name: str) -> bool:
    if client is None:
        return False
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
    if client is None:
        return []
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


# =========================
# LAZY IMPORT (Railway-safe)
# =========================
def lazy_import(path: str, attr: Optional[str] = None) -> Tuple[Any, Optional[str]]:
    try:
        mod = importlib.import_module(path)
        if attr:
            return getattr(mod, attr), None
        return mod, None
    except Exception as e:
        return None, repr(e)


# =========================
# CONNECTED DB CHECK
# =========================
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
        st.error("Anon read test: ❌ cannot read members (RLS policy or wrong schema)")
        st.code(_api_msg(e), language="text")

    st.caption("If this shows the WRONG project ref, fix Railway Variables / Streamlit secrets.")
    st.markdown(glass_close(), unsafe_allow_html=True)


# =========================
# SAFE NAVIGATION + Dashboard-AI bridge
# =========================
def request_nav(target: str):
    st.session_state["nav_request"] = target
    st.rerun()


def apply_nav_before_widget(default_page: str, allowed_pages: List[str]):
    if "main_menu" not in st.session_state:
        st.session_state["main_menu"] = default_page
    if "nav_request" not in st.session_state:
        st.session_state["nav_request"] = None

    # Bridge: dashboard_panel.py may set st.session_state["page"] = "<Menu Name>"
    dash_req = st.session_state.get("page")
    if isinstance(dash_req, str) and dash_req.strip():
        st.session_state["nav_request"] = dash_req.strip()
        st.session_state["page"] = None

    req = st.session_state.get("nav_request")
    if req:
        if req in allowed_pages:
            st.session_state["main_menu"] = req
        st.session_state["nav_request"] = None


# =========================
# TOP BAR
# =========================
left, right = st.columns([1, 0.30])
with left:
    st.markdown(f"## 🏦 {APP_BRAND} • Bank Dashboard")
    if st.session_state.get("_slow_mode_override", SLOW_MODE_DEFAULT):
        st.caption("🐢 Slow Mode ON (reduced DB load)")
    else:
        st.caption("⚡ Fast Mode ON (minimal throttling)")
with right:
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

with st.expander("🔎 Show connected database details", expanded=False):
    show_connected_db_banner()


# =========================
# SIDEBAR: SAFE MODE + FAST/SLOW MODE
# =========================
with st.sidebar.expander("🛟 Safe Mode", expanded=False):
    SAFE_MODE_UI = st.checkbox(
        "Run Dashboard only (disable optional pages)",
        value=False,
        help="Use this if Streamlit shows a blank screen; avoids importing other modules.",
    )

with st.sidebar.expander("⚡ Fast / 🐢 Slow Mode", expanded=False):
    st.write("Fast mode reduces throttling. Slow mode protects Supabase limits.")
    fast_on = st.checkbox("Enable Fast Mode", value=FAST_MODE_DEFAULT)
    slow_on = st.checkbox("Enable Slow Mode", value=(not fast_on) and SLOW_MODE_DEFAULT)

    if fast_on:
        st.session_state["_slow_mode_override"] = False
        st.session_state["MIN_SECONDS_BETWEEN_DB_CALLS_UI"] = 0.05
    else:
        st.session_state["_slow_mode_override"] = bool(slow_on)
        st.session_state["MIN_SECONDS_BETWEEN_DB_CALLS_UI"] = st.slider(
            "Min seconds between DB calls",
            min_value=0.00,
            max_value=2.00,
            value=float(get_secret("MIN_SECONDS_BETWEEN_DB_CALLS", "0.35") or "0.35"),
            step=0.05,
        )

SLOW_MODE = bool(st.session_state.get("_slow_mode_override", SLOW_MODE_DEFAULT))


# =========================
# CACHED LOADERS (NO supabase client in args)
# =========================
MEMBERS_TTL = 120 if not SLOW_MODE else 300
VIEW_TTL = 90 if not SLOW_MODE else 240
AI_TTL = 90 if not SLOW_MODE else 240


@st.cache_data(ttl=MEMBERS_TTL, show_spinner=False)
def load_members(url: str, anon_key: str, schema: str) -> pd.DataFrame:
    client = create_client(url, anon_key)

    cols_try = [
        "id,name,display_name,full_name,phone",
        "id,name,display_name,phone",
        "id,name,phone",
        "id,full_name,phone",
        "id,display_name,phone",
        "id,name",
        "id,display_name",
        "id,full_name",
    ]
    rows = []
    last_err = None
    for cols in cols_try:
        try:
            throttle_db()
            rows = (
                client.schema(schema)
                .table("members")
                .select(cols)
                .order("id", desc=False)
                .limit(5000)
                .execute()
                .data
                or []
            )
            last_err = None
            break
        except Exception as e:
            last_err = _api_msg(e)
            rows = []

    df = pd.DataFrame(rows)
    if df.empty:
        if last_err:
            st.session_state["_last_members_error"] = last_err
        return pd.DataFrame(columns=["id", "member_name", "phone", "label"])

    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    df = df[df["id"] > 0].copy()

    for c in ["name", "display_name", "full_name", "phone"]:
        if c in df.columns:
            df[c] = df[c].astype(str).replace({"None": "", "nan": ""})

    def _best_name(r):
        for k in ["display_name", "full_name", "name"]:
            if k in r and str(r.get(k, "")).strip():
                return str(r.get(k)).strip()
        return ""

    df["member_name"] = df.apply(_best_name, axis=1)
    if "phone" not in df.columns:
        df["phone"] = ""
    df["phone"] = df["phone"].astype(str).replace({"None": "", "nan": ""})
    df["label"] = df.apply(lambda r: f"{int(r['id']):02d} • {r['member_name']}", axis=1)
    return df[["id", "member_name", "phone", "label"]].copy()


@st.cache_data(ttl=VIEW_TTL, show_spinner=False)
def load_contributions_view(url: str, anon_key: str, schema: str, slow_mode: bool) -> pd.DataFrame:
    client = create_client(url, anon_key)
    throttle_db()
    try:
        rows = (
            client.schema(schema)
            .table("v_contributions_with_member")
            .select("id,member_id,member_name,session_id,amount,paid_at,note,created_at")
            .order("created_at", desc=True)
            .limit(500 if not slow_mode else 350)
            .execute()
            .data
            or []
        )
        st.session_state.pop("_last_contrib_view_error", None)
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as e:
        st.session_state["_last_contrib_view_error"] = _api_msg(e)
        return pd.DataFrame()


@st.cache_data(ttl=VIEW_TTL, show_spinner=False)
def load_attendance_view(url: str, anon_key: str, schema: str, session_id: int) -> pd.DataFrame:
    client = create_client(url, anon_key)
    throttle_db()
    try:
        rows = (
            client.schema(schema)
            .table("v_attendance_with_member")
            .select("*")
            .eq("session_id", int(session_id))
            .order("member_id", desc=False)
            .limit(5000)
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=AI_TTL, show_spinner=False)
def load_ai_table(
    url: str,
    anon_key: str,
    schema: str,
    table: str,
    select_cols: str,
    order_by: Optional[str],
    order_desc: bool,
    limit: int,
) -> pd.DataFrame:
    client = create_client(url, anon_key)
    try:
        throttle_db()
        q = client.schema(schema).table(table).select(select_cols)
        if order_by:
            q = q.order(order_by, desc=order_desc)
        q = q.limit(int(limit))
        rows = (q.execute().data or [])
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as e:
        st.session_state[f"_last_ai_load_err_{table}"] = _api_msg(e)
        return pd.DataFrame()


def _ai_limit(slow_mode: bool, fast_limit: int, slow_limit: int) -> int:
    return int(slow_limit if slow_mode else fast_limit)


def load_ai_bundle() -> dict:
    lim_small = _ai_limit(SLOW_MODE, 2500, 1200)
    lim_med = _ai_limit(SLOW_MODE, 4000, 2000)
    lim_big = _ai_limit(SLOW_MODE, 6000, 3000)

    members = load_members(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)

    contributions = load_ai_table(
        SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA,
        "contributions",
        "id,member_id,session_id,amount,paid_at,note,created_at",
        "created_at", True, lim_big
    )
    foundation = load_ai_table(
        SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA,
        "foundation_contributions",
        "id,member_id,amount,created_at,note,session_id",
        "created_at", True, lim_med
    )
    loans = load_ai_table(
        SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA,
        "loans",
        "id,member_id,status,principal,principal_current,total_due,unpaid_interest,interest_rate_monthly,due_cycle_days,borrow_date,last_paid_at,created_at",
        "created_at", True, lim_small
    )
    loan_payments = load_ai_table(
        SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA,
        "loan_payments",
        "id,member_id,loan_id,amount,paid_at,created_at,note",
        "created_at", True, lim_med
    )
    payouts = load_ai_table(
        SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA,
        "payouts",
        "id,member_id,session_id,payout_amount,amount,payout_date,created_at,note",
        "created_at", True, lim_small
    )
    fines = load_ai_table(
        SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA,
        "fines",
        "id,member_id,amount,created_at,note,session_id",
        "created_at", True, lim_small
    )
    sessions = load_ai_table(
        SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA,
        "sessions",
        "id,session_id,session_date,start_date,end_date,created_at",
        "id", True, 500
    )

    return {
        "members": members,
        "contributions": contributions,
        "foundation_contributions": foundation,
        "loans": loans,
        "loan_payments": loan_payments,
        "payouts": payouts,
        "fines": fines,
        "sessions": sessions,
    }


# =========================
# SESSION HELPERS
# =========================
def get_app_state(sb, schema: str) -> dict:
    rows = safe_select(sb, "app_state", "id,current_session_id,updated_at,created_at", schema=schema, limit=1, show_error=False)
    return rows[0] if rows else {}


def get_effective_session_id(sb_read, schema: str) -> Tuple[Optional[int], str]:
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
        order_by="id",
        order_desc=True,
        limit=1,
        show_error=False,
    )
    if srows:
        sid = srows[0].get("id") or srows[0].get("session_id")
        try:
            return int(sid), "fallback: latest sessions.id"
        except Exception:
            pass

    srows2 = safe_select(
        sb_read,
        "sessions",
        "id,session_id,start_date,end_date,created_at",
        schema=schema,
        order_by="session_id",
        order_desc=True,
        limit=1,
        show_error=False,
    )
    if srows2:
        sid = srows2[0].get("session_id") or srows2[0].get("id")
        try:
            return int(sid), "fallback: latest sessions.session_id"
        except Exception:
            pass

    return None, "no sessions"


# =========================
# NAVIGATION (SAFE)
# =========================
if SAFE_MODE_UI:
    PAGES = ["Dashboard"]
else:
    PAGES = [
        "Dashboard",
        "💬 younchat",
        "🧠 AI Suite",
        "Contributions",
        "Payouts",
        "Loans",
        "🤖 AI Risk Panel",
        "🧠 Njangi LLM (younchat)",
        "Minutes & Attendance",
        "Admin",
        "Audit",
        "Health",
    ]

apply_nav_before_widget(default_page="Dashboard", allowed_pages=PAGES)
page = st.sidebar.radio("Menu", PAGES, key="main_menu")


# =========================
# OPTIONAL: PDF FUNCTIONS (lazy)
# =========================
def get_pdf_tools():
    """
    Lazy-load pdf makers from pdfs.py.
    Returns (make_minutes_pdf, make_attendance_pdf, err)
    """
    mod, err = lazy_import("pdfs", None)
    if mod is None:
        return None, None, err or "pdfs.py not found"
    mm = getattr(mod, "make_minutes_pdf", None)
    ma = getattr(mod, "make_attendance_pdf", None)
    if not mm or not ma:
        return None, None, "pdfs.py missing make_minutes_pdf or make_attendance_pdf"
    return mm, ma, None


# =========================
# PAGES
# =========================
if page == "Dashboard":
    render_dashboard(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)

elif page == "💬 younchat":
    fn, err = lazy_import("njangi_llm_panel", "render_njangi_llm_panel")
    if fn is None:
        st.error("younchat failed to load.")
        st.code(err or "", language="text")
    else:
        fn(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)

elif page == "🧠 AI Suite":
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("🧠 NJANGI AI Suite — Advanced+ (No API Key)")
    st.caption("Risk • Reliability • Dropout • Fraud • Liquidity • Decisions • Alerts • Trends • Segments • Stress Test • Minutes Generator")

    fn, err = lazy_import("ai_suite_panel", "render_full_ai_suite_panel")
    if fn is None:
        st.error("AI Suite failed to load (ai_suite_panel.py).")
        st.code(err or "", language="text")
        st.markdown(glass_close(), unsafe_allow_html=True)
        st.stop()

    bundle = load_ai_bundle()
    if bundle["members"].empty:
        if st.session_state.get("_last_members_error"):
            st.error("Members not readable. Error:")
            st.code(st.session_state.get("_last_members_error"), language="text")
        else:
            st.error("No members found (or RLS blocked). Check DB details at top.")
        st.markdown(glass_close(), unsafe_allow_html=True)
        st.stop()

    payload = dict(
        members=bundle["members"].rename(columns={"member_name": "display_name"}).assign(
            name=bundle["members"]["member_name"]
        ),
        contributions=bundle["contributions"],
        loans=bundle["loans"],
        loan_payments=bundle["loan_payments"],
        payouts=bundle["payouts"],
        fines=bundle["fines"],
        foundation_contributions=bundle["foundation_contributions"],
        sessions=bundle["sessions"],
        schema=SUPABASE_SCHEMA,
        sb_anon=sb_anon,
        sb_service=sb_service,
        min_loans_for_ml=20,
        slow_mode=bool(SLOW_MODE),
    )

    payload = _alias_kwargs(payload)
    _call_with_supported_kwargs(fn, **payload)

    with st.expander("🔧 AI Suite load diagnostics", expanded=False):
        st.caption(f"AI Suite signature: {str(inspect.signature(fn))}")
        for t in ["contributions", "foundation_contributions", "loans", "loan_payments", "payouts", "fines", "sessions"]:
            errk = f"_last_ai_load_err_{t}"
            if st.session_state.get(errk):
                st.warning(f"{t} load error:")
                st.code(st.session_state.get(errk), language="text")
        st.write("Tip: If tables are empty due to RLS, allow anon reads (SELECT) or switch loaders to service key.")

    st.markdown(glass_close(), unsafe_allow_html=True)

elif page == "Contributions":
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Contributions")
    st.caption("Stored by member_id. Names shown via view if available.")

    df = load_contributions_view(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA, slow_mode=SLOW_MODE)

    if df.empty and st.session_state.get("_last_contrib_view_error"):
        st.warning("View v_contributions_with_member failed (error below). Falling back to raw contributions.")
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
            st.info("No contributions found (or RLS blocked). Check DB details at top.")
        else:
            st.warning("Showing raw contributions (view not available or not readable).")
            st.dataframe(df2, use_container_width=True, hide_index=True)
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

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

    needed = ["members", "loans", "loan_payments", "signatures", "interest_ledger"]
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

elif page == "🧠 Njangi LLM (younchat)":
    fn, err = lazy_import("njangi_llm_panel", "render_njangi_llm_panel")
    if fn is None:
        st.error("Njangi LLM panel (younchat) failed to load.")
        st.code(err or "", language="text")
    else:
        fn(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)

elif page == "Minutes & Attendance":
    st.subheader("📝 Minutes & ✅ Attendance")

    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY to enable writing.")
        st.stop()

    read_for_session = sb_service if sb_service is not None else sb_anon
    current_session_id, session_note = get_effective_session_id(read_for_session, SUPABASE_SCHEMA)

    if current_session_id is None:
        st.error("No sessions found. Create a session first in Admin → Sessions.")
        st.stop()

    if session_note != "from app_state":
        st.warning("app_state.current_session_id is not set. Using latest session as fallback.")

    with st.sidebar.expander("🔐 Role (Minutes/Attendance)", expanded=False):
        role = st.selectbox("Role", ["admin", "treasury", "member"], index=0, key="ma_role")
    can_write = role in ("admin", "treasury")

    df_members = load_members(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)
    if df_members.empty:
        if st.session_state.get("_last_members_error"):
            st.error("Members not readable. Error:")
            st.code(st.session_state.get("_last_members_error"), language="text")
        else:
            st.error("No members found (or RLS blocked). Check DB details at top.")
        st.stop()

    tab1, tab2, tab3 = st.tabs(["Minutes / Documentation", "Attendance", "Summaries"])

    # ---------- Minutes ----------
    with tab1:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Meeting Minutes / Documentation")
        st.caption(f"Linked session_id: {current_session_id}  •  {session_note}")

        if can_write:
            with st.form("minutes_form", clear_on_submit=False):
                title = st.text_input("Title", key="minutes_title")
                body = st.text_area("Minutes / Documentation", height=260, key="minutes_body")
                ok = st.form_submit_button("💾 Save minutes", use_container_width=True)

            if ok:
                if not title.strip() or not body.strip():
                    st.error("Title and body are required.")
                else:
                    try:
                        throttle_db()
                        existing = (
                            sb_service.schema(SUPABASE_SCHEMA)
                            .table("minutes")
                            .select("id,session_id")
                            .eq("session_id", int(current_session_id))
                            .limit(1)
                            .execute()
                            .data
                            or []
                        )
                        throttle_db()
                        if existing:
                            mid = int(existing[0]["id"])
                            sb_service.schema(SUPABASE_SCHEMA).table("minutes").update(
                                {"title": title.strip(), "body": body.strip(), "updated_at": now_iso(), "created_by": role}
                            ).eq("id", mid).execute()
                            st.success("Minutes updated.")
                        else:
                            sb_service.schema(SUPABASE_SCHEMA).table("minutes").insert(
                                {
                                    "session_id": int(current_session_id),
                                    "title": title.strip(),
                                    "body": body.strip(),
                                    "created_by": role,
                                    "created_at": now_iso(),
                                    "updated_at": now_iso(),
                                }
                            ).execute()
                            st.success("Minutes saved.")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error("Failed to save minutes.")
                        st.code(_api_msg(e), language="text")

        st.divider()
        st.markdown("### Current session minutes")

        rows = safe_select(
            sb_service,
            "minutes",
            "*",
            schema=SUPABASE_SCHEMA,
            order_by="updated_at",
            order_desc=True,
            limit=10,
            show_error=False,
            session_id=int(current_session_id),
        )
        dfm = pd.DataFrame(rows)
        if dfm.empty:
            st.info("No minutes recorded yet.")
        else:
            st.dataframe(dfm, use_container_width=True, hide_index=True)

            # ✅ PDF Download (Minutes)
            make_minutes_pdf, _, pdf_err = get_pdf_tools()
            if make_minutes_pdf is None:
                st.caption("PDF download not available.")
                st.code(pdf_err or "", language="text")
            else:
                row0 = dfm.iloc[0].to_dict()
                try:
                    pdf_bytes = make_minutes_pdf(APP_BRAND, row0)
                    fname = f"minutes_session_{int(current_session_id)}.pdf"
                    st.download_button(
                        "⬇️ Download Minutes PDF (current session)",
                        data=pdf_bytes,
                        file_name=fname,
                        mime="application/pdf",
                        use_container_width=True,
                    )
                except Exception as e:
                    st.error("Failed to generate Minutes PDF.")
                    st.code(_api_msg(e), language="text")

        st.markdown(glass_close(), unsafe_allow_html=True)

    # ---------- Attendance ----------
    with tab2:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Attendance")
        st.caption(f"Linked session_id: {current_session_id}  •  {session_note}")
        st.caption("Mark each member Present/Absent. Submit once (fast + safe).")

        arows_existing = safe_select(
            sb_anon,
            "attendance",
            "member_id,present,note,created_at",
            schema=SUPABASE_SCHEMA,
            order_by="member_id",
            order_desc=False,
            limit=2000,
            show_error=False,
            session_id=int(current_session_id),
        )
        existing_map = {int(r["member_id"]): r for r in arows_existing if r.get("member_id") is not None}

        with st.form("attendance_form"):
            attendance_rows: List[Dict] = []
            for _, r in df_members.sort_values("id").iterrows():
                mid = int(r["id"])
                name = str(r.get("member_name") or "")
                label = f"{mid:02d} • {name}"

                ex = existing_map.get(mid, {})
                ex_present = bool(ex.get("present")) if ex else True
                ex_note = str(ex.get("note") or "") if ex else ""

                c_status, c_note = st.columns([0.42, 0.58])
                with c_status:
                    status = st.radio(
                        label,
                        options=["present", "absent"],
                        index=0 if ex_present else 1,
                        horizontal=True,
                        key=f"att_status_{mid}_{current_session_id}",
                    )
                with c_note:
                    note = st.text_input(
                        "Reason / Note",
                        value=ex_note,
                        placeholder="e.g., Sick, Travel, Excused…",
                        key=f"att_note_{mid}_{current_session_id}",
                        label_visibility="collapsed",
                    )

                attendance_rows.append({"member_id": mid, "present": (status == "present"), "note": note.strip() or None})

            save = st.form_submit_button("💾 Save attendance (ALL members)", use_container_width=True)

        if save:
            if not can_write:
                st.warning("Only admin/treasury can save attendance.")
            else:
                payload_rows = [
                    {
                        "session_id": int(current_session_id),
                        "member_id": int(row["member_id"]),
                        "present": bool(row["present"]),
                        "note": row["note"],
                        "created_at": now_iso(),
                    }
                    for row in attendance_rows
                ]

                try:
                    throttle_db()
                    sb_service.schema(SUPABASE_SCHEMA).table("attendance").delete().eq("session_id", int(current_session_id)).execute()
                except Exception:
                    pass

                try:
                    throttle_db()
                    sb_service.schema(SUPABASE_SCHEMA).table("attendance").insert(payload_rows).execute()
                    present_count = sum(1 for r in payload_rows if r.get("present") is True)
                    absent_count = len(payload_rows) - present_count
                    st.success(f"Attendance saved ✅ Present: {present_count} • Absent: {absent_count}")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error("Failed to save attendance.")
                    st.code(_api_msg(e), language="text")

        st.divider()
        st.markdown("### Current session attendance (read)")

        if table_readable(sb_anon, SUPABASE_SCHEMA, "v_attendance_with_member"):
            dfa = load_attendance_view(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA, int(current_session_id))
            if dfa.empty:
                st.info("No attendance recorded for this session yet.")
            else:
                st.dataframe(dfa, use_container_width=True, hide_index=True)
        else:
            dfa = pd.DataFrame(arows_existing)
            if dfa.empty:
                st.info("No attendance recorded for this session yet.")
            else:
                dm = df_members[["id", "member_name"]].rename(columns={"id": "member_id"})
                dfa["member_id"] = pd.to_numeric(dfa["member_id"], errors="coerce")
                dfa = dfa.merge(dm, on="member_id", how="left")
                dfa = dfa[["member_id", "member_name", "present", "note", "created_at"]]
                st.warning("View v_attendance_with_member not readable. Showing attendance joined in Python.")
                st.dataframe(dfa, use_container_width=True, hide_index=True)

        # ✅ PDF Download (Attendance)
        _, make_attendance_pdf, pdf_err = get_pdf_tools()
        if make_attendance_pdf is None:
            st.caption("PDF download not available.")
            st.code(pdf_err or "", language="text")
        else:
            try:
                pdf_bytes = make_attendance_pdf(
                    brand=APP_BRAND,
                    session_id=int(current_session_id),
                    attendance_rows=arows_existing or [],
                    logo_path="assets/logo.png",
                )
                fname = f"attendance_session_{int(current_session_id)}.pdf"
                st.download_button(
                    "⬇️ Download Attendance PDF (current session)",
                    data=pdf_bytes,
                    file_name=fname,
                    mime="application/pdf",
                    use_container_width=True,
                )
            except Exception as e:
                st.error("Failed to generate Attendance PDF.")
                st.code(_api_msg(e), language="text")

        st.markdown(glass_close(), unsafe_allow_html=True)

    # ---------- Summaries ----------
    with tab3:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Summaries")

        st.markdown("### 📝 Minutes summary")
        m_rows = safe_select(sb_anon, "minutes", "*", schema=SUPABASE_SCHEMA, order_by="updated_at", order_desc=True, limit=20, show_error=False)
        dfm = pd.DataFrame(m_rows)
        if dfm.empty:
            st.info("No minutes recorded yet.")
        else:
            pick_id = st.selectbox("Pick minutes ID", dfm["id"].tolist(), index=0, key="sum_minutes_pick")
            row = dfm[dfm["id"] == pick_id].iloc[0].to_dict()
            st.write(f"**{row.get('title','')}**  •  session {row.get('session_id','')}")
            content = str(row.get("body", ""))
            lines = [ln.strip("-• ").strip() for ln in content.splitlines() if ln.strip()]
            bullets = [ln for ln in lines if len(ln) > 6][:8]
            if bullets:
                st.markdown("**Highlights**")
                for b in bullets:
                    st.write(f"• {b}")
            else:
                st.write((content[:700] + "…") if len(content) > 700 else content)

        st.divider()
        st.markdown("### ✅ Attendance summary (current session)")
        dfa0 = pd.DataFrame(arows_existing)
        if dfa0.empty:
            st.info("No attendance for current session.")
        else:
            present_count = int(dfa0["present"].astype(bool).sum()) if "present" in dfa0.columns else 0
            st.metric("Present count", f"{present_count:,}")
            st.metric("Absent count", f"{(len(dfa0) - present_count):,}")

        st.markdown(glass_close(), unsafe_allow_html=True)

elif page == "Admin":
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Admin")
    st.caption("Sessions, members, and basic setup utilities.")

    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY to enable admin writes.")
        st.markdown(glass_close(), unsafe_allow_html=True)
        st.stop()

    admin_fn, admin_err = lazy_import("admin_panels", "render_admin_panel")
    if admin_fn is not None:
        admin_fn(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)
    else:
        st.caption("Optional admin_panels not found; using built-in admin placeholder.")
        st.code(admin_err or "admin_panels missing", language="text")
        st.info("To enable Admin, ensure admin_panels.py defines: render_admin_panel(sb_anon, sb_service, schema)")

    st.markdown(glass_close(), unsafe_allow_html=True)

elif page == "Audit":
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Audit")
    st.caption("Reads audit_log if available (or shows status).")

    if table_readable(sb_anon, SUPABASE_SCHEMA, "audit_log"):
        rows = safe_select(
            sb_anon,
            "audit_log",
            "*",
            schema=SUPABASE_SCHEMA,
            order_by="created_at",
            order_desc=True,
            limit=300,
            show_error=False,
        )
        dfa = pd.DataFrame(rows)
        if dfa.empty:
            st.info("audit_log is readable but has no rows.")
        else:
            st.dataframe(dfa, use_container_width=True, hide_index=True)
    else:
        st.warning(f"{SUPABASE_SCHEMA}.audit_log not readable (missing table or RLS).")

    audit_fn, audit_err = lazy_import("audit_panel", "render_audit_panel")
    if audit_fn is not None:
        st.divider()
        audit_fn(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)
    elif audit_err:
        st.caption("Optional audit_panel not loaded (this is okay).")
        st.code(audit_err, language="text")

    st.markdown(glass_close(), unsafe_allow_html=True)

elif page == "Health":
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Health")
    st.caption("Quick readability check for key tables/views. Helps diagnose RLS/schema issues.")

    objects = [
        ("table", "members"),
        ("table", "sessions"),
        ("table", "app_state"),
        ("table", "contributions"),
        ("table", "foundation_contributions"),
        ("table", "loans"),
        ("table", "loan_payments"),
        ("table", "interest_ledger"),
        ("table", "fines"),
        ("table", "minutes"),
        ("table", "attendance"),
        ("table", "payouts"),
        ("table", "audit_log"),
        ("view", "v_contributions_with_member"),
        ("view", "v_attendance_with_member"),
        ("view", "v_next_beneficiary"),
    ]

    rows = []
    for typ, name in objects:
        ok_anon = table_readable(sb_anon, SUPABASE_SCHEMA, name)
        ok_srv = table_readable(sb_service, SUPABASE_SCHEMA, name) if sb_service else False
        rows.append(
            {
                "object": f"{typ}:{name}",
                "anon_read": "✅" if ok_anon else "❌",
                "service_read": "✅" if ok_srv else ("—" if not sb_service else "❌"),
            }
        )

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

else:
    st.info("Select a page from the sidebar menu.")
