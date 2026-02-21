
# dashboard_panel.py ✅ COMPLETE SINGLE FILE (FIXED + CLEAN)
# --------------------------------------------------------
# ✅ NJANGI STANDARD (NO legacy)
# ✅ CLEAN DASHBOARD (your request):
#   - NO extra text at top (no status lines, no "modern analytics" banner)
#   - Only: KPIs + Attendance + younchat Dashboard AI box
#
# ✅ Dashboard AI = younchat (your style):
#   - Salute is always: "Hello"
#   - Modern intro (short)
#   - Grounded answers ONLY from LIVE snapshot (no guessing)
#   - Optional: HF Router for nicer wording (still grounded on snapshot)
#   - Optional: Tavily web search ONLY when query starts with "web:"
#
# ✅ Safe navigation buttons:
#   - Buttons set st.session_state["page"] then st.rerun()
#   - Make sure your app.py uses EXACT page labels (match your sidebar labels)
#
# ✅ SAFE IMPORT FIX:
#   - APIError import is wrapped (prevents crashes if postgrest differs)
#
# Optional Railway Variables:
#   LOCAL_TZ=America/Chicago (or America/New_York)
#   TAVILY_API_KEY=...
#   HF_TOKEN=...
#   HF_MODEL=meta-llama/Meta-Llama-3-8B-Instruct
#   HF_FORCE_MODE=auto|chat|completions
#
# Requirements (optional):
#   pip install requests

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# ✅ SAFE import (fix)
try:
    from postgrest.exceptions import APIError
except Exception:
    APIError = Exception  # type: ignore

# ✅ requests optional
try:
    import requests
except Exception:
    requests = None  # type: ignore


# ============================================================
# SETTINGS
# ============================================================
AUTO_CREATE_SESSION_IF_NONE = False  # set True only if you want to auto-create sessions


# ============================================================
# LOCAL TIMEZONE (for human-touch line only)
# ============================================================
def _local_now() -> datetime:
    tz_name = (os.getenv("LOCAL_TZ", "") or "America/Chicago").strip()
    try:
        from zoneinfo import ZoneInfo  # py3.9+
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(timezone.utc)


def _greeting_of_day() -> str:
    # Your requirement: salute must ALWAYS be Hello
    return "Hello"


def _human_touch() -> str:
    h = _local_now().hour
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
# TIME (UTC stamp for snapshot)
# ============================================================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# SAFE ERROR TEXT
# ============================================================
def _api_msg(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or payload)
        return str(payload)
    return str(e)


# ============================================================
# LIGHT THROTTLE (uses app.py session_state if present)
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


def _safe_insert(client, schema: str, table: str, row: Dict[str, Any]) -> List[Dict[str, Any]]:
    if client is None:
        return []
    try:
        _throttle_db()
        return (client.schema(schema).table(table).insert(row).execute().data or [])
    except Exception:
        return []


def _safe_update_eq(client, schema: str, table: str, updates: Dict[str, Any], eq_key: str, eq_val: Any) -> bool:
    if client is None:
        return False
    try:
        _throttle_db()
        client.schema(schema).table(table).update(updates).eq(eq_key, eq_val).execute()
        return True
    except Exception:
        return False


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
# SESSION BOOTSTRAP
# ============================================================
def _resolve_session_id(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        s = str(raw).strip()
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


def _get_latest_session_id(sb_read, schema: str) -> Optional[int]:
    if sb_read is None:
        return None

    # sessions.id
    rows = _safe_select(sb_read, schema, "sessions", "id,created_at", order_by="id", desc=True, limit=1, show_error=False)
    if rows and rows[0].get("id") is not None:
        sid = _resolve_session_id(rows[0].get("id"))
        if sid is not None:
            return sid

    # fallback sessions.session_id
    rows = _safe_select(
        sb_read, schema, "sessions", "session_id,created_at", order_by="session_id", desc=True, limit=1, show_error=False
    )
    if rows and rows[0].get("session_id") is not None:
        sid = _resolve_session_id(rows[0].get("session_id"))
        if sid is not None:
            return sid

    return None


def _ensure_current_session(sb_anon, sb_service, schema: str) -> Tuple[Optional[int], str]:
    sb_read = sb_service if sb_service is not None else sb_anon
    sb_write = sb_service

    app_state_rows = _safe_select(sb_read, schema, "app_state", "id,current_session_id", limit=1, show_error=False)
    app_state = app_state_rows[0] if app_state_rows else {}
    app_state_id = app_state.get("id")
    current_sid = _resolve_session_id(app_state.get("current_session_id"))

    latest_sid = _get_latest_session_id(sb_read, schema)

    if latest_sid is None:
        if not AUTO_CREATE_SESSION_IF_NONE:
            return None, "No sessions found."
        if sb_write is None:
            return None, "No sessions found and service key missing."
        name = f"Cycle {pd.Timestamp.utcnow().strftime('%Y-%m-%d')}"
        created = _safe_insert(sb_write, schema, "sessions", {"name": name, "is_active": True})
        if created and created[0].get("id") is not None:
            latest_sid = _resolve_session_id(created[0].get("id"))
        if latest_sid is None:
            latest_sid = _resolve_session_id((created[0] or {}).get("session_id")) if created else None
        if latest_sid is None:
            return None, "Auto-create session failed."

    if not app_state_rows:
        if sb_write is None:
            return latest_sid, "Selected latest session (app_state missing; cannot write)."
        ins = _safe_insert(sb_write, schema, "app_state", {"current_session_id": latest_sid})
        if ins:
            return latest_sid, "Selected latest session (app_state created)."
        return latest_sid, "Selected latest session (app_state create failed)."

    if current_sid is None:
        if sb_write is None:
            return latest_sid, "Selected latest session (current_session_id missing; cannot write)."
        ok = _safe_update_eq(sb_write, schema, "app_state", {"current_session_id": latest_sid}, "id", app_state_id)
        if ok:
            return latest_sid, "Selected latest session (app_state updated)."
        return latest_sid, "Selected latest session (app_state update failed)."

    return current_sid, "Using current session from app_state."


# ============================================================
# WEB SEARCH (Tavily)
# ============================================================
def _has_tavily_key() -> bool:
    return bool(os.getenv("TAVILY_API_KEY", "").strip())


def _is_web_query(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.startswith("web:") or t.startswith("internet:") or t.startswith("tavily:")


def _strip_web_prefix(q: str) -> str:
    return re.sub(r"^(web:|internet:|tavily:)\s*", "", (q or "").strip(), flags=re.IGNORECASE).strip()


@st.cache_data(ttl=3600, show_spinner=False)
def _tavily_search_cached(query: str, max_results: int = 5, search_depth: str = "basic") -> Dict[str, Any]:
    if requests is None:
        return {"error": "requests is not installed. Add it to requirements.txt."}
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


# ============================================================
# HF ROUTER (optional) — better wording, STILL grounded on snapshot
# ============================================================
HF_ROUTER_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"
HF_ROUTER_COMPLETIONS_URL = "https://router.huggingface.co/v1/completions"


def _has_hf_token() -> bool:
    return bool((os.getenv("HF_TOKEN") or "").strip())


def _hf_model() -> str:
    return (os.getenv("HF_MODEL") or "meta-llama/Meta-Llama-3-8B-Instruct").strip()


def _hf_force_mode() -> str:
    return (os.getenv("HF_FORCE_MODE") or "auto").strip().lower()


def _post_with_retries(url: str, headers: dict, payload: dict, timeout: int = 60) -> Tuple[bool, str]:
    if requests is None:
        return False, "requests not installed"
    last_err = ""
    for attempt in range(4):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = f"HF error {r.status_code}: {r.text[:600]}"
                time.sleep(1.0 + attempt * 1.5)
                continue
            if r.status_code >= 400:
                return False, f"HF error {r.status_code}: {r.text[:600]}"
            return True, r.text
        except Exception as e:
            last_err = str(e)
            time.sleep(1.0 + attempt * 1.5)
    return False, last_err or "HF transient error"


def _hf_router_chat(model: str, token: str, messages: List[Dict[str, str]], timeout: int = 60) -> Tuple[bool, str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 400}
    ok, raw = _post_with_retries(HF_ROUTER_CHAT_URL, headers, payload, timeout=timeout)
    if not ok:
        return False, raw
    try:
        data = json.loads(raw)
        text = (((data.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
        return True, str(text).strip()
    except Exception:
        return False, f"Bad HF chat response: {raw[:600]}"


def _hf_router_completions(model: str, token: str, prompt: str, timeout: int = 60) -> Tuple[bool, str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"model": model, "prompt": prompt, "temperature": 0.2, "max_tokens": 400}
    ok, raw = _post_with_retries(HF_ROUTER_COMPLETIONS_URL, headers, payload, timeout=timeout)
    if not ok:
        return False, raw
    try:
        data = json.loads(raw)
        text = ((data.get("choices") or [{}])[0].get("text") or "")
        return True, str(text).strip()
    except Exception:
        return False, f"Bad HF completions response: {raw[:600]}"


def _snapshot_prompt(snapshot: Dict[str, Any], question: str) -> str:
    return (
        "You are younchat inside a dashboard.\n"
        "You MUST answer using ONLY the provided DASHBOARD_SNAPSHOT JSON.\n"
        "If missing, say what is missing.\n"
        "Never invent numbers.\n\n"
        f"DASHBOARD_SNAPSHOT:\n{json.dumps(snapshot, ensure_ascii=False)}\n\n"
        f"USER_QUESTION:\n{question}\n\n"
        "Answer now, grounded on the snapshot:"
    )


def _hf_grounded_answer(question: str, snapshot: Dict[str, Any]) -> Tuple[bool, str, str]:
    token = (os.getenv("HF_TOKEN") or "").strip()
    model = _hf_model()
    force = _hf_force_mode()
    if not token:
        return False, "HF_TOKEN missing", "hf:missing"

    sys = (
        "You are younchat.\n"
        "Start with 'Hello 👋🏽'. Keep it modern.\n"
        "Answer ONLY from DASHBOARD_SNAPSHOT. No guessing.\n"
    )
    messages = [
        {"role": "system", "content": sys},
        {"role": "user", "content": _snapshot_prompt(snapshot, question)},
    ]

    if force == "completions":
        ok, txt = _hf_router_completions(model, token, _snapshot_prompt(snapshot, question))
        return (ok, txt, "hf:completions") if ok else (False, txt, "hf:completions_failed")
    if force == "chat":
        ok, txt = _hf_router_chat(model, token, messages)
        return (ok, txt, "hf:chat") if ok else (False, txt, "hf:chat_failed")

    ok, txt = _hf_router_completions(model, token, _snapshot_prompt(snapshot, question))
    if ok and txt:
        return True, txt, "hf:completions"
    ok2, txt2 = _hf_router_chat(model, token, messages)
    if ok2 and txt2:
        return True, txt2, "hf:chat"
    return False, (txt2 or txt or "HF failed"), "hf:failed"


# ============================================================
# younchat — grounded rules (always available)
# ============================================================
def _youn_answer_rules(q: str, snap: Dict[str, Any]) -> str:
    t = (q or "").strip().lower()

    session_id = snap.get("session_id")
    total_members = int(snap.get("total_members", 0) or 0)
    pot = float(snap.get("current_pot", 0.0) or 0.0)
    cycle_total = float(snap.get("cycle_contributions_total", 0.0) or 0.0)
    members_paid = int(snap.get("members_paid", 0) or 0)

    attendance_present = int(snap.get("attendance_present", 0) or 0)
    attendance_total = int(snap.get("attendance_total", 0) or 0)

    loans_active_total = float(snap.get("loans_active_total", 0.0) or 0.0)
    loans_active_count = int(snap.get("loans_active_count", 0) or 0)

    fines_total = float(snap.get("fines_total", 0.0) or 0.0)
    repayments_total = float(snap.get("repayments_total", 0.0) or 0.0)
    interest_total = float(snap.get("interest_total", 0.0) or 0.0)

    def money(x: float) -> str:
        try:
            return f"{float(x):,.0f}"
        except Exception:
            return str(x)

    if not t:
        return "Hello 👋🏽 Ask me: **pot this session**, **loans summary**, **fines total**, or **attendance summary**."

    if "session" in t and ("id" in t or "which" in t):
        return f"Hello 👋🏽 Current **Session ID** is **{session_id}**."

    if "pot" in t:
        return (
            f"Hello 👋🏽 Here’s your snapshot:\n\n"
            f"- **Pot (Session {session_id})**: **{money(pot)}**\n"
            f"- **Members paid**: **{members_paid}/{total_members}**\n"
            f"- **Cycle contributions total**: **{money(cycle_total)}**"
        )

    if "contribution" in t or "cycle" in t:
        return (
            f"Hello 👋🏽 Cycle contributions (Session {session_id}) = **{money(cycle_total)}**.\n\n"
            f"Members paid: **{members_paid}/{total_members}**."
        )

    if "attendance" in t or "present" in t or "absent" in t:
        if attendance_total == 0:
            return f"Hello 👋🏽 No attendance records yet for **Session {session_id}**."
        absent = max(attendance_total - attendance_present, 0)
        return (
            f"Hello 👋🏽 Attendance (Session {session_id}):\n\n"
            f"- Present: **{attendance_present}**\n"
            f"- Absent: **{absent}**\n"
            f"- Total marked: **{attendance_total}**"
        )

    if "loan" in t:
        if loans_active_count == 0:
            return "Hello 👋🏽 I see **no active loans** in the current snapshot."
        return (
            "Hello 👋🏽 Loans snapshot:\n\n"
            f"- Active loans: **{loans_active_count}**\n"
            f"- Active principal total: **{money(loans_active_total)}**\n\n"
            "If you want *who owes what*, open **Loans** or **🤖 AI Risk Panel**."
        )

    if "fine" in t:
        return f"Hello 👋🏽 Fines total (snapshot) = **{money(fines_total)}**."

    if "repay" in t or "payment" in t:
        return f"Hello 👋🏽 Repayments total (snapshot) = **{money(repayments_total)}**."

    if "interest" in t:
        return f"Hello 👋🏽 Interest total (snapshot) = **{money(interest_total)}**."

    if "status" in t or "live" in t:
        return "Hello 👋🏽 Status: **LIVE** (reading from Supabase)."

    return (
        "Hello 👋🏽 I can answer from the dashboard snapshot:\n"
        "- **pot this session**\n"
        "- **cycle contributions**\n"
        "- **loans summary**\n"
        "- **fines total** / **repayments total** / **interest total**\n"
        "- **attendance summary**\n\n"
        "For web help, start with **web:** (example: `web: Maryland cosmetology license requirements`)."
    )


# ============================================================
# 🧭 SAFE NAVIGATION
# ============================================================
def _nav_to(page_name: str):
    st.session_state["page"] = str(page_name)
    st.rerun()


def _safe_nav_buttons():
    st.markdown("#### Quick actions")
    st.caption("Opens pages by setting **st.session_state['page']** (must match your app.py menu labels).")

    # ✅ IMPORTANT:
    # These labels MUST match your sidebar labels exactly.
    # If your app.py uses "AI Risk Panel" (no emoji), change it here too.
    c1, c2 = st.columns(2)

    with c1:
        if st.button("Dashboard", use_container_width=True):
            _nav_to("Dashboard")
        if st.button("Contributions", use_container_width=True):
            _nav_to("Contributions")
        if st.button("Loans", use_container_width=True):
            _nav_to("Loans")
        if st.button("Payouts", use_container_width=True):
            _nav_to("Payouts")

    with c2:
        if st.button("AI Risk Panel", use_container_width=True):
            _nav_to("AI Risk Panel")
        if st.button("Minutes & Attendance", use_container_width=True):
            _nav_to("Minutes & Attendance")
        if st.button("Audit", use_container_width=True):
            _nav_to("Audit")
        if st.button("Health", use_container_width=True):
            _nav_to("Health")


def _render_youn_ai_view(snapshot: Dict[str, Any]):
    st.markdown("### 💬 younchat — Dashboard AI")

    _safe_nav_buttons()
    st.divider()

    # ✅ YOUR REQUEST: short, clean intro
    st.write(f"{_greeting_of_day()} 👋🏽 I’m **younchat** — your dashboard assistant.")
    st.caption(_human_touch())

    use_hf = st.toggle(
        "Use HF for nicer wording (still grounded)",
        value=False,
        help="Requires HF_TOKEN. If off, I use local grounded rules.",
        key="youn_use_hf",
    )

    colA, colB = st.columns(2)
    with colA:
        st.caption(f"🧠 HF: {'READY' if _has_hf_token() else 'OFF'}")
    with colB:
        st.caption(f"🌐 Tavily: {'READY' if _has_tavily_key() else 'OFF'}")

    q = st.text_input(
        "Ask younchat…",
        placeholder="e.g., pot this session  |  loans summary  |  web: Maryland cosmetology license requirements",
        key="youn_dash_q",
    )
    ask = st.button("Ask", key="youn_dash_ask", use_container_width=True)

    if ask:
        if _is_web_query(q):
            if not _has_tavily_key():
                st.session_state["youn_dash_a"] = "Hello 👋🏽 Web search is OFF. Add **TAVILY_API_KEY** in Railway Variables."
                st.session_state["youn_dash_sources"] = []
                st.session_state["youn_dash_used"] = "web:off"
            else:
                query = _strip_web_prefix(q)
                tav = _tavily_search_cached(query=query, max_results=5, search_depth="basic")
                summary, sources = _format_web_results(tav)
                st.session_state["youn_dash_a"] = "Hello 👋🏽 " + summary
                st.session_state["youn_dash_sources"] = sources
                st.session_state["youn_dash_used"] = "web:tavily"
        else:
            if use_hf and _has_hf_token():
                ok, txt, used = _hf_grounded_answer(q, snapshot)
                if ok and txt:
                    st.session_state["youn_dash_a"] = txt if txt.lower().startswith("hello") else ("Hello 👋🏽 " + txt)
                    st.session_state["youn_dash_sources"] = []
                    st.session_state["youn_dash_used"] = used
                else:
                    st.session_state["youn_dash_a"] = (
                        "Hello 👋🏽 HF failed — using local grounded rules.\n\n"
                        f"**HF error:** {txt}\n\n"
                        + _youn_answer_rules(q, snapshot)
                    )
                    st.session_state["youn_dash_sources"] = []
                    st.session_state["youn_dash_used"] = used
            else:
                st.session_state["youn_dash_a"] = _youn_answer_rules(q, snapshot)
                st.session_state["youn_dash_sources"] = []
                st.session_state["youn_dash_used"] = "local"

    a = st.session_state.get("youn_dash_a")
    if a:
        st.markdown(a)

    sources = st.session_state.get("youn_dash_sources") or []
    if sources:
        st.markdown("**Sources:**")
        for s in sources[:5]:
            title = s.get("title") or "Source"
            url = s.get("url") or ""
            if url:
                st.markdown(f"- [{title}]({url})")
            else:
                st.markdown(f"- {title}")

    used = st.session_state.get("youn_dash_used", "—")
    st.caption(f"Source used: {used} • Snapshot time (UTC): {snapshot.get('generated_at','—')}")

    # Keep debug hidden by default (clean)
    with st.expander("🔎 Debug snapshot", expanded=False):
        st.json(snapshot)


# ============================================================
# MAIN DASHBOARD (CLEAN)
# ============================================================
def render_dashboard(sb_anon, sb_service=None, schema: str = "public"):
    """
    CLEAN dashboard:
      - KPIs
      - Attendance
      - younchat AI
    """
    sb_read = sb_service if sb_service is not None else sb_anon

    # Ensure current session (we do NOT print the verbose message on the dashboard)
    session_id, _session_msg = _ensure_current_session(sb_anon=sb_anon, sb_service=sb_service, schema=schema)

    # Members count
    members_rows = _safe_select(sb_read, schema, "members", "id", limit=5000, show_error=False)
    total_members = len(members_rows) if members_rows else 0

    # Contributions for current session
    contrib_rows: List[Dict[str, Any]] = []
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

    # Attendance for current session
    attendance_rows: List[Dict[str, Any]] = []
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

    # Loans (active)
    loans_rows: List[Dict[str, Any]] = []
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
    loans_active = [r for r in loans_rows if str(r.get("status") or "").strip().lower() in active_status]

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

    # Fines
    fines_total = 0.0
    if _table_readable(sb_read, schema, "fines"):
        fines_rows = _safe_select(sb_read, schema, "fines", "amount,created_at", limit=20000, show_error=False)
        fines_total = _sum_amount(fines_rows, "amount")

    # Repayments
    repayments_total = 0.0
    if _table_readable(sb_read, schema, "loan_payments"):
        pay_rows = _safe_select(sb_read, schema, "loan_payments", "amount,created_at", limit=20000, show_error=False)
        repayments_total = _sum_amount(pay_rows, "amount")

    # Interest
    interest_total = 0.0
    if _table_readable(sb_read, schema, "interest_ledger"):
        i_rows = _safe_select(sb_read, schema, "interest_ledger", "amount,created_at", limit=20000, show_error=False)
        interest_total = _sum_amount(i_rows, "amount")

    # KPIs
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

    # Attendance block
    st.subheader("🧾 Attendance (session)")
    if session_id is None:
        st.info("No session selected.")
    else:
        if attendance_total == 0:
            st.info("No attendance records for this session yet.")
        else:
            absent = max(attendance_total - attendance_present, 0)
            st.write(f"Present: **{attendance_present}** • Absent: **{absent}** • Marked: **{attendance_total}**")

    st.divider()

    # Snapshot for younchat AI
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

    _render_youn_ai_view(snapshot)
