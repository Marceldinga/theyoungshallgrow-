
# njangi_llm_panel.py
# ==============================================================================
# 👩🏾‍💼 YOUNG — NJANGI “DASHBOARD COPILOT” (SMART • GROUNDED • MODERN • HUMAN-LIKE)
# ------------------------------------------------------------------------------
# ✅ Single-file module (drop-in)
# ✅ NJANGI STANDARD tables (NO legacy)
# ✅ Safe for Railway / Streamlit Cloud
# ✅ Accepts: sb_anon / sb_service / schema (matches your app.py)
#
# ✅ REAL TIMEZONE + greeting of the day (via Internet time API):
#   - Uses worldtimeapi.org to get timezone + current local time (cached)
#   - Falls back safely to server time if internet fails
#
# ✅ Internet answers (Tavily):
#   - You can enable “Internet mode” (answers from web with sources)
#   - Privacy guard: Njangi/member finance questions NEVER go to the web unless you force “Allow Njangi web”
#
# ✅ ML training (XGBoost; no sklearn required) + model risk leaderboard
# ------------------------------------------------------------------------------
# ENV (Railway Variables or Streamlit secrets):
#   TAVILY_API_KEY = your Tavily key
# ------------------------------------------------------------------------------
# DEPENDENCIES:
#   streamlit, pandas, numpy, xgboost(optional)
# ==============================================================================

from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# ==============================================================================
# Constants
# ==============================================================================
W_STRETCH = "stretch"

TTL_UI = 15
TTL_WEB = 15 * 60
TTL_TIME = 10 * 60  # timezone/time refresh cache
DEFAULT_MAX_ROWS = 5000

TAVILY_ENDPOINT = "https://api.tavily.com/search"
WORLDTIME_API = "https://worldtimeapi.org/api/ip"

# ==============================================================================
# Helpers (safe + formatting)
# ==============================================================================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(s: str) -> str:
    return " ".join(str(s or "").lower().strip().split())


def _money(x: float) -> str:
    try:
        return f"${float(x):,.0f}"
    except Exception:
        return "$0"


def _safe_sum(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())


def _safe_count(df: pd.DataFrame) -> int:
    return int(len(df)) if df is not None and not df.empty else 0


def _to_numeric_cols(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


def _parse_dt(s) -> pd.Timestamp | None:
    try:
        if s is None or str(s).strip() == "":
            return None
        dt = pd.to_datetime(s, errors="coerce", utc=True)
        if pd.isna(dt):
            return None
        return dt
    except Exception:
        return None


def _days_since(ts: pd.Timestamp | None) -> float:
    if ts is None:
        return float("nan")
    now = pd.Timestamp.now(tz="UTC")
    return float((now - ts).total_seconds() / 86400.0)


def _bce_loss(y_true, y_prob, eps: float = 1e-9) -> float:
    n = len(y_true)
    if n == 0:
        return float("nan")
    s = 0.0
    for yt, yp in zip(y_true, y_prob):
        yp = max(eps, min(1.0 - eps, float(yp)))
        s += -(yt * math.log(yp) + (1 - yt) * math.log(1 - yp))
    return s / n


def _norm_status(x) -> str:
    s = str(x or "").strip().lower()
    if s in ("active", "open", "running", "current"):
        return "active"
    if s in ("closed", "paid", "completed", "settled", "done"):
        return "closed"
    if s in ("overdue", "late", "default", "delinquent"):
        return "overdue"
    return s or "unknown"


def _looks_like_key(s: str) -> bool:
    s = str(s or "").strip()
    return len(s) >= 12 and " " not in s


def _env_or_secret(key: str, default: str = "") -> str:
    v = os.getenv(key)
    if v not in (None, ""):
        return str(v).strip()
    try:
        return str(st.secrets.get(key, default)).strip()
    except Exception:
        return str(default).strip()


# ==============================================================================
# REAL TIMEZONE + Greeting (Internet)
# ==============================================================================
@st.cache_data(ttl=TTL_TIME, show_spinner=False)
def _worldtime_ip_cached() -> dict:
    """
    Uses internet to get user's IP-based timezone and local time.
    Cached to avoid hammering free APIs.
    """
    try:
        req = urllib.request.Request(WORLDTIME_API, headers={"User-Agent": "njangi-young/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        return json.loads(body) if body else {}
    except Exception as e:
        return {"_error": repr(e)}


def _get_real_local_time() -> Tuple[str, datetime]:
    """
    Returns (timezone_name, local_datetime).
    If internet fails, falls back to server local time.
    """
    data = _worldtime_ip_cached()
    if isinstance(data, dict) and not data.get("_error"):
        tzname = str(data.get("timezone") or "").strip() or "Unknown"
        dt_str = str(data.get("datetime") or "").strip()
        try:
            # worldtimeapi gives ISO8601 with offset
            dt = pd.to_datetime(dt_str, utc=True, errors="coerce")
            if pd.isna(dt):
                raise ValueError("bad datetime")
            # Convert to the API-provided timezone for display
            # (pandas can localize by name in many environments; if not, keep offset)
            try:
                dt_local = dt.tz_convert(tzname).to_pydatetime()
            except Exception:
                dt_local = dt.to_pydatetime()
            return tzname, dt_local
        except Exception:
            pass

    # fallback
    return "ServerTime", datetime.now()


def _greeting_of_day() -> Tuple[str, str]:
    """
    Returns (greeting, tzname) using real timezone (internet).
    """
    tzname, dt_local = _get_real_local_time()
    h = int(dt_local.hour)
    if h < 12:
        greet = "Good morning"
    elif h < 18:
        greet = "Good afternoon"
    else:
        greet = "Good evening"
    return greet, tzname


def _tiny_human_touch(h: int) -> str:
    if 5 <= h <= 9:
        return "Hope your day starts strong."
    if 10 <= h <= 13:
        return "Hope your day is going well."
    if 14 <= h <= 17:
        return "Hope your afternoon is going smoothly."
    if 18 <= h <= 22:
        return "Hope your evening is peaceful."
    return "Hope everything is okay on your side."


# ==============================================================================
# Safe Supabase reads (no cache decorators w/ client)
# ==============================================================================
def _try_read(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 2000,
    order_by: str | None = None,
    desc: bool = True,
) -> List[Dict]:
    if sb is None:
        return []
    q = sb.schema(schema).table(table).select(cols)
    if order_by:
        q = q.order(order_by, desc=desc)
    if limit:
        q = q.limit(int(limit))
    try:
        return (q.execute().data or [])
    except Exception:
        # fallback: try '*'
        try:
            q2 = sb.schema(schema).table(table).select("*").limit(int(limit))
            if order_by:
                q2 = q2.order(order_by, desc=desc)
            return (q2.execute().data or [])
        except Exception:
            return []


# ==============================================================================
# Persona text (human-like greeting + smart instructions)
# ==============================================================================
def _young_intro() -> str:
    tzname, dt_local = _get_real_local_time()
    h = int(dt_local.hour)
    greet, _tz = _greeting_of_day()
    touch = _tiny_human_touch(h)
    return (
        f"{greet} 👋🏾 I’m **Young** — your **Njangi Dashboard Copilot** for **theyoungshallgrow**.\n\n"
        f"_{touch}_\n\n"
        f"🕒 Timezone: **{tzname}** • Local time: **{dt_local.strftime('%Y-%m-%d %H:%M')}**\n\n"
        "I’m grounded on your **Supabase data** (members, sessions, contributions, loans, payments, interest, fines, "
        "attendance, minutes, payouts).\n\n"
        "**Ask me anything like:**\n"
        "• Loans summary / Active loans / Overdue loans\n"
        "• Contribution summary / Who hasn’t paid this session?\n"
        "• Foundation total / Interest collected this month\n"
        "• Fines summary / Attendance vs fines\n"
        "• Risk for Donald / Top 5 risky members\n"
        "• Show member Marcel loans / loan 12 status\n\n"
        "🌐 Internet: turn on **Internet mode** in the sidebar (I’ll answer with sources).\n"
        "🔒 Privacy: by default I won’t web-search your Njangi/member finance questions."
    )


def _welcome_card(schema: str):
    tzname, dt_local = _get_real_local_time()
    greet, _ = _greeting_of_day()
    st.markdown(
        f"""
        <div style="padding:14px;border-radius:16px;border:1px solid rgba(255,255,255,.12);
                    background:rgba(255,255,255,.04);">
          <div style="font-size:18px;font-weight:750;">👩🏾‍💼 Young is online</div>
          <div style="opacity:.92;margin-top:6px;">
            {greet} — connected to your Njangi snapshots and ready.
          </div>
          <div style="opacity:.70;margin-top:6px;font-size:12px;">
            timezone: <code>{tzname}</code> • local: <code>{dt_local.strftime('%Y-%m-%d %H:%M')}</code><br/>
            schema: <code>{schema}</code> • utc: <code>{_now_iso()}</code>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ==============================================================================
# Data Hub (loads “all data” snapshots Young can use)
# - uses session_state cache so supabase client is never hashed
# ==============================================================================
TABLE_SPECS = [
    ("members", "id,name,display_name,phone,created_at", "id", []),
    ("sessions", "id,session_id,session_date,cycle_index,title,start_date,end_date,created_at", "created_at", []),
    ("contributions", "id,member_id,session_id,amount,paid_at,created_at", "created_at", ["amount"]),
    ("foundation_contributions", "id,member_id,session_id,amount,paid_at,created_at", "created_at", ["amount"]),
    (
        "loans",
        "id,member_id,principal,principal_current,total_due,unpaid_interest,last_paid_at,status,borrow_date,due_cycle_days,interest_rate_monthly,created_at",
        "created_at",
        ["principal", "principal_current", "total_due", "unpaid_interest", "due_cycle_days", "interest_rate_monthly"],
    ),
    ("loan_payments", "id,loan_id,member_id,amount,paid_at,created_at", "created_at", ["amount"]),
    ("interest_ledger", "id,loan_id,member_id,amount,posted_at,created_at", "created_at", ["amount"]),
    ("fines", "*", "created_at", ["amount"]),
    ("attendance", "*", "created_at", []),
    ("minutes", "*", "created_at", []),
    ("payouts", "*", "created_at", []),   # optional
    ("app_state", "*", "created_at", []),
    ("signatures", "*", "created_at", []),
]


def _hub_key(schema: str, table: str) -> str:
    return f"younghub::{schema}::{table}"


def _hub_clear(schema: str):
    cache = st.session_state.get("young_hub_cache", {})
    for table, *_ in TABLE_SPECS:
        cache.pop(_hub_key(schema, table), None)
    st.session_state["young_hub_cache"] = cache


def _hub_load(sb_read, schema: str, slow_mode: bool, limit: int) -> Dict[str, pd.DataFrame]:
    if "young_hub_cache" not in st.session_state:
        st.session_state["young_hub_cache"] = {}

    cache: Dict[str, pd.DataFrame] = st.session_state["young_hub_cache"]
    out: Dict[str, pd.DataFrame] = {}

    for (table, cols, order_by, num_cols) in TABLE_SPECS:
        key = _hub_key(schema, table)
        if key in cache:
            out[table] = cache[key]
            continue

        if slow_mode:
            time.sleep(0.08)

        rows = _try_read(sb_read, schema, table, cols=cols, limit=limit, order_by=order_by, desc=True)
        df = pd.DataFrame(rows or [])

        if not df.empty:
            if table == "loans":
                df["status_norm"] = df.get("status", "").apply(_norm_status)
            df = _to_numeric_cols(df, num_cols)

        cache[key] = df
        out[table] = df

    st.session_state["young_hub_cache"] = cache
    return out


# ==============================================================================
# Slots: member / loan_id / session_id
# ==============================================================================
def _build_member_labels(members_df: pd.DataFrame) -> pd.DataFrame:
    if members_df is None or members_df.empty:
        return pd.DataFrame()
    m = members_df.copy()
    if "display_name" in m.columns:
        m["member_name"] = m["display_name"].fillna("").astype(str)
        m.loc[m["member_name"].str.strip() == "", "member_name"] = m.get("name", "").astype(str)
    else:
        m["member_name"] = m.get("name", "").astype(str)
    m["member_name_norm"] = m["member_name"].fillna("").astype(str).map(_normalize_text)
    return m


def _pick_member_from_question(question: str, members_df: pd.DataFrame) -> Tuple[int | None, str | None]:
    if members_df is None or members_df.empty:
        return None, None
    q = _normalize_text(question)
    if not q:
        return None, None

    m = _build_member_labels(members_df)
    if m.empty or "id" not in m.columns:
        return None, None

    candidates = []
    for _, r in m.iterrows():
        try:
            mid = int(r["id"])
        except Exception:
            continue
        name = str(r.get("member_name", "")).strip()
        name_norm = str(r.get("member_name_norm", "")).strip()
        if not name_norm:
            continue
        candidates.append((len(name_norm), mid, name, name_norm))
    candidates.sort(reverse=True, key=lambda t: t[0])

    for _, mid, name, name_norm in candidates:
        if name_norm in q:
            return mid, name
        toks = [t for t in name_norm.split() if len(t) >= 3]
        if toks and all(t in q for t in toks):
            return mid, name

    return None, None


def _pick_int_from_text(question: str, label: str) -> int | None:
    q = _normalize_text(question)
    m = re.search(rf"{re.escape(label)}\s*[:=#]?\s*(\d+)", q)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def _who_label(member_id: int | None, member_name: str | None) -> str:
    if member_id is not None and member_name:
        return f"**{member_name}**"
    if member_id is not None:
        return f"**member_id={member_id}**"
    return "**All members**"


# ==============================================================================
# Intent detection (smart)
# ==============================================================================
def _detect_intent(question: str) -> str:
    q = _normalize_text(question)

    if any(k in q for k in ["introduce", "who are you", "your name"]):
        return "intro"
    if any(k in q for k in ["help", "what can you do", "commands", "examples"]):
        return "help"

    if any(k in q for k in ["who hasn't paid", "who hasnt paid", "not paid", "missing contributors", "missing payments"]):
        return "missing_contrib"
    if any(k in q for k in ["overdue", "late", "delinquent", "default"]):
        return "overdue"
    if any(k in q for k in ["risk", "score", "risky"]):
        return "risk"

    if any(k in q for k in ["loan", "borrow", "principal", "balance", "total due"]):
        return "loans"
    if any(k in q for k in ["payment", "repay", "loan payment"]):
        return "loan_payments"
    if any(k in q for k in ["interest", "interest ledger", "interest collected"]):
        return "interest"

    if any(k in q for k in ["contribution", "contrib", "deposit"]):
        return "contributions"
    if "foundation" in q:
        return "foundation"
    if any(k in q for k in ["fine", "penalty"]):
        return "fines"
    if any(k in q for k in ["attendance", "present", "absent"]):
        return "attendance"
    if any(k in q for k in ["minutes", "meeting"]):
        return "minutes"
    if any(k in q for k in ["payout", "rotation", "next payout", "who is next"]):
        return "payouts"
    if any(k in q for k in ["session", "cycle"]):
        return "sessions"

    if any(k in q for k in ["total", "sum", "count", "how many", "top", "list", "show", "latest", "recent"]):
        return "generic"

    return "unknown"


# ==============================================================================
# Generic dataframe QA (fallback)
# ==============================================================================
def _df_best_date_col(df: pd.DataFrame) -> str | None:
    for c in ["paid_at", "posted_at", "session_date", "borrow_date", "last_paid_at", "created_at", "updated_at", "date"]:
        if c in df.columns:
            return c
    return None


def _df_top_n(df: pd.DataFrame, col: str, n: int = 10, asc: bool = False) -> pd.DataFrame:
    if df is None or df.empty or col not in df.columns:
        return pd.DataFrame()
    s = pd.to_numeric(df[col], errors="coerce")
    tmp = df.copy()
    tmp["_sort"] = s
    tmp = tmp.dropna(subset=["_sort"])
    tmp = tmp.sort_values("_sort", ascending=asc).drop(columns=["_sort"])
    return tmp.head(n)


def _maybe_filter_member(df: pd.DataFrame, member_id: int | None) -> pd.DataFrame:
    if df is None or df.empty or member_id is None:
        return df
    for c in ["member_id", "member"]:
        if c in df.columns:
            return df[df[c].astype(str) == str(member_id)].copy()
    return df


def _answer_generic(question: str, hub: Dict[str, pd.DataFrame], member_id: int | None, member_name: str | None) -> Tuple[str, pd.DataFrame | None]:
    q = _normalize_text(question)
    who = _who_label(member_id, member_name)

    if "loan" in q:
        table = "loans"
    elif "contrib" in q:
        table = "contributions"
    elif "foundation" in q:
        table = "foundation_contributions"
    elif "fine" in q:
        table = "fines"
    elif "attendance" in q:
        table = "attendance"
    elif "minutes" in q:
        table = "minutes"
    elif "interest" in q:
        table = "interest_ledger"
    elif "payment" in q:
        table = "loan_payments"
    elif "session" in q or "cycle" in q:
        table = "sessions"
    else:
        table = "loans"

    df = _maybe_filter_member(hub.get(table, pd.DataFrame()), member_id)
    if df is None or df.empty:
        return (f"I don’t see data in `{table}` for {who}. If this table is empty or blocked by RLS, I’ll still answer other questions.", None)

    if any(k in q for k in ["total", "sum"]):
        amt_col = None
        for c in ["amount", "principal_current", "principal", "total_due", "unpaid_interest"]:
            if c in df.columns:
                amt_col = c
                break
        if amt_col:
            total = _safe_sum(df, amt_col)
            return (f"For {who} in `{table}`: total **{amt_col}** = **{_money(total)}** (rows={len(df):,}).", None)

    if any(k in q for k in ["count", "how many", "rows"]):
        return (f"For {who} in `{table}`: I see **{len(df):,}** rows.", None)

    if "top" in q or "highest" in q:
        n = 5
        m = re.search(r"top\s+(\d+)", q)
        if m:
            try:
                n = max(1, min(50, int(m.group(1))))
            except Exception:
                n = 5
        candidate_cols = [c for c in ["amount", "principal_current", "unpaid_interest", "total_due"] if c in df.columns]
        if not candidate_cols:
            return (f"I can list top items, but `{table}` has no numeric columns like amount/principal/balance.", None)
        col = candidate_cols[0]
        top_df = _df_top_n(df, col, n=n, asc=False)
        return (f"Top **{n}** rows in `{table}` for {who} by **{col}**:", top_df)

    if any(k in q for k in ["latest", "recent", "last"]):
        dcol = _df_best_date_col(df)
        if not dcol:
            return (f"I can’t find a date column in `{table}` to sort by recent.", None)
        tmp = df.copy()
        tmp["_dt"] = pd.to_datetime(tmp[dcol], errors="coerce", utc=True)
        tmp = tmp.dropna(subset=["_dt"]).sort_values("_dt", ascending=False).drop(columns=["_dt"])
        return (f"Most recent rows in `{table}` for {who} (sorted by `{dcol}`):", tmp.head(15))

    if any(k in q for k in ["list", "show"]):
        return (f"Here are rows from `{table}` for {who}:", df.head(25))

    cols_preview = ", ".join(list(df.columns)[:12]) + (" ..." if len(df.columns) > 12 else "")
    return (f"Tell me what you want: **total / count / top / latest / list**. `{table}` columns: {cols_preview}", None)


# ==============================================================================
# Internet Search (Tavily)
# ==============================================================================
@st.cache_data(ttl=TTL_WEB, show_spinner=False)
def _tavily_search_cached(query: str, api_key: str, max_results: int = 5) -> dict:
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": int(max_results),
        "include_answer": True,
        "include_raw_content": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        TAVILY_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            err_body = ""
        return {"_error": f"HTTPError {getattr(e,'code','')}: {err_body or str(e)}"}
    except Exception as e:
        return {"_error": repr(e)}


def _format_web_result(res: dict) -> Tuple[str, List[dict]]:
    if not isinstance(res, dict):
        return ("I couldn’t read the web results.", [])
    if res.get("_error"):
        return (f"Internet search failed: {res.get('_error')}", [])
    answer = str(res.get("answer") or "").strip()
    results = res.get("results") or []
    sources = []
    for it in results[:6]:
        if not isinstance(it, dict):
            continue
        sources.append(
            {
                "title": str(it.get("title") or "").strip(),
                "url": str(it.get("url") or "").strip(),
                "content": str(it.get("content") or "").strip(),
                "score": it.get("score"),
            }
        )
    if not answer:
        answer = "Here’s what I found online (see sources)."
    return (answer, sources)


def _is_njangi_sensitive(q: str) -> bool:
    """
    Privacy guard: if question looks like Njangi finance/member data, don't send to web by default.
    """
    qn = _normalize_text(q)
    keywords = [
        "njangi", "theyoungshallgrow", "member", "members", "loan", "loans",
        "contribution", "contributions", "foundation", "fine", "fines",
        "attendance", "minutes", "payout", "interest", "ledger", "supabase",
        "session", "cycle", "member_id", "principal", "balance", "paid",
    ]
    return any(k in qn for k in keywords)


# ==============================================================================
# Grounded answers (smart routes)
# ==============================================================================
def _answer_grounded(
    question: str,
    hub: Dict[str, pd.DataFrame],
    selected_member_id: int | None,
    selected_member_name: int | None,
    loan_filter: str,
) -> Tuple[str, pd.DataFrame | None, List[dict] | None]:
    qraw = question.strip()
    if not qraw:
        return ("Please type a question.", None, None)

    intent = _detect_intent(qraw)

    members_df = hub.get("members", pd.DataFrame())
    sessions_df = hub.get("sessions", pd.DataFrame())
    contrib_df = hub.get("contributions", pd.DataFrame())
    foundation_df = hub.get("foundation_contributions", pd.DataFrame())
    loans_df = hub.get("loans", pd.DataFrame())
    pay_df = hub.get("loan_payments", pd.DataFrame())
    interest_df = hub.get("interest_ledger", pd.DataFrame())
    fines_df = hub.get("fines", pd.DataFrame())
    att_df = hub.get("attendance", pd.DataFrame())
    minutes_df = hub.get("minutes", pd.DataFrame())
    payouts_df = hub.get("payouts", pd.DataFrame())
    app_state_df = hub.get("app_state", pd.DataFrame())

    # Slots (member from question overrides UI selection)
    q_member_id, q_member_name = _pick_member_from_question(qraw, members_df)
    member_id = q_member_id if q_member_id is not None else selected_member_id
    member_name = q_member_name if q_member_name is not None else selected_member_name
    who = _who_label(member_id, member_name if isinstance(member_name, str) else None)

    loan_id = _pick_int_from_text(qraw, "loan")
    session_id = _pick_int_from_text(qraw, "session")

    # loan filter override by question
    qn = _normalize_text(qraw)
    status_filter = loan_filter
    if "active" in qn:
        status_filter = "Active"
    elif "closed" in qn or "paid" in qn or "completed" in qn:
        status_filter = "Closed"
    elif "all" in qn:
        status_filter = "All"

    if intent == "intro":
        return (_young_intro(), None, None)

    if intent == "help":
        return (
            "Examples you can ask:\n"
            "• **Loans summary** / **Active loans** / **Overdue loans**\n"
            "• **Who hasn’t paid this session?**\n"
            "• **Foundation total** / **Interest collected this month**\n"
            "• **Fines summary** / **Attendance vs fines**\n"
            "• **Risk for Donald** / **Top 5 risky members**\n"
            "• **Show member Marcel loans** / **loan 12 status**\n",
            None,
            None,
        )

    # Member filtered frames
    mc = _maybe_filter_member(contrib_df, member_id)
    mf = _maybe_filter_member(fines_df, member_id)
    mfd = _maybe_filter_member(foundation_df, member_id)
    ml_all = _maybe_filter_member(loans_df, member_id)
    mpay = _maybe_filter_member(pay_df, member_id)
    mint = _maybe_filter_member(interest_df, member_id)

    # loan by id
    if loan_id is not None and loans_df is not None and not loans_df.empty and "id" in loans_df.columns:
        one = loans_df[loans_df["id"].astype(str) == str(loan_id)].copy()
        if one.empty:
            return (f"I couldn’t find **loan {loan_id}** in your loans table.", None, None)
        return (f"Here is **loan {loan_id}**:", one.head(1), None)

    # session summary
    if session_id is not None:
        c_sess = contrib_df.copy() if contrib_df is not None else pd.DataFrame()
        f_sess = foundation_df.copy() if foundation_df is not None else pd.DataFrame()
        if not c_sess.empty and "session_id" in c_sess.columns:
            c_sess = c_sess[c_sess["session_id"].astype(str) == str(session_id)]
        if not f_sess.empty and "session_id" in f_sess.columns:
            f_sess = f_sess[f_sess["session_id"].astype(str) == str(session_id)]

        contrib_total = _safe_sum(c_sess, "amount")
        foundation_total = _safe_sum(f_sess, "amount")

        title = ""
        if sessions_df is not None and not sessions_df.empty:
            sid_col = "session_id" if "session_id" in sessions_df.columns else "id"
            srow = sessions_df[sessions_df[sid_col].astype(str) == str(session_id)]
            if not srow.empty:
                title = str(srow.iloc[0].get("title") or srow.iloc[0].get("session_date") or "").strip()

        # missing contributors list
        missing_note = ""
        missing_df = pd.DataFrame()
        if members_df is not None and not members_df.empty and "id" in members_df.columns and not c_sess.empty and "member_id" in c_sess.columns:
            paid_ids = set(c_sess["member_id"].astype(str).tolist())
            m = _build_member_labels(members_df)
            all_ids = m["id"].astype(str).tolist()
            miss_ids = [i for i in all_ids if i not in paid_ids]
            if miss_ids:
                miss = m[m["id"].astype(str).isin(miss_ids)][["id", "member_name"]].copy()
                missing_df = miss.rename(columns={"id": "member_id"})
                missing_note = f"Missing contributors: **{len(miss_ids)}**"

        msg = (
            f"Session **{session_id}** summary"
            + (f" — {title}" if title else "")
            + ":\n"
            f"• Contributions total: **{_money(contrib_total)}** (rows={_safe_count(c_sess):,})\n"
            f"• Foundation total: **{_money(foundation_total)}** (rows={_safe_count(f_sess):,})\n"
            + (f"• {missing_note}\n" if missing_note else "")
        )
        return (msg, (missing_df.head(25) if not missing_df.empty else None), None)

    # Missing contributions (current session from app_state if possible)
    if intent == "missing_contrib":
        current_session = None
        if app_state_df is not None and not app_state_df.empty:
            row0 = app_state_df.iloc[0].to_dict()
            cs = row0.get("current_session_id")
            try:
                current_session = int(cs) if cs is not None and str(cs).strip() != "" else None
            except Exception:
                current_session = None

        if current_session is None:
            return (
                "I can do this best if **app_state.current_session_id** exists.\n"
                "Try: `session 5 missing contributors` (replace 5 with your session).",
                None,
                None,
            )

        if members_df is None or members_df.empty or contrib_df is None or contrib_df.empty:
            return ("I need both **members** and **contributions** data to compute missing contributors.", None, None)

        c = contrib_df.copy()
        if "session_id" in c.columns:
            c = c[c["session_id"].astype(str) == str(current_session)]
        if c.empty or "member_id" not in c.columns:
            return (f"I don’t see contributions recorded for session **{current_session}** yet.", None, None)

        paid_ids = set(c["member_id"].astype(str).tolist())
        m = _build_member_labels(members_df)
        miss = m[~m["id"].astype(str).isin(paid_ids)][["id", "member_name"]].copy()
        miss = miss.rename(columns={"id": "member_id"})
        return (
            f"Session **{current_session}** missing contributors: **{len(miss):,}**",
            miss.head(60) if not miss.empty else None,
            None,
        )

    if intent == "contributions":
        total = _safe_sum(mc, "amount")
        return (
            f"Contribution summary for {who}:\n"
            f"• Total contributed: **{_money(total)}**\n"
            f"• Rows: **{_safe_count(mc):,}**\n"
            "Reminder: contributions should be **multiples of 500** (your Njangi rule).",
            None,
            None,
        )

    if intent == "foundation":
        total = _safe_sum(mfd, "amount")
        return (
            f"Foundation summary for {who}:\n"
            f"• Total foundation contributed: **{_money(total)}**\n"
            f"• Rows: **{_safe_count(mfd):,}**",
            None,
            None,
        )

    if intent == "loans":
        ml = ml_all.copy() if ml_all is not None else pd.DataFrame()
        if ml is not None and not ml.empty:
            if "status_norm" not in ml.columns:
                ml["status_norm"] = ml.get("status", "").apply(_norm_status)
            if status_filter == "Active":
                ml = ml[ml["status_norm"] == "active"].copy()
            elif status_filter == "Closed":
                ml = ml[ml["status_norm"] == "closed"].copy()

        principal = _safe_sum(ml, "principal")
        bal = _safe_sum(ml, "principal_current")
        unpaid = _safe_sum(ml, "unpaid_interest")
        due = _safe_sum(ml, "total_due")

        note = ""
        if ml is not None and not ml.empty and "last_paid_at" in ml.columns:
            lp = pd.to_datetime(ml["last_paid_at"], errors="coerce", utc=True)
            if lp.notna().any():
                days = float((pd.Timestamp.now(tz="UTC") - lp.max()).total_seconds() / 86400.0)
                if days > 45:
                    note = f"⚠️ Last payment looks old (~{days:.0f} days). Consider follow-up this session."

        return (
            f"Loans summary for {who} (filter: **{status_filter}**):\n"
            f"• Loans: **{_safe_count(ml):,}**\n"
            f"• Principal: **{_money(principal)}**\n"
            f"• Balance (principal_current): **{_money(bal)}**\n"
            f"• Unpaid interest: **{_money(unpaid)}**\n"
            f"• Total due: **{_money(due)}**\n"
            + (f"\n{note}" if note else ""),
            None,
            None,
        )

    if intent == "overdue":
        if ml_all is None or ml_all.empty:
            return (f"I can’t see loans for {who}.", None, None)

        tmp = ml_all.copy()
        if "status_norm" not in tmp.columns:
            tmp["status_norm"] = tmp.get("status", "").apply(_norm_status)

        od = tmp[tmp["status_norm"].isin(["overdue", "late", "default", "delinquent"])].copy()

        if od.empty and "last_paid_at" in tmp.columns:
            t2 = tmp[tmp["status_norm"] == "active"].copy()
            lp = pd.to_datetime(t2["last_paid_at"], errors="coerce", utc=True)
            t2["_days"] = (pd.Timestamp.now(tz="UTC") - lp).dt.total_seconds() / 86400.0
            od = t2[t2["_days"] > 30].drop(columns=["_days"], errors="ignore")

        if od.empty:
            return (f"I don’t see overdue signals for {who} right now.", None, None)

        show_cols = [c for c in ["id", "member_id", "principal_current", "unpaid_interest", "last_paid_at", "status"] if c in od.columns]
        return (f"Overdue / late loans for {who}:", (od[show_cols].head(30) if show_cols else od.head(30)), None)

    if intent == "loan_payments":
        if mpay is None or mpay.empty:
            return (f"I don’t see loan payment rows for {who}.", None, None)
        return (f"Most recent loan payments for {who}:", mpay.head(25), None)

    if intent == "interest":
        if mint is None or mint.empty:
            return (f"I don’t see interest ledger rows for {who}.", None, None)
        total = _safe_sum(mint, "amount")
        return (
            f"Interest summary for {who}:\n"
            f"• Total interest recorded: **{_money(total)}**\n"
            f"• Rows: **{len(mint):,}**\n"
            "Tip: interest_ledger is best as the single source of truth for interest reporting.",
            mint.head(25),
            None,
        )

    if intent == "fines":
        if mf is None or mf.empty:
            return (f"I don’t see fines for {who}.", None, None)
        total = _safe_sum(mf, "amount") if "amount" in mf.columns else float(len(mf))
        return (
            f"Fines summary for {who}:\n"
            f"• Fine records: **{_safe_count(mf):,}**\n"
            f"• Total fines: **{_money(total)}**",
            None,
            None,
        )

    if intent == "attendance":
        if att_df is None or att_df.empty:
            return ("I don’t see attendance records yet.", None, None)
        return ("Here are recent attendance rows:", att_df.head(25), None)

    if intent == "minutes":
        if minutes_df is None or minutes_df.empty:
            return ("I don’t see minutes saved yet.", None, None)
        return ("Here are recent minutes rows:", minutes_df.head(25), None)

    if intent == "payouts":
        if payouts_df is not None and not payouts_df.empty:
            return ("Here are recent payouts rows:", payouts_df.head(25), None)
        if app_state_df is not None and not app_state_df.empty:
            return ("I don’t see `payouts` rows, but here is `app_state` (look for rotation fields):", app_state_df.head(50), None)
        return ("I can’t see payouts/app_state data yet.", None, None)

    if intent == "risk":
        if ("top" in qn and "risky" in qn) or ("top" in qn and "risk" in qn):
            loans = hub.get("loans", pd.DataFrame())
            if loans is None or loans.empty or "member_id" not in loans.columns:
                return ("Not enough loans data to compute top risks.", None, None)
            tmp = loans.copy()
            tmp["status_norm"] = tmp.get("status", "").apply(_norm_status)
            tmp = _to_numeric_cols(tmp, ["principal_current", "unpaid_interest"])
            tmp["risk_h"] = 0.0
            tmp.loc[tmp["unpaid_interest"] > 0, "risk_h"] += 0.35
            tmp.loc[tmp["principal_current"] > 0, "risk_h"] += 0.25
            tmp.loc[tmp["status_norm"].isin(["overdue", "late", "default", "delinquent"]), "risk_h"] += 0.45
            bym = tmp.groupby("member_id", dropna=False)["risk_h"].max().reset_index().sort_values("risk_h", ascending=False)
            n = 5
            m = re.search(r"top\s+(\d+)", qn)
            if m:
                try:
                    n = max(1, min(25, int(m.group(1))))
                except Exception:
                    n = 5
            bym = bym.head(n)
            mdf = hub.get("members", pd.DataFrame())
            if mdf is not None and not mdf.empty and "id" in mdf.columns:
                names = _build_member_labels(mdf)[["id", "member_name"]].rename(columns={"id": "member_id"})
                bym["member_id"] = pd.to_numeric(bym["member_id"], errors="coerce")
                names["member_id"] = pd.to_numeric(names["member_id"], errors="coerce")
                bym = bym.merge(names, on="member_id", how="left")
            return (f"Top **{n}** risky members (heuristic from loans snapshot):", bym, None)

        total_contrib = _safe_sum(mc, "amount")
        bal = _safe_sum(ml_all, "principal_current")
        unpaid = _safe_sum(ml_all, "unpaid_interest")
        fines_total = _safe_sum(mf, "amount") if (mf is not None and not mf.empty and "amount" in mf.columns) else 0.0

        score = 0
        if unpaid > 0:
            score += 35
        if bal > 0 and total_contrib == 0:
            score += 25
        if ml_all is not None and not ml_all.empty:
            tmp = ml_all.copy()
            tmp["status_norm"] = tmp.get("status", "").apply(_norm_status)
            if tmp["status_norm"].astype(str).str.contains("overdue|default|delinquent|late", case=False, na=False).any():
                score += 45
            if "last_paid_at" in tmp.columns:
                lp = pd.to_datetime(tmp["last_paid_at"], errors="coerce", utc=True)
                if lp.notna().any():
                    age = float((pd.Timestamp.now(tz="UTC") - lp.max()).total_seconds() / 86400.0)
                    if age > 45:
                        score += 10
        if fines_total > 0:
            score += 10
        score = min(100, score)

        return (
            f"Risk view for {who} (from current DB snapshot):\n"
            f"• Balance: **{_money(bal)}** • Unpaid interest: **{_money(unpaid)}** • Fines: **{_money(fines_total)}**\n"
            f"• Quick risk score (heuristic): **{score}/100**\n\n"
            "If you want model-based risk, run **Training (XGBoost)** below.",
            None,
            None,
        )

    if intent == "sessions":
        if sessions_df is None or sessions_df.empty:
            return ("I don’t see sessions data yet.", None, None)
        return ("Recent sessions:", sessions_df.head(20), None)

    if intent == "generic":
        txt, df_show = _answer_generic(qraw, hub, member_id, member_name if isinstance(member_name, str) else None)
        return (txt, df_show, None)

    return (
        "Tell me what you want and I’ll do it.\n\n"
        "Examples:\n"
        "• `loans summary` • `active loans` • `overdue loans`\n"
        "• `who hasn’t paid this session?`\n"
        "• `interest collected` • `foundation total`\n"
        "• `risk for Donald` • `top 5 risky members`\n"
        "• `loan 12 status`\n\n"
        "Or type **help**.",
        None,
        None,
    )


# ==============================================================================
# ML: training dataset + XGBoost (no sklearn)
# ==============================================================================
def _build_training_frame(hub: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    loans_df = hub.get("loans", pd.DataFrame())
    members_df = hub.get("members", pd.DataFrame())
    contrib_df = hub.get("contributions", pd.DataFrame())
    fines_df = hub.get("fines", pd.DataFrame())

    if loans_df is None or loans_df.empty:
        return pd.DataFrame()

    df = loans_df.copy()
    if "status_norm" not in df.columns:
        df["status_norm"] = df.get("status", "").apply(_norm_status)

    df = df[df["status_norm"].isin(["active", "closed"])].copy()
    if df.empty:
        return pd.DataFrame()

    df["y"] = (df["status_norm"] == "active").astype(int)

    df["last_paid_dt"] = df.get("last_paid_at", None).apply(_parse_dt) if "last_paid_at" in df.columns else None
    df["created_dt"] = df.get("created_at", None).apply(_parse_dt) if "created_at" in df.columns else None

    def _ds(row):
        d = row.get("last_paid_dt")
        if d is None:
            d = row.get("created_dt")
        return _days_since(d)

    df["days_since_last_paid"] = df.apply(_ds, axis=1)
    df["days_since_last_paid"] = pd.to_numeric(df["days_since_last_paid"], errors="coerce")
    med = float(df["days_since_last_paid"].median()) if df["days_since_last_paid"].notna().any() else 0.0
    df["days_since_last_paid"] = df["days_since_last_paid"].fillna(med)

    df = _to_numeric_cols(df, ["principal", "principal_current", "total_due", "unpaid_interest"])

    if contrib_df is not None and not contrib_df.empty and "member_id" in contrib_df.columns:
        c = _to_numeric_cols(contrib_df.copy(), ["amount"])
        contrib_tot = c.groupby("member_id", dropna=False)["amount"].sum().reset_index().rename(columns={"amount": "member_contrib_total"})
    else:
        contrib_tot = pd.DataFrame(columns=["member_id", "member_contrib_total"])

    if fines_df is not None and not fines_df.empty and "member_id" in fines_df.columns:
        f = fines_df.copy()
        if "amount" in f.columns:
            f = _to_numeric_cols(f, ["amount"])
            fines_tot = f.groupby("member_id", dropna=False)["amount"].sum().reset_index().rename(columns={"amount": "member_fines_total"})
        else:
            fines_tot = f.groupby("member_id", dropna=False).size().reset_index(name="member_fines_total")
    else:
        fines_tot = pd.DataFrame(columns=["member_id", "member_fines_total"])

    loan_counts = df.groupby("member_id", dropna=False).size().reset_index(name="member_loan_count")

    df = df.merge(contrib_tot, on="member_id", how="left")
    df = df.merge(fines_tot, on="member_id", how="left")
    df = df.merge(loan_counts, on="member_id", how="left")

    for c in ["member_contrib_total", "member_fines_total", "member_loan_count"]:
        df[c] = pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0)

    if members_df is not None and not members_df.empty and "id" in members_df.columns:
        m = _build_member_labels(members_df).rename(columns={"id": "member_id"})[["member_id", "member_name"]].copy()
        df["member_id"] = pd.to_numeric(df["member_id"], errors="coerce")
        m["member_id"] = pd.to_numeric(m["member_id"], errors="coerce")
        df = df.merge(m, on="member_id", how="left")

    keep = [
        "id", "member_id", "member_name", "y",
        "principal", "principal_current", "total_due", "unpaid_interest",
        "days_since_last_paid", "member_contrib_total", "member_fines_total", "member_loan_count",
    ]
    keep = [c for c in keep if c in df.columns]
    df = df[keep].copy()

    for c in [
        "principal", "principal_current", "total_due", "unpaid_interest",
        "days_since_last_paid", "member_contrib_total", "member_fines_total", "member_loan_count"
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    return df


def _train_xgboost(df: pd.DataFrame, seed: int = 42, test_size: float = 0.25):
    try:
        import numpy as np
        import xgboost as xgb
    except Exception as e:
        return None, {"error": f"XGBoost not installed: {repr(e)}"}, [], df

    if df is None or df.empty:
        return None, {"error": "No training data."}, [], df
    if "y" not in df.columns:
        return None, {"error": "Missing label column 'y'."}, [], df

    y_all = df["y"].astype(int).values
    classes, counts = np.unique(y_all, return_counts=True)
    class_counts = {int(c): int(n) for c, n in zip(classes, counts)}
    if len(classes) < 2:
        return None, {"error": "Need both active(1) and closed(0) loans to train.", "class_counts": class_counts}, [], df

    feature_cols = [c for c in [
        "principal", "principal_current", "total_due", "unpaid_interest",
        "days_since_last_paid", "member_contrib_total", "member_fines_total", "member_loan_count",
    ] if c in df.columns]
    if not feature_cols:
        return None, {"error": "No feature columns found."}, [], df

    X_all = df[feature_cols].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0).values

    model = xgb.XGBClassifier(
        n_estimators=280,
        max_depth=4,
        learning_rate=0.07,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=1.0,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=int(seed),
        n_jobs=1,
    )

    rng = np.random.default_rng(int(seed))

    if len(y_all) < 8 or min(counts) < 2:
        model.fit(X_all, y_all)
        out = df.copy()
        out["p_active"] = model.predict_proba(X_all)[:, 1]
        metrics = {
            "n_rows": int(len(df)),
            "n_train": int(len(df)),
            "n_test": 0,
            "accuracy_test": float("nan"),
            "logloss_test": float("nan"),
            "pos_rate": float(df["y"].mean()),
            "class_counts": class_counts,
            "note": "Trained on ALL rows (dataset too small to split). Add more CLOSED loans for validation.",
        }
        return model, metrics, feature_cols, out

    idx0 = np.where(y_all == 0)[0]
    idx1 = np.where(y_all == 1)[0]
    rng.shuffle(idx0)
    rng.shuffle(idx1)

    def _n_test(n: int) -> int:
        k = int(round(n * float(test_size)))
        k = max(1, k)
        k = min(n - 1, k)
        return k

    n0_test = _n_test(len(idx0))
    n1_test = _n_test(len(idx1))

    test_idx = np.concatenate([idx0[:n0_test], idx1[:n1_test]])
    train_idx = np.concatenate([idx0[n0_test:], idx1[n1_test:]])
    rng.shuffle(test_idx)
    rng.shuffle(train_idx)

    X_train, y_train = X_all[train_idx], y_all[train_idx]
    X_test, y_test = X_all[test_idx], y_all[test_idx]

    if len(np.unique(y_train)) < 2:
        model.fit(X_all, y_all)
        out = df.copy()
        out["p_active"] = model.predict_proba(X_all)[:, 1]
        metrics = {
            "n_rows": int(len(df)),
            "n_train": int(len(df)),
            "n_test": 0,
            "accuracy_test": float("nan"),
            "logloss_test": float("nan"),
            "pos_rate": float(df["y"].mean()),
            "class_counts": class_counts,
            "note": "Fallback: split produced one-class training. Trained on ALL rows.",
        }
        return model, metrics, feature_cols, out

    model.fit(X_train, y_train)

    p_test = model.predict_proba(X_test)[:, 1]
    yhat = (p_test >= 0.5).astype(int)
    acc = float((yhat == y_test).mean()) if len(y_test) else float("nan")
    loss = _bce_loss(list(map(int, y_test.tolist())), list(map(float, p_test.tolist())))

    out = df.copy()
    out["p_active"] = model.predict_proba(X_all)[:, 1]
    metrics = {
        "n_rows": int(len(df)),
        "n_train": int(len(train_idx)),
        "n_test": int(len(test_idx)),
        "accuracy_test": acc,
        "logloss_test": loss,
        "pos_rate": float(df["y"].mean()),
        "class_counts": class_counts,
        "note": "Manual stratified split (no sklearn).",
    }
    return model, metrics, feature_cols, out


# ==============================================================================
# UI — Young Copilot Panel
# ==============================================================================
def render_njangi_llm_panel(sb_anon=None, sb_service=None, schema: str = "public"):
    st.title("👩🏾‍💼 Young — Njangi Dashboard Copilot")
    st.caption("Smart grounded Q&A over Njangi data + REAL timezone greeting + Internet mode + optional XGBoost training.")

    sb_read = sb_service if sb_service is not None else sb_anon

    with st.sidebar:
        st.subheader("⚙️ Settings")
        slow_mode = st.checkbox("🐢 Slow Mode (reduce DB pressure)", value=True)
        max_rows = st.slider("Max rows per table", 500, 10000, DEFAULT_MAX_ROWS, 500)

        st.markdown("---")
        st.subheader("🕒 Timezone (real)")
        if st.button("♻️ Refresh timezone/time", width=W_STRETCH):
            try:
                _worldtime_ip_cached.clear()  # type: ignore[attr-defined]
            except Exception:
                pass
            st.rerun()
        tzname, dt_local = _get_real_local_time()
        st.write(f"Timezone: **{tzname}**")
        st.caption(f"Local time: {dt_local.strftime('%Y-%m-%d %H:%M')}")

        tavily_key = _env_or_secret("TAVILY_API_KEY", "")
        web_ready = _looks_like_key(tavily_key)

        st.markdown("---")
        st.subheader("🌐 Internet")
        st.write(f"Tavily key detected: **{'YES' if web_ready else 'NO'}**")
        internet_mode = st.checkbox("✅ Internet mode (answer from web when needed)", value=True)
        allow_njangi_web = st.checkbox("⚠️ Allow Njangi web (NOT recommended)", value=False)
        max_sources = st.slider("Web sources", 2, 8, 5, 1)

        st.markdown("---")
        if st.button("🔄 Refresh snapshots", width=W_STRETCH):
            _hub_clear(schema)
            st.success("Snapshots cleared. Reloading now…")
            st.rerun()

    _welcome_card(schema=schema)

    # Load snapshots
    hub = _hub_load(sb_read=sb_read, schema=schema, slow_mode=slow_mode, limit=int(max_rows))

    members_df = hub.get("members", pd.DataFrame())
    sessions_df = hub.get("sessions", pd.DataFrame())
    contrib_df = hub.get("contributions", pd.DataFrame())
    loans_df = hub.get("loans", pd.DataFrame())
    payments_df = hub.get("loan_payments", pd.DataFrame())
    interest_df = hub.get("interest_ledger", pd.DataFrame())
    fines_df = hub.get("fines", pd.DataFrame())

    # KPIs
    st.markdown("### 📊 Live snapshot health")
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Members", f"{_safe_count(members_df):,}")
    k2.metric("Sessions", f"{_safe_count(sessions_df):,}")
    k3.metric("Contrib rows", f"{_safe_count(contrib_df):,}")
    k4.metric("Loans rows", f"{_safe_count(loans_df):,}")
    k5.metric("Fines rows", f"{_safe_count(fines_df):,}")

    st.markdown("---")

    # Member selection
    selected_member_id = None
    selected_member_name: Optional[str] = None
    if members_df is not None and not members_df.empty and "id" in members_df.columns:
        m = _build_member_labels(members_df)
        m["label"] = m.apply(lambda r: f"{int(r['id']):02d} • {str(r.get('member_name') or '').strip()}", axis=1)
        pick = st.selectbox("Select member (optional)", ["(All members)"] + m["label"].tolist())
        if pick != "(All members)":
            row = m[m["label"] == pick].iloc[0]
            selected_member_id = int(row["id"])
            selected_member_name = str(row.get("member_name") or "").strip()
    else:
        st.info("Members table not available or empty. Young will still answer general questions.")

    loan_filter = st.radio("Loans filter", ["All", "Active", "Closed"], horizontal=True)

    # Smart insights
    st.markdown("### ✨ Smart insights")
    a, b, c = st.columns(3)

    with a:
        st.caption("Overdue / late signals")
        if loans_df is None or loans_df.empty:
            st.write("No loans data.")
        else:
            tmp = loans_df.copy()
            tmp["status_norm"] = tmp.get("status", "").apply(_norm_status)
            od = tmp[tmp["status_norm"].isin(["overdue", "late", "default", "delinquent"])].copy()
            if od.empty and "last_paid_at" in tmp.columns:
                t2 = tmp[tmp["status_norm"] == "active"].copy()
                lp = pd.to_datetime(t2["last_paid_at"], errors="coerce", utc=True)
                t2["_days"] = (pd.Timestamp.now(tz="UTC") - lp).dt.total_seconds() / 86400.0
                od = t2[t2["_days"] > 30].drop(columns=["_days"], errors="ignore")
            st.write(f"Count: **{len(od):,}**")
            if not od.empty:
                cols = [x for x in ["id", "member_id", "principal_current", "unpaid_interest", "last_paid_at", "status"] if x in od.columns]
                st.dataframe(od[cols].head(7) if cols else od.head(7), width=W_STRETCH, hide_index=True)

    with b:
        st.caption("Totals (all members)")
        st.write(f"Contributions: **{_money(_safe_sum(contrib_df, 'amount'))}**")
        st.write(f"Payments: **{_money(_safe_sum(payments_df, 'amount'))}**")
        st.write(f"Interest ledger: **{_money(_safe_sum(interest_df, 'amount'))}**")
        st.write(f"Fines: **{_money(_safe_sum(fines_df, 'amount'))}**")

    with c:
        st.caption("Top risk (heuristic)")
        if loans_df is None or loans_df.empty or "member_id" not in loans_df.columns:
            st.write("Not enough loans data.")
        else:
            tmp = loans_df.copy()
            tmp["status_norm"] = tmp.get("status", "").apply(_norm_status)
            tmp = _to_numeric_cols(tmp, ["principal_current", "unpaid_interest"])
            tmp["risk_h"] = 0.0
            tmp.loc[tmp["unpaid_interest"] > 0, "risk_h"] += 0.35
            tmp.loc[tmp["principal_current"] > 0, "risk_h"] += 0.25
            tmp.loc[tmp["status_norm"].isin(["overdue", "late", "default", "delinquent"]), "risk_h"] += 0.45
            bym = tmp.groupby("member_id", dropna=False)["risk_h"].max().reset_index().sort_values("risk_h", ascending=False).head(7)
            if members_df is not None and not members_df.empty and "id" in members_df.columns:
                names = _build_member_labels(members_df)[["id", "member_name"]].rename(columns={"id": "member_id"})
                bym["member_id"] = pd.to_numeric(bym["member_id"], errors="coerce")
                names["member_id"] = pd.to_numeric(names["member_id"], errors="coerce")
                bym = bym.merge(names, on="member_id", how="left")
            st.dataframe(bym, width=W_STRETCH, hide_index=True)

    st.markdown("---")

    # Training
    st.markdown("### 🧪 Training (XGBoost)")
    st.caption("Label: active=1, closed=0 (trained on loan rows). No sklearn required.")
    with st.expander("Training settings", expanded=False):
        seed = st.number_input("Random seed", 0, 999999, 42, 1)
        test_size = st.slider("Test size", 0.10, 0.50, 0.25, 0.05)
        run_train = st.button("🚀 Train model now", width=W_STRETCH)

    if run_train:
        train_df = _build_training_frame(hub)
        if train_df.empty:
            st.error("No training data found. Need loans with statuses that normalize to 'active' and 'closed'.")
        else:
            model, metrics, feature_cols, pred_df = _train_xgboost(train_df, seed=int(seed), test_size=float(test_size))
            if model is None:
                st.error("Training failed.")
                st.code(metrics.get("error", "Unknown error"), language="text")
                if "class_counts" in metrics:
                    st.caption(f"class_counts: {metrics['class_counts']}")
            else:
                st.success("Training complete ✅")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Rows", f"{metrics['n_rows']:,}")
                m2.metric("Pos rate (active)", f"{metrics['pos_rate']:.2f}")
                m3.metric("Test accuracy", "—" if str(metrics["accuracy_test"]) == "nan" else f"{metrics['accuracy_test']:.2f}")
                m4.metric("Test logloss", "—" if str(metrics["logloss_test"]) == "nan" else f"{metrics['logloss_test']:.3f}")
                st.caption(metrics.get("note", ""))

                st.caption("Features used:")
                st.code(", ".join(feature_cols), language="text")

                if pred_df is not None and not pred_df.empty and "member_id" in pred_df.columns and "p_active" in pred_df.columns:
                    tmp = pred_df.copy()
                    tmp["member_id"] = pd.to_numeric(tmp["member_id"], errors="coerce")
                    tmp["risk_score"] = 1.0 - pd.to_numeric(tmp["p_active"], errors="coerce").fillna(0.5)
                    bym = (
                        tmp.groupby(["member_id", "member_name"], dropna=False)["risk_score"]
                        .max()
                        .sort_values(ascending=False)
                        .reset_index()
                        .rename(columns={"risk_score": "risk_max"})
                    )
                    st.markdown("#### 🔥 Top risk (model) — max risk among loans (1 - p_active)")
                    st.dataframe(bym.head(15), width=W_STRETCH, hide_index=True)

                st.markdown("#### 🔎 Sample predictions (loan rows)")
                show_cols = [c for c in ["id", "member_id", "member_name", "y", "p_active", "principal_current", "unpaid_interest", "days_since_last_paid"] if pred_df is not None and c in pred_df.columns]
                if pred_df is not None and show_cols:
                    st.dataframe(pred_df[show_cols].head(25), width=W_STRETCH, hide_index=True)

    st.markdown("---")

    # Chat
    st.markdown("### 💬 Chat with Young")

    if "young_chat" not in st.session_state:
        st.session_state["young_chat"] = [{"role": "assistant", "content": _young_intro(), "sources": []}]

    bar1, bar2, bar3 = st.columns([1, 1, 2])
    with bar1:
        if st.button("🧹 Clear chat", width=W_STRETCH):
            st.session_state["young_chat"] = [{"role": "assistant", "content": _young_intro(), "sources": []}]
            st.rerun()
    with bar2:
        if st.button("👋 Greeting", width=W_STRETCH):
            st.session_state["young_chat"].append({"role": "assistant", "content": _young_intro(), "sources": []})
            st.rerun()
    with bar3:
        st.caption("Internet mode: ON means I can answer general questions from the web (with sources).")

    for msg in st.session_state["young_chat"][-25:]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sources"):
                st.markdown("**Sources**")
                for i, s in enumerate(msg["sources"][:6], start=1):
                    title = s.get("title") or f"Source {i}"
                    url = s.get("url") or ""
                    snippet = (s.get("content") or "").strip()
                    snippet = snippet[:240] + ("…" if len(snippet) > 240 else "")
                    if url:
                        st.write(f"{i}. [{title}]({url})")
                    else:
                        st.write(f"{i}. {title}")
                    if snippet:
                        st.caption(snippet)

    user_text = st.chat_input("Ask Young… (Njangi questions stay grounded. Internet mode answers general questions with sources.)")
    if user_text:
        st.session_state["young_chat"].append({"role": "user", "content": user_text, "sources": []})

        # 1) Always try GROUNDED first
        answer, df_show, _ = _answer_grounded(
            question=user_text,
            hub=hub,
            selected_member_id=selected_member_id,
            selected_member_name=selected_member_name,
            loan_filter=loan_filter,
        )

        # 2) If unknown/generic and Internet mode is ON, do web search
        do_web = False
        qn = _normalize_text(user_text)
        if internet_mode and web_ready:
            # if question is Njangi-sensitive, only web-search if user allowed it
            if _is_njangi_sensitive(user_text) and not allow_njangi_web:
                do_web = False
            else:
                # Use web if grounded answer looks like "tell me what you want" or "examples"
                if any(k in _normalize_text(answer) for k in ["tell me what you want", "examples:", "type help"]):
                    do_web = True

        if do_web:
            res = _tavily_search_cached(query=user_text, api_key=tavily_key, max_results=int(max_sources))
            web_answer, sources = _format_web_result(res)
            st.session_state["young_chat"].append({"role": "assistant", "content": web_answer, "sources": sources})
            st.rerun()

        # Grounded answer
        st.session_state["young_chat"].append({"role": "assistant", "content": answer, "sources": []})

        # Show dataframe (if any) right after the answer
        if df_show is not None and isinstance(df_show, pd.DataFrame) and not df_show.empty:
            st.session_state["young_chat"].append({"role": "assistant", "content": "Here’s the table I used:", "sources": []})
            with st.chat_message("assistant"):
                st.dataframe(df_show, width=W_STRETCH, hide_index=True)

        st.rerun()


__all__ = ["render_njangi_llm_panel"]
