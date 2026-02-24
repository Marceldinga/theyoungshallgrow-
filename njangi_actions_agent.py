# njangi_llm_panel.py ✅ SINGLE COMPLETE FILE — younchat reads your DB (members = source of truth)
# =============================================================================
# 💬 younchat — DB-TOOLS FIRST + Manifold State + Foundation Reasoner (HF) + Optional Tavily
# + ✅ ONE AGENT: tools layer integrated (READ + WRITE w/ confirm)
#
# HARD RULES:
#   1) The ONLY intro message must be EXACTLY:
#        "Hello 👋🏽 I’m younchat — your Njangi assistant."
#   2) DB commands are answered ONLY from DB (no HF for DB numbers)
#   3) EVERY message that is NOT DB-related is routed to HF foundation model (if HF_TOKEN exists)
#   4) Start every answer with "Hello 👋🏽"
#
# IMPORTANT ADDITION:
#   - Non-DB messages routed to HF use strict Njangi "intent & next step" prompt
#   - ✅ New: DB "do something" messages can be PLANNED by HF into tool JSON,
#     but execution is ONLY via DB tools and writes require confirmation.
# =============================================================================

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

# ✅ Import Njangi tools layer
from njangi_actions_agent import (
    READ_TOOLS,
    WRITE_TOOLS,
    ALL_TOOLS,
    ToolResult,
    safe_json_from_text,
    execute_confirmed_action,
    resolve_user_context,
    iso_utc,
)

# =============================================================================
# 0) CONSTANTS / ALLOWLISTS
# =============================================================================
HF_ROUTER_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"
HF_ROUTER_COMPLETIONS_URL = "https://router.huggingface.co/v1/completions"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"

HF_ALLOWED_MODELS: List[str] = [
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.2",
]

RELATIONS: Dict[str, Dict[str, Any]] = {
    "members": {"type": "table", "truth": True},
    "contributions": {"type": "table"},
    "foundation_contributions": {"type": "table"},
    "loans": {"type": "table"},
    "loan_payments": {"type": "table"},
    "fines": {"type": "table"},
    "payouts": {"type": "table"},
    "sessions": {"type": "table"},
    "minutes": {"type": "table"},
    "attendance": {"type": "table"},
    "signatures": {"type": "table"},
    "audit_log": {"type": "table"},
    "app_state": {"type": "table"},
    "loan_requests": {"type": "table"},
    "loan_repayments_pending": {"type": "table"},
    "profiles": {"type": "table"},
    "ml_training_data": {"type": "table"},
    "member_contribution_totals": {"type": "table"},
    "interest_ledger": {"type": "table"},
    # Views (optional)
    "v_finance_kpis": {"type": "view"},
    "v_member_financial_totals": {"type": "view"},
    "v_loans_with_member": {"type": "view"},
    "v_loan_payments_with_member": {"type": "view"},
    "v_contributions_with_member": {"type": "view"},
    "v_foundation_contributions_with_member": {"type": "view"},
    "v_payouts_with_member": {"type": "view"},
    "v_next_beneficiary": {"type": "view"},
    "v_loans_dpd": {"type": "view"},
    "v_loans_next_interest": {"type": "view"},
    "v_loans_next_interest_with_member": {"type": "view"},
    "v_loan_power_status": {"type": "view"},
    "v_attendance_all_time_per_member": {"type": "view"},
    "v_attendance_by_member_session": {"type": "view"},
    "v_attendance_member_totals": {"type": "view"},
    "v_attendance_with_member": {"type": "view"},
}

# =============================================================================
# 1) CORE HELPERS
# =============================================================================
def _intro_only() -> str:
    return "Hello 👋🏽 I’m younchat — your Njangi assistant."

def _force_hello_prefix(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "Hello 👋🏽"
    if not t.lower().startswith("hello"):
        return "Hello 👋🏽 " + t
    return t

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

def _api_msg(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload)
        return str(payload)
    return str(e)

def _clean(text: str) -> str:
    return (text or "").strip()

def _lc(text: str) -> str:
    return _clean(text).lower()

def _looks_like_code_output(txt: str) -> bool:
    t = (txt or "").strip().lower()
    if not t:
        return False
    if "```" in t:
        return True
    code_markers = [
        "import ", "def ", "class ", "select ", "create table", "alter table", "drop table",
        "insert into", "update ", "delete from"
    ]
    return any(m in t for m in code_markers)

# =============================================================================
# 2) DB ADAPTER (Supabase reads)
# =============================================================================
def _sb_select(
    sb_anon,
    sb_service,
    schema: str,
    relation: str,
    cols: str = "*",
    limit: int = 2000,
    filters: Optional[List[Tuple[str, str, Any]]] = None,
    order: Optional[Tuple[str, bool]] = None,
) -> pd.DataFrame:
    sb = sb_service or sb_anon
    if sb is None:
        return pd.DataFrame()
    if relation not in RELATIONS:
        return pd.DataFrame()

    def _apply(q):
        if filters:
            for col, op, val in filters:
                if val is None:
                    continue
                if op == "eq":
                    q = q.eq(col, val)
                elif op == "gte":
                    q = q.gte(col, val)
                elif op == "lte":
                    q = q.lte(col, val)
                elif op == "ilike":
                    q = q.ilike(col, val)
                elif op == "in":
                    q = q.in_(col, val)  # type: ignore
        if order:
            col, asc = order
            q = q.order(col, desc=not asc)
        return q

    try:
        q = sb.schema(schema).table(relation).select(cols).limit(limit)
        q = _apply(q)
        res = q.execute()
        return pd.DataFrame(getattr(res, "data", None) or [])
    except Exception:
        try:
            q = sb.table(relation).select(cols).limit(limit)
            q = _apply(q)
            res = q.execute()
            return pd.DataFrame(getattr(res, "data", None) or [])
        except Exception as e2:
            st.warning(f"Could not read {schema}.{relation}: {_api_msg(e2)}")
            return pd.DataFrame()

# =============================================================================
# 3) MEMBERS TRUTH (unchanged)
# =============================================================================
def _pick_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    return None

def _load_members_truth(sb_anon, sb_service, schema: str, limit: int = 3000) -> pd.DataFrame:
    df = _sb_select(sb_anon, sb_service, schema, "members", cols="*", limit=limit)
    if df.empty:
        return df

    id_col = _pick_col(df, ["id", "member_id"])
    name_col = _pick_col(df, ["name", "full_name"])
    display_col = _pick_col(df, ["display_name"])

    if not id_col:
        return pd.DataFrame()

    out = pd.DataFrame()
    out["member_id"] = df[id_col].astype(str)

    disp_clean = (
        df[display_col].astype(str).replace(["None", "nan", "NaN", "NULL", "null"], "").fillna("").str.strip()
        if display_col and display_col in df.columns
        else pd.Series([""] * len(df))
    )
    nm_clean = (
        df[name_col].astype(str).replace(["None", "nan", "NaN", "NULL", "null"], "").fillna("").str.strip()
        if name_col and name_col in df.columns
        else pd.Series([""] * len(df))
    )

    out["member_name"] = disp_clean.where(disp_clean != "", nm_clean).fillna("").replace("", "(no name)")

    try:
        out["_id_num"] = pd.to_numeric(out["member_id"], errors="coerce")
        out = out.sort_values(["_id_num", "member_id"], ascending=True).drop(columns=["_id_num"])
    except Exception:
        pass

    return out

# =============================================================================
# 4) INTENTS / PARSING
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

def _wants_help(text: str) -> bool:
    return _lc(text) in {"help", "/help", "commands", "options"}

def _wants_list_members(text: str) -> bool:
    t = _lc(text)
    phrases = [
        "list all members", "list members", "show all members", "show members",
        "members list", "all members", "member list", "who are the members",
        "list members id", "member ids",
    ]
    return t in {"members", "member"} or any(p in t for p in phrases)

def _wants_tables_list(text: str) -> bool:
    return _lc(text) in {"tables", "relations", "views", "list tables", "list views"}

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
    token = t.split()[0]
    return token if token in RELATIONS else None

def _wants_internet(text: str) -> bool:
    t = _lc(text)
    return t.startswith("web:") or t.startswith("internet:") or t.startswith("tavily:")

def _strip_web_prefix(q: str) -> str:
    return re.sub(r"^(web:|internet:|tavily:)\s*", "", (q or "").strip(), flags=re.IGNORECASE).strip()

def _wants_kpis(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["kpi", "kpis", "finance kpi", "finance kpis", "dashboard kpi"])

def _wants_loans(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["loan", "loans", "borrow", "repay", "repayment", "overdue", "dpd", "interest due"])

def _wants_verify_member(text: str) -> bool:
    t = _lc(text)
    return t.startswith("verify member ") or t.startswith("verify ")

def _extract_verify_member_id(text: str) -> Optional[str]:
    t = _lc(text)
    t = re.sub(r"^verify(\s+member)?\s+", "", t).strip()
    m = re.search(r"(\d+)", t)
    return m.group(1) if m else None

def _is_member_id_only_request(text: str) -> bool:
    t = _lc(text)
    mid = _extract_member_id(t)
    if not mid:
        return False
    if t.strip().isdigit():
        return True
    other_cmd_tokens = [
        "loans", "loan", "kpi", "kpis", "tables", "show ", "describe ", "verify ",
        "contributions", "fines", "payouts", "attendance", "minutes",
        "web:", "internet:", "tavily:",
        "create", "add", "record", "fine", "approve", "payout", "pay", "session"
    ]
    has_other = any(tok in t for tok in other_cmd_tokens)
    member_tokens = {"member", "member_id", "member-id", "member id", "id"}
    has_member_token = any(tok in t for tok in member_tokens)
    return has_member_token and not has_other

def _is_db_command(text: str) -> bool:
    t = _lc(text)
    if not t:
        return False

    # ✅ member id present => DB
    if _extract_member_id(t) is not None:
        return True

    if t in RELATIONS:
        return True
    if _wants_list_members(t) or _wants_loans(t) or _wants_kpis(t) or _wants_tables_list(t):
        return True
    if _wants_show_table(t) or _wants_describe(t) or _wants_help(t) or _wants_verify_member(t):
        return True

    finance_words = [
        "contribution", "contributions", "payout", "payouts", "loan", "loans",
        "repayment", "interest", "unpaid", "overdue", "balance", "exposure",
        "liquidity", "foundation", "kpi", "kpis", "risk", "health score", "grade",
        "total", "arrears", "dpd", "due",
        # ✅ action words (still DB)
        "create", "add", "record", "approve", "fine", "pay", "payout", "session"
    ]
    return any(w in t for w in finance_words)

def _wants_db_actions(text: str) -> bool:
    """
    If the user is asking to DO something (write), we let HF plan tool JSON.
    Execution stays DB-tool-only + confirmation.
    """
    t = _lc(text)
    verbs = [
        "create", "add", "record", "approve", "fine", "mark fine", "pay fine",
        "payout", "disburse", "request loan", "loan request", "make payment", "loan payment",
        "new session", "open session"
    ]
    return any(v in t for v in verbs)

# =============================================================================
# 5) INTERNET (Tavily) — NEVER used for Njangi numbers
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
# 6) HF Router (same as your original, plus an ACTION PLANNER prompt)
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
    payload = {"model": model, "messages": messages, "temperature": 0.10, "max_tokens": 600}
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
    payload = {"model": model, "prompt": prompt, "temperature": 0.10, "max_tokens": 600}
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
        "GOAL:\n"
        "Help members achieve their goal inside Njangi.\n\n"
        "STRICT RULES:\n"
        "1) Start every reply with exactly: \"Hello 👋🏽\"\n"
        "2) You are NOT allowed to invent or guess any Njangi financial numbers, balances, totals, dates, or member IDs.\n"
        "3) If the request needs real Njangi data, tell them the exact DB command to type next, OR ask exactly ONE short clarifying question.\n"
        "4) Do NOT output SQL, Python, code blocks, schema changes.\n"
        "5) Keep replies short and system-focused.\n\n"
        "OUTPUT FORMAT:\n"
        "Hello 👋🏽 <one-sentence helpful response>\n"
        "Intent: <what you think they want>\n"
        "Next: <one command OR one question>\n"
    )

def _foundation_intent_user_wrapper(user_message: str) -> str:
    msg = (user_message or "").strip()
    return (
        "User message:\n"
        f"\"{msg}\"\n\n"
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

def _db_action_planner_system_prompt() -> str:
    """
    HF is ONLY allowed to output JSON actions; it must NOT output narrative.
    Execution is done by DB tools.
    """
    tool_list = sorted(list(ALL_TOOLS.keys()))
    read_list = sorted(list(READ_TOOLS.keys()))
    write_list = sorted(list(WRITE_TOOLS.keys()))
    return (
        "You are younchat inside Njangi.\n"
        "You MUST output ONLY valid JSON. No prose.\n\n"
        "GOAL:\n"
        "Translate the user's request into Njangi tool actions.\n\n"
        "STRICT RULES:\n"
        "- Output ONLY JSON in one of these forms:\n"
        "  1) {\"tool\":\"<tool>\",\"params\":{...}}\n"
        "  2) {\"actions\":[{\"tool\":\"...\",\"params\":{...}}, ...]}\n"
        "- NEVER invent IDs, totals, balances, or DB facts.\n"
        "- If missing required info, output ONE action that is a READ tool to fetch it, OR ask for exactly one missing field by returning:\n"
        "  {\"tool\":\"ask_one\",\"params\":{\"question\":\"...\"}}\n"
        "- Use READ tools for data lookup. Use WRITE tools only when the user explicitly wants a write.\n\n"
        f"READ_TOOLS: {read_list}\n"
        f"WRITE_TOOLS: {write_list}\n"
        f"ALL_TOOLS: {tool_list}\n\n"
        "PARAMS HINTS:\n"
        "- member lookup: use member_query (name fragment or numeric member id)\n"
        "- contributions write requires: member_query, session_id, amount\n"
        "- fine write requires: member_query, amount, reason (optional session_id)\n"
        "- approve_loan_request requires: loan_request_id, session_id\n"
    )

def _hf_plan_db_actions(user_text: str) -> Tuple[bool, Dict[str, Any], str]:
    token = (os.getenv("HF_TOKEN") or "").strip()
    if not token:
        return False, {}, "hf:missing"
    model = _hf_model()

    sys = _db_action_planner_system_prompt()
    messages = [{"role": "system", "content": sys}, {"role": "user", "content": user_text}]
    ok, txt = _hf_router_chat(model, token, messages)
    if not ok or not txt:
        return False, {}, f"hf:planner_failed:{model}"

    payload = safe_json_from_text(txt)
    if not payload:
        return False, {}, f"hf:planner_bad_json:{model}"

    # allow "ask_one"
    if payload.get("tool") == "ask_one":
        return True, payload, f"hf:planner:{model}"

    # validate tool(s)
    if "tool" in payload:
        if payload.get("tool") not in ALL_TOOLS:
            return False, {}, f"hf:planner_unknown_tool:{model}"
        return True, payload, f"hf:planner:{model}"

    if "actions" in payload and isinstance(payload["actions"], list):
        for a in payload["actions"]:
            if _clean(str(a.get("tool") or "")) not in ALL_TOOLS and a.get("tool") != "ask_one":
                return False, {}, f"hf:planner_unknown_tool:{model}"
        return True, payload, f"hf:planner:{model}"

    return False, {}, f"hf:planner_invalid_format:{model}"


# =============================================================================
# 7) TOOL RUNNER (READ immediately, WRITE -> pending confirmations)
# =============================================================================
def _run_tool_payload(supabase, ctx, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns dict with:
      - reads: list of tool results
      - staged: list of staged writes
      - asks: optional one question
      - errors: list
    """
    out = {"reads": [], "staged": [], "asks": None, "errors": []}

    if not isinstance(payload, dict):
        out["errors"].append("Invalid payload type.")
        return out

    if payload.get("tool") == "ask_one":
        q = _clean((payload.get("params") or {}).get("question"))
        out["asks"] = q or "I need one detail to proceed. What’s missing?"
        return out

    actions: List[Dict[str, Any]] = []
    if "actions" in payload and isinstance(payload["actions"], list):
        actions = payload["actions"]
    elif "tool" in payload:
        actions = [payload]
    else:
        out["errors"].append("Payload must contain tool or actions.")
        return out

    st.session_state.setdefault("pending_confirmations", [])

    for act in actions:
        tool = _clean(str(act.get("tool") or ""))
        params = act.get("params") if isinstance(act.get("params"), dict) else {}

        if tool not in ALL_TOOLS:
            out["errors"].append(f"Unknown tool: {tool}")
            continue

        # READ tools
        if tool in READ_TOOLS:
            try:
                tr: ToolResult = READ_TOOLS[tool](supabase, ctx, params)
                out["reads"].append({"tool": tool, "ok": tr.ok, "message": tr.message, "data": tr.data})
            except Exception as e:
                out["errors"].append(f"Read tool error ({tool}): {e}")
            continue

        # WRITE tools -> stage
        if tool in WRITE_TOOLS:
            try:
                tr: ToolResult = WRITE_TOOLS[tool](supabase, ctx, params)
                if tr.needs_confirmation and tr.confirmation_payload:
                    st.session_state["pending_confirmations"].append({
                        "created_at": iso_utc(),
                        "tool": tr.confirmation_payload.get("tool"),
                        "params": tr.confirmation_payload.get("params"),
                    })
                    out["staged"].append({"tool": tool, "message": tr.message})
                else:
                    out["staged"].append({"tool": tool, "message": tr.message})
            except Exception as e:
                out["errors"].append(f"Write tool error ({tool}): {e}")

    return out


def _render_pending_confirmations(supabase, ctx):
    pending = st.session_state.get("pending_confirmations", [])
    if not pending:
        return

    st.markdown("### ✅ Pending Actions (Confirm to Execute)")
    pending = list(reversed(pending))

    for idx, p in enumerate(pending):
        tool = _clean(str(p.get("tool") or ""))
        params = p.get("params") if isinstance(p.get("params"), dict) else {}
        created_at = _clean(str(p.get("created_at") or ""))

        with st.expander(f"Confirm: {tool} • {created_at}", expanded=(idx == 0)):
            st.json({"tool": tool, "params": params}, expanded=False)

            c1, c2 = st.columns(2)
            with c1:
                if st.button(f"CONFIRM & EXECUTE ({tool})", key=f"chat_confirm_{idx}", type="primary", use_container_width=True):
                    tr = execute_confirmed_action(supabase, ctx, tool, params)
                    if tr.ok:
                        st.success(tr.message)
                    else:
                        st.error(tr.message)

                    try:
                        orig = list(reversed(st.session_state.get("pending_confirmations", [])))
                        orig.remove(p)
                        st.session_state["pending_confirmations"] = list(reversed(orig))
                    except Exception:
                        st.session_state["pending_confirmations"] = []
                    st.rerun()

            with c2:
                if st.button("Cancel", key=f"chat_cancel_{idx}", use_container_width=True):
                    try:
                        orig = list(reversed(st.session_state.get("pending_confirmations", [])))
                        orig.remove(p)
                        st.session_state["pending_confirmations"] = list(reversed(orig))
                    except Exception:
                        st.session_state["pending_confirmations"] = []
                    st.rerun()


# =============================================================================
# 8) MAIN UI
# =============================================================================
def render_njangi_llm_panel(sb_anon, sb_service, schema: str) -> None:
    st.subheader("💬 younchat", anchor=False)

    hf_token = (os.getenv("HF_TOKEN") or "").strip()
    internet_on = _internet_enabled()
    supabase = sb_service or sb_anon  # tools layer uses supabase.table(...)

    # ✅ Init toggles
    if "younchat_write_mode" not in st.session_state:
        st.session_state["younchat_write_mode"] = False

    with st.expander("⚙️ Chat Settings", expanded=False):
        st.write("**Schema**:", schema)
        st.write("**HF models (locked)**:", ", ".join(HF_ALLOWED_MODELS))
        st.write("**HF_TOKEN present**:", "✅ Yes" if hf_token else "❌ No")
        st.write("**HF_FORCE_MODE**:", (os.getenv("HF_FORCE_MODE") or "auto"))
        st.write("**Internet**:", "✅ ON" if internet_on else "❌ OFF")
        st.caption("DB numbers: DB-only. Non-DB messages: HF intent/next-step. Writes: confirm-first.")

    colW1, colW2 = st.columns([1, 2], gap="small")
    with colW1:
        st.session_state["younchat_write_mode"] = st.toggle("Write Mode ON", value=st.session_state["younchat_write_mode"])
    with colW2:
        if st.session_state["younchat_write_mode"]:
            st.warning("Write Mode is ON. Writes still require confirmation.", icon="⚠️")
        else:
            st.info("Write Mode is OFF (reads only).", icon="ℹ️")

    @st.cache_data(ttl=30, show_spinner=False)
    def _cached_members_truth(_ts: int) -> pd.DataFrame:
        return _load_members_truth(sb_anon, sb_service, schema, limit=3000)

    members_truth = _cached_members_truth(int(time.time() // 10))

    if "younchat_history" not in st.session_state:
        st.session_state["younchat_history"] = [{"role": "assistant", "content": _intro_only()}]

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

    # ✅ show pending confirmations always (inside younchat)
    ctx = resolve_user_context(supabase, st.session_state)
    _render_pending_confirmations(supabase, ctx)

    # ✅ quick JSON tool input (optional)
    with st.expander("🧰 Paste tool JSON (optional)", expanded=False):
        tool_json = st.text_area("Tool JSON", height=120, placeholder='{"tool":"get_member","params":{"member_query":"John"}}')
        if st.button("Run tool JSON", use_container_width=True):
            payload = safe_json_from_text(tool_json)
            if not payload:
                st.error("Invalid JSON.")
            else:
                if (payload.get("tool") in WRITE_TOOLS) and (not st.session_state["younchat_write_mode"]):
                    st.error("Write Mode is OFF. Turn it ON to stage write actions.")
                else:
                    ran = _run_tool_payload(supabase, ctx, payload)
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

    # remember last member id
    detected_id = _extract_member_id(q)
    if detected_id:
        st.session_state["younchat_last_member_id"] = detected_id
    member_id_focus = st.session_state.get("younchat_last_member_id")

    used_source = "local"
    answer = ""

    # -------------------------------------------------------------------------
    # ROUTING
    # -------------------------------------------------------------------------
    is_db = _is_db_command(q)

    # Internet commands (non-DB)
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

    # Non-DB => HF intent model
    elif not is_db:
        if _has_hf_token():
            ok, txt, used = _hf_smalltalk_answer(q)
            if ok and txt and (not _looks_like_code_output(txt)):
                used_source = used
                answer = txt
            else:
                used_source = f"{used}:fallback_local"
                answer = (
                    "Hello 👋🏽 I couldn’t get a clean response from the foundation model.\n"
                    "Intent: Foundation model failed\n"
                    "Next: Please try again (or type: members / loans / finance kpis)."
                )
        else:
            used_source = "local:no_hf"
            answer = (
                "Hello 👋🏽 HF_TOKEN is missing, so foundation replies are OFF.\n"
                "Intent: Non-DB request\n"
                "Next: Add HF_TOKEN to enable non-DB answers (or use: members / loans / finance kpis)."
            )

    # DB => tools / local DB features
    else:
        # ✅ If user wants actions (writes/operations), let HF PLAN tools JSON
        if _wants_db_actions(q):
            if not _has_hf_token():
                used_source = "db:planner:no_hf"
                answer = (
                    "Hello 👋🏽 To auto-plan DB actions, HF_TOKEN must be set.\n"
                    "Next: Either set HF_TOKEN or paste tool JSON in the Tool JSON expander."
                )
            else:
                ok, payload, used = _hf_plan_db_actions(q)
                used_source = used
                if not ok or not payload:
                    answer = "Hello 👋🏽 I couldn’t plan that action. Please rephrase or paste tool JSON."
                else:
                    # ask_one
                    if payload.get("tool") == "ask_one":
                        question = _clean((payload.get("params") or {}).get("question"))
                        answer = _force_hello_prefix(question or "I need one detail to proceed. What’s missing?")
                    else:
                        # block writes if write mode off
                        is_write_payload = (
                            (payload.get("tool") in WRITE_TOOLS)
                            or any((a.get("tool") in WRITE_TOOLS) for a in (payload.get("actions") or []) if isinstance(a, dict))
                        )
                        if is_write_payload and not st.session_state["younchat_write_mode"]:
                            answer = (
                                "Hello 👋🏽 This requires a WRITE action.\n"
                                "Next: Turn **Write Mode ON**, then send the same request again."
                            )
                        else:
                            ran = _run_tool_payload(supabase, ctx, payload)
                            parts = ["Hello 👋🏽 I ran the requested Njangi actions (DB-only)."]

                            for r in ran.get("reads", []):
                                parts.append(f"- {r['tool']}: {r['message']}")

                            for s in ran.get("staged", []):
                                parts.append(f"- Staged (needs confirmation): {s['tool']}")

                            for e in ran.get("errors", []):
                                parts.append(f"- Error: {e}")

                            if ran.get("asks"):
                                parts.append(f"Next: {ran['asks']}")

                            # Encourage confirmation if staged
                            if ran.get("staged"):
                                parts.append("Next: Confirm the pending action(s) below to execute.")

                            answer = "\n".join(parts)

        # ✅ Simple DB commands still work (fast)
        elif _wants_help(q):
            used_source = "help"
            answer = (
                "Hello 👋🏽 Commands:\n\n"
                "- **members**\n"
                "- type **10** (member intelligence)\n"
                "- **verify member 10**\n"
                "- **loans**\n"
                "- **finance kpis**\n"
                "- **tables**\n"
                "- **show <table>** (example: show contributions)\n"
                "- **describe <table>** (example: describe loans)\n"
                "- For actions: \"create contribution for John session 12 amount 1000\" (Write Mode ON)\n"
                "- **web: <topic>** (internet)\n"
            )

        elif _wants_tables_list(q):
            used_source = "relations"
            rows = [{"relation": k, "type": RELATIONS[k].get("type", "?")} for k in sorted(RELATIONS.keys())]
            df_show = pd.DataFrame(rows)
            answer = "Hello 👋🏽 Here are the tables/views younchat can read:"
            st.session_state["younchat_history"].append({"role": "assistant", "content": answer})
            with st.chat_message("assistant"):
                st.markdown(answer)
                st.dataframe(df_show, use_container_width=True)
            return

        elif _wants_describe(q):
            rel = _extract_relation_name(q)
            if not rel:
                used_source = "describe"
                answer = "Hello 👋🏽 Say: **describe loans** (or any table/view in the allowlist)."
            else:
                df = _sb_select(sb_anon, sb_service, schema, rel, cols="*", limit=1)
                cols = list(df.columns) if df is not None else []
                df_show = pd.DataFrame({"column_name": cols})
                answer = f"Hello 👋🏽 Columns for **{rel}** ({RELATIONS[rel]['type']}):"
                st.session_state["younchat_history"].append({"role": "assistant", "content": answer})
                with st.chat_message("assistant"):
                    st.markdown(answer)
                    st.dataframe(df_show, use_container_width=True)
                return

        elif _wants_show_table(q):
            rel = _extract_relation_name(q)
            if not rel:
                used_source = "show"
                answer = "Hello 👋🏽 Say: **show contributions** (or any table/view in the allowlist)."
            else:
                df = _sb_select(sb_anon, sb_service, schema, rel, cols="*", limit=2000)
                answer = f"Hello 👋🏽 Preview of **{rel}** ({RELATIONS[rel]['type']}):"
                st.session_state["younchat_history"].append({"role": "assistant", "content": answer})
                with st.chat_message("assistant"):
                    st.markdown(answer)
                    st.dataframe(df, use_container_width=True)
                return

        elif _wants_list_members(q):
            used_source = "members"
            if members_truth is None or members_truth.empty:
                answer = "Hello 👋🏽 I couldn’t read **members** (source of truth). Check RLS / permissions."
            else:
                lines = ["Hello 👋🏽 Here are all members (from `members`):\n"]
                for r in members_truth.itertuples(index=False):
                    lines.append(f"- **{r.member_id}** • {r.member_name}")
                answer = "\n".join(lines)

        elif _wants_kpis(q):
            used_source = "kpis"
            if "v_finance_kpis" in RELATIONS:
                df = _sb_select(sb_anon, sb_service, schema, "v_finance_kpis", cols="*", limit=200)
                st.session_state["younchat_history"].append({"role": "assistant", "content": "Hello 👋🏽 Finance KPIs:"})
                with st.chat_message("assistant"):
                    st.markdown("Hello 👋🏽 Finance KPIs:")
                    st.dataframe(df, use_container_width=True)
                return
            answer = "Hello 👋🏽 v_finance_kpis not available."

        elif _wants_loans(q):
            used_source = "loans"
            mid = _extract_member_id(q) or member_id_focus
            filters = [("member_id", "eq", mid)] if mid else None
            rel = "v_loans_with_member" if "v_loans_with_member" in RELATIONS else "loans"
            df = _sb_select(sb_anon, sb_service, schema, rel, cols="*", limit=5000, filters=filters)
            title = "Loans" if not mid else f"Loans (member_id={mid})"
            st.session_state["younchat_history"].append({"role": "assistant", "content": f"Hello 👋🏽 {title}:"})
            with st.chat_message("assistant"):
                st.markdown(f"Hello 👋🏽 {title}:")
                st.dataframe(df, use_container_width=True)
            return

        elif _wants_verify_member(q):
            mid = _extract_verify_member_id(q) or member_id_focus
            answer = _force_hello_prefix(f"Verify member {mid} is not wired in this simplified agent build yet. Use your original verify block if you want it back.")
        elif _is_member_id_only_request(q):
            mid = _extract_member_id(q)
            answer = _force_hello_prefix(f"Member intelligence is handled in your original manifold block. (You can paste it back if you want.) Member id: {mid}")
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
                "- For actions: \"create contribution for John session 12 amount 1000\" (Write Mode ON)\n"
            )

    # Enforce Hello prefix while preserving exact intro message
    if answer != _intro_only():
        answer = _force_hello_prefix(answer)

    st.session_state["younchat_history"].append({"role": "assistant", "content": answer})
    with st.chat_message("assistant"):
        st.markdown(answer)

    st.caption(
        f"Source: {used_source} • member_id: {member_id_focus or '—'} • "
        f"Internet: {'ON' if internet_on else 'OFF'} • "
        f"HF_TOKEN: {'ON' if _has_hf_token() else 'OFF'} • "
        f"Write Mode: {'ON' if st.session_state['younchat_write_mode'] else 'OFF'}"
)
