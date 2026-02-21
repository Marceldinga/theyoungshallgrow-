
# njangi_llm_panel.py
# =============================================================================
# 🤖 YOUNG — Hugging Face Foundation Model (Grounded)
# - Uses HF Inference API with HF_TOKEN from environment (Railway Variables)
# - Default model: mistralai/Mistral-7B-Instruct-v0.3 (works without gated access)
# - Answers are grounded on LIVE Njangi snapshot pulled from Supabase
# - Safe mode fallback if HF call fails or token missing
#
# Expected env vars (Railway):
#   HF_TOKEN = hf_...
#   HF_MODEL = optional (default below)
#
# Works with your app.py that calls:
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
        q = sb.schema(schema).table(table).select(cols).limit(limit)
        res = q.execute()
        data = getattr(res, "data", None) or []
        return pd.DataFrame(data)
    except Exception:
        # Try without schema (some clients ignore schema)
        try:
            q = sb.table(table).select(cols).limit(limit)
            res = q.execute()
            data = getattr(res, "data", None) or []
            return pd.DataFrame(data)
        except Exception as e2:
            st.warning(f"Could not read {schema}.{table}: {_api_msg(e2)}")
            return pd.DataFrame()


def _safe_sum(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
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
    session_id_col = _pick_col(sessions, ["id", "session_id"])
    session_date_col = _pick_col(sessions, ["date", "session_date", "held_on", "created_at"])

    # totals
    contrib_amt_col = _pick_col(contributions, ["amount", "contribution_amount", "paid_amount"])
    foundation_amt_col = _pick_col(foundation, ["amount", "base_amount", "foundation_amount"])
    fines_amt_col = _pick_col(fines, ["amount", "fine_amount"])
    payout_amt_col = _pick_col(payouts, ["amount", "payout_amount"])
    interest_amt_col = _pick_col(interest_ledger, ["amount", "interest_amount", "interest"])

    loans_principal_col = _pick_col(loans, ["principal", "principal_amount", "amount"])
    loans_principal_current_col = _pick_col(loans, ["principal_current", "balance", "outstanding_principal"])
    loans_status_col = _pick_col(loans, ["status"])
    loans_member_col = _pick_col(loans, ["member_id", "borrower_id"])

    # Build small “facts” summary (numbers only, no guess)
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
            "contributions_total": _safe_sum(contributions, contrib_amt_col) if contrib_amt_col else 0.0,
            "foundation_total": _safe_sum(foundation, foundation_amt_col) if foundation_amt_col else 0.0,
            "fines_total": _safe_sum(fines, fines_amt_col) if fines_amt_col else 0.0,
            "payouts_total": _safe_sum(payouts, payout_amt_col) if payout_amt_col else 0.0,
            "interest_total": _safe_sum(interest_ledger, interest_amt_col) if interest_amt_col else 0.0,
        },
        "columns": {
            "members_name_col": name_col,
            "members_id_col": member_id_col,
            "sessions_id_col": session_id_col,
            "sessions_date_col": session_date_col,
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
        # Minimal tables for grounding / member questions
        "members_preview": (members[[c for c in [member_id_col, name_col] if c in members.columns]].head(50).to_dict("records")
                            if not members.empty and member_id_col and name_col else []),
        # Keep raw dfs in memory for local computations (not sent to model unless needed)
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
# Member resolver
# -----------------------------------------------------------------------------
def _resolve_member_id(snapshot: Dict[str, Any], text: str) -> Optional[str]:
    members = snapshot.get("_raw", {}).get("members", pd.DataFrame())
    if members is None or members.empty:
        return None

    id_col = snapshot["columns"].get("members_id_col")
    name_col = snapshot["columns"].get("members_name_col")
    if not id_col or not name_col or id_col not in members.columns or name_col not in members.columns:
        return None

    t = (text or "").strip().lower()

    # If user typed an ID
    if t.isdigit():
        matches = members[members[id_col].astype(str) == t]
        if not matches.empty:
            return str(matches.iloc[0][id_col])

    # Match by name contains
    members["_name_lc"] = members[name_col].astype(str).str.lower()
    hits = members[members["_name_lc"].str.contains(re.escape(t), na=False)]
    if not hits.empty:
        return str(hits.iloc[0][id_col])

    return None


# -----------------------------------------------------------------------------
# Hugging Face call
# -----------------------------------------------------------------------------
def _hf_chat(model: str, token: str, messages: List[Dict[str, str]], timeout: int = 45) -> Tuple[bool, str]:
    """
    Uses HF Inference API "chat completions"-like endpoint used by some providers.
    Fallback to text-generation endpoint if needed.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 1) Try HF Inference "models" endpoint (text generation)
    url = f"https://api-inference.huggingface.co/models/{model}"

    payload = {
        "inputs": _messages_to_prompt(messages),
        "parameters": {
            "max_new_tokens": 400,
            "temperature": 0.2,
            "return_full_text": False,
        }
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if r.status_code >= 400:
            return False, f"HF error {r.status_code}: {r.text[:200]}"
        data = r.json()
        # Common format: [{"generated_text": "..."}]
        if isinstance(data, list) and data and isinstance(data[0], dict) and "generated_text" in data[0]:
            return True, str(data[0]["generated_text"]).strip()
        # Sometimes returns dict with "generated_text"
        if isinstance(data, dict) and "generated_text" in data:
            return True, str(data["generated_text"]).strip()
        return False, f"HF returned unexpected format: {str(data)[:200]}"
    except Exception as e:
        return False, str(e)


def _messages_to_prompt(messages: List[Dict[str, str]]) -> str:
    # Simple, robust formatting that works with most instruct models
    # (We keep it short to reduce token usage and cost.)
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
# Grounded answer builder (LLM only formats + intent; Python computes)
# -----------------------------------------------------------------------------
def _compute_member_financials(snapshot: Dict[str, Any], member_id: str) -> Dict[str, Any]:
    raw = snapshot.get("_raw", {})
    members = raw.get("members", pd.DataFrame())
    contributions = raw.get("contributions", pd.DataFrame())
    foundation = raw.get("foundation", pd.DataFrame())
    loans = raw.get("loans", pd.DataFrame())
    loan_payments = raw.get("loan_payments", pd.DataFrame())
    fines = raw.get("fines", pd.DataFrame())
    interest_ledger = raw.get("interest_ledger", pd.DataFrame())

    cols = snapshot["columns"]
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
    if not members.empty and mem_id_col in members.columns:
        mm = members[members[mem_id_col].astype(str) == str(member_id)]
        if not mm.empty:
            member_row = mm.iloc[0].to_dict()

    # Contributions
    contrib_total = 0.0
    if not contributions.empty and "member_id" in contributions.columns and contrib_amt_col in contributions.columns:
        df = contributions[contributions["member_id"].astype(str) == str(member_id)]
        contrib_total = _safe_sum(df, contrib_amt_col)

    # Foundation
    foundation_total = 0.0
    if not foundation.empty and "member_id" in foundation.columns and found_amt_col in foundation.columns:
        df = foundation[foundation["member_id"].astype(str) == str(member_id)]
        foundation_total = _safe_sum(df, found_amt_col)

    # Fines
    fines_total = 0.0
    if not fines.empty and "member_id" in fines.columns and fines_amt_col in fines.columns:
        df = fines[fines["member_id"].astype(str) == str(member_id)]
        fines_total = _safe_sum(df, fines_amt_col)

    # Loans summary
    loans_count = 0
    active_balance = 0.0
    active_unpaid_interest = 0.0

    if not loans.empty and loans_member_col in loans.columns:
        ldf = loans[loans[loans_member_col].astype(str) == str(member_id)]
        loans_count = int(len(ldf))

        # active filter
        if loans_status_col and loans_status_col in ldf.columns:
            active = ldf[ldf[loans_status_col].astype(str).str.lower().isin(["active", "open", "ongoing", "overdue"])]
        else:
            active = ldf

        if loans_principal_current_col and loans_principal_current_col in active.columns:
            active_balance = _safe_sum(active, loans_principal_current_col)
        elif loans_principal_col and loans_principal_col in active.columns:
            active_balance = _safe_sum(active, loans_principal_col)

        # unpaid_interest if exists
        if "unpaid_interest" in active.columns:
            active_unpaid_interest = _safe_sum(active, "unpaid_interest")

    # Interest ledger (per member)
    interest_total = 0.0
    if not interest_ledger.empty and "member_id" in interest_ledger.columns and interest_amt_col in interest_ledger.columns:
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


def _local_fallback_answer(snapshot: Dict[str, Any], question: str, selected_member_id: Optional[str]) -> str:
    q = (question or "").lower().strip()

    # Basic totals
    if any(k in q for k in ["total", "overall", "all members"]) and any(k in q for k in ["contribution", "contributions"]):
        return f"Total contributions (all members): {snapshot['totals']['contributions_total']:.2f}"

    if "foundation" in q and any(k in q for k in ["total", "overall", "all"]):
        return f"Total foundation contributions (all members): {snapshot['totals']['foundation_total']:.2f}"

    # Member summary
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

    # Default
    return (
        "I can answer from LIVE Njangi data.\n"
        "Try asking:\n"
        "- 'Total contributions this year?'\n"
        "- 'Show me Marcel's loan status'\n"
        "- 'Total foundation money?'\n"
        "- 'Who has the highest active loan balance?'"
    )


def _build_grounded_messages(snapshot: Dict[str, Any], question: str, member_fin: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    # Keep prompt compact: totals + counts + optional member summary
    sys = (
        "You are Young, a finance assistant for a Njangi app. "
        "You MUST answer ONLY using the provided SNAPSHOT FACTS. "
        "If a fact is missing, say 'I don’t have that in the snapshot.' "
        "Do not guess. Do not invent members, loans, or amounts. "
        "When giving amounts, keep them as numbers exactly from snapshot."
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
        "Answer clearly in 3–8 bullet points max. If user asks for calculation, use given totals."
    )

    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


# -----------------------------------------------------------------------------
# UI entry point
# -----------------------------------------------------------------------------
def render_njangi_llm_panel(sb_anon, sb_service, schema: str) -> None:
    st.subheader("🤖 Young — Hugging Face AI Helper", anchor=False)

    # Config
    hf_token = os.getenv("HF_TOKEN", "").strip()
    hf_model = os.getenv("HF_MODEL", "").strip() or "mistralai/Mistral-7B-Instruct-v0.3"

    with st.expander("🔧 AI Settings", expanded=False):
        st.write("**Model**:", hf_model)
        st.write("**HF_TOKEN present**:", "✅ Yes" if hf_token else "❌ No (set HF_TOKEN in Railway Variables)")
        st.caption("Tip: Llama models are often gated. Mistral-7B-Instruct-v0.3 usually works immediately.")

    # Build snapshot (cache lightly)
    @st.cache_data(ttl=30, show_spinner=False)
    def _cached_snapshot(_ts: int) -> Dict[str, Any]:
        return _build_snapshot(sb_anon, sb_service, schema)

    # “_ts” makes cache refresh without using unhashable clients
    snapshot = _cached_snapshot(int(time.time() // 10))

    # Member select
    members_preview = snapshot.get("members_preview", [])
    member_options = []
    for r in members_preview:
        # show "id • name"
        rid = r.get(snapshot["columns"].get("members_id_col") or "id")
        rname = r.get(snapshot["columns"].get("members_name_col") or "name")
        if rid is not None and rname is not None:
            member_options.append((str(rid), f"{rid} • {rname}"))

    selected_member_id = None
    if member_options:
        label_map = {lbl: mid for (mid, lbl) in member_options}
        chosen = st.selectbox("Select member (optional)", ["(None)"] + [lbl for _, lbl in member_options], index=0)
        if chosen != "(None)":
            selected_member_id = label_map.get(chosen)

    # Chat input
    question = st.text_input("Ask Young (grounded on live snapshot)", placeholder="e.g., Total foundation money? What is my loan status?")
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

    member_fin = None
    if selected_member_id:
        member_fin = _compute_member_financials(snapshot, selected_member_id)

    # If no token, fallback
    if not hf_token:
        st.info("HF_TOKEN is missing, using local fallback (still grounded).")
        st.code(_local_fallback_answer(snapshot, question, selected_member_id))
        return

    messages = _build_grounded_messages(snapshot, question, member_fin)

    with st.spinner("Calling Hugging Face model..."):
        ok, text = _hf_chat(hf_model, hf_token, messages)

    if not ok:
        st.warning(f"Hugging Face call failed. Using fallback.\n\nDetails: {text}")
        st.code(_local_fallback_answer(snapshot, question, selected_member_id))
        return

    st.markdown(text)
