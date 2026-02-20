# app.py ✅ COMPLETE SINGLE CODE — NJANGI STANDARD (NO legacy) — FAST VERSION + SLOW/GENTLE MODE + 🤖 AI MODE
# ------------------------------------------------------------------------------
# ✅ Adds "AI Mode" (Young) in the app:
#   - A global AI Assistant expander in the sidebar
#   - A full "🤖 AI Mode" page (chat + smart shortcuts)
#   - Can route you to Risk Panel, Njangi LLM, Audit AI
#
# ✅ Fixes your cache crash:
#   - Never passes Supabase client into @st.cache_data functions
#   - Cached functions accept only hashable primitives (url, key, schema, ids)
#
# ✅ FAST/Slow:
#   - Fast Mode toggle reduces throttle + shorter cache TTL
#   - Slow Mode keeps stability for Supabase/Railway limits
#
# ✅ Safe Mode:
#   - Dashboard only if other modules break
#
# ✅ NJANGI STANDARD objects:
#   tables: members, sessions, app_state, minutes, attendance, contributions, foundation_contributions,
#           payouts, loans, loan_payments, fines, interest_ledger, audit_log
#   views (optional): v_next_beneficiary, v_contributions_with_member, v_attendance_with_member
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
W_STRETCH = "stretch"

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

# FAST defaults (you can override with secrets):
FAST_MODE_DEFAULT = str(get_secret("FAST_MODE", "1")).strip() not in ("0", "false", "False", "no", "NO")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error(
        "Missing SUPABASE_URL or SUPABASE_ANON_KEY.\n\n"
        "Streamlit Cloud: Manage app → Settings → Secrets\n\n"
        "Add:\n"
        "SUPABASE_URL\nSUPABASE_ANON_KEY\n(optional) SUPABASE_SERVICE_KEY\nSUPABASE_SCHEMA\n(optional) FAST_MODE"
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
# SLOW MODE (THROTTLE DB CALLS) + FAST MODE OVERRIDE
# ============================================================
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


# ============================================================
# LAZY IMPORT HELPER ✅ Railway-safe
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

    st.caption("If this shows the WRONG project ref, fix Streamlit secrets / Railway variables.")
    st.markdown(glass_close(), unsafe_allow_html=True)


# ============================================================
# 🤖 AI MODE (Young) — lightweight helper (no external LLM)
# ============================================================
def _young_reply(user_text: str) -> str:
    t = (user_text or "").strip().lower()

    if not t:
        return "Tell me what you want to do: Dashboard, Contributions, Loans, Payouts, Audit, or a Risk check."

    # Routing / shortcuts
    if any(k in t for k in ["risk", "ai risk", "default", "probability", "score"]):
        return "Open **🤖 AI Risk Panel** from the left menu. If it says 'single class', you need loans.status with both good/bad labels."
    if any(k in t for k in ["llm", "assistant", "njangi llm", "chat with data"]):
        return "Open **🧠 Njangi LLM** from the left menu — ask questions like: *'How much did we collect this session?'*"
    if any(k in t for k in ["audit", "logs", "who did", "history"]):
        return "Open **Audit** page — the AI audit panel can summarize recent actions and errors."
    if any(k in t for k in ["dashboard", "kpi", "net", "cash available", "pot"]):
        return "Dashboard shows KPIs. If you want, tell me: *which KPI looks wrong* and I’ll guide you to the table causing it."

    # Quick data help
    if "rls" in t or "policy" in t:
        return "If reads fail, it is usually **RLS**. Use **Health** page to see which tables are blocked for anon vs service."
    if "cache" in t and "unhashable" in t:
        return "That error happens when a Supabase client is passed into @st.cache_data. We fixed that by caching only (url,key,schema)."

    return (
        "I can help with:\n"
        "• **Check errors** (paste the traceback)\n"
        "• **Explain numbers** (pot/cash/net)\n"
        "• **Guide actions** (record attendance, create session, approve loans)\n\n"
        "Ask me a question like: *'Why is Cash Available negative?'*"
    )


def ai_sidebar_assistant():
    with st.sidebar.expander("🤖 AI Mode (Young)", expanded=False):
        st.caption("Ask Young anything about your Njangi system (lightweight, no OpenAI).")
        q = st.text_input("Ask Young", key="young_sidebar_q", placeholder="e.g., Why is Cash Available 0?")
        if st.button("Ask", key="young_sidebar_btn", width=W_STRETCH):
            st.session_state["young_last_answer"] = _young_reply(q)

        ans = st.session_state.get("young_last_answer")
        if ans:
            st.markdown(ans)

        st.divider()
        st.caption("Quick actions")
        if st.button("Go to 🤖 AI Risk Panel", key="go_risk", width=W_STRETCH):
            st.session_state["main_menu"] = "🤖 AI Risk Panel"
            st.rerun()
        if st.button("Go to 🧠 Njangi LLM", key="go_llm", width=W_STRETCH):
            st.session_state["main_menu"] = "🧠 Njangi LLM"
            st.rerun()
        if st.button("Go to Audit", key="go_audit", width=W_STRETCH):
            st.session_state["main_menu"] = "Audit"
            st.rerun()


# ============================================================
# TOP BAR
# ============================================================
left, right = st.columns([1, 0.30])
with left:
    st.markdown(f"## 🏦 {APP_BRAND} • Bank Dashboard")
    if st.session_state.get("_slow_mode_override", SLOW_MODE_DEFAULT):
        st.caption("🐢 Slow Mode ON (reduced DB load)")
    else:
        st.caption("⚡ Fast Mode ON (minimal throttling)")
with right:
    if st.button("🔄 Refresh data", width=W_STRETCH):
        st.cache_data.clear()
        st.rerun()

with st.expander("🔎 Show connected database details", expanded=False):
    show_connected_db_banner()

# ============================================================
# SIDEBAR SAFE MODE / FAST/SLOW MODE
# ============================================================
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

# Add AI Mode block in sidebar
ai_sidebar_assistant()

# Effective slow settings
SLOW_MODE = bool(st.session_state.get("_slow_mode_override", SLOW_MODE_DEFAULT))
MIN_SECONDS_BETWEEN_DB_CALLS = float(
    st.session_state.get("MIN_SECONDS_BETWEEN_DB_CALLS_UI", MIN_SECONDS_BETWEEN_DB_CALLS_DEFAULT)
)

# ============================================================
# CACHED LOADERS (FAST TTLs) — IMPORTANT: NO Supabase client in cache args
# ============================================================
MEMBERS_TTL = 120 if not SLOW_MODE else 300
VIEW_TTL = 90 if not SLOW_MODE else 240


@st.cache_data(ttl=MEMBERS_TTL, show_spinner=False)
def load_members(url: str, anon_key: str, schema: str) -> pd.DataFrame:
    client = create_client(url, anon_key)

    cols_try = [
        "id,name,display_name,full_name,phone",
        "id,name,display_name,phone",
        "id,name,phone",
        "id,full_name,phone",
        "id,display_name,phone",
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
    df["phone"] = df.get("phone", "").astype(str).replace({"None": "", "nan": ""})
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
        "🤖 AI Mode",
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

elif page == "🤖 AI Mode":
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("🤖 AI Mode (Young)")
    st.caption("Your Njangi assistant — routing + troubleshooting + guidance (no external LLM).")

    if "young_chat" not in st.session_state:
        st.session_state["young_chat"] = []

    c1, c2 = st.columns([0.72, 0.28])
    with c2:
        st.markdown("### Quick actions")
        if st.button("Open Dashboard", width=W_STRETCH):
            st.session_state["main_menu"] = "Dashboard"
            st.rerun()
        if st.button("Open 🤖 AI Risk Panel", width=W_STRETCH):
            st.session_state["main_menu"] = "🤖 AI Risk Panel"
            st.rerun()
        if st.button("Open 🧠 Njangi LLM", width=W_STRETCH):
            st.session_state["main_menu"] = "🧠 Njangi LLM"
            st.rerun()
        if st.button("Open Audit", width=W_STRETCH):
            st.session_state["main_menu"] = "Audit"
            st.rerun()

        st.divider()
        st.markdown("### Health tips")
        st.write("• If a page is blank → enable **Safe Mode**")
        st.write("• If reads fail → check **Health** page for RLS blocks")
        st.write("• If cache error → never pass `sb_*` into cache")

    with c1:
        st.markdown("### Chat")
        for m in st.session_state["young_chat"]:
            if m["role"] == "user":
                st.markdown(f"**You:** {m['text']}")
            else:
                st.markdown(f"**Young:** {m['text']}")

        user_q = st.text_input("Ask Young", placeholder="e.g., Why is Cash Available negative?", key="young_page_q")
        if st.button("Send", width=W_STRETCH):
            ans = _young_reply(user_q)
            st.session_state["young_chat"].append({"role": "user", "text": user_q})
            st.session_state["young_chat"].append({"role": "assistant", "text": ans})
            st.rerun()

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

elif page == "🧠 Njangi LLM":
    fn, err = lazy_import("njangi_llm_panel", "render_njangi_llm_panel")
    if fn is None:
        st.error("Njangi LLM panel failed to load.")
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

    with tab1:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Meeting Minutes / Documentation")
        st.caption(f"Linked session_id: {current_session_id}  •  {session_note}")

        if can_write:
            with st.form("minutes_form", clear_on_submit=False):
                title = st.text_input("Title", key="minutes_title")
                body = st.text_area("Minutes / Documentation", height=260, key="minutes_body")
                ok = st.form_submit_button("💾 Save minutes", width=W_STRETCH)

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
            session_id=int(current_session_id),
        )
        dfm = pd.DataFrame(rows)
        if dfm.empty:
            st.info("No minutes recorded yet.")
        else:
            st.dataframe(dfm, width=W_STRETCH, hide_index=True)
        st.markdown(glass_close(), unsafe_allow_html=True)

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
            session_id=int(current_session_id),
            show_error=False,
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

            save = st.form_submit_button("💾 Save attendance (ALL members)", width=W_STRETCH)

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
                st.dataframe(dfa, width=W_STRETCH, hide_index=True)
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
                st.dataframe(dfa, width=W_STRETCH, hide_index=True)

        st.markdown(glass_close(), unsafe_allow_html=True)

    with tab3:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Summaries")

        st.markdown("### 📝 Minutes summary")
        m_rows = safe_select(
            sb_anon, "minutes", "*", schema=SUPABASE_SCHEMA, order_by="updated_at", order_desc=True, limit=20, show_error=False
        )
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

        st.divider()
        st.markdown("### 💰 Contributions summary (current session)")
        dfc = load_contributions_view(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA, slow_mode=SLOW_MODE)

        if dfc.empty:
            c_rows = safe_select(
                sb_anon,
                "contributions",
                "member_id,session_id,amount,paid_at,created_at",
                schema=SUPABASE_SCHEMA,
                order_by="created_at",
                order_desc=True,
                limit=1500,
                session_id=int(current_session_id),
                show_error=False,
            )
            dfc = pd.DataFrame(c_rows)

        if dfc.empty:
            st.info("No contributions for current session.")
        else:
            if "amount" in dfc.columns:
                dfc["amount"] = pd.to_numeric(dfc["amount"], errors="coerce").fillna(0.0)
            if "session_id" in dfc.columns:
                dfc["session_id"] = pd.to_numeric(dfc["session_id"], errors="coerce")
                dfc = dfc[dfc["session_id"] == float(current_session_id)].copy()

            total_amt = float(dfc["amount"].sum()) if "amount" in dfc.columns else 0.0
            st.metric("Total contributions (session)", f"{total_amt:,.0f}")

            if "member_name" in dfc.columns:
                by = (
                    dfc.groupby("member_name", dropna=False)["amount"]
                    .sum()
                    .sort_values(ascending=False)
                    .reset_index()
                    .rename(columns={"amount": "total_amount"})
                )
                st.dataframe(by, width=W_STRETCH, hide_index=True)
            elif "member_id" in dfc.columns:
                by = (
                    dfc.groupby("member_id", dropna=False)["amount"]
                    .sum()
                    .sort_values(ascending=False)
                    .reset_index()
                    .rename(columns={"amount": "total_amount"})
                )
                st.dataframe(by, width=W_STRETCH, hide_index=True)
            else:
                st.dataframe(dfc, width=W_STRETCH, hide_index=True)

        st.markdown(glass_close(), unsafe_allow_html=True)

elif page == "Admin":
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Admin")
    st.caption("Sessions, members, and basic setup utilities.")

    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY to enable admin writes.")
        st.markdown(glass_close(), unsafe_allow_html=True)
        st.stop()

    admin_fn, admin_err = lazy_import("admin_panel", "render_admin_panel")
    if admin_fn is not None:
        admin_fn(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)
        st.markdown(glass_close(), unsafe_allow_html=True)
    else:
        st.markdown("### 📅 Sessions")
        sessions = safe_select(
            sb_anon,
            "sessions",
            "id,session_id,start_date,end_date,created_at",
            schema=SUPABASE_SCHEMA,
            order_by="session_id",
            order_desc=True,
            limit=200,
            show_error=False,
        )
        dfs = pd.DataFrame(sessions)
        if dfs.empty:
            st.info("No sessions yet.")
        else:
            st.dataframe(dfs, width=W_STRETCH, hide_index=True)

        st.divider()
        st.markdown("### ➕ Create a new session")
        with st.form("create_session_form"):
            new_session_id = st.number_input("Session number (session_id)", min_value=1, value=1, step=1)
            start_date = st.date_input("Start date", value=datetime.now().date())
            end_date = st.date_input("End date", value=datetime.now().date())
            create_btn = st.form_submit_button("Create session", width=W_STRETCH)

        if create_btn:
            try:
                payload = {
                    "session_id": int(new_session_id),
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                    "created_at": now_iso(),
                }
                throttle_db()
                sb_service.schema(SUPABASE_SCHEMA).table("sessions").insert(payload).execute()
                st.success("Session created.")
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error("Failed to create session.")
                st.code(_api_msg(e), language="text")

        st.divider()
        st.markdown("### 🎯 Set current session (app_state)")
        current_session_id, note = get_effective_session_id(sb_anon, SUPABASE_SCHEMA)
        st.write("Current session:", current_session_id, f"({note})")
        set_to = st.number_input("Set current_session_id to", min_value=1, value=int(current_session_id or 1), step=1)

        if st.button("Save current_session_id", width=W_STRETCH):
            try:
                throttle_db()
                exists = safe_select(sb_service, "app_state", "id", schema=SUPABASE_SCHEMA, limit=1, show_error=False, id=1)
                if exists:
                    sb_service.schema(SUPABASE_SCHEMA).table("app_state").update(
                        {"current_session_id": int(set_to), "updated_at": now_iso()}
                    ).eq("id", 1).execute()
                else:
                    sb_service.schema(SUPABASE_SCHEMA).table("app_state").insert(
                        {"id": 1, "current_session_id": int(set_to), "created_at": now_iso(), "updated_at": now_iso()}
                    ).execute()
                st.success("Updated app_state.current_session_id")
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error("Failed to update app_state.")
                st.code(_api_msg(e), language="text")

        if admin_err:
            st.caption("Optional admin_panel not found; using built-in admin tools.")
            st.code(admin_err, language="text")

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
            st.dataframe(dfa, width=W_STRETCH, hide_index=True)
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

    st.dataframe(pd.DataFrame(rows), width=W_STRETCH, hide_index=True)

    health_fn, health_err = lazy_import("health_panel", "render_health_panel")
    if health_fn is not None:
        st.divider()
        health_fn(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)
    elif health_err:
        st.caption("Optional health_panel not loaded (this is okay).")
        st.code(health_err, language="text")

    st.markdown(glass_close(), unsafe_allow_html=True)

else:
    st.info("Select a page from the sidebar menu.")
