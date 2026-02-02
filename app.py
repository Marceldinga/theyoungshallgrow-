
# app.py ✅ COMPLETE SINGLE CODE — Minutes + Attendance (Present/Absent + Reason) + Summaries (NO "legacy")
# ✅ NO "legacy" tables referenced in this file
# ✅ Uses NEW tables only:
#    - members
#    - sessions
#    - app_state (current_session_id, next_member_id, updated_at)
#    - minutes
#    - attendance
#    - contributions
#    - foundation_contributions
#    - payouts
#    - loans
#    - loan_payments
#    - audit_log
#    - v_next_beneficiary (optional)
# ✅ Member identity: all transactions use member_id only (names display via views)
#
# ✅ Attendance: saves one row per member per session_id (delete-then-insert to prevent duplicates)
# ✅ Minutes: one record per session_id (update if exists)
# ✅ Summaries: minutes + attendance + contributions (new tables)
# ✅ No duplicate tabs/pages in sidebar
# ✅ Contributions page shows names using v_contributions_with_member (view)

from __future__ import annotations

import os
from datetime import date, datetime, timezone
import streamlit as st
import pandas as pd
from supabase import create_client
from postgrest.exceptions import APIError

from admin_panels import render_admin
from payout import render_payouts
from audit_panel import render_audit
from health_panel import render_health
from dashboard_panel import render_dashboard

# Optional PDFs (safe)
try:
    from pdfs import make_minutes_pdf, make_attendance_pdf
except Exception:
    make_minutes_pdf = None
    make_attendance_pdf = None

# Loans UI (safe import + error capture)
loans_entry = None
loans_import_error = None
try:
    import loans as loans_entry  # noqa: F401
except Exception as e:
    loans_entry = None
    loans_import_error = e

# AI Risk Panel (safe import + error capture)
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
# TIME
# ============================================================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# GLOBAL THEME
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

        /* Inputs */
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

        div[data-testid="stMetric"]{
            background: rgba(255,255,255,0.04) !important;
            border: 1px solid rgba(255,255,255,0.06) !important;
            border-radius: 16px !important;
            padding: 12px 14px !important;
        }

        div[data-testid="stDataFrame"]{
            border-radius: 14px !important;
            overflow: hidden !important;
            border: 1px solid rgba(255,255,255,0.08) !important;
            background: rgba(255,255,255,0.02) !important;
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
# SECRETS
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
    st.error("Missing SUPABASE_URL or SUPABASE_ANON_KEY. Set Variables or Secrets.")
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
# SAFE HELPERS
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
        return (q.execute().data or [])
    except Exception as e:
        st.error(f"Error reading {schema}.{table_name}")
        st.code(_api_msg(e), language="text")
        return []


def get_app_state(sb, schema: str) -> dict:
    rows = safe_select(sb, "app_state", "*", schema=schema, limit=1, id=1)
    return rows[0] if rows else {}


@st.cache_data(ttl=90)
def load_members(url: str, anon_key: str, schema: str) -> tuple[list[str], dict, dict, pd.DataFrame]:
    client = create_client(url, anon_key)
    rows = (
        client.schema(schema)
        .table("members")
        .select("id,name")
        .order("id", desc=False)
        .limit(5000)
        .execute()
        .data
        or []
    )
    df = pd.DataFrame(rows)

    if df.empty:
        return [], {}, {}, pd.DataFrame(columns=["id", "name"])

    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    df = df[df["id"] > 0].copy()
    df["name"] = df["name"].astype(str)
    df["label"] = df.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)

    labels = df["label"].tolist()
    label_to_id = dict(zip(df["label"], df["id"]))
    label_to_name = dict(zip(df["label"], df["name"]))
    return labels, label_to_id, label_to_name, df


@st.cache_data(ttl=60)
def load_contributions(url: str, anon_key: str, schema: str) -> pd.DataFrame:
    """
    ✅ Display contributions with member_name via VIEW.
    Tables still store only member_id.
    Requires: public.v_contributions_with_member
    """
    client = create_client(url, anon_key)
    try:
        rows = (
            client.schema(schema)
            .table("v_contributions_with_member")
            .select("id,member_id,member_name,session_id,amount,paid_at,note,created_at")
            .order("created_at", desc=True)
            .limit(500)
            .execute()
            .data
            or []
        )
    except Exception:
        rows = []
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ============================================================
# NAVIGATION (no duplicates)
# ============================================================
PAGES = [
    "Dashboard",
    "Contributions",
    "Payouts",
    "Loans",
    "🤖 AI Risk Panel",
    "Minutes & Attendance",
    "Admin",
    "Audit",
    "Health",
]

page = st.sidebar.radio("Menu", PAGES, key="main_menu")


# ============================================================
# PAGES
# ============================================================
if page == "Dashboard":
    render_dashboard(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)

elif page == "Contributions":
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Contributions")
    st.caption("One contribution per member per session (stored by member_id; names shown via view).")
    df = load_contributions(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)
    if df.empty:
        st.info("No contributions found (or view not readable).")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

elif page == "Payouts":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY.")
    else:
        render_payouts(sb_service, SUPABASE_SCHEMA)

elif page == "Loans":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY.")
    else:
        if loans_entry is None:
            st.error("Loans UI not available. loans.py failed to import.")
            if loans_import_error is not None:
                st.code(repr(loans_import_error), language="text")
        else:
            loans_fn = getattr(loans_entry, "show_loans", None) or getattr(loans_entry, "render_loans", None)
            if loans_fn is None:
                st.error("Loans UI not available. loans.py must define show_loans() or render_loans().")
            else:
                loans_fn(sb_service, SUPABASE_SCHEMA, actor_user_id="admin")

elif page == "🤖 AI Risk Panel":
    if ai_render_fn is None:
        st.error("AI Risk Panel failed to load.")
        st.code(repr(ai_import_error), language="text")
    else:
        ai_render_fn(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)

elif page == "Minutes & Attendance":
    st.subheader("📝 Minutes & ✅ Attendance")

    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY to enable writing.")
        st.stop()

    # current session from app_state
    state = get_app_state(sb_anon, SUPABASE_SCHEMA)
    current_session_id = state.get("current_session_id")

    if current_session_id is None:
        st.warning("app_state.current_session_id is not set. Set it in Admin → Rotation.")
        st.stop()

    with st.sidebar.expander("🔐 Role (Minutes/Attendance)", expanded=False):
        role = st.selectbox("Role", ["admin", "treasury", "member"], index=0, key="ma_role")
    can_write = role in ("admin", "treasury")

    _, _, _, df_members = load_members(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)

    tab1, tab2, tab3 = st.tabs(["Minutes / Documentation", "Attendance", "Summaries"])

    # -------------------------
    # Minutes
    # -------------------------
    with tab1:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Meeting Minutes / Documentation")
        st.caption(f"Linked session_id: {current_session_id}")

        if can_write:
            with st.form("minutes_form", clear_on_submit=False):
                title = st.text_input("Title", key="minutes_title")
                body = st.text_area("Minutes / Documentation", height=260, key="minutes_body")
                ok = st.form_submit_button("💾 Save minutes", use_container_width=True)

            if ok:
                if not title.strip() or not body.strip():
                    st.error("Title and body are required.")
                else:
                    existing = (
                        sb_service.schema(SUPABASE_SCHEMA)
                        .table("minutes")
                        .select("id,session_id")
                        .eq("session_id", int(current_session_id))
                        .limit(1)
                        .execute()
                        .data
                        or []
                    )
                    if existing:
                        mid = int(existing[0]["id"])
                        try:
                            sb_service.schema(SUPABASE_SCHEMA).table("minutes").update(
                                {
                                    "title": title.strip(),
                                    "body": body.strip(),
                                    "updated_at": now_iso(),
                                    "created_by": role,
                                }
                            ).eq("id", mid).execute()
                            st.success("Minutes updated.")
                            st.rerun()
                        except Exception as e:
                            st.error("Failed to update minutes.")
                            st.code(_api_msg(e), language="text")
                    else:
                        payload = {
                            "session_id": int(current_session_id),
                            "title": title.strip(),
                            "body": body.strip(),
                            "created_by": role,
                            "created_at": now_iso(),
                            "updated_at": now_iso(),
                        }
                        try:
                            sb_service.schema(SUPABASE_SCHEMA).table("minutes").insert(payload).execute()
                            st.success("Minutes saved.")
                            st.rerun()
                        except Exception as e:
                            st.error("Failed to save minutes.")
                            st.code(_api_msg(e), language="text")

        st.divider()
        st.markdown("### Current session minutes")
        rows = safe_select(
            sb_service,
            "minutes",
            "*",
            schema=SUPABASE_SCHEMA,
            order_by="updated_at",
            order_desc=True,
            limit=10,
            session_id=int(current_session_id),
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
                    file_name=f"minutes_session_{row.get('session_id')}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key="dl_minutes_pdf",
                )

        st.markdown(glass_close(), unsafe_allow_html=True)

    # -------------------------
    # Attendance (write member_id only; display via view)
    # -------------------------
    with tab2:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Attendance")
        st.caption(f"Linked session_id: {current_session_id}")

        if df_members.empty:
            st.warning("No members found in members.")
            st.markdown(glass_close(), unsafe_allow_html=True)
            st.stop()

        st.caption("Mark each member as Present or Absent. Add a reason/note if needed (especially for absent).")

        attendance_rows: list[dict] = []
        for _, r in df_members.sort_values("id").iterrows():
            mid = int(r["id"])
            name = str(r["name"])
            label = f"{mid:02d} • {name}"

            c_status, c_note = st.columns([0.42, 0.58])

            with c_status:
                status_key = f"att_status_{mid}_{current_session_id}"
                status = st.radio(
                    label,
                    options=["present", "absent"],
                    index=0,
                    horizontal=True,
                    key=status_key,
                )

            with c_note:
                note_key = f"att_note_{mid}_{current_session_id}"
                note = st.text_input(
                    "Reason / Note",
                    value="",
                    placeholder="e.g., Sick, Travel, Excused…",
                    key=note_key,
                    label_visibility="collapsed",
                )

            attendance_rows.append(
                {
                    "member_id": mid,
                    "present": (status == "present"),
                    "note": note.strip() or None,
                }
            )

        st.divider()
        save = st.button("💾 Save attendance (ALL members)", use_container_width=True)

        if save:
            payload_rows = [
                {
                    "session_id": int(current_session_id),
                    "member_id": int(row["member_id"]),
                    "present": bool(row["present"]),
                    "note": row["note"],
                    "created_at": now_iso(),
                }
                for row in attendance_rows
            ]

            # delete existing attendance for this session then insert full set
            try:
                sb_service.schema(SUPABASE_SCHEMA).table("attendance").delete().eq("session_id", int(current_session_id)).execute()
            except Exception:
                pass

            try:
                sb_service.schema(SUPABASE_SCHEMA).table("attendance").insert(payload_rows).execute()
                present_count = sum(1 for r in payload_rows if r.get("present") is True)
                absent_count = len(payload_rows) - present_count
                st.success(f"Attendance saved ✅ Present: {present_count} • Absent: {absent_count}")
                st.rerun()
            except Exception as e:
                st.error("Failed to save attendance.")
                st.code(_api_msg(e), language="text")

        st.divider()
        st.markdown("### Current session attendance")
        arows = safe_select(
            sb_service,
            "v_attendance_with_member",   # ✅ show member_name via view
            "*",
            schema=SUPABASE_SCHEMA,
            order_by="member_id",
            order_desc=False,
            limit=2000,
            session_id=int(current_session_id),
        )
        dfa = pd.DataFrame(arows)
        if dfa.empty:
            st.info("No attendance recorded for this session yet.")
        else:
            st.dataframe(dfa, use_container_width=True, hide_index=True)

            if make_attendance_pdf is not None:
                try:
                    pdf_bytes = make_attendance_pdf(
                        APP_BRAND,
                        str(date.today()),
                        int(current_session_id),
                        dfa.to_dict("records"),
                    )
                    st.download_button(
                        "⬇️ Download Attendance (PDF)",
                        pdf_bytes,
                        file_name=f"attendance_session_{current_session_id}.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                        key="dl_att_pdf",
                    )
                except Exception:
                    pass

        st.markdown(glass_close(), unsafe_allow_html=True)

    # -------------------------
    # Summaries (show names via views where available)
    # -------------------------
    with tab3:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Summaries")
        st.caption("Summaries for Minutes, Attendance, and Contributions (member_id only; names shown via views).")

        # Minutes summary
        st.markdown("### 📝 Minutes summary")
        m_rows = safe_select(sb_service, "minutes", "*", schema=SUPABASE_SCHEMA, order_by="updated_at", order_desc=True, limit=20)
        dfm = pd.DataFrame(m_rows)
        if dfm.empty:
            st.info("No minutes recorded yet.")
        else:
            pick_id = st.selectbox("Pick minutes ID", dfm["id"].tolist(), index=0, key="sum_minutes_pick")
            row = dfm[dfm["id"] == pick_id].iloc[0].to_dict()
            st.write(f"**{row.get('title','')}**  •  session {row.get('session_id','')}")
            content = str(row.get("body", ""))
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
        a_rows = safe_select(
            sb_service,
            "attendance",
            "*",
            schema=SUPABASE_SCHEMA,
            order_by="created_at",
            order_desc=True,
            limit=2000,
            session_id=int(current_session_id),
        )
        dfa = pd.DataFrame(a_rows)
        if dfa.empty:
            st.info("No attendance for current session.")
        else:
            present_count = int(dfa["present"].astype(bool).sum()) if "present" in dfa.columns else 0
            st.metric("Present count", f"{present_count:,}")
            st.metric("Absent count", f"{(len(dfa)-present_count):,}")

        st.divider()

        # Contributions summary (current session) — show names via view
        st.markdown("### 💰 Contributions summary (current session)")
        c_rows = safe_select(
            sb_service,
            "v_contributions_with_member",
            "member_id,member_name,session_id,amount,paid_at,created_at",
            schema=SUPABASE_SCHEMA,
            order_by="created_at",
            order_desc=True,
            limit=2000,
            session_id=int(current_session_id),
        )
        dfc = pd.DataFrame(c_rows)
        if dfc.empty:
            st.info("No contributions for current session.")
        else:
            dfc["amount"] = pd.to_numeric(dfc["amount"], errors="coerce").fillna(0)
            st.metric("Rows", f"{len(dfc):,}")
            st.metric("Sum", f"{float(dfc['amount'].sum()):,.0f}")
            top = (
                dfc.groupby(["member_id", "member_name"], dropna=False)["amount"].sum()
                .sort_values(ascending=False)
                .head(10)
                .reset_index()
            )
            st.caption("Top contributors (current session)")
            st.dataframe(top, use_container_width=True, hide_index=True)

        st.markdown(glass_close(), unsafe_allow_html=True)

elif page == "Admin":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY.")
    else:
        render_admin(sb_service=sb_service, schema=SUPABASE_SCHEMA, actor_email="admin@yourorg.com")

elif page == "Audit":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY.")
    else:
        render_audit(sb_service=sb_service, schema=SUPABASE_SCHEMA)

elif page == "Health":
    render_health(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)
