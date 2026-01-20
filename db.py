

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
        import streamlit as st  # local import

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
    return create_client(url.strip(), anon_key.strip())


def service_client(url: str, service_key: str):
    """Service-role client (bypasses RLS). Use ONLY server-side."""
    return create_client(url.strip(), service_key.strip())


def authed_client(url: str, anon_key: str, session_obj: Any):
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
# DATA LOADERS (NO streamlit calls inside; return safe values)
# ============================================================
def _safe_execute(resp: Any) -> List[Dict[str, Any]]:
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
    # 1) app_state.current_session_id
    try:
        rows = _safe_execute(c.table("app_state").select("current_session_id").limit(1).execute())
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
    try:
        rows = _safe_execute(c.table("app_state").select("*").limit(1).execute())
        return rows[0] if rows else {}
    except Exception:
        return {}


# ============================================================
# ✅ FIXED: MEMBERS LOADER (members_legacy uses id + name)
# ============================================================
def load_members_legacy(c) -> Tuple[List[str], Dict[str, int], Dict[str, str], pd.DataFrame]:
    """
    Loads public.members_legacy and returns:
      labels: list[str] (used for selectbox)
      label_to_id: dict[label -> id]
      label_to_name: dict[label -> name]
      df_members: pd.DataFrame

    Confirmed columns:
      - id (bigint)
      - name (text)
    """
    try:
        rows = _safe_execute(
            c.table("members_legacy")
            .select("id,name,position,phone,has_benefits,contributed,foundation_contrib,loan_due,payout_total,total_fines_accumulated")
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
