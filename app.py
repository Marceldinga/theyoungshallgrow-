
# app.py ✅ COMPLETE SINGLE FILE — NJANGI STANDARD (NO legacy)
# One-file Streamlit app (NO external modules required)
# ------------------------------------------------------------------------------
# ✅ Includes:
# - Dashboard (LIVE snapshot)
# - 👩🏾‍💼 Young — grounded dashboard copilot (rules + optional Tavily web + optional OpenAI LLM)
# - Contributions (view if available, else raw table)
# - Minutes & Attendance (write requires SERVICE key)
# - Admin (basic sessions + app_state.current_session_id helper)
# - Audit (reads audit_log if exists)
# - Health (readability checks)
# - Safe navigation (no session_state main_menu crash) + Dashboard AI -> Menu navigation bridge
#
# ✅ Fixes:
# - No duplicate “Quick actions” sidebar menu
# - Dashboard AI can navigate by setting st.session_state["page"] then st.rerun()
#
# ENV / Secrets (Railway/Streamlit Cloud):
#   SUPABASE_URL
#   SUPABASE_ANON_KEY
#   (optional) SUPABASE_SERVICE_KEY
#   (optional) SUPABASE_SCHEMA=public
#   (optional) LOCAL_TZ=America/New_York
#   (optional) TAVILY_API_KEY=...
#   (optional) OPENAI_API_KEY=...
#   (optional) OPENAI_MODEL=gpt-4o-mini
#
# Requirements:
#   pip install streamlit pandas supabase postgrest requests openai
#   (openai only needed if you enable LLM mode)

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from postgrest.exceptions import APIError
from supabase import create_client

# -----------------------------
# PAGE CONFIG
# -----------------------------
APP_BRAND = "theyoungshallgrow"
W_STRETCH = "stretch"

st.set_page_config(
    page_title=f"{APP_BRAND} • Bank Dashboard",
    layout="wide",
    page_icon="🏦",
)

# -----------------------------
# THEME (Midnight Navy + Emerald)
# -----------------------------
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

# -----------------------------
# TIME helpers
# -----------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_now(tz_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo  # py3.9+
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(timezone.utc)


def _greeting_of_day(local_dt: datetime) -> str:
    h = int(local_dt.hour)
    if h < 12:
        return "Good morning"
    if h < 18:
        return "Good afternoon"
    return "Good evening"


# -----------------------------
# Secrets / ENV
# -----------------------------
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
        "Add these in Railway Variables or Streamlit Secrets."
    )
    st.stop()

# -----------------------------
# Clients
# -----------------------------
@st.cache_resource
def get_client(url: str, key: str):
    return create_client(url.strip(), key.strip())


sb_anon = get_client(SUPABASE_URL, SUPABASE_ANON_KEY)
sb_service = get_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else None

# -----------------------------
# DB throttle (protect limits)
# -----------------------------
SLOW_MODE_DEFAULT = str(get_secret("SLOW_MODE", "1")).strip().lower() not in ("0", "false", "no")
MIN_WAIT_DEFAULT = float(get_secret("MIN_SECONDS_BETWEEN_DB_CALLS", "0.30") or "0.30")

def throttle_db():
    if not st.session_state.get("_slow_mode", SLOW_MODE_DEFAULT):
        return
    last = float(st.session_state.get("_last_db_ts", 0.0))
    now = time.time()
    min_wait = float(st.session_state.get("_min_wait", MIN_WAIT_DEFAULT))
    wait = min_wait - (now - last)
    if wait > 0:
        time.sleep(wait)
    st.session_state["_last_db_ts"] = time.time()

# -----------------------------
# Safe error text
# -----------------------------
def api_msg(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or "APIError")
        return str(e)
    return repr(e)

# -----------------------------
# Safe DB ops
# -----------------------------
def safe_select(
    client,
    table: str,
    cols: str = "*",
    schema: str = "public",
    limit: Optional[int] = None,
    order_by: Optional[str] = None,
    desc: bool = False,
    show_error: bool = False,
    **filters,
) -> List[Dict[str, Any]]:
    if client is None:
        return []
    try:
        throttle_db()
        q = client.schema(schema).table(table).select(cols)
        for k, v in (filters or {}).items():
            if v is None:
                continue
            q = q.eq(k, v)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit is not None:
            q = q.limit(int(limit))
        return (q.execute().data or [])
    except Exception as e:
        if show_error:
            st.error(f"Error reading {schema}.{table}")
            st.code(api_msg(e), language="text")
        return []

def safe_insert(client, schema: str, table: str, row: Dict[str, Any]) -> bool:
    if client is None:
        return False
    try:
        throttle_db()
        client.schema(schema).table(table).insert(row).execute()
        return True
    except Exception:
        return False

def safe_update_eq(client, schema: str, table: str, updates: Dict[str, Any], eq_key: str, eq_val: Any) -> bool:
    if client is None:
        return False
    try:
        throttle_db()
        client.schema(schema).table(table).update(updates).eq(eq_key, eq_val).execute()
        return True
    except Exception:
        return False

def safe_delete_eq(client, schema: str, table: str, eq_key: str, eq_val: Any) -> bool:
    if client is None:
        return False
    try:
        throttle_db()
        client.schema(schema).table(table).delete().eq(eq_key, eq_val).execute()
        return True
    except Exception:
        return False

def table_readable(client, schema: str, name: str) -> bool:
    if client is None:
        return False
    try:
        throttle_db()
        client.schema(schema).table(name).select("*").limit(1).execute()
        return True
    except Exception:
        return False

def sum_amount(rows: List[Dict[str, Any]], col: str = "amount") -> float:
    s = 0.0
    for r in rows or []:
        try:
            s += float(r.get(col) or 0)
        except Exception:
            pass
    return float(s)

# -----------------------------
# Navigation (SAFE) + Dashboard AI bridge
# -----------------------------
def request_nav(page_name: str):
    st.session_state["nav_request"] = page_name
    st.rerun()

def apply_nav_before_widget(default_page: str, allowed_pages: List[str]):
    if "main_menu" not in st.session_state:
        st.session_state["main_menu"] = default_page
    if "nav_request" not in st.session_state:
        st.session_state["nav_request"] = None

    # Dashboard AI can set st.session_state["page"]
    dash_req = st.session_state.get("page")
    if isinstance(dash_req, str) and dash_req.strip():
        st.session_state["nav_request"] = dash_req.strip()
        st.session_state["page"] = None

    req = st.session_state.get("nav_request")
    if isinstance(req, str) and req in allowed_pages:
        st.session_state["main_menu"] = req
    st.session_state["nav_request"] = None

# -----------------------------
# Tavily web search
# -----------------------------
def has_tavily_key() -> bool:
    return bool((get_secret("TAVILY_API_KEY") or "").strip())

def is_web_query(q: str) -> bool:
    t = (q or "").strip().lower()
    return t.startswith("web:") or t.startswith("internet:") or t.startswith("tavily:")

def strip_web_prefix(q: str) -> str:
    return re.sub(r"^(web:|internet:|tavily:)\s*", "", (q or "").strip(), flags=re.IGNORECASE).strip()

@st.cache_data(ttl=3600, show_spinner=False)
def tavily_search_cached(query: str, max_results: int) -> Dict[str, Any]:
    import requests
    api_key = (get_secret("TAVILY_API_KEY") or "").strip()
    if not api_key:
        return {"error": "Missing TAVILY_API_KEY."}
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            json={"query": query, "max_results": int(max_results), "search_depth": "basic"},
            timeout=20,
        )
        if r.status_code != 200:
            return {"error": f"Tavily error {r.status_code}: {r.text[:300]}"}
        j = r.json()
        return j if isinstance(j, dict) else {"raw": r.text}
    except Exception as e:
        return {"error": repr(e)}

def format_web_results(tav: Dict[str, Any], take: int = 3) -> Tuple[str, List[Dict[str, str]]]:
    if not isinstance(tav, dict):
        return ("Internet results unreadable.", [])
    if "error" in tav:
        return (f"Internet search failed: {tav['error']}", [])
    results = tav.get("results") or []
    if not results:
        return ("I searched the web but didn’t find clear results. Try rephrasing.", [])
    bullets, sources = [], []
    for r in results[: max(1, take)]:
        title = str(r.get("title") or "Source").strip()
        url = str(r.get("url") or "").strip()
        content = str(r.get("content") or "").strip()
        if content:
            bullets.append(f"• {content[:240].rstrip()}…")
        if url:
            sources.append({"title": title, "url": url})
    return ("Here’s what I found online:\n" + "\n".join(bullets), sources)

# -----------------------------
# OpenAI (optional, strictly grounded)
# -----------------------------
def has_openai_key() -> bool:
    return bool((get_secret("OPENAI_API_KEY") or "").strip())

def openai_model() -> str:
    return (get_secret("OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini").strip()

@st.cache_data(ttl=120, show_spinner=False)
def llm_answer_cached(question: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    if not has_openai_key():
        return {"error": "Missing OPENAI_API_KEY."}
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:
        return {"error": f"openai package not installed: {repr(e)}"}

    instructions = (
        "You are 'Young', a dashboard copilot for a community finance app.\n"
        "You MUST answer using ONLY the provided DASHBOARD_SNAPSHOT JSON.\n"
        "Rules:\n"
        "- If not explicitly in the snapshot, say you don't have it.\n"
        "- Never invent numbers, names, totals, dates, or statuses.\n"
        "- Never claim who built you (no Amazon/OpenAI identity claims).\n"
        "- Be concise.\n"
    )
    user_input = f"DASHBOARD_SNAPSHOT:\n{snapshot}\n\nQUESTION:\n{question}\n"

    try:
        client = OpenAI()
        text_out = None
        try:
            resp = client.responses.create(
                model=openai_model(),
                instructions=instructions,
                input=user_input,
            )
            text_out = getattr(resp, "output_text", None)
        except Exception:
            text_out = None

        if not text_out:
            chat = client.chat.completions.create(
                model=openai_model(),
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": user_input},
                ],
            )
            text_out = (chat.choices[0].message.content or "").strip()

        if not text_out:
            return {"error": "LLM returned empty."}
        return {"text": text_out.strip()}
    except Exception as e:
        return {"error": repr(e)}

# -----------------------------
# Grounded Young rules
# -----------------------------
def young_answer_rules(q: str, snap: Dict[str, Any]) -> str:
    t = (q or "").strip().lower()

    def fmt(x: Any) -> str:
        try:
            return f"{float(x):,.0f}"
        except Exception:
            return str(x)

    session_id = snap.get("session_id")
    total_members = snap.get("members_count", 0)
    contributions_total = snap.get("contributions_total", 0.0)
    payments_total = snap.get("payments_total", 0.0)
    interest_total = snap.get("interest_total", 0.0)
    fines_total = snap.get("fines_total", 0.0)
    loans_count = snap.get("loans_count", 0)
    loans_active_count = snap.get("loans_active_count", 0)
    loans_active_total = snap.get("loans_active_total", 0.0)

    if not t:
        return "Ask: **loans summary**, **contributions total**, **interest total**, **fines total**, **payments total**."

    if "loan" in t:
        if loans_count == 0:
            return "Snapshot shows **no loans rows**."
        return (
            f"**Loans summary**\n"
            f"• Total loan rows: **{loans_count}**\n"
            f"• Active/overdue/open: **{loans_active_count}**\n"
            f"• Active total (approx): **{fmt(loans_active_total)}**\n"
            "If you want who owes what, filter by member and open Loans (or ask for member details)."
        )

    if "contribution" in t or "pot" in t or "collect" in t:
        return f"**Contributions total** = **{fmt(contributions_total)}** (members: {total_members}, session: {session_id})."

    if "payment" in t or "repay" in t:
        return f"**Payments total** = **{fmt(payments_total)}**."

    if "interest" in t:
        return f"**Interest ledger total** = **{fmt(interest_total)}**."

    if "fine" in t:
        return f"**Fines total** = **{fmt(fines_total)}**."

    if "session" in t:
        return f"Current **session_id** = **{session_id}**."

    if "help" in t or "what can you do" in t:
        return (
            "I answer from your LIVE Njangi snapshot:\n"
            "• loans summary\n"
            "• contributions total\n"
            "• payments total\n"
            "• interest total\n"
            "• fines total\n\n"
            "For internet info: type **web: your question** (only if Internet mode is ON)."
        )

    return "Try: **loans summary**, **contributions total**, **interest total**, **fines total**, **payments total**."

# -----------------------------
# App State / session helper
# -----------------------------
def get_current_session_id(sb_read, schema: str) -> Tuple[Optional[int], str]:
    # app_state first
    st_rows = safe_select(sb_read, "app_state", "id,current_session_id", schema=schema, limit=1, show_error=False)
    if st_rows:
        raw = st_rows[0].get("current_session_id")
        try:
            if raw is not None and str(raw).strip() != "":
                return int(float(raw)), "from app_state"
        except Exception:
            pass

    # fallback sessions.id
    s1 = safe_select(sb_read, "sessions", "id,created_at", schema=schema, order_by="id", desc=True, limit=1, show_error=False)
    if s1 and s1[0].get("id") is not None:
        try:
            return int(float(s1[0]["id"])), "fallback: latest sessions.id"
        except Exception:
            pass

    # fallback sessions.session_id
    s2 = safe_select(sb_read, "sessions", "session_id,created_at", schema=schema, order_by="session_id", desc=True, limit=1, show_error=False)
    if s2 and s2[0].get("session_id") is not None:
        try:
            return int(float(s2[0]["session_id"])), "fallback: latest sessions.session_id"
        except Exception:
            pass

    return None, "no sessions"

# -----------------------------
# Settings (sidebar)
# -----------------------------
with st.sidebar.expander("⚙️ Settings", expanded=False):
    max_rows = st.slider("Max rows per table", 500, 10000, int(st.session_state.get("_max_rows", 5000)), step=500)
    st.session_state["_max_rows"] = int(max_rows)

    st.divider()
    st.markdown("🕒 **Timezone (real)**")

    env_tz = (get_secret("LOCAL_TZ") or "").strip() or "America/New_York"
    tz_choice = st.selectbox("Timezone", ["ServerTime", env_tz], index=0, key="tz_choice")
    local_dt = datetime.now(timezone.utc) if tz_choice == "ServerTime" else _local_now(env_tz)
    st.caption(f"Local time: {local_dt.strftime('%Y-%m-%d %H:%M')}")

    st.divider()
    st.markdown("🌐 **Internet**")
    internet_mode = st.toggle("Internet mode (for web: queries)", value=bool(st.session_state.get("_internet_mode", False)))
    st.session_state["_internet_mode"] = bool(internet_mode)

    st.caption(f"Tavily key detected: {'YES' if has_tavily_key() else 'NO'}")
    web_sources = st.slider("Web sources", 2, 8, int(st.session_state.get("_web_sources", 5)), step=1)
    st.session_state["_web_sources"] = int(web_sources)

with st.sidebar.expander("⚡ Fast / 🐢 Slow Mode", expanded=False):
    fast_on = st.checkbox("Enable Fast Mode", value=bool(st.session_state.get("_fast_on", False)))
    st.session_state["_fast_on"] = bool(fast_on)
    if fast_on:
        st.session_state["_slow_mode"] = False
        st.session_state["_min_wait"] = 0.05
        st.caption("⚡ Fast Mode ON (minimal throttling)")
    else:
        st.session_state["_slow_mode"] = True
        st.session_state["_min_wait"] = st.slider("Min seconds between DB calls", 0.00, 2.00, float(st.session_state.get("_min_wait", MIN_WAIT_DEFAULT)), 0.05)
        st.caption("🐢 Slow Mode ON (reduced DB load)")

with st.sidebar.expander("🛟 Safe Mode", expanded=False):
    SAFE_MODE_UI = st.checkbox(
        "Run Dashboard only (disable optional pages)",
        value=bool(st.session_state.get("_safe_mode", False)),
        help="Use if you ever see blank screen; disables other pages.",
    )
    st.session_state["_safe_mode"] = bool(SAFE_MODE_UI)

# -----------------------------
# Top bar
# -----------------------------
left, right = st.columns([1, 0.30])
with left:
    st.markdown(f"## 🏦 {APP_BRAND} • Bank Dashboard")
    st.caption("⚡ Fast Mode ON (minimal throttling)" if st.session_state.get("_fast_on") else "🐢 Slow Mode ON (reduced DB load)")
with right:
    if st.button("🔄 Refresh data", width=W_STRETCH):
        st.cache_data.clear()
        st.rerun()

with st.expander("🔎 Show connected database details", expanded=False):
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.write("Schema:", f"`{SUPABASE_SCHEMA}`")
    st.write("Service key set:", "✅" if bool(SUPABASE_SERVICE_KEY) else "❌")
    try:
        throttle_db()
        test = sb_anon.schema(SUPABASE_SCHEMA).table("members").select("id").limit(1).execute().data or []
        st.success("Anon read test: ✅ can read members")
        st.write("Sample:", test)
    except Exception as e:
        st.error("Anon read test: ❌ cannot read members (RLS / schema / key issue)")
        st.code(api_msg(e), language="text")
    st.markdown(glass_close(), unsafe_allow_html=True)

# -----------------------------
# Pages list
# -----------------------------
if SAFE_MODE_UI:
    PAGES = ["Dashboard"]
else:
    PAGES = ["Dashboard", "🤖 AI Mode", "Contributions", "Minutes & Attendance", "Admin", "Audit", "Health"]

apply_nav_before_widget(default_page="Dashboard", allowed_pages=PAGES)
page = st.sidebar.radio("Menu", PAGES, key="main_menu")

# =============================================================================
# DASHBOARD
# =============================================================================
def render_dashboard_page():
    sb_read = sb_service if sb_service is not None else sb_anon
    max_rows = int(st.session_state.get("_max_rows", 5000))

    # Resolve session
    session_id, session_note = get_current_session_id(sb_read, SUPABASE_SCHEMA)

    # Live snapshot health
    members_rows = safe_select(sb_read, "members", "id", schema=SUPABASE_SCHEMA, limit=max_rows, show_error=False)
    sessions_rows = safe_select(sb_read, "sessions", "id", schema=SUPABASE_SCHEMA, limit=1, show_error=False)
    contrib_rows = safe_select(sb_read, "contributions", "amount,session_id,member_id,created_at", schema=SUPABASE_SCHEMA, limit=max_rows, show_error=False)
    loans_rows = safe_select(sb_read, "loans", "*", schema=SUPABASE_SCHEMA, limit=max_rows, show_error=False) if table_readable(sb_read, SUPABASE_SCHEMA, "loans") else []
    fines_rows = safe_select(sb_read, "fines", "amount,created_at", schema=SUPABASE_SCHEMA, limit=max_rows, show_error=False) if table_readable(sb_read, SUPABASE_SCHEMA, "fines") else []
    pay_rows = safe_select(sb_read, "loan_payments", "amount,created_at", schema=SUPABASE_SCHEMA, limit=max_rows, show_error=False) if table_readable(sb_read, SUPABASE_SCHEMA, "loan_payments") else []
    int_rows = safe_select(sb_read, "interest_ledger", "amount,created_at", schema=SUPABASE_SCHEMA, limit=max_rows, show_error=False) if table_readable(sb_read, SUPABASE_SCHEMA, "interest_ledger") else []

    members_count = len(members_rows or [])
    sessions_count = len(sessions_rows or [])
    contrib_count = len(contrib_rows or [])
    loans_count = len(loans_rows or [])
    fines_count = len(fines_rows or [])

    # Totals
    contributions_total = sum_amount(contrib_rows, "amount")
    payments_total = sum_amount(pay_rows, "amount")
    interest_total = sum_amount(int_rows, "amount")
    fines_total = sum_amount(fines_rows, "amount")

    # Loans active heuristics
    active_status = {"active", "overdue", "late", "open"}
    loans_active = []
    for r in loans_rows or []:
        stt = str(r.get("status") or "").strip().lower()
        if stt in active_status:
            loans_active.append(r)

    loans_active_count = len(loans_active)
    loans_active_total = 0.0
    for r in loans_active:
        for k in ("principal_current", "principal", "total_due", "amount"):
            if r.get(k) is not None:
                try:
                    loans_active_total += float(r.get(k) or 0)
                    break
                except Exception:
                    pass

    # Member selector (optional)
    member_id = None
    member_label = "(All members)"
    if members_rows:
        # try load member names if possible
        mdf = pd.DataFrame(safe_select(sb_read, "members", "*", schema=SUPABASE_SCHEMA, limit=min(max_rows, 2000), show_error=False))
        if not mdf.empty:
            for c in ["name", "display_name", "full_name"]:
                if c in mdf.columns:
                    mdf[c] = mdf[c].astype(str).replace({"None": "", "nan": ""})
            def best_name(r):
                for k in ["display_name", "full_name", "name"]:
                    v = str(r.get(k) or "").strip()
                    if v:
                        return v
                return ""
            if "id" in mdf.columns:
                mdf["id"] = pd.to_numeric(mdf["id"], errors="coerce").fillna(0).astype(int)
                mdf = mdf[mdf["id"] > 0].copy()
                mdf["member_name"] = mdf.apply(best_name, axis=1)
                mdf["label"] = mdf.apply(lambda r: f"{int(r['id']):02d} • {r['member_name']}", axis=1)
                options = ["(All members)"] + mdf["label"].tolist()
                pick = st.selectbox("Select member (optional)", options, index=0, key="dash_member_pick")
                if pick != "(All members)":
                    row = mdf[mdf["label"] == pick].iloc[0].to_dict()
                    member_id = int(row["id"])
                    member_label = pick

    loans_filter = st.selectbox("Loans filter", ["All", "Active", "Closed"], index=0, key="dash_loans_filter")

    # Filter totals by member if selected
    def filter_by_member(rows: List[Dict[str, Any]], key: str = "member_id") -> List[Dict[str, Any]]:
        if member_id is None:
            return rows or []
        out = []
        for r in rows or []:
            try:
                if int(float(r.get(key))) == int(member_id):
                    out.append(r)
            except Exception:
                pass
        return out

    contrib_rows_m = filter_by_member(contrib_rows, "member_id")
    loans_rows_m = filter_by_member(loans_rows, "member_id")

    if loans_filter == "Active":
        loans_rows_m = [r for r in loans_rows_m if str(r.get("status") or "").strip().lower() in active_status]
    elif loans_filter == "Closed":
        loans_rows_m = [r for r in loans_rows_m if str(r.get("status") or "").strip().lower() in {"closed", "paid", "cleared"}]

    contributions_total_m = sum_amount(contrib_rows_m, "amount")

    # Snapshot for Young
    tz_choice = st.session_state.get("tz_choice", "ServerTime")
    env_tz = (get_secret("LOCAL_TZ") or "").strip() or "America/New_York"
    local_dt = datetime.now(timezone.utc) if tz_choice == "ServerTime" else _local_now(env_tz)

    snapshot = {
        "schema": SUPABASE_SCHEMA,
        "utc": now_iso(),
        "timezone": tz_choice,
        "local_time": local_dt.strftime("%Y-%m-%d %H:%M"),
        "session_id": session_id,
        "session_note": session_note,
        "members_count": int(members_count),
        "sessions_count": int(sessions_count),
        "contrib_rows": int(contrib_count),
        "loans_rows": int(loans_count),
        "fines_rows": int(fines_count),
        "member_selected": member_label,
        "contributions_total": float(contributions_total_m if member_id is not None else contributions_total),
        "payments_total": float(payments_total),
        "interest_total": float(interest_total),
        "fines_total": float(fines_total),
        "loans_count": int(len(loans_rows_m) if member_id is not None else loans_count),
        "loans_active_count": int(loans_active_count),
        "loans_active_total": float(loans_active_total),
    }

    # Header
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.markdown("### 👩🏾‍💼 Young — Njangi Dashboard Copilot")
    st.caption("Smart grounded Q&A over Njangi data + REAL timezone greeting + Internet mode + optional OpenAI LLM.")
    st.success("👩🏾‍💼 Young is online")

    greet = _greeting_of_day(local_dt)
    st.write(f"**{greet} — connected to your Njangi snapshots and ready.**")
    st.caption(f"timezone: **{tz_choice}** • local: **{snapshot['local_time']}** • schema: **{SUPABASE_SCHEMA}** • utc: **{snapshot['utc']}**")

    st.divider()

    st.markdown("#### 📊 Live snapshot health")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Members", f"{members_count:,}")
    c2.metric("Sessions", f"{sessions_count:,}")
    c3.metric("Contrib rows", f"{contrib_count:,}")
    c4.metric("Loans rows", f"{loans_count:,}")
    c5.metric("Fines rows", f"{fines_count:,}")

    st.divider()

    st.markdown("#### ✨ Smart insights")
    st.write("**Totals (selected scope)**")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Contributions", f"${snapshot['contributions_total']:,.0f}")
    s2.metric("Payments", f"${payments_total:,.0f}")
    s3.metric("Interest ledger", f"${interest_total:,.0f}")
    s4.metric("Fines", f"${fines_total:,.0f}")

    st.write("**Loans (active snapshot)**")
    a1, a2 = st.columns(2)
    a1.metric("Active loans count", f"{loans_active_count:,}")
    a2.metric("Active loans total (approx)", f"${loans_active_total:,.0f}")

    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # -----------------------------
    # Young chat (NO duplicates)
    # -----------------------------
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.markdown("### 💬 Chat with Young")
    st.caption("Njangi questions stay grounded. Web answers only work when Internet mode is ON and you use: web: ...")

    # Navigation helpers (Dashboard AI -> Menu)
    nav_cols = st.columns(6)
    with nav_cols[0]:
        if st.button("Open Dashboard", width=W_STRETCH):
            st.session_state["page"] = "Dashboard"
            st.rerun()
    with nav_cols[1]:
        if st.button("Open Contributions", width=W_STRETCH):
            st.session_state["page"] = "Contributions"
            st.rerun()
    with nav_cols[2]:
        if st.button("Open Minutes", width=W_STRETCH):
            st.session_state["page"] = "Minutes & Attendance"
            st.rerun()
    with nav_cols[3]:
        if st.button("Open Admin", width=W_STRETCH):
            st.session_state["page"] = "Admin"
            st.rerun()
    with nav_cols[4]:
        if st.button("Open Audit", width=W_STRETCH):
            st.session_state["page"] = "Audit"
            st.rerun()
    with nav_cols[5]:
        if st.button("Open Health", width=W_STRETCH):
            st.session_state["page"] = "Health"
            st.rerun()

    st.divider()

    # LLM toggle (optional)
    use_llm = st.toggle(
        "Use LLM (OpenAI) for grounded answers",
        value=bool(st.session_state.get("_use_llm", False)),
        help="Requires OPENAI_API_KEY. Still forced to use ONLY the snapshot.",
        key="_use_llm",
    )

    lcol1, lcol2 = st.columns(2)
    with lcol1:
        st.caption(f"🧠 LLM: {'READY' if has_openai_key() else 'MISSING OPENAI_API_KEY'}")
    with lcol2:
        st.caption(f"🌐 Tavily: {'READY' if has_tavily_key() else 'MISSING TAVILY_API_KEY'}")

    if "young_chat" not in st.session_state:
        st.session_state["young_chat"] = []

    for m in st.session_state["young_chat"][-12:]:
        if m["role"] == "user":
            st.markdown(f"**You:** {m['text']}")
        else:
            st.markdown(f"**Young:** {m['text']}")

    q = st.text_input("Ask Young…", placeholder="e.g., loans summary OR web: Maryland cosmetology license requirements", key="young_q")
    if st.button("Ask", key="young_ask_btn", width=W_STRETCH):
        st.session_state["young_chat"].append({"role": "user", "text": q})

        # WEB path (only if internet mode ON + web:)
        if is_web_query(q):
            if not st.session_state.get("_internet_mode", False):
                ans = "Internet mode is OFF. Turn it ON in **Settings** to use `web:` queries."
                st.session_state["young_chat"].append({"role": "assistant", "text": ans})
            elif not has_tavily_key():
                ans = "Tavily is not configured. Add **TAVILY_API_KEY** in Railway Variables."
                st.session_state["young_chat"].append({"role": "assistant", "text": ans})
            else:
                query = strip_web_prefix(q)
                tav = tavily_search_cached(query=query, max_results=int(st.session_state.get("_web_sources", 5)))
                summary, sources = format_web_results(tav, take=3)
                # Guardrail: do not claim identity/builder
                safe_summary = summary.replace("built by", "created by").replace("Amazon", "[source]").strip()
                st.session_state["young_chat"].append({"role": "assistant", "text": safe_summary})
                if sources:
                    src_lines = "\n".join([f"- {s['title']}: {s['url']}" for s in sources[:5] if s.get("url")])
                    if src_lines.strip():
                        st.session_state["young_chat"].append({"role": "assistant", "text": f"**Sources**\n{src_lines}"})

        # GROUNDED path
        else:
            if use_llm and has_openai_key():
                res = llm_answer_cached(q, snapshot)
                if res.get("error"):
                    ans = f"LLM error: {res['error']}\n\n" + young_answer_rules(q, snapshot)
                else:
                    ans = res.get("text") or young_answer_rules(q, snapshot)
            else:
                ans = young_answer_rules(q, snapshot)

            st.session_state["young_chat"].append({"role": "assistant", "text": ans})

        st.rerun()

    with st.expander("🔎 Debug snapshot", expanded=False):
        st.json(snapshot)

    st.markdown(glass_close(), unsafe_allow_html=True)


# =============================================================================
# AI MODE (Young) — routing + guidance (no web by default)
# =============================================================================
def render_ai_mode_page():
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("🤖 AI Mode (Young)")
    st.caption("Routing + troubleshooting (lightweight). For web research, use Dashboard chat with `web:` + Internet mode.")

    tips = [
        "If dashboard shows 0 totals → check sessions + app_state.current_session_id + contributions.session_id.",
        "If reads fail → likely RLS. Use Health page to see what anon/service can read.",
        "If you want LLM mode → add OPENAI_API_KEY in Railway Variables.",
        "If you want web search → add TAVILY_API_KEY and turn Internet mode ON in Settings.",
    ]
    for t in tips:
        st.write("•", t)

    st.divider()
    c1, c2, c3 = st.columns(3)
    if c1.button("Go Dashboard", width=W_STRETCH):
        request_nav("Dashboard")
    if c2.button("Go Contributions", width=W_STRETCH):
        request_nav("Contributions")
    if c3.button("Go Health", width=W_STRETCH):
        request_nav("Health")

    st.markdown(glass_close(), unsafe_allow_html=True)

# =============================================================================
# Contributions
# =============================================================================
@st.cache_data(ttl=180, show_spinner=False)
def load_contributions_view(url: str, anon_key: str, schema: str, limit: int) -> pd.DataFrame:
    client = create_client(url, anon_key)
    try:
        rows = (
            client.schema(schema)
            .table("v_contributions_with_member")
            .select("id,member_id,member_name,session_id,amount,paid_at,note,created_at")
            .order("created_at", desc=True)
            .limit(int(limit))
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()

def render_contributions_page():
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Contributions")
    st.caption("Uses view `v_contributions_with_member` if available; otherwise raw table.")
    limit = int(st.session_state.get("_max_rows", 5000))

    df = pd.DataFrame()
    if table_readable(sb_anon, SUPABASE_SCHEMA, "v_contributions_with_member"):
        df = load_contributions_view(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA, limit=min(limit, 2000))

    if df.empty:
        rows = safe_select(sb_anon, "contributions", "*", schema=SUPABASE_SCHEMA, order_by="created_at", desc=True, limit=min(limit, 2000), show_error=False)
        df = pd.DataFrame(rows)
        if df.empty:
            st.info("No contributions found (or RLS blocked).")
        else:
            st.warning("Showing raw contributions (view not readable).")
            st.dataframe(df, width=W_STRETCH, hide_index=True)
    else:
        st.dataframe(df, width=W_STRETCH, hide_index=True)

    st.markdown(glass_close(), unsafe_allow_html=True)

# =============================================================================
# Minutes & Attendance
# =============================================================================
@st.cache_data(ttl=180, show_spinner=False)
def load_members_basic(url: str, anon_key: str, schema: str, limit: int) -> pd.DataFrame:
    client = create_client(url, anon_key)
    cols_try = [
        "id,display_name,full_name,name,phone",
        "id,display_name,name",
        "id,full_name,name",
        "id,name",
        "id,display_name",
    ]
    rows = []
    for cols in cols_try:
        try:
            rows = (
                client.schema(schema)
                .table("members")
                .select(cols)
                .order("id", desc=False)
                .limit(int(limit))
                .execute()
                .data
                or []
            )
            if rows:
                break
        except Exception:
            rows = []
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["id", "member_name"])
    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    df = df[df["id"] > 0].copy()
    for c in ["display_name", "full_name", "name"]:
        if c in df.columns:
            df[c] = df[c].astype(str).replace({"None": "", "nan": ""})
    def best(r):
        for k in ["display_name", "full_name", "name"]:
            v = str(r.get(k) or "").strip()
            if v:
                return v
        return ""
    df["member_name"] = df.apply(best, axis=1)
    return df[["id", "member_name"]].copy()

def render_minutes_attendance_page():
    st.subheader("📝 Minutes & ✅ Attendance")
    if not sb_service:
        st.warning("SERVICE key missing. Add SUPABASE_SERVICE_KEY to enable writing minutes/attendance.")
        st.stop()

    sb_read = sb_service
    session_id, note = get_current_session_id(sb_read, SUPABASE_SCHEMA)
    if session_id is None:
        st.error("No sessions found. Create a session in Admin first.")
        st.stop()
    if note != "from app_state":
        st.warning("app_state.current_session_id is not set. Using latest session fallback.")

    with st.sidebar.expander("🔐 Role (Minutes/Attendance)", expanded=False):
        role = st.selectbox("Role", ["admin", "treasury", "member"], index=0, key="ma_role")
    can_write = role in ("admin", "treasury")

    members_df = load_members_basic(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA, limit=5000)
    if members_df.empty:
        st.error("Members not readable (RLS/schema).")
        st.stop()

    tab1, tab2 = st.tabs(["Minutes", "Attendance"])

    with tab1:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.caption(f"Linked session_id: {session_id} • {note}")

        if can_write:
            with st.form("minutes_form"):
                title = st.text_input("Title")
                body = st.text_area("Minutes / Documentation", height=220)
                ok = st.form_submit_button("💾 Save minutes", width=W_STRETCH)
            if ok:
                if not title.strip() or not body.strip():
                    st.error("Title and body are required.")
                else:
                    existing = safe_select(sb_service, "minutes", "id,session_id", schema=SUPABASE_SCHEMA, limit=1, show_error=False, session_id=int(session_id))
                    if existing:
                        mid = int(existing[0]["id"])
                        if safe_update_eq(sb_service, SUPABASE_SCHEMA, "minutes", {"title": title.strip(), "body": body.strip(), "updated_at": now_iso(), "created_by": role}, "id", mid):
                            st.success("Minutes updated.")
                        else:
                            st.error("Update failed (check table columns/RLS).")
                    else:
                        if safe_insert(sb_service, SUPABASE_SCHEMA, "minutes", {"session_id": int(session_id), "title": title.strip(), "body": body.strip(), "created_by": role, "created_at": now_iso(), "updated_at": now_iso()}):
                            st.success("Minutes saved.")
                        else:
                            st.error("Insert failed (check table columns/RLS).")
                    st.cache_data.clear()
                    st.rerun()

        st.divider()
        rows = safe_select(sb_service, "minutes", "*", schema=SUPABASE_SCHEMA, order_by="updated_at", desc=True, limit=10, show_error=False, session_id=int(session_id))
        dfm = pd.DataFrame(rows)
        if dfm.empty:
            st.info("No minutes recorded yet.")
        else:
            st.dataframe(dfm, width=W_STRETCH, hide_index=True)

        st.markdown(glass_close(), unsafe_allow_html=True)

    with tab2:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.caption(f"Linked session_id: {session_id} • {note}")

        existing_rows = safe_select(sb_anon, "attendance", "member_id,present,note,created_at", schema=SUPABASE_SCHEMA, limit=5000, show_error=False, session_id=int(session_id))
        existing_map = {int(r["member_id"]): r for r in existing_rows if r.get("member_id") is not None}

        with st.form("attendance_form"):
            payload = []
            for _, r in members_df.sort_values("id").iterrows():
                mid = int(r["id"])
                name = str(r["member_name"])
                ex = existing_map.get(mid, {})
                ex_present = bool(ex.get("present")) if ex else True
                ex_note = str(ex.get("note") or "") if ex else ""

                c1, c2 = st.columns([0.42, 0.58])
                with c1:
                    status = st.radio(f"{mid:02d} • {name}", ["present", "absent"], index=0 if ex_present else 1, horizontal=True, key=f"att_{mid}")
                with c2:
                    note_txt = st.text_input("Reason / Note", value=ex_note, key=f"att_note_{mid}", label_visibility="collapsed")
                payload.append({"session_id": int(session_id), "member_id": mid, "present": (status == "present"), "note": note_txt.strip() or None, "created_at": now_iso()})

            save = st.form_submit_button("💾 Save attendance (ALL members)", width=W_STRETCH)

        if save:
            if not can_write:
                st.warning("Only admin/treasury can save attendance.")
            else:
                safe_delete_eq(sb_service, SUPABASE_SCHEMA, "attendance", "session_id", int(session_id))
                try:
                    throttle_db()
                    sb_service.schema(SUPABASE_SCHEMA).table("attendance").insert(payload).execute()
                    present = sum(1 for p in payload if p["present"])
                    st.success(f"Attendance saved ✅ Present: {present} • Absent: {len(payload)-present}")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error("Failed to save attendance.")
                    st.code(api_msg(e), language="text")

        st.divider()
        # Read view if exists
        if table_readable(sb_anon, SUPABASE_SCHEMA, "v_attendance_with_member"):
            try:
                throttle_db()
                rows = sb_anon.schema(SUPABASE_SCHEMA).table("v_attendance_with_member").select("*").eq("session_id", int(session_id)).order("member_id").limit(5000).execute().data or []
                dfa = pd.DataFrame(rows)
                if dfa.empty:
                    st.info("No attendance recorded for this session yet.")
                else:
                    st.dataframe(dfa, width=W_STRETCH, hide_index=True)
            except Exception:
                st.info("Attendance view not readable.")
        else:
            dfa = pd.DataFrame(existing_rows)
            if dfa.empty:
                st.info("No attendance recorded for this session yet.")
            else:
                st.dataframe(dfa, width=W_STRETCH, hide_index=True)

        st.markdown(glass_close(), unsafe_allow_html=True)

# =============================================================================
# Admin
# =============================================================================
def render_admin_page():
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Admin")
    st.caption("Basic sessions + app_state.current_session_id helper.")

    if not sb_service:
        st.warning("SERVICE key missing. Add SUPABASE_SERVICE_KEY to enable admin writes.")
        st.markdown(glass_close(), unsafe_allow_html=True)
        st.stop()

    sb_read = sb_service
    sess = safe_select(sb_read, "sessions", "*", schema=SUPABASE_SCHEMA, order_by="id", desc=True, limit=50, show_error=False)
    df = pd.DataFrame(sess)
    st.markdown("### Sessions (latest 50)")
    if df.empty:
        st.info("No sessions found.")
    else:
        st.dataframe(df, width=W_STRETCH, hide_index=True)

    st.divider()
    st.markdown("### Set current session (app_state.current_session_id)")
    current_sid, note = get_current_session_id(sb_read, SUPABASE_SCHEMA)
    st.caption(f"Current: {current_sid} • {note}")

    if not df.empty and "id" in df.columns:
        ids = [int(x) for x in pd.to_numeric(df["id"], errors="coerce").dropna().astype(int).tolist()]
        pick = st.selectbox("Pick session id", sorted(ids), index=0)
        if st.button("✅ Set as current session", width=W_STRETCH):
            st_rows = safe_select(sb_service, "app_state", "id,current_session_id", schema=SUPABASE_SCHEMA, limit=1, show_error=False)
            if st_rows:
                ok = safe_update_eq(sb_service, SUPABASE_SCHEMA, "app_state", {"current_session_id": int(pick), "updated_at": now_iso()}, "id", st_rows[0]["id"])
                st.success("Updated app_state." if ok else "Update failed.")
            else:
                ok = safe_insert(sb_service, SUPABASE_SCHEMA, "app_state", {"current_session_id": int(pick), "created_at": now_iso(), "updated_at": now_iso()})
                st.success("Created app_state." if ok else "Insert failed.")
            st.cache_data.clear()
            st.rerun()

    st.markdown(glass_close(), unsafe_allow_html=True)

# =============================================================================
# Audit
# =============================================================================
def render_audit_page():
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Audit")
    st.caption("Reads audit_log if available.")

    if table_readable(sb_anon, SUPABASE_SCHEMA, "audit_log"):
        rows = safe_select(sb_anon, "audit_log", "*", schema=SUPABASE_SCHEMA, order_by="created_at", desc=True, limit=min(int(st.session_state.get("_max_rows", 5000)), 500), show_error=False)
        df = pd.DataFrame(rows)
        if df.empty:
            st.info("audit_log readable but has no rows.")
        else:
            st.dataframe(df, width=W_STRETCH, hide_index=True)
    else:
        st.warning(f"{SUPABASE_SCHEMA}.audit_log not readable (missing or RLS).")

    st.markdown(glass_close(), unsafe_allow_html=True)

# =============================================================================
# Health
# =============================================================================
def render_health_page():
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Health")
    st.caption("Quick readability check for key tables/views (anon vs service).")

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
    st.markdown(glass_close(), unsafe_allow_html=True)

# =============================================================================
# ROUTER
# =============================================================================
if page == "Dashboard":
    render_dashboard_page()
elif page == "🤖 AI Mode":
    render_ai_mode_page()
elif page == "Contributions":
    render_contributions_page()
elif page == "Minutes & Attendance":
    render_minutes_attendance_page()
elif page == "Admin":
    render_admin_page()
elif page == "Audit":
    render_audit_page()
elif page == "Health":
    render_health_page()
else:
    st.info("Select a page from the sidebar menu.")
