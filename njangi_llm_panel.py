
# njangi_llm_panel.py ✅ SINGLE COMPLETE FILE — younchat reads your DB (members = source of truth)
# =============================================================================
# 💬 younchat — DB-TOOLS FIRST + Manifold State + Foundation Reasoner (HF) + Optional Tavily
#
# HARD RULES:
#   1) The ONLY intro message must be EXACTLY:
#        "Hello 👋🏽 I’m younchat — your Njangi assistant."
#   2) DB commands are answered ONLY from DB (no HF for DB numbers)
#   3) EVERY message that is NOT DB-related is routed to HF foundation model (if HF_TOKEN exists)
#   4) Start every answer with "Hello 👋🏽"
#
# ✅ Works with the FIXED circular-import-safe njangi_actions_agent.py:
#   - Uses READ_TOOLS + WRITE_TOOLS + execute_tool
#   - Adds: JSON tool runner + staged confirmations (writes still require confirm)
#   - Member id messages like "member_id=4" trigger DB member intelligence summary
# =============================================================================

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

try:
    from postgrest.exceptions import APIError
except Exception:
    APIError = Exception  # type: ignore

# =============================================================================
# ✅ TOOLS LAYER
# =============================================================================
from njangi_actions_agent import READ_TOOLS, WRITE_TOOLS, execute_tool  # type: ignore

# =============================================================================
# 0) CONSTANTS
# =============================================================================
HF_ROUTER_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"
HF_ROUTER_COMPLETIONS_URL = "https://router.huggingface.co/v1/completions"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"

HF_ALLOWED_MODELS: List[str] = [
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.2",
]

ALL_TOOLS: Dict[str, Any] = {**dict(READ_TOOLS.items()), **dict(WRITE_TOOLS.items())}

# =============================================================================
# 1) CORE HELPERS
# =============================================================================
def iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _intro_only() -> str:
    return "Hello 👋🏽 I’m younchat — your Njangi assistant."


def _force_hello_prefix(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "Hello 👋🏽"
    if t == _intro_only():
        return t
    if not t.lower().startswith("hello"):
        return "Hello 👋🏽 " + t
    return t


def _clean(s: Any) -> str:
    return ("" if s is None else str(s)).strip()


def _lc(s: Any) -> str:
    return _clean(s).lower()


def _api_msg(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or payload)
        return str(payload)
    return str(e)


def _looks_like_code_output(txt: str) -> bool:
    t = (txt or "").strip().lower()
    if not t:
        return False
    if "```" in t:
        return True
    markers = [
        "import ",
        "def ",
        "class ",
        "select ",
        "create table",
        "alter table",
        "drop table",
        "insert into",
        "update ",
        "delete from",
    ]
    return any(m in t for m in markers)


def safe_json_from_text(txt: str) -> Dict[str, Any]:
    """Extract JSON from text safely (handles cases where model returns extra words)."""
    if not txt:
        return {}
    t = txt.strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass

    try:
        i = t.find("{")
        j = t.rfind("}")
        if i >= 0 and j > i:
            obj = json.loads(t[i : j + 1])
            return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    return {}


def _fmt_money(x: Any) -> str:
    try:
        v = pd.to_numeric(x, errors="coerce")
        if pd.isna(v):
            return "0.00"
        return f"{float(v):,.2f}"
    except Exception:
        return "0.00"


# =============================================================================
# 2) MEMBER ID PARSING (member_id=4 etc.)
# =============================================================================
_MEMBER_ID_PATTERNS = [
    re.compile(r"\bmember[_\s-]?id\s*[:=#]?\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\bmember\s*#?\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\bid\s*[:=#]?\s*(\d+)\b", re.IGNORECASE),
]


def _extract_member_id(text: str) -> Optional[str]:
    t = _clean(text)
    if not t:
        return None
    if t.isdigit():
        return t
    for pat in _MEMBER_ID_PATTERNS:
        m = pat.search(t)
        if m:
            return str(m.group(1))
    return None


def _is_member_id_only_request(text: str) -> bool:
    t = _lc(text)
    mid = _extract_member_id(t)
    if not mid:
        return False
    if t.strip().isdigit():
        return True
    has_member_token = any(tok in t for tok in ["member", "member_id", "member id", "id="])
    other = any(
        tok in t
        for tok in ["show ", "describe ", "tables", "members", "loans", "kpi", "web:", "internet:", "tavily:", "verify "]
    )
    return has_member_token and (not other)


# =============================================================================
# 3) INTENTS / ROUTING
# =============================================================================
def _wants_help(text: str) -> bool:
    return _lc(text) in {"help", "/help", "commands", "options"}


def _wants_internet(text: str) -> bool:
    t = _lc(text)
    return t.startswith("web:") or t.startswith("internet:") or t.startswith("tavily:")


def _strip_web_prefix(q: str) -> str:
    return re.sub(r"^(web:|internet:|tavily:)\s*", "", (q or "").strip(), flags=re.IGNORECASE).strip()


def _wants_tables_list(text: str) -> bool:
    return _lc(text) in {"tables", "relations", "views", "list tables", "list views"}


def _wants_list_members(text: str) -> bool:
    t = _lc(text)
    phrases = [
        "list all members",
        "list members",
        "show all members",
        "show members",
        "members list",
        "all members",
        "member list",
        "who are the members",
        "member ids",
        "list members id",
    ]
    return t in {"members", "member"} or any(p in t for p in phrases)


def _wants_kpis(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["kpi", "kpis", "finance kpi", "finance kpis", "dashboard kpi"])


def _wants_loans(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["loan", "loans", "overdue", "dpd", "repay", "repayment"])


def _wants_describe(text: str) -> bool:
    t = _lc(text)
    return t.startswith("describe ") or t.startswith("columns ") or t.startswith("cols ") or t.startswith("schema ")


def _wants_show_table(text: str) -> bool:
    t = _lc(text)
    return t.startswith("show ") or t.startswith("preview ") or t.startswith("open ")


def _extract_relation_name(text: str) -> Optional[str]:
    t = _lc(text)
    t = re.sub(r"^(show|preview|open|describe|columns|cols|schema)\s+", "", t).strip()
    t = re.sub(r"^table\s+", "", t).strip()
    t = re.sub(r"[^\w]+$", "", t)
    if not t:
        return None
    return t.split()[0]


def _wants_verify_member(text: str) -> bool:
    t = _lc(text)
    return t.startswith("verify member ") or t.startswith("verify ")


def _extract_verify_member_id(text: str) -> Optional[str]:
    t = _lc(text)
    t = re.sub(r"^verify(\s+member)?\s+", "", t).strip()
    m = re.search(r"(\d+)", t)
    return m.group(1) if m else None


def _wants_db_actions(text: str) -> bool:
    t = _lc(text)
    verbs = [
        "create ",
        "add ",
        "record ",
        "approve ",
        "fine ",
        "pay ",
        "payout ",
        "disburse ",
        "mark paid",
        "new session",
        "open session",
        "make payment",
    ]
    return any(v in t for v in verbs)


def _is_db_command(text: str) -> bool:
    t = _lc(text)
    if not t:
        return False
    if _extract_member_id(t) is not None:
        return True
    if _wants_help(t) or _wants_tables_list(t) or _wants_list_members(t) or _wants_kpis(t) or _wants_loans(t):
        return True
    if _wants_show_table(t) or _wants_describe(t) or _wants_verify_member(t):
        return True
    finance_words = ["contribution", "payout", "loan", "interest", "overdue", "liquidity", "foundation", "risk", "health score", "total"]
    if any(w in t for w in finance_words):
        return True
    if _wants_db_actions(t):
        return True
    return False


# =============================================================================
# 4) INTERNET (Tavily) — NEVER for Njangi numbers
# =============================================================================
def _internet_enabled() -> bool:
    key = (os.getenv("TAVILY_API_KEY") or "").strip()
    mode = (os.getenv("INTERNET_MODE") or "").strip().lower()
    if mode == "off":
        return False
    return bool(key)


@st.cache_data(ttl=600, show_spinner=False)
def _tavily_search(query: str) -> Dict[str, Any]:
    key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not key:
        return {"ok": False, "error": "TAVILY_API_KEY missing", "results": []}
    payload = {
        "api_key": key,
        "query": query,
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": False,
        "include_raw_content": False,
    }
    try:
        r = requests.post(TAVILY_SEARCH_URL, json=payload, timeout=30)
        if r.status_code >= 400:
            return {"ok": False, "error": f"Tavily error {r.status_code}: {r.text[:300]}", "results": []}
        data = r.json() or {}
        results = data.get("results") or []
        clean = []
        for it in results:
            clean.append({"title": it.get("title"), "url": it.get("url"), "content": (it.get("content") or "")[:300]})
        return {"ok": True, "results": clean}
    except Exception as e:
        return {"ok": False, "error": str(e), "results": []}


# =============================================================================
# 5) FOUNDATION MODEL (HF Router) — Non-DB only
# =============================================================================
def _has_hf_token() -> bool:
    return bool((os.getenv("HF_TOKEN") or "").strip())


def _hf_force_mode() -> str:
    return (os.getenv("HF_FORCE_MODE") or "auto").strip().lower()


def _hf_model() -> str:
    requested = (os.getenv("HF_MODEL") or HF_ALLOWED_MODELS[0]).strip()
    return requested if requested in HF_ALLOWED_MODELS else HF_ALLOWED_MODELS[0]


def _post_with_retries(url: str, headers: dict, payload: dict, timeout: int = 60) -> Tuple[bool, str]:
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
    payload = {"model": model, "messages": messages, "temperature": 0.20, "max_tokens": 700}
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
    payload = {"model": model, "prompt": prompt, "temperature": 0.20, "max_tokens": 700}
    ok, raw = _post_with_retries(HF_ROUTER_COMPLETIONS_URL, headers, payload, timeout=timeout)
    if not ok:
        return False, raw
    try:
        data = json.loads(raw)
        text = ((data.get("choices") or [{}])[0].get("text") or "")
        return True, str(text).strip()
    except Exception:
        return False, f"Bad HF completions response: {raw[:600]}"


def _foundation_intent_system_prompt() -> str:
    return (
        "You are younchat, the assistant inside the Njangi system.\n\n"
        "STRICT RULES:\n"
        "1) Start every reply with exactly: \"Hello 👋🏽\"\n"
        "2) You are NOT allowed to invent or guess any Njangi financial numbers, balances, totals, dates, or member IDs.\n"
        "3) If the request needs real Njangi data, tell them the exact DB command to type next, OR ask exactly ONE short clarifying question.\n"
        "4) Do NOT output SQL, Python, code blocks, schema changes, or markdown fences.\n"
        "5) Keep replies short and system-focused.\n\n"
        "AVAILABLE DB COMMANDS YOU CAN RECOMMEND:\n"
        "- members\n"
        "- loans\n"
        "- finance kpis\n"
        "- tables\n"
        "- show <table>\n"
        "- describe <table>\n"
        "- verify member <id>\n"
        "- type a member id (example: 10) for that member’s intelligence summary\n\n"
        "OUTPUT FORMAT (always):\n"
        "Hello 👋🏽 <one-sentence helpful response>\n"
        "Intent: <what you think they want>\n"
        "Next: <one command OR one question>\n"
    )


def _foundation_intent_user_wrapper(user_message: str) -> str:
    msg = (user_message or "").strip()
    return (
        "User message:\n"
        f"\"{msg}\"\n\n"
        "Your task:\n"
        "- Decide what the member wants inside Njangi.\n"
        "- If it requires database data, tell them EXACTLY what DB command to type next.\n"
        "- If unclear, ask exactly ONE clarifying question.\n"
        "- Do not guess numbers.\n"
        "Return in the required output format.\n"
    )


def _hf_smalltalk_answer(question: str) -> Tuple[bool, str, str]:
    token = (os.getenv("HF_TOKEN") or "").strip()
    model = _hf_model()
    force = _hf_force_mode()
    if not token:
        return False, "HF_TOKEN missing", "hf:missing"

    sys = _foundation_intent_system_prompt()
    user = _foundation_intent_user_wrapper(question)

    if force == "completions":
        prompt = f"{sys}\n\n{user}\nAssistant:"
        ok, txt = _hf_router_completions(model, token, prompt)
        return (ok, txt, f"hf:intent:completions:{model}") if ok else (False, txt, f"hf:intent:completions_failed:{model}")

    messages = [{"role": "system", "content": sys}, {"role": "user", "content": user}]
    ok, txt = _hf_router_chat(model, token, messages)
    return (ok, txt, f"hf:intent:chat:{model}") if ok else (False, txt, f"hf:intent:chat_failed:{model}")


# =============================================================================
# 6) TOOL RUNNER + CONFIRMATIONS (writes staged)
# =============================================================================
@dataclass
class ToolRun:
    ok: bool
    message: str
    data: Any = None


def _run_read_tool(sb_anon, sb_service, schema: str, tool: str, params: Dict[str, Any]) -> ToolRun:
    try:
        if tool == "members":
            res = execute_tool("members", sb_anon, sb_service, schema, limit=int(params.get("limit") or 5000))
            return ToolRun(True, "members ok", res)
        if tool == "tables":
            res = execute_tool("tables")
            return ToolRun(True, "tables ok", res)
        if tool == "show_table":
            rel = _clean(params.get("relation"))
            lim = int(params.get("limit") or 2000)
            order_by = params.get("order_by") or None
            order_asc = bool(params.get("order_asc") or False)
            res = execute_tool("show_table", sb_anon, sb_service, schema, relation=rel, limit=lim, order_by=order_by, order_asc=order_asc)
            return ToolRun(True, "show_table ok", res)
        if tool == "describe_table":
            rel = _clean(params.get("relation"))
            res = execute_tool("describe_table", sb_anon, sb_service, schema, relation=rel)
            return ToolRun(True, "describe_table ok", res)
        if tool == "loans":
            mid = params.get("member_id")
            res = execute_tool("loans", sb_anon, sb_service, schema, member_id=str(mid) if mid is not None else None)
            return ToolRun(True, "loans ok", res)
        if tool == "member_summary":
            mid = _clean(params.get("member_id"))
            res = execute_tool("member_summary", sb_anon, sb_service, schema, member_id=mid)
            return ToolRun(True, "member_summary ok", res)

        res = execute_tool(tool, sb_anon, sb_service, schema, **(params or {}))
        return ToolRun(True, f"{tool} ok", res)
    except Exception as e:
        return ToolRun(False, f"{tool} failed: {e}", None)


def _stage_write_action(tool: str, params: Dict[str, Any]) -> ToolRun:
    st.session_state.setdefault("pending_confirmations", [])
    st.session_state["pending_confirmations"].append({"created_at": iso_utc(), "tool": tool, "params": params})
    return ToolRun(True, f"Staged write: {tool} (confirm to execute)", {"tool": tool, "params": params})


def execute_confirmed_action(sb_anon, sb_service, schema: str, tool: str, params: Dict[str, Any]) -> ToolRun:
    if tool not in WRITE_TOOLS:
        return ToolRun(False, f"Write tool not available: {tool}", None)
    # NOTE: intentionally not wired yet (safe default)
    return ToolRun(False, f"Write tool exists but execution is not wired yet: {tool}", None)


def _render_pending_confirmations(sb_anon, sb_service, schema: str):
    pending = st.session_state.get("pending_confirmations", [])
    if not pending:
        return

    st.markdown("### ✅ Pending Actions (Confirm to Execute)")
    pending = list(reversed(pending))

    for idx, p in enumerate(pending):
        tool = _clean(p.get("tool"))
        params = p.get("params") if isinstance(p.get("params"), dict) else {}
        created_at = _clean(p.get("created_at"))

        with st.expander(f"Confirm: {tool} • {created_at}", expanded=(idx == 0)):
            st.json({"tool": tool, "params": params}, expanded=False)

            c1, c2 = st.columns(2)
            with c1:
                if st.button(f"CONFIRM & EXECUTE ({tool})", key=f"y_confirm_{idx}", type="primary", use_container_width=True):
                    tr = execute_confirmed_action(sb_anon, sb_service, schema, tool, params)
                    (st.success if tr.ok else st.error)(tr.message)

                    try:
                        orig = list(reversed(st.session_state.get("pending_confirmations", [])))
                        orig.remove(p)
                        st.session_state["pending_confirmations"] = list(reversed(orig))
                    except Exception:
                        st.session_state["pending_confirmations"] = []
                    st.rerun()

            with c2:
                if st.button("Cancel", key=f"y_cancel_{idx}", use_container_width=True):
                    try:
                        orig = list(reversed(st.session_state.get("pending_confirmations", [])))
                        orig.remove(p)
                        st.session_state["pending_confirmations"] = list(reversed(orig))
                    except Exception:
                        st.session_state["pending_confirmations"] = []
                    st.rerun()


def _run_tool_payload(sb_anon, sb_service, schema: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    out = {"reads": [], "staged": [], "asks": None, "errors": []}
    if not isinstance(payload, dict) or not payload:
        out["errors"].append("Invalid JSON payload.")
        return out

    actions: List[Dict[str, Any]] = []
    if "actions" in payload and isinstance(payload["actions"], list):
        actions = [a for a in payload["actions"] if isinstance(a, dict)]
    elif "tool" in payload:
        actions = [payload]
    else:
        out["errors"].append("Payload must contain 'tool' or 'actions'.")
        return out

    for act in actions:
        tool = _clean(act.get("tool"))
        params = act.get("params") if isinstance(act.get("params"), dict) else {}

        if not tool:
            out["errors"].append("Missing tool name.")
            continue

        if tool == "ask_one":
            q = _clean((params or {}).get("question"))
            out["asks"] = q or "I need one detail to proceed. What’s missing?"
            continue

        if tool in READ_TOOLS:
            tr = _run_read_tool(sb_anon, sb_service, schema, tool, params)
            out["reads"].append({"tool": tool, "ok": tr.ok, "message": tr.message, "data": tr.data})
            continue

        if tool in WRITE_TOOLS:
            tr = _stage_write_action(tool, params)
            out["staged"].append({"tool": tool, "ok": tr.ok, "message": tr.message, "data": tr.data})
            continue

        out["errors"].append(f"Unknown tool: {tool}")

    return out


# =============================================================================
# 7) MAIN UI
# =============================================================================
def render_njangi_llm_panel(sb_anon, sb_service, schema: str) -> None:
    st.subheader("💬 younchat", anchor=False)

    internet_on = _internet_enabled()
    hf_on = _has_hf_token()

    if "younchat_write_mode" not in st.session_state:
        st.session_state["younchat_write_mode"] = False

    with st.expander("⚙️ Chat Settings", expanded=False):
        st.write("**Schema**:", schema)
        st.write("**HF models (locked)**:", ", ".join(HF_ALLOWED_MODELS))
        st.write("**HF_TOKEN present**:", "✅ Yes" if hf_on else "❌ No")
        st.write("**HF_FORCE_MODE**:", (os.getenv("HF_FORCE_MODE") or "auto"))
        st.write("**Internet**:", "✅ ON" if internet_on else "❌ OFF")
        st.caption("DB integrity: DB commands are DB-only. Non-DB routes to HF (intent & next-step). Writes require confirmation.")

    colW1, colW2 = st.columns([1, 2], gap="small")
    with colW1:
        st.session_state["younchat_write_mode"] = st.toggle("Write Mode ON", value=st.session_state["younchat_write_mode"])
    with colW2:
        if st.session_state["younchat_write_mode"]:
            st.warning("Write Mode is ON. Writes still require confirmation.", icon="⚠️")
        else:
            st.info("Write Mode is OFF (reads only).", icon="ℹ️")

    if "younchat_history" not in st.session_state:
        st.session_state["younchat_history"] = [{"role": "assistant", "content": _intro_only()}]

    # Render chat history
    for m in st.session_state["younchat_history"]:
        with st.chat_message("assistant" if m.get("role") == "assistant" else "user"):
            st.markdown(m.get("content", ""))

    colA, colB = st.columns([1, 1], gap="small")
    if colA.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    if colB.button("🧹 Clear chat", use_container_width=True):
        st.session_state["younchat_history"] = [{"role": "assistant", "content": _intro_only()}]
        st.session_state.pop("younchat_last_member_id", None)
        st.session_state.pop("pending_confirmations", None)
        st.rerun()

    _render_pending_confirmations(sb_anon, sb_service, schema)

    with st.expander("🧰 Paste tool JSON (optional)", expanded=False):
        tool_json = st.text_area("Tool JSON", height=120, placeholder='{"tool":"members","params":{"limit":200}}')
        if st.button("Run tool JSON", use_container_width=True):
            payload = safe_json_from_text(tool_json)
            if not payload:
                st.error("Invalid JSON.")
            else:
                is_write_payload = (
                    (payload.get("tool") in WRITE_TOOLS)
                    or any((a.get("tool") in WRITE_TOOLS) for a in (payload.get("actions") or []) if isinstance(a, dict))
                )
                if is_write_payload and (not st.session_state["younchat_write_mode"]):
                    st.error("Write Mode is OFF. Turn it ON to stage write actions.")
                else:
                    ran = _run_tool_payload(sb_anon, sb_service, schema, payload)
                    if ran.get("asks"):
                        st.info(_force_hello_prefix(ran["asks"]))
                    for r in ran.get("reads", []):
                        (st.success if r.get("ok") else st.error)(f"{r['tool']}: {r['message']}")
                        if r.get("data") is not None:
                            st.json(r["data"], expanded=False)
                    for s in ran.get("staged", []):
                        st.warning(f"Staged: {s['tool']} • {s['message']}")
                    for e in ran.get("errors", []):
                        st.error(e)
                    st.rerun()

    q = st.chat_input("Type your message…")
    if not q:
        return

    st.session_state["younchat_history"].append({"role": "user", "content": q})
    with st.chat_message("user"):
        st.markdown(q)

    detected_id = _extract_member_id(q)
    if detected_id:
        st.session_state["younchat_last_member_id"] = detected_id
    member_id_focus = st.session_state.get("younchat_last_member_id")

    used_source = "local"
    answer = ""
    df_show: Optional[pd.DataFrame] = None
    df_title: Optional[str] = None

    is_db = _is_db_command(q)

    # INTERNET
    if _wants_internet(q):
        used_source = "tavily" if internet_on else "tavily:off"
        if not internet_on:
            answer = "Hello 👋🏽 Internet is OFF. Set TAVILY_API_KEY and INTERNET_MODE=on."
        else:
            query = _strip_web_prefix(q)
            res = _tavily_search(query)
            if not res.get("ok"):
                answer = f"Hello 👋🏽 Internet error: {res.get('error')}"
            else:
                items = res.get("results") or []
                if not items:
                    answer = "Hello 👋🏽 No web results found."
                else:
                    lines = ["Hello 👋🏽 Here are the top web results:\n"]
                    for it in items[:5]:
                        title = it.get("title") or "Source"
                        url = it.get("url") or ""
                        snippet = (it.get("content") or "").strip()
                        lines.append(f"- [{title}]({url})" if url else f"- {title}")
                        if snippet:
                            lines.append(f"  - {snippet[:180]}…")
                    answer = "\n".join(lines)

    # NON-DB => HF intent helper
    elif not is_db:
        if hf_on:
            ok, txt, used = _hf_smalltalk_answer(q)
            if ok and txt and (not _looks_like_code_output(txt)):
                used_source = used
                answer = txt
            else:
                used_source = f"{used}:fallback_local"
                answer = (
                    "Hello 👋🏽 I couldn’t get a clean response from the foundation model.\n"
                    "Intent: Foundation model failed\n"
                    "Next: Try again, or type: members / loans / finance kpis."
                )
        else:
            used_source = "local:no_hf"
            answer = (
                "Hello 👋🏽 HF_TOKEN is missing, so non-DB answers are OFF.\n"
                "Intent: Non-DB request\n"
                "Next: Add HF_TOKEN (or use: members / loans / finance kpis)."
            )

    # DB COMMANDS => DB-only tools
    else:
        if _wants_help(q):
            used_source = "help"
            answer = (
                "Hello 👋🏽 Commands:\n\n"
                "- **members**\n"
                "- type **10** or **member_id=10** (member intelligence)\n"
                "- **verify member 10**\n"
                "- **loans** (optionally include member id)\n"
                "- **finance kpis**\n"
                "- **tables**\n"
                "- **show <table>** (example: show contributions)\n"
                "- **describe <table>** (example: describe loans)\n"
                "- **web: <topic>** (internet)\n"
            )

        elif _wants_tables_list(q):
            used_source = "tool:tables"
            tr = _run_read_tool(sb_anon, sb_service, schema, "tables", {})
            data = (tr.data or {}).get("data") if isinstance(tr.data, dict) else None
            df_show = pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame()
            df_title = "Allowed relations"
            answer = "Hello 👋🏽 Here are the allowed relations (tables/views):"

        elif _wants_list_members(q):
            used_source = "tool:members"
            tr = _run_read_tool(sb_anon, sb_service, schema, "members", {"limit": 5000})
            rows = (tr.data or {}).get("data") if isinstance(tr.data, dict) else []
            df_show = pd.DataFrame(rows) if isinstance(rows, list) else pd.DataFrame()
            df_title = "members (truth)"
            if df_show.empty:
                answer = "Hello 👋🏽 I couldn’t read members (RLS blocked or empty)."
            else:
                lines = ["Hello 👋🏽 Here are members (from `members`):\n"]
                for _, r in df_show.head(60).iterrows():
                    lines.append(f"- **{_clean(r.get('member_id'))}** • {_clean(r.get('member_name'))}")
                if len(df_show) > 60:
                    lines.append(f"\n… and {len(df_show) - 60} more (see table).")
                answer = "\n".join(lines)

        elif _wants_kpis(q):
            used_source = "tool:finance_kpis"
            if "finance_kpis" in READ_TOOLS:
                tr = _run_read_tool(sb_anon, sb_service, schema, "finance_kpis", {"limit": 200})
                rows = (tr.data or {}).get("data") if isinstance(tr.data, dict) else []
                df_show = pd.DataFrame(rows) if isinstance(rows, list) else pd.DataFrame()
                df_title = "Finance KPIs"
                answer = "Hello 👋🏽 Finance KPIs (DB-grounded):"
            else:
                answer = "Hello 👋🏽 `finance_kpis` tool is not available. If you have `v_finance_kpis`, add it into actions_agent."

        elif _wants_loans(q):
            used_source = "tool:loans"
            mid = _extract_member_id(q) or member_id_focus
            tr = _run_read_tool(sb_anon, sb_service, schema, "loans", {"member_id": mid} if mid else {})
            rows = (tr.data or {}).get("data") if isinstance(tr.data, dict) else []
            df_show = pd.DataFrame(rows) if isinstance(rows, list) else pd.DataFrame()
            df_title = f"Loans{' (member_id=' + str(mid) + ')' if mid else ''}"
            answer = "Hello 👋🏽 Loans (DB-grounded):" if not df_show.empty else "Hello 👋🏽 No loan rows returned."

        elif _wants_describe(q):
            rel = _extract_relation_name(q)
            if not rel:
                answer = "Hello 👋🏽 Say: **describe loans** (or describe <table/view>)."
            else:
                used_source = "tool:describe_table"
                tr = _run_read_tool(sb_anon, sb_service, schema, "describe_table", {"relation": rel})
                rows = (tr.data or {}).get("data") if isinstance(tr.data, dict) else []
                df_show = pd.DataFrame(rows) if isinstance(rows, list) else pd.DataFrame()
                df_title = f"Columns: {rel}"
                answer = f"Hello 👋🏽 Columns for **{rel}**:"

        elif _wants_show_table(q):
            rel = _extract_relation_name(q)
            if not rel:
                answer = "Hello 👋🏽 Say: **show contributions** (or show <table/view>)."
            else:
                used_source = "tool:show_table"
                tr = _run_read_tool(sb_anon, sb_service, schema, "show_table", {"relation": rel, "limit": 2000})
                rows = (tr.data or {}).get("data") if isinstance(tr.data, dict) else []
                df_show = pd.DataFrame(rows) if isinstance(rows, list) else pd.DataFrame()
                df_title = f"Preview: {rel}"
                answer = f"Hello 👋🏽 Preview of **{rel}**:"

        elif _wants_verify_member(q):
            mid = _extract_verify_member_id(q) or member_id_focus
            if not mid:
                answer = "Hello 👋🏽 Say: **verify member 10**"
            else:
                used_source = "tool:member_summary"
                tr = _run_read_tool(sb_anon, sb_service, schema, "member_summary", {"member_id": str(mid)})
                data = (tr.data or {}).get("data") if isinstance(tr.data, dict) else {}
                answer = "Hello 👋🏽 Verify (DB-grounded member summary):"
                if isinstance(data, dict):
                    st.session_state["__last_verify_payload"] = data

        elif _wants_db_actions(q):
            used_source = "db:actions_guard"
            if not st.session_state["younchat_write_mode"]:
                answer = (
                    "Hello 👋🏽 This looks like a WRITE request.\n"
                    "Next: Turn **Write Mode ON**, then paste tool JSON in the Tool JSON section."
                )
            else:
                answer = (
                    "Hello 👋🏽 Write Mode is ON.\n"
                    "Next: Paste a tool JSON action (example):\n"
                    '{ "tool": "<write_tool>", "params": { ... } }'
                )

        else:
            if _is_member_id_only_request(q) or (_extract_member_id(q) is not None and _extract_member_id(q) == member_id_focus):
                mid = str(_extract_member_id(q) or member_id_focus or "").strip()
                used_source = "tool:member_summary"
                if not mid:
                    answer = "Hello 👋🏽 Type a member id (example: **10**) to get that member’s intelligence summary."
                else:
                    tr = _run_read_tool(sb_anon, sb_service, schema, "member_summary", {"member_id": mid})
                    payload = tr.data if isinstance(tr.data, dict) else {}
                    data = payload.get("data") if isinstance(payload, dict) else None

                    if not tr.ok or not isinstance(data, dict):
                        answer = "Hello 👋🏽 I couldn’t fetch that member summary (RLS blocked or missing member). Type **members** to confirm IDs."
                    else:
                        mem = (data.get("member") or {})
                        totals = (data.get("totals") or {})
                        derived = (data.get("derived") or {})

                        lines = []
                        lines.append("Hello 👋🏽 Member Financial Intelligence (DB-grounded)\n")
                        lines.append("1️⃣ Current Situation")
                        lines.append(f"- Member: **{_clean(mem.get('member_name'))}** (member_id={_clean(mem.get('member_id'))})")
                        lines.append(f"- Contributions total: **{_fmt_money(totals.get('contributions_total'))}**")
                        lines.append(f"- Foundation total: **{_fmt_money(totals.get('foundation_total'))}**")
                        lines.append(f"- Fines total: **{_fmt_money(totals.get('fines_total'))}**")
                        lines.append(f"- Active loan balance: **{_fmt_money(totals.get('active_loan_balance'))}**")
                        lines.append(f"- Active unpaid interest: **{_fmt_money(totals.get('active_unpaid_interest'))}**")
                        lines.append(f"- Active loan count: **{int(totals.get('active_loan_count') or 0)}**")
                        lines.append(f"- Overdue loan count: **{int(totals.get('overdue_loan_count') or 0)}**")

                        lines.append("\n2️⃣ Risk Assessment")
                        lines.append(f"- Risk grade: **{_clean(derived.get('risk_grade')) or '—'}**")
                        ratio = derived.get("exposure_to_contributions_ratio")
                        if isinstance(ratio, (int, float)):
                            lines.append(f"- Exposure ÷ Contributions: **{ratio * 100:.1f}%**")
                        else:
                            lines.append("- Exposure ÷ Contributions: **—**")

                        lines.append("\n3️⃣ Next Best Actions")
                        lines.append("- Type: **loans** (or 'loans member_id=10') to see loan rows.")
                        lines.append("- Type: **verify member 10** to re-run totals summary.")
                        answer = "\n".join(lines)
            else:
                used_source = "db:guard"
                answer = (
                    "Hello 👋🏽 I can answer using your real Njangi database only.\n\n"
                    "Try:\n"
                    "- **members**\n"
                    "- **loans**\n"
                    "- **finance kpis**\n"
                    "- **tables**\n"
                    "- **show contributions**\n"
                    "- **describe loans**\n"
                    "- **verify member 10**\n"
                    "- Or type **10** / **member_id=10** (member intelligence)\n"
                    "- **web: <topic>** (internet)\n"
                )

    answer = _force_hello_prefix(answer)

    st.session_state["younchat_history"].append({"role": "assistant", "content": answer})
    with st.chat_message("assistant"):
        st.markdown(answer)
        if df_show is not None and df_title:
            with st.expander(df_title, expanded=False):
                st.dataframe(df_show, use_container_width=True, hide_index=True)

        if st.session_state.get("__last_verify_payload"):
            with st.expander("Verify payload (raw)", expanded=False):
                st.json(st.session_state.get("__last_verify_payload"), expanded=False)

    st.caption(
        f"Source used: {used_source} • member_id: {member_id_focus or '—'} • "
        f"Internet: {'ON' if internet_on else 'OFF'} • "
        f"HF_TOKEN: {'ON' if hf_on else 'OFF'} • "
        f"Write Mode: {'ON' if st.session_state['younchat_write_mode'] else 'OFF'}"
    )
