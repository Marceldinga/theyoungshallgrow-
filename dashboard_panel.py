# dashboard_panel.py ✅ COMPLETE UPDATED + BEAUTIFUL THEME (dark dotted grid like your image)
# FIXED:
# ✅ Option A enforced: "Current Pot" = CURRENT ACTIVE SESSION contributions ONLY
#    -> No more reading old pot from dashboard_next_view.current_pot
# ✅ Cycle Contributions always shows 0 (not "—") when empty
# ✅ Members Paid defaults to 0 when empty
# ✅ Interest KPI FIXED: shows PAID interest (generated - unpaid) from loans_legacy (ACTIVE loans)
# ✅ Finance view forced to PUBLIC schema (because your views are in public)
#
# Requires:
# - sb_anon: supabase client (anon)
# - sb_service: supabase client (service) or None
# - schema: your schema (default "public")

from __future__ import annotations

import streamlit as st
import pandas as pd


# ============================================================
# THEME (Dark dotted grid + glass cards)
# ============================================================
def inject_dashboard_theme():
    st.markdown(
        """
        <style>
        /* ----- BACKGROUND (dotted grid) ----- */
        .stApp {
            background-color: #0b0f1a;
            background-image:
                radial-gradient(circle at 1px 1px, rgba(255,255,255,0.06) 1px, transparent 0);
            background-size: 24px 24px;
            color: #e5e7eb;
        }

        /* Sidebar */
        section[data-testid="stSidebar"]{
            background: #0b0f1a;
            border-right: 1px solid rgba(255,255,255,0.06);
        }

        header, footer { background: transparent !important; }

        /* Typography tweaks */
        h1, h2, h3, h4, h5, h6, p, div, span, label {
            color: #e5e7eb;
        }

        /* ----- GLASS CONTAINER CARD ----- */
        .glass {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 18px;
            padding: 18px 18px;
            box-shadow: 0 14px 45px rgba(0,0,0,0.45);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
        }

        /* ----- KPI CARD ----- */
        .kpi {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 16px;
            padding: 14px 16px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.35);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
        }

        .kpi-label {
            font-size: 12px;
            letter-spacing: 0.10em;
            text-transform: uppercase;
            opacity: 0.7;
        }

        .kpi-value {
            font-size: 28px;
            font-weight: 750;
            margin-top: 8px;
            line-height: 1.1;
        }

        .kpi-sub {
            margin-top: 6px;
            font-size: 12px;
            opacity: 0.65;
        }

        /* Accents */
        .blue { color: #60a5fa; }
        .green { color: #34d399; }
        .purple { color: #a78bfa; }
        .orange { color: #fb923c; }
        .red { color: #f87171; }

        /* Buttons */
        .stButton button {
            border-radius: 14px !important;
            border: 1px solid rgba(255,255,255,0.10) !important;
            background: rgba(255,255,255,0.04) !important;
        }
        .stButton button:hover {
            border: 1px solid rgba(255,255,255,0.20) !important;
            background: rgba(255,255,255,0.06) !important;
        }

        /* Dataframes soften */
        div[data-testid="stDataFrame"]{
            border-radius: 14px;
            overflow: hidden;
            border: 1px solid rgba(255,255,255,0.06);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def kpi_card(label: str, value: str, color: str = "blue", sub: str | None = None) -> str:
    sub_html = f"<div class='kpi-sub'>{sub}</div>" if sub else ""
    return f"""
    <div class="kpi">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value {color}">{value}</div>
        {sub_html}
    </div>
    """


def glass_open() -> str:
    return "<div class='glass'>"


def glass_close() -> str:
    return "</div>"


# ------------------------------------------------------------
# Safe helpers
# ------------------------------------------------------------
def safe_view(sb, schema: str, name: str, limit: int = 1):
    """Safe SELECT * from a view/table. Returns [] on error."""
    try:
        q = sb.schema(schema).table(name).select("*")
        if limit is not None:
            q = q.limit(int(limit))
        return q.execute().data or []
    except Exception:
        return []


def safe_select_where(sb, schema: str, table: str, cols: str, where_col: str, where_val, limit: int = 1):
    """Safe SELECT cols FROM table WHERE where_col = where_val."""
    try:
        q = sb.schema(schema).table(table).select(cols).eq(where_col, where_val)
        if limit is not None:
            q = q.limit(int(limit))
        return q.execute().data or []
    except Exception:
        return []


def _num(x, default=0.0) -> float:
    try:
        if x is None or x == "":
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _fmt_money(x, decimals: int = 0) -> str:
    try:
        v = float(x)
        if decimals == 0:
            return f"{v:,.0f}"
        return f"{v:,.{decimals}f}"
    except Exception:
        return "—"


def _pick(row: dict, *keys, default=None):
    """Pick first existing key with non-null value."""
    for k in keys:
        if row and k in row and row.get(k) not in (None, "", "null"):
            return row.get(k)
    return default


def _s(x) -> str | None:
    """Safe stringify for dates/values."""
    if x is None:
        return None
    s = str(x).strip()
    return s if s else None


# ------------------------------------------------------------
# NEW: Paid interest helper (no interest_ledger.member_id ever)
# ------------------------------------------------------------
def compute_interest_paid_all_time(sb, schema: str = "public") -> float:
    """
    Paid interest is implied as:
      SUM(total_interest_generated - unpaid_interest)
    for ACTIVE loans only.

    This matches your verified totals:
      generated=950, unpaid=400 -> paid=550.
    """
    try:
        rows = (
            sb.schema(schema)
            .table("loans_legacy")
            .select("total_interest_generated,unpaid_interest,status")
            .execute()
            .data
            or []
        )
        paid = 0.0
        for r in rows:
            if str(r.get("status", "")).lower() != "active":
                continue
            gen = _num(r.get("total_interest_generated"), 0.0)
            unpaid = _num(r.get("unpaid_interest"), 0.0)
            paid += (gen - unpaid)
        return float(paid)
    except Exception:
        return 0.0


# ------------------------------------------------------------
# Dashboard renderer
# ------------------------------------------------------------
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    inject_dashboard_theme()

    st.markdown("## 📊 Dashboard")

    # =========================================================
    # 1) SESSION / ROTATION (dashboard_next_view)
    # =========================================================
    dash = (safe_view(sb_anon, schema, "dashboard_next_view", limit=1) or [{}])[0]

    session_number = _pick(dash, "session_number", "payout_index", "next_payout_index", default=None)
    next_idx = _pick(dash, "payout_index", "next_payout_index", default=session_number)

    beneficiary_name = _pick(dash, "next_beneficiary", "beneficiary_name", "next_beneficiary_name", default="—")

    start_date = _s(_pick(dash, "start_date", "rotation_start_date"))
    end_date = _s(_pick(dash, "end_date", "rotation_end_date"))

    if (not start_date or not end_date) and session_number not in (None, "—", ""):
        try:
            sid_int = int(session_number)
        except Exception:
            sid_int = None

        if sid_int is not None:
            sess = (
                safe_select_where(
                    sb_anon,
                    schema,
                    "sessions_legacy",
                    "start_date,end_date,session_number",
                    "session_number",
                    sid_int,
                    limit=1,
                )
                or [{}]
            )[0]
            start_date = start_date or _s(_pick(sess, "start_date"))
            end_date = end_date or _s(_pick(sess, "end_date"))

    window = f"{start_date} → {end_date}" if start_date and end_date else "—"

    st.markdown(glass_open(), unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(kpi_card("Session #", str(session_number) if session_number not in (None, "") else "—", "blue"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card("Next Payout Index", str(next_idx) if next_idx not in (None, "") else "—", "purple"), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card("Next Beneficiary", beneficiary_name if beneficiary_name else "—", "green"), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card("Session Window", window, "orange"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # =========================================================
    # 2) SESSION POT / CYCLE TOTALS (AUTHORITATIVE: v_current_cycle_kpis)
    #    ✅ Option A: Current Pot MUST equal current session contributions
    # =========================================================
    kpis = (safe_view(sb_anon, schema, "v_current_cycle_kpis", limit=1) or [{}])[0]

    cycle_total_num = _num(_pick(kpis, "cycle_total", default=0), default=0.0)
    members_paid_num = int(_num(_pick(kpis, "members_paid", default=0), default=0))

    # ✅ FIX: do NOT read pot from dashboard_next_view. It can be stale.
    current_pot_num = cycle_total_num

    st.markdown(glass_open(), unsafe_allow_html=True)
    p1, p2, p3 = st.columns(3)
    with p1:
        st.markdown(kpi_card("Current Pot", _fmt_money(current_pot_num, 0), "green"), unsafe_allow_html=True)
    with p2:
        st.markdown(kpi_card("Cycle Contributions", _fmt_money(cycle_total_num, 0), "blue"), unsafe_allow_html=True)
    with p3:
        st.markdown(kpi_card("Members Paid", str(members_paid_num), "purple"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # =========================================================
    # 3) PAYOUT STATUS
    # =========================================================
    is_day = (safe_view(sb_anon, schema, "v_is_payout_day", limit=1) or [{}])[0]
    payout_status = (safe_view(sb_anon, schema, "v_payout_status_current_session", limit=1) or [{}])[0]

    is_payout_day = bool(_pick(is_day, "is_payout_day", default=False))
    ready = _pick(payout_status, "ready")
    missing = _pick(payout_status, "missing_signatures")

    st.markdown(glass_open(), unsafe_allow_html=True)
    s1, s2, s3 = st.columns(3)
    with s1:
        st.markdown(kpi_card("Is Payout Day", "YES" if is_payout_day else "NO", "orange"), unsafe_allow_html=True)
    with s2:
        st.markdown(
            kpi_card(
                "Payout Ready",
                "YES" if ready is True else ("NO" if ready is False else "—"),
                "green" if ready else "red",
            ),
            unsafe_allow_html=True,
        )
    with s3:
        st.markdown(kpi_card("Missing Signatures", missing if missing else "—", "purple"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # =========================================================
    # 4) ALL-TIME FINANCE (FORCE PUBLIC) ✅ Interest PAID fixed here
    # =========================================================
    fin = (safe_view(sb_anon, "public", "dashboard_finance_view", limit=1) or [{}])[0]

    total_foundation_paid = _pick(fin, "total_foundation_paid", "foundation_paid", "total_foundation")
    total_foundation_unpaid = _pick(fin, "total_foundation_unpaid", "foundation_unpaid")
    total_fines_paid = _pick(fin, "total_fines_paid", "fines_paid")
    total_fines_unpaid = _pick(fin, "total_fines_unpaid", "fines_unpaid")

    foundation_all = _num(total_foundation_paid) + _num(total_foundation_unpaid)

    # ✅ NEW: Interest Paid All-Time (active loans) = generated - unpaid
    interest_paid_all_time = compute_interest_paid_all_time(sb_anon, schema="public")

    st.markdown("### 🧾 All-Time Finance Summary")

    st.markdown(glass_open(), unsafe_allow_html=True)
    f1, f2, f3, f4, f5 = st.columns(5)

    with f1:
        st.markdown(kpi_card("Foundation (All-Time)", _fmt_money(foundation_all, 0) if foundation_all > 0 else "—", "blue"), unsafe_allow_html=True)
    with f2:
        st.markdown(kpi_card("Foundation Paid", _fmt_money(_num(total_foundation_paid), 0) if total_foundation_paid is not None else "—", "green"), unsafe_allow_html=True)
    with f3:
        st.markdown(kpi_card("Fines Paid", _fmt_money(_num(total_fines_paid), 0) if total_fines_paid is not None else "—", "purple"), unsafe_allow_html=True)
    with f4:
        st.markdown(kpi_card("Fines Unpaid", _fmt_money(_num(total_fines_unpaid), 0) if total_fines_unpaid is not None else "—", "orange"), unsafe_allow_html=True)
    with f5:
        st.markdown(
            kpi_card(
                "Interest Paid (All-Time)",
                _fmt_money(interest_paid_all_time, 2),
                "green",
                sub="Paid = Generated − Unpaid (Active Loans)",
            ),
            unsafe_allow_html=True,
        )

    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # =========================================================
    # 5) OPTIONAL: KPI TABLES
    # =========================================================
    kpi_cycle = pd.DataFrame(safe_view(sb_anon, schema, "v_kpi_current_cycle", limit=200))
    if not kpi_cycle.empty:
        st.markdown("### 📈 KPIs — Current Cycle")
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.dataframe(kpi_cycle, use_container_width=True, hide_index=True)
        st.markdown(glass_close(), unsafe_allow_html=True)

    kpi_member = pd.DataFrame(safe_view(sb_anon, schema, "v_kpi_member_cycle", limit=2000))
    if not kpi_member.empty:
        st.markdown("### 👤 KPIs — Member Cycle")
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.dataframe(kpi_member, use_container_width=True, hide_index=True)
        st.markdown(glass_close(), unsafe_allow_html=True)

    # =========================================================
    # DEBUG
    # =========================================================
    with st.expander("🔎 Debug (raw rows)", expanded=False):
        st.write("dashboard_next_view", dash)
        st.write("sessions_legacy (resolved window)", {"start_date": start_date, "end_date": end_date, "session_number": session_number})
        st.write("v_current_cycle_kpis", kpis)
        st.write("dashboard_finance_view (PUBLIC)", fin)
        st.write("v_is_payout_day", is_day)
        st.write("v_payout_status_current_session", payout_status)
        st.write("interest_paid_all_time (computed)", {"interest_paid_all_time": interest_paid_all_time})
        st.write("Option A current_pot_num (from cycle_total)", current_pot_num)

    # Service key status
    if sb_service is None:
        st.warning("Admin/write features disabled (no service key).")
    else:
        st.success("Admin/write features enabled.")
