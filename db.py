
# db.py
# ============================================================
# Database helpers for Njangi system
# - Secrets handling (Railway / Streamlit Cloud / local)
# - Supabase client factories
# - Safe data loaders (NO Streamlit UI calls here)
# - Canonical helpers reused across app.py, loans.py, payout.py
# ============================================================

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple, Optional

import pandas as pd
from supabase import create_client


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
        return v

    try:
        import streamlit as st  # local import to avoid hard dependency

        if name in st.secrets and str(st.secrets.get(name, "")).strip() != "":
            return str(st.secrets[name])
    except Exception:
        pass

    return default


# ============================================================
# CLIENT FACTORIES
# ============================================================
def public_client(url: str, anon_key: str):
    """
    Public (anon) Supabase client.
    - Used for dashboard + read-only views
    - RLS enforced
    """
    return create_client(url.strip(), anon_key.strip())


def service_client(url: str, service_key: str):
    """
    Service-role Supabase client.
    - Bypasses RLS
    - Use ONLY for admin / server-side operations
    """
    return create_client(url.strip(), service_key.strip())


def authed_client(url: str, anon_key: str, session_obj: Any):
    """
    Authenticated client using a user session (access token).
    Useful if you later add per-user auth.
    """
    sb = create_client(url.strip(), anon_key.strip())

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
    # 1) app_state.current_session_id
    try:
        rows = _safe_execute(
            c.table("app_state")
            .select("current_session_id")
            .limit(1)
            .execute()
        )
        if rows and rows[0].get("current_session_id") is not None:
            return str(rows[0]["current_session_id"])
    except Exception:
        pass

    # 2) sessions_legacy: latest row
    for order_col in ("created_at", "id", "session_id"):
        try:
            rows = _safe_execute(
                c.table("sessions_legacy")
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
    Returns the singleton app_state row (id=1 by convention).
    Safe fallback to {}.
    """
    try:
        rows = _safe_execute(
            c.table("app_state")
            .select("*")
            .limit(1)
            .execute()
        )
        return rows[0] if rows else {}
    except Exception:
        return {}


# ============================================================
# MEMBERS LOADER (members_legacy)
# ============================================================
def load_members_legacy(c) -> Tuple[List[str], Dict[str, int], Dict[str, str], pd.DataFrame]:
    """
    Loads public.members_legacy and returns:
      labels: list[str]              -> for selectbox
      label_to_id: dict[label -> id]
      label_to_name: dict[label -> name]
      df_members: pd.DataFrame

    Expected / known columns (superset-safe):
      - id (bigint)
      - name (text)
      - position
      - phone
      - has_benefits
      - contributed
      - foundation_contrib
      - loan_due
      - payout_total
      - total_fines_accumulated
    """
    try:
        rows = _safe_execute(
            c.table("members_legacy")
            .select(
                "id,name,position,phone,has_benefits,contributed,"
                "foundation_contrib,loan_due,payout_total,total_fines_accumulated"
            )
            .order("id", desc=False)
            .execute()
        )
    except Exception:
        rows = []

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["id", "name"])

    labels: List[str] = []
    label_to_id: Dict[str, int] = {}
    label_to_name: Dict[str, str] = {}

    if not df.empty and "id" in df.columns and "name" in df.columns:
        df["id"] = pd.to_numeric(df["id"], errors="coerce")
        df = df.dropna(subset=["id"]).copy()
        df["id"] = df["id"].astype(int)
        df["name"] = df["name"].astype(str)

        df["label"] = df.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)

        labels = df["label"].tolist()
        label_to_id = dict(zip(df["label"], df["id"]))
        label_to_name = dict(zip(df["label"], df["name"]))

    return labels, label_to_id, label_to_name, df
