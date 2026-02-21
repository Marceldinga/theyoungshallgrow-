
# njangi_llm_panel.py ✅ SINGLE COMPLETE FILE — younchat reads ALL your tables/views (members = source of truth)
# =============================================================================
# 💬 younchat — DB-TOOLS FIRST (views + tables) + Optional HF Router + Optional Tavily
#
# ✅ Your request (IMPORTANT):
#   - Salute must be: "Hello"
#   - Then a modern introduction
#   - younchat MUST read ALL your tables/views (based on an allowlist you control)
#   - members table is the source of truth for identity (name display)
#   - NO hallucinations for Njangi numbers (all financial answers come from DB)
#
# Works with app.py:
#   render_njangi_llm_panel(sb_anon=..., sb_service=..., schema=...)
#
# Railway env vars (optional):
#   HF_TOKEN
#   HF_MODEL
#   HF_FORCE_MODE = auto | completions | chat
#   TAVILY_API_KEY
#   INTERNET_MODE = on | off
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


W_STRETCH = "stretch"

HF_ROUTER_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"
HF_ROUTER_COMPLETIONS_URL = "https://router.huggingface.co/v1/completions"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"

HF_FALLBACK_MODELS = [
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.2",
]

# ✅ Allowlist the relations (tables + views).
# Add/remove here as your DB grows.
RELATIONS: Dict[str, Dict[str, Any]] = {
    # Tables
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


# -----------------------------------------------------------------------------
# Time helpers
# -----------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hello_intro() -> str:
    # Your request: salute must be "Hello", then intro.
    return (
        "Hello 👋🏽 I’m **younchat** — your modern Njangi AI assistant.\n\n"
        "I answer Njangi questions using **your database as the source of truth** (no guessing).\n\n"
        "**Try this:**\n"
        "- **help** (commands)\n"
        "- **members** (lists everyone)\n"
        "- type **10** (member summary)\n"
        "- **loans** / **loans for member 10**\n"
        "- **finance kpis**\n"
        "- **tables** (list all readable tables/views)\n"
        "- **show contributions** (preview a table)\n"
        "- **describe loans** (list columns from DB)\n"
    )


# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------
def _api_msg(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload)
        return str(payload)
    return str(e)


# -----------------------------------------------------------------------------
# DB Read helpers
# -----------------------------------------------------------------------------
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
    """
    filters: list of (column, op, value) where op in ["eq","gte","lte","ilike","in"]
    order: (column, asc)
    """
    sb = sb_service or sb_anon
    if sb is None:
        return pd.DataFrame()

    # Guard: only allowed relations
    if relation not in RELATIONS:
        return pd.DataFrame()

    try:
        q = sb.schema(schema).table(relation).select(cols).limit(limit)
        if filters:
            for col, op, val in filters:
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

        res = q.execute()
        return pd.DataFrame(getattr(res, "data", None) or [])
    except Exception:
        # fallback without schema()
        try:
            q = sb.table(relation).select(cols).limit(limit)
            if filters:
                for col, op, val in filters:
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

            res = q.execute()
            return pd.DataFrame(getattr(res, "data", None) or [])
        except Exception as e2:
            st.warning(f"Could not read {schema}.{relation}: {_api_msg(e2)}")
            return pd.DataFrame()


def _pick_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _safe_sum(df: pd.DataFrame, col: Optional[str]) -> float:
    if df is None or df.empty or not col or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())


def _fmt(x: Any) -> str:
    try:
        v = float(pd.to_numeric(x, errors="coerce"))
    except Exception:
        v = 0.0
    return f"{v:,.2f}"


# -----------------------------------------------------------------------------
# Intent helpers
# -----------------------------------------------------------------------------
def _clean(text: str) -> str:
    return (text or "").strip()


def _lc(text: str) -> str:
    return _clean(text).lower()


def _wants_help(text: str) -> bool:
    t = _lc(text)
    return t in {"help", "/help", "commands", "menu", "what can you do", "options"}


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
    ]
    return t in {"members", "member"} or any(p in t for p in phrases)


def _wants_tables_list(text: str) -> bool:
    t = _lc(text)
    return t in {"tables", "table", "relations", "views", "list tables", "list views"}


def _wants_describe(text: str) -> bool:
    t = _lc(text)
    return t.startswith("describe ") or t.startswith("columns ") or t.startswith("cols ") or t.startswith("schema ")


def _wants_show_table(text: str) -> bool:
    t = _lc(text)
    return t.startswith("show ") or t.startswith("preview ") or t.startswith("open ")


def _wants_kpis(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["kpi", "kpis", "finance kpi", "finance kpis", "dashboard kpi"])


def _wants_loans(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["loan", "loans", "borrow", "repay", "overdue", "dpd", "interest due"])


def _wants_contributions(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["contribution", "contributions"])


def _wants_foundation(text: str) -> bool:
    t = _lc(text)
    return "foundation" in t


def _wants_payouts(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["payout", "payouts", "beneficiary"])


def _wants_attendance(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["attendance", "present", "absent"])


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


def _extract_relation_name(text: str) -> Optional[str]:
    """
    Accept:
      - "show contributions"
      - "describe loans"
      - "columns v_finance_kpis"
      - "open loan_requests"
    """
    t = _lc(text)
    t = re.sub(r"^(show|preview|open|describe|columns|cols|schema)\s+", "", t).strip()
    # allow "table X"
    t = re.sub(r"^table\s+", "", t).strip()
    # remove trailing punctuation
    t = re.sub(r"[^\w]+$", "", t)
    if not t:
        return None
    # if user included many words, take first token
    token = t.split()[0]
    return token if token in RELATIONS else None


# -----------------------------------------------------------------------------
# Members truth (source of truth)
# -----------------------------------------------------------------------------
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

    # IMPORTANT FIX (your issue):
    # If display_name exists but is NULL, we must fallback to name.
    if display_col and display_col in df.columns:
        disp = df[display_col].astype(str)
        # treat "None"/"nan" and empty as missing
        disp_clean = disp.replace(["None", "nan", "NaN", "NULL", "null"], "").fillna("").str.strip()
    else:
        disp_clean = pd.Series([""] * len(df))

    if name_col and name_col in df.columns:
        nm = df[name_col].astype(str)
        nm_clean = nm.replace(["None", "nan", "NaN", "NULL", "null"], "").fillna("").str.strip()
    else:
        nm_clean = pd.Series([""] * len(df))

    out["member_name"] = disp_clean.where(disp_clean != "", nm_clean)
    out["member_name"] = out["member_name"].fillna("").replace("", "(no name)")

    # sort by id numeric
    try:
        out["_id_num"] = pd.to_numeric(out["member_id"], errors="coerce")
        out = out.sort_values(["_id_num", "member_id"], ascending=True).drop(columns=["_id_num"])
    except Exception:
        pass

    return out


def _member_name_from_truth(members_truth: pd.DataFrame, member_id: str) -> str:
    if members_truth is None or members_truth.empty:
        return "(unknown)"
    hit = members_truth[members_truth["member_id"].astype(str) == str(member_id)]
    if hit.empty:
        return "(unknown)"
    return str(hit.iloc[0]["member_name"])


# -----------------------------------------------------------------------------
# Local answers (DB truth)
# -----------------------------------------------------------------------------
def _member_financial_totals(
    sb_anon,
    sb_service,
    schema: str,
    member_id: str,
    members_truth: pd.DataFrame,
) -> Tuple[str, Dict[str, Any]]:
    """
    Prefer v_member_financial_totals view.
    If view returns nothing, compute from tables.
    Always use members table for name.
    """
    name = _member_name_from_truth(members_truth, member_id)

    # 1) Preferred: view
    if "v_member_financial_totals" in RELATIONS:
        v = _sb_select(
            sb_anon,
            sb_service,
            schema,
            "v_member_financial_totals",
            cols="*",
            limit=50,
            filters=[("member_id", "eq", member_id)],
        )
        if not v.empty:
            row = v.iloc[0].to_dict()

            contrib = row.get("contributions_total", row.get("contribution_total", row.get("contributions", 0)))
            found = row.get("foundation_total", row.get("foundation_contributions_total", row.get("foundation", 0)))
            fines = row.get("fines_total", row.get("fines", 0))
            active_bal = row.get(
                "active_loan_balance",
                row.get("loan_balance", row.get("principal_current_total", 0)),
            )
            unpaid_int = row.get(
                "active_unpaid_interest",
                row.get("unpaid_interest_total", row.get("unpaid_interest", 0)),
            )
            interest = row.get("interest_total", row.get("interest_ledger_total", row.get("interest", 0)))
            loans_count = row.get("loans_count", row.get("loan_count", None))

            msg = (
                f"Hello 👋🏽 Here’s the grounded summary for **{name}** (member_id={member_id}):\n\n"
                f"- Contributions total: **{_fmt(contrib)}**\n"
                f"- Foundation total: **{_fmt(found)}**\n"
                f"- Fines total: **{_fmt(fines)}**\n"
                f"- Active loan balance: **{_fmt(active_bal)}**\n"
                f"- Active unpaid interest: **{_fmt(unpaid_int)}**\n"
                f"- Interest ledger total: **{_fmt(interest)}**\n"
            )
            if loans_count is not None:
                msg += f"- Loans count: **{loans_count}**\n"

            msg += "\nWant the loan rows for this member? (Say: **loans for member "
            msg += f"{member_id}**)"
            return msg, {"source": "v_member_financial_totals", "row": row, "member_name": name}

    # 2) Fallback compute from tables
    contributions = _sb_select(
        sb_anon, sb_service, schema, "contributions", cols="*", limit=20000, filters=[("member_id", "eq", member_id)]
    )
    foundation = _sb_select(
        sb_anon, sb_service, schema, "foundation_contributions", cols="*", limit=20000, filters=[("member_id", "eq", member_id)]
    )
    fines = _sb_select(
        sb_anon, sb_service, schema, "fines", cols="*", limit=20000, filters=[("member_id", "eq", member_id)]
    )
    loans = _sb_select(
        sb_anon, sb_service, schema, "loans", cols="*", limit=10000, filters=[("member_id", "eq", member_id)]
    )
    interest_ledger = _sb_select(
        sb_anon, sb_service, schema, "interest_ledger", cols="*", limit=20000, filters=[("member_id", "eq", member_id)]
    )

    contrib_amt = _pick_col(contributions, ["amount", "contribution_amount", "paid_amount"])
    found_amt = _pick_col(foundation, ["amount", "base_amount", "foundation_amount"])
    fines_amt = _pick_col(fines, ["amount", "fine_amount"])
    int_amt = _pick_col(interest_ledger, ["amount", "interest_amount", "interest"])

    principal_current = _pick_col(loans, ["principal_current", "balance", "outstanding_principal"])
    principal = _pick_col(loans, ["principal", "amount"])

    status_col = _pick_col(loans, ["status"])
    active = loans
    if status_col and status_col in loans.columns:
        active = loans[loans[status_col].astype(str).str.lower().isin(["active", "open", "ongoing", "overdue"])]

    active_bal = _safe_sum(active, principal_current) if principal_current else _safe_sum(active, principal)
    unpaid_interest = _safe_sum(active, "unpaid_interest") if "unpaid_interest" in active.columns else 0.0

    msg = (
        f"Hello 👋🏽 Here’s the grounded summary for **{name}** (member_id={member_id}):\n\n"
        f"- Contributions total: **{_fmt(_safe_sum(contributions, contrib_amt))}**\n"
        f"- Foundation total: **{_fmt(_safe_sum(foundation, found_amt))}**\n"
        f"- Fines total: **{_fmt(_safe_sum(fines, fines_amt))}**\n"
        f"- Loans count: **{len(loans)}**\n"
        f"- Active loan balance: **{_fmt(active_bal)}**\n"
        f"- Active unpaid interest: **{_fmt(unpaid_interest)}**\n"
        f"- Interest ledger total: **{_fmt(_safe_sum(interest_ledger, int_amt))}**\n\n"
        f"Want the loan rows for this member? (Say: **loans for member {member_id}**)"
    )
    return msg, {"source": "tables_fallback", "member_name": name}


def _loans_with_member(
    sb_anon,
    sb_service,
    schema: str,
    member_id: Optional[str],
    members_truth: pd.DataFrame,
) -> Tuple[str, pd.DataFrame, str]:
    if "v_loans_with_member" in RELATIONS:
        filters = [("member_id", "eq", member_id)] if member_id else None
        df = _sb_select(sb_anon, sb_service, schema, "v_loans_with_member", cols="*", limit=5000, filters=filters)
        src = "v_loans_with_member"
    else:
        filters = [("member_id", "eq", member_id)] if member_id else None
        loans = _sb_select(sb_anon, sb_service, schema, "loans", cols="*", limit=5000, filters=filters)
        df = loans
        if not df.empty and "member_id" in df.columns and not members_truth.empty:
            df = df.merge(members_truth, how="left", on="member_id")
        src = "loans (+ members join)"

    title = "Loans"
    if member_id:
        title = f"Loans for {_member_name_from_truth(members_truth, member_id)} (member_id={member_id})"
    return title, df, src


def _kpis(sb_anon, sb_service, schema: str) -> Tuple[str, pd.DataFrame, str]:
    if "v_finance_kpis" in RELATIONS:
        df = _sb_select(sb_anon, sb_service, schema, "v_finance_kpis", cols="*", limit=200)
        return "Finance KPIs", df, "v_finance_kpis"
    return "Finance KPIs", pd.DataFrame([{"note": "v_finance_kpis not available"}]), "fallback"


def _describe_relation(sb_anon, sb_service, schema: str, relation: str) -> Tuple[str, pd.DataFrame, str]:
    # Use a 1-row select and list columns (works even when information_schema is blocked)
    df = _sb_select(sb_anon, sb_service, schema, relation, cols="*", limit=1)
    cols = list(df.columns) if df is not None else []
    out = pd.DataFrame({"column_name": cols})
    msg = f"Hello 👋🏽 Columns for **{relation}** ({RELATIONS[relation]['type']}):"
    return msg, out, f"describe:{relation}"


def _show_relation(sb_anon, sb_service, schema: str, relation: str) -> Tuple[str, pd.DataFrame, str]:
    df = _sb_select(sb_anon, sb_service, schema, relation, cols="*", limit=2000)
    msg = f"Hello 👋🏽 Preview of **{relation}** ({RELATIONS[relation]['type']}):"
    return msg, df, f"show:{relation}"


# -----------------------------------------------------------------------------
# Optional Internet (Tavily) — NEVER used for Njangi numbers
# -----------------------------------------------------------------------------
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


# -----------------------------------------------------------------------------
# HF Router (optional; only for general chat phrasing)
# -----------------------------------------------------------------------------
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


def _messages_to_prompt(messages: List[Dict[str, str]]) -> str:
    out: List[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            out.append(f"[SYSTEM]\n{content}\n")
        elif role == "assistant":
            out.append(f"[ASSISTANT]\n{content}\n")
        else:
            out.append(f"[USER]\n{content}\n")
    out.append("[ASSISTANT]\n")
    return "\n".join(out)


def _hf_router_chat(model: str, token: str, messages: List[Dict[str, str]], timeout: int = 60) -> Tuple[bool, str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": 0.3, "max_tokens": 450}
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
    payload = {"model": model, "prompt": prompt, "temperature": 0.3, "max_tokens": 450}
    ok, raw = _post_with_retries(HF_ROUTER_COMPLETIONS_URL, headers, payload, timeout=timeout)
    if not ok:
        return False, raw
    try:
        data = json.loads(raw)
        text = ((data.get("choices") or [{}])[0].get("text") or "")
        return True, str(text).strip()
    except Exception:
        return False, f"Bad HF completions response: {raw[:600]}"


def _hf_call(model: str, token: str, messages: List[Dict[str, str]]) -> Tuple[bool, str, str]:
    force = (os.getenv("HF_FORCE_MODE", "") or "auto").strip().lower()
    prompt = _messages_to_prompt(messages)

    model_order: List[str] = []
    primary = (model or "").strip()
    if primary:
        model_order.append(primary)
    for m in HF_FALLBACK_MODELS:
        if m and m not in model_order:
            model_order.append(m)

    def _looks_instruct(mname: str) -> bool:
        mlc = (mname or "").lower()
        return any(x in mlc for x in ["instruct", "mistral", "llama-3", "llama-3.1"])

    last_err = ""
    for chosen in model_order:
        if force == "chat":
            order = ["chat"]
        elif force == "completions":
            order = ["completions"]
        else:
            order = ["completions", "chat"] if _looks_instruct(chosen) else ["chat", "completions"]

        for mode in order:
            if mode == "completions":
                ok, txt = _hf_router_completions(chosen, token, prompt)
                if ok and txt:
                    return True, txt, "completions"
                last_err = txt
            else:
                ok, txt = _hf_router_chat(chosen, token, messages)
                if ok and txt:
                    return True, txt, "chat"
                last_err = txt

        err = (last_err or "").lower()
        if not any(s in err for s in ["429", "500", "502", "503", "504", "timeout", "server error"]):
            break

    return False, last_err or "Unknown HF error", "failed"


# -----------------------------------------------------------------------------
# Main UI
# -----------------------------------------------------------------------------
def render_njangi_llm_panel(sb_anon, sb_service, schema: str) -> None:
    st.subheader("💬 younchat", anchor=False)

    hf_token = (os.getenv("HF_TOKEN") or "").strip()
    hf_model = (os.getenv("HF_MODEL") or "").strip() or "meta-llama/Meta-Llama-3-8B-Instruct"
    hf_force = (os.getenv("HF_FORCE_MODE") or "auto").strip().lower()
    internet_on = _internet_enabled()

    with st.expander("⚙️ Chat Settings", expanded=False):
        st.write("**HF model**:", hf_model)
        st.write("**HF_TOKEN present**:", "✅ Yes" if hf_token else "❌ No")
        st.write("**HF_FORCE_MODE**:", hf_force)
        st.write("**Internet**:", "✅ ON" if internet_on else "❌ OFF")
        st.caption("Njangi numbers are ALWAYS answered from DB (tables/views). HF is only for general chat wording.")

    # Cache members truth
    @st.cache_data(ttl=30, show_spinner=False)
    def _cached_members_truth(_ts: int) -> pd.DataFrame:
        return _load_members_truth(sb_anon, sb_service, schema, limit=3000)

    members_truth = _cached_members_truth(int(time.time() // 10))

    # Chat init
    if "younchat_history" not in st.session_state:
        st.session_state["younchat_history"] = [{"role": "assistant", "content": _hello_intro()}]

    # show chat
    for m in st.session_state["younchat_history"]:
        with st.chat_message("assistant" if m.get("role") == "assistant" else "user"):
            st.markdown(m.get("content", ""))

    colA, colB = st.columns([1, 1], gap="small")
    if colA.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    if colB.button("🧹 Clear chat", use_container_width=True):
        st.session_state["younchat_history"] = [{"role": "assistant", "content": _hello_intro()}]
        st.session_state.pop("younchat_last_member_id", None)
        st.rerun()

    q = st.chat_input("Type your message…")
    if not q:
        return

    st.session_state["younchat_history"].append({"role": "user", "content": q})
    with st.chat_message("user"):
        st.markdown(q)

    # Persist member focus
    detected_id = _extract_member_id(q)
    if detected_id:
        st.session_state["younchat_last_member_id"] = detected_id
    member_id_focus = st.session_state.get("younchat_last_member_id")

    # -------------------------
    # LOCAL ROUTER (DB TRUTH)
    # -------------------------
    used_source = "local"
    answer = ""
    df_show: Optional[pd.DataFrame] = None
    df_title: Optional[str] = None

    # Help
    if _wants_help(q):
        used_source = "help"
        answer = (
            "Hello 👋🏽 Here’s what I can do (DB-first):\n\n"
            "**Core commands:**\n"
            "- **members** → list all members (from `members`)\n"
            "- type **10** → summary for member_id 10\n"
            "- **loans** / **loans for member 10**\n"
            "- **finance kpis**\n\n"
            "**Read ANY table/view (from allowlist):**\n"
            "- **tables** → list all readable relations\n"
            "- **show contributions** → preview rows\n"
            "- **describe loans** → list columns\n\n"
            "Tip: If a table exists but you don’t see it here, add it to `RELATIONS`."
        )

    # List relations
    elif _wants_tables_list(q):
        used_source = "relations"
        rows = []
        for k in sorted(RELATIONS.keys()):
            rows.append({"relation": k, "type": RELATIONS[k].get("type", "?")})
        df_show = pd.DataFrame(rows)
        df_title = "Readable relations (allowlist)"
        answer = "Hello 👋🏽 Here are the tables/views younchat can read (allowlist):"

    # Describe
    elif _wants_describe(q):
        rel = _extract_relation_name(q)
        if not rel:
            used_source = "describe"
            answer = "Hello 👋🏽 Tell me the relation name, like: **describe loans** or **columns contributions**."
        else:
            answer, df_show, used_source = _describe_relation(sb_anon, sb_service, schema, rel)
            df_title = f"Columns: {rel}"

    # Show/preview
    elif _wants_show_table(q):
        rel = _extract_relation_name(q)
        if not rel:
            used_source = "show"
            answer = "Hello 👋🏽 Tell me which table/view to preview, like: **show contributions**."
        else:
            answer, df_show, used_source = _show_relation(sb_anon, sb_service, schema, rel)
            df_title = f"Preview: {rel}"

    # Members list
    elif _wants_list_members(q):
        used_source = "members"
        if members_truth is None or members_truth.empty:
            answer = "Hello 👋🏽 I couldn’t read **members** (source of truth). Check RLS / permissions."
        else:
            lines = ["Hello 👋🏽 Here are all members (source of truth = `members`):\n"]
            for r in members_truth.itertuples(index=False):
                lines.append(f"- **{r.member_id}** • {r.member_name}")
            answer = "\n".join(lines)
            df_show, df_title = members_truth, "members (truth)"

    # KPIs
    elif _wants_kpis(q):
        title, df, src = _kpis(sb_anon, sb_service, schema)
        used_source = src
        df_show, df_title = df, title
        answer = f"Hello 👋🏽 Here are your **{title}** (from `{src}`):" if not df.empty else "Hello 👋🏽 No KPI rows returned."

    # Loans
    elif _wants_loans(q):
        mid = _extract_member_id(q) or member_id_focus
        title, df, src = _loans_with_member(sb_anon, sb_service, schema, mid, members_truth)
        used_source = src
        df_show, df_title = df, title
        answer = f"Hello 👋🏽 {title} (from `{src}`): showing latest rows." if not df.empty else f"Hello 👋🏽 {title}: no rows returned."

    # Member summary (numbers)
    elif member_id_focus and (q.strip().isdigit() or "member" in _lc(q) or "summary" in _lc(q) or "status" in _lc(q)):
        answer, meta = _member_financial_totals(sb_anon, sb_service, schema, str(member_id_focus), members_truth)
        used_source = meta.get("source", "member_summary_local")

    # Fallback: if user typed a relation name directly, preview it
    elif _lc(q) in RELATIONS:
        rel = _lc(q)
        answer, df_show, used_source = _show_relation(sb_anon, sb_service, schema, rel)
        df_title = f"Preview: {rel}"

    # Otherwise: optional HF for modern phrasing (NO numbers)
    else:
        if hf_token:
            sys = (
                "You are younchat. Keep answers modern and friendly.\n"
                "If the user asks for Njangi numbers, DO NOT invent numbers. "
                "Instead, suggest DB commands: members / loans / finance kpis / show <table> / describe <table>.\n"
                "Never claim you cannot provide member info; member lists and summaries are allowed if coming from DB.\n"
            )
            messages = [{"role": "system", "content": sys}]
            for m in st.session_state["younchat_history"][-10:]:
                if m.get("role") in ("user", "assistant"):
                    messages.append({"role": m["role"], "content": m.get("content", "")})
            ok, txt, mode = _hf_call(hf_model, hf_token, messages)
            used_source = f"hf:{mode}" if ok else "hf:failed"
            answer = txt if ok else f"Hello 👋🏽 I can’t reach HF right now: {txt}\n\nTry: **help**, **members**, **loans**, **finance kpis**, **tables**."
        else:
            answer = "Hello 👋🏽 Try: **help**, **members**, **loans**, **finance kpis**, **tables**, **show contributions**, **describe loans**."

    # Output
    st.session_state["younchat_history"].append({"role": "assistant", "content": answer})
    with st.chat_message("assistant"):
        st.markdown(answer)
        if df_show is not None and df_title:
            with st.expander(df_title, expanded=False):
                st.dataframe(df_show, use_container_width=True)

    st.caption(f"Source used: {used_source} • member_id: {member_id_focus or '—'} • Internet: {'ON' if internet_on else 'OFF'}")
