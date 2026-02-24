# njangi_actions_agent.py ✅ SINGLE COMPLETE FILE (NO legacy)
# ✅ Fixes circular imports by:
#   - NOT importing app.py or njangi_llm_panel.py
#   - NOT importing Streamlit
#   - Exporting READ_TOOLS / WRITE_TOOLS / ALL_TOOLS as LAZY mappings (init on first use)
#
# Exports:
#   - RELATIONS
#   - READ_TOOLS, WRITE_TOOLS, ALL_TOOLS
#   - get_read_tools(), get_write_tools(), get_all_tools()
#   - execute_tool(tool_name, *args, **kwargs)

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

import pandas as pd

# =============================================================================
# 0) Allowlist relations (safe)
# =============================================================================
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
# 1) Small helpers
# =============================================================================
def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _clean(x: Any) -> str:
    return ("" if x is None else str(x)).strip()


def _pick_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _to_num(x: Any) -> float:
    try:
        v = pd.to_numeric(x, errors="coerce")
        if pd.isna(v):
            return 0.0
        return float(v)
    except Exception:
        return 0.0


# =============================================================================
# 2) Safe Supabase SELECT (no streamlit, no side effects)
# =============================================================================
def sb_select(
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
    sb_anon, sb_service are Supabase clients from supabase-py create_client().
    This function never imports Streamlit and never throws (returns empty df on error).
    """
    if relation not in RELATIONS:
        return pd.DataFrame()

    sb = sb_service or sb_anon
    if sb is None:
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
                    q = q.in_(col, val)  # type: ignore[attr-defined]
        if order:
            c, asc = order
            q = q.order(c, desc=not asc)
        return q

    # schema-first, fallback to default schema calls
    try:
        q = sb.schema(schema).table(relation).select(cols).limit(int(limit))
        q = _apply(q)
        res = q.execute()
        return pd.DataFrame(getattr(res, "data", None) or [])
    except Exception:
        try:
            q = sb.table(relation).select(cols).limit(int(limit))
            q = _apply(q)
            res = q.execute()
            return pd.DataFrame(getattr(res, "data", None) or [])
        except Exception:
            return pd.DataFrame()


# =============================================================================
# 3) READ TOOLS (DB-GROUNDED)
# =============================================================================
def tool_tables(*_args, **_kwargs) -> Dict[str, Any]:
    rows = [{"relation": k, "type": RELATIONS[k].get("type", "?")} for k in sorted(RELATIONS.keys())]
    return {"ok": True, "data": rows, "meta": {"generated_at": _utc_now()}}


def tool_members(sb_anon, sb_service, schema: str, limit: int = 5000) -> Dict[str, Any]:
    df = sb_select(sb_anon, sb_service, schema, "members", cols="*", limit=limit, order=("id", True))
    if df.empty:
        return {"ok": True, "data": [], "meta": {"note": "no members or RLS blocked", "generated_at": _utc_now()}}

    id_col = _pick_col(df, ["id", "member_id"])
    name_col = _pick_col(df, ["display_name", "full_name", "name"])
    phone_col = _pick_col(df, ["phone"])

    out: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        out.append(
            {
                "member_id": _clean(r.get(id_col)) if id_col else None,
                "member_name": _clean(r.get(name_col)) if name_col else "",
                "phone": _clean(r.get(phone_col)) if phone_col else "",
            }
        )
    return {"ok": True, "data": out, "meta": {"count": len(out), "generated_at": _utc_now()}}


def tool_show_table(
    sb_anon,
    sb_service,
    schema: str,
    relation: str,
    limit: int = 2000,
    order_by: Optional[str] = None,
    order_asc: bool = False,
) -> Dict[str, Any]:
    if relation not in RELATIONS:
        return {"ok": False, "error": f"relation not allowed: {relation}", "data": []}

    order = (order_by, order_asc) if order_by else None
    df = sb_select(sb_anon, sb_service, schema, relation, cols="*", limit=limit, order=order)
    return {"ok": True, "data": (df.to_dict(orient="records") if not df.empty else []), "meta": {"generated_at": _utc_now()}}


def tool_describe_table(sb_anon, sb_service, schema: str, relation: str) -> Dict[str, Any]:
    if relation not in RELATIONS:
        return {"ok": False, "error": f"relation not allowed: {relation}", "data": []}
    df = sb_select(sb_anon, sb_service, schema, relation, cols="*", limit=1)
    cols = list(df.columns) if df is not None else []
    return {"ok": True, "data": [{"column_name": c} for c in cols], "meta": {"generated_at": _utc_now()}}


def tool_loans(sb_anon, sb_service, schema: str, member_id: Optional[str] = None, limit: int = 5000) -> Dict[str, Any]:
    # prefer view if allowlisted
    rel = "v_loans_with_member" if "v_loans_with_member" in RELATIONS else "loans"
    filters = [("member_id", "eq", member_id)] if member_id else None
    df = sb_select(sb_anon, sb_service, schema, rel, cols="*", limit=limit, filters=filters, order=("created_at", False))
    return {"ok": True, "data": (df.to_dict(orient="records") if not df.empty else []), "meta": {"source": rel, "generated_at": _utc_now()}}


def tool_member_summary(sb_anon, sb_service, schema: str, member_id: str) -> Dict[str, Any]:
    """
    Member intelligence summary (DB-grounded).
    Uses tables only (safe + generic) and tolerates different column names.
    """
    members = sb_select(sb_anon, sb_service, schema, "members", cols="*", limit=1, filters=[("id", "eq", member_id)])
    mname = ""
    if not members.empty:
        name_col = _pick_col(members, ["display_name", "full_name", "name"])
        mname = _clean(members.iloc[0].get(name_col)) if name_col else ""

    contributions = sb_select(
        sb_anon, sb_service, schema, "contributions", cols="amount", limit=200000, filters=[("member_id", "eq", member_id)]
    )
    foundation = sb_select(
        sb_anon,
        sb_service,
        schema,
        "foundation_contributions",
        cols="amount",
        limit=200000,
        filters=[("member_id", "eq", member_id)],
    )
    fines = sb_select(sb_anon, sb_service, schema, "fines", cols="amount", limit=200000, filters=[("member_id", "eq", member_id)])
    loans = sb_select(
        sb_anon,
        sb_service,
        schema,
        "loans",
        cols="status,principal_current,principal,unpaid_interest,total_due,created_at",
        limit=200000,
        filters=[("member_id", "eq", member_id)],
    )

    c_total = float(pd.to_numeric(contributions.get("amount"), errors="coerce").fillna(0).sum()) if "amount" in contributions.columns else 0.0
    f_total = float(pd.to_numeric(foundation.get("amount"), errors="coerce").fillna(0).sum()) if "amount" in foundation.columns else 0.0
    fines_total = float(pd.to_numeric(fines.get("amount"), errors="coerce").fillna(0).sum()) if "amount" in fines.columns else 0.0

    active_bal = 0.0
    unpaid_int = 0.0
    active_count = 0
    overdue_count = 0

    if not loans.empty:
        status_col = _pick_col(loans, ["status"])
        bal_col = _pick_col(loans, ["principal_current", "principal", "total_due"])
        unpaid_col = _pick_col(loans, ["unpaid_interest"])

        st_series = loans[status_col].astype(str).str.lower().fillna("") if status_col else pd.Series([""] * len(loans))
        active_mask = st_series.isin({"active", "open", "overdue", "late", "running", "ongoing", "disbursed"})
        overdue_mask = st_series.isin({"overdue", "late"})

        active_df = loans[active_mask] if len(loans) else loans
        overdue_df = loans[overdue_mask] if len(loans) else loans.iloc[0:0]

        if bal_col:
            active_bal = float(pd.to_numeric(active_df[bal_col], errors="coerce").fillna(0).sum())
        if unpaid_col:
            unpaid_int = float(pd.to_numeric(active_df[unpaid_col], errors="coerce").fillna(0).sum())

        active_count = int(len(active_df))
        overdue_count = int(len(overdue_df))

    exposure_ratio = (active_bal / c_total) if c_total > 0 else None
    # simple conservative grade
    risk_grade = "A" if (active_bal <= 0 and unpaid_int <= 0) else ("B" if unpaid_int <= 0 else "C")

    return {
        "ok": True,
        "data": {
            "member": {"member_id": str(member_id), "member_name": mname or "(unknown)"},
            "totals": {
                "contributions_total": c_total,
                "foundation_total": f_total,
                "fines_total": fines_total,
                "active_loan_balance": active_bal,
                "active_unpaid_interest": unpaid_int,
                "active_loan_count": active_count,
                "overdue_loan_count": overdue_count,
            },
            "derived": {
                "exposure_to_contributions_ratio": exposure_ratio,
                "risk_grade": risk_grade,
            },
        },
        "meta": {"generated_at": _utc_now()},
    }


def tool_finance_kpis(sb_anon, sb_service, schema: str, limit: int = 200) -> Dict[str, Any]:
    rel = "v_finance_kpis" if "v_finance_kpis" in RELATIONS else None
    if rel is None:
        return {"ok": False, "error": "v_finance_kpis not available", "data": []}
    df = sb_select(sb_anon, sb_service, schema, rel, cols="*", limit=limit)
    return {"ok": True, "data": (df.to_dict(orient="records") if not df.empty else []), "meta": {"source": rel, "generated_at": _utc_now()}}


# =============================================================================
# 4) Lazy tool mapping (prevents circular import crashes)
# =============================================================================
ToolFn = Callable[..., Any]


def _build_read_tools_internal() -> Dict[str, ToolFn]:
    """Build tools WITHOUT importing other app modules. Safe to call at runtime."""
    return {
        "tables": tool_tables,
        "members": tool_members,
        "show_table": tool_show_table,
        "describe_table": tool_describe_table,
        "loans": tool_loans,
        "member_summary": tool_member_summary,
        "finance_kpis": tool_finance_kpis,
    }


def _build_write_tools_internal() -> Dict[str, ToolFn]:
    # Add write tools later (insert/update) using service key.
    return {}


class _LazyToolMap(Mapping[str, ToolFn]):
    """Mapping that initializes tools only when accessed."""

    def __init__(self, builder: Callable[[], Dict[str, ToolFn]]):
        self._builder = builder
        self._tools: Optional[Dict[str, ToolFn]] = None

    def _ensure(self) -> Dict[str, ToolFn]:
        if self._tools is None:
            self._tools = self._builder() or {}
        return self._tools

    def __getitem__(self, key: str) -> ToolFn:
        return self._ensure()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._ensure())

    def __len__(self) -> int:
        return len(self._ensure())

    def keys(self) -> Iterable[str]:
        return self._ensure().keys()

    def items(self) -> Iterable[Tuple[str, ToolFn]]:
        return self._ensure().items()

    def values(self) -> Iterable[ToolFn]:
        return self._ensure().values()

    def get(self, key: str, default: Optional[ToolFn] = None) -> Optional[ToolFn]:
        return self._ensure().get(key, default)


# ✅ EXPORTS (safe)
READ_TOOLS: Mapping[str, ToolFn] = _LazyToolMap(_build_read_tools_internal)
WRITE_TOOLS: Mapping[str, ToolFn] = _LazyToolMap(_build_write_tools_internal)

# ✅ IMPORTANT: provides ALL_TOOLS so nothing crashes if imported
ALL_TOOLS: Mapping[str, ToolFn] = _LazyToolMap(lambda: {**dict(READ_TOOLS.items()), **dict(WRITE_TOOLS.items())})


def get_read_tools() -> Dict[str, ToolFn]:
    return dict(READ_TOOLS.items())


def get_write_tools() -> Dict[str, ToolFn]:
    return dict(WRITE_TOOLS.items())


def get_all_tools() -> Dict[str, ToolFn]:
    return {**get_read_tools(), **get_write_tools()}


def execute_tool(tool_name: str, *args, **kwargs) -> Any:
    """
    Utility executor:
      execute_tool("members", sb_anon, sb_service, schema)
    """
    fn = READ_TOOLS.get(tool_name) or WRITE_TOOLS.get(tool_name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool: {tool_name}", "data": []}
    return fn(*args, **kwargs)
