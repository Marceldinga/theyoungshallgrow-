# app.py ✅ CLEAN + UPDATED (adds Minutes & Attendance legacy page)
# - Railway-safe secrets
# - Safe imports (Audit / Health / Loans)
# - Dashboard fixed (uses dashboard_next_view.current_pot)
# - Loans entry works with your current loans.py wrapper (show_loans or render_loans)
# - Adds ✅ Minutes & Attendance (Legacy): meeting_minutes_legacy + meeting_attendance_legacy
# - Avoids crashes if a module is missing

from __future__ import annotations

import os
from datetime import date, datetime
import streamlit as st
import pandas as pd
from supabase import create_client
from postgrest.exceptions import APIError

from admin_panels import render_admin
from payout import render_payouts
from audit_panel import render_audit
from health_panel import render_health

# ✅ Loans UI (safe import)
# Support both patterns:
#  - loans.py defines show_loans(...)
#  - loans.py defines render_loans(...)
try:
    import loans as loans_entry
except Exception:
    loans_entry = None

APP_BRAND = "theyoungshallgrow"

st.set_page_config(
    page_title=f"{APP_BRAND} • Bank Dashboard",
    layout="wide",
    page_icon="🏦",
)

# ============================================================
# SECRETS (Railway-safe)
# ============================================================
def get_secret(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key)
    if v not in (None, ""):
        return v
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


SUPABASE_URL = (get_secret("SUPABASE_URL") or "").strip()
SUPABASE_ANON_KEY = (get_secret("SUPABASE_ANON_KEY") or "").strip()
SUPABASE_SERVICE_KEY = (get_secret("SUPABASE_SERVICE_KEY") or "").strip()
SUPABASE_SCHEMA = (get_secret("SUPABASE_SCHEMA", "public") or "public").strip()

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Missing SUPABASE_URL or SUPABASE_ANON_KEY. Set Railway Variables or Streamlit Secrets.")
    st.stop()

if not SUPABASE_SERVICE_KEY:
    st.warning("SUPABASE_SERVICE_KEY not set. Admin/Loans/Payout write features will be disabled.")

# ============================================================
# CLIENTS
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
# TOP BAR
# ============================================================
left, right = st.columns([1, 0.25])
with right:
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()

st.title(f"🏦 {APP_BRAND} • Bank Dashboard")

# ============================================================
# SAFE QUERY HELPER
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
        return []
    except Exception as e:
        st.error(f"Unexpected error reading {schema}.{table_name}: {e}")
        return []


# ✅ Canonical dashboard source
def get_dashboard_next(sb, schema: str) -> dict:
    rows = safe_select(sb, "dashboard_next_view", "*", schema=schema, limit=1)
    return rows[0] if rows else {}


# ============================================================
# LOADERS
# ============================================================
@st.cache_data(ttl=90)
def load_members_legacy(url: str, anon_key: str, schema: str):
    client = create_client(url, anon_key)
    rows = safe_select(client, "members_legacy", "id,name,position", schema=schema, order_by="id")
    df = pd.DataFrame(rows)

    if df.empty:
        return [], {}, {}, pd.DataFrame(columns=["id", "name", "position"])

    df["id"] = pd.to_numeric(df["id"], errors="coerce")
    df = df.dropna(subset=["id"]).copy()
    df["id"] = df["id"].astype(int)
    df["name"] = df["name"].astype(str)
    df["label"] = df.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)

    labels = df["label"].tolist()
    label_to_id = dict(zip(df["label"], df["id"]))
    label_to_name = dict(zip(df["label"], df["name"]))

    return labels, label_to_id, label_to_name, df


@st.cache_data(ttl=60)
def load_contributions_view(url: str, anon_key: str, schema: str) -> pd.DataFrame:
    client = create_client(url, anon_key)
    rows = safe_select(
        client,
        "contributions_with_member",
        "*",
        schema=schema,
        order_by="created_at",
        order_desc=True,
        limit=200,
    )
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ============================================================
# NAVIGATION ✅ UPDATED (adds Minutes & Attendance)
# ============================================================
page = st.sidebar.radio(
    "Menu",
    [
        "Dashboard",
        "Contributions",
        "Payouts",
        "Loans",
        "Minutes & Attendance",
        "Admin",
        "Audit",
        "Health",
    ],
    key="main_menu",
)

# ============================================================
# DASHBOARD ✅ FIXED (reads canonical dashboard_next_view.current_pot)
# ============================================================
if page == "Dashboard":
    labels, label_to_id, label_to_name, df_members = load_members_legacy(
        SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA
    )

    dash = get_dashboard_next(sb_anon, SUPABASE_SCHEMA)

    next_index = dash.get("next_payout_index")
    next_date = dash.get("next_payout_date")
    next_beneficiary = dash.get("next_beneficiary")  # formatted "3 • Name"
    current_pot = dash.get("current_pot")            # ✅ canonical pot (this session)
    session_number = dash.get("session_number")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Members", f"{len(df_members):,}")
    c2.metric("Next Payout Index", str(next_index) if next_index is not None else "—")
    c3.metric("Next Payout Date", str(next_date) if next_date else "—")
    c4.metric("Next Beneficiary", str(next_beneficiary) if next_beneficiary else "—")

    try:
        st.caption(f"Pot Amount (this session): {float(current_pot or 0):,.0f}")
    except Exception:
        st.caption(f"Pot Amount (this session): {current_pot}")

    st.caption(f"Current session #: {session_number if session_number is not None else '—'}")

    st.divider()

    if labels:
        pick = st.selectbox("Select member", labels, key="dash_member_pick")
        st.write("Selected member id:", label_to_id.get(pick))
        st.write("Selected member:", label_to_name.get(pick))
    else:
        st.warning("No members found in members_legacy.")

    with st.expander("Member Registry (preview)", expanded=False):
        if not df_members.empty:
            st.dataframe(df_members[["id", "name", "position"]], use_container_width=True, hide_index=True)
        else:
            st.info("members_legacy empty or not readable.")

# ============================================================
# CONTRIBUTIONS
# ============================================================
elif page == "Contributions":
    st.header("Contributions (View)")
    df = load_contributions_view(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)
    if df.empty:
        st.info("No contributions found (or view not readable).")
        st.caption("Confirm contributions_with_member exists and GRANT SELECT to anon.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

# ============================================================
# PAYOUTS
# ============================================================
elif page == "Payouts":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in Railway Variables / Secrets.")
    else:
        render_payouts(sb_service, SUPABASE_SCHEMA)

# ============================================================
# LOANS
# ============================================================
elif page == "Loans":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in Railway Variables / Secrets.")
    else:
        if loans_entry is None:
            st.error("Loans UI not available. loans.py failed to import.")
            st.caption("Check Railway/Streamlit logs for the import error.")
        else:
            loans_fn = getattr(loans_entry, "show_loans", None) or getattr(loans_entry, "render_loans", None)
            if loans_fn is None:
                st.error("Loans UI not available. loans.py must define show_loans() or render_loans().")
            else:
                loans_fn(sb_service, SUPABASE_SCHEMA, actor_user_id="admin")

# ============================================================
# ✅ Minutes & Attendance (Legacy)
# Tables:
#   - meeting_minutes_legacy
#   - meeting_attendance_legacy (legacy_member_id)
# ============================================================
elif page == "Minutes & Attendance":
    st.header("📝 Meeting Minutes & ✅ Attendance (Legacy)")

    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY to enable writing minutes & attendance.")
        st.stop()

    labels, label_to_id, label_to_name, df_members = load_members_legacy(
        SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA
    )

    tab1, tab2 = st.tabs(["Minutes / Documentation", "Attendance"])

    # --------------------------
    # MINUTES (LEGACY)
    # --------------------------
    with tab1:
        st.subheader("Meeting Minutes / Documentation (Legacy)")

        with st.form("minutes_legacy_form", clear_on_submit=True):
            mdate = st.date_input("Meeting date", value=date.today(), key="minutes_legacy_date")
            title = st.text_input("Title", key="minutes_legacy_title")
            tags = st.text_input("Tags (optional)", key="minutes_legacy_tags")
            content = st.text_area("Minutes / Documentation", height=260, key="minutes_legacy_content")
            ok = st.form_submit_button("💾 Save minutes", use_container_width=True)

        if ok:
            if not title.strip() or not content.strip():
                st.error("Title and content are required.")
            else:
                payload = {
                    "meeting_date": str(mdate),
                    "title": title.strip(),
                    "content": content.strip(),
                    "tags": tags.strip() or None,
                    "created_by": "admin",
                }
                try:
                    sb_service.schema(SUPABASE_SCHEMA).table("meeting_minutes_legacy").insert(payload).execute()
                    st.success("Minutes saved.")
                except Exception as e:
                    st.error("Failed to save minutes.")
                    st.exception(e)

        st.divider()
        st.markdown("### Recent minutes")
        try:
            rows = (
                sb_service.schema(SUPABASE_SCHEMA).table("meeting_minutes_legacy")
                .select("*")
                .order("meeting_date", desc=True)
                .limit(50)
                .execute().data
                or []
            )
            df = pd.DataFrame(rows)
            if df.empty:
                st.info("No minutes recorded yet.")
            else:
                st.dataframe(df, use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning("Could not load minutes.")
            st.exception(e)

    # --------------------------
    # ATTENDANCE (LEGACY)
    # --------------------------
    with tab2:
        st.subheader("Attendance (Legacy)")

        adate = st.date_input("Attendance date", value=date.today(), key="att_legacy_date")
        st.caption("Record attendance per member for this meeting date.")

        with st.form("attendance_legacy_form", clear_on_submit=True):
            if labels:
                pick = st.selectbox("Member", labels, key="att_legacy_member_pick")
                legacy_member_id = int(label_to_id.get(pick))
                member_name = str(label_to_name.get(pick))
            else:
                st.warning("No members loaded from members_legacy.")
                legacy_member_id = 0
                member_name = ""

            status = st.selectbox("Status", ["present", "absent", "late", "excused"], index=0, key="att_legacy_status")
            note = st.text_input("Note (optional)", "", key="att_legacy_note")
            ok2 = st.form_submit_button("✅ Save attendance", use_container_width=True)

        if ok2:
            if legacy_member_id <= 0:
                st.error("Invalid member selection.")
            else:
                payload = {
                    "meeting_date": str(adate),
                    "legacy_member_id": int(legacy_member_id),
                    "member_name": member_name,
                    "status": status,
                    "note": note.strip() or None,
                    "created_by": "admin",
                }
                try:
                    sb_service.schema(SUPABASE_SCHEMA).table("meeting_attendance_legacy").insert(payload).execute()
                    st.success("Attendance saved.")
                except Exception as e:
                    st.error("Failed to save attendance.")
                    st.exception(e)

        st.divider()
        st.markdown("### Attendance for selected date")
        try:
            rows = (
                sb_service.schema(SUPABASE_SCHEMA).table("meeting_attendance_legacy")
                .select("*")
                .eq("meeting_date", str(adate))
                .order("legacy_member_id", desc=False)
                .limit(500)
                .execute().data
                or []
            )
            df = pd.DataFrame(rows)
            if df.empty:
                st.info("No attendance recorded for this date yet.")
            else:
                st.dataframe(df, use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning("Could not load attendance.")
            st.exception(e)

# ============================================================
# ADMIN
# ============================================================
elif page == "Admin":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in Railway Variables / Secrets.")
    else:
        render_admin(sb_service=sb_service, schema=SUPABASE_SCHEMA, actor_email="admin@yourorg.com")

# ============================================================
# AUDIT
# ============================================================
elif page == "Audit":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in Railway Variables / Secrets.")
    else:
        render_audit(sb_service=sb_service, schema=SUPABASE_SCHEMA)

# ============================================================
# HEALTH
# ============================================================
elif page == "Health":
    render_health(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)
