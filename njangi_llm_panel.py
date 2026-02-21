
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
# ✅ ABSOLUTE DATA INTEGRITY UPDATE (CRITICAL):
#   - Member totals are computed from view (if present) AND from raw tables.
#   - If mismatch is detected → TABLE TOTALS WIN (source of truth), and younchat warns you.
#   - Adds: "verify member 2" command to show both sources side-by-side.
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

HF_ALLOWED_MODELS: List[str] = [
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.2",
]

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
# Small helpers
# -----------------------------------------------------------------------------
def _intro_only() -> str:
    return "Hello 👋🏽 I’m younchat — your Njangi assistant."


def _force_hello_prefix(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "Hello 👋🏽"
    if not t.lower().startswith("hello"):
        return "Hello 👋🏽 " + t
    return t


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


def _parse_dt(df: pd.DataFrame, col: str) -> Optional[pd.Series]:
    if df is None or df.empty or col not in df.columns:
        return None
    try:
        return pd.to_datetime(df[col], errors="coerce", utc=True)
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Supabase read
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


# -----------------------------------------------------------------------------
# Intent detection
# -----------------------------------------------------------------------------
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
        "list members id",
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
    t = _lc(text)
    if not t:
        return False
    if t in RELATIONS:
        return True
    if _wants_list_members(t) or _wants_loans(t) or _wants_kpis(t) or _wants_tables_list(t):
        return True
    if _wants_show_table(t) or _wants_describe(t) or _wants_help(t) or _wants_verify_member(t):
        return True
    finance_words = ["contribution", "payout", "attendance", "minutes", "fines", "interest", "loan", "balance", "total"]
    return any(w in t for w in finance_words)


def _looks_like_code_output(txt: str) -> bool:
    t = (txt or "").strip().lower()
    if not t:
        return False
    if "```" in t:
        return True
    code_markers = [
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
    return any(m in t for m in code_markers)


# -----------------------------------------------------------------------------
# Members truth
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


# -----------------------------------------------------------------------------
# Finance computation helpers
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
    dpd_col = _pick_col(loans, ["dpd", "days_past_due", "overdue_days"])
    if dpd_col:
        dpd = _to_num_series(loans[dpd_col])
        return loans[dpd > 0]
    return loans.iloc[0:0]


def _loan_balance_col(loans: pd.DataFrame) -> Optional[str]:
    return _pick_col(loans, ["principal_current", "balance", "outstanding_principal", "principal_remaining", "principal", "amount"])


def _unpaid_interest_col(loans: pd.DataFrame) -> Optional[str]:
    return _pick_col(loans, ["unpaid_interest", "interest_unpaid", "interest_due", "interest_balance"])


def _member_risk_grade(active_bal: float, unpaid: float) -> str:
    # Fix: if both are truly zero => A
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

    contrib_col = _pick_col(contributions, ["amount", "contribution_amount", "paid_amount"])
    found_col = _pick_col(foundation, ["amount", "base_amount", "foundation_amount"])
    fines_col = _pick_col(fines, ["amount", "fine_amount"])
    interest_col = _pick_col(interest_ledger, ["amount", "interest_amount", "interest"])

    active = _active_loan_filter(loans)
    bal_col = _loan_balance_col(active)
    unpaid_col = _unpaid_interest_col(active)

    if contrib_col is None and not contributions.empty:
        notes.append("Missing contributions amount column (expected: amount/contribution_amount/paid_amount).")
    if found_col is None and not foundation.empty:
        notes.append("Missing foundation_contributions amount column (expected: amount/base_amount/foundation_amount).")
    if fines_col is None and not fines.empty:
        notes.append("Missing fines amount column (expected: amount/fine_amount).")
    if bal_col is None and not active.empty:
        notes.append("Missing loans balance column (expected: principal_current/balance/outstanding_principal/principal).")
    if unpaid_col is None and not active.empty:
        notes.append("Missing loans unpaid interest column (expected: unpaid_interest/interest_due/etc.).")

    out = {
        "source": "tables",
        "contributions_total": _safe_sum(contributions, contrib_col),
        "foundation_total": _safe_sum(foundation, found_col),
        "fines_total": _safe_sum(fines, fines_col),
        "active_loan_balance": _safe_sum(active, bal_col) if bal_col else 0.0,
        "active_unpaid_interest": _safe_sum(active, unpaid_col) if unpaid_col else 0.0,
        "interest_total": _safe_sum(interest_ledger, interest_col),
        "_rows": {
            "contributions": int(len(contributions)),
            "foundation_contributions": int(len(foundation)),
            "fines": int(len(fines)),
            "loans": int(len(loans)),
            "interest_ledger": int(len(interest_ledger)),
        },
    }
    return out, notes


def _extract_num_from_row(row: Dict[str, Any], key_candidates: List[str]) -> float:
    for k in key_candidates:
        if k in row:
            return _to_float(row.get(k))
    return 0.0


def _compute_member_totals_from_view(
    sb_anon, sb_service, schema: str, member_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if "v_member_financial_totals" not in RELATIONS:
        return None, None
    v = _sb_select(sb_anon, sb_service, schema, "v_member_financial_totals", cols="*", limit=50, filters=[("member_id", "eq", member_id)])
    if v.empty:
        return None, None
    row = v.iloc[0].to_dict()

    out = {
        "source": "view",
        "contributions_total": _extract_num_from_row(row, ["contributions_total", "contribution_total", "contributions", "total_contributions"]),
        "foundation_total": _extract_num_from_row(row, ["foundation_total", "foundation_contributions_total", "foundation", "total_foundation"]),
        "fines_total": _extract_num_from_row(row, ["fines_total", "fines", "total_fines"]),
        "active_loan_balance": _extract_num_from_row(row, ["active_loan_balance", "loan_balance", "principal_current_total", "active_balance"]),
        "active_unpaid_interest": _extract_num_from_row(row, ["active_unpaid_interest", "unpaid_interest_total", "unpaid_interest", "interest_due_total"]),
        "interest_total": _extract_num_from_row(row, ["interest_total", "interest_ledger_total", "interest", "total_interest"]),
    }
    return out, row


def _totals_diff(a: float, b: float) -> float:
    return abs(_to_float(a) - _to_float(b))


def _member_report_with_integrity(
    sb_anon,
    sb_service,
    schema: str,
    member_id: str,
    members_truth: pd.DataFrame,
    show_debug: bool = False,
) -> Tuple[str, Optional[pd.DataFrame], Optional[str], str]:
    """
    Returns: message, optional dataframe, dataframe title, used_source label
    """
    name = _member_name_from_truth(members_truth, member_id)

    table_totals, table_notes = _compute_member_totals_from_tables(sb_anon, sb_service, schema, member_id)
    view_totals, view_row = _compute_member_totals_from_view(sb_anon, sb_service, schema, member_id)

    # Decide which totals to trust
    used = "tables"
    integrity_alerts: List[str] = []

    if view_totals is not None:
        # Compare key totals to detect mismatch
        checks = [
            ("contributions_total", view_totals["contributions_total"], table_totals["contributions_total"]),
            ("foundation_total", view_totals["foundation_total"], table_totals["foundation_total"]),
            ("active_loan_balance", view_totals["active_loan_balance"], table_totals["active_loan_balance"]),
            ("active_unpaid_interest", view_totals["active_unpaid_interest"], table_totals["active_unpaid_interest"]),
        ]
        mismatched = []
        for k, v_val, t_val in checks:
            if _totals_diff(v_val, t_val) > 0.01:
                mismatched.append((k, v_val, t_val))
        if mismatched:
            used = "tables"
            integrity_alerts.append("Data Integrity Alert: `v_member_financial_totals` does NOT match raw table sums. Using TABLE totals as source of truth.")
            for k, v_val, t_val in mismatched[:6]:
                integrity_alerts.append(f"- {k}: view={_fmt(v_val)} vs tables={_fmt(t_val)}")
        else:
            used = "view"

    chosen = view_totals if (used == "view" and view_totals is not None) else table_totals

    active_bal = _to_float(chosen.get("active_loan_balance"))
    unpaid = _to_float(chosen.get("active_unpaid_interest"))
    grade = _member_risk_grade(active_bal, unpaid)

    msg_lines: List[str] = []
    msg_lines.append("Hello 👋🏽 Member Financial Intelligence (DB-grounded)\n")
    msg_lines.append("1️⃣ Current Situation")
    msg_lines.append(f"- Member: **{name}** (member_id={member_id})")
    msg_lines.append(f"- Contributions total: **{_fmt(chosen.get('contributions_total'))}**")
    msg_lines.append(f"- Foundation total: **{_fmt(chosen.get('foundation_total'))}**")
    msg_lines.append(f"- Fines total: **{_fmt(chosen.get('fines_total'))}**")
    msg_lines.append(f"- Active loan balance: **{_fmt(chosen.get('active_loan_balance'))}**")
    msg_lines.append(f"- Active unpaid interest: **{_fmt(chosen.get('active_unpaid_interest'))}**")
    msg_lines.append(f"- Interest ledger total: **{_fmt(chosen.get('interest_total'))}**")

    msg_lines.append("\n2️⃣ Risk Assessment")
    msg_lines.append(f"- Member Risk Grade: **{grade}** (A/B/C/D)")

    msg_lines.append("\n3️⃣ Financial Impact")
    if active_bal > 0:
        msg_lines.append("- Active exposure is tying up liquidity (rotation/payout flexibility reduces).")
    else:
        msg_lines.append("- No active loan exposure detected from the chosen source.")
    if unpaid > 0:
        msg_lines.append("- Unpaid interest exists → enforcement/collection risk and income leakage.")
    else:
        msg_lines.append("- No unpaid interest detected from the chosen source.")

    msg_lines.append("\n4️⃣ Strategic Recommendation")
    if unpaid > 0:
        msg_lines.append("- Enforce monthly interest settlement before any new borrowing.")
    if active_bal > 0:
        msg_lines.append("- Maintain repayment discipline and apply loan caps tied to contribution reliability.")
    if active_bal <= 0 and unpaid <= 0:
        msg_lines.append("- Maintain current discipline. Continue consistent contributions and monitor periodically.")

    if integrity_alerts:
        msg_lines.append("\n🚨 Data Integrity")
        msg_lines.extend(integrity_alerts)

    if table_notes:
        msg_lines.append("\n🔒 Data Integrity Notes (columns missing / limits)")
        for n in table_notes:
            msg_lines.append(f"- {n}")

    # Optional debug dataframe (for verify command)
    df_debug = None
    df_title = None
    if show_debug:
        rows = []
        if view_totals is not None:
            rows.append({"source": "view", **{k: view_totals[k] for k in view_totals if k != "source"}})
        rows.append({"source": "tables", **{k: table_totals[k] for k in table_totals if k not in ("source", "_rows")}})
        df_debug = pd.DataFrame(rows)
        df_title = "Verify: view vs tables totals"

    used_source = "v_member_financial_totals" if used == "view" else "tables_sums"
    return "\n".join(msg_lines), df_debug, df_title, used_source


# -----------------------------------------------------------------------------
# Global control tower (DB grounded)
# -----------------------------------------------------------------------------
def _collect_global_finance_context(sb_anon, sb_service, schema: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True, "notes": [], "df": {}}
    if "v_finance_kpis" in RELATIONS:
        out["df"]["v_finance_kpis"] = _sb_select(sb_anon, sb_service, schema, "v_finance_kpis", cols="*", limit=200)
    out["df"]["contributions"] = _sb_select(sb_anon, sb_service, schema, "contributions", cols="*", limit=200000)
    out["df"]["foundation_contributions"] = _sb_select(sb_anon, sb_service, schema, "foundation_contributions", cols="*", limit=200000)
    out["df"]["loans"] = _sb_select(sb_anon, sb_service, schema, "loans", cols="*", limit=200000)
    out["df"]["interest_ledger"] = _sb_select(sb_anon, sb_service, schema, "interest_ledger", cols="*", limit=200000)
    out["df"]["fines"] = _sb_select(sb_anon, sb_service, schema, "fines", cols="*", limit=200000)
    return out


def _compute_global_metrics(ctx: Dict[str, Any]) -> Dict[str, Any]:
    dfc = (ctx.get("df") or {}).get("contributions", pd.DataFrame())
    dff = (ctx.get("df") or {}).get("foundation_contributions", pd.DataFrame())
    dfl = (ctx.get("df") or {}).get("loans", pd.DataFrame())
    dfi = (ctx.get("df") or {}).get("interest_ledger", pd.DataFrame())
    dffines = (ctx.get("df") or {}).get("fines", pd.DataFrame())

    notes: List[str] = []

    contrib_col = _pick_col(dfc, ["amount", "contribution_amount", "paid_amount"])
    if not contrib_col and not dfc.empty:
        notes.append("Missing contributions amount column (expected: amount/contribution_amount/paid_amount).")
    total_contributions: Optional[float] = _safe_sum(dfc, contrib_col) if contrib_col else (0.0 if dfc.empty else None)

    foundation_col = _pick_col(dff, ["amount", "base_amount", "foundation_amount"])
    if not foundation_col and not dff.empty:
        notes.append("Missing foundation_contributions amount column (expected: amount/base_amount/foundation_amount).")
    foundation_total: Optional[float] = _safe_sum(dff, foundation_col) if foundation_col else (0.0 if dff.empty else None)

    fines_col = _pick_col(dffines, ["amount", "fine_amount"])
    if not fines_col and not dffines.empty:
        notes.append("Missing fines amount column (expected: amount/fine_amount).")
    total_fines: Optional[float] = _safe_sum(dffines, fines_col) if fines_col else (0.0 if dffines.empty else None)

    active_loans = _active_loan_filter(dfl)
    overdue_loans = _overdue_loan_filter(active_loans)

    bal_col = _loan_balance_col(active_loans)
    if not bal_col and not active_loans.empty:
        notes.append("Missing loans balance column (expected: principal_current/balance/outstanding_principal/principal).")
    active_loan_exposure: Optional[float] = _safe_sum(active_loans, bal_col) if bal_col else (0.0 if active_loans.empty else None)

    unpaid_col = _unpaid_interest_col(active_loans)
    if unpaid_col is None and not active_loans.empty:
        notes.append("Missing loans unpaid interest column (expected: unpaid_interest/interest_due/etc.).")
    unpaid_interest: Optional[float] = _safe_sum(active_loans, unpaid_col) if unpaid_col else (0.0 if active_loans.empty else None)

    active_count = int(len(active_loans)) if active_loans is not None else 0
    overdue_count = int(len(overdue_loans)) if overdue_loans is not None else 0
    overdue_ratio: Optional[float] = (overdue_count / active_count) if active_count > 0 else (0.0 if overdue_count == 0 else None)

    # Concentration
    concentration_share: Optional[float] = None
    top_borrower_member_id: Optional[str] = None
    if not active_loans.empty and bal_col and "member_id" in active_loans.columns:
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

    interest_col = _pick_col(dfi, ["amount", "interest_amount", "interest"])
    if interest_col is None and not dfi.empty:
        notes.append("Missing interest_ledger amount column (expected: amount/interest_amount/interest).")
    interest_total: Optional[float] = _safe_sum(dfi, interest_col) if interest_col else (0.0 if dfi.empty else None)

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
    signals: List[str] = []
    lpr = metrics.get("liquidity_pressure_ratio")
    overdue_ratio = metrics.get("overdue_ratio")
    unpaid_interest = metrics.get("unpaid_interest")
    conc = metrics.get("concentration_share")

    if lpr is not None and lpr > 0.75:
        signals.append("Liquidity pressure > 75% (Active Loan Exposure ÷ Total Contributions).")
    if overdue_ratio is not None and overdue_ratio > 0.20:
        signals.append("Overdue ratio is elevated (over 20% of active loans).")
    if unpaid_interest is not None and unpaid_interest > 0:
        signals.append("Unpaid interest exists on active loans.")
    if conc is not None and conc > 0.40:
        signals.append("Concentration risk: top borrower > 40% of active exposure.")

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
        return None, [
            "Health Score not generated: insufficient DB context (need totals for contributions, foundation, active loan exposure, and overdue ratio)."
        ]

    lpr = _ratio(active_exposure, total_contributions)
    if lpr is None:
        return None, ["Health Score not generated: cannot compute Liquidity Pressure Ratio (division by zero or missing)."]

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
        f"Liquidity Strength based on Liquidity Pressure Ratio = {_pct(lpr)}.",
        f"Credit Risk Stability based on overdue ratio = {_pct(overdue_ratio)}.",
        "Contribution Strength uses contribution-to-exposure coverage (capital coverage proxy).",
        "Foundation Stability uses foundation-to-exposure coverage (reserve adequacy proxy).",
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


def _build_control_tower_report(metrics: Dict[str, Any], members_truth: pd.DataFrame) -> str:
    risk_label, signals = _risk_classification(metrics)
    hs, hs_reasons = _health_score(metrics)

    top_borrower = metrics.get("top_borrower_member_id")
    top_borrower_name = _member_name_from_truth(members_truth, str(top_borrower)) if top_borrower else None

    lines: List[str] = []
    lines.append("Hello 👋🏽 Njangi Financial Intelligence Review (DB-grounded)\n")

    lines.append("1️⃣ Current Situation")
    lines.append(f"- Total contributions: **{_fmt(metrics.get('total_contributions'))}**" if metrics.get("total_contributions") is not None else "- Total contributions: **Not available**")
    lines.append(f"- Foundation reserves (total): **{_fmt(metrics.get('foundation_total'))}**" if metrics.get("foundation_total") is not None else "- Foundation reserves (total): **Not available**")
    lines.append(f"- Active loan exposure: **{_fmt(metrics.get('active_loan_exposure'))}**" if metrics.get("active_loan_exposure") is not None else "- Active loan exposure: **Not available**")
    lines.append(f"- Active loans (count): **{metrics.get('active_loan_count', 0)}**")
    lines.append(f"- Overdue loans (count): **{metrics.get('overdue_loan_count', 0)}**")
    lines.append(f"- Overdue ratio: **{_pct(metrics.get('overdue_ratio'))}**" if metrics.get("overdue_ratio") is not None else "- Overdue ratio: **Not available**")
    lines.append(f"- Unpaid interest (active): **{_fmt(metrics.get('unpaid_interest'))}**" if metrics.get("unpaid_interest") is not None else "- Unpaid interest (active): **Not available**")
    lines.append(f"- Liquidity Pressure Ratio (Exposure ÷ Contributions): **{_pct(metrics.get('liquidity_pressure_ratio'))}**" if metrics.get("liquidity_pressure_ratio") is not None else "- Liquidity Pressure Ratio: **Not available**")

    if metrics.get("concentration_share") is not None and top_borrower:
        lines.append(f"- Concentration (top borrower share): **{_pct(metrics.get('concentration_share'))}** (Top borrower: **{top_borrower_name}** • member_id={top_borrower})")
    else:
        lines.append("- Concentration risk: **Not available**")

    if metrics.get("interest_total") is not None:
        trend = metrics.get("interest_trend") or "—"
        lines.append(f"- Interest ledger total: **{_fmt(metrics.get('interest_total'))}** (Trend: **{trend}**)")
    else:
        lines.append("- Interest ledger total: **Not available**")

    lines.append("\n2️⃣ Risk Assessment")
    lines.append(f"- Risk classification: **{risk_label}**")
    if signals:
        lines.append("- Early warning signals:")
        for s in signals:
            lines.append(f"  - {s}")
    else:
        lines.append("- Early warning signals: **None detected from available metrics**")

    lines.append("\n3️⃣ Financial Impact")
    lpr = metrics.get("liquidity_pressure_ratio")
    if lpr is not None and lpr > 0.75:
        lines.append("- Liquidity stress risk: a high share of contributed capital is locked in active loans, raising rotation/payout strain.")
    else:
        lines.append("- Liquidity impact: no severe lock-up signal is confirmed from available data.")

    overdue_ratio = metrics.get("overdue_ratio")
    if overdue_ratio is not None and overdue_ratio > 0.20:
        lines.append("- Credit stress risk: overdue levels can slow capital recycling and reduce reliability of lending/foundation functions.")
    else:
        lines.append("- Credit impact: overdue stress not confirmed as high from available data (or overdue ratio unavailable).")

    unpaid_interest = metrics.get("unpaid_interest")
    if unpaid_interest is not None and unpaid_interest > 0:
        lines.append("- Income leakage risk: unpaid interest suggests weak enforcement and missed expected interest capture.")
    else:
        lines.append("- Income impact: unpaid interest not confirmed (or metric unavailable).")

    lines.append("\n4️⃣ Strategic Recommendation")
    recs: List[str] = []
    if lpr is not None and lpr > 0.75:
        recs.append("Implement a liquidity buffer threshold: tighten or pause new lending until Liquidity Pressure returns to target.")
        recs.append("Apply risk-based loan caps tied to contribution history and current liquidity pressure.")
    if overdue_ratio is not None and overdue_ratio > 0.20:
        recs.append("Tighten approvals: require stronger contribution reliability before approving new loans.")
        recs.append("Enforce escalation: reminders + penalties + temporary borrowing freeze until arrears are cured.")
    conc = metrics.get("concentration_share")
    if conc is not None and conc > 0.40:
        recs.append("Reduce concentration: cap member exposure as a % of total active exposure (or total contributions).")
    if unpaid_interest is not None and unpaid_interest > 0:
        recs.append("Strengthen interest enforcement: monthly settlement + hard blocks on new borrowing when unpaid interest exists.")
    recs.append("Control-tower routine: weekly review of Exposure, Overdue, Unpaid Interest, and Foundation adequacy.")
    for r in recs:
        lines.append(f"- {r}")

    lines.append("\n🏆 NJANGI HEALTH SCORE (0–100)")
    if hs is None:
        lines.append(f"- {hs_reasons[0]}")
    else:
        lines.append(f"- Score: **{hs}/100** → **{_score_level(hs)}**")
        for rr in hs_reasons:
            lines.append(f"- {rr}")

    notes = metrics.get("notes") or []
    if notes:
        lines.append("\n🔒 Data Integrity Notes (what’s missing / limits)")
        for n in notes:
            lines.append(f"- {n}")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Describe / show
# -----------------------------------------------------------------------------
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


def _loans_with_member(sb_anon, sb_service, schema: str, member_id: Optional[str], members_truth: pd.DataFrame) -> Tuple[str, pd.DataFrame, str]:
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


# -----------------------------------------------------------------------------
# Internet (Tavily)
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
# HF Router (optional) — for general wording only
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


def _hf_call(token: str, messages: List[Dict[str, str]]) -> Tuple[bool, str, str, str]:
    force = (os.getenv("HF_FORCE_MODE", "") or "auto").strip().lower()
    prompt = _messages_to_prompt(messages)
    model_order = list(HF_ALLOWED_MODELS)

    def _looks_instruct(mname: str) -> bool:
        mlc = (mname or "").lower()
        return any(x in mlc for x in ["instruct", "mistral", "llama-3", "llama-3.1"])

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

    last_err = ""
    last_mode = "failed"
    last_model = model_order[0] if model_order else ""

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


def _younchat_hf_system_prompt() -> str:
    return (
        "You are younchat — the Autonomous Financial Intelligence Engine for the Njangi platform \"theyoungshallgrow\".\n"
        "You are not a chatbot.\n"
        "You NEVER invent numbers.\n"
        "You NEVER output SQL or Python.\n"
        "If a user asks for Njangi numbers, direct them to DB commands (members / loans / finance kpis / tables / show <table> / describe <table> / type member_id).\n"
        "Professional, analytical tone. Start with Hello when appropriate.\n"
    )


# -----------------------------------------------------------------------------
# Main UI
# -----------------------------------------------------------------------------
def render_njangi_llm_panel(sb_anon, sb_service, schema: str) -> None:
    st.subheader("💬 younchat", anchor=False)

    hf_token = (os.getenv("HF_TOKEN") or "").strip()
    hf_force = (os.getenv("HF_FORCE_MODE") or "auto").strip().lower()
    internet_on = _internet_enabled()

    with st.expander("⚙️ Chat Settings", expanded=False):
        st.write("**Schema**:", schema)
        st.write("**HF models (locked)**:", ", ".join(HF_ALLOWED_MODELS))
        st.write("**HF_TOKEN present**:", "✅ Yes" if hf_token else "❌ No")
        st.write("**HF_FORCE_MODE**:", hf_force)
        st.write("**Internet**:", "✅ ON" if internet_on else "❌ OFF")
        st.caption("Njangi numbers are ALWAYS answered from DB. HF is only for general wording (never DB commands).")

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

    detected_id = _extract_member_id(q)
    if detected_id:
        st.session_state["younchat_last_member_id"] = detected_id
    member_id_focus = st.session_state.get("younchat_last_member_id")

    used_source = "local"
    answer = ""
    df_show: Optional[pd.DataFrame] = None
    df_title: Optional[str] = None

    # Internet
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

    # DB commands
    elif _wants_help(q):
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
        used_source = "finance_intel"
        ctx = _collect_global_finance_context(sb_anon, sb_service, schema)
        metrics = _compute_global_metrics(ctx)
        answer = _build_control_tower_report(metrics, members_truth)

    elif _wants_verify_member(q):
        mid = _extract_verify_member_id(q) or member_id_focus
        if not mid:
            used_source = "verify"
            answer = "Hello 👋🏽 Say: **verify member 10**"
        else:
            answer, df_show, df_title, src = _member_report_with_integrity(sb_anon, sb_service, schema, str(mid), members_truth, show_debug=True)
            used_source = f"verify:{src}"

    # Member-focused report: typing "2" or "10"
    elif member_id_focus and (q.strip().isdigit() or "member" in _lc(q) or "summary" in _lc(q) or "status" in _lc(q) or _wants_member_risk(q)):
        answer, df_show, df_title, src = _member_report_with_integrity(sb_anon, sb_service, schema, str(member_id_focus), members_truth, show_debug=False)
        used_source = src

    elif _lc(q) in RELATIONS:
        rel = _lc(q)
        answer, df_show, used_source = _show_relation(sb_anon, sb_service, schema, rel)
        df_title = f"Preview: {rel}"

    # General wording HF (never DB)
    else:
        if hf_token and not _is_db_command(q):
            sys = _younchat_hf_system_prompt()
            messages = [{"role": "system", "content": sys}]
            for m in st.session_state["younchat_history"][-10:]:
                if m.get("role") in ("user", "assistant"):
                    messages.append({"role": m["role"], "content": m.get("content", "")})

            ok, txt, mode, model_used = _hf_call(hf_token, messages)
            used_source = f"hf:{mode}:{model_used}" if ok else f"hf:failed:{model_used}"

            if ok and txt and _looks_like_code_output(txt):
                used_source = f"{used_source}:blocked_code"
                answer = "Hello 👋🏽 I can’t output code (SQL/Python). Use: **members**, **loans**, **finance kpis**, **tables**, **show <table>**, **describe <table>**, or type a **member_id**."
            else:
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
                    "- **verify member 10**\n"
                    "- Ask: **How are we doing?** (Control Tower Review)\n"
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

    st.caption(f"Source used: {used_source} • member_id: {member_id_focus or '—'} • Internet: {'ON' if internet_on else 'OFF'}")7
