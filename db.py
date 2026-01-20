# db.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import streamlit as st
from supabase import create_client


# -------------------------
# TIME HELPERS
# -------------------------
def now_iso() -> str:
    """UTC ISO string with Z suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# -------------------------
# SECRETS
# -------------------------
def get_secret(key: str):
    import os
    import streamlit as st

    # Railway (env vars)
    if os.getenv(key):
        return os.getenv(key)

    # Streamlit Cloud (secrets)
    if hasattr(st, "secrets") and key in st.secrets:
        return st.secrets[key]

    return None


# -------------------------
# SUPABASE CLIENTS
# -------------------------
def _extract_access_token(session: Any) -> Optional[str]:
    """
    Tries to pull an access token from various session shapes:
    - supabase-py session object: session.access_token
    - dict-like session: session["access_token"]
    """
    if session is None:
        return None

    # object style
    token = getattr(session, "access_token", None)
    if token:
        return token

    # dict style
    try:
        token = session.get("access_token")
        if token:
            return token
    except Exception:
        pass

    return None


def authed_client(supabase_url: str, supabase_anon_key: str, session: Any):
    """
    Creates a Supabase client and attaches the user's JWT to PostgREST
    so that RLS policies apply correctly.
    """
    c = create_client(supabase_url, supabase_anon_key)

    token = _extract_access_token(session)
    if token:
        # Attach bearer token to database requests (RLS-aware)
        try:
            c.postgrest.auth(token)
        except Exception:
            # Some versions differ; if this fails, app may still work depending on RLS usage.
            pass

        # Also try to set auth session if supported
        try:
            refresh = getattr(session, "refresh_token", None)
            if refresh is None:
                try:
                    refresh = session.get("refresh_token")
                except Exception:
                    refresh = None
            if refresh:
                c.auth.set_session(token, refresh)
        except Exception:
            pass

    return c


# -------------------------
# QUERY HELPERS
# -------------------------
def fetch_one(query_builder) -> Optional[Dict[str, Any]]:
    """
    Execute a Supabase query builder and return the first row (dict) or None.
    """
    resp = query_builder.limit(1).execute()
    data = getattr(resp, "data", None) or []
    return data[0] if data else None


def safe_select_autosort(c, table: str, limit: int = 800):
    """
    Select rows from a table ordered by the first available timestamp-ish column.
    """
    for col in ["created_at", "issued_at", "updated_at", "paid_at", "date_paid", "start_date"]:
        try:
            return c.table(table).select("*").order(col, desc=True).limit(limit).execute()
        except Exception:
            continue
    return c.table(table).select("*").limit(limit).execute()


def has_columns(c, table: str, columns: Iterable[str]) -> bool:
    """
    Returns True if all requested columns can be selected from the table.
    Uses a lightweight select test; if Supabase returns an error, we treat as missing.
    """
    cols = list(columns)
    if not cols:
        return True
    try:
        c.table(table).select(",".join(cols)).limit(1).execute()
        return True
    except Exception:
        return False


# -------------------------
# APP-SPECIFIC LOADERS
# -------------------------
def current_session_id(c) -> Optional[str]:
    """
    Returns the current session_id from sessions_legacy.
    Tries common patterns:
      - filter is_current == true
      - otherwise latest by start_date/created_at
    """
    # Try explicit current flag
    try:
        resp = (
            c.table("sessions_legacy")
            .select("*")
            .eq("is_current", True)
            .order("start_date", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows:
            r = rows[0]
            return str(r.get("session_id") or r.get("id") or "")
    except Exception:
        pass

    # Fallback: latest session
    try:
        resp = safe_select_autosort(c, "sessions_legacy", limit=1)
        rows = resp.data or []
        if rows:
            r = rows[0]
            sid = r.get("session_id") or r.get("id")
            return str(sid) if sid is not None else None
    except Exception:
        pass

    return None


def get_app_state(c) -> Dict[str, Any]:
    """
    Returns app_state row.
    Assumes a single-row table; takes the newest row if multiple exist.
    """
    try:
        resp = safe_select_autosort(c, "app_state", limit=1)
        rows = resp.data or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def load_member_registry(c) -> Tuple[List[str], Dict[str, int], Dict[str, str], pd.DataFrame]:
    """
    Loads member_registry and returns:
      - member_labels: ["1 — John Doe", "2 — Jane Doe", ...]
      - label_to_legacy_id: {label: legacy_member_id}
      - label_to_name: {label: full_name}
      - df_registry: dataframe of registry rows
    """
    try:
        resp = (
            c.table("member_registry")
            .select("legacy_member_id,full_name,is_active")
            .order("legacy_member_id", desc=False)
            .limit(5000)
            .execute()
        )
        rows = resp.data or []
    except Exception:
        rows = []

    df = pd.DataFrame(rows)
    if df.empty:
        return ([], {}, {}, df)

    def _name(r):
        nm = (r.get("full_name") or "").strip()
        mid = r.get("legacy_member_id")
        return nm if nm else f"Member {mid}"

    labels: List[str] = []
    label_to_id: Dict[str, int] = {}
    label_to_name: Dict[str, str] = {}

    for r in rows:
        mid = int(r.get("legacy_member_id") or 0)
        nm = _name(r)
        label = f"{mid} — {nm}"
        labels.append(label)
        label_to_id[label] = mid
        label_to_name[label] = nm

    return labels, label_to_id, label_to_name, df


# -------------------------
# SCHEMA CHECK
# -------------------------
def schema_check_or_stop(c) -> None:
    """
    Basic safety checks so the app fails with a readable message
    instead of crashing later.
    """
    required_tables = [
        "profiles",
        "member_registry",
        "sessions_legacy",
        "app_state",
        "contributions_legacy",
        "foundation_payments_legacy",
        "fines_legacy",
        "audit_log",
        "signatures",
        "payouts_legacy",
        "loan_requests",
        "loans_legacy",
        "loan_payments",
    ]

    # Table existence check (best-effort): try select limit 1
    missing_tables = []
    for t in required_tables:
        try:
            c.table(t).select("*").limit(1).execute()
        except Exception:
            missing_tables.append(t)

    if missing_tables:
        st.error("Database schema is missing required tables:")
        st.code("\n".join(missing_tables))
        st.stop()

    # Optional: verify key columns used heavily
    col_checks = [
        ("member_registry", ["legacy_member_id", "full_name"]),
        ("contributions_legacy", ["session_id", "member_id", "amount", "kind"]),
        ("fines_legacy", ["member_id", "amount", "status"]),
        ("app_state", ["next_payout_index"]),
        ("profiles", ["id", "role", "approved", "member_id"]),
    ]

    bad_cols = []
    for table, cols in col_checks:
        if not has_columns(c, table, cols):
            bad_cols.append(f"{table}: {', '.join(cols)}")

    if bad_cols:
        st.error("Database tables exist, but some expected columns are missing:")
        st.code("\n".join(bad_cols))
        st.stop()
