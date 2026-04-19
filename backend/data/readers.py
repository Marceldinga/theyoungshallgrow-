from __future__ import annotations

from typing import Any, List, Optional, Tuple

import pandas as pd

from backend.data.guards import relation_guard


FilterSpec = Tuple[str, str, Any]
OrderSpec = Tuple[str, bool]


def sb_select(
    sb_anon,
    sb_service,
    schema: str,
    relation: str,
    cols: str = "*",
    limit: int = 2000,
    filters: Optional[List[FilterSpec]] = None,
    order: Optional[OrderSpec] = None,
) -> pd.DataFrame:
    relation_guard(relation)

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
                    q = q.in_(col, val)
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
        except Exception:
            return pd.DataFrame()


def rpc_finance_snapshot(sb_anon, sb_service, schema: str) -> dict:
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
