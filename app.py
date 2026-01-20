
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
# SECRETS (Streamlit Cloud → Settings → Secrets)
#   SUPABASE_URL = "https://xxxx.supabase.co"
#   SUPABASE_ANON_KEY = "..."
#   SUPABASE_SERVICE_KEY = "..."        # service_role (SECRET)
#   SUPABASE_SCHEMA = "public"          # optional
# ============================================================
def get_secret(key: str, default: str | None = None) -> str | None:
    if key in st.secrets:
        return str(st.secrets.get(key))
    return os.getenv(key, default)

SUPABASE_URL = (get_secret("SUPABASE_URL") or "").strip()
SUPABASE_ANON_KEY = (get_secret("SUPABASE_ANON_KEY") or "").strip()
SUPABASE_SERVICE_KEY = (get_secret("SUPABASE_SERVICE_KEY") or "").strip()
SUPABASE_SCHEMA = (get_secret("SUPABASE_SCHEMA", "public") or "public").strip()

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Missing SUPABASE_URL or SUPABASE_ANON_KEY in Streamlit Secrets / Environment.")
    st.stop()

if not SUPABASE_SERVICE_KEY:
    st.warning(
        "SUPABASE_SERVICE_KEY is not set. Admin write actions will be disabled.\n"
        "Add it in Streamlit Secrets if you want admin actions."
    )

# ============================================================
# CLIENTS (cache_resource OK)
# ============================================================
@st.cache_resource
def get_anon_client(url: str, anon_key: str):
    return create_client(url.strip(), anon_key.strip())

@st.cache_resource
def get_service_client(url: str, service_key: str):
    return create_client(url.strip(), service_key.strip())

sb_anon = get_anon_client(SUPABASE_URL, SUPABASE_ANON_KEY)
sb_service = get_service_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else None

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
# SAFE SELECT / SAFE WRITE (shows real PostgREST error)
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
        return []
    except Exception as e:
        st.error(f"Unexpected error reading {schema}.{table_name}: {e}")
        return []

def safe_upsert(
    client,
    table_name: str,
    payload: dict,
    schema: str = "public",
):
    try:
        resp = client.schema(schema).table(table_name).upsert(payload).execute()
        return resp.data or []
    except APIError as e:
        st.error(f"Supabase APIError writing {schema}.{table_name}")
        st.code(str(e), language="text")
        p = e.args[0] if getattr(e, "args", None) else None
        if p:
            st.write("Error payload:", p)
        return []
    except Exception as e:
        st.error(f"Unexpected error writing {schema}.{table_name}: {e}")
        return []

# ============================================================
# DATA LOADERS (cache_data takes only primitives)
#   Reads use ANON client (respects RLS)
# ============================================================
@st.cache_data(ttl=90)
def load_members_legacy(url: str, anon_key: str, schema: str):
    client = create_client(url.strip(), anon_key.strip())
    rows = safe_select(
        client=client,
        table_name="members_legacy",
        select_cols="id, name",
        schema=schema,
        order_by="id",
    )

    df = pd.DataFrame(rows)
    if df.empty:
        return [], {}, {}, pd.DataFrame(columns=["id", "name"])

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
    client = create_client(url.strip(), anon_key.strip())

    rows = safe_select(client, "current_season_view", "*", schema=schema, limit=1)
    if rows:
        row = rows[0]
        for k in ("session_id", "season_id", "current_session_id", "id", "season_name", "session_name"):
            if k in row and row[k]:
                return str(row[k])

    rows2 = safe_select(client, "sessions_legacy", "*", schema=schema, order_by="id", order_desc=True, limit=1)
    if rows2:
        row = rows2[0]
        for k in ("id", "session_id", "season_id", "season_name", "session_name"):
            if k in row and row[k]:
                return str(row[k])

    return None

# ============================================================
# LOAD DATA (READS via ANON)
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
# ADMIN PANEL (WRITES via SERVICE key)
# ============================================================
with st.expander("Admin (Service Key)", expanded=False):
    if not sb_service:
        st.info("Service key not configured. Add SUPABASE_SERVICE_KEY in secrets to enable admin actions.")
    else:
        st.caption("These actions use the Service Role key (bypasses RLS). Keep this app private/admin-only.")

        colA, colB = st.columns([1, 1])

        with colA:
            st.subheader("Set Next Payout Index")
            new_idx = st.number_input("next_payout_index", min_value=1, step=1, value=int(app_state.get("next_payout_index") or 1))
            if st.button("✅ Save next_payout_index", use_container_width=True):
                safe_upsert(
                    sb_service,
                    "app_state",
                    {"next_payout_index": int(new_idx)},
                    schema=SUPABASE_SCHEMA,
                )
                st.cache_data.clear()
                st.success("Saved. Click Refresh or wait for cache to expire.")
                st.rerun()

        with colB:
            st.subheader("Quick Health Check")
            if st.button("🔎 Test service read members_legacy", use_container_width=True):
                rows = safe_select(sb_service, "members_legacy", "id,name", schema=SUPABASE_SCHEMA, limit=3)
                st.write(rows)

# ============================================================
# TROUBLESHOOTING
# ============================================================
with st.expander("Troubleshooting (if Members still shows 0)"):
    st.markdown(
        "Reads are using the **ANON** key (RLS applies). Admin writes use the **SERVICE** key (RLS bypass).\n\n"
        f"Current schema: `{SUPABASE_SCHEMA}`\n\n"
        "If anon reads fail, add a SELECT policy on `public.members_legacy`:\n"
        "- `FOR SELECT TO anon USING (true)`\n"
    )
