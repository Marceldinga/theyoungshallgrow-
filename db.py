
# db.py ✅ NJANGI STANDARD (NO "legacy") — COMPLETE UPDATED
# ============================================================
# Database helpers for Njangi system (Railway + Streamlit Cloud safe)
# - Secrets handling
# - Canonical Supabase clients (public + service)
# - Safe loaders (NO streamlit UI calls here)
# - Canonical app_state helpers (integer sessions + 17-member rotation)
# - Canonical pot helpers (contributions by session_id)
#
# ✅ Uses NEW tables only:
#   - app_state (id=1, current_session_id INT, next_member_id INT, optional next_payout_date)
#   - sessions  (session_id OR id INT, start_date, end_date)
#   - members   (id INT 1..17, name)
#   - contributions (member_id, session_id, amount, paid_at, created_at)

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
# CANONICAL CLIENTS
# ============================================================
def _validate_supabase_env(url: str | None, key: str | None) -> tuple[str, str]:
    if not url or not str(url).strip():
        raise RuntimeError("Missing SUPABASE_URL. Set it in Railway Variables.")
    if not key or not str(key).strip():
        raise RuntimeError("Missing Supabase key. Set SUPABASE_ANON_KEY / SUPABASE_SERVICE_KEY.")

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
            + ". Delete them from Railway Variables (including auto-added ones)."
        )

    return url, key


def get_schema() -> str:
    return str(get_secret("SUPABASE_SCHEMA", "public") or "public")


def get_public_client():
    """Anon client (read-only; RLS enforced if you enable it later)."""
    url = get_secret("SUPABASE_URL")
    anon = get_secret("SUPABASE_ANON_KEY")
    url, anon = _validate_supabase_env(url, anon)
    return create_client(url, anon)


def get_service_client():
    """Service-role client (admin/write). Returns None if missing."""
    url = get_secret("SUPABASE_URL")
    sk = get_secret("SUPABASE_SERVICE_KEY") or get_secret("SUPABASE_SERVICE_ROLE_KEY")
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


def fetch_one(resp: Any) -> Dict[str, Any]:
    """Return first row from a Supabase response or {}."""
    rows = _safe_execute(resp)
    return rows[0] if rows else {}


# ============================================================
# TABLE INTROSPECTION
# ============================================================
def table_exists(c, table: str) -> bool:
    try:
        c.schema(get_schema()).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def sessions_pk_col(c) -> str:
    """
    sessions table may use (session_id) or (id). Return the correct PK column name.
    """
    schema = get_schema()
    try:
        rows = _safe_execute(c.schema(schema).table("sessions").select("*").limit(1).execute())
        if rows:
            return "session_id" if "session_id" in rows[0] else ("id" if "id" in rows[0] else "session_id")
    except Exception:
        pass
    return "session_id"


# ============================================================
# CANONICAL STATE HELPERS (NJANGI STANDARD)
# ============================================================
def get_app_state(c) -> Dict[str, Any]:
    """Returns the singleton app_state row (id=1)."""
    schema = get_schema()
    try:
        rows = _safe_execute(
            c.schema(schema).table("app_state").select("*").eq("id", 1).limit(1).execute()
        )
        return rows[0] if rows else {}
    except Exception:
        return {}


def ensure_app_state(c) -> Dict[str, Any]:
    """
    Ensures app_state has id=1 row with:
      - current_session_id (int or None)
      - next_member_id (int 1..17)
      - updated_at
    """
    schema = get_schema()
    state = get_app_state(c)
    if state and any(v is not None for v in state.values()):
        return state

    payload = {"id": 1, "current_session_id": None, "next_member_id": 1, "updated_at": now_iso()}
    try:
        c.schema(schema).table("app_state").upsert(payload, on_conflict="id").execute()
    except Exception:
        try:
            c.schema(schema).table("app_state").insert(payload).execute()
        except Exception:
            pass
    return get_app_state(c)


def current_session_id(c) -> int | None:
    """
    ✅ Single source of truth for current session (integer).
    Reads app_state.id=1.current_session_id
    """
    stt = ensure_app_state(c)
    v = stt.get("current_session_id")
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except Exception:
        return None


def next_member_id(c) -> int:
    """
    ✅ Single source of truth for next beneficiary member_id (1..17).
    Reads app_state.id=1.next_member_id
    """
    stt = ensure_app_state(c)
    v = stt.get("next_member_id")
    try:
        x = int(v) if v is not None else 1
        return x if 1 <= x <= 17 else 1
    except Exception:
        return 1


# Backward-compatible alias (some files may import this name)
def current_payout_index(c) -> int | None:
    """
    Backward-compatible alias for rotation pointer.
    In Njangi standard, this is the beneficiary position which equals next_member_id.
    """
    try:
        return int(next_member_id(c))
    except Exception:
        return None


# ============================================================
# POT HELPERS (CONTRIBUTIONS)
# ============================================================
def pot_for_session(c, session_id: int) -> float:
    """
    Returns total contribution pot for a given integer session_id.
    Uses contributions.amount where contributions.session_id == session_id
    """
    schema = get_schema()
    try:
        resp = (
            c.schema(schema)
            .table("contributions")
            .select("amount,session_id")
            .eq("session_id", int(session_id))
            .limit(20000)
            .execute()
        )
        rows = resp.data or []
        return float(sum(float(r.get("amount") or 0) for r in rows))
    except Exception:
        return 0.0


def pot_for_current_session(c) -> float:
    sid = current_session_id(c)
    return pot_for_session(c, int(sid)) if sid is not None else 0.0


# ============================================================
# MEMBERS LOADER (NEW)
# ============================================================
def load_members(c) -> Tuple[List[str], Dict[str, int], Dict[str, str], pd.DataFrame]:
    """
    Loads members and returns:
      labels: list[str]              -> for selectbox
      label_to_id: dict[label -> id]
      label_to_name: dict[label -> name]
      df_members: pd.DataFrame (id,name,position,label)
    """
    schema = get_schema()
    try:
        rows = _safe_execute(
            c.schema(schema).table("members").select("id,name").order("id", desc=False).limit(5000).execute()
        )
    except Exception:
        rows = []

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["id", "name"])
    if df.empty:
        return [], {}, {}, df

    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    df = df[df["id"] > 0].copy()
    df["name"] = df["name"].astype(str)
    df["position"] = df["id"]  # Njangi standard: position == id
    df["label"] = df.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)

    labels = df["label"].tolist()
    label_to_id = dict(zip(df["label"], df["id"].astype(int)))
    label_to_name = dict(zip(df["label"], df["name"]))

    df = df.sort_values("id", ascending=True).reset_index(drop=True)
    return labels, label_to_id, label_to_name, df


# ============================================================
# SESSIONS LOADER (NEW)
# ============================================================
def load_sessions(c) -> pd.DataFrame:
    """
    Loads sessions table and returns DataFrame with standardized columns:
      session_id, start_date, end_date
    """
    schema = get_schema()
    if not table_exists(c, "sessions"):
        return pd.DataFrame(columns=["session_id", "start_date", "end_date"])

    pk = sessions_pk_col(c)
    try:
        rows = _safe_execute(
            c.schema(schema)
            .table("sessions")
            .select(f"{pk},start_date,end_date,created_at")
            .order(pk, desc=False)
            .limit(5000)
            .execute()
        )
    except Exception:
        rows = []

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=[pk, "start_date", "end_date"])
    if df.empty:
        return pd.DataFrame(columns=["session_id", "start_date", "end_date"])

    df[pk] = pd.to_numeric(df[pk], errors="coerce").fillna(0).astype(int)
    df = df[df[pk] > 0].copy()
    df = df.rename(columns={pk: "session_id"})
    return df
