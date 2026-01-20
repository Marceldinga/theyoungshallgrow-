
# db.py  (Railway-safe helpers for Supabase + Streamlit; no cached funcs here)
from __future__ import annotations

import os
from typing import Any, Dict, Tuple, List, Optional

import pandas as pd
from supabase import create_client


# ============================================================
# SECRETS
# ============================================================
def get_secret(name: str, default: str | None = None) -> str | None:
    """
    Railway-safe secret getter:
    - Prefer environment variables (Railway)
    - Fallback to Streamlit secrets (local / Streamlit Cloud)
    """
    v = os.getenv(name)
    if v is not None and str(v).strip() != "":
        return v

    try:
        import streamlit as st  # local import to avoid hard dependency outside Streamlit

        if name in st.secrets and str(st.secrets.get(name, "")).strip() != "":
            return str(st.secrets[name])
    except Exception:
        pass

    return default


# ============================================================
# CLIENTS
# ============================================================
def public_client(url: str, anon_key: str):
    """Public (anon) client; do NOT cache here (cache in app.py only)."""
    return create_client(url, anon_key)


def authed_client(url: str, anon_key: str, session_obj: Any):
    """
    Create an authed Supabase client using a user session after login.
    session_obj can be:
      - an object/dict with access_token (recommended)
      - or a raw access token string
    """
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

    # supabase-py supports setting auth with the access token
    sb.auth.set_session(token, None)  # refresh token optional; None is ok if you don't store it
    return sb


# ============================================================
# DATA LOADERS (NO streamlit calls inside; return safe values)
# ============================================================
def _safe_execute(resp: Any) -> List[Dict[str, Any]]:
    """
    Normalizes supabase-py execute() response into a list[dict].
    Works across versions where execute returns:
      - object with .data
      - dict with "data"
      - list directly
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


def current_session_id(c) -> str | None:
    """
    Reads current 'session' identifier from your legacy sessions table.
    Adjust the table/column names if your schema differs.
    """
    # Try a few common patterns safely.
    # 1) app_state.current_session_id
    try:
        rows = _safe_execute(c.table("app_state").select("current_session_id").limit(1).execute())
        if rows and rows[0].get("current_session_id") is not None:
            return str(rows[0]["current_session_id"])
    except Exception:
        pass

    # 2) sessions_legacy: latest row by id or created_at
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
                # common column names
                for k in ("session_id", "id", "season_id", "legacy_session_id"):
                    if rows[0].get(k) is not None:
                        return str(rows[0][k])
        except Exception:
            continue

    return None


def get_app_state(c) -> Dict[str, Any]:
    """
    Loads app_state row (expects next_payout_index, etc.).
    Returns {} if missing.
    """
    try:
        rows = _safe_execute(c.table("app_state").select("*").limit(1).execute())
        return rows[0] if rows else {}
    except Exception:
        return {}


def load_member_registry(c) -> Tuple[List[str], Dict[str, int], Dict[str, str], pd.DataFrame]:
    """
    Loads member_registry and returns:
      labels: list[str] (used for selectbox)
      label_to_id: dict[label -> legacy_member_id]
      label_to_name: dict[label -> full_name]
      df_members: pd.DataFrame
    """
    try:
        rows = _safe_execute(
            c.table("member_registry")
            .select("*")
            .order("legacy_member_id", desc=False)
            .execute()
        )
    except Exception:
        rows = []

    df = pd.DataFrame(rows) if rows else pd.DataFrame()

    labels: List[str] = []
    label_to_id: Dict[str, int] = {}
    label_to_name: Dict[str, str] = {}

    if not df.empty:
        # Make sure legacy_member_id exists
        if "legacy_member_id" not in df.columns:
            # Try fallback column name
            if "member_id" in df.columns:
                df = df.rename(columns={"member_id": "legacy_member_id"})

        # Build a display name
        def _build_name(r: pd.Series) -> str:
            for key in ("full_name", "name", "member_name"):
                if key in r and pd.notna(r[key]) and str(r[key]).strip():
                    return str(r[key]).strip()
            first = str(r.get("first_name", "") or "").strip()
            last = str(r.get("last_name", "") or "").strip()
            nm = (first + " " + last).strip()
            return nm if nm else "Member"

        if "legacy_member_id" in df.columns:
            for _, r in df.iterrows():
                mid = r.get("legacy_member_id")
                if pd.isna(mid):
                    continue
                try:
                    mid_int = int(mid)
                except Exception:
                    continue
                name = _build_name(r)
                label = f"{mid_int:02d} • {name}"
                labels.append(label)
                label_to_id[label] = mid_int
                label_to_name[label] = name

    return labels, label_to_id, label_to_name, df
