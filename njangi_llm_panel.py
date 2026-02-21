
# njangi_llm_panel.py
# =============================================================================
# 💬 YOUR CHAT — Hugging Face Router + Optional Internet Search ✅ SINGLE COMPLETE FILE
#
# ✅ What you asked for:
#   1) The ONLY name shown is: "Your Chat" (no "Young" anywhere)
#   2) Chats like a human (chat UI + conversation memory)
#   3) "Internet is ON" when you provide a web key (Tavily). If no key, it stays OFF safely.
#   4) Still GROUNDED on live Njangi snapshot for ALL Njangi numbers (no guessing).
#
# ✅ Hugging Face Router (OpenAI-compatible):
#   - Chat:         https://router.huggingface.co/v1/chat/completions
#   - Completions:  https://router.huggingface.co/v1/completions
#
# ✅ Railway env vars:
#   HF_TOKEN = hf_...
#   HF_MODEL = mistralai/Mistral-7B-Instruct-v0.2   (default)
#   HF_FORCE_MODE = auto | completions | chat       (default auto)
#
# ✅ Optional Internet Search (Tavily):
#   TAVILY_API_KEY = tvly-...
#   INTERNET_MODE = on | off      (default: on if key exists, else off)
#
# Works with app.py:
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

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


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
            .head(100)
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
    # default: ON if key exists
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
        # keep only small safe fields
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
) -> List[Dict[str, str]]:
    # NOTE: We keep a strict rule: Njangi numbers MUST come from snapshot only.
    sys = (
        "You are **Your Chat**, a friendly, human-like assistant for a Njangi finance app.\n"
        "CRITICAL RULES:\n"
        "1) For ANY Njangi totals, counts, members, loans, payouts, contributions, interest, fines — use ONLY SNAPSHOT_FACTS.\n"
        "2) If something is missing in the snapshot, say: 'I don’t have that in the snapshot.'\n"
        "3) You MAY use INTERNET_SOURCES only for general questions (definitions, laws, how-to), NOT for Njangi numbers.\n"
        "4) Be concise, friendly, and conversational. Use bullets only when it helps.\n"
    )

    facts = {
        "generated_at_utc": snapshot.get("generated_at_utc"),
        "counts": snapshot.get("counts", {}),
        "totals": snapshot.get("totals", {}),
    }

    content = "SNAPSHOT_FACTS:\n" + json.dumps(facts, indent=2)
    if member_fin:
        content += "\n\nSELECTED_MEMBER_FACTS:\n" + json.dumps(member_fin, indent=2)

    if internet_pack and internet_pack.get("ok") and internet_pack.get("results"):
        content += "\n\nINTERNET_SOURCES:\n" + json.dumps(internet_pack.get("results"), indent=2)

    # Build messages with short history (last 8 turns)
    msgs: List[Dict[str, str]] = [{"role": "system", "content": sys}]
    for m in chat_history[-16:]:  # 8 turns (user+assistant)
        if m.get("role") in ("user", "assistant"):
            msgs.append({"role": m["role"], "content": m.get("content", "")})

    user = (
        f"{content}\n\n"
        f"User message: {question}\n\n"
        "Respond as Your Chat."
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
# Hugging Face Router calls
# -----------------------------------------------------------------------------
def _hf_router_chat(model: str, token: str, messages: List[Dict[str, str]], timeout: int = 60) -> Tuple[bool, str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": 0.3, "max_tokens": 500}

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
    payload = {"model": model, "prompt": prompt, "temperature": 0.3, "max_tokens": 500}

    try:
        r = requests.post(HF_ROUTER_COMPLETIONS_URL, headers=headers, json=payload, timeout=timeout)
        if r.status_code >= 400:
            return False, f"HF error {r.status_code}: {r.text[:500]}"
        data = r.json()
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

    model_lc = (model or "").lower()
    looks_instruct = any(x in model_lc for x in ["instruct", "instruction", "mistral-7b-instruct", "llama-3", "llama-3.1"])

    if force == "chat":
        order = ["chat"]
    elif force == "completions":
        order = ["completions"]
    else:
        # Auto: prefer completions for instruct-style models
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
    if "hello" in q or "hi" in q:
        return "Hi 👋🏽 I’m **Your Chat**. Ask me anything about your Njangi snapshot (totals, loans, payouts, etc.)."

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
        "I can answer from your LIVE Njangi snapshot.\n"
        "Try:\n"
        "- Total contributions?\n"
        "- Total foundation money?\n"
        "- Show my loan status\n"
        "- Who has the highest active loan balance?"
    )


# -----------------------------------------------------------------------------
# UI entry point (CHAT UI)
# -----------------------------------------------------------------------------
def render_njangi_llm_panel(sb_anon, sb_service, schema: str) -> None:
    # ✅ ONLY NAME shown
    st.subheader("💬 Your Chat", anchor=False)

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
        chosen = st.selectbox("Optional: choose a member (for 'my status')", ["(None)"] + [lbl for _, lbl in member_options], index=0)
        if chosen != "(None)":
            selected_member_id = label_map.get(chosen)

    # Initialize chat history
    if "your_chat_history" not in st.session_state:
        st.session_state["your_chat_history"] = []
        st.session_state["your_chat_history"].append(
            {"role": "assistant", "content": "Hi 👋🏽 I’m **Your Chat**. Ask me about your Njangi snapshot or general questions (internet on if enabled)."}
        )

    # Show chat
    for m in st.session_state["your_chat_history"]:
        role = m.get("role", "assistant")
        with st.chat_message("assistant" if role == "assistant" else "user"):
            st.markdown(m.get("content", ""))

    colA, colB = st.columns([1, 1], gap="small")
    if colA.button("🔄 Refresh snapshot", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    if colB.button("🧹 Clear chat", use_container_width=True):
        st.session_state["your_chat_history"] = [
            {"role": "assistant", "content": "Hi 👋🏽 I’m **Your Chat**. Ask me something."}
        ]
        st.rerun()

    # Chat input
    question = st.chat_input("Type your message…")
    if not question:
        return

    # Add user message
    st.session_state["your_chat_history"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    member_fin = _compute_member_financials(snapshot, selected_member_id) if selected_member_id else None

    # Optional internet pack: only when enabled AND question looks like a general web question
    internet_pack: Optional[Dict[str, Any]] = None
    if internet_on:
        # Heuristic: use internet when user asks "how", "what is", "requirements", "steps", etc.
        ql = question.lower()
        needs_web = any(k in ql for k in ["how", "what is", "requirements", "steps", "permit", "license", "llc", "tax", "law", "maryland", "railway", "hugging face"])
        if needs_web:
            with st.spinner("🌐 Searching the internet…"):
                internet_pack = _tavily_search(question)

    messages = _build_grounded_messages(
        snapshot=snapshot,
        question=question,
        member_fin=member_fin,
        internet_pack=internet_pack,
        chat_history=st.session_state["your_chat_history"],
    )

    # If no HF token -> local grounded fallback
    if not hf_token:
        answer = _local_fallback_answer(snapshot, question, selected_member_id)
        st.session_state["your_chat_history"].append({"role": "assistant", "content": answer})
        with st.chat_message("assistant"):
            st.markdown(answer)
        return

    # Call HF
    with st.spinner("🤖 Your Chat is thinking…"):
        ok, text_or_err, mode = _hf_call(hf_model, hf_token, messages)

    if not ok:
        # Use fallback, but show error in a small note
        fallback = _local_fallback_answer(snapshot, question, selected_member_id)
        answer = f"{fallback}\n\n<small>⚠️ HF failed: {text_or_err}</small>"
        st.session_state["your_chat_history"].append({"role": "assistant", "content": answer})
        with st.chat_message("assistant"):
            st.markdown(answer, unsafe_allow_html=True)
        return

    # Optional: attach sources at bottom (only if internet was used)
    final = text_or_err.strip()
    if internet_pack and internet_pack.get("ok") and internet_pack.get("results"):
        src_lines = []
        for i, it in enumerate(internet_pack["results"], start=1):
            title = it.get("title") or "source"
            url = it.get("url") or ""
            if url:
                src_lines.append(f"{i}. [{title}]({url})")
        if src_lines:
            final += "\n\n---\n**Sources:**\n" + "\n".join(src_lines)

    # Save + display
    st.session_state["your_chat_history"].append({"role": "assistant", "content": final})
    with st.chat_message("assistant"):
        st.markdown(final)

    # Small debug caption (does not change the name shown)
    st.caption(f"HF mode used: {mode} • Internet: {'ON' if internet_on else 'OFF'}")
