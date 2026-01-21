
# db.py
# ============================================================
# Database helpers for Njangi system (Railway + Streamlit Cloud safe)
# - Secrets handling
# - Canonical Supabase clients (public + service)
# - Safe loaders (NO streamlit UI calls here)
# ============================================================

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple, Optional

import pandas as pd
from supabase import create_client

# If Railway injects these, it can break DNS/resolution flows if your app accidentally uses Postgres.
POSTGRES_ENV_VARS = ["DATABASE_URL", "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"]


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
    # (Railway sometimes injects DB vars automatically; we do not want any Postgres usage.)
    bad = [k for k in POSTGRES_ENV_VARS if os.getenv(k)]
    if bad:
        raise RuntimeError(
            "Forbidden Postgres env vars detected: "
            + ", ".join(bad)
            + ". Delete them from Railway Variables (including the auto-added ones)."
        )

    return url, key


def get_schema() -> str:
    # You can set SUPABASE_SCHEMA if needed; default is public.
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
        return None  # app can run without service key (read-only)
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
# CANONICAL STATE HELPERS
# ============================================================
def current_session_id(c) -> str | None:
    """
    Returns the current Njangi session/season identifier.
    Tries multiple safe fallbacks.
    """
    try:
        rows = _safe_execute(
            c.schema(get_schema()).table("app_state").select("current_session_id").limit(1).execute()
        )
        if rows and rows[0].get("current_session_id") is not None:
            return str(rows[0]["current_session_id"])
    except Exception:
        pass

    for order_col in ("created_at", "id", "session_id"):
        try:
            rows = _safe_execute(
                c.schema(get_schema())
                .table("sessions_legacy")
                .select("*")
                .order(order_col, desc=True)
                .limit(1)
                .execute()
            )
            if rows:
                for k in ("session_id", "id", "season_id", "legacy_session_id"):
                    if rows[0].get(k) is not None:
                        return str(rows[0][k])
        except Exception:
            continue

    return None


def get_app_state(c) -> Dict[str, Any]:
    """
    Returns the singleton app_state row (first row).
    Safe fallback to {}.
    """
    try:
        rows = _safe_execute(
            c.schema(get_schema()).table("app_state").select("*").limit(1).execute()
        )
        return rows[0] if rows else {}
    except Exception:
        return {}


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

    # Try both possible key column names
    select_variants = [
        # legacy_member_id version
        "legacy_member_id,name,position,phone,has_benefits,contributed,foundation_contrib,loan_due,payout_total,total_fines_accumulated",
        # id version
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
                # Could still be valid but empty; detect column by trying again with limit 1 + select key only
                # We’ll decide later; continue.
                rows = tmp
                key_col = "legacy_member_id" if "legacy_member_id" in sel else "id"
                break
        except Exception:
            continue

    df = pd.DataFrame(rows) if rows else pd.DataFrame()

    # Ensure df has a key column and name column
    if df.empty:
        # Create predictable empty frame
        df = pd.DataFrame(columns=[key_col or "id", "name"])
        return [], {}, {}, df

    if "name" not in df.columns:
        df["name"] = ""

    # Determine key column robustly
    if "legacy_member_id" in df.columns:
        key_col = "legacy_member_id"
    elif "id" in df.columns:
        key_col = "id"
    else:
        # fallback: first column
        key_col = df.columns[0]

    # Sort safely
    try:
        df[key_col] = pd.to_numeric(df[key_col], errors="coerce")
        df = df.dropna(subset=[key_col]).copy()
        df[key_col] = df[key_col].astype(int)
    except Exception:
        pass

    df["name"] = df["name"].astype(str)

    # Create labels
    df["label"] = df.apply(lambda r: f'{int(r[key_col]):02d} • {r["name"]}', axis=1)

    labels = df["label"].tolist()
    label_to_id = dict(zip(df["label"], df[key_col].astype(int)))
    label_to_name = dict(zip(df["label"], df["name"]))

    # Order by key
    try:
        df = df.sort_values(by=key_col, ascending=True).reset_index(drop=True)
    except Exception:
        pass

    return labels, label_to_id, label_to_name, df
