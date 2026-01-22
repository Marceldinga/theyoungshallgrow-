# db.py
# ============================================================
# Database helpers for Njangi system (Railway + Streamlit Cloud safe)
# - Secrets handling
# - Canonical Supabase clients (public + service)
# - Safe loaders (NO streamlit UI calls here)
# - Canonical session UUID + session-scoped pot helpers
# ============================================================

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple, Optional

import pandas as pd
from supabase import create_client
from datetime import datetime, timezone

# If Railway injects these, it can break DNS/resolution flows if your app accidentally uses Postgres.
POSTGRES_ENV_VARS = ["DATABASE_URL", "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"]


# ============================================================
# TIME HELPERS
# ============================================================
def now_iso() -> str:
    """UTC ISO timestamp with Z suffix."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# ============================================================
# SECRETS
# ============================================================
def get_secret(name: str, default: str | None = None) -> str | None:
    """
    Railway-safe secret getter:
    - Prefer environment variables (Railway, Docker, prod)
    - Fallback to Streamlit secrets (Streamlit Cloud / local)
    """
    v = os.getenv(name)
    if v is not None and str(v).strip() != "":
        return str(v)

    try:
        import streamlit as st  # local import to avoid hard dependency
        if name in st.secrets and str(st.secrets.get(name, "")).strip() != "":
            return str(st.secrets[name])
    except Exception:
        pass

    return default


# ============================================================
# CANONICAL CLIENTS (single source of truth)
# ============================================================
def _validate_supabase_env(url: str | None, key: str | None) -> tuple[str, str]:
    if not url or not str(url).strip():
        raise RuntimeError("Missing SUPABASE_URL. Set it in Railway Variables.")
    if not key or not str(key).strip():
        raise RuntimeError("Missing SUPABASE_ANON_KEY (or SUPABASE_SERVICE_KEY). Set it in Railway Variables.")

    url = str(url).strip()
    key = str(key).strip()

    if not url.startswith("https://"):
        raise RuntimeError(f"SUPABASE_URL must start with https:// (got {url!r}).")

    # HARD BLOCK: if Postgres vars exist, fail with a clear instruction.
    bad = [k for k in POSTGRES_ENV_VARS if os.getenv(k)]
    if bad:
        raise RuntimeError(
            "Forbidden Postgres env vars detected: "
            + ", ".join(bad)
            + ". Delete them from Railway Variables (including the auto-added ones)."
        )

    return url, key


def get_schema() -> str:
    return str(get_secret("SUPABASE_SCHEMA", "public") or "public")


def get_public_client():
    """
    Public (anon) Supabase client.
    - Used for dashboard + read-only views
    - RLS enforced
    """
    url = get_secret("SUPABASE_URL")
    anon = get_secret("SUPABASE_ANON_KEY")
    url, anon = _validate_supabase_env(url, anon)
    return create_client(url, anon)


def get_service_client():
    """
    Service-role Supabase client (admin/write).
    - Bypasses RLS
    - Use ONLY for admin/server-side operations
    """
    url = get_secret("SUPABASE_URL")
    sk = get_secret("SUPABASE_SERVICE_KEY")
    if not sk or not str(sk).strip():
        return None
    url, sk = _validate_supabase_env(url, sk)
    return create_client(url, sk)


def authed_client(url: str, anon_key: str, session_obj: Any):
    """
    Authenticated client using a user session (access token).
    Useful if you later add per-user auth.
    """
    url, anon_key = _validate_supabase_env(url, anon_key)
    sb = create_client(url, anon_key)

    token: Optional[str] = None
    if isinstance(session_obj, str):
        token = session_obj
    elif isinstance(session_obj, dict):
        token = session_obj.get("access_token") or session_obj.get("accessToken")
    else:
        token = getattr(session_obj, "access_token", None) or getattr(session_obj, "accessToken", None)

    if not token:
        raise ValueError("Missing access_token in session_obj. Cannot create authed client.")

    sb.auth.set_session(token, None)
    return sb


# ============================================================
# INTERNAL SAFE EXECUTE
# ============================================================
def _safe_execute(resp: Any) -> List[Dict[str, Any]]:
    """
    Normalizes Supabase responses into list[dict].
    Handles:
      - list
      - dict with 'data'
      - object with .data
    """
    if resp is None:
        return []
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        data = resp.get("data")
        return data if isinstance(data, list) else []
    data = getattr(resp, "data", None)
    return data if isinstance(data, list) else []


# ============================================================
# SIMPLE DB HELPERS
# ============================================================
def fetch_one(resp: Any) -> Dict[str, Any]:
    """Return first row from a Supabase response or {}."""
    rows = _safe_execute(resp)
    return rows[0] if rows else {}


def _looks_like_uuid(s: Any) -> bool:
    try:
        t = str(s)
    except Exception:
        return False
    if len(t) != 36:
        return False
    parts = t.split("-")
    return len(parts) == 5 and all(parts)


# ============================================================
# CANONICAL STATE HELPERS
# ============================================================
def get_app_state(c) -> Dict[str, Any]:
    """Returns the singleton app_state row (first row). Safe fallback to {}."""
    try:
        rows = _safe_execute(
            c.schema(get_schema()).table("app_state").select("*").limit(1).execute()
        )
        return rows[0] if rows else {}
    except Exception:
        return {}


def current_payout_index(c) -> int | None:
    """
    Returns current rotation pointer as an integer (business index).
    This is NOT the UUID session_id. Use current_session_uuid() for that.
    """
    schema = get_schema()
    try:
        rows = _safe_execute(
            c.schema(schema).table("app_state").select("next_payout_index").limit(1).execute()
        )
        if rows and rows[0].get("next_payout_index") is not None:
            return int(rows[0]["next_payout_index"])
    except Exception:
        pass
    return None


def current_session_uuid(c) -> str | None:
    """
    ✅ Single source of truth for the current cycle/session UUID.

    Priority:
    1) app_state.current_session_id (if it is a UUID)
    2) app_state.next_payout_index -> lookup sessions_legacy by payout_index (if present)
    3) sessions_legacy latest row -> try UUID-like keys
    """
    schema = get_schema()

    # 1) app_state.current_session_id if it's already a UUID
    try:
        rows = _safe_execute(
            c.schema(schema).table("app_state").select("current_session_id,next_payout_index").limit(1).execute()
        )
        if rows:
            csid = rows[0].get("current_session_id")
            if csid and _looks_like_uuid(csid):
                return str(csid)

            # 2) use next_payout_index to resolve UUID from sessions_legacy
            npi = rows[0].get("next_payout_index")
            if npi is not None:
                try:
                    npi_int = int(npi)
                except Exception:
                    npi_int = None

                if npi_int is not None:
                    # Try a few select variants to handle schema differences
                    select_variants = [
                        "id,payout_index,created_at",
                        "session_id,payout_index,created_at",
                        "id,next_payout_index,created_at",
                        "session_id,next_payout_index,created_at",
                        "*",
                    ]
                    for sel in select_variants:
                        try:
                            r = _safe_execute(
                                c.schema(schema)
                                .table("sessions_legacy")
                                .select(sel)
                                .eq("payout_index", npi_int)
                                .limit(1)
                                .execute()
                            )
                            if not r:
                                # sometimes the column is named next_payout_index
                                r = _safe_execute(
                                    c.schema(schema)
                                    .table("sessions_legacy")
                                    .select(sel)
                                    .eq("next_payout_index", npi_int)
                                    .limit(1)
                                    .execute()
                                )
                            if r:
                                row = r[0]
                                for k in ("id", "session_id", "current_session_id", "season_id", "legacy_session_id"):
                                    v = row.get(k)
                                    if v and _looks_like_uuid(v):
                                        return str(v)
                        except Exception:
                            continue
    except Exception:
        pass

    # 3) sessions_legacy latest fallback
    for order_col in ("created_at", "id", "session_id"):
        try:
            rows = _safe_execute(
                c.schema(schema)
                .table("sessions_legacy")
                .select("*")
                .order(order_col, desc=True)
                .limit(1)
                .execute()
            )
            if rows:
                for k in ("id", "session_id", "season_id", "legacy_session_id"):
                    v = rows[0].get(k)
                    if v and _looks_like_uuid(v):
                        return str(v)
        except Exception:
            continue

    return None


# Backward-compatible name (so existing imports don't break)
def current_session_id(c) -> str | None:
    """
    Backward-compatible alias.
    ✅ Returns the CURRENT SESSION UUID (not the integer payout index).
    """
    return current_session_uuid(c)


# ============================================================
# CONTRIBUTION HELPERS ✅ (single source of truth for pot)
# ============================================================
def pot_for_session(c, session_uuid: str) -> float:
    """
    Returns total contribution pot for a given session UUID.
    Use this everywhere (Dashboard + Payouts) to avoid mismatches.
    """
    resp = (
        c.schema(get_schema())
        .table("contributions_legacy")
        .select("amount")
        .eq("session_id", session_uuid)
        .execute()
    )
    rows = resp.data or []
    return float(sum(float(r.get("amount") or 0) for r in rows))


# ============================================================
# MEMBERS LOADER (members_legacy) - supports id OR legacy_member_id
# ============================================================
def load_members_legacy(c) -> Tuple[List[str], Dict[str, int], Dict[str, str], pd.DataFrame]:
    """
    Loads members_legacy and returns:
      labels: list[str]              -> for selectbox
      label_to_id: dict[label -> id]
      label_to_name: dict[label -> name]
      df_members: pd.DataFrame
    """
    schema = get_schema()

    select_variants = [
        "legacy_member_id,name,position,phone,has_benefits,contributed,foundation_contrib,loan_due,payout_total,total_fines_accumulated",
        "id,name,position,phone,has_benefits,contributed,foundation_contrib,loan_due,payout_total,total_fines_accumulated",
    ]

    rows: List[Dict[str, Any]] = []
    key_col: str | None = None

    for sel in select_variants:
        try:
            tmp = _safe_execute(
                c.schema(schema).table("members_legacy").select(sel).execute()
            )
            if tmp:
                rows = tmp
                key_col = "legacy_member_id" if "legacy_member_id" in tmp[0] else "id"
                break
            else:
                rows = tmp
                key_col = "legacy_member_id" if "legacy_member_id" in sel else "id"
                break
        except Exception:
            continue

    df = pd.DataFrame(rows) if rows else pd.DataFrame()

    if df.empty:
        df = pd.DataFrame(columns=[key_col or "id", "name"])
        return [], {}, {}, df

    if "name" not in df.columns:
        df["name"] = ""

    if "legacy_member_id" in df.columns:
        key_col = "legacy_member_id"
    elif "id" in df.columns:
        key_col = "id"
    else:
        key_col = df.columns[0]

    try:
        df[key_col] = pd.to_numeric(df[key_col], errors="coerce")
        df = df.dropna(subset=[key_col]).copy()
        df[key_col] = df[key_col].astype(int)
    except Exception:
        pass

    df["name"] = df["name"].astype(str)
    df["label"] = df.apply(lambda r: f'{int(r[key_col]):02d} • {r["name"]}', axis=1)

    labels = df["label"].tolist()
    label_to_id = dict(zip(df["label"], df[key_col].astype(int)))
    label_to_name = dict(zip(df["label"], df["name"]))

    try:
        df = df.sort_values(by=key_col, ascending=True).reset_index(drop=True)
    except Exception:
        pass

    return labels, label_to_id, label_to_name, df
