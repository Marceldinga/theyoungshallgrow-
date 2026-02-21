
# njangi_llm_panel.py
# =============================================================================
# 🤖 YOUNG — Hugging Face Router (Grounded) ✅ SINGLE COMPLETE FILE
#
# ✅ Uses Hugging Face Router (OpenAI-compatible):
#   - Chat models:        https://router.huggingface.co/v1/chat/completions
#   - Instruct models:    https://router.huggingface.co/v1/completions
#
# ✅ IMPORTANT:
#   - mistralai/Mistral-7B-Instruct-v0.3 is typically NOT a chat model
#   - So we default to /v1/completions (more reliable)
#   - Optional fallback to chat (if you force it)
#
# ✅ Grounded:
#   - Pulls LIVE snapshot from Supabase tables (NJANGI STANDARD)
#   - LLM only formats answer; numbers come from snapshot
#
# ✅ Railway env vars:
#   HF_TOKEN = hf_...
#   HF_MODEL = (optional) default: mistralai/Mistral-7B-Instruct-v0.3
#   HF_FORCE_MODE = auto | completions | chat   (optional; default: auto)
#
# Works with app.py that calls:
#   render_njangi_llm_panel(sb_anon=..., sb_service=..., schema=...)
# =============================================================================

from __future__ import annotations

import json
import os
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


# -----------------------------------------------------------------------------
# Time helpers
# -----------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
def _sb_select(sb_anon, sb_service, schema: str, table: str, cols: str = "*", limit: int = 1000) -> pd.DataFrame:
    sb = sb_service or sb_anon
    if sb is None:
        return pd.DataFrame()

    try:
        res = sb.schema(schema).table(table).select(cols).limit(limit).execute()
        data = getattr(res, "data", None) or []
        return pd.DataFrame(data)
    except Exception:
        # Try without schema (some clients ignore schema)
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
    members = _sb_select(sb_anon, sb_service, schema, "members", cols="*", limit=2000)
    sessions = _sb_select(sb_anon, sb_service, schema, "sessions", cols="*", limit=2000)
    contributions = _sb_select(sb_anon, sb_service, schema, "contributions", cols="*", limit=5000)
    foundation = _sb_select(sb_anon, sb_service, schema, "foundation_contributions", cols="*", limit=5000)
    loans = _sb_select(sb_anon, sb_service, schema, "loans", cols="*", limit=2000)
    loan_payments = _sb_select(sb_anon, sb_service, schema, "loan_payments", cols="*", limit=5000)
    fines = _sb_select(sb_anon, sb_service, schema, "fines", cols="*", limit=5000)
    payouts = _sb_select(sb_anon, sb_service, schema, "payouts", cols="*", limit=2000)
    interest_ledger = _sb_select(sb_anon, sb_service, schema, "interest_ledger", cols="*", limit=5000)

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
        "members_preview": (
            members[[c for c in [member_id_col, name_col] if c and c in members.columns]]
            .head(50)
            .to_dict("records")
            if not members.empty and member_id_col and name_col
            else []
        ),
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

    # Contributions
    contrib_total = 0.0
    if not contributions.empty and "member_id" in contributions.columns:
        df = contributions[contributions["member_id"].astype(str) == str(member_id)]
        contrib_total = _safe_sum(df, contrib_amt_col)

    # Foundation
    foundation_total = 0.0
    if not foundation.empty and "member_id" in foundation.columns:
        df = foundation[foundation["member_id"].astype(str) == str(member_id)]
        foundation_total = _safe_sum(df, found_amt_col)

    # Fines
    fines_total = 0.0
    if not fines.empty and "member_id" in fines.columns:
        df = fines[fines["member_id"].astype(str) == str(member_id)]
        fines_total = _safe_sum(df, fines_amt_col)

    # Loans summary
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

    # Interest ledger (per member)
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
# Prompt builders
# -----------------------------------------------------------------------------
def _build_grounded_messages(snapshot: Dict[str, Any], question: str, member_fin: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    sys = (
        "You are Young, a finance assistant for a Njangi app. "
        "You MUST answer ONLY using the provided SNAPSHOT FACTS. "
        "If a fact is missing, say 'I don’t have that in the snapshot.' "
        "Do not guess. Do not invent members, loans, or amounts. "
        "Keep numbers exactly as given."
    )

    facts = {
        "generated_at_utc": snapshot.get("generated_at_utc"),
        "counts": snapshot.get("counts", {}),
        "totals": snapshot.get("totals", {}),
    }

    content = "SNAPSHOT_FACTS:\n" + json.dumps(facts, indent=2)
    if member_fin:
        content += "\n\nSELECTED_MEMBER_FACTS:\n" + json.dumps(member_fin, indent=2)

    user = (
        f"{content}\n\n"
        f"User question: {question}\n\n"
        "Answer clearly in 3–8 bullet points max."
    )

    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def _messages_to_prompt(messages: List[Dict[str, str]]) -> str:
    # For /v1/completions we provide a single prompt string
    out = []
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
# Hugging Face Router calls
# -----------------------------------------------------------------------------
def _hf_router_chat(model: str, token: str, messages: List[Dict[str, str]], timeout: int = 60) -> Tuple[bool, str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 400}

    try:
        r = requests.post(HF_ROUTER_CHAT_URL, headers=headers, json=payload, timeout=timeout)
        if r.status_code >= 400:
            return False, f"HF error {r.status_code}: {r.text[:500]}"
        data = r.json()
        text = (((data.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
        return True, str(text).strip()
    except Exception as e:
        return False, str(e)


def _hf_router_completions(model: str, token: str, prompt: str, timeout: int = 60) -> Tuple[bool, str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"model": model, "prompt": prompt, "temperature": 0.2, "max_tokens": 400}

    try:
        r = requests.post(HF_ROUTER_COMPLETIONS_URL, headers=headers, json=payload, timeout=timeout)
        if r.status_code >= 400:
            return False, f"HF error {r.status_code}: {r.text[:500]}"
        data = r.json()
        # OpenAI completions format: {"choices":[{"text":"..."}]}
        text = ((data.get("choices") or [{}])[0].get("text") or "")
        return True, str(text).strip()
    except Exception as e:
        return False, str(e)


def _hf_call(model: str, token: str, messages: List[Dict[str, str]]) -> Tuple[bool, str, str]:
    """
    Returns (ok, text_or_error, mode_used)
    mode_used: "completions" | "chat" | "failed"
    """
    force = (os.getenv("HF_FORCE_MODE", "") or "auto").strip().lower()
    prompt = _messages_to_prompt(messages)

    # Heuristic: Instruct models work best with /v1/completions
    model_lc = (model or "").lower()
    looks_instruct = any(x in model_lc for x in ["instruct", "instruction", "mistral-7b-instruct", "llama-3", "llama-3.1"])

    # Decide order
    if force == "chat":
        order = ["chat"]
    elif force == "completions":
        order = ["completions"]
    else:
        # auto
        order = ["completions", "chat"] if looks_instruct else ["chat", "completions"]

    last_err = ""
    for mode in order:
        if mode == "completions":
            ok, txt = _hf_router_completions(model, token, prompt)
            if ok and txt:
                return True, txt, "completions"
            last_err = txt
        else:
            ok, txt = _hf_router_chat(model, token, messages)
            if ok and txt:
                return True, txt, "chat"
            last_err = txt

    return False, last_err or "Unknown HF error", "failed"


# -----------------------------------------------------------------------------
# Local fallback (still grounded)
# -----------------------------------------------------------------------------
def _local_fallback_answer(snapshot: Dict[str, Any], question: str, selected_member_id: Optional[str]) -> str:
    q = (question or "").lower().strip()

    if "contribution" in q and any(k in q for k in ["total", "overall", "all"]):
        return f"Total contributions (all members): {snapshot['totals']['contributions_total']:.2f}"

    if "foundation" in q and any(k in q for k in ["total", "overall", "all"]):
        return f"Total foundation contributions (all members): {snapshot['totals']['foundation_total']:.2f}"

    if selected_member_id:
        fin = _compute_member_financials(snapshot, selected_member_id)
        if any(k in q for k in ["my", "member", "summary", "status", "loan", "contribution", "foundation", "fine", "interest"]):
            return (
                f"Member: {fin['member_name']} (ID {fin['member_id']})\n"
                f"- Contributions total: {fin['contributions_total']:.2f}\n"
                f"- Foundation total: {fin['foundation_total']:.2f}\n"
                f"- Fines total: {fin['fines_total']:.2f}\n"
                f"- Loans count: {fin['loans_count']}\n"
                f"- Active loan balance: {fin['active_loan_balance']:.2f}\n"
                f"- Active unpaid interest: {fin['active_unpaid_interest']:.2f}\n"
                f"- Interest ledger total: {fin['interest_total']:.2f}"
            )

    return (
        "I can answer from LIVE Njangi data.\n"
        "Try asking:\n"
        "- 'Total contributions?'\n"
        "- 'Total foundation money?'\n"
        "- 'Show my loan status'\n"
        "- 'Who has the highest active loan balance?'"
    )


# -----------------------------------------------------------------------------
# UI entry point
# -----------------------------------------------------------------------------
def render_njangi_llm_panel(sb_anon, sb_service, schema: str) -> None:
    st.subheader("🤖 Young — Hugging Face AI Helper", anchor=False)

    hf_token = os.getenv("HF_TOKEN", "").strip()
    hf_model = os.getenv("HF_MODEL", "").strip() or "mistralai/Mistral-7B-Instruct-v0.3"
    hf_force = (os.getenv("HF_FORCE_MODE", "") or "auto").strip().lower()

    with st.expander("🔧 AI Settings", expanded=False):
        st.write("**Model**:", hf_model)
        st.write("**HF_TOKEN present**:", "✅ Yes" if hf_token else "❌ No (set HF_TOKEN in Railway Variables)")
        st.write("**HF_FORCE_MODE**:", hf_force)
        st.caption("Router endpoints: /v1/completions (instruct) and /v1/chat/completions (chat models)")

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
        chosen = st.selectbox("Select member (optional)", ["(None)"] + [lbl for _, lbl in member_options], index=0)
        if chosen != "(None)":
            selected_member_id = label_map.get(chosen)

    question = st.text_input(
        "Ask Young (grounded on live snapshot)",
        placeholder="e.g., Total foundation money? What is my loan status?"
    )

    colA, colB = st.columns([1, 1], gap="small")
    ask = colA.button("Ask", type="primary", use_container_width=True)
    refresh = colB.button("Refresh snapshot", use_container_width=True)

    if refresh:
        st.cache_data.clear()
        st.rerun()

    if not ask:
        st.caption("Young will answer only from what is inside the live database snapshot.")
        return

    if not question.strip():
        st.warning("Type a question first.")
        return

    member_fin = _compute_member_financials(snapshot, selected_member_id) if selected_member_id else None
    messages = _build_grounded_messages(snapshot, question, member_fin)

    if not hf_token:
        st.info("HF_TOKEN is missing, using local fallback (still grounded).")
        st.code(_local_fallback_answer(snapshot, question, selected_member_id))
        return

    with st.spinner("Calling Hugging Face (Router)..."):
        ok, text_or_err, mode = _hf_call(hf_model, hf_token, messages)

    if not ok:
        st.warning(f"Hugging Face call failed. Using fallback.\n\nDetails: {text_or_err}")
        st.code(_local_fallback_answer(snapshot, question, selected_member_id))
        return

    st.caption(f"✅ Hugging Face mode used: {mode}")
    st.markdown(text_or_err)
