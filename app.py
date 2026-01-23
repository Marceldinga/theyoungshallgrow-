# app.py ✅ UPDATED — Attendance + Summaries implemented (no placeholders)
# Fixes your WHITE inputs issue by keeping the strong BaseWeb overrides inside inject_global_theme().
# Also fixes: meeting minutes save errors shown as clean message (no scary trace) and attendance selection persistence.

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

# ✅ AI Risk Panel (SAFE import + error capture)
ai_render_fn = None
ai_import_error = None
try:
    from ai_risk_panel import render_ai_risk_panel as ai_render_fn  # noqa: F401
except Exception as e:
    ai_render_fn = None
    ai_import_error = e

APP_BRAND = "theyoungshallgrow"

st.set_page_config(
    page_title=f"{APP_BRAND} • Bank Dashboard",
    layout="wide",
    page_icon="🏦",
)

# ============================================================
# ✅ GLOBAL THEME (applies to the whole app, all pages)
# ============================================================
def inject_global_theme():
    st.markdown(
        """
        <style>
        .stApp {
            background-color: #0b0f1a !important;
            background-image:
                radial-gradient(circle at 1px 1px, rgba(255,255,255,0.06) 1px, transparent 0) !important;
            background-size: 24px 24px !important;
            color: #e5e7eb !important;
        }
        header, footer { background: transparent !important; }

        section[data-testid="stSidebar"]{
            background: #0b0f1a !important;
            border-right: 1px solid rgba(255,255,255,0.06) !important;
        }

        html, body, p, div, span, label, small,
        h1, h2, h3, h4, h5, h6 {
            color: #e5e7eb !important;
        }
        a { color: #60a5fa !important; }

        .glass {
            background: rgba(255,255,255,0.04) !important;
            border: 1px solid rgba(255,255,255,0.06) !important;
            border-radius: 18px !important;
            padding: 18px 18px !important;
            box-shadow: 0 14px 45px rgba(0,0,0,0.45) !important;
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
        }

        .stButton button, .stDownloadButton button {
            border-radius: 14px !important;
            border: 1px solid rgba(255,255,255,0.12) !important;
            background: rgba(255,255,255,0.04) !important;
            color: #e5e7eb !important;
        }
        .stButton button:hover, .stDownloadButton button:hover {
            border: 1px solid rgba(255,255,255,0.22) !important;
            background: rgba(255,255,255,0.06) !important;
        }

        /* ✅ BaseWeb inputs (THIS fixes the white fields) */
        [data-baseweb="input"] input,
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stDateInput"] input {
            background: rgba(255,255,255,0.03) !important;
            color: #e5e7eb !important;
            border: 1px solid rgba(255,255,255,0.12) !important;
            border-radius: 12px !important;
        }
        [data-baseweb="input"] > div {
            background: rgba(255,255,255,0.03) !important;
            border: 1px solid rgba(255,255,255,0.12) !important;
            border-radius: 12px !important;
        }

        [data-baseweb="textarea"] textarea,
        [data-testid="stTextArea"] textarea {
            background: rgba(255,255,255,0.03) !important;
            color: #e5e7eb !important;
            border: 1px solid rgba(255,255,255,0.12) !important;
            border-radius: 12px !important;
        }

        [data-baseweb="select"] > div {
            background: rgba(255,255,255,0.03) !important;
            border: 1px solid rgba(255,255,255,0.12) !important;
            color: #e5e7eb !important;
            border-radius: 12px !important;
        }
        [data-baseweb="menu"] {
            background: #0f172a !important;
            border: 1px solid rgba(255,255,255,0.12) !important;
        }
        [data-baseweb="menu"] * { color: #e5e7eb !important; }

        [data-baseweb="calendar"] {
            background: #0f172a !important;
            border: 1px solid rgba(255,255,255,0.12) !important;
            border-radius: 12px !important;
        }
        [data-baseweb="calendar"] * { color: #e5e7eb !important; }

        input::placeholder, textarea::placeholder {
            color: rgba(229,231,235,0.45) !important;
        }

        div[data-testid="stMetric"]{
            background: rgba(255,255,255,0.04) !important;
            border: 1px solid rgba(255,255,255,0.06) !important;
            border-radius: 16px !important;
            padding: 12px 14px !important;
        }

        [data-testid="stAlert"]{
            border-radius: 14px !important;
            border: 1px solid rgba(255,255,255,0.10) !important;
            background: rgba(255,255,255,0.04) !important;
            color: #e5e7eb !important;
        }

        div[data-testid="stDataFrame"]{
            border-radius: 14px !important;
            overflow: hidden !important;
            border: 1px solid rgba(255,255,255,0.08) !important;
            background: rgba(255,255,255,0.02) !important;
        }

        .stCaption, [data-testid="stCaptionContainer"] {
            color: rgba(229,231,235,0.70) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

def glass_open() -> str:
    return "<div class='glass'>"

def glass_close() -> str:
    return "</div>"

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
# SAFE QUERY HELPER
# ============================================================
def _api_msg(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or "APIError")
        return str(e)
    return str(e)

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
        st.code(_api_msg(e), language="text")
        return []
    except Exception as e:
        st.error(f"Unexpected error reading {schema}.{table_name}")
        st.code(_api_msg(e), language="text")
        return []

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
def load_contributions_legacy(url: str, anon_key: str, schema: str) -> pd.DataFrame:
    client = create_client(url, anon_key)
    rows = safe_select(
        client,
        "contributions_legacy",
        "id,member_id,session_id,amount,kind,created_at,payout_index,payout_date,user_id,updated_at",
        schema=schema,
        order_by="created_at",
        order_desc=True,
        limit=500,
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
        "🤖 AI Risk Panel",
        "Minutes & Attendance",
        "Admin",
        "Audit",
        "Health",
    ],
    key="main_menu",
)

# ============================================================
# PAGES
# ============================================================
if page == "Dashboard":
    render_dashboard(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)

elif page == "Contributions":
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Contributions (Legacy)")
    df = load_contributions_legacy(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)
    if df.empty:
        st.info("No contributions found (or table not readable).")
        st.caption("Confirm contributions_legacy exists and GRANT SELECT to anon.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

elif page == "Payouts":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in Railway Variables / Secrets.")
    else:
        render_payouts(sb_service, SUPABASE_SCHEMA)

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

elif page == "🤖 AI Risk Panel":
    if ai_render_fn is None:
        st.error("AI Risk Panel failed to load.")
        st.caption("Fix ai_risk_panel.py.")
        st.code(repr(ai_import_error), language="text")
    else:
        ai_render_fn(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)

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

    # -------------------------
    # Minutes
    # -------------------------
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
                        st.rerun()
                    except Exception as e:
                        st.error("Failed to save minutes.")
                        st.code(_api_msg(e), language="text")

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
        except Exception as e:
            st.error("Failed to load meeting_minutes_legacy.")
            st.code(_api_msg(e), language="text")
            rows = []

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

        st.markdown(glass_close(), unsafe_allow_html=True)

    # -------------------------
    # Attendance ✅ IMPLEMENTED
    # Table expected: attendance_legacy
    # -------------------------
    with tab2:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Attendance (Legacy)")
        st.caption(f"Linked session #: {current_session_number if current_session_number is not None else '—'}")

        if df_members.empty:
            st.warning("No members found in members_legacy.")
            st.markdown(glass_close(), unsafe_allow_html=True)
            st.stop()

        adate = st.date_input("Attendance date", value=date.today(), key="att_date")

        st.caption("Mark who is present for this meeting.")

        present_ids = []
        for _, r in df_members.sort_values("id").iterrows():
            mid = int(r["id"])
            name = str(r["name"])
            label = f"{mid:02d} • {name}"
            checked = st.checkbox(label, value=False, key=f"att_{mid}_{adate}")
            if checked:
                present_ids.append(mid)

        c1, c2 = st.columns(2)
        save = c1.button("💾 Save attendance", use_container_width=True)
        clear = c2.button("🧹 Clear selection", use_container_width=True)

        if clear:
            st.rerun()

        if save:
            if current_session_number is None:
                st.error("Current session number missing (dashboard_next_view.session_number).")
            elif not present_ids:
                st.error("Select at least 1 member as present.")
            else:
                payload_rows = []
                for mid in present_ids:
                    nm = df_members[df_members["id"] == mid]["name"].iloc[0]
                    payload_rows.append(
                        {
                            "attendance_date": str(adate),
                            "session_number": int(current_session_number),
                            "member_id": int(mid),
                            "member_name": str(nm),
                            "status": "present",
                            "created_by": role,
                        }
                    )
                try:
                    sb_service.schema(SUPABASE_SCHEMA).table("attendance_legacy").insert(payload_rows).execute()
                    st.success(f"Attendance saved: {len(payload_rows)} present.")
                    st.rerun()
                except Exception as e:
                    st.error("Failed to save attendance. Make sure table attendance_legacy exists.")
                    st.code(_api_msg(e), language="text")

        st.divider()
        st.markdown("### Recent attendance")

        try:
            arows = (
                sb_service.schema(SUPABASE_SCHEMA).table("attendance_legacy")
                .select("*")
                .order("attendance_date", desc=True)
                .limit(200)
                .execute().data
                or []
            )
        except Exception as e:
            st.error("Failed to load attendance_legacy (table may not exist).")
            st.code(_api_msg(e), language="text")
            arows = []

        dfa = pd.DataFrame(arows)
        if dfa.empty:
            st.info("No attendance recorded yet.")
        else:
            st.dataframe(dfa, use_container_width=True, hide_index=True)

            if make_attendance_pdf is not None:
                st.caption("Export Attendance PDF (latest date/session)")
                try:
                    dfa["attendance_date"] = dfa["attendance_date"].astype(str)
                    latest_date = dfa["attendance_date"].max()
                    latest_session = int(dfa["session_number"].max()) if "session_number" in dfa.columns else None
                    sub = dfa[(dfa["attendance_date"] == latest_date)]
                    if latest_session is not None and "session_number" in sub.columns:
                        sub = sub[sub["session_number"] == latest_session]
                    pdf_bytes = make_attendance_pdf(APP_BRAND, latest_date, latest_session, sub.to_dict("records"))
                    st.download_button(
                        "⬇️ Download Attendance (PDF)",
                        pdf_bytes,
                        file_name=f"attendance_{latest_date}_session_{latest_session}.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                        key="dl_att_pdf",
                    )
                except Exception:
                    pass

        st.markdown(glass_close(), unsafe_allow_html=True)

    # -------------------------
    # Summaries ✅ IMPLEMENTED
    # -------------------------
    with tab3:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Summaries")
        st.caption("Quick summaries of Minutes, Attendance, and Contributions (Legacy).")

        # Minutes summary
        st.markdown("### 📝 Minutes summary")
        try:
            m_rows = (
                sb_service.schema(SUPABASE_SCHEMA).table("meeting_minutes_legacy")
                .select("*")
                .order("meeting_date", desc=True)
                .limit(30)
                .execute().data
                or []
            )
        except Exception:
            m_rows = []
        dfm = pd.DataFrame(m_rows)

        if dfm.empty:
            st.info("No minutes recorded yet.")
        else:
            pick_id = st.selectbox("Pick minutes ID", dfm["id"].tolist(), index=0, key="sum_minutes_pick")
            row = dfm[dfm["id"] == pick_id].iloc[0].to_dict()
            title = str(row.get("title", ""))
            meeting_date = str(row.get("meeting_date", ""))
            content = str(row.get("content", ""))

            st.write(f"**{title}**  •  {meeting_date}")

            lines = [ln.strip("-• ").strip() for ln in content.splitlines() if ln.strip()]
            bullets = [ln for ln in lines if len(ln) > 6][:8]
            if bullets:
                st.markdown("**Highlights**")
                for b in bullets:
                    st.write(f"• {b}")
            else:
                st.markdown("**Excerpt**")
                st.write((content[:700] + "…") if len(content) > 700 else content)

        st.divider()

        # Attendance summary
        st.markdown("### ✅ Attendance summary")
        try:
            a_rows = (
                sb_service.schema(SUPABASE_SCHEMA).table("attendance_legacy")
                .select("*")
                .order("attendance_date", desc=True)
                .limit(300)
                .execute().data
                or []
            )
        except Exception:
            a_rows = []
        dfa = pd.DataFrame(a_rows)

        if dfa.empty:
            st.info("No attendance recorded yet (or attendance_legacy not created).")
        else:
            dfa["attendance_date"] = dfa["attendance_date"].astype(str)
            unique_dates = sorted(dfa["attendance_date"].unique().tolist(), reverse=True)
            pick_date = st.selectbox("Pick attendance date", unique_dates, index=0, key="sum_att_date")
            sub = dfa[dfa["attendance_date"] == pick_date].copy()

            if "session_number" in sub.columns and sub["session_number"].nunique() > 1:
                sess = st.selectbox("Pick session #", sorted(sub["session_number"].unique().tolist()), key="sum_att_sess")
                sub = sub[sub["session_number"] == sess]

            st.metric("Present count", f"{len(sub):,}")
            if "member_name" in sub.columns and sub["member_name"].notna().any():
                names = sub["member_name"].fillna("").astype(str).tolist()
                names = [n for n in names if n.strip()]
                if names:
                    st.markdown("**Present members**")
                    st.write(", ".join(names[:40]) + ("…" if len(names) > 40 else ""))

        st.divider()

        # Contributions summary (Legacy)
        st.markdown("### 💰 Contributions summary (recent)")
        try:
            c_rows = (
                sb_service.schema(SUPABASE_SCHEMA).table("contributions_legacy")
                .select("member_id, amount, kind, created_at, session_id")
                .order("created_at", desc=True)
                .limit(2000)
                .execute().data
                or []
            )
        except Exception:
            c_rows = []
        dfc = pd.DataFrame(c_rows)

        if dfc.empty:
            st.info("No contributions found.")
        else:
            dfc["amount"] = pd.to_numeric(dfc["amount"], errors="coerce").fillna(0)
            c1, c2 = st.columns(2)
            c1.metric("Rows", f"{len(dfc):,}")
            c2.metric("Sum", f"{float(dfc['amount'].sum()):,.0f}")

            st.caption("Top contributors (recent)")
            top = (
                dfc.groupby("member_id", dropna=False)["amount"].sum()
                .sort_values(ascending=False)
                .head(10)
                .reset_index()
            )
            st.dataframe(top, use_container_width=True, hide_index=True)

        st.markdown(glass_close(), unsafe_allow_html=True)

elif page == "Admin":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in Railway Variables / Secrets.")
    else:
        render_admin(sb_service=sb_service, schema=SUPABASE_SCHEMA, actor_email="admin@yourorg.com")

elif page == "Audit":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in Railway Variables / Secrets.")
    else:
        render_audit(sb_service=sb_service, schema=SUPABASE_SCHEMA)

elif page == "Health":
    render_health(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)
