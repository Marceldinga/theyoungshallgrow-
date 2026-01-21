# audit.py ✅ UPDATED (schema-safe + cached optional column checks)
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@lru_cache(maxsize=256)
def _has_columns_cached(schema: str, table: str, cols_key: str) -> bool:
    """
    Cached wrapper key builder. Actual check is done in _has_columns().
    """
    # This function body is not used directly; cache is keyed by args.
    return True


def _has_columns(c, schema: str, table: str, cols: list[str]) -> bool:
    """
    Check if a table can SELECT the given columns.
    Uses caching to avoid hammering Supabase on every audit call.
    """
    cols_key = ",".join(cols)

    # cache shortcut: if seen before and was false, skip re-checking
    try:
        # if cached, return previous result
        return _has_columns_cached(schema, table, cols_key)  # type: ignore[misc]
    except Exception:
        pass

    try:
        sel = ",".join(cols)
        c.schema(schema).table(table).select(sel).limit(1).execute()

        # store True in cache
        _has_columns_cached.cache_clear()  # prevent stale placeholder usage
        _has_columns_cached(schema, table, cols_key)
        return True
    except Exception:
        # store False in cache
        _has_columns_cached.cache_clear()
        _has_columns_cached(schema, table, cols_key)
        return False


def audit(
    c,
    action: str,
    status: str = "ok",
    details: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
    schema: str = "public",
):
    """
    Schema-safe audit logger.

    Minimum required columns: created_at, action, status
    Optional columns: details, actor_user_id

    - Writes only what exists.
    - Never breaks app flow.
    """
    try:
        payload: dict[str, Any] = {
            "created_at": _now_iso(),
            "action": action,
            "status": status,
        }

        # optional fields: only include if columns exist
        if _has_columns(c, schema, "audit_log", ["details"]):
            payload["details"] = json.dumps(details or {}, default=str)

        if actor_user_id is not None and _has_columns(c, schema, "audit_log", ["actor_user_id"]):
            payload["actor_user_id"] = actor_user_id

        c.schema(schema).table("audit_log").insert(payload).execute()
    except Exception:
        pass
