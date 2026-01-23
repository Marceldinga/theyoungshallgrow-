# app.py ✅ COMPLETE UPDATED — GLOBAL DARK THEME FOR ENTIRE SYSTEM (Dashboard + Payouts + Loans + Admin + Audit + Health)
# Goal: Make EVERY page use the same dark dotted-grid theme (like your dashboard screenshot).
#
# What changed vs your current app.py:
# ✅ Added inject_global_theme() (single place)
# ✅ Called inject_global_theme() once at the top (applies to ALL pages)
# ✅ Keeps your existing routing + safe imports
# ✅ Keeps your Minutes & Attendance upgraded page
#
# IMPORTANT:
# - After this change, you can REMOVE theme injection from dashboard_panel.py if you want.
#   (Leaving it is OK, but best is: dashboard_panel should focus on layout, app.py owns global theme.)

from __future__ import annotations

import os
from datetime import date
import streamlit as st
import pandas as pd
from supabase import create_client
from postgrest.exceptions import APIError

from admin_panels import render_admin
from payout import render_payouts
from audit_panel import render_audit
from health_panel import render_health

# ✅ Dashboard (upgraded UI)
from dashboard_panel import render_dashboard

# ✅ Optional PDFs (safe)
try:
    from pdfs import make_minutes_pdf, make_attendance_pdf
except Exception:
    make_minutes_pdf = None
    make_attendance_pdf = None

# ✅ Loans UI (safe import + error capture)
loans_entry = None
loans_import_error = None
try:
    import loans as loans_entry  # noqa: F401
except Exception as e:
    loans_entry = None
    loans_import_error = e

APP_BRAND = "theyoungshallgrow"

st.set_page_config(
    page_title=f"{APP_BRAND} • Bank Dashboard",
    layout="wide",
    page_icon="🏦",
)

# ============================================================
# ✅ GLOBAL THEME (applies to the whole app, all pages)
# This is the "color like your dashboard" + dotted grid background.
# ============================================================
def inject_global_theme():
    st.markdown(
        """
        <style>
        /* =======================
           GLOBAL BACKGROUND
           ======================= */
        .stApp {
            background-color: #0b0f1a;
            background-image:
                radial-gradient(circle at 1px 1px, rgba(255,255,255,0.06) 1px, transparent 0);
            background-size: 24px 24px;
            color: #e5e7eb;
        }

        /* Hide default header bar background */
        header, footer { background: transparent !important; }

        /* =======================
           SIDEBAR
           ======================= */
        section[data-testid="stSidebar"]{
            background: #0b0f1a;
            border-right: 1px solid rgba(255,255,255,0.06);
        }

        /* =======================
           TEXT / TYPOGRAPHY
           ======================= */
        h1, h2, h3, h4, h5, h6, p, div, span, label, small {
            color: #e5e7eb !important;
        }

        /* Make captions a bit softer */
        .stCaption, [data-testid="stCaptionContainer"] {
            color: rgba(229,231,235,0.70) !important;
        }

        /* =======================
           CONTAINERS (cards)
           ======================= */
        .glass {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 18px;
            padding: 18px 18px;
            box-shadow: 0 14px 45px rgba(0,0,0,0.45);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
        }

        /* Buttons */
        .stButton button, .stDownloadButton button {
            border-radius: 14px !important;
            border: 1px solid rgba(255,255,255,0.10) !important;
            background: rgba(255,255,255,0.04) !important;
            color: #e5e7eb !important;
        }
        .stButton button:hover, .stDownloadButton button:hover {
            border: 1px solid rgba(255,255,255,0.20) !important;
            background: rgba(255,255,255,0.06) !important;
        }

        /* Inputs */
        input, textarea {
            background: rgba(255,255,255,0.03) !important;
            border: 1px solid rgba(255,255,255,0.08) !important;
            color: #e5e7eb !important;
            border-radius: 12px !important;
        }

        /* Selectbox / multiselect containers */
        [data-baseweb="select"] > div {
            background: rgba(255,255,255,0.03) !important;
            border: 1px solid rgba(255,255,255,0.08) !important;
            color: #e5e7eb !important;
            border-radius: 12px !important;
        }

        /* Dataframes */
        div[data-testid="stDataFrame"]{
            border-radius: 14px;
            overflow: hidden;
            border: 1px solid rgba(255,255,255,0.06);
        }

        /* Alerts */
        [data-testid="stAlert"]{
            border-radius: 14px;
            border: 1px solid rgba(255,255,255,0.08);
            background: rgba(255,255,255,0.04);
        }

        /* Metrics (Streamlit default metric styling improvement) */
        div[data-testid="stMetric"]{
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 16px;
            padding: 12px 14px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def glass_open() -> str:
    return "<div class='glass'>"


def glass_close() -> str:
    return "</div>"


# ✅ Apply theme ONCE (global)
inject_global_theme()

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
with left:
    st.markdown(f"## 🏦 {APP_BRAND} • Bank Dashboard")
with right:
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()

# ============================================================
# SAFE QUERY HELPER (used by Minutes & Attendance page)
# ✅ supports filters like meeting_date=...
# ============================================================
def safe_select(
    client,
    table_name: str,
    select_cols: str = "*",
    schema: str = "public",
    order_by: str | None = None,
    order_desc: bool = False,
    limit: int | None = None,
    **filters,
) -> list[dict]:
    try:
        q = client.schema(schema).table(table_name).select(select_cols)

        for col, val in (filters or {}).items():
            if val is None:
                continue
            q = q.eq(col, val)

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
        st.code(repr(e), language="text")
        return []


def get_dashboard_next(sb, schema: str) -> dict:
    rows = safe_select(sb, "dashboard_next_view", "*", schema=schema, limit=1)
    return rows[0] if rows else {}


# ============================================================
# LOADERS (Minutes & Attendance)
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
# NAVIGATION
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
# DASHBOARD
# ============================================================
if page == "Dashboard":
    # dashboard_panel can still have its own cards,
    # but global background/theme already applied here.
    render_dashboard(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)

# ============================================================
# CONTRIBUTIONS
# ============================================================
elif page == "Contributions":
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Contributions (View)")
    df = load_contributions_view(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)
    if df.empty:
        st.info("No contributions found (or view not readable).")
        st.caption("Confirm contributions_with_member exists and GRANT SELECT to anon.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

# ============================================================
# PAYOUTS
# ============================================================
elif page == "Payouts":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in Railway Variables / Secrets.")
    else:
        # payouts page will now inherit the same dark theme
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
            if loans_import_error is not None:
                st.caption("Import error detail:")
                st.code(repr(loans_import_error), language="text")
            st.caption("Fix the error in loans.py (or its dependencies) and redeploy.")
        else:
            loans_fn = getattr(loans_entry, "show_loans", None) or getattr(loans_entry, "render_loans", None)
            if loans_fn is None:
                st.error("Loans UI not available. loans.py must define show_loans() or render_loans().")
            else:
                loans_fn(sb_service, SUPABASE_SCHEMA, actor_user_id="admin")

# ============================================================
# Minutes & Attendance (Legacy) — upgraded
# ============================================================
elif page == "Minutes & Attendance":
    st.subheader("📝 Meeting Minutes & ✅ Attendance (Legacy)")

    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY to enable writing minutes & attendance.")
        st.stop()

    with st.sidebar.expander("🔐 Role (Minutes/Attendance)", expanded=False):
        role = st.selectbox("Role", ["admin", "treasury", "member"], index=0, key="ma_role")
    can_write = role in ("admin", "treasury")

    labels, label_to_id, label_to_name, df_members = load_members_legacy(
        SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA
    )

    dash = get_dashboard_next(sb_anon, SUPABASE_SCHEMA)
    current_session_number = dash.get("session_number")

    tab1, tab2, tab3 = st.tabs(["Minutes / Documentation", "Attendance", "Summaries"])

    # --------------------------
    # MINUTES
    # --------------------------
    with tab1:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Meeting Minutes / Documentation (Legacy)")
        st.caption(f"Linked session #: {current_session_number if current_session_number is not None else '—'}")

        if can_write:
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
                        "session_number": int(current_session_number) if current_session_number is not None else None,
                        "title": title.strip(),
                        "content": content.strip(),
                        "tags": tags.strip() or None,
                        "created_by": role,
                    }
                    payload = {k: v for k, v in payload.items() if v is not None}
                    try:
                        sb_service.schema(SUPABASE_SCHEMA).table("meeting_minutes_legacy").insert(payload).execute()
                        st.success("Minutes saved.")
                    except Exception as e:
                        st.error("Failed to save minutes.")
                        st.exception(e)
        else:
            st.info("Read-only: switch role to Admin/Treasury to write minutes.")

        st.divider()
        st.markdown("### Recent minutes")
        rows = (
            sb_service.schema(SUPABASE_SCHEMA).table("meeting_minutes_legacy")
            .select("*")
            .order("meeting_date", desc=True)
            .limit(50)
            .execute().data
            or []
        )
        dfm = pd.DataFrame(rows)
        if dfm.empty:
            st.info("No minutes recorded yet.")
        else:
            st.dataframe(dfm, use_container_width=True, hide_index=True)

            if make_minutes_pdf is not None and "id" in dfm.columns:
                pick_id = st.selectbox("Export minutes PDF (pick id)", dfm["id"].tolist(), key="minutes_pdf_pick")
                row = dfm[dfm["id"] == pick_id].iloc[0].to_dict()
                pdf_bytes = make_minutes_pdf(APP_BRAND, row)
                st.download_button(
                    "⬇️ Download Minutes (PDF)",
                    pdf_bytes,
                    file_name=f"minutes_{row.get('meeting_date')}_session_{row.get('session_number','')}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key="dl_minutes_pdf",
                )
            elif make_minutes_pdf is None:
                st.caption("Minutes PDF export not available (add make_minutes_pdf to pdfs.py).")

        st.markdown(glass_close(), unsafe_allow_html=True)

    # --------------------------
    # ATTENDANCE + BULK
    # --------------------------
    with tab2:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Attendance (Legacy)")
        st.caption(f"Linked session #: {current_session_number if current_session_number is not None else '—'}")

        adate = st.date_input("Attendance date", value=date.today(), key="att_legacy_date")

        st.markdown("### ⚡ Bulk tools")
        if can_write:
            if st.button("✅ Mark ALL members PRESENT for this date", use_container_width=True, key="mark_all_present"):
                if df_members.empty:
                    st.error("members_legacy is empty; cannot bulk mark.")
                else:
                    payloads = []
                    for _, r in df_members.iterrows():
                        payloads.append({
                            "meeting_date": str(adate),
                            "session_number": int(current_session_number) if current_session_number is not None else None,
                            "legacy_member_id": int(r["id"]),
                            "member_name": str(r["name"]),
                            "status": "present",
                            "note": None,
                            "created_by": role,
                        })
                    payloads = [{k: v for k, v in p.items() if v is not None} for p in payloads]
                    try:
                        sb_service.schema(SUPABASE_SCHEMA).table("meeting_attendance_legacy").upsert(payloads).execute()
                        st.success("All members marked present (upserted).")
                    except Exception as e:
                        st.error("Bulk upsert failed.")
                        st.exception(e)
        else:
            st.info("Read-only: switch role to Admin/Treasury to write attendance.")

        st.divider()
        st.markdown("### Single entry")
        if can_write:
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
                        "session_number": int(current_session_number) if current_session_number is not None else None,
                        "legacy_member_id": int(legacy_member_id),
                        "member_name": member_name,
                        "status": status,
                        "note": note.strip() or None,
                        "created_by": role,
                    }
                    payload = {k: v for k, v in payload.items() if v is not None}
                    try:
                        sb_service.schema(SUPABASE_SCHEMA).table("meeting_attendance_legacy").insert(payload).execute()
                        st.success("Attendance saved.")
                    except Exception as e:
                        st.error("Failed to save attendance.")
                        st.exception(e)

        st.divider()
        st.markdown("### Attendance for selected date")
        rows = (
            sb_service.schema(SUPABASE_SCHEMA).table("meeting_attendance_legacy")
            .select("*")
            .eq("meeting_date", str(adate))
            .order("legacy_member_id", desc=False)
            .limit(2000)
            .execute().data
            or []
        )
        dfa = pd.DataFrame(rows)
        if dfa.empty:
            st.info("No attendance recorded for this date yet.")
        else:
            st.dataframe(dfa, use_container_width=True, hide_index=True)

            if make_attendance_pdf is not None:
                pdf_bytes = make_attendance_pdf(
                    APP_BRAND,
                    meeting_date=str(adate),
                    session_number=(int(current_session_number) if current_session_number is not None else None),
                    attendance_rows=dfa.to_dict(orient="records"),
                )
                st.download_button(
                    "⬇️ Download Attendance Sheet (PDF)",
                    pdf_bytes,
                    file_name=f"attendance_{str(adate)}_session_{current_session_number or ''}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key="dl_attendance_pdf",
                )
            else:
                st.caption("Attendance PDF export not available (add make_attendance_pdf to pdfs.py).")

        st.markdown(glass_close(), unsafe_allow_html=True)

    # --------------------------
    # SUMMARIES
    # --------------------------
    with tab3:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Attendance Summaries")

        st.markdown("### Daily summary (latest 120)")
        try:
            rows = (
                sb_service.schema(SUPABASE_SCHEMA).table("v_attendance_daily_summary")
                .select("*")
                .order("meeting_date", desc=True)
                .limit(120)
                .execute().data
                or []
            )
            dfd = pd.DataFrame(rows)
            if dfd.empty:
                st.info("No daily summary yet.")
            else:
                dfd["present_count"] = pd.to_numeric(dfd.get("present_count"), errors="coerce").fillna(0)
                dfd["total_marked"] = pd.to_numeric(dfd.get("total_marked"), errors="coerce").fillna(0)
                dfd["present_pct"] = dfd.apply(
                    lambda r: (float(r["present_count"]) / float(r["total_marked"]) * 100.0)
                    if float(r["total_marked"]) > 0 else 0.0,
                    axis=1,
                )
                st.dataframe(dfd, use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning("Could not load v_attendance_daily_summary (create the SQL view).")
            st.exception(e)

        st.divider()
        st.markdown("### Member summary")
        try:
            rows = (
                sb_service.schema(SUPABASE_SCHEMA).table("v_attendance_member_summary")
                .select("*")
                .order("legacy_member_id", desc=False)
                .limit(2000)
                .execute().data
                or []
            )
            dfms = pd.DataFrame(rows)
            if dfms.empty:
                st.info("No member summary yet.")
            else:
                st.dataframe(dfms, use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning("Could not load v_attendance_member_summary (create the SQL view).")
            st.exception(e)

        st.markdown(glass_close(), unsafe_allow_html=True)

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
