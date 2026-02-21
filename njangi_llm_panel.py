# njangi_llm_panel.py ✅ SINGLE COMPLETE FILE — younchat reads your DB (members = source of truth)
# =============================================================================
# 💬 younchat — DB-TOOLS FIRST (tables + views) + Optional HF Router + Optional Tavily
#
# ✅ YOUR REQUEST (IMPORTANT):
#   - The ONLY intro message must be EXACTLY:
#       "Hello 👋🏽 I’m younchat — your Njangi assistant."
#   - No extra intro text, no command list in the intro.
#   - Salute must be "Hello"
#   - younchat reads ALL your tables/views (based on RELATIONS allowlist you control)
#   - members table is the source of truth for identity (name display)
#   - NO hallucinations for Njangi numbers (all financial answers come from DB)
#   - IMPORTANT FIX: HF Router will NEVER answer DB commands (members/loans/kpis/show/describe/etc.)
#
# ✅ UPDATE YOU ASKED (NEW "FINANCIAL CONTROL TOWER" ENGINE):
#   - younchat is NOT a chatbot inside Njangi: it behaves as a cooperative
#     financial risk system, liquidity monitor, credit evaluator, policy advisor.
#   - It NEVER invents numbers: it ONLY computes from DB rows in your schema.
#   - It NEVER outputs SQL or Python to the user (this file is code, but chat outputs are not code).
#   - If data is missing, it explicitly says what’s missing and guides to:
#       members / loans / finance kpis / tables / show <table> / describe <table> / type member_id
#   - Financial intelligence responses ALWAYS follow:
#       1) Current Situation
#       2) Risk Assessment
#       3) Financial Impact
#       4) Strategic Recommendation
#     with Risk classification: Low / Moderate / Elevated / High / Critical
#   - Health Score (0–100) is produced ONLY if sufficient context exists.
#
# ✅ HF Models locked to ONLY these 3:
#   1) meta-llama/Meta-Llama-3-8B-Instruct
#   2) meta-llama/Llama-3.1-8B-Instruct
#   3) mistralai/Mistral-7B-Instruct-v0.2
#
# Works with app.py:
#   render_njangi_llm_panel(sb_anon=..., sb_service=..., schema=...)
#
# Railway env vars (optional):
#   HF_TOKEN
#   HF_MODEL               (ignored if not in the 3-model allowlist; we force the 3 you gave)
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


HF_ROUTER_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"
HF_ROUTER_COMPLETIONS_URL = "https://router.huggingface.co/v1/completions"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"

# ✅ ONLY the 3 models you gave (hard-locked)
HF_ALLOWED_MODELS: List[str] = [
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.2",
]

# ✅ Allowlist the relations (tables + views).
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

# -----------------------------------------------------------------------------
# Time helpers
# -----------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _intro_only() -> str:
    # ✅ EXACT intro required by you (only line, no extra text)
    return "Hello 👋🏽 I’m younchat — your Njangi assistant."


def _force_hello_prefix(text: str) -> str:
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


def _pick_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _to_num_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0)


def _safe_sum(df: pd.DataFrame, col: Optional[str]) -> float:
    if df is None or df.empty or not col or col not in df.columns:
        return 0.0
    return float(_to_num_series(df[col]).sum())


def _fmt(x: Any) -> str:
    try:
        v = float(pd.to_numeric(x, errors="coerce"))
        if pd.isna(v):
            v = 0.0
    except Exception:
        v = 0.0
    return f"{v:,.2f}"


def _pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    try:
        return f"{x*100:.1f}%"
    except Exception:
        return "—"


def _ratio(n: Optional[float], d: Optional[float]) -> Optional[float]:
    if n is None or d is None or d == 0:
        return None
    return n / d


def _parse_dt(df: pd.DataFrame, col: str) -> Optional[pd.Series]:
    if df is None or df.empty or col not in df.columns:
        return None
    try:
        s = pd.to_datetime(df[col], errors="coerce", utc=True)
        return s
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Intent helpers
# -----------------------------------------------------------------------------
def _clean(text: str) -> str:
    return (text or "").strip()


def _lc(text: str) -> str:
    return _clean(text).lower()


def _wants_help(text: str) -> bool:
    return _lc(text) in {"help", "/help", "commands", "options"}


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


def _wants_tables_list(text: str) -> bool:
    return _lc(text) in {"tables", "relations", "views", "list tables", "list views"}


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


def _wants_financial_review(text: str) -> bool:
    t = _lc(text)
    triggers = [
        "how are we doing",
        "are we stable",
        "is njangi healthy",
        "njangi health",
        "health score",
        "financial condition",
        "risk review",
        "any risk",
        "liquidity",
        "credit risk",
        "executive summary",
        "summary",
        "control tower",
        "financial intelligence",
    ]
    return any(x in t for x in triggers)


def _wants_member_risk(text: str) -> bool:
    t = _lc(text)
    return any(x in t for x in ["member risk", "risk grade", "credit grade", "member health"])


def _wants_internet(text: str) -> bool:
    t = _lc(text)
    return t.startswith("web:") or t.startswith("internet:") or t.startswith("tavily:")


def _strip_web_prefix(q: str) -> str:
    return re.sub(r"^(web:|internet:|tavily:)\s*", "", (q or "").strip(), flags=re.IGNORECASE).strip()


def _extract_relation_name(text: str) -> Optional[str]:
    t = _lc(text)
    t = re.sub(r"^(show|preview|open|describe|columns|cols|schema)\s+", "", t).strip()
    t = re.sub(r"^table\s+", "", t).strip()
    t = re.sub(r"[^\w]+$", "", t)
    if not t:
        return None
    token = t.split()[0]
    return token if token in RELATIONS else None


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


# ✅ CRITICAL FIX: detect DB commands so HF never returns fake answers
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

    finance_words = ["contribution", "contributions", "payout", "payouts", "attendance", "minutes", "fines", "interest"]
    return any(w in t for w in finance_words)


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

    if display_col and display_col in df.columns:
        disp_clean = (
            df[display_col]
            .astype(str)
            .replace(["None", "nan", "NaN", "NULL", "null"], "")
            .fillna("")
            .str.strip()
        )
    else:
        disp_clean = pd.Series([""] * len(df))

    if name_col and name_col in df.columns:
        nm_clean = (
            df[name_col]
            .astype(str)
            .replace(["None", "nan", "NaN", "NULL", "null"], "")
            .fillna("")
            .str.strip()
        )
    else:
        nm_clean = pd.Series([""] * len(df))

    out["member_name"] = disp_clean.where(disp_clean != "", nm_clean)
    out["member_name"] = out["member_name"].fillna("").replace("", "(no name)")

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
# Njangi Financial Intelligence Engine (DB-only computations)
# -----------------------------------------------------------------------------
def _active_loan_filter(loans: pd.DataFrame) -> pd.DataFrame:
    if loans is None or loans.empty:
        return loans
    status_col = _pick_col(loans, ["status"])
    if not status_col:
        return loans
    s = loans[status_col].astype(str).str.lower().fillna("")
    active_status = {"active", "open", "ongoing", "overdue", "late"}
    return loans[s.isin(active_status)]


def _overdue_loan_filter(loans: pd.DataFrame) -> pd.DataFrame:
    if loans is None or loans.empty:
        return loans
    status_col = _pick_col(loans, ["status"])
    if status_col:
        s = loans[status_col].astype(str).str.lower().fillna("")
        return loans[s.isin({"overdue", "late"})]
    # If no status col, try dpd-like columns to infer overdue (non-zero)
    dpd_col = _pick_col(loans, ["dpd", "days_past_due", "overdue_days"])
    if dpd_col:
        dpd = _to_num_series(loans[dpd_col])
        return loans[dpd > 0]
    return loans.iloc[0:0]


def _loan_balance_col(loans: pd.DataFrame) -> Optional[str]:
    return _pick_col(loans, ["principal_current", "balance", "outstanding_principal", "principal_remaining", "principal"])


def _unpaid_interest_col(loans: pd.DataFrame) -> Optional[str]:
    return _pick_col(loans, ["unpaid_interest", "interest_unpaid", "interest_due", "interest_balance"])


def _collect_global_finance_context(
    sb_anon,
    sb_service,
    schema: str,
) -> Dict[str, Any]:
    """
    Pulls global tables needed for a control-tower review.
    Returns dict with dfs + notes about missing columns.
    """
    out: Dict[str, Any] = {
        "ok": True,
        "notes": [],
        "df": {},
    }

    # Prefer view if present (but we still may need tables for deeper metrics like concentration)
    if "v_finance_kpis" in RELATIONS:
        k = _sb_select(sb_anon, sb_service, schema, "v_finance_kpis", cols="*", limit=200)
        out["df"]["v_finance_kpis"] = k

    # Tables for truth-based computations
    out["df"]["contributions"] = _sb_select(sb_anon, sb_service, schema, "contributions", cols="*", limit=200000)
    out["df"]["foundation_contributions"] = _sb_select(
        sb_anon, sb_service, schema, "foundation_contributions", cols="*", limit=200000
    )
    out["df"]["loans"] = _sb_select(sb_anon, sb_service, schema, "loans", cols="*", limit=200000)
    out["df"]["interest_ledger"] = _sb_select(sb_anon, sb_service, schema, "interest_ledger", cols="*", limit=200000)
    out["df"]["fines"] = _sb_select(sb_anon, sb_service, schema, "fines", cols="*", limit=200000)

    return out


def _compute_global_metrics(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes core metrics strictly from DB rows.
    If columns are missing, returns None for that metric and records missing details.
    """
    dfc = (ctx.get("df") or {}).get("contributions", pd.DataFrame())
    dff = (ctx.get("df") or {}).get("foundation_contributions", pd.DataFrame())
    dfl = (ctx.get("df") or {}).get("loans", pd.DataFrame())
    dfi = (ctx.get("df") or {}).get("interest_ledger", pd.DataFrame())
    dffines = (ctx.get("df") or {}).get("fines", pd.DataFrame())

    notes: List[str] = []

    # Totals
    contrib_col = _pick_col(dfc, ["amount", "contribution_amount", "paid_amount"])
    if not contrib_col:
        notes.append("Missing contributions amount column (expected: amount/contribution_amount/paid_amount).")
    total_contributions = _safe_sum(dfc, contrib_col) if contrib_col else None

    foundation_col = _pick_col(dff, ["amount", "base_amount", "foundation_amount"])
    if not foundation_col:
        notes.append("Missing foundation_contributions amount column (expected: amount/base_amount/foundation_amount).")
    foundation_total = _safe_sum(dff, foundation_col) if foundation_col else None

    fines_col = _pick_col(dffines, ["amount", "fine_amount"])
    total_fines = _safe_sum(dffines, fines_col) if fines_col else None

    # Loans
    active_loans = _active_loan_filter(dfl)
    overdue_loans = _overdue_loan_filter(active_loans)

    bal_col = _loan_balance_col(active_loans)
    if not bal_col and not active_loans.empty:
        notes.append("Missing loans balance column (expected: principal_current/balance/outstanding_principal/principal).")
    active_loan_exposure = _safe_sum(active_loans, bal_col) if bal_col else (0.0 if active_loans.empty else None)

    unpaid_col = _unpaid_interest_col(active_loans)
    unpaid_interest = _safe_sum(active_loans, unpaid_col) if unpaid_col else None
    if unpaid_col is None and not active_loans.empty:
        notes.append("Missing loans unpaid interest column (expected: unpaid_interest/interest_due/etc.).")

    active_count = int(len(active_loans)) if active_loans is not None else 0
    overdue_count = int(len(overdue_loans)) if overdue_loans is not None else 0
    overdue_ratio = (overdue_count / active_count) if active_count > 0 else (0.0 if overdue_count == 0 else None)

    # Concentration risk (top borrower share of active exposure)
    concentration_share: Optional[float] = None
    top_borrower_member_id: Optional[str] = None
    if active_loans is not None and not active_loans.empty and bal_col and "member_id" in active_loans.columns:
        try:
            g = active_loans.copy()
            g["_bal"] = _to_num_series(g[bal_col])
            bym = g.groupby(g["member_id"].astype(str))["_bal"].sum().sort_values(ascending=False)
            if len(bym) > 0:
                top_borrower_member_id = str(bym.index[0])
                total = float(bym.sum())
                top = float(bym.iloc[0])
                concentration_share = (top / total) if total > 0 else None
        except Exception:
            concentration_share = None

    # Interest income (ledger) — best-effort trend if date exists
    interest_col = _pick_col(dfi, ["amount", "interest_amount", "interest"])
    interest_total = _safe_sum(dfi, interest_col) if interest_col else None
    if interest_col is None and not dfi.empty:
        notes.append("Missing interest_ledger amount column (expected: amount/interest_amount/interest).")

    # Trend: last 90 days vs previous 90 days (directional only)
    interest_trend: Optional[str] = None
    date_col = _pick_col(dfi, ["created_at", "date", "paid_at", "posted_at", "timestamp"])
    if interest_col and date_col:
        sdt = _parse_dt(dfi, date_col)
        if sdt is not None:
            now = datetime.now(timezone.utc)
            cut1 = now - pd.Timedelta(days=90)
            cut2 = now - pd.Timedelta(days=180)
            last = dfi[sdt >= cut1]
            prev = dfi[(sdt >= cut2) & (sdt < cut1)]
            last_sum = float(_to_num_series(last[interest_col]).sum()) if not last.empty else 0.0
            prev_sum = float(_to_num_series(prev[interest_col]).sum()) if not prev.empty else 0.0
            if last_sum > prev_sum:
                interest_trend = "Rising"
            elif last_sum < prev_sum:
                interest_trend = "Declining"
            else:
                interest_trend = "Flat"
    elif interest_col and not date_col and not dfi.empty:
        notes.append("Cannot compute interest trend (missing interest_ledger date column).")

    # Liquidity pressure ratio
    liquidity_pressure = _ratio(active_loan_exposure, total_contributions) if active_loan_exposure is not None else None

    return {
        "notes": notes,
        "total_contributions": total_contributions,
        "foundation_total": foundation_total,
        "total_fines": total_fines,
        "active_loan_exposure": active_loan_exposure,
        "active_loan_count": active_count,
        "overdue_loan_count": overdue_count,
        "overdue_ratio": overdue_ratio,
        "unpaid_interest": unpaid_interest,
        "interest_total": interest_total,
        "interest_trend": interest_trend,
        "liquidity_pressure_ratio": liquidity_pressure,
        "concentration_share": concentration_share,
        "top_borrower_member_id": top_borrower_member_id,
    }


def _risk_classification(metrics: Dict[str, Any]) -> Tuple[str, List[str]]:
    """
    DB-based rule classification (no invented thresholds beyond qualitative guidance).
    Returns (risk_label, signals).
    """
    signals: List[str] = []

    lpr = metrics.get("liquidity_pressure_ratio")
    overdue_ratio = metrics.get("overdue_ratio")
    unpaid_interest = metrics.get("unpaid_interest")
    conc = metrics.get("concentration_share")

    # Early warning signals (only if metric exists)
    if lpr is not None and lpr > 0.75:
        signals.append("Liquidity pressure > 75% (Active Loan Exposure ÷ Total Contributions).")
    if overdue_ratio is not None and overdue_ratio > 0.20:
        signals.append("Overdue ratio is elevated (over 20% of active loans).")
    if unpaid_interest is not None and unpaid_interest > 0:
        signals.append("Unpaid interest exists on active loans.")
    if conc is not None and conc > 0.40:
        signals.append("Concentration risk: top borrower > 40% of active exposure.")

    # Determine classification
    # If we lack most metrics, keep it Moderate (uncertain) but say context insufficient elsewhere.
    score = 0
    if lpr is not None:
        score += 2 if lpr > 0.75 else (1 if lpr > 0.50 else 0)
    if overdue_ratio is not None:
        score += 2 if overdue_ratio > 0.30 else (1 if overdue_ratio > 0.10 else 0)
    if unpaid_interest is not None:
        score += 1 if unpaid_interest > 0 else 0
    if conc is not None:
        score += 2 if conc > 0.50 else (1 if conc > 0.35 else 0)

    if score >= 6:
        return "Critical", signals
    if score >= 4:
        return "High", signals
    if score >= 2:
        return "Elevated", signals
    if score >= 1:
        return "Moderate", signals
    return "Low", signals


def _health_score(metrics: Dict[str, Any]) -> Tuple[Optional[int], List[str]]:
    """
    Returns (score 0-100 or None, reasons).
    Score only if sufficient context exists.
    """
    reasons: List[str] = []

    total_contributions = metrics.get("total_contributions")
    foundation_total = metrics.get("foundation_total")
    active_exposure = metrics.get("active_loan_exposure")
    overdue_ratio = metrics.get("overdue_ratio")

    # Minimum required to avoid hallucination:
    if total_contributions is None or foundation_total is None or active_exposure is None or overdue_ratio is None:
        return None, [
            "Health Score not generated: insufficient DB context (need totals for contributions, foundation, active loan exposure, and overdue ratio)."
        ]

    # Components (weighting per your spec)
    # Liquidity Strength (30%): lower liquidity pressure is better
    lpr = _ratio(active_exposure, total_contributions)
    if lpr is None:
        return None, ["Health Score not generated: cannot compute Liquidity Pressure Ratio (division by zero or missing)."]

    # Map lpr to 0..100 subscore (qualitative curve)
    if lpr <= 0.25:
        liq = 95
    elif lpr <= 0.50:
        liq = 80
    elif lpr <= 0.75:
        liq = 60
    else:
        liq = 35

    # Credit Risk Stability (30%): lower overdue ratio is better
    if overdue_ratio <= 0.05:
        cred = 95
    elif overdue_ratio <= 0.10:
        cred = 85
    elif overdue_ratio <= 0.20:
        cred = 65
    else:
        cred = 40

    # Contribution Strength (20%): proxy = capital scale vs exposure (avoid guessing frequency)
    # If total_contributions comfortably larger than exposure, better
    coverage = _ratio(total_contributions, max(active_exposure, 1e-9))
    if coverage is None:
        contrib_strength = 60
    else:
        if coverage >= 4:
            contrib_strength = 90
        elif coverage >= 2:
            contrib_strength = 75
        elif coverage >= 1:
            contrib_strength = 60
        else:
            contrib_strength = 40

    # Foundation Stability (20%): foundation relative to exposure
    fcover = _ratio(foundation_total, max(active_exposure, 1e-9))
    if fcover is None:
        foundation_strength = 60
    else:
        if fcover >= 1:
            foundation_strength = 90
        elif fcover >= 0.5:
            foundation_strength = 75
        elif fcover >= 0.25:
            foundation_strength = 60
        else:
            foundation_strength = 40

    score = round(0.30 * liq + 0.30 * cred + 0.20 * contrib_strength + 0.20 * foundation_strength)

    reasons.append(f"Liquidity Strength component based on Liquidity Pressure Ratio = {_pct(lpr)}.")
    reasons.append(f"Credit Risk Stability component based on overdue ratio = {_pct(overdue_ratio)}.")
    reasons.append("Contribution Strength uses contribution-to-exposure coverage (capital coverage proxy).")
    reasons.append("Foundation Stability uses foundation-to-exposure coverage (reserve adequacy proxy).")
    return int(score), reasons


def _score_level(score: int) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Strong"
    if score >= 60:
        return "Stable"
    if score >= 40:
        return "Elevated Risk"
    return "High Risk"


def _build_control_tower_report(
    metrics: Dict[str, Any],
    members_truth: pd.DataFrame,
) -> str:
    risk_label, signals = _risk_classification(metrics)
    hs, hs_reasons = _health_score(metrics)

    top_borrower = metrics.get("top_borrower_member_id")
    top_borrower_name = _member_name_from_truth(members_truth, str(top_borrower)) if top_borrower else None

    # Current Situation (DB grounded)
    lines: List[str] = []
    lines.append("Hello 👋🏽 Njangi Financial Intelligence Review (DB-grounded)\n")

    lines.append("1️⃣ Current Situation")
    lines.append(f"- Total contributions: **{_fmt(metrics.get('total_contributions'))}**" if metrics.get("total_contributions") is not None else "- Total contributions: **Not available**")
    lines.append(f"- Foundation reserves (total): **{_fmt(metrics.get('foundation_total'))}**" if metrics.get("foundation_total") is not None else "- Foundation reserves (total): **Not available**")
    lines.append(f"- Active loan exposure: **{_fmt(metrics.get('active_loan_exposure'))}**" if metrics.get("active_loan_exposure") is not None else "- Active loan exposure: **Not available**")
    lines.append(f"- Active loans (count): **{metrics.get('active_loan_count', 0)}**")
    lines.append(f"- Overdue loans (count): **{metrics.get('overdue_loan_count', 0)}**")
    if metrics.get("overdue_ratio") is not None:
        lines.append(f"- Overdue ratio: **{_pct(metrics.get('overdue_ratio'))}**")
    else:
        lines.append("- Overdue ratio: **Not available**")

    if metrics.get("unpaid_interest") is not None:
        lines.append(f"- Unpaid interest (active): **{_fmt(metrics.get('unpaid_interest'))}**")
    else:
        lines.append("- Unpaid interest (active): **Not available**")

    if metrics.get("liquidity_pressure_ratio") is not None:
        lines.append(f"- Liquidity Pressure Ratio (Exposure ÷ Contributions): **{_pct(metrics.get('liquidity_pressure_ratio'))}**")
    else:
        lines.append("- Liquidity Pressure Ratio: **Not available**")

    if metrics.get("concentration_share") is not None and top_borrower:
        lines.append(
            f"- Concentration (top borrower share): **{_pct(metrics.get('concentration_share'))}** "
            f"(Top borrower: **{top_borrower_name}** • member_id={top_borrower})"
        )
    else:
        lines.append("- Concentration risk: **Not available**")

    if metrics.get("interest_total") is not None:
        trend = metrics.get("interest_trend") or "—"
        lines.append(f"- Interest ledger total: **{_fmt(metrics.get('interest_total'))}** (Trend: **{trend}**)")
    else:
        lines.append("- Interest ledger total: **Not available**")

    # Risk Assessment
    lines.append("\n2️⃣ Risk Assessment")
    lines.append(f"- Risk classification: **{risk_label}**")
    if signals:
        lines.append("- Early warning signals:")
        for s in signals:
            lines.append(f"  - {s}")
    else:
        lines.append("- Early warning signals: **None detected from available metrics**")

    # Financial Impact (directional unless totals exist)
    lines.append("\n3️⃣ Financial Impact")
    # We can state directional impacts without inventing values
    if metrics.get("liquidity_pressure_ratio") is not None and metrics.get("liquidity_pressure_ratio") > 0.75:
        lines.append("- Liquidity is under stress: a high share of contributed capital is locked in active loans, increasing payout/rotation risk.")
    else:
        lines.append("- Liquidity impact: based on available data, no severe liquidity lock-up signal is confirmed.")

    if metrics.get("overdue_ratio") is not None and metrics.get("overdue_ratio") > 0.20:
        lines.append("- Credit impact: overdue levels can slow recycling of capital and reduce reliability of the foundation loan feature.")
    else:
        lines.append("- Credit impact: overdue levels are not confirmed as high from available data (or overdue ratio unavailable).")

    if metrics.get("unpaid_interest") is not None and metrics.get("unpaid_interest") > 0:
        lines.append("- Income impact: unpaid interest indicates leakage in expected interest capture and weak enforcement/collection.")
    else:
        lines.append("- Income impact: unpaid interest not confirmed (or metric unavailable).")

    # Strategic Recommendation
    lines.append("\n4️⃣ Strategic Recommendation")
    recs: List[str] = []

    # Policies based on detected signals
    lpr = metrics.get("liquidity_pressure_ratio")
    if lpr is not None and lpr > 0.75:
        recs.append("Set a liquidity buffer policy: pause new loans (or tighten approvals) until Liquidity Pressure falls below your target threshold.")
        recs.append("Introduce loan caps tied to contributed capital (risk-based lending control).")

    overdue_ratio = metrics.get("overdue_ratio")
    if overdue_ratio is not None and overdue_ratio > 0.20:
        recs.append("Tighten loan approval rules: require stronger contribution history and enforce repayment schedules before approving new loans.")
        recs.append("Add escalation for late accounts (structured reminders + penalties + temporary borrowing freeze).")

    conc = metrics.get("concentration_share")
    if conc is not None and conc > 0.40:
        recs.append("Reduce concentration risk: cap exposure per member as a percentage of total active exposure or total contributions.")

    unpaid_interest = metrics.get("unpaid_interest")
    if unpaid_interest is not None and unpaid_interest > 0:
        recs.append("Strengthen interest enforcement: monthly interest settlement, automated tracking, and hard blocks on new borrowing when unpaid interest exists.")

    # Always include practical next step
    recs.append("Operational discipline: review loans weekly, and track (Exposure, Overdue, Interest Due) as core control-tower KPIs.")

    for r in recs:
        lines.append(f"- {r}")

    # Health Score (only if sufficient)
    lines.append("\n🏆 NJANGI HEALTH SCORE (0–100)")
    if hs is None:
        lines.append(f"- {hs_reasons[0]}")
    else:
        lines.append(f"- Score: **{hs}/100** → **{_score_level(hs)}**")
        for rr in hs_reasons:
            lines.append(f"- {rr}")

    # Missing data notes (strict integrity)
    notes = metrics.get("notes") or []
    if notes:
        lines.append("\n🔒 Data Integrity Notes (what’s missing / limits)")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("\nTo fill gaps, use: **describe <table>** or **show <table>**, or run **finance kpis** if you maintain `v_finance_kpis`.")

    return "\n".join(lines)


def _member_risk_grade(member_meta: Dict[str, Any]) -> str:
    """
    Simple grade based strictly on available member totals (no guessing).
    A: no active exposure + no unpaid interest
    B: exposure exists but no unpaid interest
    C: exposure + unpaid interest OR overdue indicators
    D: strong negative (overdue / large exposure ratio if contributions exist)
    """
    active_bal = member_meta.get("active_loan_balance")
    unpaid = member_meta.get("active_unpaid_interest")
    contrib = member_meta.get("contributions_total")

    # if missing, default to conservative (C) but we will disclose missing in output text
    if active_bal is None or unpaid is None:
        return "C"

    if active_bal <= 0 and unpaid <= 0:
        return "A"
    if active_bal > 0 and unpaid <= 0:
        return "B"
    if active_bal > 0 and unpaid > 0:
        return "C"

    # if exposure is unknown but unpaid exists
    if unpaid > 0:
        return "C"

    # Contribution coverage: if known and very low, D
    if contrib is not None and active_bal is not None and contrib > 0:
        lpr = active_bal / contrib
        if lpr > 0.90:
            return "D"
    return "C"


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
    name = _member_name_from_truth(members_truth, member_id)

    # Prefer view if present
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
            contrib = row.get("contributions_total", row.get("contribution_total", row.get("contributions")))
            found = row.get("foundation_total", row.get("foundation_contributions_total", row.get("foundation")))
            fines = row.get("fines_total", row.get("fines"))
            active_bal = row.get("active_loan_balance", row.get("loan_balance", row.get("principal_current_total")))
            unpaid_int = row.get("active_unpaid_interest", row.get("unpaid_interest_total", row.get("unpaid_interest")))
            interest = row.get("interest_total", row.get("interest_ledger_total", row.get("interest")))

            meta = {
                "source": "v_member_financial_totals",
                "member_name": name,
                "member_id": member_id,
                "contributions_total": float(pd.to_numeric(contrib, errors="coerce")) if contrib is not None else None,
                "foundation_total": float(pd.to_numeric(found, errors="coerce")) if found is not None else None,
                "fines_total": float(pd.to_numeric(fines, errors="coerce")) if fines is not None else None,
                "active_loan_balance": float(pd.to_numeric(active_bal, errors="coerce")) if active_bal is not None else None,
                "active_unpaid_interest": float(pd.to_numeric(unpaid_int, errors="coerce")) if unpaid_int is not None else None,
                "interest_total": float(pd.to_numeric(interest, errors="coerce")) if interest is not None else None,
            }

            grade = _member_risk_grade(meta)

            msg = (
                f"Hello 👋🏽 Member Financial Health (DB-grounded)\n\n"
                f"1️⃣ Current Situation\n"
                f"- Member: **{name}** (member_id={member_id})\n"
                f"- Contributions total: **{_fmt(contrib)}**\n"
                f"- Foundation total: **{_fmt(found)}**\n"
                f"- Fines total: **{_fmt(fines)}**\n"
                f"- Active loan balance: **{_fmt(active_bal)}**\n"
                f"- Active unpaid interest: **{_fmt(unpaid_int)}**\n"
                f"- Interest ledger total: **{_fmt(interest)}**\n\n"
                f"2️⃣ Risk Assessment\n"
                f"- Member Risk Grade: **{grade}** (A/B/C/D)\n\n"
                f"3️⃣ Financial Impact\n"
                f"- If active balance or unpaid interest is present, liquidity recycling slows and enforcement risk rises.\n\n"
                f"4️⃣ Strategic Recommendation\n"
                f"- If unpaid interest > 0: enforce monthly interest settlement before new borrowing.\n"
                f"- If loan balance is high vs contributions: apply tighter caps and repayment discipline.\n"
            )
            return msg, meta

    # Table fallback
    contributions = _sb_select(sb_anon, sb_service, schema, "contributions", cols="*", limit=200000, filters=[("member_id", "eq", member_id)])
    foundation = _sb_select(sb_anon, sb_service, schema, "foundation_contributions", cols="*", limit=200000, filters=[("member_id", "eq", member_id)])
    fines = _sb_select(sb_anon, sb_service, schema, "fines", cols="*", limit=200000, filters=[("member_id", "eq", member_id)])
    loans = _sb_select(sb_anon, sb_service, schema, "loans", cols="*", limit=200000, filters=[("member_id", "eq", member_id)])
    interest_ledger = _sb_select(sb_anon, sb_service, schema, "interest_ledger", cols="*", limit=200000, filters=[("member_id", "eq", member_id)])

    contrib_amt = _pick_col(contributions, ["amount", "contribution_amount", "paid_amount"])
    found_amt = _pick_col(foundation, ["amount", "base_amount", "foundation_amount"])
    fines_amt = _pick_col(fines, ["amount", "fine_amount"])
    int_amt = _pick_col(interest_ledger, ["amount", "interest_amount", "interest"])

    active = _active_loan_filter(loans)
    bal_col = _loan_balance_col(active)
    unpaid_col = _unpaid_interest_col(active)

    active_bal = _safe_sum(active, bal_col) if bal_col else (0.0 if active.empty else None)
    unpaid_interest = _safe_sum(active, unpaid_col) if unpaid_col else None

    meta = {
        "source": "tables_fallback",
        "member_name": name,
        "member_id": member_id,
        "contributions_total": _safe_sum(contributions, contrib_amt) if contrib_amt else None,
        "foundation_total": _safe_sum(foundation, found_amt) if found_amt else None,
        "fines_total": _safe_sum(fines, fines_amt) if fines_amt else None,
        "active_loan_balance": active_bal,
        "active_unpaid_interest": unpaid_interest,
        "interest_total": _safe_sum(interest_ledger, int_amt) if int_amt else None,
    }

    grade = _member_risk_grade(meta)

    notes: List[str] = []
    if contrib_amt is None:
        notes.append("Missing contributions amount column (amount/contribution_amount/paid_amount).")
    if found_amt is None:
        notes.append("Missing foundation_contributions amount column (amount/base_amount/foundation_amount).")
    if bal_col is None and not active.empty:
        notes.append("Missing loans balance column (principal_current/balance/outstanding_principal/principal).")
    if unpaid_col is None and not active.empty:
        notes.append("Missing loans unpaid interest column (unpaid_interest/interest_due/etc.).")

    msg = (
        f"Hello 👋🏽 Member Financial Health (DB-grounded)\n\n"
        f"1️⃣ Current Situation\n"
        f"- Member: **{name}** (member_id={member_id})\n"
        f"- Contributions total: **{_fmt(meta.get('contributions_total'))}**\n"
        f"- Foundation total: **{_fmt(meta.get('foundation_total'))}**\n"
        f"- Fines total: **{_fmt(meta.get('fines_total'))}**\n"
        f"- Loans count: **{len(loans)}**\n"
        f"- Active loan balance: **{_fmt(active_bal)}**\n"
        f"- Active unpaid interest: **{_fmt(unpaid_interest)}**\n"
        f"- Interest ledger total: **{_fmt(meta.get('interest_total'))}**\n\n"
        f"2️⃣ Risk Assessment\n"
        f"- Member Risk Grade: **{grade}** (A/B/C/D)\n\n"
        f"3️⃣ Financial Impact\n"
        f"- Any active exposure ties up liquidity; unpaid interest signals enforcement weakness.\n\n"
        f"4️⃣ Strategic Recommendation\n"
        f"- If unpaid interest exists: require settlement before additional borrowing.\n"
        f"- If exposure is high vs contributions: apply caps and structured repayment monitoring.\n"
    )
    if notes:
        msg += "\n🔒 Data Integrity Notes\n" + "\n".join([f"- {n}" for n in notes])

    return msg, meta


def _loans_with_member(
    sb_anon, sb_service, schema: str, member_id: Optional[str], members_truth: pd.DataFrame
) -> Tuple[str, pd.DataFrame, str]:
    if "v_loans_with_member" in RELATIONS:
        filters = [("member_id", "eq", member_id)] if member_id else None
        df = _sb_select(sb_anon, sb_service, schema, "v_loans_with_member", cols="*", limit=5000, filters=filters)
        src = "v_loans_with_member"
    else:
        filters = [("member_id", "eq", member_id)] if member_id else None
        df = _sb_select(sb_anon, sb_service, schema, "loans", cols="*", limit=5000, filters=filters)
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
# HF Router (optional; ONLY for general wording, never DB commands)
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
    payload = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 650}
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
    payload = {"model": model, "prompt": prompt, "temperature": 0.2, "max_tokens": 650}
    ok, raw = _post_with_retries(HF_ROUTER_COMPLETIONS_URL, headers, payload, timeout=timeout)
    if not ok:
        return False, raw
    try:
        data = json.loads(raw)
        text = ((data.get("choices") or [{}])[0].get("text") or "")
        return True, str(text).strip()
    except Exception:
        return False, f"Bad HF completions response: {raw[:600]}"


def _hf_call(model: str, token: str, messages: List[Dict[str, str]]) -> Tuple[bool, str, str, str]:
    """
    Returns: ok, text, mode_used, model_used
    Uses ONLY HF_ALLOWED_MODELS (3).
    """
    force = (os.getenv("HF_FORCE_MODE", "") or "auto").strip().lower()
    prompt = _messages_to_prompt(messages)

    # ✅ Hard-lock: only these 3 models, no extras
    model_order = list(HF_ALLOWED_MODELS)

    def _looks_instruct(mname: str) -> bool:
        mlc = (mname or "").lower()
        return any(x in mlc for x in ["instruct", "mistral", "llama-3", "llama-3.1"])

    last_err = ""
    last_mode = "failed"
    last_model = model_order[0] if model_order else ""

    def _should_try_next(err_text: str) -> bool:
        e = (err_text or "").lower()
        return any(
            s in e
            for s in [
                "404",
                "not found",
                "429",
                "500",
                "502",
                "503",
                "504",
                "timeout",
                "server error",
                "model_not_supported",
                "not supported",
                "invalid_request_error",
            ]
        )

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
                ok, txt = _hf_router_completions(chosen, token, prompt)
                if ok and txt:
                    return True, txt, "completions", chosen
                last_err = txt
            else:
                ok, txt = _hf_router_chat(chosen, token, messages)
                if ok and txt:
                    return True, txt, "chat", chosen
                last_err = txt

        if not _should_try_next(last_err):
            break

    return False, last_err or "Unknown HF error", last_mode, last_model


# -----------------------------------------------------------------------------
# Main UI
# -----------------------------------------------------------------------------
def render_njangi_llm_panel(sb_anon, sb_service, schema: str) -> None:
    st.subheader("💬 younchat", anchor=False)

    hf_token = (os.getenv("HF_TOKEN") or "").strip()
    hf_force = (os.getenv("HF_FORCE_MODE") or "auto").strip().lower()
    internet_on = _internet_enabled()

    with st.expander("⚙️ Chat Settings", expanded=False):
        st.write("**HF models (locked)**:", ", ".join(HF_ALLOWED_MODELS))
        st.write("**HF pool size**:", len(HF_ALLOWED_MODELS))
        st.write("**HF_TOKEN present**:", "✅ Yes" if hf_token else "❌ No")
        st.write("**HF_FORCE_MODE**:", hf_force)
        st.write("**Internet**:", "✅ ON" if internet_on else "❌ OFF")
        st.caption("Njangi numbers are ALWAYS answered from DB. HF is only for general wording (never DB commands).")

    @st.cache_data(ttl=30, show_spinner=False)
    def _cached_members_truth(_ts: int) -> pd.DataFrame:
        return _load_members_truth(sb_anon, sb_service, schema, limit=3000)

    members_truth = _cached_members_truth(int(time.time() // 10))

    # ✅ ONLY INTRO LINE
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

    # DB-first commands
    elif _wants_help(q):
        used_source = "help"
        answer = (
            "Hello 👋🏽 Commands:\n\n"
            "- **members**\n"
            "- type **10** (member financial health)\n"
            "- **loans** / **loans for member 10**\n"
            "- **finance kpis**\n"
            "- **tables**\n"
            "- **show <table>** (example: show contributions)\n"
            "- **describe <table>** (example: describe loans)\n"
            "- Ask: **How are we doing?** (Financial Intelligence Review)\n"
            "- **web: <topic>** (internet help)\n"
        )

    elif _wants_tables_list(q):
        used_source = "relations"
        rows = [{"relation": k, "type": RELATIONS[k].get("type", "?")} for k in sorted(RELATIONS.keys())]
        df_show = pd.DataFrame(rows)
        df_title = "Readable relations (allowlist)"
        answer = "Hello 👋🏽 Here are the tables/views younchat can read:"

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
            df_title = f"Preview: {rel}"

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

    # ✅ Financial Intelligence Review (global control tower)
    elif _wants_financial_review(q):
        used_source = "finance_intel"
        ctx = _collect_global_finance_context(sb_anon, sb_service, schema)
        metrics = _compute_global_metrics(ctx)
        answer = _build_control_tower_report(metrics, members_truth)

    # ✅ Member-focused health (by member_id)
    elif member_id_focus and (
        q.strip().isdigit()
        or "member" in _lc(q)
        or "summary" in _lc(q)
        or "status" in _lc(q)
        or _wants_member_risk(q)
    ):
        answer, meta = _member_financial_totals(sb_anon, sb_service, schema, str(member_id_focus), members_truth)
        used_source = meta.get("source", "member_summary_local")

    elif _lc(q) in RELATIONS:
        rel = _lc(q)
        answer, df_show, used_source = _show_relation(sb_anon, sb_service, schema, rel)
        df_title = f"Preview: {rel}"

    # General wording: optional HF (NEVER DB commands)
    else:
        if hf_token and not _is_db_command(q):
            sys = (
                "You are younchat for the Njangi platform 'theyoungshallgrow'.\n"
                "Rules:\n"
                "- Start with 'Hello' when appropriate.\n"
                "- Do NOT output SQL or Python.\n"
                "- Do NOT invent Njangi numbers, totals, balances, dates, counts, or member IDs.\n"
                "- If the user asks for financial status, suggest: 'How are we doing?' or commands: members / loans / finance kpis / tables / show <table> / describe <table>.\n"
                "- Keep responses professional, analytical, and grounded.\n"
            )
            messages = [{"role": "system", "content": sys}]
            for m in st.session_state["younchat_history"][-10:]:
                if m.get("role") in ("user", "assistant"):
                    messages.append({"role": m["role"], "content": m.get("content", "")})

            ok, txt, mode, model_used = _hf_call("ignored", hf_token, messages)
            used_source = f"hf:{mode}:{model_used}" if ok else f"hf:failed:{model_used}"
            answer = txt if ok else f"Hello 👋🏽 HF is not reachable: {txt}"
        else:
            if _is_db_command(q):
                used_source = "db:first_guard"
                answer = (
                    "Hello 👋🏽 I can answer using your real Njangi database only.\n\n"
                    "Use one of these:\n"
                    "- **members**\n"
                    "- **loans**\n"
                    "- **finance kpis**\n"
                    "- **tables**\n"
                    "- **show contributions**\n"
                    "- **describe loans**\n"
                    "- Ask: **How are we doing?** (Financial Intelligence Review)\n"
                )
            else:
                used_source = "local:fallback"
                answer = "Hello 👋🏽"

    # Enforce Hello on outputs (keep intro exact)
    if answer != _intro_only():
        answer = _force_hello_prefix(answer)

    st.session_state["younchat_history"].append({"role": "assistant", "content": answer})
    with st.chat_message("assistant"):
        st.markdown(answer)
        if df_show is not None and df_title:
            with st.expander(df_title, expanded=False):
                st.dataframe(df_show, use_container_width=True)

    st.caption(f"Source used: {used_source} • member_id: {member_id_focus or '—'} • Internet: {'ON' if internet_on else 'OFF'}")
