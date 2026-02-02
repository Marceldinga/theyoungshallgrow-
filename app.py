
# app.py ✅ COMPLETE SINGLE CODE — NJANGI STANDARD (NO legacy)
# ✅ Built to STOP Streamlit blank-screen crashes:
#    - ALL optional modules are lazy-imported inside page blocks (no import-time crash)
#    - Secrets/env validated with visible errors
#    - Service key optional (writes disabled if missing)
#    - Safe Mode switch to run Dashboard-only
#
# ✅ Uses NEW tables/views only:
#   tables: members, sessions, app_state, minutes, attendance, contributions, foundation_contributions,
#           payouts, loans, loan_payments, fines, interest_ledger, audit_log
#   views (optional): v_next_beneficiary, v_contributions_with_member, v_attendance_with_member
#
# ✅ Dashboard: delegates to dashboard_panel.render_dashboard (no duplicate header)
# ✅ Minutes & Attendance:
#    - Attendance save = delete session rows then insert (prevents duplicates)
#    - Minutes = update if session exists else insert
#    - Summaries = minutes + attendance + contributions
#
# NOTE: This file does NOT reference any "legacy" tables.

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

import pandas as pd
import streamlit as st
from postgrest.exceptions import APIError
from supabase import create_client

from dashboard_panel import render_dashboard

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
# SECRETS / ENV
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
    st.error(
        "Missing SUPABASE_URL or SUPABASE_ANON_KEY.\n\n"
        "If you are on Streamlit Cloud: Manage app → Settings → Secrets.\n"
        "Add:\n"
        "SUPABASE_URL\nSUPABASE_ANON_KEY\n(optional) SUPABASE_SERVICE_KEY\nSUPABASE_SCHEMA"
    )
    st.stop()

if not SUPABASE_SERVICE_KEY:
    st.warning("SUPABASE_SERVICE_KEY not set. Writes (Admin/Loans/Payouts/Minutes/Attendance) may be disabled.")


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
# SAFE ERROR TEXT
# ============================================================
def _api_msg(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or "APIError")
        return str(e)
    return repr(e)


def table_readable(client, schema: str, table_name: str) -> bool:
    try:
        client.schema(schema).table(table_name).select("*").limit(1).execute()
        return True
    except Exception:
        return False


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
            q = q.limit(int(limit))
        return (q.execute().data or [])
    except Exception as e:
        st.error(f"Error reading {schema}.{table_name}")
        st.code(_api_msg(e), language="text")
        return []


# ============================================================
# LAZY IMPORT HELPER (PREVENTS BLACK SCREEN)
# ============================================================
def lazy_import(path: str, attr: str | None = None) -> tuple[Any | None, str | None]:
    """
    Returns (obj, error_text). Never raises.
    """
    try:
        mod = __import__(path, fromlist=["*"])
        if attr:
            return getattr(mod, attr), None
        return mod, None
    except Exception as e:
        return None, repr(e)


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
# SIDEBAR SAFE MODE
# ============================================================
with st.sidebar.expander("🛟 Safe Mode", expanded=False):
    SAFE_MODE = st.checkbox(
        "Run Dashboard only (disable optional pages)",
        value=False,
        help="Use this if Streamlit Cloud shows a blank screen; it avoids importing other modules.",
    )


# ============================================================
# CACHED LOADERS
# ============================================================
@st.cache_data(ttl=90)
def load_members(url: str, anon_key: str, schema: str) -> pd.DataFrame:
    client = create_client(url, anon_key)
    rows = (
        client.schema(schema)
        .table("members")
        .select("id,name,display_name,phone")
        .order("id", desc=False)
        .limit(5000)
        .execute()
        .data
        or []
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["id", "name", "display_name", "phone", "member_name", "label"])
    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    df = df[df["id"] > 0].copy()
    df["name"] = df["name"].astype(str)
    df["display_name"] = df.get("display_name", "").astype(str).replace({"None": "", "nan": ""})
    df["phone"] = df.get("phone", "").astype(str).replace({"None": "", "nan": ""})
    df["member_name"] = df["display_name"].where(df["display_name"].str.strip() != "", df["name"])
    df["label"] = df.apply(lambda r: f"{int(r['id']):02d} • {r['member_name']}", axis=1)
    return df


@st.cache_data(ttl=60)
def load_contributions_view(url: str, anon_key: str, schema: str) -> pd.DataFrame:
    client = create_client(url, anon_key)
    try:
        rows = (
            client.schema(schema)
            .table("v_contributions_with_member")
            .select("id,member_id,member_name,session_id,amount,paid_at,note,created_at")
            .order("created_at", desc=True)
            .limit(1000)
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


# ============================================================
# SESSION HELPERS (Minutes/Attendance)
# ============================================================
def get_app_state(sb, schema: str) -> dict:
    rows = safe_select(sb, "app_state", "*", schema=schema, limit=1, id=1)
    if rows:
        return rows[0]
    rows2 = safe_select(sb, "app_state", "*", schema=schema, limit=1)
    return rows2[0] if rows2 else {}


def get_effective_session_id(sb_read, schema: str) -> tuple[int | None, str]:
    """
    Returns (session_id, note): from app_state OR fallback to latest session.
    """
    state = get_app_state(sb_read, schema)
    raw = state.get("current_session_id")
    try:
        cs = int(raw) if raw is not None and str(raw).strip() != "" else None
    except Exception:
        cs = None

    if cs is not None:
        return cs, "from app_state"

    srows = safe_select(
        sb_read,
        "sessions",
        "session_id,start_date,end_date,created_at",
        schema=schema,
        order_by="session_id",
        order_desc=True,
        limit=1,
    )
    if srows:
        try:
            return int(srows[0].get("session_id")), "fallback: latest session"
        except Exception:
            return None, "fallback failed"
    return None, "no sessions"


# ============================================================
# NAVIGATION
# ============================================================
if SAFE_MODE:
    PAGES = ["Dashboard"]
else:
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
    # Dashboard panel must NOT render big header (app.py owns it)
    render_dashboard(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)


elif page == "Contributions":
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.subheader("Contributions")
    st.caption("Stored by member_id. Names shown via view if available.")

    df = load_contributions_view(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)
    if df.empty:
        # fallback: show raw contributions without names
        rows = safe_select(sb_anon, "contributions", "*", schema=SUPABASE_SCHEMA, order_by="created_at", order_desc=True, limit=500)
        df2 = pd.DataFrame(rows)
        if df2.empty:
            st.info("No contributions found.")
        else:
            st.warning("View v_contributions_with_member not readable. Showing raw contributions.")
            st.dataframe(df2, use_container_width=True, hide_index=True)
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown(glass_close(), unsafe_allow_html=True)


elif page == "Payouts":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY to enable payout writes.")
        st.stop()

    payout_fn, payout_err = lazy_import("payout", "render_payouts")
    if payout_fn is None:
        st.error("Payout module failed to load.")
        st.code(payout_err or "", language="text")
    else:
        payout_fn(sb_service, SUPABASE_SCHEMA)


elif page == "Loans":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY to enable loans writes.")
        st.stop()

    needed = ["members", "loans", "loan_payments", "loan_requests", "signatures", "interest_ledger"]
    missing = [t for t in needed if not table_readable(sb_service, SUPABASE_SCHEMA, t)]
    if missing:
        st.error("Loans module is not ready — missing required table(s):")
        st.write(", ".join([f"{SUPABASE_SCHEMA}.{t}" for t in missing]))
        st.stop()

    loans_mod, loans_err = lazy_import("loans", None)  # expects show_loans or render_loans
    if loans_mod is None:
        st.error("Loans module failed to import.")
        st.code(loans_err or "", language="text")
    else:
        loans_fn = getattr(loans_mod, "show_loans", None) or getattr(loans_mod, "render_loans", None)
        if loans_fn is None:
            st.error("loans.py must define show_loans() or render_loans().")
        else:
            loans_fn(sb_service, SUPABASE_SCHEMA, actor_user_id="")


elif page == "🤖 AI Risk Panel":
    fn, err = lazy_import("ai_risk_panel", "render_ai_risk_panel")
    if fn is None:
        st.error("AI Risk Panel failed to load.")
        st.code(err or "", language="text")
    else:
        fn(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)


elif page == "Minutes & Attendance":
    st.subheader("📝 Minutes & ✅ Attendance")

    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY to enable writing.")
        st.stop()

    read_for_session = sb_service if sb_service is not None else sb_anon
    current_session_id, session_note = get_effective_session_id(read_for_session, SUPABASE_SCHEMA)

    if current_session_id is None:
        st.error("No sessions found. Create a session first in Admin → Sessions.")
        st.stop()

    if session_note != "from app_state":
        st.warning("app_state.current_session_id is not set. Using latest session as fallback. Set it in Admin → Rotation.")

    with st.sidebar.expander("🔐 Role (Minutes/Attendance)", expanded=False):
        role = st.selectbox("Role", ["admin", "treasury", "member"], index=0, key="ma_role")
    can_write = role in ("admin", "treasury")

    df_members = load_members(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)
    if df_members.empty:
        st.error("No members found in members.")
        st.stop()

    tab1, tab2, tab3 = st.tabs(["Minutes / Documentation", "Attendance", "Summaries"])

    # -------------------------
    # Minutes
    # -------------------------
    with tab1:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Meeting Minutes / Documentation")
        st.caption(f"Linked session_id: {current_session_id}  •  {session_note}")

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
                    try:
                        if existing:
                            mid = int(existing[0]["id"])
                            sb_service.schema(SUPABASE_SCHEMA).table("minutes").update(
                                {"title": title.strip(), "body": body.strip(), "updated_at": now_iso(), "created_by": role}
                            ).eq("id", mid).execute()
                            st.success("Minutes updated.")
                        else:
                            sb_service.schema(SUPABASE_SCHEMA).table("minutes").insert(
                                {
                                    "session_id": int(current_session_id),
                                    "title": title.strip(),
                                    "body": body.strip(),
                                    "created_by": role,
                                    "created_at": now_iso(),
                                    "updated_at": now_iso(),
                                }
                            ).execute()
                            st.success("Minutes saved.")
                        st.rerun()
                    except Exception as e:
                        st.error("Failed to save minutes.")
                        st.code(_api_msg(e), language="text")

        st.divider()
        st.markdown("### Current session minutes")
        rows = safe_select(
            sb_service, "minutes", "*",
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

        st.markdown(glass_close(), unsafe_allow_html=True)

    # -------------------------
    # Attendance
    # -------------------------
    with tab2:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Attendance")
        st.caption(f"Linked session_id: {current_session_id}  •  {session_note}")
        st.caption("Mark each member as Present or Absent. Add a reason/note if needed.")

        attendance_rows: list[dict] = []
        for _, r in df_members.sort_values("id").iterrows():
            mid = int(r["id"])
            name = str(r.get("member_name") or r.get("name") or "")
            label = f"{mid:02d} • {name}"

            c_status, c_note = st.columns([0.42, 0.58])
            with c_status:
                status_key = f"att_status_{mid}_{current_session_id}"
                status = st.radio(label, options=["present", "absent"], index=0, horizontal=True, key=status_key)
            with c_note:
                note_key = f"att_note_{mid}_{current_session_id}"
                note = st.text_input(
                    "Reason / Note",
                    value="",
                    placeholder="e.g., Sick, Travel, Excused…",
                    key=note_key,
                    label_visibility="collapsed",
                )

            attendance_rows.append({"member_id": mid, "present": (status == "present"), "note": note.strip() or None})

        st.divider()
        save = st.button("💾 Save attendance (ALL members)", use_container_width=True)

        if save:
            if not can_write:
                st.warning("Only admin/treasury can save attendance.")
            else:
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

                # delete-then-insert for this session (prevents duplicates)
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

        # Try optional view; fallback to join in python
        if table_readable(sb_anon, SUPABASE_SCHEMA, "v_attendance_with_member"):
            arows = safe_select(
                sb_anon,
                "v_attendance_with_member",
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
        else:
            arows = safe_select(
                sb_anon,
                "attendance",
                "id,member_id,session_id,present,note,created_at",
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
                dm = df_members[["id", "member_name"]].rename(columns={"id": "member_id"})
                dfa["member_id"] = pd.to_numeric(dfa["member_id"], errors="coerce")
                dfa = dfa.merge(dm, on="member_id", how="left")
                dfa = dfa[["member_id", "member_name", "present", "note", "created_at"]]
                st.warning("View v_attendance_with_member not readable. Showing attendance joined in Python.")
                st.dataframe(dfa, use_container_width=True, hide_index=True)

        st.markdown(glass_close(), unsafe_allow_html=True)

    # -------------------------
    # Summaries
    # -------------------------
    with tab3:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.subheader("Summaries")
        st.caption("Summaries for Minutes, Attendance, and Contributions.")

        st.markdown("### 📝 Minutes summary")
        m_rows = safe_select(sb_anon, "minutes", "*", schema=SUPABASE_SCHEMA, order_by="updated_at", order_desc=True, limit=20)
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
                st.write((content[:700] + "…") if len(content) > 700 else content)

        st.divider()
        st.markdown("### ✅ Attendance summary (current session)")
        a_rows = safe_select(
            sb_anon,
            "attendance",
            "id,member_id,session_id,present,created_at",
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
        st.markdown("### 💰 Contributions summary (current session)")
        dfc = load_contributions_view(SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SCHEMA)
        if dfc.empty:
            st.info("No contributions view available. Showing raw contributions.")
            c_rows = safe_select(
                sb_anon,
                "contributions",
                "member_id,session_id,amount,paid_at,created_at",
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
                st.dataframe(dfc, use_container_width=True, hide_index=True)
        else:
            dfc = dfc[dfc["session_id"].astype(str) == str(current_session_id)].copy()
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
        st.stop()

    admin_fn, admin_err = lazy_import("admin_panels", "render_admin")
    if admin_fn is None:
        st.error("Admin panel failed to load.")
        st.code(admin_err or "", language="text")
    else:
        admin_fn(sb_service=sb_service, schema=SUPABASE_SCHEMA, actor_email="admin@yourorg.com")


elif page == "Audit":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY.")
        st.stop()

    audit_fn, audit_err = lazy_import("audit_panel", "render_audit")
    if audit_fn is None:
        st.error("Audit panel failed to load.")
        st.code(audit_err or "", language="text")
    else:
        audit_fn(sb_service=sb_service, schema=SUPABASE_SCHEMA)


elif page == "Health":
    health_fn, health_err = lazy_import("health_panel", "render_health")
    if health_fn is None:
        st.error("Health panel failed to load.")
        st.code(health_err or "", language="text")
    else:
        health_fn(sb_anon=sb_anon, sb_service=sb_service, schema=SUPABASE_SCHEMA)
