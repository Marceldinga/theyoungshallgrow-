
# njangi_llm_panel.py ✅ SINGLE COMPLETE FILE — younchat reads your DB (members = source of truth)
# =============================================================================
# 💬 younchat — DB-TOOLS FIRST (tables + views) + ✅ PDF tools + Optional HF Router + Optional Tavily
#
# ✅ YOUR REQUEST (IMPORTANT):
#   - The ONLY intro message must be EXACTLY:
#       "Hello 👋🏽 I’m younchat — your Njangi assistant."
#   - No extra intro text in the intro.
#   - Salute must be "Hello" (enforced for ALL replies incl HF)
#   - younchat reads your DB from an allowlist (RELATIONS)
#   - members table is the source of truth for identity (name display)
#   - NO hallucinations for Njangi numbers (all financial answers come from DB)
#   - HF Router will NEVER answer DB / PDF commands
#
# Extra:
#   - ✅ Can access and display ALL columns for your actual tables (select "*")
#   - ✅ "health" + "counts" commands to assess all tables quickly
#   - ✅ Optional "random model routing" for HF (general chat ONLY, never DB/PDF)
#   - ✅ Supports ~40 model pool (random) for HF router general chat
#   - ✅ HF "model_not_supported" / provider errors auto-skip to next model
#
# Railway env vars (optional):
#   HF_TOKEN
#   HF_MODEL
#   HF_FORCE_MODE = auto | completions | chat
#   HF_RANDOM_DEFAULT = on | off      (default random routing)
#   HF_MODELS_CSV                    (comma separated pool, optional)
#   HF_MODEL_DENYLIST_CSV            (comma separated denylist, optional)
#   TAVILY_API_KEY
#   INTERNET_MODE = on | off
# =============================================================================

from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
import requests
import streamlit as st

try:
    from postgrest.exceptions import APIError
except Exception:
    APIError = Exception  # type: ignore

# PDF deps (ReportLab)
try:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
except Exception:
    LETTER = None  # type: ignore
    inch = None  # type: ignore
    canvas = None  # type: ignore


# ---------------------------
# External endpoints (optional)
# ---------------------------
HF_ROUTER_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"
HF_ROUTER_COMPLETIONS_URL = "https://router.huggingface.co/v1/completions"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"


# ---------------------------
# HF model pool (curated ~40)
# - Used ONLY for general chat (never DB/PDF)
# - You can override with Railway env var HF_MODELS_CSV
# ---------------------------
HF_FALLBACK_MODELS: List[str] = [
    # Llama 3 / 3.1 family
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "meta-llama/Meta-Llama-3-70B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
    "meta-llama/Llama-3.1-70B-Instruct",
    # Mistral family
    "mistralai/Mistral-7B-Instruct-v0.2",
    "mistralai/Mistral-7B-Instruct-v0.3",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
    "mistralai/Mixtral-8x22B-Instruct-v0.1",
    # Nous Hermes (✅ removed bad model)
    "NousResearch/Nous-Hermes-2-Yi-34B",
    "NousResearch/Nous-Hermes-2-Mistral-7B-DPO",
    # Qwen instruct
    "Qwen/Qwen2-7B-Instruct",
    "Qwen/Qwen2-14B-Instruct",
    "Qwen/Qwen2-72B-Instruct",
    # DeepSeek chat
    "deepseek-ai/deepseek-llm-7b-chat",
    "deepseek-ai/deepseek-llm-67b-chat",
    # OpenChat
    "openchat/openchat-3.5-0106",
    "openchat/openchat-3.5-1210",
    # Zephyr
    "HuggingFaceH4/zephyr-7b-beta",
    "HuggingFaceH4/zephyr-7b-alpha",
    # Phi instruct
    "microsoft/Phi-3-mini-4k-instruct",
    "microsoft/Phi-3-small-8k-instruct",
    "microsoft/Phi-3-medium-4k-instruct",
    # Yi chat
    "01-ai/Yi-6B-Chat",
    "01-ai/Yi-34B-Chat",
    # Gemma instruct
    "google/gemma-2b-it",
    "google/gemma-7b-it",
    # Falcon instruct
    "tiiuae/falcon-7b-instruct",
    "tiiuae/falcon-40b-instruct",
    # StableLM chat
    "stabilityai/stablelm-2-1_6b-chat",
    "stabilityai/stablelm-2-12b-chat",
    # Orca style
    "Open-Orca/Mistral-7B-OpenOrca",
    "Open-Orca/Mixtral-8x7B-OpenOrca",
    # Command R family
    "CohereForAI/c4ai-command-r-v01",
    "CohereForAI/c4ai-command-r-plus-v01",
    # Misc strong instruct
    "teknium/OpenHermes-2.5-Mistral-7B",
    "teknium/OpenHermes-2.5-Mixtral-8x7B",
    "allenai/tulu-2-dpo-7b",
    "allenai/tulu-2-dpo-13b",
]

# ✅ Hard denylist (permanent) + optional env denylist
HF_HARD_DENYLIST = {
    "NousResearch/Nous-Hermes-2-Mixtral-8x7B-DPO",
}

HF_MODEL_DENYLIST = set(
    m.strip() for m in (os.getenv("HF_MODEL_DENYLIST_CSV") or "").split(",") if m.strip()
)
HF_MODEL_DENYLIST |= HF_HARD_DENYLIST

# ✅ Allowlist relations (tables + views).
RELATIONS: Dict[str, Dict[str, Any]] = {
    # Core tables
    "members": {"type": "table", "truth": True},
    "sessions": {"type": "table"},
    "app_state": {"type": "table"},
    "contributions": {"type": "table"},
    "foundation_contributions": {"type": "table"},
    "fines": {"type": "table"},
    "loans": {"type": "table"},
    "loan_payments": {"type": "table"},
    "loan_requests": {"type": "table"},
    "loan_repayments_pending": {"type": "table"},
    "interest_ledger": {"type": "table"},
    "payouts": {"type": "table"},
    "minutes": {"type": "table"},
    "attendance": {"type": "table"},
    "signatures": {"type": "table"},
    "audit_log": {"type": "table"},
    # Optional/extra
    "profiles": {"type": "table"},
    "ml_training_data": {"type": "table"},
    "member_contribution_totals": {"type": "table"},
    # Optional views
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
def _intro_only() -> str:
    # ✅ MUST be exact
    return "Hello 👋🏽 I’m younchat — your Njangi assistant."


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _force_hello_prefix(text: str) -> str:
    """Enforce salute rule for every response (including HF)."""
    t = (text or "").strip()
    if not t:
        return "Hello 👋🏽"
    if not t.lower().startswith("hello"):
        return "Hello 👋🏽 " + t
    return t


# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------
def _api_msg(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or payload)
        return str(payload)
    return str(e)


# -----------------------------------------------------------------------------
# DB Read helpers (allowlist enforced)
# -----------------------------------------------------------------------------
_SBSelectReturn = Union[pd.DataFrame, Tuple[pd.DataFrame, bool, str]]


def _sb_select(
    sb_anon,
    sb_service,
    schema: str,
    relation: str,
    cols: str = "*",
    limit: int = 2000,
    filters: Optional[List[Tuple[str, str, Any]]] = None,
    order: Optional[Tuple[str, bool]] = None,
    prefer_service: bool = True,
    return_meta: bool = False,
) -> _SBSelectReturn:
    """
    filters: list of (column, op, value) where op in ["eq","gte","lte","ilike","in"]
    order: (column, asc)
    If return_meta=True -> returns (df, ok, err_msg)
    """
    if relation not in RELATIONS:
        return (pd.DataFrame(), False, "relation not allowlisted") if return_meta else pd.DataFrame()

    sb = (sb_service if (prefer_service and sb_service is not None) else None) or sb_anon
    if sb is None:
        return (pd.DataFrame(), False, "supabase client missing") if return_meta else pd.DataFrame()

    def _apply_filters(q):
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

    # Try schema-qualified first
    try:
        q = sb.schema(schema).table(relation).select(cols).limit(int(limit))
        q = _apply_filters(q)
        res = q.execute()
        df = pd.DataFrame(getattr(res, "data", None) or [])
        return (df, True, "") if return_meta else df
    except Exception:
        # Fallback to default schema
        try:
            q = sb.table(relation).select(cols).limit(int(limit))
            q = _apply_filters(q)
            res = q.execute()
            df = pd.DataFrame(getattr(res, "data", None) or [])
            return (df, True, "") if return_meta else df
        except Exception as e2:
            msg = _api_msg(e2)
            st.warning(f"Could not read {schema}.{relation}: {msg}")
            return (pd.DataFrame(), False, msg) if return_meta else pd.DataFrame()


def _sb_count(sb_anon, sb_service, schema: str, relation: str) -> Optional[int]:
    """Best-effort row count (may be blocked by RLS)."""
    if relation not in RELATIONS:
        return None
    sb = sb_service or sb_anon
    if sb is None:
        return None
    try:
        res = sb.schema(schema).table(relation).select("*", count="exact").limit(1).execute()
        c = getattr(res, "count", None)
        return int(c) if c is not None else None
    except Exception:
        try:
            res = sb.table(relation).select("*", count="exact").limit(1).execute()
            c = getattr(res, "count", None)
            return int(c) if c is not None else None
        except Exception:
            return None


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


def _coalesce_note_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if "note_text" in df.columns:
        return df
    cand = "notes" if "notes" in df.columns else ("note" if "note" in df.columns else None)
    if cand:
        df = df.copy()
        df["note_text"] = df[cand]
    return df


# -----------------------------------------------------------------------------
# Intent helpers
# -----------------------------------------------------------------------------
def _clean(text: str) -> str:
    return (text or "").strip()


def _lc(text: str) -> str:
    return _clean(text).lower()


def _wants_help(text: str) -> bool:
    return _lc(text) in {"help", "/help", "commands", "options"}


def _wants_tables_list(text: str) -> bool:
    return _lc(text) in {"tables", "relations", "views", "list tables", "list views"}


def _wants_health(text: str) -> bool:
    return _lc(text) in {"health", "db health", "check db", "check tables"}


def _wants_counts(text: str) -> bool:
    return _lc(text) in {"counts", "row counts", "table counts", "count tables"}


def _wants_describe(text: str) -> bool:
    t = _lc(text)
    return t.startswith("describe ") or t.startswith("columns ") or t.startswith("cols ") or t.startswith("schema ")


def _wants_show_table(text: str) -> bool:
    t = _lc(text)
    return t.startswith("show ") or t.startswith("preview ") or t.startswith("open ")


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
        "list all the members",
        "list members id",
        "list all members id",
        "members id",
        "member ids",
    ]
    return t in {"members", "member"} or any(p in t for p in phrases)


def _wants_kpis(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["kpi", "kpis", "finance kpi", "finance kpis", "dashboard kpi"])


def _wants_loans(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["loan", "loans", "borrow", "repay", "overdue", "dpd", "interest due"])


def _wants_internet(text: str) -> bool:
    t = _lc(text)
    return t.startswith("web:") or t.startswith("internet:") or t.startswith("tavily:")


def _strip_web_prefix(q: str) -> str:
    return re.sub(r"^(web:|internet:|tavily:)\s*", "", (q or "").strip(), flags=re.IGNORECASE).strip()


# PDF commands (only minutes + attendance enabled)
def _wants_pdf_minutes(text: str) -> bool:
    t = _lc(text)
    return ("pdf" in t and "minutes" in t) or t.startswith("minutes pdf") or t.startswith("pdf minutes")


def _wants_pdf_attendance(text: str) -> bool:
    t = _lc(text)
    return ("pdf" in t and "attendance" in t) or t.startswith("attendance pdf") or t.startswith("pdf attendance")


def _is_pdf_command(text: str) -> bool:
    return _wants_pdf_minutes(text) or _wants_pdf_attendance(text)


def _extract_relation_name(text: str) -> Optional[str]:
    """
    More forgiving extraction:
    - strips command prefix, then scans tokens for any allowlisted relation
    """
    t = _lc(text)
    t = re.sub(r"^(show|preview|open|describe|columns|cols|schema)\s+", "", t).strip()
    t = re.sub(r"^table\s+", "", t).strip()
    t = re.sub(r"[^\w\s]+", " ", t).strip()
    if not t:
        return None
    toks = [x for x in t.split() if x]
    # exact token match
    for tok in toks:
        if tok in RELATIONS:
            return tok
    # fallback: sometimes user pastes relation with punctuation; try raw first token
    first = toks[0] if toks else None
    return first if first in RELATIONS else None


_MEMBER_ID_PATTERNS = [
    re.compile(r"\bmember[_\s-]?id\s*[:=#]?\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\bmember\s*#?\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\bid\s*[:=#]?\s*(\d+)\b", re.IGNORECASE),
]
_SESSION_ID_PATTERNS = [
    re.compile(r"\bsession[_\s-]?id\s*[:=#]?\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\bsession\s*#?\s*(\d+)\b", re.IGNORECASE),
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


def _extract_session_id(text: str) -> Optional[str]:
    t = _clean(text)
    for pat in _SESSION_ID_PATTERNS:
        m = pat.search(t)
        if m:
            return str(m.group(1))
    return None


# ✅ CRITICAL: detect DB commands so HF never answers DB/PDF with fake numbers
def _is_db_command(text: str) -> bool:
    t = _lc(text)
    if not t:
        return False
    if t in RELATIONS:
        return True
    if _wants_list_members(t) or _wants_loans(t) or _wants_kpis(t) or _wants_tables_list(t):
        return True
    if _wants_show_table(t) or _wants_describe(t) or _wants_help(t):
        return True
    if _wants_health(t) or _wants_counts(t):
        return True
    if _is_pdf_command(t):
        return True

    finance_words = [
        "contribution", "contributions",
        "foundation", "foundation_contributions",
        "payout", "payouts",
        "attendance", "minutes",
        "fines", "audit_log", "app_state",
        "loan_requests", "loan_repayments_pending",
        "interest_ledger", "loan_payments",
        "members", "sessions",
    ]
    return any(w in t for w in finance_words)


# -----------------------------------------------------------------------------
# Members truth (source of truth)
# -----------------------------------------------------------------------------
def _load_members_truth(sb_anon, sb_service, schema: str, limit: int = 3000) -> pd.DataFrame:
    df = _sb_select(sb_anon, sb_service, schema, "members", cols="*", limit=limit)
    if isinstance(df, tuple):  # safety
        df = df[0]
    if df.empty:
        return df

    id_col = _pick_col(df, ["id", "member_id"])
    phone_col = _pick_col(df, ["phone"])

    if not id_col:
        return pd.DataFrame(columns=["member_id", "member_name", "phone"])

    out = pd.DataFrame()
    out["member_id"] = df[id_col].astype(str)

    # prefer display_name, fallback to name
    disp = (
        df["display_name"].fillna("").astype(str).replace({"None": "", "nan": "", "NaN": ""}).str.strip()
        if "display_name" in df.columns
        else pd.Series([""] * len(df))
    )
    nm = (
        df["name"].fillna("").astype(str).replace({"None": "", "nan": "", "NaN": ""}).str.strip()
        if "name" in df.columns
        else pd.Series([""] * len(df))
    )
    out["member_name"] = disp.where(disp != "", nm).replace("", "(no name)")

    if phone_col and phone_col in df.columns:
        out["phone"] = df[phone_col].astype(str).replace({"None": "", "nan": "", "NaN": ""}).fillna("").str.strip()
    else:
        out["phone"] = ""

    try:
        out["_id_num"] = pd.to_numeric(out["member_id"], errors="coerce")
        out = out.sort_values(["_id_num", "member_id"], ascending=True).drop(columns=["_id_num"])
    except Exception:
        pass

    return out


def _get_members_truth_cached(sb_anon, sb_service, schema: str, ttl_sec: int = 30) -> pd.DataFrame:
    now = time.time()
    cache = st.session_state.get("_younchat_members_truth_cache", None)
    if isinstance(cache, dict):
        ts = float(cache.get("ts", 0))
        df = cache.get("df", None)
        if (now - ts) < ttl_sec and isinstance(df, pd.DataFrame):
            return df

    df = _load_members_truth(sb_anon, sb_service, schema, limit=3000)
    st.session_state["_younchat_members_truth_cache"] = {"ts": now, "df": df}
    return df


def _member_name_from_truth(members_truth: pd.DataFrame, member_id: str) -> str:
    if members_truth is None or members_truth.empty:
        return "(unknown)"
    hit = members_truth[members_truth["member_id"].astype(str) == str(member_id)]
    if hit.empty:
        return "(unknown)"
    return str(hit.iloc[0]["member_name"])


def _find_row_for_member(df: pd.DataFrame, member_id: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if "member_id" in df.columns:
        hit = df[df["member_id"].astype(str) == str(member_id)]
        if not hit.empty:
            return hit
    return pd.DataFrame()


# -----------------------------------------------------------------------------
# PDF helpers
# -----------------------------------------------------------------------------
def _require_pdf() -> Optional[str]:
    if canvas is None or LETTER is None or inch is None:
        return "PDF libraries missing. Add `reportlab` to requirements.txt."
    return None


def _make_simple_pdf(title: str, lines: List[str]) -> bytes:
    err = _require_pdf()
    if err:
        raise Exception(err)

    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    left = 1 * inch
    y = height - 0.9 * inch

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(left, y, title)
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - left, y, _utc_now_str())
    y -= 0.35 * inch

    pdf.setFont("Helvetica", 10)
    for raw in lines:
        line = str(raw or "")
        if not line.strip():
            y -= 0.14 * inch
            continue
        while len(line) > 110:
            pdf.drawString(left, y, line[:110])
            line = line[110:]
            y -= 0.14 * inch
            if y < 1.0 * inch:
                pdf.showPage()
                y = height - 1.0 * inch
                pdf.setFont("Helvetica", 10)
        pdf.drawString(left, y, line)
        y -= 0.14 * inch
        if y < 1.0 * inch:
            pdf.showPage()
            y = height - 1.0 * inch
            pdf.setFont("Helvetica", 10)

    pdf.showPage()
    pdf.save()
    buf.seek(0)
    return buf.getvalue()


def _pdf_minutes(sb_anon, sb_service, schema: str, session_id: str) -> Tuple[str, bytes, str]:
    df = _sb_select(
        sb_anon, sb_service, schema, "minutes",
        cols="*", limit=1,
        filters=[("session_id", "eq", int(session_id))],
        order=("created_at", False),
    )
    if isinstance(df, tuple):
        df = df[0]
    if df.empty:
        raise Exception(f"No minutes found for session_id={session_id}.")
    row = df.iloc[0].to_dict()
    title = f"theyoungshallgrow — Minutes (session {session_id})"
    body = str(row.get("body") or "")
    pdf_bytes = _make_simple_pdf(title, [str(row.get("title") or ""), "", body])
    return _force_hello_prefix(f"Minutes PDF is ready for session_id={session_id}."), pdf_bytes, f"minutes_session_{int(session_id)}.pdf"


def _pdf_attendance(sb_anon, sb_service, schema: str, session_id: str, members_truth: pd.DataFrame) -> Tuple[str, bytes, str]:
    df = _sb_select(
        sb_anon, sb_service, schema, "attendance",
        cols="*", limit=5000,
        filters=[("session_id", "eq", int(session_id))],
        order=("member_id", True),
    )
    if isinstance(df, tuple):
        df = df[0]
    lines = [f"Session ID: {session_id}", ""]
    if df.empty:
        lines.append("No attendance recorded.")
    else:
        for r in df.to_dict("records"):
            mid = r.get("member_id")
            mid_s = str(mid) if mid is not None else ""
            name = _member_name_from_truth(members_truth, mid_s) if mid_s else "(unknown)"
            present = "present" if bool(r.get("present")) else "absent"
            note = r.get("note") or ""
            lines.append(f"{mid_s} • {name} • {present} • {note}")
    pdf_bytes = _make_simple_pdf(f"theyoungshallgrow — Attendance (session {session_id})", lines)
    return _force_hello_prefix(f"Attendance PDF is ready for session_id={session_id}."), pdf_bytes, f"attendance_session_{int(session_id)}.pdf"


# -----------------------------------------------------------------------------
# Local DB answers (grounded)
# -----------------------------------------------------------------------------
def _member_financial_totals(
    sb_anon, sb_service, schema: str, member_id: str, members_truth: pd.DataFrame
) -> Tuple[str, Dict[str, Any]]:
    name = _member_name_from_truth(members_truth, member_id)

    if "v_member_financial_totals" in RELATIONS:
        v_all = _sb_select(sb_anon, sb_service, schema, "v_member_financial_totals", cols="*", limit=5000)
        if isinstance(v_all, tuple):
            v_all = v_all[0]
        hit = _find_row_for_member(v_all, member_id)
        if not hit.empty:
            row = hit.iloc[0].to_dict()
            msg = (
                f"Hello 👋🏽 Here’s the grounded summary for **{name}** (member_id={member_id}):\n\n"
                f"- Contributions total: **{_fmt(row.get('contributions_total', row.get('contribution_total', 0)))}**\n"
                f"- Foundation total: **{_fmt(row.get('foundation_total', row.get('foundation_contributions_total', 0)))}**\n"
                f"- Fines total: **{_fmt(row.get('fines_total', 0))}**\n"
                f"- Active loan balance: **{_fmt(row.get('active_loan_balance', row.get('principal_current_total', 0)))}**\n"
                f"- Active unpaid interest: **{_fmt(row.get('active_unpaid_interest', row.get('unpaid_interest_total', 0)))}**\n"
                f"- Interest ledger total: **{_fmt(row.get('interest_total', row.get('interest_ledger_total', 0)))}**\n"
            )
            return _force_hello_prefix(msg), {"source": "v_member_financial_totals", "row": row}

    mid_int = int(member_id)

    contributions = _sb_select(sb_anon, sb_service, schema, "contributions", cols="*", limit=20000, filters=[("member_id", "eq", mid_int)])
    foundation = _sb_select(sb_anon, sb_service, schema, "foundation_contributions", cols="*", limit=20000, filters=[("member_id", "eq", mid_int)])
    fines = _sb_select(sb_anon, sb_service, schema, "fines", cols="*", limit=20000, filters=[("member_id", "eq", mid_int)])
    loans = _sb_select(sb_anon, sb_service, schema, "loans", cols="*", limit=10000, filters=[("member_id", "eq", mid_int)])
    interest_ledger = _sb_select(sb_anon, sb_service, schema, "interest_ledger", cols="*", limit=20000, filters=[("member_id", "eq", mid_int)])

    if isinstance(contributions, tuple): contributions = contributions[0]
    if isinstance(foundation, tuple): foundation = foundation[0]
    if isinstance(fines, tuple): fines = fines[0]
    if isinstance(loans, tuple): loans = loans[0]
    if isinstance(interest_ledger, tuple): interest_ledger = interest_ledger[0]

    contrib_amt = _pick_col(contributions, ["amount"])
    found_amt = _pick_col(foundation, ["amount"])
    fines_amt = _pick_col(fines, ["amount"])
    int_amt = _pick_col(interest_ledger, ["amount"])

    status_col = _pick_col(loans, ["status"])
    principal_current = _pick_col(loans, ["principal_current"])
    principal = _pick_col(loans, ["principal"])

    active = loans
    if status_col and status_col in loans.columns:
        active = loans[loans[status_col].astype(str).str.lower().isin(["active", "open", "ongoing", "overdue", "late"])]

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
        f"- Interest ledger total: **{_fmt(_safe_sum(interest_ledger, int_amt))}**\n"
    )
    return _force_hello_prefix(msg), {"source": "tables_fallback"}


def _loans_with_member(
    sb_anon, sb_service, schema: str, member_id: Optional[str], members_truth: pd.DataFrame
) -> Tuple[str, pd.DataFrame, str]:
    if "v_loans_with_member" in RELATIONS:
        filters = [("member_id", "eq", int(member_id))] if member_id else None
        df = _sb_select(sb_anon, sb_service, schema, "v_loans_with_member", cols="*", limit=5000, filters=filters)
        if isinstance(df, tuple): df = df[0]
        src = "v_loans_with_member"
    else:
        filters = [("member_id", "eq", int(member_id))] if member_id else None
        df = _sb_select(sb_anon, sb_service, schema, "loans", cols="*", limit=5000, filters=filters)
        if isinstance(df, tuple): df = df[0]
        if not df.empty and "member_id" in df.columns and not members_truth.empty:
            mt = members_truth.copy()
            mt["member_id_num"] = pd.to_numeric(mt["member_id"], errors="coerce")
            df2 = df.copy()
            df2["member_id_num"] = pd.to_numeric(df2["member_id"], errors="coerce")
            df = df2.merge(mt[["member_id_num", "member_name"]], on="member_id_num", how="left").drop(columns=["member_id_num"])
        src = "loans (+ members join)"

    df = _coalesce_note_cols(df)
    title = "Loans"
    if member_id:
        title = f"Loans for {_member_name_from_truth(members_truth, member_id)} (member_id={member_id})"
    return title, df, src


def _kpis(sb_anon, sb_service, schema: str) -> Tuple[str, pd.DataFrame, str]:
    if "v_finance_kpis" in RELATIONS:
        df = _sb_select(sb_anon, sb_service, schema, "v_finance_kpis", cols="*", limit=2000)
        if isinstance(df, tuple): df = df[0]
        return "Finance KPIs", df, "v_finance_kpis"
    return "Finance KPIs", pd.DataFrame([{"note": "v_finance_kpis not available"}]), "fallback"


def _describe_relation(sb_anon, sb_service, schema: str, relation: str) -> Tuple[str, pd.DataFrame, str]:
    df = _sb_select(sb_anon, sb_service, schema, relation, cols="*", limit=1)
    if isinstance(df, tuple): df = df[0]
    cols = list(df.columns) if df is not None else []
    out = pd.DataFrame({"column_name": cols})
    msg = _force_hello_prefix(f"Columns for **{relation}** ({RELATIONS[relation]['type']}):")
    return msg, out, f"describe:{relation}"


def _show_relation(sb_anon, sb_service, schema: str, relation: str) -> Tuple[str, pd.DataFrame, str]:
    df = _sb_select(sb_anon, sb_service, schema, relation, cols="*", limit=2000)
    if isinstance(df, tuple): df = df[0]
    df = _coalesce_note_cols(df)

    if "created_at" in df.columns:
        try:
            df = df.sort_values("created_at", ascending=False)
        except Exception:
            pass

    msg = _force_hello_prefix(f"Preview of **{relation}** ({RELATIONS[relation]['type']}):")
    return msg, df, f"show:{relation}"


def _health_table(sb_anon, sb_service, schema: str) -> pd.DataFrame:
    rows = []
    for rel in sorted(RELATIONS.keys()):
        typ = RELATIONS[rel].get("type", "?")
        df, ok, err = _sb_select(sb_anon, sb_service, schema, rel, cols="*", limit=1, return_meta=True)  # type: ignore
        rows.append({"relation": rel, "type": typ, "readable": "✅" if ok else "❌", "note": (err or "")[:140]})
    return pd.DataFrame(rows)


def _counts_table(sb_anon, sb_service, schema: str) -> pd.DataFrame:
    rows = []
    for rel in sorted(RELATIONS.keys()):
        typ = RELATIONS[rel].get("type", "?")
        c = _sb_count(sb_anon, sb_service, schema, rel)
        rows.append({"relation": rel, "type": typ, "count_exact": c})
    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# Internet (Tavily) — NEVER used for Njangi numbers
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
# HF Router (optional; ONLY for general chat, never DB commands)
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


def _hf_router_chat(
    model: str, token: str, messages: List[Dict[str, str]], timeout: int = 60, temperature: float = 0.35
) -> Tuple[bool, str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": float(temperature), "max_tokens": 500}
    ok, raw = _post_with_retries(HF_ROUTER_CHAT_URL, headers, payload, timeout=timeout)
    if not ok:
        return False, raw
    try:
        data = json.loads(raw)
        text = (((data.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
        return True, str(text).strip()
    except Exception:
        return False, f"Bad HF chat response: {raw[:600]}"


def _hf_router_completions(
    model: str, token: str, prompt: str, timeout: int = 60, temperature: float = 0.35
) -> Tuple[bool, str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"model": model, "prompt": prompt, "temperature": float(temperature), "max_tokens": 500}
    ok, raw = _post_with_retries(HF_ROUTER_COMPLETIONS_URL, headers, payload, timeout=timeout)
    if not ok:
        return False, raw
    try:
        data = json.loads(raw)
        text = ((data.get("choices") or [{}])[0].get("text") or "")
        return True, str(text).strip()
    except Exception:
        return False, f"Bad HF completions response: {raw[:600]}"


def _get_hf_model_pool(primary_model: str) -> List[str]:
    """
    Build model pool from:
      1) HF_MODELS_CSV (Railway env var)
      2) HF_MODEL (primary)
      3) HF_FALLBACK_MODELS (curated list)
    Dedup while keeping order. Apply denylist.
    """
    pool: List[str] = []
    csv = (os.getenv("HF_MODELS_CSV") or "").strip()
    if csv:
        for token in csv.split(","):
            m = token.strip()
            if m:
                pool.append(m)

    if primary_model:
        pool.insert(0, primary_model.strip())

    for m in HF_FALLBACK_MODELS:
        if m:
            pool.append(m)

    # dedupe preserve order
    seen = set()
    out: List[str] = []
    for m in pool:
        if m and m not in seen:
            seen.add(m)
            out.append(m)

    # apply denylist (includes hard denylist)
    out = [m for m in out if m not in HF_MODEL_DENYLIST]
    return out


def _sanitize_primary_model(primary: str) -> str:
    """
    If HF_MODEL is denylisted, pick the first safe model from the pool.
    This prevents accidental use via env var.
    """
    p = (primary or "").strip()
    if not p or p in HF_MODEL_DENYLIST:
        pool = _get_hf_model_pool("")
        return pool[0] if pool else "meta-llama/Meta-Llama-3-8B-Instruct"
    return p


def _hf_call(
    model: str, token: str, messages: List[Dict[str, str]], random_route: bool, temperature: float
) -> Tuple[bool, str, str, str]:
    """
    Returns: ok, text, mode_used, model_used
    """
    force = (os.getenv("HF_FORCE_MODE", "") or "auto").strip().lower()
    prompt = _messages_to_prompt(messages)

    model_pool = _get_hf_model_pool(model)
    if not model_pool:
        return False, "No HF models available (pool empty).", "failed", ""

    # Random routing: choose one model but keep others as fallback
    if random_route and model_pool:
        chosen = random.choice(model_pool)
        model_order = [chosen] + [m for m in model_pool if m != chosen]
    else:
        model_order = model_pool

    def _looks_instruct(mname: str) -> bool:
        mlc = (mname or "").lower()
        return any(x in mlc for x in ["instruct", "mistral", "llama-3", "llama-3.1", "qwen", "phi", "gemma", "tulu", "openhermes", "mixtral"])

    last_err = ""
    last_mode = "failed"
    last_model = model_order[0] if model_order else ""

    # Treat these as "try next model" errors (important for model_not_supported/provider mismatch)
    def _should_try_next(err_text: str) -> bool:
        e = (err_text or "").lower()
        return any(s in e for s in [
            "429", "500", "502", "503", "504", "timeout", "server error",
            "model_not_supported", "not supported by any provider", "invalid_request_error", "param\":\"model\"",
        ])

    for chosen in model_order:
        last_model = chosen
        if force == "chat":
            order = ["chat"]
        elif force == "completions":
            order = ["completions"]
        else:
            order = ["completions", "chat"] if _looks_instruct(chosen) else ["chat", "completions"]

        for mode in order:
            last_mode = mode
            if mode == "completions":
                ok, txt = _hf_router_completions(chosen, token, prompt, temperature=temperature)
                if ok and txt:
                    return True, txt, "completions", chosen
                last_err = txt
            else:
                ok, txt = _hf_router_chat(chosen, token, messages, temperature=temperature)
                if ok and txt:
                    return True, txt, "chat", chosen
                last_err = txt

        # if model/provider mismatch etc -> try next model; otherwise stop
        if not _should_try_next(last_err):
            break

    return False, last_err or "Unknown HF error", last_mode, last_model


# -----------------------------------------------------------------------------
# Main UI
# -----------------------------------------------------------------------------
def render_njangi_llm_panel(sb_anon, sb_service, schema: str) -> None:
    st.subheader("💬 younchat", anchor=False)

    hf_token = (os.getenv("HF_TOKEN") or "").strip()

    # ✅ sanitize primary model so denylisted ones never used even if set in env
    raw_primary = (os.getenv("HF_MODEL") or "").strip() or "meta-llama/Meta-Llama-3-8B-Instruct"
    hf_model = _sanitize_primary_model(raw_primary)

    hf_force = (os.getenv("HF_FORCE_MODE") or "auto").strip().lower()

    random_default = (os.getenv("HF_RANDOM_DEFAULT") or "").strip().lower() in {"1", "true", "yes", "on"}
    internet_on = _internet_enabled()

    with st.expander("⚙️ Chat Settings", expanded=False):
        random_route = st.checkbox(
            "🎲 Random model routing (HF only)",
            value=bool(st.session_state.get("younchat_random_route", random_default)),
            help="Randomly picks one model from the HF pool for GENERAL chat. DB/PDF never uses HF.",
        )
        st.session_state["younchat_random_route"] = bool(random_route)

        temp = st.slider(
            "HF temperature (general chat only)",
            min_value=0.0,
            max_value=1.0,
            value=float(st.session_state.get("younchat_temp", 0.35)),
            step=0.05,
        )
        st.session_state["younchat_temp"] = float(temp)

        pool = _get_hf_model_pool(hf_model)
        st.write("**HF primary model**:", hf_model)
        st.write("**HF pool size**:", len(pool))
        st.write("**HF_TOKEN present**:", "✅ Yes" if hf_token else "❌ No")
        st.write("**HF_FORCE_MODE**:", hf_force)
        st.write("**Internet**:", "✅ ON" if internet_on else "❌ OFF")
        st.caption("Njangi numbers are ALWAYS answered from DB. HF is only for general chat wording (never DB/PDF commands).")

        with st.expander("🔎 View HF model pool", expanded=False):
            st.write(pool)
        if HF_MODEL_DENYLIST:
            with st.expander("⛔ Denylisted HF models", expanded=False):
                st.write(sorted(HF_MODEL_DENYLIST))

    # If a PDF was generated earlier, keep it visible
    if st.session_state.get("younchat_last_pdf_bytes") and st.session_state.get("younchat_last_pdf_name"):
        st.success("✅ Last PDF is ready.")
        st.download_button(
            "⬇️ Download Last PDF",
            data=st.session_state["younchat_last_pdf_bytes"],
            file_name=st.session_state["younchat_last_pdf_name"],
            mime="application/pdf",
            use_container_width=True,
            key="younchat_dl_last_pdf",
        )
        st.divider()

    members_truth = _get_members_truth_cached(sb_anon, sb_service, schema, ttl_sec=30)

    # ✅ ONLY INTRO LINE
    if "younchat_history" not in st.session_state:
        st.session_state["younchat_history"] = [{"role": "assistant", "content": _intro_only()}]

    for m in st.session_state["younchat_history"]:
        with st.chat_message("assistant" if m.get("role") == "assistant" else "user"):
            st.markdown(m.get("content", ""))

    colA, colB = st.columns([1, 1], gap="small")
    if colA.button("🔄 Refresh", use_container_width=True):
        st.session_state.pop("_younchat_members_truth_cache", None)
        st.rerun()
    if colB.button("🧹 Clear chat", use_container_width=True):
        st.session_state["younchat_history"] = [{"role": "assistant", "content": _intro_only()}]
        st.session_state.pop("younchat_last_member_id", None)
        st.session_state.pop("_younchat_members_truth_cache", None)
        st.session_state.pop("younchat_last_pdf_bytes", None)
        st.session_state.pop("younchat_last_pdf_name", None)
        st.rerun()

    q = st.chat_input("Type your message…")
    if not q:
        return

    st.session_state["younchat_history"].append({"role": "user", "content": q})
    with st.chat_message("user"):
        st.markdown(q)

    # ✅ Focus: set member focus ONLY when user clearly means member id
    detected_id = _extract_member_id(q)
    t = _lc(q)
    is_explicit_member = ("member" in t and "id" in t) or t.strip().startswith("member")
    is_bare_number = q.strip().isdigit()
    if detected_id and (is_bare_number or is_explicit_member):
        st.session_state["younchat_last_member_id"] = detected_id

    member_id_focus = st.session_state.get("younchat_last_member_id")

    used_source = "local"
    answer = ""
    df_show: Optional[pd.DataFrame] = None
    df_title: Optional[str] = None

    # Internet forced (never for Njangi numbers)
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
                        if url:
                            lines.append(f"- [{title}]({url})")
                        else:
                            lines.append(f"- {title}")
                        if snippet:
                            lines.append(f"  - {snippet[:180]}…")
                    answer = "\n".join(lines)

    # PDF commands (DB-grounded)
    elif _wants_pdf_minutes(q):
        used_source = "pdf:minutes"
        sid = _extract_session_id(q)
        if not sid:
            answer = "Hello 👋🏽 Say: **minutes pdf session 12**."
        else:
            try:
                msg, pdf_bytes, filename = _pdf_minutes(sb_anon, sb_service, schema, str(sid))
                st.session_state["younchat_last_pdf_bytes"] = pdf_bytes
                st.session_state["younchat_last_pdf_name"] = filename
                answer = msg
            except Exception as e:
                answer = f"Hello 👋🏽 Could not generate minutes PDF: {_api_msg(e)}"

    elif _wants_pdf_attendance(q):
        used_source = "pdf:attendance"
        sid = _extract_session_id(q)
        if not sid:
            answer = "Hello 👋🏽 Say: **attendance pdf session 12**."
        else:
            try:
                msg, pdf_bytes, filename = _pdf_attendance(sb_anon, sb_service, schema, str(sid), members_truth)
                st.session_state["younchat_last_pdf_bytes"] = pdf_bytes
                st.session_state["younchat_last_pdf_name"] = filename
                answer = msg
            except Exception as e:
                answer = f"Hello 👋🏽 Could not generate attendance PDF: {_api_msg(e)}"

    # DB-first commands
    elif _wants_help(q):
        used_source = "help"
        answer = (
            "Hello 👋🏽 Commands:\n\n"
            "- **members**\n"
            "- type **10** (member summary)\n"
            "- **member 10** (explicit)\n"
            "- **loans** / **loans for member 10**\n"
            "- **finance kpis**\n"
            "- **tables**\n"
            "- **health** (readability check)\n"
            "- **counts** (row counts, best-effort)\n"
            "- **show <table>** (example: show contributions)\n"
            "- **describe <table>** (example: describe loans)\n\n"
            "PDF:\n"
            "- **minutes pdf session 12**\n"
            "- **attendance pdf session 12**\n\n"
            "- **web: <topic>** (internet)\n"
        )

    elif _wants_tables_list(q):
        used_source = "relations"
        rows = [{"relation": k, "type": RELATIONS[k].get("type", "?")} for k in sorted(RELATIONS.keys())]
        df_show = pd.DataFrame(rows)
        df_title = "Readable relations (allowlist)"
        answer = "Hello 👋🏽 Here are the tables/views younchat can read:"

    elif _wants_health(q):
        used_source = "health"
        df_show = _health_table(sb_anon, sb_service, schema)
        df_title = "DB Health (read test)"
        answer = "Hello 👋🏽 Here is the DB health check (✅ readable / ❌ blocked):"

    elif _wants_counts(q):
        used_source = "counts"
        df_show = _counts_table(sb_anon, sb_service, schema)
        df_title = "DB Row Counts (best-effort)"
        answer = "Hello 👋🏽 Here are table row counts (may be NULL if RLS blocks count):"

    elif _wants_describe(q):
        rel = _extract_relation_name(q)
        if not rel:
            used_source = "describe"
            answer = "Hello 👋🏽 Say: **describe loans** (or any table/view in the allowlist)."
        else:
            answer, df_show, used_source = _describe_relation(sb_anon, sb_service, schema, rel)
            df_title = f"Columns: {rel}"

    elif _wants_show_table(q):
        rel = _extract_relation_name(q)
        if not rel:
            used_source = "show"
            answer = "Hello 👋🏽 Say: **show contributions** (or any table/view in the allowlist)."
        else:
            answer, df_show, used_source = _show_relation(sb_anon, sb_service, schema, rel)
            df_title = f"Preview: {rel} (all columns)"

    elif _wants_list_members(q):
        used_source = "members"
        if members_truth is None or members_truth.empty:
            answer = "Hello 👋🏽 I couldn’t read **members** (source of truth). Check RLS / permissions."
        else:
            lines = ["Hello 👋🏽 Here are all members (from `members`):\n"]
            for r in members_truth.itertuples(index=False):
                lines.append(f"- **{r.member_id}** • {r.member_name}")
            answer = "\n".join(lines)
            df_show, df_title = members_truth, "members (truth)"

    elif _wants_kpis(q):
        title, df, src = _kpis(sb_anon, sb_service, schema)
        used_source = src
        df_show, df_title = df, title
        answer = f"Hello 👋🏽 {title} (from `{src}`):" if not df.empty else "Hello 👋🏽 No KPI rows returned."

    elif _wants_loans(q):
        mid = _extract_member_id(q) or member_id_focus
        title, df, src = _loans_with_member(sb_anon, sb_service, schema, mid, members_truth)
        used_source = src
        df_show, df_title = df, title
        answer = f"Hello 👋🏽 {title} (from `{src}`):" if not df.empty else f"Hello 👋🏽 {title}: no rows returned."

    # Member summary (grounded) ✅ clearer trigger
    elif q.strip().isdigit() or t.startswith("member ") or t.startswith("summary "):
        mid = _extract_member_id(q) or member_id_focus
        if mid:
            answer, meta = _member_financial_totals(sb_anon, sb_service, schema, str(mid), members_truth)
            used_source = meta.get("source", "member_summary_local")
        else:
            used_source = "member_summary"
            answer = "Hello 👋🏽 Say: **10** or **member 10**."

    elif _lc(q) in RELATIONS:
        rel = _lc(q)
        answer, df_show, used_source = _show_relation(sb_anon, sb_service, schema, rel)
        df_title = f"Preview: {rel} (all columns)"

    # General chat: optional HF (NEVER DB/PDF commands)
    else:
        if hf_token and not _is_db_command(q):
            sys = (
                "You are younchat.\n"
                "Rules:\n"
                "- Start with 'Hello' if greeting.\n"
                "- Do NOT output SQL or Python code.\n"
                "- Do NOT invent Njangi numbers or member IDs.\n"
                "- If user asks for database results, suggest commands: members / loans / finance kpis / tables / health / counts / show <table> / describe <table>.\n"
                "- If user asks for PDFs, suggest: minutes pdf session <id>, attendance pdf session <id>.\n"
            )
            messages = [{"role": "system", "content": sys}]
            for m in st.session_state["younchat_history"][-10:]:
                if m.get("role") in ("user", "assistant"):
                    messages.append({"role": m["role"], "content": m.get("content", "")})

            ok, txt, mode, model_used = _hf_call(
                hf_model,
                hf_token,
                messages,
                random_route=bool(st.session_state.get("younchat_random_route", random_default)),
                temperature=float(st.session_state.get("younchat_temp", 0.35)),
            )
            used_source = f"hf:{mode}:{model_used}" if ok else "hf:failed"
            answer = txt if ok else f"Hello 👋🏽 HF is not reachable: {txt}"
        else:
            if _is_db_command(q):
                used_source = "db:first_guard"
                answer = (
                    "Hello 👋🏽 I can show the real data from your database.\n\n"
                    "Try:\n"
                    "- **members**\n"
                    "- **loans**\n"
                    "- **finance kpis**\n"
                    "- **tables**\n"
                    "- **health**\n"
                    "- **counts**\n"
                    "- **show contributions**\n"
                    "- **show loan_requests**\n"
                    "- **show loan_repayments_pending**\n"
                    "- **describe loans**\n\n"
                    "PDF:\n"
                    "- **minutes pdf session 12**\n"
                    "- **attendance pdf session 12**\n"
                )
            else:
                used_source = "local:fallback"
                answer = "Hello 👋🏽"

    # ✅ enforce Hello salute rule for every assistant output (but keep intro untouched)
    if answer != _intro_only():
        answer = _force_hello_prefix(answer)

    st.session_state["younchat_history"].append({"role": "assistant", "content": answer})
    with st.chat_message("assistant"):
        st.markdown(answer)

        if st.session_state.get("younchat_last_pdf_bytes") and st.session_state.get("younchat_last_pdf_name"):
            st.download_button(
                "⬇️ Download PDF",
                data=st.session_state["younchat_last_pdf_bytes"],
                file_name=st.session_state["younchat_last_pdf_name"],
                mime="application/pdf",
                use_container_width=True,
                key=f"younchat_dl_pdf_{int(time.time())}",
            )

        if df_show is not None and df_title:
            with st.expander(df_title, expanded=False):
                st.dataframe(df_show, use_container_width=True)

    st.caption(
        f"Source used: {used_source} • member_id: {member_id_focus or '—'} • "
        f"Internet: {'ON' if internet_on else 'OFF'}"
    )
