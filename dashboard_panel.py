
# dashboard_panel.py ✅ COMPLETE SINGLE FILE — CLEAN DASHBOARD (NO EXTRA TEXT) + 💬 younchat (Snapshot Copilot)
# ----------------------------------------------------------------------------------------------------------
# ✅ NJANGI STANDARD (NO legacy)
# ✅ Clean UI: dashboard shows ONLY full information (KPIs + sections). No marketing lines.
# ✅ younchat on dashboard:
#    - Minimal header (no extra paragraphs)
#    - Answers ONLY from LIVE snapshot (no guessing)
#    - Optional web search ONLY if user types "web:" (Tavily)
#    - Optional HF wording (does NOT invent numbers; still grounded on snapshot)
#
# Works with app.py calling:
#   render_dashboard(sb_anon=..., sb_service=..., schema=...)
#
# Railway env vars (optional):
#   LOCAL_TZ=America/New_York   (or America/Chicago)
#   TAVILY_API_KEY=<...>
#   HF_TOKEN=<...>
#   HF_MODEL=meta-llama/Meta-Llama-3-8B-Instruct
#   HF_FORCE_MODE=auto|chat|completions
#
# NOTE:
# - "Quick actions (safe navigation)" can be ENABLED by setting ENABLE_DASH_NAV=True
# - Requires app.py to bridge st.session_state["page"] to the page selector.
# ----------------------------------------------------------------------------------------------------------

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

try:
    from postgrest.exceptions import APIError
except Exception:
    APIError = Exception  # type: ignore


# ============================================================
# SETTINGS
# ============================================================
AUTO_CREATE_SESSION_IF_NONE = False
ENABLE_DASH_NAV = False  # ✅ CLEAN: default OFF (no extra UI)
W_STRETCH = "stretch"


# ============================================================
# LOCAL TIMEZONE (optional, for greeting only)
# ============================================================
def _local_now() -> datetime:
    tz_name = (os.getenv("LOCAL_TZ", "") or "America/Chicago").strip()
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(timezone.utc)


def _hello() -> str:
    return "Hello"


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
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or "APIError")
        return str(e)
    return repr(e)


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

    rows = _safe_select(sb_read, schema, "sessions", "id,created_at", order_by="id", desc=True, limit=1, show_error=False)
    if rows and rows[0].get("id") is not None:
        sid = _resolve_session_id(rows[0].get("id"))
        if sid is not None:
            return sid

    rows = _safe_select(
        sb_read,
        schema,
        "sessions",
        "session_id,created_at",
        order_by="session_id",
        desc=True,
        limit=1,
        show_error=False,
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
            return None, "No sessions found (service key missing; cannot auto-create)."
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
            return latest_sid, "Selected latest session (app_state missing; read-only)."
        ins = _safe_insert(sb_write, schema, "app_state", {"current_session_id": latest_sid})
        return latest_sid, "Selected latest session."

    if current_sid is None:
        if sb_write is None:
            return latest_sid, "Selected latest session (current_session_id missing; read-only)."
        _safe_update_eq(sb_write, schema, "app_state", {"current_session_id": latest_sid}, "id", app_state_id)
        return latest_sid, "Selected latest session."

    return current_sid, "Using current session."


# ============================================================
# Tavily Web Search (only when user types "web:")
# ============================================================
def _has_tavily_key() -> bool:
    return bool(os.getenv("TAVILY_API_KEY", "").strip())


def _is_web_query(text: str) -> bool:
    return (text or "").strip().lower().startswith("web:")


def _strip_web_prefix(q: str) -> str:
    return re.sub(r"^web:\s*", "", (q or "").strip(), flags=re.IGNORECASE).strip()


@st.cache_data(ttl=3600, show_spinner=False)
def _tavily_search_cached(query: str, max_results: int = 5) -> Dict[str, Any]:
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return {"error": "Missing TAVILY_API_KEY."}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {"query": query, "max_results": int(max_results), "search_depth": "basic"}
    try:
        r = requests.post("https://api.tavily.com/search", headers=headers, json=payload, timeout=20)
        if r.status_code != 200:
            return {"error": f"Tavily error {r.status_code}: {r.text[:300]}"}
        j = r.json()
        return j if isinstance(j, dict) else {"raw": r.text}
    except Exception as e:
        return {"error": repr(e)}


def _format_web_results(tav: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]]]:
    if not isinstance(tav, dict):
        return ("Hello 👋🏽 I couldn’t read the web results.", [])
    if "error" in tav:
        return (f"Hello 👋🏽 Web search failed: {tav['error']}", [])

    results = tav.get("results", []) or []
    if not results:
        return ("Hello 👋🏽 I searched the web but didn’t find clear results.", [])

    bullets = []
    sources: List[Dict[str, str]] = []
    for r in results[:5]:
        title = str(r.get("title") or "Source").strip()
        url = str(r.get("url") or "").strip()
        content = str(r.get("content") or "").strip()
        if content:
            bullets.append(f"- {content[:240].rstrip()}…")
        if url:
            sources.append({"title": title, "url": url})

    summary = "Hello 👋🏽 Web results (top):\n" + ("\n".join(bullets[:3]) if bullets else "- (No snippets)")
    return (summary, sources)


# ============================================================
# HF Router (optional; only for phrasing, NOT for numbers)
# ============================================================
def _hf_enabled() -> bool:
    return bool(os.getenv("HF_TOKEN", "").strip())


def _hf_model() -> str:
    return (os.getenv("HF_MODEL", "") or "meta-llama/Meta-Llama-3-8B-Instruct").strip()


def _post_with_retries(url: str, headers: dict, payload: dict, timeout: int = 60) -> Tuple[bool, str]:
    last_err = ""
    for attempt in range(4):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = f"HF error {r.status_code}: {r.text[:400]}"
                time.sleep(1.0 + attempt * 1.5)
                continue
            if r.status_code >= 400:
                return False, f"HF error {r.status_code}: {r.text[:400]}"
            return True, r.text
        except Exception as e:
            last_err = str(e)
            time.sleep(1.0 + attempt * 1.5)
    return False, last_err or "HF transient error"


def _hf_router_chat(messages: List[Dict[str, str]]) -> Tuple[bool, str]:
    token = os.getenv("HF_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"model": _hf_model(), "messages": messages, "temperature": 0.2, "max_tokens": 250}
    ok, raw = _post_with_retries(HF_ROUTER_CHAT_URL, headers, payload, timeout=60)
    if not ok:
        return False, raw
    try:
        data = json.loads(raw)
        txt = (((data.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
        return True, str(txt).strip()
    except Exception:
        return False, f"Bad HF response: {raw[:400]}"


# ============================================================
# younchat (dashboard) — STRICTLY SNAPSHOT GROUNDED
# ============================================================
def _younchat_rules(q: str, snap: Dict[str, Any]) -> str:
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

    def fmt0(x: float) -> str:
        try:
            return f"{float(x):,.0f}"
        except Exception:
            return str(x)

    if not t:
        return "Hello 👋🏽 Ask: pot • contributions • loans • fines • repayments • interest • attendance"

    if "pot" in t:
        return f"Hello 👋🏽 Pot (Session {session_id}): **{fmt0(pot)}** • Paid: **{members_paid}/{total_members}** • Cycle: **{fmt0(cycle_total)}**"
    if "contribution" in t or "cycle" in t:
        return f"Hello 👋🏽 Cycle contributions (Session {session_id}): **{fmt0(cycle_total)}** • Paid: **{members_paid}/{total_members}**"
    if "attendance" in t or "present" in t or "absent" in t:
        if attendance_total == 0:
            return f"Hello 👋🏽 Attendance (Session {session_id}): **no records yet**"
        absent = max(int(attendance_total) - int(attendance_present), 0)
        return f"Hello 👋🏽 Attendance (Session {session_id}): Present **{attendance_present}**, Absent **{absent}**, Marked **{attendance_total}**"
    if "loan" in t:
        if loans_active_count == 0:
            return "Hello 👋🏽 Loans: **no active loans** in the snapshot."
        return f"Hello 👋🏽 Loans: Active **{loans_active_count}** • Active principal total **{fmt0(loans_active_total)}**"
    if "fine" in t:
        return f"Hello 👋🏽 Fines total (snapshot): **{fmt0(fines_total)}**"
    if "repay" in t or "payment" in t:
        return f"Hello 👋🏽 Repayments total (snapshot): **{fmt0(repayments_total)}**"
    if "interest" in t:
        return f"Hello 👋🏽 Interest total (snapshot): **{fmt0(interest_total)}**"

    return "Hello 👋🏽 Ask: pot • contributions • loans • fines • repayments • interest • attendance"


def _nav_to(page_name: str):
    st.session_state["page"] = str(page_name)
    st.rerun()


def _render_dashboard_younchat(snapshot: Dict[str, Any]):
    st.subheader("💬 younchat — Dashboard AI", anchor=False)

    # ✅ CLEAN: no paragraphs, no “Try…” wall of text, no debug snapshot by default
    # Optional nav
    if ENABLE_DASH_NAV:
        with st.expander("Quick actions", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Dashboard", use_container_width=True):
                    _nav_to("Dashboard")
                if st.button("Contributions", use_container_width=True):
                    _nav_to("Contributions")
                if st.button("Loans", use_container_width=True):
                    _nav_to("Loans")
            with c2:
                if st.button("Payouts", use_container_width=True):
                    _nav_to("Payouts")
                if st.button("AI Risk Panel", use_container_width=True):
                    _nav_to("AI Risk Panel")
                if st.button("Minutes & Attendance", use_container_width=True):
                    _nav_to("Minutes & Attendance")

    q = st.text_input("Ask younchat…", placeholder="e.g., pot • loans • fines • web: your question", key="dash_youn_q")
    ask = st.button("Ask", key="dash_youn_ask", use_container_width=True)

    if ask:
        if _is_web_query(q):
            if not _has_tavily_key():
                st.session_state["dash_youn_a"] = "Hello 👋🏽 Web search is not configured (missing TAVILY_API_KEY)."
                st.session_state["dash_youn_sources"] = []
            else:
                tav = _tavily_search_cached(_strip_web_prefix(q), max_results=5)
                summary, sources = _format_web_results(tav)
                st.session_state["dash_youn_a"] = summary
                st.session_state["dash_youn_sources"] = sources
        else:
            # Strictly grounded answer
            grounded = _younchat_rules(q, snapshot)

            # Optional HF to rewrite style ONLY (still grounded)
            if _hf_enabled() and q and not _is_web_query(q):
                try:
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                "Rewrite the assistant answer to sound modern and friendly, but keep ALL numbers and facts exactly the same. "
                                "Do not add any new facts."
                            ),
                        },
                        {"role": "user", "content": f"ANSWER:\n{grounded}"},
                    ]
                    ok, txt = _hf_router_chat(messages)
                    st.session_state["dash_youn_a"] = txt if ok and txt else grounded
                except Exception:
                    st.session_state["dash_youn_a"] = grounded
            else:
                st.session_state["dash_youn_a"] = grounded

            st.session_state["dash_youn_sources"] = []

    a = st.session_state.get("dash_youn_a")
    if a:
        st.markdown(a)

    sources = st.session_state.get("dash_youn_sources") or []
    if sources:
        with st.expander("Sources", expanded=False):
            for s in sources[:5]:
                title = s.get("title") or "Source"
                url = s.get("url") or ""
                if url:
                    st.markdown(f"- [{title}]({url})")
                else:
                    st.markdown(f"- {title}")

    st.caption(f"Snapshot time (UTC): {snapshot.get('generated_at', '—')}")


# ============================================================
# MAIN DASHBOARD (CLEAN)
# ============================================================
def render_dashboard(sb_anon, sb_service=None, schema: str = "public"):
    # ✅ CLEAN: no extra “marketing” text
    sb_read = sb_service if sb_service is not None else sb_anon

    session_id, session_msg = _ensure_current_session(sb_anon=sb_anon, sb_service=sb_service, schema=schema)
    st.caption(f"📌 Session: {session_msg}")

    # Members
    members_rows = _safe_select(sb_read, schema, "members", "id", limit=5000, show_error=False)
    total_members = len(members_rows) if members_rows else 0

    # Contributions (session)
    contrib_rows: List[Dict[str, Any]] = []
    if session_id is not None:
        contrib_rows = _safe_select(
            sb_read,
            schema,
            "contributions",
            "id,member_id,amount,session_id,created_at",
            session_id=int(session_id),
            limit=20000,
            show_error=False,
        )
    cycle_total = _sum_amount(contrib_rows, "amount")
    members_paid = _count_distinct(contrib_rows, "member_id")
    current_pot = float(cycle_total)

    # Attendance (session)
    attendance_rows: List[Dict[str, Any]] = []
    if session_id is not None and _table_readable(sb_read, schema, "attendance"):
        attendance_rows = _safe_select(
            sb_read,
            schema,
            "attendance",
            "member_id,present,session_id,created_at",
            session_id=int(session_id),
            limit=10000,
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
            limit=30000,
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
        fines_rows = _safe_select(sb_read, schema, "fines", "amount,created_at", limit=30000, show_error=False)
        fines_total = _sum_amount(fines_rows, "amount")

    # Repayments
    repayments_total = 0.0
    if _table_readable(sb_read, schema, "loan_payments"):
        pay_rows = _safe_select(sb_read, schema, "loan_payments", "amount,created_at", limit=30000, show_error=False)
        repayments_total = _sum_amount(pay_rows, "amount")

    # Interest ledger
    interest_total = 0.0
    if _table_readable(sb_read, schema, "interest_ledger"):
        i_rows = _safe_select(sb_read, schema, "interest_ledger", "amount,created_at", limit=30000, show_error=False)
        interest_total = _sum_amount(i_rows, "amount")

    # KPI display
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

    st.subheader("🧾 Attendance (session)", anchor=False)
    if session_id is None:
        st.info("No session selected.")
    else:
        if attendance_total == 0:
            st.info("No attendance records for this session yet.")
        else:
            absent = max(attendance_total - attendance_present, 0)
            st.write(f"Present: **{attendance_present}** • Absent: **{absent}** • Marked: **{attendance_total}**")

    st.divider()

    # Snapshot for younchat
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

    _render_dashboard_younchat(snapshot)
