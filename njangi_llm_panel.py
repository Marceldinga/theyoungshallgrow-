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
# IMPORTANT ADDITION (your request):
#   - Non-DB messages routed to HF use a strict Njangi "intent & next step" prompt:
#     The model MUST assess what the member wants inside Njangi and guide them to the
#     right DB command (or ask 1 short clarifying question), without inventing numbers.
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

# ✅ Allowlist relations (tables + views)
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


def _to_float(x: Any) -> float:
    try:
        v = pd.to_numeric(x, errors="coerce")
        if pd.isna(v):
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def _fmt(x: Any) -> str:
    return f"{_to_float(x):,.2f}"


def _pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    try:
        return f"{x * 100:.1f}%"
    except Exception:
        return "—"


def _ratio(n: Optional[float], d: Optional[float]) -> Optional[float]:
    if n is None or d is None or d == 0:
        return None
    return n / d


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


def _db_proof_line(row_counts: Dict[str, int]) -> str:
    ts = _utc_now()
    if not row_counts:
        return f"DB Proof: (no row counts) • fetched_at={ts}"
    parts = [f"{k}={int(v)}" for k, v in row_counts.items()]
    return f"DB Proof: {', '.join(parts)} • fetched_at={ts}"


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
# 3) SNAPSHOT / STATE (Manifold builders)
# =============================================================================
def _rpc_finance_snapshot(sb_anon, sb_service, schema: str) -> Dict[str, Any]:
    sb = sb_service or sb_anon
    if sb is None:
        return {}

    try:
        res = sb.schema(schema).rpc("fn_finance_snapshot", {}).execute()
    except Exception:
        try:
            res = sb.rpc("fn_finance_snapshot", {}).execute()
        except Exception:
            return {}

    data = getattr(res, "data", None)
    if not data:
        return {}
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    if isinstance(data, dict):
        return data
    return {}


def _snapshot_to_metrics(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not snapshot:
        return None

    if isinstance(snapshot.get("totals"), dict) or isinstance(snapshot.get("counts"), dict) or isinstance(snapshot.get("ratios"), dict):
        totals = snapshot.get("totals") or {}
        counts = snapshot.get("counts") or {}
        ratios = snapshot.get("ratios") or {}
        return {
            "notes": [],
            "row_counts": {k: int(v) for k, v in (counts or {}).items() if v is not None},
            "total_contributions": totals.get("total_contributions") or totals.get("contributions_total"),
            "foundation_total": totals.get("foundation_total"),
            "total_fines": totals.get("total_fines"),
            "active_loan_exposure": totals.get("active_loan_exposure"),
            "unpaid_interest": totals.get("unpaid_interest"),
            "interest_total": totals.get("interest_ledger_total") or totals.get("interest_total"),
            "active_loan_count": counts.get("active_loans") or counts.get("active_loan_count") or 0,
            "overdue_loan_count": counts.get("overdue_loans") or counts.get("overdue_loan_count") or 0,
            "overdue_ratio": ratios.get("overdue_ratio"),
            "liquidity_pressure_ratio": ratios.get("liquidity_pressure_ratio"),
            "concentration_share": ratios.get("concentration_share") if "concentration_share" in ratios else None,
            "top_borrower_member_id": ratios.get("top_borrower_member_id") if "top_borrower_member_id" in ratios else None,
        }

    rc = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    return {
        "notes": [],
        "row_counts": {k: int(v) for k, v in (rc or {}).items() if v is not None},
        "total_contributions": snapshot.get("total_contributions"),
        "foundation_total": snapshot.get("foundation_total"),
        "total_fines": snapshot.get("total_fines"),
        "active_loan_exposure": snapshot.get("active_loan_exposure"),
        "unpaid_interest": snapshot.get("unpaid_interest"),
        "interest_total": snapshot.get("interest_ledger_total") or snapshot.get("interest_total"),
        "active_loan_count": snapshot.get("active_loan_count") or 0,
        "overdue_loan_count": snapshot.get("overdue_loan_count") or 0,
        "overdue_ratio": snapshot.get("overdue_ratio"),
        "liquidity_pressure_ratio": snapshot.get("liquidity_pressure_ratio"),
        "concentration_share": snapshot.get("concentration_share"),
        "top_borrower_member_id": snapshot.get("top_borrower_member_id"),
    }


def _active_loan_filter(loans: pd.DataFrame) -> pd.DataFrame:
    if loans is None or loans.empty:
        return loans
    status_col = _pick_col(loans, ["status"])
    if not status_col:
        return loans
    s = loans[status_col].astype(str).str.lower().fillna("")
    active_status = {"active", "open", "ongoing", "overdue", "late", "running", "disbursed"}
    return loans[s.isin(active_status)]


def _overdue_loan_filter(loans: pd.DataFrame) -> pd.DataFrame:
    if loans is None or loans.empty:
        return loans
    status_col = _pick_col(loans, ["status"])
    if status_col:
        s = loans[status_col].astype(str).str.lower().fillna("")
        return loans[s.isin({"overdue", "late"})]
    dpd_col = _pick_col(loans, ["dpd", "days_past_due", "overdue_days"])
    if dpd_col:
        dpd = _to_num_series(loans[dpd_col])
        return loans[dpd > 0]
    return loans.iloc[0:0]


def _loan_balance_col(loans: pd.DataFrame) -> Optional[str]:
    return _pick_col(loans, ["principal_current", "outstanding_principal", "principal_remaining", "principal", "amount", "total_due"])


def _unpaid_interest_col(loans: pd.DataFrame) -> Optional[str]:
    return _pick_col(loans, ["unpaid_interest", "interest_unpaid", "interest_due", "interest_balance"])


def _collect_global_finance_context(sb_anon, sb_service, schema: str) -> Dict[str, Any]:
    snap = _rpc_finance_snapshot(sb_anon, sb_service, schema)
    if snap:
        return {"ok": True, "notes": [], "snapshot": snap, "mode": "rpc"}

    out: Dict[str, Any] = {"ok": True, "notes": ["Snapshot unavailable → fallback table scan."], "df": {}, "mode": "tables"}
    out["df"]["contributions"] = _sb_select(sb_anon, sb_service, schema, "contributions", cols="*", limit=200000)
    out["df"]["foundation_contributions"] = _sb_select(sb_anon, sb_service, schema, "foundation_contributions", cols="*", limit=200000)
    out["df"]["loans"] = _sb_select(sb_anon, sb_service, schema, "loans", cols="*", limit=200000)
    out["df"]["interest_ledger"] = _sb_select(sb_anon, sb_service, schema, "interest_ledger", cols="*", limit=200000)
    out["df"]["fines"] = _sb_select(sb_anon, sb_service, schema, "fines", cols="*", limit=200000)
    return out


def _compute_global_metrics(ctx: Dict[str, Any]) -> Dict[str, Any]:
    snap = ctx.get("snapshot") or {}
    snap_metrics = _snapshot_to_metrics(snap) if isinstance(snap, dict) else None
    if snap_metrics is not None:
        snap_metrics["notes"] = list(snap_metrics.get("notes") or []) + list(ctx.get("notes") or [])
        snap_metrics["_mode"] = ctx.get("mode")
        snap_metrics["_generated_at"] = _utc_now()
        return snap_metrics

    dfc = (ctx.get("df") or {}).get("contributions", pd.DataFrame())
    dff = (ctx.get("df") or {}).get("foundation_contributions", pd.DataFrame())
    dfl = (ctx.get("df") or {}).get("loans", pd.DataFrame())
    dfi = (ctx.get("df") or {}).get("interest_ledger", pd.DataFrame())
    dffines = (ctx.get("df") or {}).get("fines", pd.DataFrame())

    notes: List[str] = list(ctx.get("notes") or [])

    contrib_col = _pick_col(dfc, ["amount"])
    if not contrib_col and not dfc.empty:
        notes.append("Missing contributions amount column (expected: amount).")
    total_contributions: Optional[float] = _safe_sum(dfc, contrib_col) if contrib_col else (0.0 if dfc.empty else None)

    foundation_col = _pick_col(dff, ["amount"])
    if not foundation_col and not dff.empty:
        notes.append("Missing foundation_contributions amount column (expected: amount).")
    foundation_total: Optional[float] = _safe_sum(dff, foundation_col) if foundation_col else (0.0 if dff.empty else None)

    fines_col = _pick_col(dffines, ["amount"])
    if not fines_col and not dffines.empty:
        notes.append("Missing fines amount column (expected: amount).")
    total_fines: Optional[float] = _safe_sum(dffines, fines_col) if fines_col else (0.0 if dffines.empty else None)

    active_loans = _active_loan_filter(dfl)
    overdue_loans = _overdue_loan_filter(active_loans)

    bal_col = _loan_balance_col(active_loans)
    if not bal_col and not active_loans.empty:
        notes.append("Missing loans balance column (expected: principal_current or principal).")
    active_loan_exposure: Optional[float] = _safe_sum(active_loans, bal_col) if bal_col else (0.0 if active_loans.empty else None)

    unpaid_col = _unpaid_interest_col(active_loans)
    if unpaid_col is None and not active_loans.empty:
        notes.append("Missing loans unpaid interest column (expected: unpaid_interest).")
    unpaid_interest: Optional[float] = _safe_sum(active_loans, unpaid_col) if unpaid_col else (0.0 if active_loans.empty else None)

    active_count = int(len(active_loans)) if active_loans is not None else 0
    overdue_count = int(len(overdue_loans)) if overdue_loans is not None else 0
    overdue_ratio: Optional[float] = (overdue_count / active_count) if active_count > 0 else (0.0 if overdue_count == 0 else None)

    interest_col = _pick_col(dfi, ["amount"])
    if interest_col is None and not dfi.empty:
        notes.append("Missing interest_ledger amount column (expected: amount).")
    interest_total: Optional[float] = _safe_sum(dfi, interest_col) if interest_col else (0.0 if dfi.empty else None)

    liquidity_pressure = _ratio(active_loan_exposure, total_contributions) if active_loan_exposure is not None else None

    row_counts = {
        "contributions": int(len(dfc)),
        "foundation_contributions": int(len(dff)),
        "loans": int(len(dfl)),
        "interest_ledger": int(len(dfi)),
        "fines": int(len(dffines)),
    }

    return {
        "notes": notes,
        "row_counts": row_counts,
        "total_contributions": total_contributions,
        "foundation_total": foundation_total,
        "total_fines": total_fines,
        "active_loan_exposure": active_loan_exposure,
        "active_loan_count": active_count,
        "overdue_loan_count": overdue_count,
        "overdue_ratio": overdue_ratio,
        "unpaid_interest": unpaid_interest,
        "interest_total": interest_total,
        "liquidity_pressure_ratio": liquidity_pressure,
        "concentration_share": None,
        "top_borrower_member_id": None,
        "_mode": ctx.get("mode"),
        "_generated_at": _utc_now(),
    }


def _risk_classification(metrics: Dict[str, Any]) -> Tuple[str, List[str]]:
    signals: List[str] = []
    lpr = metrics.get("liquidity_pressure_ratio")
    overdue_ratio = metrics.get("overdue_ratio")
    unpaid_interest = metrics.get("unpaid_interest")
    conc = metrics.get("concentration_share")

    if lpr is not None and lpr > 0.75:
        signals.append("Liquidity pressure > 75% (Exposure ÷ Contributions).")
    if overdue_ratio is not None and overdue_ratio > 0.20:
        signals.append("Overdue ratio is elevated (> 20% of active loans).")
    if unpaid_interest is not None and unpaid_interest > 0:
        signals.append("Unpaid interest exists on active loans.")
    if conc is not None and conc > 0.40:
        signals.append("Concentration risk: top borrower > 40% of exposure.")

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
    total_contributions = metrics.get("total_contributions")
    foundation_total = metrics.get("foundation_total")
    active_exposure = metrics.get("active_loan_exposure")
    overdue_ratio = metrics.get("overdue_ratio")

    if total_contributions is None or foundation_total is None or active_exposure is None or overdue_ratio is None:
        return None, ["Health Score not generated: insufficient DB context (missing totals/exposure/overdue ratio)."]

    lpr = _ratio(active_exposure, total_contributions)
    if lpr is None:
        return None, ["Health Score not generated: cannot compute Liquidity Pressure Ratio."]

    if lpr <= 0.25:
        liq = 95
    elif lpr <= 0.50:
        liq = 80
    elif lpr <= 0.75:
        liq = 60
    else:
        liq = 35

    if overdue_ratio <= 0.05:
        cred = 95
    elif overdue_ratio <= 0.10:
        cred = 85
    elif overdue_ratio <= 0.20:
        cred = 65
    else:
        cred = 40

    coverage = _ratio(total_contributions, max(active_exposure, 1e-9))
    contrib_strength = 90 if (coverage is not None and coverage >= 4) else (75 if (coverage is not None and coverage >= 2) else (60 if (coverage is not None and coverage >= 1) else 40))

    fcover = _ratio(foundation_total, max(active_exposure, 1e-9))
    foundation_strength = 90 if (fcover is not None and fcover >= 1) else (75 if (fcover is not None and fcover >= 0.5) else (60 if (fcover is not None and fcover >= 0.25) else 40))

    score = round(0.30 * liq + 0.30 * cred + 0.20 * contrib_strength + 0.20 * foundation_strength)
    reasons = [
        f"Liquidity Strength from Liquidity Pressure Ratio = {_pct(lpr)}.",
        f"Credit Stability from overdue ratio = {_pct(overdue_ratio)}.",
        "Contribution Strength uses contribution-to-exposure coverage proxy.",
        "Foundation Stability uses foundation-to-exposure coverage proxy.",
    ]
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


def _manifold_global_state(metrics: Dict[str, Any]) -> Dict[str, Any]:
    risk_label, signals = _risk_classification(metrics)
    hs, hs_reasons = _health_score(metrics)

    return {
        "manifold_type": "global_finance_state",
        "generated_at_utc": metrics.get("_generated_at") or _utc_now(),
        "mode": metrics.get("_mode") or "unknown",
        "metrics": {
            "total_contributions": metrics.get("total_contributions"),
            "foundation_total": metrics.get("foundation_total"),
            "total_fines": metrics.get("total_fines"),
            "active_loan_exposure": metrics.get("active_loan_exposure"),
            "active_loan_count": metrics.get("active_loan_count"),
            "overdue_loan_count": metrics.get("overdue_loan_count"),
            "overdue_ratio": metrics.get("overdue_ratio"),
            "unpaid_interest": metrics.get("unpaid_interest"),
            "interest_total": metrics.get("interest_total"),
            "liquidity_pressure_ratio": metrics.get("liquidity_pressure_ratio"),
            "concentration_share": metrics.get("concentration_share"),
            "top_borrower_member_id": metrics.get("top_borrower_member_id"),
        },
        "derived": {
            "risk_classification": risk_label,
            "risk_signals": signals,
            "health_score": hs,
            "health_score_level": (_score_level(hs) if isinstance(hs, int) else None),
            "health_score_reasons": hs_reasons,
        },
        "db_proof": {
            "row_counts": metrics.get("row_counts") or {},
            "notes": metrics.get("notes") or [],
        },
    }


# =============================================================================
# 4) MEMBERS TRUTH
# =============================================================================
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


def _member_name_from_truth(members_truth: pd.DataFrame, member_id: str) -> str:
    if members_truth is None or members_truth.empty:
        return "(unknown)"
    hit = members_truth[members_truth["member_id"].astype(str) == str(member_id)]
    if hit.empty:
        return "(unknown)"
    return str(hit.iloc[0]["member_name"])


def _member_exists(members_truth: pd.DataFrame, member_id: str) -> bool:
    if members_truth is None or members_truth.empty:
        return False
    return not members_truth[members_truth["member_id"].astype(str) == str(member_id)].empty


def _member_risk_grade(active_bal: float, unpaid: float) -> str:
    if active_bal <= 0 and unpaid <= 0:
        return "A"
    if active_bal > 0 and unpaid <= 0:
        return "B"
    if active_bal > 0 and unpaid > 0:
        return "C"
    if unpaid > 0:
        return "C"
    return "C"


def _compute_member_totals_from_tables(
    sb_anon, sb_service, schema: str, member_id: str
) -> Tuple[Dict[str, Any], List[str]]:
    notes: List[str] = []

    contributions = _sb_select(sb_anon, sb_service, schema, "contributions", cols="*", limit=200000, filters=[("member_id", "eq", member_id)])
    foundation = _sb_select(sb_anon, sb_service, schema, "foundation_contributions", cols="*", limit=200000, filters=[("member_id", "eq", member_id)])
    fines = _sb_select(sb_anon, sb_service, schema, "fines", cols="*", limit=200000, filters=[("member_id", "eq", member_id)])
    loans = _sb_select(sb_anon, sb_service, schema, "loans", cols="*", limit=200000, filters=[("member_id", "eq", member_id)])
    interest_ledger = _sb_select(sb_anon, sb_service, schema, "interest_ledger", cols="*", limit=200000, filters=[("member_id", "eq", member_id)])

    contrib_col = _pick_col(contributions, ["amount"])
    found_col = _pick_col(foundation, ["amount"])
    fines_col = _pick_col(fines, ["amount"])
    interest_col = _pick_col(interest_ledger, ["amount"])

    active = _active_loan_filter(loans)
    bal_col = _loan_balance_col(active)
    unpaid_col = _unpaid_interest_col(active)

    if contrib_col is None and not contributions.empty:
        notes.append("Missing contributions amount column (expected: amount).")
    if found_col is None and not foundation.empty:
        notes.append("Missing foundation_contributions amount column (expected: amount).")
    if fines_col is None and not fines.empty:
        notes.append("Missing fines amount column (expected: amount).")
    if bal_col is None and not active.empty:
        notes.append("Missing loans balance column (expected: principal_current or principal).")
    if unpaid_col is None and not active.empty:
        notes.append("Missing loans unpaid interest column (expected: unpaid_interest).")

    out = {
        "source": "tables",
        "contributions_total": _safe_sum(contributions, contrib_col),
        "foundation_total": _safe_sum(foundation, found_col),
        "fines_total": _safe_sum(fines, fines_col),
        "active_loan_balance": _safe_sum(active, bal_col) if bal_col else 0.0,
        "active_unpaid_interest": _safe_sum(active, unpaid_col) if unpaid_col else 0.0,
        "interest_total": _safe_sum(interest_ledger, interest_col),
        "_rows": {
            "members": 1,
            "contributions": int(len(contributions)),
            "foundation_contributions": int(len(foundation)),
            "fines": int(len(fines)),
            "loans": int(len(loans)),
            "interest_ledger": int(len(interest_ledger)),
        },
        "_generated_at": _utc_now(),
    }
    return out, notes


def _manifold_member_state(member_id: str, name: str, table_totals: Dict[str, Any], table_notes: List[str]) -> Dict[str, Any]:
    active_bal = _to_float(table_totals.get("active_loan_balance"))
    unpaid = _to_float(table_totals.get("active_unpaid_interest"))
    grade = _member_risk_grade(active_bal, unpaid)

    contrib = _to_float(table_totals.get("contributions_total"))
    exposure_ratio = _ratio(active_bal, contrib)

    return {
        "manifold_type": "member_finance_state",
        "generated_at_utc": table_totals.get("_generated_at") or _utc_now(),
        "member": {"member_id": str(member_id), "member_name": str(name)},
        "metrics": {
            "contributions_total": table_totals.get("contributions_total"),
            "foundation_total": table_totals.get("foundation_total"),
            "fines_total": table_totals.get("fines_total"),
            "active_loan_balance": table_totals.get("active_loan_balance"),
            "active_unpaid_interest": table_totals.get("active_unpaid_interest"),
            "interest_total": table_totals.get("interest_total"),
        },
        "derived": {
            "risk_grade": grade,
            "exposure_to_contributions_ratio": exposure_ratio,
            "signals": [
                ("Unpaid interest exists" if unpaid > 0 else "No unpaid interest detected"),
                ("Active exposure exists" if active_bal > 0 else "No active exposure detected"),
            ],
        },
        "db_proof": {"row_counts": table_totals.get("_rows") or {}, "notes": table_notes or []},
    }


# =============================================================================
# 5) INTENTS / PARSING
# =============================================================================
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


def _wants_kpis(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["kpi", "kpis", "finance kpi", "finance kpis", "dashboard kpi"])


def _wants_loans(text: str) -> bool:
    t = _lc(text)
    return any(k in t for k in ["loan", "loans", "borrow", "repay", "repayment", "overdue", "dpd", "interest due"])


def _wants_financial_review(text: str) -> bool:
    t = _lc(text)
    triggers = [
        "how are we doing", "are we stable", "is njangi healthy", "njangi health",
        "health score", "financial condition", "risk review", "any risk",
        "liquidity", "credit risk", "executive summary", "summary",
        "control tower", "financial intelligence",
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


def _wants_verify_member(text: str) -> bool:
    t = _lc(text)
    return t.startswith("verify member ") or t.startswith("verify ")


def _extract_verify_member_id(text: str) -> Optional[str]:
    t = _lc(text)
    t = re.sub(r"^verify(\s+member)?\s+", "", t).strip()
    m = re.search(r"(\d+)", t)
    return m.group(1) if m else None


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


def _is_db_command(text: str) -> bool:
    """
    True => MUST be answered from DB tools (never HF).
    False => route to HF (foundation model) per your request.
    """
    t = _lc(text)
    if not t:
        return False

    if t in RELATIONS:
        return True
    if _wants_list_members(t) or _wants_loans(t) or _wants_kpis(t) or _wants_tables_list(t):
        return True
    if _wants_show_table(t) or _wants_describe(t) or _wants_help(t) or _wants_verify_member(t):
        return True

    # any finance-ish questions should be treated DB-only (to prevent hallucination)
    finance_words = [
        "contribution", "contributions", "payout", "payouts", "loan", "loans",
        "repayment", "interest", "unpaid", "overdue", "balance", "exposure",
        "liquidity", "foundation", "kpi", "kpis", "risk", "health score", "grade",
        "total", "arrears", "dpd", "due",
    ]
    return any(w in t for w in finance_words)


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
# 6) TABLE/VIEW OPERATIONS
# =============================================================================
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
        src = "loans"
    title = "Loans" if not member_id else f"Loans for {_member_name_from_truth(members_truth, member_id)} (member_id={member_id})"
    return title, df, src


def _kpis(sb_anon, sb_service, schema: str) -> Tuple[str, pd.DataFrame, str]:
    if "v_finance_kpis" in RELATIONS:
        df = _sb_select(sb_anon, sb_service, schema, "v_finance_kpis", cols="*", limit=200)
        return "Finance KPIs", df, "v_finance_kpis"
    return "Finance KPIs", pd.DataFrame([{"note": "v_finance_kpis not available"}]), "fallback"


def _compute_member_totals_from_view(
    sb_anon, sb_service, schema: str, member_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if "v_member_financial_totals" not in RELATIONS:
        return None, None
    v = _sb_select(sb_anon, sb_service, schema, "v_member_financial_totals", cols="*", limit=50, filters=[("member_id", "eq", member_id)])
    if v.empty:
        return None, None
    row = v.iloc[0].to_dict()

    def _extract(row0: Dict[str, Any], keys: List[str]) -> float:
        for k in keys:
            if k in row0:
                return _to_float(row0.get(k))
        return 0.0

    out = {
        "source": "view",
        "contributions_total": _extract(row, ["contributions_total", "contribution_total", "contributions", "total_contributions"]),
        "foundation_total": _extract(row, ["foundation_total", "foundation_contributions_total", "foundation", "total_foundation"]),
        "fines_total": _extract(row, ["fines_total", "fines", "total_fines"]),
        "active_loan_balance": _extract(row, ["active_loan_balance", "loan_balance", "principal_current_total", "active_balance"]),
        "active_unpaid_interest": _extract(row, ["active_unpaid_interest", "unpaid_interest_total", "unpaid_interest", "interest_due_total"]),
        "interest_total": _extract(row, ["interest_total", "interest_ledger_total", "interest", "total_interest"]),
    }
    return out, row


def _member_verify_view_vs_tables(
    sb_anon,
    sb_service,
    schema: str,
    member_id: str,
    members_truth: pd.DataFrame,
) -> Tuple[str, pd.DataFrame, str, str]:
    if not _member_exists(members_truth, member_id):
        return (
            "Hello 👋🏽 I can’t confirm that member_id exists in `members` (source of truth). Type **members** first.",
            pd.DataFrame(),
            "Verify",
            "members_truth_missing",
        )

    name = _member_name_from_truth(members_truth, member_id)
    table_totals, table_notes = _compute_member_totals_from_tables(sb_anon, sb_service, schema, member_id)
    view_totals, _view_row = _compute_member_totals_from_view(sb_anon, sb_service, schema, member_id)

    rows = []
    if view_totals is not None:
        rows.append({"source": "view", **{k: view_totals.get(k) for k in view_totals if k != "source"}})
    rows.append(
        {
            "source": "tables",
            "contributions_total": table_totals.get("contributions_total"),
            "foundation_total": table_totals.get("foundation_total"),
            "fines_total": table_totals.get("fines_total"),
            "active_loan_balance": table_totals.get("active_loan_balance"),
            "active_unpaid_interest": table_totals.get("active_unpaid_interest"),
            "interest_total": table_totals.get("interest_total"),
        }
    )
    df = pd.DataFrame(rows)

    msg_lines = [
        "Hello 👋🏽 Verify Member Totals (DB-grounded)",
        "",
        "1️⃣ Current Situation",
        f"- Member: **{name}** (member_id={member_id})",
        "- This compares view `v_member_financial_totals` (if present) vs raw table sums.",
        "",
        "🧾 DB Proof",
        f"- {_db_proof_line(table_totals.get('_rows', {}))}",
    ]
    if table_notes:
        msg_lines.append("")
        msg_lines.append("🔒 Data Integrity Notes (columns missing / limits)")
        for n in table_notes:
            msg_lines.append(f"- {n}")

    return "\n".join(msg_lines), df, "Verify: view vs tables totals", "verify:view_vs_tables"


# =============================================================================
# 7) INTERNET (Tavily) — NEVER used for Njangi numbers
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
# 8) FOUNDATION MODEL (HF Router)
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
    payload = {"model": model, "messages": messages, "temperature": 0.20, "max_tokens": 520}
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
    payload = {"model": model, "prompt": prompt, "temperature": 0.20, "max_tokens": 520}
    ok, raw = _post_with_retries(HF_ROUTER_COMPLETIONS_URL, headers, payload, timeout=timeout)
    if not ok:
        return False, raw
    try:
        data = json.loads(raw)
        text = ((data.get("choices") or [{}])[0].get("text") or "")
        return True, str(text).strip()
    except Exception:
        return False, f"Bad HF completions response: {raw[:600]}"


def _manifold_reasoner_prompt(manifold_state: Dict[str, Any], question: str) -> str:
    return (
        "You are younchat, an assistant inside the Njangi platform.\n"
        "RULES (STRICT):\n"
        "- Use ONLY MANIFOLD_STATE JSON below.\n"
        "- Do NOT invent numbers, dates, or IDs.\n"
        "- If something is missing in MANIFOLD_STATE, say: 'I don’t have that in this snapshot/state.'\n"
        "- Keep answers short, structured, and directly responsive.\n"
        "- Never output SQL or Python.\n\n"
        f"MANIFOLD_STATE:\n{json.dumps(manifold_state, ensure_ascii=False)}\n\n"
        f"USER_QUESTION:\n{question}\n\n"
        "Answer:"
    )


def _hf_reason_over_manifold(question: str, manifold_state: Dict[str, Any]) -> Tuple[bool, str, str]:
    token = (os.getenv("HF_TOKEN") or "").strip()
    model = _hf_model()
    force = _hf_force_mode()
    if not token:
        return False, "HF_TOKEN missing", "hf:missing"

    sys = (
        "You are younchat.\n"
        "Start every answer with: 'Hello 👋🏽'.\n"
        "Never output SQL or code.\n"
    )
    prompt = _manifold_reasoner_prompt(manifold_state, question)
    messages = [{"role": "system", "content": sys}, {"role": "user", "content": prompt}]

    if force == "completions":
        ok, txt = _hf_router_completions(model, token, prompt)
        return (ok, txt, f"hf:completions:{model}") if ok else (False, txt, f"hf:completions_failed:{model}")
    if force == "chat":
        ok, txt = _hf_router_chat(model, token, messages)
        return (ok, txt, f"hf:chat:{model}") if ok else (False, txt, f"hf:chat_failed:{model}")

    ok, txt = _hf_router_completions(model, token, prompt)
    if ok and txt:
        return True, txt, f"hf:completions:{model}"
    ok2, txt2 = _hf_router_chat(model, token, messages)
    if ok2 and txt2:
        return True, txt2, f"hf:chat:{model}"
    return False, (txt2 or txt or "HF failed"), f"hf:failed:{model}"


def _foundation_intent_system_prompt() -> str:
    """
    Strict prompt for non-DB messages:
    - Assess what the member wants inside Njangi
    - Map to the right DB command (or ask ONE question)
    - Never invent any Njangi numbers
    """
    return (
        "You are younchat, the assistant inside the Njangi system.\n\n"
        "GOAL:\n"
        "Help members achieve their goal inside Njangi. Understand their intent and guide them to the correct Njangi action.\n\n"
        "STRICT RULES:\n"
        "1) Start every reply with exactly: \"Hello 👋🏽\"\n"
        "2) You are NOT allowed to invent or guess any Njangi financial numbers, balances, totals, dates, or member IDs.\n"
        "3) If the request needs real Njangi data, you must tell them the exact DB command to type next, OR ask exactly ONE short clarifying question.\n"
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
    """
    For NON-DB messages only.
    Uses HF directly with strict "intent & next" Njangi prompt.
    """
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
# 9) LOCAL REPORT BUILDERS (still manifold-grounded)
# =============================================================================
def _build_control_tower_report_local(manifold: Dict[str, Any]) -> str:
    m = (manifold or {}).get("metrics") or {}
    d = (manifold or {}).get("derived") or {}
    proof = (manifold or {}).get("db_proof") or {}

    risk_label = d.get("risk_classification") or "—"
    signals = d.get("risk_signals") or []
    hs = d.get("health_score")
    hs_lvl = d.get("health_score_level") or "—"
    hs_reasons = d.get("health_score_reasons") or []

    lines: List[str] = []
    lines.append("Hello 👋🏽 Njangi Financial Intelligence Review (DB-grounded)\n")

    lines.append("1️⃣ Current Situation")
    lines.append(f"- Total contributions: **{_fmt(m.get('total_contributions'))}**" if m.get("total_contributions") is not None else "- Total contributions: **Not available**")
    lines.append(f"- Foundation reserves: **{_fmt(m.get('foundation_total'))}**" if m.get("foundation_total") is not None else "- Foundation reserves: **Not available**")
    lines.append(f"- Active loan exposure: **{_fmt(m.get('active_loan_exposure'))}**" if m.get("active_loan_exposure") is not None else "- Active loan exposure: **Not available**")
    lines.append(f"- Active loans (count): **{int(m.get('active_loan_count') or 0)}**")
    lines.append(f"- Overdue loans (count): **{int(m.get('overdue_loan_count') or 0)}**")
    lines.append(f"- Overdue ratio: **{_pct(m.get('overdue_ratio'))}**" if m.get("overdue_ratio") is not None else "- Overdue ratio: **Not available**")
    lines.append(f"- Unpaid interest: **{_fmt(m.get('unpaid_interest'))}**" if m.get("unpaid_interest") is not None else "- Unpaid interest: **Not available**")
    lines.append(f"- Liquidity Pressure Ratio: **{_pct(m.get('liquidity_pressure_ratio'))}**" if m.get("liquidity_pressure_ratio") is not None else "- Liquidity Pressure Ratio: **Not available**")

    lines.append("\n2️⃣ Risk Assessment")
    lines.append(f"- Risk classification: **{risk_label}**")
    if signals:
        lines.append("- Signals:")
        for s in signals[:8]:
            lines.append(f"  - {s}")
    else:
        lines.append("- Signals: **None detected from available metrics**")

    lines.append("\n🏆 NJANGI HEALTH SCORE (0–100)")
    if hs is None:
        lines.append("- Health Score not generated (missing inputs).")
    else:
        lines.append(f"- Score: **{hs}/100** → **{hs_lvl}**")
        for r in hs_reasons[:6]:
            lines.append(f"- {r}")

    lines.append("\n🧾 DB Proof")
    lines.append(f"- {_db_proof_line((proof.get('row_counts') or {}))}")

    notes = proof.get("notes") or []
    if notes:
        lines.append("\n🔒 Data Integrity Notes")
        for n in notes[:8]:
            lines.append(f"- {n}")

    return "\n".join(lines)


def _build_member_report_local(manifold: Dict[str, Any]) -> str:
    mem = (manifold or {}).get("member") or {}
    m = (manifold or {}).get("metrics") or {}
    d = (manifold or {}).get("derived") or {}
    proof = (manifold or {}).get("db_proof") or {}

    lines: List[str] = []
    lines.append("Hello 👋🏽 Member Financial Intelligence (DB-grounded)\n")
    lines.append("1️⃣ Current Situation")
    lines.append(f"- Member: **{mem.get('member_name','(unknown)')}** (member_id={mem.get('member_id','—')})")
    lines.append(f"- Contributions total: **{_fmt(m.get('contributions_total'))}**")
    lines.append(f"- Foundation total: **{_fmt(m.get('foundation_total'))}**")
    lines.append(f"- Fines total: **{_fmt(m.get('fines_total'))}**")
    lines.append(f"- Active loan balance: **{_fmt(m.get('active_loan_balance'))}**")
    lines.append(f"- Active unpaid interest: **{_fmt(m.get('active_unpaid_interest'))}**")
    lines.append(f"- Interest ledger total: **{_fmt(m.get('interest_total'))}**")

    lines.append("\n2️⃣ Risk Assessment")
    lines.append(f"- Member Risk Grade: **{d.get('risk_grade','—')}**")
    ratio_val = d.get("exposure_to_contributions_ratio")
    lines.append(f"- Exposure/Contributions ratio: **{_pct(ratio_val) if isinstance(ratio_val,(int,float)) else '—'}**")

    lines.append("\n3️⃣ Signals")
    for s in (d.get("signals") or [])[:6]:
        lines.append(f"- {s}")

    lines.append("\n🧾 DB Proof")
    lines.append(f"- {_db_proof_line((proof.get('row_counts') or {}))}")

    notes = proof.get("notes") or []
    if notes:
        lines.append("\n🔒 Data Integrity Notes")
        for n in notes[:8]:
            lines.append(f"- {n}")

    return "\n".join(lines)


# =============================================================================
# 10) MAIN UI
# =============================================================================
def render_njangi_llm_panel(sb_anon, sb_service, schema: str) -> None:
    st.subheader("💬 younchat", anchor=False)

    hf_token = (os.getenv("HF_TOKEN") or "").strip()
    internet_on = _internet_enabled()

    with st.expander("⚙️ Chat Settings", expanded=False):
        st.write("**Schema**:", schema)
        st.write("**HF models (locked)**:", ", ".join(HF_ALLOWED_MODELS))
        st.write("**HF_TOKEN present**:", "✅ Yes" if hf_token else "❌ No")
        st.write("**HF_FORCE_MODE**:", (os.getenv("HF_FORCE_MODE") or "auto"))
        st.write("**Internet**:", "✅ ON" if internet_on else "❌ OFF")
        st.caption("DB integrity: DB commands are DB-only. Non-DB messages route to HF with intent & next-step guidance.")

    # Only affects manifold narrative for intelligence reports
    use_foundation_reasoner = st.toggle(
        "Use foundation reasoner (HF) for intelligence reports (manifold-grounded)",
        value=bool(hf_token),
        help="HF writes the narrative ONLY from DB-built manifold JSON for reports. DB commands stay DB-only.",
        key="younchat_use_foundation",
    )

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
        st.rerun()

    q = st.chat_input("Type your message…")
    if not q:
        return

    st.session_state["younchat_history"].append({"role": "user", "content": q})
    with st.chat_message("user"):
        st.markdown(q)

    # Remember last member id if user mentions it
    detected_id = _extract_member_id(q)
    if detected_id:
        st.session_state["younchat_last_member_id"] = detected_id
    member_id_focus = st.session_state.get("younchat_last_member_id")

    used_source = "local"
    answer = ""
    df_show: Optional[pd.DataFrame] = None
    df_title: Optional[str] = None

    # -------------------------------------------------------------------------
    # ROUTING RULE:
    #   - DB => DB tools only
    #   - Non-DB => HF intent model (if HF_TOKEN exists)
    # -------------------------------------------------------------------------
    is_db = _is_db_command(q)

    # Internet commands are NON-DB (still never Njangi numbers)
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

    elif not is_db:
        # ✅ STRICT: every non-DB message goes to foundation model (if possible)
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

    else:
        # ---------------------------------------------------------------------
        # DB COMMANDS: DB-only (never HF for these)
        # ---------------------------------------------------------------------
        if _wants_help(q):
            used_source = "help"
            answer = (
                "Hello 👋🏽 Commands:\n\n"
                "- **members**\n"
                "- type **10** (member intelligence)\n"
                "- **verify member 10** (view vs tables)\n"
                "- **loans** / **loans for member 10**\n"
                "- **finance kpis**\n"
                "- **tables**\n"
                "- **show <table>** (example: show contributions)\n"
                "- **describe <table>** (example: describe loans)\n"
                "- Ask: **How are we doing?** (Control Tower Review)\n"
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

        elif _wants_financial_review(q):
            ctx = _collect_global_finance_context(sb_anon, sb_service, schema)
            metrics = _compute_global_metrics(ctx)
            manifold = _manifold_global_state(metrics)

            if use_foundation_reasoner and _has_hf_token():
                ok, txt, used = _hf_reason_over_manifold(q, manifold)
                if ok and txt and not _looks_like_code_output(txt):
                    used_source = used
                    answer = txt
                else:
                    used_source = f"{used}:fallback_local"
                    answer = _build_control_tower_report_local(manifold)
            else:
                used_source = "local:manifold"
                answer = _build_control_tower_report_local(manifold)

            proof_counts = (manifold.get("db_proof") or {}).get("row_counts") or {}
            answer = answer.rstrip() + "\n\n🧾 DB Proof\n- " + _db_proof_line(proof_counts)

        elif _wants_verify_member(q):
            mid = _extract_verify_member_id(q) or member_id_focus
            if not mid:
                used_source = "verify"
                answer = "Hello 👋🏽 Say: **verify member 10**"
            else:
                answer, df_show, df_title, used_source = _member_verify_view_vs_tables(sb_anon, sb_service, schema, str(mid), members_truth)

        elif member_id_focus and (q.strip().isdigit() or "member" in _lc(q) or "summary" in _lc(q) or "status" in _lc(q) or _wants_member_risk(q)):
            mid = str(member_id_focus)

            if not _member_exists(members_truth, mid):
                used_source = "members_truth_missing"
                answer = (
                    "Hello 👋🏽 I can’t confirm that member_id exists in `members` (source of truth). "
                    "Type **members** to verify IDs, then retry."
                )
            else:
                name = _member_name_from_truth(members_truth, mid)
                table_totals, table_notes = _compute_member_totals_from_tables(sb_anon, sb_service, schema, mid)
                manifold = _manifold_member_state(mid, name, table_totals, table_notes)

                if use_foundation_reasoner and _has_hf_token():
                    ok, txt, used = _hf_reason_over_manifold(q, manifold)
                    if ok and txt and not _looks_like_code_output(txt):
                        used_source = used
                        answer = txt
                    else:
                        used_source = f"{used}:fallback_local"
                        answer = _build_member_report_local(manifold)
                else:
                    used_source = "local:manifold_member"
                    answer = _build_member_report_local(manifold)

                proof_counts = (manifold.get("db_proof") or {}).get("row_counts") or {}
                answer = answer.rstrip() + "\n\n🧾 DB Proof\n- " + _db_proof_line(proof_counts)

        elif _lc(q) in RELATIONS:
            rel = _lc(q)
            answer, df_show, used_source = _show_relation(sb_anon, sb_service, schema, rel)
            df_title = f"Preview: {rel}"

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
                "- Ask: **How are we doing?**\n"
            )

    # Enforce Hello prefix while preserving exact intro message
    if answer != _intro_only():
        answer = _force_hello_prefix(answer)

    st.session_state["younchat_history"].append({"role": "assistant", "content": answer})
    with st.chat_message("assistant"):
        st.markdown(answer)
        if df_show is not None and df_title:
            with st.expander(df_title, expanded=False):
                st.dataframe(df_show, use_container_width=True)

    st.caption(
        f"Source used: {used_source} • member_id: {member_id_focus or '—'} • "
        f"Internet: {'ON' if internet_on else 'OFF'} • "
        f"HF_TOKEN: {'ON' if _has_hf_token() else 'OFF'} • "
        f"Manifold reasoner: {'ON' if (use_foundation_reasoner and _has_hf_token()) else 'OFF'}"
)
