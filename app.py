

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
# SECRETS
# Streamlit Cloud → App → Settings → Secrets:
#   SUPABASE_URL = "https://xxxx.supabase.co"
#   SUPABASE_ANON_KEY = "xxxx"
#   SUPABASE_SCHEMA = "public"   # optional
# ============================================================
def get_secret(key: str, default: str | None = None) -> str | None:
    if key in st.secrets:
        return str(st.secrets.get(key))
    return os.getenv(key, default)

SUPABASE_URL = (get_secret("SUPABASE_URL") or "").strip()
SUPABASE_ANON_KEY = (get_secret("SUPABASE_ANON_KEY") or "").strip()
SUPABASE_SCHEMA = (get_secret("SUPABASE_SCHEMA", "public") or "public").strip()

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Missing SUPABASE_URL or SUPABASE_ANON_KEY in Streamlit Secrets / Environment.")
    st.stop()

# ============================================================
# CLIENT (cache_resource is OK for client object)
# ============================================================
@st.cache_resource
def get_public_client(url: str, anon_key: str):
    return create_client(url.strip(), anon_key.strip())

sb = get_public_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# ============================================================
# TOP BAR ACTIONS (cache clear)
# ============================================================
bar1, bar2 = st.columns([1, 0.25])
with bar2:
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.title(f"🏦 {APP_BRAND} • Bank Dashboard")

# ============================================================
# SAFE SELECT HELPER (shows real PostgREST error)
# ============================================================
def safe_select(
    client,
    table_name: str,
    select_cols: str = "*",
    schema: str = "public",
    order_by: str | None = None,
    order_desc: bool = False,
    limit: int | None = None,
) -> list[dict]:
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

        payload = e.args[0] if getattr(e, "args", None) else None
        if payload:
            st.write("Error payload:", payload)

        msg = str(e).lower()
        if "row level security" in msg or "permission denied" in msg or "rls" in msg:
            st.warning(
                f"RLS is likely blocking reads on {schema}.{table_name}. "
                f"Add a SELECT policy for anon/authenticated or disable RLS for testing."
            )
        if "does not exist" in msg or "relation" in msg:
            st.warning(
                f"Table/view not found: {schema}.{table_name}. "
                f"Confirm table name and schema (SUPABASE_SCHEMA)."
            )
        if "column" in msg and "does not exist" in msg:
            st.warning("Column mismatch. Confirm selected column names exist exactly.")
        return []
    except Exception as e:
        st.error(f"Unexpected error reading {schema}.{table_name}: {e}")
        return []

# ============================================================
# DATA LOADERS (cache_data must only take hashable primitives)
# ============================================================
@st.cache_data(ttl=90)
def load_members_legacy(url: str, anon_key: str, schema: str):
    """
    Source of truth: <schema>.members_legacy
    Confirmed columns: id (bigint), name (text)
    Returns:
      labels, label_to_id, label_to_name, df_members
    """
    client = create_client(url.strip(), anon_key.strip())

    rows = safe_select(
        client=client,
        table_name="members_legacy",
        select_cols="id, name",
        schema=schema,
        order_by="id",
        order_desc=False,
        limit=None,
    )

    df = pd.DataFrame(rows)
    if df.empty:
        empty = pd.DataFrame(columns=["id", "name"])
        return [], {}, {}, empty

    df["id"] = pd.to_numeric(df["id"], errors="coerce")
    df = df.dropna(subset=["id"]).copy()
    df["id"] = df["id"].astype(int)
    df["name"] = df["name"].astype(str)

    df["label"] = df.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)

    labels = df["label"].tolist()
    label_to_id = dict(zip(df["label"], df["id"]))
    label_to_name = dict(zip(df["label"], df["name"]))

    return labels, label_to_id, label_to_name, df


@st.cache_data(ttl=90)
def load_app_state(url: str, anon_key: str, schema: str) -> dict:
    client = create_client(url.strip(), anon_key.strip())
    rows = safe_select(client, "app_state", "*", schema=schema, limit=1)
    return rows[0] if rows else {}

@st.cache_data(ttl=90)
def load_current_session_id(url: str, anon_key: str, schema: str) -> str | None:
    """
    Try current_season_view first, fallback to sessions_legacy.
    """
    client = create_client(url.strip(), anon_key.strip())

    # view exists in your schema list; try it
    rows = safe_select(client, "current_season_view", "*", schema=schema, limit=1)
    if rows:
        row = rows[0]
        for k in ("session_id", "season_id", "current_session_id", "id", "season_name", "session_name"):
            if k in row and row[k]:
                return str(row[k])

    # fallback
    rows2 = safe_select(client, "sessions_legacy", "*", schema=schema, order_by="id", order_desc=True, limit=1)
    if rows2:
        row = rows2[0]
        for k in ("id", "session_id", "season_id", "season_name", "session_name"):
            if k in row and row[k]:
                return str(row[k])

    return None

# ============================================================
# LOAD DATA (IMPORTANT: actually call the loader)
# ============================================================
labels, label_to_id, label_to_name, df_members = load_members_legacy(
    SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA
)

app_state = load_app_state(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)
sid = load_current_session_id(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)

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
    st.write("Selected member id:", mid)
    st.write("Selected member:", mname)
else:
    st.warning("No members found in members_legacy (or table could not be read).")

# ============================================================
# PREVIEW TABLE
# ============================================================
with st.expander("Member Registry (preview)", expanded=False):
    if isinstance(df_members, pd.DataFrame) and not df_members.empty:
        st.dataframe(df_members[["id", "name"]], use_container_width=True)
    else:
        st.info("members_legacy is empty or could not be loaded (check errors above / RLS / schema).")

# ============================================================
# TROUBLESHOOTING
# ============================================================
with st.expander("Troubleshooting (if Members still shows 0)"):
    st.markdown(
        "Most common cause: Row Level Security (RLS) blocking anon reads.\n\n"
        f"Current schema: `{SUPABASE_SCHEMA}`\n\n"
        "If needed, add this policy on `public.members_legacy`:\n"
        "- FOR SELECT TO anon USING (true)\n"
        )
