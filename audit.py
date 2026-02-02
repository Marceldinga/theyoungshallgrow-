
# audit.py ✅ UPDATED (schema-safe + REAL cached optional column checks)
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Iterable, Tuple


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cols_key(cols: Iterable[str]) -> Tuple[str, ...]:
    # stable cache key (sorted, unique)
    return tuple(sorted({c.strip() for c in cols if c and str(c).strip()}))


@lru_cache(maxsize=512)
def _columns_probe_cached(schema: str, table: str, cols_key: Tuple[str, ...]) -> bool:
    """
    Cached result store for column-probe checks.

    IMPORTANT:
    - This function MUST be called via _has_columns(...), because the actual probe
      requires the Supabase client and cannot be done inside an lru_cache directly.
    - We use this as a cache storage layer (True/False) keyed by (schema, table, cols).
    """
    # This body is never used for probing; it is only a cache "store".
    # If not present in cache, lru_cache would call this, so we should NOT use it that way.
    # We'll never call it without first setting the value via _set_columns_probe_cache().
    return False


def _set_columns_probe_cache(schema: str, table: str, cols_key: Tuple[str, ...], value: bool) -> None:
    """
    Store a True/False value into the lru_cache.

    Trick: we call the cached function by temporarily monkey-returning `value`
    using a wrapper approach: simplest is to just bypass and use a second cache.
    But since we can't mutate lru_cache directly, we implement a small shadow dict.
    """
    # Use a shadow dict for real cache storage (simple & reliable).
    _shadow_cache[(schema, table, cols_key)] = value


# Real cache storage (reliable, mutable)
_shadow_cache: dict[tuple[str, str, Tuple[str, ...]], bool] = {}


def _has_columns(c, schema: str, table: str, cols: list[str]) -> bool:
    """
    Check if a table can SELECT the given columns.

    - Caches the result per (schema, table, cols_set)
    - If Supabase rejects the select (missing column or permission), caches False.
    - If it succeeds, caches True.

    This avoids hammering Supabase for every audit() call.
    """
    key = (schema, table, _cols_key(cols))

    # cache hit
    if key in _shadow_cache:
        return _shadow_cache[key]

    # probe (no raw SQL)
    try:
        sel = ",".join(key[2])
        if not sel:
            _shadow_cache[key] = False
            return False

        c.schema(schema).table(table).select(sel).limit(1).execute()
        _shadow_cache[key] = True
        return True
    except Exception:
        _shadow_cache[key] = False
        return False


def audit(
    c,
    action: str,
    status: str = "ok",
    details: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
    schema: str = "public",
) -> None:
    """
    Schema-safe audit logger.

    Minimum required columns: created_at, action, status
    Optional columns: details (json/text), actor_user_id

    - Writes only what exists.
    - Never breaks app flow.
    """
    try:
        # minimum payload
        payload: dict[str, Any] = {
            "created_at": _now_iso(),
            "action": action,
            "status": status,
        }

        # Optional: details
        if _has_columns(c, schema, "audit_log", ["details"]):
            payload["details"] = json.dumps(details or {}, default=str)

        # Optional: actor_user_id
        if actor_user_id is not None and _has_columns(c, schema, "audit_log", ["actor_user_id"]):
            payload["actor_user_id"] = actor_user_id

        # Insert
        c.schema(schema).table("audit_log").insert(payload).execute()

    except Exception:
        # audit must never break the app
        pass


def audit_cache_clear() -> None:
    """
    Optional helper: call this if you just migrated audit_log columns
    and want the app to re-probe columns without redeploy.
    """
    _shadow_cache.clear() 
