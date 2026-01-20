
# app.py ✅ COMPLETE SINGLE-FILE VERSION (uses members_legacy)
from __future__ import annotations

import os
import streamlit as st
import pandas as pd
from supabase import create_client
from postgrest.exceptions import APIError

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
#   SUPABASE_SCHEMA = "public"   # optional (use if tables not in public)
# ============================================================
def get_secret(key: str, default: str | None = None) -> str | None:
    # Prefer Streamlit secrets, fallback to environment variables
    if key in st.secrets:
        return str(st.secrets.get(key))
    return os.getenv(key, default)

SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_ANON_KEY = get_secret("SUPABASE_ANON_KEY")
SUPABASE_SCHEMA = (get_secret("SUPABASE_SCHEMA", "public") or "public").strip()

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Missing SUPABASE_URL or SUPABASE_ANON_KEY in Streamlit Secrets / Environment.")
    st.stop()

SUPABASE_URL = SUPABASE_URL.strip()
SUPABASE_ANON_KEY = SUPABASE_ANON_KEY.strip()

# ============================================================
# CLIENT (cache_resource is OK for client object)
# ============================================================
@st.cache_resource
def get_public_client(url: str, anon_key: str):
    return create_client(url.strip(), anon_key.strip())

sb = get_public_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# ============================================================
# SAFE SELECT HELPER (prevents crash + shows real PostgREST error)
# ============================================================
def safe_select(
    client,
    table_name: str,
    select_cols: str = "*",
    schema: str = "public",
    order_by: str | None = None,
    order_desc: bool = False,
    limit: int | None = None,
):
    """
    Returns list[dict]. Never raises APIError; instead shows a useful error on screen.
    """
    try:
        q = client.schema(schema).table(table_name).select(select_cols)
        if order_by:
            q = q.order(order_by, desc=order_desc)
        if limit is not None:
            q = q.limit(limit)
        resp = q.execute()
        return resp.data or []
    except APIError as e:
        st.error(f"Supabase APIError reading {schema}.{table_name}")
        st.code(str(e), language="text")

        # structured payload (usually includes: message, details, hint, code)
        payload = e.args[0] if getattr(e, "args", None) else None
        if payload:
            st.write("Error payload:", payload)

        msg = str(e).lower()
        if "row level security" in msg or "permission denied" in msg or "rls" in msg:
            st.warning(
                f"RLS likely blocking reads on {schema}.{table_name}. "
                f"Add a SELECT policy for anon/authenticated OR disable RLS for that table."
            )
        if "does not exist" in msg or "relation" in msg:
            st.warning(
                f"Table/view not found: {schema}.{table_name}. "
                f"Confirm the table name and schema (try setting SUPABASE_SCHEMA in secrets)."
            )
        if "column" in msg and "does not exist" in msg:
            st.warning("Column mismatch. Confirm your selected column names exist exactly.")
        return []
    except Exception as e:
        st.error(f"Unexpected error reading {schema}.{table_name}: {e}")
        return []

# ============================================================
# DATA LOADERS (cache_data must only take hashable primitives)
# ============================================================
@st.cache_data(ttl=90)
def load_members_legacy(url: str, anon_key: str, schema: str) -> tuple[list[str], dict, dict, pd.DataFrame]:
    """
    Source of truth: <schema>.members_legacy

    Expected columns (minimum):
      - legacy_member_id (int)
      - full_name (text)
    """
    client = create_client(url.strip(), anon_key.strip())

    rows = safe_select(
        client=client,
        table_name="members_legacy",
        select_cols="legacy_member_id, full_name",
        schema=schema,
        order_by="legacy_member_id",
        order_desc=False,
        limit=None,
    )

    df_members = pd.DataFrame(rows)

    if df_members.empty:
        # Return empty but valid structures so app doesn't crash
        empty = pd.DataFrame(columns=["legacy_member_id", "full_name"])
        return [], {}, {}, empty

    # Normalize
    df_members["legacy_member_id"] = pd.to_numeric(df_members["legacy_member_id"], errors="coerce").astype("Int64")
    df_members["full_name"] = df_members["full_name"].astype(str)
    df_members = df_members.dropna(subset=["legacy_member_id"]).copy()
    df_members["legacy_member_id"] = df_members["legacy_member_id"].astype(int)

    # Labels
    df_members["label"] = df_members.apply(
        lambda r: f'{int(r["legacy_member_id"]):02d} • {r["full_name"]}',
        axis=1,
    )

    labels = df_members["label"].tolist()
    label_to_id = dict(zip(df_members["label"], df_members["legacy_member_id"]))
    label_to_name = dict(zip(df_members["label"], df_members["full_name"]))

    return labels, label_to_id, label_to_name, df_members


@st.cache_data(ttl=90)
def load_app_state(url: str, anon_key: str, schema: str) -> dict:
    client = create_client(url.strip(), anon_key.strip())
    rows = safe_select(client, "app_state", "*", schema=schema, limit=1)
    return rows[0] if rows else {}


@st.cache_data(ttl=90)
from supabase import create_client

@st.cache_data(ttl=30)
def load_current_session_id(SUPABASE_URL: str, SUPABASE_ANON_KEY: str, SUPABASE_SCHEMA: str):
    sb = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

    try:
        resp = (
            sb.schema(SUPABASE_SCHEMA)
              .table("app_state")
              .select("next_payout_index")
              .limit(1)
              .execute()
        )
    except Exception as e:
        st.error(f"Failed to load session id: {e}")
        return None

    rows = getattr(resp, "data", None)

    if not rows or not isinstance(rows, list):
        return None

    row = rows[0]
    if not isinstance(row, dict):
        return None

    return row.get("next_payout_index")

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
        st.info("members_legacy is empty or could not be loaded (check errors above / RLS / schema).")

# ============================================================
# TROUBLESHOOTING
# ============================================================
with st.expander("Troubleshooting (if Members still shows 0)"):
    st.markdown(
        f"""
**Most common cause:** Row Level Security (RLS) is blocking anon reads.

**Fix options:**
1. In Supabase → Table Editor → `members_legacy` → **RLS Policies**  
   Add a SELECT policy like: `USING (true)` (for anon/authenticated), OR  
2. Disable RLS (not recommended for production public apps).

**Schema tip:** Your app is currently reading from schema: `{SUPABASE_SCHEMA}`  
If your legacy tables live in another schema, set `SUPABASE_SCHEMA` in Streamlit secrets.
"""
    )
