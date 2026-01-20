
# app.py  ✅ COMPLETE SINGLE-FILE VERSION (uses members_legacy)
from __future__ import annotations

import os
import streamlit as st
import pandas as pd
from supabase import create_client

APP_BRAND = "theyoungshallgrow"

st.set_page_config(
    page_title=f"{APP_BRAND} • Bank Dashboard",
    layout="wide",
    page_icon="🏦",
)

# ============================================================
# SECRETS (Streamlit Cloud: set in App > Settings > Secrets)
#   SUPABASE_URL = "..."
#   SUPABASE_ANON_KEY = "..."
# ============================================================
def get_secret(key: str, default: str | None = None) -> str | None:
    # Prefer Streamlit secrets, fallback to environment variables
    if key in st.secrets:
        return str(st.secrets.get(key))
    return os.getenv(key, default)

SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_ANON_KEY = get_secret("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Missing SUPABASE_URL or SUPABASE_ANON_KEY in Streamlit Secrets / Environment.")
    st.stop()

# ============================================================
# CLIENT (cache_resource is OK for client object)
# ============================================================
@st.cache_resource
def get_public_client(url: str, anon_key: str):
    return create_client(url, anon_key)

sb = get_public_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# ============================================================
# DATA LOADERS (cache_data must only take hashable primitives)
# ============================================================
@st.cache_data(ttl=90)
from postgrest.exceptions import APIError

@st.cache_data(ttl=90)
def load_members_legacy(url: str, anon_key: str) -> tuple[list[str], dict, dict, pd.DataFrame]:
    """
    Source of truth: public.members_legacy

    Expected columns (minimum):
      - legacy_member_id (int)
      - full_name (text)

    Returns:
      labels: list[str] (for selectbox)
      label_to_id: dict[label -> legacy_member_id]
      label_to_name: dict[label -> full_name]
      df_members: DataFrame of members
    """
    # Create a local client INSIDE the cached function (safe: url/key are primitives)
    supabase = create_client(url.strip(), anon_key.strip())

    try:
        # If your table is in a non-public schema, use:
        # resp = supabase.schema("legacy").table("members_legacy").select("legacy_member_id,full_name").execute()
        resp = (
            supabase
            .table("members_legacy")
            .select("legacy_member_id, full_name")
            .order("legacy_member_id", desc=False)
            .execute()
        )

        rows = resp.data or []
        df_members = pd.DataFrame(rows)

        # Guardrails for missing columns
        if df_members.empty:
            # Return empty but valid structures so app doesn't crash
            return [], {}, {}, pd.DataFrame(columns=["legacy_member_id", "full_name"])

        # Normalize columns just in case
        df_members["legacy_member_id"] = pd.to_numeric(df_members["legacy_member_id"], errors="coerce").astype("Int64")
        df_members["full_name"] = df_members["full_name"].astype(str)

        df_members = df_members.dropna(subset=["legacy_member_id"]).copy()
        df_members["legacy_member_id"] = df_members["legacy_member_id"].astype(int)

        # Create UI labels
        df_members["label"] = df_members.apply(
            lambda r: f'{int(r["legacy_member_id"]):02d} • {r["full_name"]}',
            axis=1
        )

        labels = df_members["label"].tolist()
        label_to_id = dict(zip(df_members["label"], df_members["legacy_member_id"]))
        label_to_name = dict(zip(df_members["label"], df_members["full_name"]))

        return labels, label_to_id, label_to_name, df_members

    except APIError as e:
        # Show the REAL PostgREST error (this is what your traceback is hiding)
        st.error("Supabase APIError while loading members_legacy.")
        st.code(str(e), language="text")

        # Also print structured fields if present
        payload = None
        if hasattr(e, "args") and e.args:
            payload = e.args[0]
        st.write("Error payload:", payload)

        # Helpful hints based on common failures
        msg = str(e).lower()
        if "permission denied" in msg or "row level security" in msg or "rls" in msg:
            st.warning(
                "This looks like an RLS/policy issue. Either create a SELECT policy for public.members_legacy "
                "or use a service_role key on the server (never expose service_role to browsers)."
            )
        if "does not exist" in msg or "relation" in msg:
            st.warning(
                "This looks like a table/schema mismatch. Confirm the table name is exactly 'members_legacy' "
                "and whether it is in the 'public' schema (otherwise use supabase.schema('...').table(...))."
            )
        if "column" in msg and "does not exist" in msg:
            st.warning(
                "This looks like a column name mismatch. Confirm columns are exactly legacy_member_id and full_name."
            )

        raise


@st.cache_data(ttl=90)
def load_app_state(url: str, anon_key: str) -> dict:
    """
    Reads app_state table (single row expected).
    If table is empty, returns {}.
    """
    sb_local = get_public_client(url, anon_key)

    try:
        resp = sb_local.table("app_state").select("*").limit(1).execute()
        rows = resp.data or []
        return rows[0] if rows else {}
    except Exception:
        # If table missing or blocked by RLS, just fail gracefully
        return {}


@st.cache_data(ttl=90)
def load_current_session_id(url: str, anon_key: str) -> str | None:
    """
    Uses current_season_view if available, otherwise sessions_legacy heuristic.
    Returns a string session/season id/name if found.
    """
    sb_local = get_public_client(url, anon_key)

    # Try view first (you have current_season_view in your schema list)
    try:
        resp = sb_local.table("current_season_view").select("*").limit(1).execute()
        rows = resp.data or []
        if rows:
            row = rows[0]
            # try common keys
            for k in ("session_id", "season_id", "current_session_id", "id", "season_name", "session_name"):
                if k in row and row[k]:
                    return str(row[k])
    except Exception:
        pass

    # Fallback: sessions_legacy (if you store an active row)
    try:
        resp = sb_local.table("sessions_legacy").select("*").order("id", desc=True).limit(1).execute()
        rows = resp.data or []
        if rows:
            row = rows[0]
            for k in ("id", "session_id", "season_id", "season_name", "session_name"):
                if k in row and row[k]:
                    return str(row[k])
    except Exception:
        pass

    return None

# ============================================================
# UI: TOP BAR ACTIONS
# ============================================================
bar1, bar2 = st.columns([1, 0.25])
with bar2:
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.title(f"🏦 {APP_BRAND} • Bank Dashboard")

# ============================================================
# LOAD DATA
# ============================================================
sid = load_current_session_id(SUPABASE_URL, SUPABASE_ANON_KEY)
app_state = load_app_state(SUPABASE_URL, SUPABASE_ANON_KEY)

labels, label_to_id, label_to_name, df_members = load_members_legacy(SUPABASE_URL, SUPABASE_ANON_KEY)

# ============================================================
# KPI CARDS
# ============================================================
c1, c2, c3 = st.columns(3)
c1.metric("Current Session ID", sid or "N/A")
c2.metric("Members", f"{len(df_members):,}" if isinstance(df_members, pd.DataFrame) else "0")
c3.metric("Next Payout Index", str(app_state.get("next_payout_index", "N/A")))

st.divider()

# ============================================================
# MEMBER SELECTOR
# ============================================================
if labels:
    pick = st.selectbox("Select member", labels)
    mid = label_to_id.get(pick)
    mname = label_to_name.get(pick)
    st.write("Selected legacy_member_id:", mid)
    st.write("Selected member:", mname)
else:
    st.warning("No members found in members_legacy (or table could not be read).")

# ============================================================
# PREVIEW TABLE
# ============================================================
with st.expander("Member Registry (preview)", expanded=False):
    if isinstance(df_members, pd.DataFrame) and not df_members.empty:
        st.dataframe(df_members[["legacy_member_id", "full_name"]], use_container_width=True)
    else:
        st.info("members_legacy is empty or could not be loaded (check RLS policies).")

# ============================================================
# QUICK RLS DEBUG HINT
# ============================================================
with st.expander("Troubleshooting (if Members still shows 0)"):
    st.markdown(
        "If `members_legacy` has rows in Supabase but Streamlit still shows 0, "
        "the most common cause is **Row Level Security (RLS)**.\n\n"
        "Quick test:\n"
        "- Ensure `members_legacy` has a SELECT policy for anon or authenticated users\n"
        "- Or temporarily disable RLS on the table\n\n"
        "After fixing RLS, click **Refresh data**."
    )
