
# njangi_llm_panel.py ✅ UPDATED — FIX “List all members” (NO more “not in snapshot”)
# =============================================================================
# 💬 younchat — Hugging Face Router + Optional Internet Search ✅ SINGLE COMPLETE FILE (HARDENED)
#
# ✅ FIXED (your exact issue):
#   - When user asks: “list all the members” / “show members” / “members list”
#     → younchat answers LOCALLY from LIVE members table (already loaded in snapshot)
#     → It prints the full names (and IDs), NOT “I don’t have that in the snapshot.”
#
# ✅ Keeps your existing behavior:
#   - ONLY name shown: "younchat"
#   - Good morning/afternoon/evening greeting
#   - Streamlit chat memory
#   - member_id context selection & persistence
#   - Internet is ON only if TAVILY_API_KEY exists (and INTERNET_MODE not off)
#   - Grounded on live Njangi snapshot for Njangi numbers (no guessing)
#   - HF reliability (retries + model failover)
#
# Works with app.py:
#   render_njangi_llm_panel(sb_anon=..., sb_service=..., schema=...)
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
from postgrest.exceptions import APIError

W_STRETCH = "stretch"

HF_ROUTER_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"
HF_ROUTER_COMPLETIONS_URL = "https://router.huggingface.co/v1/completions"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"

# If HF has a transient outage (500s), younchat will try these automatically.
HF_FALLBACK_MODELS = [
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.2",
]


# -----------------------------------------------------------------------------
# Time helpers
# -----------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tod_greeting() -> str:
    """
    Uses server local time. If you want it to match your timezone,
    set Railway Variable: TZ=America/Chicago (or your timezone).
    """
    h = datetime.now().hour
    if 5 <= h < 12:
        return "Good morning"
    if 12 <= h < 17:
        return "Good afternoon"
    if 17 <= h < 22:
        return "Good evening"
    return "Hello"


# -----------------------------------------------------------------------------
# Safe errors
# -----------------------------------------------------------------------------
def _api_msg(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("hint") or payload)
        return str(payload)
    return str(e)


# -----------------------------------------------------------------------------
# Safe Supabase read (schema-safe)
# -----------------------------------------------------------------------------
def _sb_select(
    sb_anon,
    sb_service,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 1000,
) -> pd.DataFrame:
    sb = sb_service or sb_anon
    if sb is None:
        return pd.DataFrame()

    try:
        res = sb.schema(schema).table(table).select(cols).limit(limit).execute()
        data = getattr(res, "data", None) or []
        return pd.DataFrame(data)
    except Exception:
        try:
            res = sb.table(table).select(cols).limit(limit).execute()
            data = getattr(res, "data", None) or []
            return pd.DataFrame(data)
        except Exception as e2:
            st.warning(f"Could not read {schema}.{table}: {_api_msg(e2)}")
            return pd.DataFrame()


def _safe_sum(df: pd.DataFrame, col: Optional[str]) -> float:
    if df is None or df.empty or not col or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())


def _safe_count(df: pd.DataFrame) -> int:
    return int(len(df)) if df is not None and not df.empty else 0


def _pick_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    return None


# -----------------------------------------------------------------------------
# Snapshot builder (GROUNDING)
# -----------------------------------------------------------------------------
def _build_snapshot(sb_anon, sb_service, schema: str) -> Dict[str, Any]:
    # NOTE: members is FULL table (limit 3000) — we use it locally for "list members"
    members = _sb_select(sb_anon, sb_service, schema, "members", cols="*", limit=3000)
    sessions = _sb_select(sb_anon, sb_service, schema, "sessions", cols="*", limit=3000)
    contributions = _sb_select(sb_anon, sb_service, schema, "contributions", cols="*", limit=10000)
    foundation = _sb_select(sb_anon, sb_service, schema, "foundation_contributions", cols="*", limit=10000)
    loans = _sb_select(sb_anon, sb_service, schema, "loans", cols="*", limit=4000)
    loan_payments = _sb_select(sb_anon, sb_service, schema, "loan_payments", cols="*", limit=10000)
    fines = _sb_select(sb_anon, sb_service, schema, "fines", cols="*", limit=10000)
    payouts = _sb_select(sb_anon, sb_service, schema, "payouts", cols="*", limit=4000)
    interest_ledger = _sb_select(sb_anon, sb_service, schema, "interest_ledger", cols="*", limit=10000)

    name_col = _pick_col(members, ["display_name", "name", "full_name"])
    member_id_col = _pick_col(members, ["id", "member_id"])

    contrib_amt_col = _pick_col(contributions, ["amount", "contribution_amount", "paid_amount"])
    foundation_amt_col = _pick_col(foundation, ["amount", "base_amount", "foundation_amount"])
    fines_amt_col = _pick_col(fines, ["amount", "fine_amount"])
    payout_amt_col = _pick_col(payouts, ["amount", "payout_amount"])
    interest_amt_col = _pick_col(interest_ledger, ["amount", "interest_amount", "interest"])

    loans_principal_col = _pick_col(loans, ["principal", "principal_amount", "amount"])
    loans_principal_current_col = _pick_col(loans, ["principal_current", "balance", "outstanding_principal"])
    loans_status_col = _pick_col(loans, ["status"])
    loans_member_col = _pick_col(loans, ["member_id", "borrower_id"])

    snapshot = {
        "generated_at_utc": _now_iso(),
        "counts": {
            "members": _safe_count(members),
            "sessions": _safe_count(sessions),
            "contributions": _safe_count(contributions),
            "foundation_contributions": _safe_count(foundation),
            "loans": _safe_count(loans),
            "loan_payments": _safe_count(loan_payments),
            "fines": _safe_count(fines),
            "payouts": _safe_count(payouts),
            "interest_ledger": _safe_count(interest_ledger),
        },
        "totals": {
            "contributions_total": _safe_sum(contributions, contrib_amt_col),
            "foundation_total": _safe_sum(foundation, foundation_amt_col),
            "fines_total": _safe_sum(fines, fines_amt_col),
            "payouts_total": _safe_sum(payouts, payout_amt_col),
            "interest_total": _safe_sum(interest_ledger, interest_amt_col),
        },
        "columns": {
            "members_name_col": name_col,
            "members_id_col": member_id_col,
            "contributions_amount_col": contrib_amt_col,
            "foundation_amount_col": foundation_amt_col,
            "fines_amount_col": fines_amt_col,
            "payouts_amount_col": payout_amt_col,
            "interest_amount_col": interest_amt_col,
            "loans_principal_col": loans_principal_col,
            "loans_principal_current_col": loans_principal_current_col,
            "loans_status_col": loans_status_col,
            "loans_member_col": loans_member_col,
        },
        # For the selectbox only (keep it light)
        "members_preview": (
            members[[c for c in [member_id_col, name_col] if c and c in members.columns]]
            .head(200)
            .to_dict("records")
            if not members.empty and member_id_col and name_col
            else []
        ),
        # Raw tables stay local-only (not shown to HF). Used for computations & "list members".
        "_raw": {
            "members": members,
            "sessions": sessions,
            "contributions": contributions,
            "foundation": foundation,
            "loans": loans,
            "loan_payments": loan_payments,
            "fines": fines,
            "payouts": payouts,
            "interest_ledger": interest_ledger,
        },
    }
    return snapshot


# -----------------------------------------------------------------------------
# Member_id extraction & persistence
# -----------------------------------------------------------------------------
_MEMBER_ID_PATTERNS = [
    re.compile(r"\bmember[_\s-]?id\s*[:=#]?\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\bid\s*[:=#]?\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\bmember\s*#?\s*(\d+)\b", re.IGNORECASE),
]
_ANY_NUM = re.compile(r"\b(\d{1,6})\b")


def _extract_member_id_from_text(snapshot: Dict[str, Any], text: str) -> Optional[str]:
    txt = (text or "").strip()
    if not txt:
        return None

    mem_id_col = snapshot.get("columns", {}).get("members_id_col")
    members = snapshot.get("_raw", {}).get("members", pd.DataFrame())
    if members is None or members.empty or not mem_id_col or mem_id_col not in members.columns:
        return None

    valid_ids = set(members[mem_id_col].astype(str).tolist())

    for pat in _MEMBER_ID_PATTERNS:
        m = pat.search(txt)
        if m:
            cand = str(m.group(1))
            if cand in valid_ids:
                return cand

    for nm in _ANY_NUM.findall(txt):
        cand = str(nm)
        if cand in valid_ids:
            return cand

    return None


# -----------------------------------------------------------------------------
# INTENT: list members (LOCAL ANSWER)
# -----------------------------------------------------------------------------
def _wants_list_members(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    phrases = [
        "list all members",
        "list members",
        "show all members",
        "show members",
        "members list",
        "all the members",
        "all members",
        "member list",
        "display members",
        "who are the members",
    ]
    if any(p in t for p in phrases):
        return True
    # short commands like: "members"
    if t in {"members", "member"}:
        return True
    return False


def _local_list_members_answer(snapshot: Dict[str, Any]) -> Tuple[str, pd.DataFrame]:
    raw_members = snapshot.get("_raw", {}).get("members", pd.DataFrame())
    cols = snapshot.get("columns", {})
    id_col = cols.get("members_id_col") or "id"
    name_col = cols.get("members_name_col") or "name"

    if raw_members is None or raw_members.empty:
        return "I couldn’t load **members** from the database snapshot.", pd.DataFrame()

    df = raw_members.copy()
    if name_col not in df.columns:
        # try fallback
        for alt in ["display_name", "name", "full_name"]:
            if alt in df.columns:
                name_col = alt
                break
    if id_col not in df.columns:
        for alt in ["id", "member_id"]:
            if alt in df.columns:
                id_col = alt
                break

    show_cols = [c for c in [id_col, name_col] if c in df.columns]
    if not show_cols:
        return "Members table loaded, but I can’t find name/id columns to display.", pd.DataFrame()

    out = df[show_cols].copy()
    out.columns = ["member_id", "member_name"] if len(show_cols) == 2 else ["member_value"]
    if "member_id" in out.columns:
        out["member_id"] = out["member_id"].astype(str)
    if "member_name" in out.columns:
        out["member_name"] = out["member_name"].astype(str)

    # sort nicely by id if numeric-like
    if "member_id" in out.columns:
        try:
            out["_id_num"] = pd.to_numeric(out["member_id"], errors="coerce")
            out = out.sort_values(["_id_num", "member_id"], ascending=True).drop(columns=["_id_num"])
        except Exception:
            pass

    lines = ["Here are all members:"]
    if "member_id" in out.columns and "member_name" in out.columns:
        for i, r in enumerate(out.itertuples(index=False), start=1):
            lines.append(f"{i}. {r.member_id} • {r.member_name}")
    else:
        for i, v in enumerate(out.iloc[:, 0].tolist(), start=1):
            lines.append(f"{i}. {v}")

    return "\n".join(lines), out


# -----------------------------------------------------------------------------
# Member financial summary (local compute)
# -----------------------------------------------------------------------------
def _compute_member_financials(snapshot: Dict[str, Any], member_id: str) -> Dict[str, Any]:
    raw = snapshot.get("_raw", {})
    members = raw.get("members", pd.DataFrame())
    contributions = raw.get("contributions", pd.DataFrame())
    foundation = raw.get("foundation", pd.DataFrame())
    loans = raw.get("loans", pd.DataFrame())
    fines = raw.get("fines", pd.DataFrame())
    interest_ledger = raw.get("interest_ledger", pd.DataFrame())

    cols = snapshot.get("columns", {})
    mem_name_col = cols.get("members_name_col")
    mem_id_col = cols.get("members_id_col")

    contrib_amt_col = cols.get("contributions_amount_col")
    found_amt_col = cols.get("foundation_amount_col")
    fines_amt_col = cols.get("fines_amount_col")

    loans_principal_col = cols.get("loans_principal_col")
    loans_principal_current_col = cols.get("loans_principal_current_col")
    loans_status_col = cols.get("loans_status_col")
    loans_member_col = cols.get("loans_member_col")

    interest_amt_col = cols.get("interest_amount_col")

    member_row = None
    if not members.empty and mem_id_col and mem_id_col in members.columns:
        mm = members[members[mem_id_col].astype(str) == str(member_id)]
        if not mm.empty:
            member_row = mm.iloc[0].to_dict()

    contrib_total = 0.0
    if not contributions.empty and "member_id" in contributions.columns:
        df = contributions[contributions["member_id"].astype(str) == str(member_id)]
        contrib_total = _safe_sum(df, contrib_amt_col)

    foundation_total = 0.0
    if not foundation.empty and "member_id" in foundation.columns:
        df = foundation[foundation["member_id"].astype(str) == str(member_id)]
        foundation_total = _safe_sum(df, found_amt_col)

    fines_total = 0.0
    if not fines.empty and "member_id" in fines.columns:
        df = fines[fines["member_id"].astype(str) == str(member_id)]
        fines_total = _safe_sum(df, fines_amt_col)

    loans_count = 0
    active_balance = 0.0
    active_unpaid_interest = 0.0
    if not loans.empty and loans_member_col and loans_member_col in loans.columns:
        ldf = loans[loans[loans_member_col].astype(str) == str(member_id)]
        loans_count = int(len(ldf))

        if loans_status_col and loans_status_col in ldf.columns:
            active = ldf[ldf[loans_status_col].astype(str).str.lower().isin(["active", "open", "ongoing", "overdue"])]
        else:
            active = ldf

        if loans_principal_current_col and loans_principal_current_col in active.columns:
            active_balance = _safe_sum(active, loans_principal_current_col)
        elif loans_principal_col and loans_principal_col in active.columns:
            active_balance = _safe_sum(active, loans_principal_col)

        if "unpaid_interest" in active.columns:
            active_unpaid_interest = _safe_sum(active, "unpaid_interest")

    interest_total = 0.0
    if not interest_ledger.empty and "member_id" in interest_ledger.columns:
        df = interest_ledger[interest_ledger["member_id"].astype(str) == str(member_id)]
        interest_total = _safe_sum(df, interest_amt_col)

    member_name = None
    if member_row and mem_name_col and mem_name_col in member_row:
        member_name = str(member_row.get(mem_name_col))

    return {
        "member_id": str(member_id),
        "member_name": member_name or "(unknown)",
        "contributions_total": round(contrib_total, 2),
        "foundation_total": round(foundation_total, 2),
        "fines_total": round(fines_total, 2),
        "loans_count": loans_count,
        "active_loan_balance": round(active_balance, 2),
        "active_unpaid_interest": round(active_unpaid_interest, 2),
        "interest_total": round(interest_total, 2),
    }


# -----------------------------------------------------------------------------
# Optional Internet search (Tavily)
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
            clean.append(
                {
                    "title": it.get("title"),
                    "url": it.get("url"),
                    "content": (it.get("content") or "")[:500],
                }
            )
        return {"ok": True, "results": clean}
    except Exception as e:
        return {"ok": False, "error": str(e), "results": []}


# -----------------------------------------------------------------------------
# Prompt builders (human chat + grounded Njangi + optional internet)
# -----------------------------------------------------------------------------
def _build_grounded_messages(
    snapshot: Dict[str, Any],
    question: str,
    member_fin: Optional[Dict[str, Any]],
    internet_pack: Optional[Dict[str, Any]],
    chat_history: List[Dict[str, str]],
    member_id_focus: Optional[str],
) -> List[Dict[str, str]]:
    sys = (
        "You are **younchat**, a friendly, human-like assistant for a Njangi finance app.\n"
        "CRITICAL RULES:\n"
        "1) For ANY Njangi totals, counts, loans, payouts, contributions, interest, fines — use ONLY SNAPSHOT_FACTS and SELECTED_MEMBER_FACTS.\n"
        "2) If something is missing in the snapshot facts provided, say exactly: 'I don’t have that in the snapshot.'\n"
        "3) You MAY use INTERNET_SOURCES only for general questions (definitions, laws, how-to), NOT for Njangi numbers.\n"
        "4) Speak naturally like a real person. Short paragraphs.\n"
        "5) Ask ONE helpful follow-up question when it makes sense.\n"
    )

    facts = {
        "generated_at_utc": snapshot.get("generated_at_utc"),
        "counts": snapshot.get("counts", {}),
        "totals": snapshot.get("totals", {}),
        "member_id_focus": member_id_focus,
    }

    content = "SNAPSHOT_FACTS:\n" + json.dumps(facts, indent=2)
    if member_fin:
        content += "\n\nSELECTED_MEMBER_FACTS:\n" + json.dumps(member_fin, indent=2)

    if internet_pack and internet_pack.get("ok") and internet_pack.get("results"):
        content += "\n\nINTERNET_SOURCES:\n" + json.dumps(internet_pack.get("results"), indent=2)

    msgs: List[Dict[str, str]] = [{"role": "system", "content": sys}]
    for m in chat_history[-16:]:
        if m.get("role") in ("user", "assistant"):
            msgs.append({"role": m["role"], "content": m.get("content", "")})

    user = (
        f"{content}\n\n"
        f"User message: {question}\n\n"
        "Respond as younchat."
    )
    msgs.append({"role": "user", "content": user})
    return msgs


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


# -----------------------------------------------------------------------------
# Hugging Face Router (retries + failover)
# -----------------------------------------------------------------------------
def _post_with_retries(url: str, headers: dict, payload: dict, timeout: int = 60) -> Tuple[bool, str]:
    """
    Retries transient HF errors (429/5xx/network/timeouts) with backoff.
    Returns (ok, response_text_or_error).
    """
    last_err = ""
    for attempt in range(4):  # 0..3
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
    payload = {"model": model, "messages": messages, "temperature": 0.4, "max_tokens": 650}

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
    payload = {"model": model, "prompt": prompt, "temperature": 0.4, "max_tokens": 650}

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
    """
    Returns (ok, text_or_error, mode_used)
    mode_used: "completions" | "chat" | "failed"
    """
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
        return any(x in mlc for x in ["instruct", "instruction", "mistral-7b-instruct", "llama-3", "llama-3.1"])

    last_err = ""
    for chosen_model in model_order:
        if force == "chat":
            order = ["chat"]
        elif force == "completions":
            order = ["completions"]
        else:
            order = ["completions", "chat"] if _looks_instruct(chosen_model) else ["chat", "completions"]

        for mode in order:
            if mode == "completions":
                ok, txt = _hf_router_completions(chosen_model, token, prompt)
                if ok and txt:
                    return True, txt, "completions"
                last_err = txt
            else:
                ok, txt = _hf_router_chat(chosen_model, token, messages)
                if ok and txt:
                    return True, txt, "chat"
                last_err = txt

        err_lc = (last_err or "").lower()
        transient = any(
            s in err_lc
            for s in [
                "hf error 500",
                "hf error 502",
                "hf error 503",
                "hf error 504",
                "hf error 429",
                "timeout",
                "server error",
                "transient",
            ]
        )
        if not transient:
            break

    return False, last_err or "Unknown HF error", "failed"


# -----------------------------------------------------------------------------
# Local fallback (still grounded)
# -----------------------------------------------------------------------------
def _local_fallback_answer(snapshot: Dict[str, Any], question: str, member_id: Optional[str]) -> str:
    greet = _tod_greeting()
    q = (question or "").lower().strip()

    if _wants_list_members(question):
        txt, _ = _local_list_members_answer(snapshot)
        return txt

    if any(x in q for x in ["hi", "hello", "hey"]):
        return f"{greet} 👋🏽 I’m **younchat**. Ask me: **members**, **loans**, **totals**, or give a **member_id**."

    if "total" in q and "contribution" in q:
        return f"{greet} 👋🏽 Total contributions (all members): **{snapshot['totals']['contributions_total']:.2f}**. Want totals for foundation too?"

    if "total" in q and "foundation" in q:
        return f"{greet} 👋🏽 Total foundation contributions (all members): **{snapshot['totals']['foundation_total']:.2f}**. Want totals for contributions too?"

    if member_id:
        fin = _compute_member_financials(snapshot, member_id)
        return (
            f"{greet} 👋🏽 Here’s what I have for **{fin['member_name']}** (member_id={fin['member_id']}):\n"
            f"- Contributions: **{fin['contributions_total']:.2f}**\n"
            f"- Foundation: **{fin['foundation_total']:.2f}**\n"
            f"- Fines: **{fin['fines_total']:.2f}**\n"
            f"- Loans: **{fin['loans_count']}** • Active balance: **{fin['active_loan_balance']:.2f}**\n"
            f"- Unpaid interest: **{fin['active_unpaid_interest']:.2f}**\n"
            f"- Interest ledger total: **{fin['interest_total']:.2f}**\n"
            "\nWant me to check another member_id?"
        )

    return (
        f"{greet} 👋🏽 I can answer from your LIVE Njangi snapshot.\n"
        "Try:\n"
        "- **Members** (lists everyone)\n"
        "- Total contributions?\n"
        "- Total foundation money?\n"
        "- Show member_id 10 status\n"
    )


# -----------------------------------------------------------------------------
# UI entry point (CHAT UI)
# -----------------------------------------------------------------------------
def render_njangi_llm_panel(sb_anon, sb_service, schema: str) -> None:
    st.subheader("💬 younchat", anchor=False)

    hf_token = (os.getenv("HF_TOKEN") or "").strip()
    hf_model = (os.getenv("HF_MODEL") or "").strip() or "mistralai/Mistral-7B-Instruct-v0.2"
    hf_force = (os.getenv("HF_FORCE_MODE") or "auto").strip().lower()

    tavily_key = (os.getenv("TAVILY_API_KEY") or "").strip()
    internet_on = _internet_enabled()

    with st.expander("⚙️ Chat Settings", expanded=False):
        st.write("**HF model**:", hf_model)
        st.write("**HF_TOKEN present**:", "✅ Yes" if hf_token else "❌ No (set HF_TOKEN in Railway Variables)")
        st.write("**HF_FORCE_MODE**:", hf_force)
        st.write("**Internet**:", "✅ ON" if internet_on else "❌ OFF")
        if not tavily_key:
            st.caption("To enable Internet: set TAVILY_API_KEY in Railway. (INTERNET_MODE=off disables it.)")
        st.caption("Tip: For fewer HF failures, set HF_FORCE_MODE=completions and HF_MODEL=meta-llama/Meta-Llama-3-8B-Instruct.")

    @st.cache_data(ttl=30, show_spinner=False)
    def _cached_snapshot(_ts: int) -> Dict[str, Any]:
        return _build_snapshot(sb_anon, sb_service, schema)

    snapshot = _cached_snapshot(int(time.time() // 10))

    # Member select (optional)
    members_preview = snapshot.get("members_preview", [])
    id_col = snapshot.get("columns", {}).get("members_id_col") or "id"
    name_col = snapshot.get("columns", {}).get("members_name_col") or "name"

    member_options: List[Tuple[str, str]] = []
    for r in members_preview:
        rid = r.get(id_col)
        rname = r.get(name_col)
        if rid is not None and rname is not None:
            member_options.append((str(rid), f"{rid} • {rname}"))

    selected_member_id: Optional[str] = None
    if member_options:
        label_map = {lbl: mid for (mid, lbl) in member_options}
        chosen = st.selectbox(
            "Optional: choose a member (member_id focus)",
            ["(None)"] + [lbl for _, lbl in member_options],
            index=0,
        )
        if chosen != "(None)":
            selected_member_id = label_map.get(chosen)

    # Initialize chat history
    if "younchat_history" not in st.session_state:
        greet = _tod_greeting()
        st.session_state["younchat_history"] = [
            {
                "role": "assistant",
                "content": (
                    f"{greet} 👋🏽 I’m **younchat**.\n\n"
                    "Try: **members**, **loans**, **total contributions**, or give a **member_id** (like `10`)."
                ),
            }
        ]

    # Show chat
    for m in st.session_state["younchat_history"]:
        role = m.get("role", "assistant")
        with st.chat_message("assistant" if role == "assistant" else "user"):
            st.markdown(m.get("content", ""))

    colA, colB = st.columns([1, 1], gap="small")
    if colA.button("🔄 Refresh snapshot", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    if colB.button("🧹 Clear chat", use_container_width=True):
        greet = _tod_greeting()
        st.session_state["younchat_history"] = [
            {"role": "assistant", "content": f"{greet} 👋🏽 I’m **younchat**. What do you want to check right now?"}
        ]
        st.session_state.pop("younchat_last_member_id", None)
        st.rerun()

    # Chat input
    question = st.chat_input("Type your message…")
    if not question:
        return

    # Add user message
    st.session_state["younchat_history"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # ✅ LOCAL SHORT-CIRCUIT: list members (no HF, no internet, 100% grounded)
    if _wants_list_members(question):
        answer, members_df = _local_list_members_answer(snapshot)
        st.session_state["younchat_history"].append({"role": "assistant", "content": answer})
        with st.chat_message("assistant"):
            st.markdown(answer)
            with st.expander("Members table (ground truth)", expanded=False):
                if not members_df.empty:
                    st.dataframe(members_df, use_container_width=True)
        st.caption("HF mode used: local • Internet: OFF • member_id: —")
        return

    # Resolve member_id context:
    detected_from_text = _extract_member_id_from_text(snapshot, question)
    member_id_focus = selected_member_id or detected_from_text or st.session_state.get("younchat_last_member_id")
    if member_id_focus:
        st.session_state["younchat_last_member_id"] = member_id_focus

    member_fin = _compute_member_financials(snapshot, member_id_focus) if member_id_focus else None

    # Optional internet pack: ONLY when enabled AND question is general web question (not Njangi)
    internet_pack: Optional[Dict[str, Any]] = None
    if internet_on:
        ql = question.lower()
        is_njangi = any(k in ql for k in ["contribution", "foundation", "loan", "payout", "interest", "fine", "member", "session", "njangi"])
        needs_web = any(k in ql for k in ["requirements", "steps", "permit", "license", "llc", "zoning", "tax", "law", "maryland"])
        # ✅ IMPORTANT: we do NOT treat plain "how" as a web request anymore (avoids dictionary spam)
        if needs_web and not is_njangi:
            with st.spinner("🌐 Searching the internet…"):
                internet_pack = _tavily_search(question)

    messages = _build_grounded_messages(
        snapshot=snapshot,
        question=question,
        member_fin=member_fin,
        internet_pack=internet_pack,
        chat_history=st.session_state["younchat_history"],
        member_id_focus=member_id_focus,
    )

    # If no HF token -> local grounded fallback
    if not hf_token:
        answer = _local_fallback_answer(snapshot, question, member_id_focus)
        st.session_state["younchat_history"].append({"role": "assistant", "content": answer})
        with st.chat_message("assistant"):
            st.markdown(answer)
        st.caption(f"HF mode used: local • Internet: {'ON' if internet_on else 'OFF'} • member_id: {member_id_focus or '—'}")
        return

    # Call HF
    with st.spinner("🤖 younchat is thinking…"):
        ok, text_or_err, mode = _hf_call(hf_model, hf_token, messages)

    if not ok:
        fallback = _local_fallback_answer(snapshot, question, member_id_focus)
        answer = f"{fallback}\n\n<small>⚠️ HF failed: {text_or_err}</small>"
        st.session_state["younchat_history"].append({"role": "assistant", "content": answer})
        with st.chat_message("assistant"):
            st.markdown(answer, unsafe_allow_html=True)
        st.caption(f"HF mode used: failed • Internet: {'ON' if internet_on else 'OFF'} • member_id: {member_id_focus or '—'}")
        return

    final = (text_or_err or "").strip()

    # Optional: attach sources at bottom (only if internet was used)
    if internet_pack and internet_pack.get("ok") and internet_pack.get("results"):
        src_lines = []
        for i, it in enumerate(internet_pack["results"], start=1):
            title = it.get("title") or "source"
            url = it.get("url") or ""
            if url:
                src_lines.append(f"{i}. [{title}]({url})")
        if src_lines:
            final += "\n\n---\n**Sources:**\n" + "\n".join(src_lines)

    st.session_state["younchat_history"].append({"role": "assistant", "content": final})
    with st.chat_message("assistant"):
        st.markdown(final)

    st.caption(f"HF mode used: {mode} • Internet: {'ON' if internet_on else 'OFF'} • member_id: {member_id_focus or '—'}")
