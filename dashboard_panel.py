# dashboard_panel.py ✅ COMPLETE — Dashboard + 🤖 Young AI View + 🌐 Web Search (Tavily)
# ------------------------------------------------------------------------------
# - NJANGI STANDARD (NO legacy)
# - Works with app.py calling: render_dashboard(sb_anon=..., sb_service=..., schema=...)
# - Adds: "🤖 Young — Dashboard AI Helper" INSIDE dashboard
# - Young answers from LIVE snapshot (no guessing)
# - Adds Web Search: use "web:" prefix (example: web: Maryland cosmetology license requirements)
# - Web search uses TAVILY_API_KEY from environment (Railway Variables)
# ------------------------------------------------------------------------------

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
from postgrest.exceptions import APIError

# ============================================================
# TIME + GREETING
# ============================================================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _greeting_of_day() -> str:
    h = datetime.now().hour
    if h < 12:
        return "Good morning"
    if h < 18:
        return "Good afternoon"
    return "Good evening"


def _human_touch() -> str:
    h = datetime.now().hour
    if 5 <= h <= 9:
        return "Hope your day starts strong."
    if 10 <= h <= 13:
        return "Hope your day is going well."
    if 14 <= h <= 17:
        return "Hope your afternoon is going smoothly."
    if 18 <= h <= 22:
        return "Hope your evening is peaceful."
    return "Hope everything is okay on your side."


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


# ============================================================
# LIGHT THROTTLE (optional, uses app.py session_state if present)
# ============================================================
def _throttle_db():
    slow = bool(st.session_state.get("_slow_mode_override", True))
    min_wait = float(st.session_state.get("MIN_SECONDS_BETWEEN_DB_CALLS_UI", 0.15))
    if not slow:
        return
    last = float(st.session_state.get("_last_db_call_ts", 0.0))
    now = time.time()
    wait = min_wait - (now - last)
    if wait > 0:
        time.sleep(wait)
    st.session_state["_last_db_call_ts"] = time.time()


def _safe_select(
    client,
    schema: str,
    table: str,
    cols: str = "*",
    limit: Optional[int] = None,
    order_by: Optional[str] = None,
    desc: bool = False,
    show_error: bool = False,
    **filters,
) -> List[Dict[str, Any]]:
    if client is None:
        return []
    try:
        _throttle_db()
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
            st.code(_api_msg(e), language="text")
        return []


def _table_readable(client, schema: str, table: str) -> bool:
    if client is None:
        return False
    try:
        _throttle_db()
        client.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def _sum_amount(rows: List[Dict[str, Any]], col: str = "amount") -> float:
    if not rows:
        return 0.0
    s = 0.0
    for r in rows:
        try:
            s += float(r.get(col) or 0)
        except Exception:
            pass
    return float(s)


def _count_distinct(rows: List[Dict[str, Any]], key: str) -> int:
    if not rows:
        return 0
    s = set()
    for r in rows:
        if r.get(key) is not None:
            s.add(str(r.get(key)))
    return len(s)


# ============================================================
# WEB SEARCH (Tavily)
# ============================================================
def _has_tavily_key() -> bool:
    return bool(os.getenv("TAVILY_API_KEY", "").strip())


def _strip_web_prefix(q: str) -> str:
    return re.sub(r"^(web:|internet:|tavily:)\s*", "", (q or "").strip(), flags=re.IGNORECASE).strip()


@st.cache_data(ttl=3600, show_spinner=False)
def _tavily_search_cached(query: str, max_results: int = 5, search_depth: str = "basic") -> Dict[str, Any]:
    """
    Tavily search. Requires env var: TAVILY_API_KEY
    """
    import requests

    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return {"error": "Missing TAVILY_API_KEY in environment variables."}

    url = "https://api.tavily.com/search"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {"query": query, "max_results": int(max_results), "search_depth": str(search_depth)}

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code != 200:
            return {"error": f"Tavily error {r.status_code}: {r.text[:400]}"}
        j = r.json()
        return j if isinstance(j, dict) else {"raw": r.text}
    except Exception as e:
        return {"error": f"Request failed: {repr(e)}"}


def _format_web_results(tav: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]]]:
    if not isinstance(tav, dict):
        return ("I couldn’t read the web results.", [])
    if "error" in tav:
        return (f"Internet search failed: {tav['error']}", [])

    results = tav.get("results", []) or []
    if not results:
        return ("I searched the web but didn’t find clear results. Try rephrasing.", [])

    bullets = []
    sources: List[Dict[str, str]] = []
    for r in results[:5]:
        title = str(r.get("title") or "Source").strip()
        url = str(r.get("url") or "").strip()
        content = str(r.get("content") or "").strip()
        if content:
            bullets.append(f"• {content[:240].rstrip()}…")
        if url:
            sources.append({"title": title, "url": url})

    summary = "Here’s what I found online (top results):\n" + ("\n".join(bullets[:3]) if bullets else "• (No snippets returned)")
    return (summary, sources)


def _is_web_query(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.startswith("web:") or t.startswith("internet:") or t.startswith("tavily:")


# ============================================================
# DASHBOARD AI (Young) — grounded on snapshot only
# ============================================================
def _young_answer(q: str, snap: Dict[str, Any]) -> str:
    t = (q or "").strip().lower()

    session_id = snap.get("session_id")
    total_members = snap.get("total_members", 0)
    pot = snap.get("current_pot", 0.0)
    cycle_total = snap.get("cycle_contributions_total", 0.0)
    members_paid = snap.get("members_paid", 0)
    attendance_present = snap.get("attendance_present", 0)
    attendance_total = snap.get("attendance_total", 0)
    loans_active_total = snap.get("loans_active_total", 0.0)
    loans_active_count = snap.get("loans_active_count", 0)
    fines_total = snap.get("fines_total", 0.0)
    repayments_total = snap.get("repayments_total", 0.0)
    interest_total = snap.get("interest_total", 0.0)

    def fmt_money(x: float) -> str:
        try:
            return f"{float(x):,.0f}"
        except Exception:
            return str(x)

    if not t:
        return "Ask me: **pot this session**, **loans summary**, **fines total**, or **attendance summary**."

    if "session" in t and ("id" in t or "which" in t):
        return f"Current **Session ID** is **{session_id}**."

    if "pot" in t:
        return (
            f"**Pot (Session {session_id})** = **{fmt_money(pot)}**.\n\n"
            f"Members paid: **{members_paid}/{total_members}** • Cycle contributions total: **{fmt_money(cycle_total)}**."
        )

    if "contribution" in t or "cycle" in t:
        return (
            f"**Cycle contributions (Session {session_id})** total = **{fmt_money(cycle_total)}**.\n\n"
            f"Members paid: **{members_paid}/{total_members}**."
        )

    if "attendance" in t or "present" in t or "absent" in t:
        if attendance_total == 0:
            return f"No attendance records yet for **Session {session_id}**."
        absent = max(int(attendance_total) - int(attendance_present), 0)
        return (
            f"**Attendance (Session {session_id})**\n"
            f"Present: **{attendance_present}** • Absent: **{absent}** • Total marked: **{attendance_total}**."
        )

    if "loan" in t:
        if loans_active_count == 0:
            return "I see **no active loans** in the current snapshot."
        return (
            f"**Loans summary**\n"
            f"Active loans: **{loans_active_count}** • Active principal total: **{fmt_money(loans_active_total)}**.\n\n"
            "If you want *who owes what*, open **Loans** or **🤖 AI Risk Panel**."
        )

    if "fine" in t:
        return f"**Fines total** (snapshot) = **{fmt_money(fines_total)}**."

    if "repay" in t or "payment" in t:
        return f"**Repayments total** (snapshot) = **{fmt_money(repayments_total)}**."

    if "interest" in t:
        return f"**Interest total** (snapshot) = **{fmt_money(interest_total)}**."

    if "status" in t or "live" in t:
        return "Status: **LIVE** (reading from Supabase)."

    return (
        "I can answer from the dashboard snapshot:\n"
        "• **pot this session**\n"
        "• **cycle contributions**\n"
        "• **loans summary**\n"
        "• **fines total** / **repayments total** / **interest total**\n"
        "• **attendance summary**\n\n"
        "For internet help, start with **web:** (example: `web: Maryland cosmetology license requirements`)."
    )


def _render_young_ai_view(snapshot: Dict[str, Any]):
    st.markdown("### 🤖 Young — Dashboard AI Helper")
    st.caption("Grounded on your LIVE dashboard snapshot. Internet only when you type **web:**")

    st.write(f"{_greeting_of_day()} 👋🏾 I’m **Young** — your dashboard copilot for **theyoungshallgrow**.")
    st.caption(_human_touch())

    st.write(
        "Try:\n"
        "• *pot this session*\n"
        "• *loans summary*\n"
        "• *fines total*\n"
        "• *repayments total*\n"
        "• *interest total*\n"
        "• *attendance summary*\n\n"
        "Internet (only if you force it):\n"
        "• `web: Maryland cosmetology license requirements`"
    )

    q = st.text_input(
        "Ask Young…",
        placeholder="e.g., 'pot this session' OR 'web: Maryland cosmetology license requirements'",
        key="young_dash_q",
    )

    cols = st.columns([0.22, 0.78])
    with cols[0]:
        ask = st.button("Ask", key="young_dash_ask", width="stretch")
    with cols[1]:
        if ask:
            # Route: WEB vs GROUNDED
            if _is_web_query(q):
                if not _has_tavily_key():
                    st.session_state["young_dash_a"] = (
                        "Web search is not configured yet.\n\n"
                        "Add **TAVILY_API_KEY** in Railway → Variables, then redeploy."
                    )
                else:
                    query = _strip_web_prefix(q)
                    tav = _tavily_search_cached(query=query, max_results=5, search_depth="basic")
                    summary, sources = _format_web_results(tav)
                    st.session_state["young_dash_a"] = summary
                    st.session_state["young_dash_sources"] = sources
            else:
                st.session_state["young_dash_a"] = _young_answer(q, snapshot)
                st.session_state["young_dash_sources"] = []

    a = st.session_state.get("young_dash_a")
    if a:
        st.markdown(a)

    sources = st.session_state.get("young_dash_sources") or []
    if sources:
        st.markdown("**Sources:**")
        for s in sources[:5]:
            title = s.get("title") or "Source"
            url = s.get("url") or ""
            if url:
                st.markdown(f"- [{title}]({url})")
            else:
                st.markdown(f"- {title}")

    with st.expander("🔎 Debug snapshot", expanded=False):
        st.json(snapshot)


# ============================================================
# MAIN DASHBOARD
# ============================================================
def render_dashboard(sb_anon, sb_service=None, schema: str = "public"):
    st.markdown("Modern Njangi analytics + AI helper • no legacy • no SQL")
    st.success("Status: LIVE")

    # Prefer service for reads if available (helps with RLS)
    sb_read = sb_service if sb_service is not None else sb_anon

    # --------------------------------------------------------
    # Current session
    # --------------------------------------------------------
    state = _safe_select(sb_read, schema, "app_state", "*", limit=1, show_error=False, id=1)
    state = state[0] if state else (_safe_select(sb_read, schema, "app_state", "*", limit=1, show_error=False) or [{}])[0]

    raw_sid = state.get("current_session_id")
    session_id = None
    try:
        session_id = int(raw_sid) if raw_sid is not None and str(raw_sid).strip() else None
    except Exception:
        session_id = None

    if session_id is None:
        s = _safe_select(sb_read, schema, "sessions", "id,session_id,created_at", order_by="session_id", desc=True, limit=1, show_error=False)
        if s:
            try:
                session_id = int(s[0].get("session_id") or s[0].get("id"))
            except Exception:
                session_id = None

    # --------------------------------------------------------
    # Members
    # --------------------------------------------------------
    members_rows = _safe_select(sb_read, schema, "members", "id", limit=5000, show_error=False)
    total_members = len(members_rows) if members_rows else 0

    # --------------------------------------------------------
    # Contributions (session)
    # --------------------------------------------------------
    contrib_rows = []
    if session_id is not None:
        contrib_rows = _safe_select(
            sb_read,
            schema,
            "contributions",
            "id,member_id,amount,session_id,created_at",
            session_id=int(session_id),
            limit=10000,
            show_error=False,
        )

    cycle_total = _sum_amount(contrib_rows, "amount")
    members_paid = _count_distinct(contrib_rows, "member_id")
    current_pot = float(cycle_total)

    # --------------------------------------------------------
    # Attendance (session)
    # --------------------------------------------------------
    attendance_rows = []
    if session_id is not None and _table_readable(sb_read, schema, "attendance"):
        attendance_rows = _safe_select(
            sb_read,
            schema,
            "attendance",
            "member_id,present,session_id,created_at",
            session_id=int(session_id),
            limit=5000,
            show_error=False,
        )
    attendance_total = len(attendance_rows) if attendance_rows else 0
    attendance_present = 0
    for r in attendance_rows:
        try:
            if bool(r.get("present")):
                attendance_present += 1
        except Exception:
            pass

    # --------------------------------------------------------
    # Loans (active)
    # --------------------------------------------------------
    loans_rows = []
    if _table_readable(sb_read, schema, "loans"):
        loans_rows = _safe_select(
            sb_read,
            schema,
            "loans",
            "id,member_id,status,principal,principal_current,total_due,created_at",
            limit=20000,
            show_error=False,
        )

    active_status = {"active", "overdue", "late", "open"}
    loans_active = []
    for r in loans_rows:
        stt = str(r.get("status") or "").strip().lower()
        if stt in active_status:
            loans_active.append(r)

    loans_active_count = len(loans_active)
    loans_active_total = 0.0
    for r in loans_active:
        for k in ("principal_current", "principal", "total_due"):
            if k in r and r.get(k) is not None:
                try:
                    loans_active_total += float(r.get(k) or 0)
                    break
                except Exception:
                    pass

    # --------------------------------------------------------
    # Fines
    # --------------------------------------------------------
    fines_total = 0.0
    if _table_readable(sb_read, schema, "fines"):
        fines_rows = _safe_select(sb_read, schema, "fines", "amount,created_at", limit=20000, show_error=False)
        fines_total = _sum_amount(fines_rows, "amount")

    # --------------------------------------------------------
    # Repayments + Interest ledger
    # --------------------------------------------------------
    repayments_total = 0.0
    if _table_readable(sb_read, schema, "loan_payments"):
        pay_rows = _safe_select(sb_read, schema, "loan_payments", "amount,created_at", limit=20000, show_error=False)
        repayments_total = _sum_amount(pay_rows, "amount")

    interest_total = 0.0
    if _table_readable(sb_read, schema, "interest_ledger"):
        i_rows = _safe_select(sb_read, schema, "interest_ledger", "amount,created_at", limit=20000, show_error=False)
        interest_total = _sum_amount(i_rows, "amount")

    # --------------------------------------------------------
    # KPI display
    # --------------------------------------------------------
    c1, c2, c3, c4, c5 = st.columns([0.9, 0.9, 0.9, 0.9, 1.2])
    with c1:
        st.metric("Session ID", session_id if session_id is not None else "—")
    with c2:
        st.metric("Total Members", f"{total_members:,}")
    with c3:
        st.metric("Current Pot", f"{current_pot:,.0f}")
    with c4:
        st.metric("Cycle Contributions", f"{cycle_total:,.0f}")
    with c5:
        st.metric("Members Paid", f"{members_paid}/{total_members}")

    st.divider()

    st.subheader("🧾 Attendance (session)")
    if session_id is None:
        st.info("No session selected (app_state.current_session_id missing and no sessions found).")
    else:
        if attendance_total == 0:
            st.info("No attendance records for this session yet.")
        else:
            absent = max(attendance_total - attendance_present, 0)
            st.write(f"Present: **{attendance_present}** • Absent: **{absent}** • Marked: **{attendance_total}**")

    st.divider()

    # --------------------------------------------------------
    # ✅ Young AI view on dashboard
    # --------------------------------------------------------
    snapshot = {
        "schema": schema,
        "session_id": session_id,
        "total_members": int(total_members),
        "current_pot": float(current_pot),
        "cycle_contributions_total": float(cycle_total),
        "members_paid": int(members_paid),
        "attendance_total": int(attendance_total),
        "attendance_present": int(attendance_present),
        "loans_active_count": int(loans_active_count),
        "loans_active_total": float(loans_active_total),
        "fines_total": float(fines_total),
        "repayments_total": float(repayments_total),
        "interest_total": float(interest_total),
        "generated_at": _now_iso(),
    }
    _render_young_ai_view(snapshot)
