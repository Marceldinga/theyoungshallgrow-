# dashboard_panel.py ✅ COMPLETE SINGLE FILE — CLEAN Dashboard + 🤖 Young AI (grounded) + Snapshot-first
# ---------------------------------------------------------------------------------------------
# ✅ NJANGI STANDARD (NO legacy)
# ✅ CLEAN DASHBOARD:
#   - Only KPIs + Attendance + Young AI box
#   - No extra banners/status text at the top
#
# ✅ Snapshot-FIRST (FAST + consistent):
#   - Uses DB RPC snapshot when available:
#       public.fn_finance_snapshot()
#     (falls back to table reads if RPC is missing)
#   - Young answers ONLY from the LIVE snapshot (no guessing)
#   - Adds snapshot time (UTC) + source used
#
# ✅ Young AI behavior:
#   - Name: "Young"
#   - Responses MUST match the question asked (no random answers)
#   - Short + direct + grounded
#   - Optional HF Router for nicer wording (still grounded)
#   - Optional Tavily web search ONLY when query starts with "web:"
#
# ✅ Safe navigation buttons:
#   - Buttons set st.session_state["page"] then st.rerun()
#   - Page labels MUST match your app.py sidebar labels exactly.
#
# Optional env vars:
#   LOCAL_TZ=America/Chicago
#   HF_TOKEN=...
#   HF_FORCE_MODE=auto|chat|completions
#   HF_MODEL=meta-llama/Meta-Llama-3-8B-Instruct   (optional; still locked to allowed list below)
#   TAVILY_API_KEY=...
#
# requirements.txt (optional):
#   requests

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# SAFE import for APIError
try:
    from postgrest.exceptions import APIError
except Exception:
    APIError = Exception  # type: ignore

# requests optional
try:
    import requests
except Exception:
    requests = None  # type: ignore


# ============================================================
# HF ROUTER ENDPOINTS
# ============================================================
HF_ROUTER_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"
HF_ROUTER_COMPLETIONS_URL = "https://router.huggingface.co/v1/completions"

# ✅ Lock HF models to the same stable 3 (prevents unsupported model errors)
HF_ALLOWED_MODELS: List[str] = [
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.2",
]

# ============================================================
# SETTINGS
# ============================================================
AUTO_CREATE_SESSION_IF_NONE = False  # set True only if you want auto-create sessions
SNAPSHOT_TTL_SECONDS = 10            # snapshot cache to reduce DB spam (dashboard still stays "live")


# ============================================================
# LOCAL TIMEZONE (only for 1 human-touch line)
# ============================================================
def _local_now() -> datetime:
    tz_name = (os.getenv("LOCAL_TZ", "") or "America/Chicago").strip()
    try:
        from zoneinfo import ZoneInfo  # py3.9+
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(timezone.utc)


def _hello() -> str:
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
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
# THROTTLE (respects app.py session_state knobs if present)
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


# ============================================================
# SAFE DB HELPERS
# ============================================================
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


@st.cache_data(ttl=600, show_spinner=False)
def _table_readable_cached(schema: str, table: str, _sig: str) -> bool:
    # _sig forces cache separation per environment; caller supplies stable signature
    return True  # replaced at runtime by wrapper below


def _table_readable(client, schema: str, table: str) -> bool:
    if client is None:
        return False
    sig = str(id(client))
    try:
        # Use cached wrapper pattern to reduce repeated 1-row probes
        key = f"{schema}:{table}:{sig}"
        if "_tbl_ok_cache" not in st.session_state:
            st.session_state["_tbl_ok_cache"] = {}
        cache = st.session_state["_tbl_ok_cache"]
        if key in cache and isinstance(cache[key], bool):
            return bool(cache[key])

        _throttle_db()
        client.schema(schema).table(table).select("*").limit(1).execute()
        cache[key] = True
        return True
    except Exception:
        st.session_state["_tbl_ok_cache"][f"{schema}:{table}:{sig}"] = False
        return False


def _sum_amount(rows: List[Dict[str, Any]], col: str = "amount") -> float:
    s = 0.0
    for r in rows or []:
        try:
            s += float(r.get(col) or 0)
        except Exception:
            pass
    return float(s)


def _count_distinct(rows: List[Dict[str, Any]], key: str) -> int:
    s = set()
    for r in rows or []:
        v = r.get(key)
        if v is not None:
            s.add(str(v))
    return int(len(s))


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

    rows = _safe_select(sb_read, schema, "sessions", "id,created_at", order_by="id", desc=True, limit=1)
    if rows and rows[0].get("id") is not None:
        sid = _resolve_session_id(rows[0].get("id"))
        if sid is not None:
            return sid

    rows = _safe_select(sb_read, schema, "sessions", "session_id,created_at", order_by="session_id", desc=True, limit=1)
    if rows and rows[0].get("session_id") is not None:
        sid = _resolve_session_id(rows[0].get("session_id"))
        if sid is not None:
            return sid

    return None


def _ensure_current_session(sb_anon, sb_service, schema: str) -> Tuple[Optional[int], str]:
    sb_read = sb_service if sb_service is not None else sb_anon
    sb_write = sb_service

    app_state_rows = _safe_select(sb_read, schema, "app_state", "id,current_session_id", limit=1)
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
        latest_sid = _resolve_session_id((created[0] or {}).get("id")) if created else None
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
# RPC Snapshot: fn_finance_snapshot()
# ============================================================
def _rpc_finance_snapshot(sb_read, schema: str) -> Dict[str, Any]:
    """
    Calls public.fn_finance_snapshot().
    Supports formats:
      A) flat dict keys
      B) dict with nested: totals/counts/ratios
      C) list with 1 dict row
    """
    if sb_read is None:
        return {}
    try:
        _throttle_db()
        try:
            res = sb_read.schema(schema).rpc("fn_finance_snapshot", {}).execute()
        except Exception:
            res = sb_read.rpc("fn_finance_snapshot", {}).execute()

        data = getattr(res, "data", None)
        if not data:
            return {}
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def _extract_snapshot_number(obj: Any, keys: List[str], default: float = 0.0) -> float:
    if not isinstance(obj, dict):
        return float(default)
    for k in keys:
        if k in obj and obj.get(k) is not None:
            try:
                return float(obj.get(k))
            except Exception:
                pass
    return float(default)


def _snapshot_get(snap: Dict[str, Any], group: str, keys: List[str], default: Any = 0) -> Any:
    """
    Reads either:
      snap[group][key]  (nested)
      OR snap[key]      (flat)
    """
    if not isinstance(snap, dict):
        return default
    nested = snap.get(group)
    if isinstance(nested, dict):
        for k in keys:
            if k in nested and nested.get(k) is not None:
                return nested.get(k)
    for k in keys:
        if k in snap and snap.get(k) is not None:
            return snap.get(k)
    return default


# ============================================================
# WEB SEARCH (Tavily) — ONLY when user types "web:"
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

    summary = "Here’s what I found online:\n" + ("\n".join(bullets[:3]) if bullets else "• (No snippets returned)")
    return (summary, sources)


# ============================================================
# HF ROUTER (optional) — nicer wording, STILL grounded on snapshot
# ============================================================
def _has_hf_token() -> bool:
    return bool((os.getenv("HF_TOKEN") or "").strip())


def _hf_force_mode() -> str:
    return (os.getenv("HF_FORCE_MODE") or "auto").strip().lower()


def _hf_model() -> str:
    requested = (os.getenv("HF_MODEL") or HF_ALLOWED_MODELS[0]).strip()
    # lock to allowed list
    return requested if requested in HF_ALLOWED_MODELS else HF_ALLOWED_MODELS[0]


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
    payload = {"model": model, "messages": messages, "temperature": 0.15, "max_tokens": 240}
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
    payload = {"model": model, "prompt": prompt, "temperature": 0.15, "max_tokens": 240}
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
        "You are Young, an assistant inside a dashboard.\n"
        "Rules:\n"
        "- Use ONLY the DASHBOARD_SNAPSHOT JSON.\n"
        "- Answer ONLY what the user asked. No unrelated details.\n"
        "- If the answer is not in snapshot, say: 'I don’t have that in this snapshot.'\n"
        "- Never invent numbers.\n"
        "- Keep it short.\n\n"
        f"DASHBOARD_SNAPSHOT:\n{json.dumps(snapshot, ensure_ascii=False)}\n\n"
        f"USER_QUESTION:\n{question}\n\n"
        "Now answer:"
    )


def _hf_grounded_answer(question: str, snapshot: Dict[str, Any]) -> Tuple[bool, str, str]:
    token = (os.getenv("HF_TOKEN") or "").strip()
    model = _hf_model()
    force = _hf_force_mode()
    if not token:
        return False, "HF_TOKEN missing", "hf:missing"

    sys = (
        "You are Young.\n"
        "Start every answer with: 'Hello 👋🏽'.\n"
        "Answer ONLY from DASHBOARD_SNAPSHOT. Answer ONLY the question asked.\n"
        "No SQL. No Python.\n"
        "Keep it short.\n"
    )
    messages = [
        {"role": "system", "content": sys},
        {"role": "user", "content": _snapshot_prompt(snapshot, question)},
    ]

    if force == "completions":
        ok, txt = _hf_router_completions(model, token, _snapshot_prompt(snapshot, question))
        return (ok, txt, f"hf:completions:{model}") if ok else (False, txt, f"hf:completions_failed:{model}")
    if force == "chat":
        ok, txt = _hf_router_chat(model, token, messages)
        return (ok, txt, f"hf:chat:{model}") if ok else (False, txt, f"hf:chat_failed:{model}")

    ok, txt = _hf_router_completions(model, token, _snapshot_prompt(snapshot, question))
    if ok and txt:
        return True, txt, f"hf:completions:{model}"
    ok2, txt2 = _hf_router_chat(model, token, messages)
    if ok2 and txt2:
        return True, txt2, f"hf:chat:{model}"
    return False, (txt2 or txt or "HF failed"), f"hf:failed:{model}"


# ============================================================
# Young — local grounded rules (fallback)
# ============================================================
def _money0(x: Any) -> str:
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)


def _young_answer_rules(q: str, snap: Dict[str, Any]) -> str:
    t = (q or "").strip().lower()
    if not t:
        return "Hello 👋🏽 Ask a question about this dashboard snapshot."

    # Core fields
    session_id = snap.get("session_id")
    total_members = int(snap.get("total_members", 0) or 0)

    current_pot = float(snap.get("current_pot", 0.0) or 0.0)
    cycle_total = float(snap.get("cycle_contributions_total", 0.0) or 0.0)
    members_paid = int(snap.get("members_paid", 0) or 0)

    attendance_present = int(snap.get("attendance_present", 0) or 0)
    attendance_total = int(snap.get("attendance_total", 0) or 0)

    loans_active_total = float(snap.get("loans_active_total", 0.0) or 0.0)
    loans_active_count = int(snap.get("loans_active_count", 0) or 0)

    fines_total = float(snap.get("fines_total", 0.0) or 0.0)
    repayments_total = float(snap.get("repayments_total", 0.0) or 0.0)
    interest_total = float(snap.get("interest_total", 0.0) or 0.0)

    # Intents (simple + strict)
    if "total members" in t or (("members" in t) and ("total" in t)):
        return f"Hello 👋🏽 Total members: **{total_members}**."

    if t.strip() in {"members", "member"}:
        return f"Hello 👋🏽 Total members: **{total_members}**."

    if "members paid" in t or (("paid" in t) and ("members" in t)):
        return f"Hello 👋🏽 Members paid: **{members_paid}/{total_members}**."

    if ("session" in t and "id" in t) or t.strip() in {"session id", "session"}:
        return f"Hello 👋🏽 Session ID: **{session_id}**."

    if "pot" in t:
        return f"Hello 👋🏽 Current pot (Session {session_id}): **{_money0(current_pot)}**."

    if ("cycle" in t and "contribution" in t) or "cycle total" in t:
        return f"Hello 👋🏽 Cycle contributions (Session {session_id}): **{_money0(cycle_total)}**."

    if "attendance" in t:
        if attendance_total == 0:
            return f"Hello 👋🏽 No attendance recorded for Session **{session_id}** yet."
        absent = max(attendance_total - attendance_present, 0)
        return f"Hello 👋🏽 Attendance (Session {session_id}): Present **{attendance_present}**, Absent **{absent}**, Marked **{attendance_total}**."

    if "loan" in t or "loans" in t:
        if loans_active_count == 0:
            return "Hello 👋🏽 Active loans: **0**."
        return f"Hello 👋🏽 Active loans: **{loans_active_count}** (total **{_money0(loans_active_total)}**)."

    if "fine" in t or "fines" in t:
        return f"Hello 👋🏽 Fines total: **{_money0(fines_total)}**."

    if "repay" in t or "payment" in t or "paid back" in t:
        return f"Hello 👋🏽 Repayments total: **{_money0(repayments_total)}**."

    if "interest" in t:
        return f"Hello 👋🏽 Interest total: **{_money0(interest_total)}**."

    return "Hello 👋🏽 I don’t have that in this snapshot. Check another page (Loans / Contributions / Minutes)."


# ============================================================
# NAV BUTTONS
# ============================================================
def _nav_to(page_name: str):
    st.session_state["page"] = str(page_name)
    st.rerun()


def _safe_nav_buttons():
    st.markdown("#### Quick actions")
    st.caption("Opens pages by setting **st.session_state['page']** (must match your app.py menu labels).")

    c1, c2 = st.columns(2)

    with c1:
        if st.button("Dashboard", use_container_width=True):
            _nav_to("Dashboard")
        if st.button("💬 younchat", use_container_width=True):
            _nav_to("💬 younchat")
        if st.button("Contributions", use_container_width=True):
            _nav_to("Contributions")
        if st.button("Loans", use_container_width=True):
            _nav_to("Loans")

    with c2:
        if st.button("Payouts", use_container_width=True):
            _nav_to("Payouts")
        if st.button("🤖 AI Risk Panel", use_container_width=True):
            _nav_to("🤖 AI Risk Panel")
        if st.button("Minutes & Attendance", use_container_width=True):
            _nav_to("Minutes & Attendance")
        if st.button("Health", use_container_width=True):
            _nav_to("Health")


# ============================================================
# Young AI view
# ============================================================
def _render_young_ai_view(snapshot: Dict[str, Any]):
    st.markdown("### 🤖 Young — Dashboard AI")

    _safe_nav_buttons()
    st.divider()

    st.write(f"{_hello()} 👋🏽 I’m **Young** — your dashboard assistant.")
    st.caption(_human_touch())

    use_hf = st.toggle(
        "Use HF for nicer wording (still grounded)",
        value=False,
        help="Requires HF_TOKEN. If off, uses local grounded rules.",
        key="young_use_hf",
    )

    colA, colB = st.columns(2)
    with colA:
        st.caption(f"🧠 HF: {'READY' if _has_hf_token() else 'OFF'}")
    with colB:
        st.caption(f"🌐 Tavily: {'READY' if _has_tavily_key() else 'OFF'}")

    q = st.text_input("Ask Young…", placeholder="Ask about KPIs, attendance, loans, fines, etc.", key="young_dash_q")
    ask = st.button("Ask", key="young_dash_ask", use_container_width=True)

    if ask:
        q_clean = (q or "").strip()

        if _is_web_query(q_clean):
            if not _has_tavily_key():
                st.session_state["young_dash_a"] = "Hello 👋🏽 Web search is OFF. Add **TAVILY_API_KEY** in Railway Variables."
                st.session_state["young_dash_sources"] = []
                st.session_state["young_dash_used"] = "web:off"
            else:
                query = _strip_web_prefix(q_clean)
                tav = _tavily_search_cached(query=query, max_results=5, search_depth="basic")
                summary, sources = _format_web_results(tav)
                st.session_state["young_dash_a"] = "Hello 👋🏽 " + summary
                st.session_state["young_dash_sources"] = sources
                st.session_state["young_dash_used"] = "web:tavily"
        else:
            if use_hf and _has_hf_token():
                ok, txt, used = _hf_grounded_answer(q_clean, snapshot)
                if ok and txt:
                    ans = txt.strip()
                    if not ans.lower().startswith("hello"):
                        ans = "Hello 👋🏽 " + ans
                    st.session_state["young_dash_a"] = ans
                    st.session_state["young_dash_sources"] = []
                    st.session_state["young_dash_used"] = used
                else:
                    st.session_state["young_dash_a"] = (
                        "Hello 👋🏽 HF failed — using local grounded rules.\n\n"
                        f"**HF error:** {txt}\n\n"
                        + _young_answer_rules(q_clean, snapshot)
                    )
                    st.session_state["young_dash_sources"] = []
                    st.session_state["young_dash_used"] = used
            else:
                st.session_state["young_dash_a"] = _young_answer_rules(q_clean, snapshot)
                st.session_state["young_dash_sources"] = []
                st.session_state["young_dash_used"] = "local"

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

    used = st.session_state.get("young_dash_used", "—")
    st.caption(f"Source used: {used} • Snapshot time (UTC): {snapshot.get('generated_at','—')} • Snapshot src: {snapshot.get('_snapshot_source','—')}")

    with st.expander("🔎 Debug snapshot", expanded=False):
        st.json(snapshot)


# ============================================================
# SNAPSHOT BUILDERS
# ============================================================
@st.cache_data(ttl=SNAPSHOT_TTL_SECONDS, show_spinner=False)
def _cached_finance_snapshot(schema: str, _sig: str) -> Dict[str, Any]:
    # NOTE: body replaced by wrapper; cache key uses schema+_sig
    return {}


def _get_finance_snapshot(sb_read, schema: str) -> Tuple[Dict[str, Any], str]:
    """
    Returns (finance_snapshot_dict, source_label)
    """
    if sb_read is None:
        return {}, "none"

    # in-session cache (fast) + streamlit cache (ttl)
    sig = str(id(sb_read))
    key = f"_finance_snapshot::{schema}::{sig}"
    now = time.time()

    local_cache = st.session_state.get("_dash_snap_cache") or {}
    if isinstance(local_cache, dict):
        entry = local_cache.get(key)
        if isinstance(entry, dict) and (now - float(entry.get("_ts", 0.0))) <= SNAPSHOT_TTL_SECONDS:
            return dict(entry.get("snap") or {}), "rpc:cached"

    # Try RPC snapshot
    snap = _rpc_finance_snapshot(sb_read, schema)
    if snap:
        local_cache[key] = {"_ts": now, "snap": snap}
        st.session_state["_dash_snap_cache"] = local_cache
        return snap, "rpc:fn_finance_snapshot"

    return {}, "rpc:missing"


def _build_dashboard_snapshot(
    sb_read,
    schema: str,
    session_id: Optional[int],
) -> Tuple[Dict[str, Any], str]:
    """
    Builds the dashboard snapshot:
      - Finance totals from RPC (if available)
      - Session KPIs (cycle pot, members paid) from contributions table (session-scoped)
      - Attendance (session-scoped)
      - Loans active totals (table)
      - Fines/repayments/interest totals (table)   [global totals]
    """
    finance_snap, finance_src = _get_finance_snapshot(sb_read, schema)

    # Always compute session-scoped contribution KPIs (needs session_id)
    contrib_rows: List[Dict[str, Any]] = []
    if session_id is not None and _table_readable(sb_read, schema, "contributions"):
        contrib_rows = _safe_select(
            sb_read,
            schema,
            "contributions",
            "id,member_id,amount,session_id,created_at",
            session_id=int(session_id),
            limit=15000,
            show_error=False,
        )

    cycle_total = _sum_amount(contrib_rows, "amount")
    members_paid = _count_distinct(contrib_rows, "member_id")
    current_pot = float(cycle_total)

    # Members count (fallback to table)
    total_members = 0
    if _table_readable(sb_read, schema, "members"):
        mrows = _safe_select(sb_read, schema, "members", "id", limit=20000, show_error=False)
        total_members = len(mrows) if mrows else 0

    # Attendance (session)
    attendance_rows: List[Dict[str, Any]] = []
    if session_id is not None and _table_readable(sb_read, schema, "attendance"):
        attendance_rows = _safe_select(
            sb_read,
            schema,
            "attendance",
            "member_id,present,session_id,created_at",
            session_id=int(session_id),
            limit=15000,
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

    # Loans active totals (global)
    loans_active_count = 0
    loans_active_total = 0.0
    if _table_readable(sb_read, schema, "loans"):
        loans_rows = _safe_select(
            sb_read,
            schema,
            "loans",
            "id,member_id,status,principal,principal_current,total_due,created_at",
            limit=30000,
            show_error=False,
        )
        active_status = {"active", "overdue", "late", "open"}
        loans_active = [r for r in loans_rows if str(r.get("status") or "").strip().lower() in active_status]
        loans_active_count = len(loans_active)
        for r in loans_active:
            # prefer principal_current if present
            for k in ("principal_current", "principal", "total_due"):
                if k in r and r.get(k) is not None:
                    try:
                        loans_active_total += float(r.get(k) or 0)
                        break
                    except Exception:
                        pass

    # Fines (global)
    fines_total = 0.0
    if _table_readable(sb_read, schema, "fines"):
        fines_rows = _safe_select(sb_read, schema, "fines", "amount,created_at", limit=30000, show_error=False)
        fines_total = _sum_amount(fines_rows, "amount")

    # Repayments (global)
    repayments_total = 0.0
    if _table_readable(sb_read, schema, "loan_payments"):
        pay_rows = _safe_select(sb_read, schema, "loan_payments", "amount,created_at", limit=30000, show_error=False)
        repayments_total = _sum_amount(pay_rows, "amount")

    # Interest (global)
    interest_total = 0.0
    if _table_readable(sb_read, schema, "interest_ledger"):
        i_rows = _safe_select(sb_read, schema, "interest_ledger", "amount,created_at", limit=30000, show_error=False)
        interest_total = _sum_amount(i_rows, "amount")

    # Pull any finance totals if your fn_finance_snapshot provides them
    # (safe: supports nested or flat)
    total_contrib_all = _snapshot_get(finance_snap, "totals", ["total_contributions", "contributions_total"], default=None)
    foundation_total = _snapshot_get(finance_snap, "totals", ["foundation_total"], default=None)

    out = {
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
        # Optional (from finance snapshot)
        "total_contributions_all_time": (float(total_contrib_all) if total_contrib_all is not None else None),
        "foundation_total_all_time": (float(foundation_total) if foundation_total is not None else None),
        "generated_at": _now_iso(),
        "_snapshot_source": finance_src,
    }

    return out, finance_src


# ============================================================
# MAIN DASHBOARD (CLEAN)
# ============================================================
def render_dashboard(sb_anon, sb_service=None, schema: str = "public"):
    """
    CLEAN dashboard:
      - KPIs
      - Attendance
      - Young AI
    """
    sb_read = sb_service if sb_service is not None else sb_anon

    # Ensure current session (do not print the verbose message)
    session_id, _ = _ensure_current_session(sb_anon=sb_anon, sb_service=sb_service, schema=schema)

    # Snapshot-first build
    snapshot, _src = _build_dashboard_snapshot(sb_read=sb_read, schema=schema, session_id=session_id)

    # KPIs row (clean)
    c1, c2, c3, c4, c5 = st.columns([0.9, 0.9, 0.9, 0.9, 1.2])
    with c1:
        st.metric("Session ID", snapshot.get("session_id") if snapshot.get("session_id") is not None else "—")
    with c2:
        st.metric("Total Members", f"{int(snapshot.get('total_members') or 0):,}")
    with c3:
        st.metric("Current Pot", f"{float(snapshot.get('current_pot') or 0):,.0f}")
    with c4:
        st.metric("Cycle Contributions", f"{float(snapshot.get('cycle_contributions_total') or 0):,.0f}")
    with c5:
        st.metric("Members Paid", f"{int(snapshot.get('members_paid') or 0)}/{int(snapshot.get('total_members') or 0)}")

    st.divider()

    # Attendance
    st.subheader("🧾 Attendance (session)")
    if snapshot.get("session_id") is None:
        st.info("No session selected.")
    else:
        attendance_total = int(snapshot.get("attendance_total") or 0)
        attendance_present = int(snapshot.get("attendance_present") or 0)
        if attendance_total == 0:
            st.info("No attendance records for this session yet.")
        else:
            absent = max(attendance_total - attendance_present, 0)
            st.write(f"Present: **{attendance_present}** • Absent: **{absent}** • Marked: **{attendance_total}**")

    st.divider()

    # Young AI
    _render_young_ai_view(snapshot)
