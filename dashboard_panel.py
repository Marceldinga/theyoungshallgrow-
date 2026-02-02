# dashboard_panel.py ✅ COMPLETE SINGLE CODE (NO SQL) — NJANGI STANDARD (NO "legacy")
# ✅ Fixes Dashboard not updating:
#    - Uses sb_service for app_state/sessions/contributions when available (RLS-safe)
#    - Uses sb_anon only as fallback
# ✅ Shows Session ID / Session Window / Beneficiary / Pot correctly
# ✅ Dark theme + glass KPI cards
# ✅ Auto-refresh on app_state stamp change
#
# ✅ FINANCE MODEL (your rule):
#    - Payouts are pot redistribution → NOT cash flow (informational only)
#    - Adds FINES PAID as cash flow (fines.status='paid')
#    - Interest is LEDGER-based (interest_ledger):
#         - Interest this month = sum(amount) where interest_month startswith YYYY-MM
#         - Interest all-time  = sum(amount) all rows
#    - Cash Available = foundation + loan_payments + interest(ledger) + fines_paid − outstanding_principal
#
# ✅ Attendance (ALL-TIME) on dashboard:
#    - CHART ONLY (no dataframe numbers shown)
#    - Reads from view public.v_attendance_member_totals if available
#    - Fallback: computes from attendance table (no SQL)
#
# NEW TABLES ONLY:
#   - app_state                (id=1, current_session_id, next_member_id, optional next_payout_date, updated_at)
#   - sessions                 (session_id, start_date, end_date, created_at)
#   - members                  (id, name, phone, display_name optional)
#   - contributions            (member_id, session_id, amount, paid_at, created_at)
#   - foundation_contributions (member_id, session_id, amount, paid_at, created_at)
#   - loans                    (id, member_id, status, principal_current, principal, unpaid_interest, total_interest_generated,
#                               interest_rate_monthly, total_due (generated), borrow_date, due_cycle_days, last_paid_at, created_at, updated_at)
#   - loan_payments            (loan_id, member_id, amount, paid_at, created_at)
#   - interest_ledger          (id, loan_id, member_id, interest_month, amount, created_at, ...)
#   - payouts                  (session_id, member_id, payout_amount, payout_date, payout_index, created_at, updated_at)  # informational only
#   - fines                    (id, member_id, session_id, amount, reason, issued_by, status, paid_at, created_at, updated_at)
#   - attendance               (id, member_id, session_id, present, note, created_at)
#   - v_next_beneficiary       (optional view)
#   - v_attendance_member_totals (recommended view)

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
import streamlit as st
import pandas as pd

DUE_DAYS = 28


# ============================================================
# THEME
# ============================================================
def inject_dashboard_theme():
    st.markdown(
        """
        <style>
        .stApp {
            background-color: #0b0f1a;
            background-image:
                radial-gradient(circle at 1px 1px, rgba(255,255,255,0.06) 1px, transparent 0);
            background-size: 24px 24px;
            color: #e5e7eb;
        }
        section[data-testid="stSidebar"]{
            background: #0b0f1a;
            border-right: 1px solid rgba(255,255,255,0.06);
        }
        header, footer { background: transparent !important; }
        h1, h2, h3, h4, h5, h6, p, div, span, label { color: #e5e7eb; }

        .glass {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 18px;
            padding: 18px 18px;
            box-shadow: 0 14px 45px rgba(0,0,0,0.45);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
        }

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
            word-break: break-word;
        }

        .blue { color: #60a5fa; }
        .green { color: #34d399; }
        .purple { color: #a78bfa; }
        .orange { color: #fb923c; }
        .red { color: #f87171; }

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


# ============================================================
# SAFE HELPERS (NO RAW SQL)
# ============================================================
def safe_table(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int | None = 20000,
    order_by: str | None = None,
    desc: bool = True,
):
    try:
        q = sb.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit is not None:
            q = q.limit(int(limit))
        res = q.execute()
        return res.data or []
    except Exception:
        return []


def safe_table_order_fallback(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int | None = 20000,
    order_candidates: list[str] | None = None,
    desc: bool = True,
):
    order_candidates = order_candidates or []
    for c in order_candidates:
        try:
            rows = safe_table(sb, schema, table, cols=cols, limit=limit, order_by=c, desc=desc)
            if rows is not None:
                return rows or []
        except Exception:
            continue
    return safe_table(sb, schema, table, cols=cols, limit=limit, order_by=None, desc=desc)


def safe_single(sb, schema: str, table: str, cols: str = "*", **eq_filters) -> dict:
    try:
        q = sb.schema(schema).table(table).select(cols)
        for k, v in eq_filters.items():
            q = q.eq(k, v)
        q = q.limit(1)
        rows = q.execute().data or []
        return rows[0] if rows else {}
    except Exception:
        return {}


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


def _to_date(x) -> date | None:
    if x is None:
        return None
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    if isinstance(x, datetime):
        return x.date()
    s = str(x).strip()
    if not s:
        return None
    if "T" in s:
        s = s.split("T")[0]
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def _table_exists(sb, schema: str, table: str) -> bool:
    try:
        sb.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


# ============================================================
# AUTO-REFRESH ON app_state CHANGE
# ============================================================
def _read_state_stamp(sb, schema: str) -> str:
    r = safe_single(sb, schema, "app_state", "*", id=1)
    return "|".join(
        [
            str(r.get("current_session_id") or ""),
            str(r.get("next_member_id") or ""),
            str(r.get("next_payout_date") or ""),
            str(r.get("updated_at") or ""),
        ]
    )


def _auto_refresh_if_state_changed(sb, schema: str):
    stamp = _read_state_stamp(sb, schema)
    prev = st.session_state.get("_state_stamp_dashboard")
    st.session_state["_state_stamp_dashboard"] = stamp
    if prev is None:
        return
    if stamp != prev:
        try:
            st.cache_data.clear()
            st.cache_resource.clear()
        except Exception:
            pass
        st.rerun()


# ============================================================
# KPI COMPUTATIONS
# ============================================================
def compute_interest_ledger(sb, schema: str) -> tuple[float, float]:
    """
    ✅ Interest ledger-based:
      - this_month: sum(amount) where interest_month startswith YYYY-MM
      - all_time:   sum(amount) all rows
    Works whether interest_month is 'YYYY-MM' OR 'YYYY-MM-01' OR ISO string.
    """
    if not _table_exists(sb, schema, "interest_ledger"):
        return 0.0, 0.0

    rows = safe_table_order_fallback(
        sb,
        schema,
        "interest_ledger",
        "*",
        limit=20000,
        order_candidates=["interest_month", "created_at", "id"],
        desc=True,
    )

    if not rows:
        return 0.0, 0.0

    month_prefix = date.today().strftime("%Y-%m")
    this_month = 0.0
    all_time = 0.0

    for r in rows:
        amt = r.get("amount") if "amount" in r else r.get("interest_amount")
        v = _num(amt, 0.0)
        all_time += v

        im = str(r.get("interest_month") or "").strip()
        if im.startswith(month_prefix):
            this_month += v

    return float(this_month), float(all_time)


def compute_loan_payments(sb, schema: str) -> tuple[float, dict[int, date]]:
    if not _table_exists(sb, schema, "loan_payments"):
        return 0.0, {}

    rows = safe_table_order_fallback(
        sb,
        schema,
        "loan_payments",
        "*",
        limit=20000,
        order_candidates=["paid_at", "created_at", "id"],
        desc=True,
    )
    total = 0.0
    last_by_loan: dict[int, date] = {}
    for r in rows or []:
        total += _num(r.get("amount"), 0.0)
        try:
            lid = int(r.get("loan_id"))
        except Exception:
            continue
        d = _to_date(r.get("paid_at") or r.get("created_at"))
        if d is not None:
            if lid not in last_by_loan or d > last_by_loan[lid]:
                last_by_loan[lid] = d
    return float(total), last_by_loan


def compute_loans_kpis(sb, schema: str) -> dict[str, Any]:
    if not _table_exists(sb, schema, "loans"):
        return {"active_loans": 0, "principal_active": 0.0, "total_due_active": 0.0}

    rows = safe_table(sb, schema, "loans", "*", limit=20000)
    active = 0
    principal_sum = 0.0
    total_due_sum = 0.0

    for r in rows or []:
        status = str(r.get("status") or "").lower().strip()
        if status not in ("active", "open"):
            continue

        active += 1
        pc = _num(r.get("principal_current") or r.get("principal"), 0.0)
        principal_sum += pc

        # total_due is generated in DB (read-only)
        td = _num(r.get("total_due"), pc + _num(r.get("unpaid_interest"), 0.0))
        total_due_sum += td

    return {
        "active_loans": int(active),
        "principal_active": float(principal_sum),
        "total_due_active": float(total_due_sum),
    }


def sum_table_amount(sb, schema: str, table: str, amount_cols: list[str]) -> float:
    if not _table_exists(sb, schema, table):
        return 0.0
    rows = safe_table(sb, schema, table, "*", limit=20000)
    total = 0.0
    for r in rows or []:
        val = None
        for c in amount_cols:
            if c in r:
                val = r.get(c)
                break
        total += _num(val, 0.0)
    return float(total)


def compute_fines_paid_total(sb, schema: str) -> float:
    """
    ✅ Cash flow from fines = SUM(amount) where status='paid'
    """
    if not _table_exists(sb, schema, "fines"):
        return 0.0

    rows = safe_table_order_fallback(
        sb,
        schema,
        "fines",
        "*",
        limit=20000,
        order_candidates=["paid_at", "created_at", "id"],
        desc=True,
    )
    total = 0.0
    for r in rows or []:
        status = str(r.get("status") or "").lower().strip()
        if status != "paid":
            continue
        total += _num(r.get("amount"), 0.0)
    return float(total)


def get_session_window(sb, schema: str, session_id: int) -> str:
    if not session_id:
        return "—"
    srow = safe_single(sb, schema, "sessions", "*", session_id=int(session_id))
    sd = srow.get("start_date")
    ed = srow.get("end_date")
    if sd and ed:
        return f"{sd} → {ed}"
    return "—"


def build_repayment_plan(sb, schema: str, last_payment_dates: dict[int, date]) -> pd.DataFrame:
    loans = safe_table(sb, schema, "loans", "*", limit=20000)
    out: list[dict[str, Any]] = []

    for r in loans or []:
        status = str(r.get("status") or "").lower().strip()
        if status not in ("active", "open"):
            continue

        try:
            lid = int(r.get("id"))
        except Exception:
            continue

        principal = _num(r.get("principal_current") or r.get("principal"), 0.0)
        unpaid_interest = _num(r.get("unpaid_interest"), 0.0)
        total_due = _num(r.get("total_due"), principal + unpaid_interest)

        borrow_date = (
            _to_date(r.get("borrow_date"))
            or _to_date(r.get("created_at"))
            or date.today()
        )

        if lid in last_payment_dates:
            last_paid = last_payment_dates[lid]
            next_due = last_paid + timedelta(days=DUE_DAYS)
        else:
            last_paid = None
            next_due = borrow_date + timedelta(days=DUE_DAYS)

        out.append(
            {
                "loan_id": lid,
                "member_id": r.get("member_id"),
                "principal_current": principal,
                "unpaid_interest": unpaid_interest,
                "total_due": total_due,
                "last_paid": last_paid.isoformat() if isinstance(last_paid, date) else "—",
                "next_due_date": next_due.isoformat(),
            }
        )

    df = pd.DataFrame(out)
    if df.empty:
        return df
    return df.sort_values("next_due_date", ascending=True)


# ============================================================
# ATTENDANCE (ALL-TIME PER MEMBER) — CHART ONLY
# ============================================================
def load_attendance_totals(read_sb, schema: str) -> pd.DataFrame:
    """
    Preferred: read from view v_attendance_member_totals (fast).
    Fallback: compute from attendance table in Python.
    Returns columns at least: member_name, attendance_percent.
    """
    # 1) Try view by name (your created view name)
    view_name = "v_attendance_member_totals"
    if _table_exists(read_sb, schema, view_name):
        rows = safe_table(read_sb, schema, view_name, "*", limit=5000, order_by="member_id", desc=False)
        if rows:
            return pd.DataFrame(rows)

    # 2) Fallback compute
    members = safe_table(read_sb, schema, "members", "id,name,display_name,phone", limit=5000, order_by="id", desc=False)
    attendance = safe_table(read_sb, schema, "attendance", "id,member_id,session_id,present,note,created_at", limit=20000)

    dfm = pd.DataFrame(members or [])
    dfa = pd.DataFrame(attendance or [])

    if dfm.empty:
        return pd.DataFrame()

    for col in ("id", "name", "display_name", "phone"):
        if col not in dfm.columns:
            dfm[col] = None

    df = dfm.rename(columns={"id": "member_id"}).copy()
    df["member_name"] = df["display_name"].fillna("").astype(str).str.strip()
    df.loc[df["member_name"] == "", "member_name"] = df["name"].fillna("").astype(str).str.strip()

    if dfa.empty:
        df["attendance_percent"] = 0.0
        return df[["member_id", "member_name", "phone", "attendance_percent"]].sort_values("member_id")

    dfa["present"] = dfa.get("present").fillna(False).astype(bool)
    dfa["member_id"] = pd.to_numeric(dfa.get("member_id"), errors="coerce")

    grp = dfa.groupby("member_id", dropna=True).agg(
        total_sessions=("id", "count"),
        total_present=("present", lambda s: int(s.sum())),
    ).reset_index()

    grp["attendance_percent"] = grp.apply(
        lambda r: round((r["total_present"] / r["total_sessions"]) * 100, 2) if r["total_sessions"] else 0.0,
        axis=1,
    )

    df = df.merge(grp[["member_id", "attendance_percent"]], on="member_id", how="left")
    df["attendance_percent"] = df["attendance_percent"].fillna(0.0).astype(float)

    return df[["member_id", "member_name", "phone", "attendance_percent"]].sort_values("member_id")


def render_attendance_chart(att_df: pd.DataFrame):
    if att_df is None or att_df.empty:
        st.info("No attendance data yet.")
        return

    # normalize columns
    name_col = "member_name" if "member_name" in att_df.columns else ("Member" if "Member" in att_df.columns else None)
    pct_col = "attendance_percent" if "attendance_percent" in att_df.columns else ("Attendance %" if "Attendance %" in att_df.columns else None)

    if name_col is None or pct_col is None:
        st.warning("Attendance chart unavailable (missing columns).")
        return

    plot_df = att_df[[name_col, pct_col]].copy()
    plot_df[pct_col] = pd.to_numeric(plot_df[pct_col], errors="coerce").fillna(0.0)

    # Keep chart readable: top N only
    max_n = min(50, len(plot_df))
    default_n = min(17, len(plot_df))
    top_n = st.slider("Show top N members", min_value=5, max_value=max_n, value=default_n)

    plot_df = plot_df.sort_values(pct_col, ascending=False).head(int(top_n))
    plot_df = plot_df.set_index(name_col)

    # CHART ONLY (no dataframe shown)
    st.bar_chart(plot_df[pct_col])


# ============================================================
# DASHBOARD (STANDARD)
# ============================================================
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    inject_dashboard_theme()

    # ✅ Use service client if available for reads (RLS-safe)
    read_sb = sb_service if sb_service is not None else sb_anon
    finance_sb = sb_service if sb_service is not None else sb_anon

    # ✅ Auto-refresh when app_state changes
    _auto_refresh_if_state_changed(read_sb, schema)

    st.markdown("## 🏦 theyoungshallgrow • Bank Dashboard")

    # --- App state ---
    state = safe_single(read_sb, schema, "app_state", "*", id=1)
    if not state:
        rows = safe_table(read_sb, schema, "app_state", "*", limit=1)
        state = rows[0] if rows else {}

    # --- Current session id (robust fallback) ---
    raw_cs = state.get("current_session_id")
    try:
        current_session_id = int(raw_cs) if raw_cs is not None and str(raw_cs).strip() != "" else None
    except Exception:
        current_session_id = None

    # If not set, fallback to latest sessions.session_id (read-only)
    if current_session_id is None:
        srows = safe_table_order_fallback(
            read_sb,
            schema,
            "sessions",
            "session_id,start_date,end_date,created_at",
            limit=1,
            order_candidates=["session_id", "start_date", "created_at"],
            desc=True,
        )
        if srows:
            try:
                current_session_id = int(srows[0].get("session_id"))
            except Exception:
                current_session_id = None

    session_note = "from app_state" if state.get("current_session_id") else "fallback: latest session"
    next_member_id = state.get("next_member_id")

    # --- Members ---
    members_rows = safe_table(read_sb, schema, "members", "id,name,display_name", limit=5000, order_by="id", desc=False)
    total_members = int(len(members_rows or []))

    # --- Beneficiary (try optional view; fallback to members lookup) ---
    beneficiary_name = "—"
    beneficiary_id = next_member_id

    v = safe_single(read_sb, schema, "v_next_beneficiary", "*")
    if v:
        beneficiary_name = str(v.get("beneficiary_name") or v.get("member_name") or "—")
        beneficiary_id = v.get("beneficiary_id") or v.get("member_id") or beneficiary_id
    else:
        try:
            bid = int(beneficiary_id) if beneficiary_id is not None else None
        except Exception:
            bid = None
        if bid is not None:
            for m in members_rows or []:
                if str(m.get("id")) == str(bid):
                    dn = str(m.get("display_name") or "").strip()
                    nm = str(m.get("name") or "").strip()
                    beneficiary_name = dn or nm or f"Member {bid:02d}"
                    break

    window = "—"
    if isinstance(current_session_id, int) and current_session_id > 0:
        window = get_session_window(read_sb, schema, int(current_session_id))

    # --- Current pot (cycle contributions) ---
    pot = 0.0
    members_paid = 0
    if isinstance(current_session_id, int) and current_session_id > 0:
        crows = safe_table(finance_sb, schema, "contributions", "member_id,session_id,amount", limit=20000)
        dfc = pd.DataFrame(crows or [])
        if not dfc.empty and "session_id" in dfc.columns:
            dfc["session_id"] = pd.to_numeric(dfc["session_id"], errors="coerce").fillna(-1).astype(int)
            dfc = dfc[dfc["session_id"] == int(current_session_id)].copy()
            dfc["amount"] = pd.to_numeric(dfc.get("amount"), errors="coerce").fillna(0.0)
            pot = float(dfc["amount"].sum())
            members_paid = int(dfc["member_id"].nunique()) if "member_id" in dfc.columns else 0

    # --- Header KPIs ---
    st.markdown(glass_open(), unsafe_allow_html=True)
    a1, a2, a3, a4 = st.columns(4)
    with a1:
        st.markdown(kpi_card("Session ID", str(current_session_id or "—"), "blue", sub=session_note), unsafe_allow_html=True)
    with a2:
        st.markdown(kpi_card("Session Window", window, "orange"), unsafe_allow_html=True)
    with a3:
        st.markdown(kpi_card("Total Members", str(total_members), "purple", sub="members"), unsafe_allow_html=True)
    with a4:
        st.markdown(
            kpi_card(
                "Current Beneficiary",
                beneficiary_name,
                "green",
                sub=f"member_id: {beneficiary_id if beneficiary_id is not None else '—'}",
            ),
            unsafe_allow_html=True,
        )
    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # --- Cycle KPIs ---
    st.markdown(glass_open(), unsafe_allow_html=True)
    p1, p2, p3 = st.columns(3)
    with p1:
        st.markdown(kpi_card("Current Pot", _fmt_money(pot, 0), "green"), unsafe_allow_html=True)
    with p2:
        st.markdown(kpi_card("Cycle Contributions", _fmt_money(pot, 0), "blue"), unsafe_allow_html=True)
    with p3:
        st.markdown(kpi_card("Members Paid", str(members_paid), "purple"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # --- Financial Totals ---
    foundation_total = sum_table_amount(finance_sb, schema, "foundation_contributions", ["amount"])
    payouts_total = sum_table_amount(finance_sb, schema, "payouts", ["payout_amount", "amount"])  # informational only
    interest_this_month, interest_all_time = compute_interest_ledger(finance_sb, schema)
    repayments_total, last_payment_dates = compute_loan_payments(finance_sb, schema)
    fines_paid_total = compute_fines_paid_total(finance_sb, schema)
    loan_kpis = compute_loans_kpis(finance_sb, schema)
    loans_outstanding = float(loan_kpis["principal_active"])

    cash_available_raw = foundation_total + repayments_total + interest_all_time + fines_paid_total - loans_outstanding
    cash_available = max(cash_available_raw, 0.0)
    net_available = cash_available + float(pot)

    st.markdown("### 🏦 Financial Summary")

    st.markdown(glass_open(), unsafe_allow_html=True)
    f1, f2, f3, f4, f5, f6, f7, f8, f9 = st.columns(9)
    with f1:
        st.markdown(kpi_card("Foundation Total", _fmt_money(foundation_total, 0), "blue", sub="foundation_contributions"), unsafe_allow_html=True)
    with f2:
        st.markdown(kpi_card("Payouts Total", _fmt_money(payouts_total, 0), "orange", sub="pot redistribution (info)"), unsafe_allow_html=True)
    with f3:
        st.markdown(kpi_card("Interest This Month", _fmt_money(interest_this_month, 2), "green", sub="interest_ledger (YYYY-MM)"), unsafe_allow_html=True)
    with f4:
        st.markdown(kpi_card("Interest All-time", _fmt_money(interest_all_time, 2), "green", sub="interest_ledger (all)"), unsafe_allow_html=True)
    with f5:
        st.markdown(kpi_card("Loan Payments", _fmt_money(repayments_total, 0), "green", sub="loan_payments"), unsafe_allow_html=True)
    with f6:
        st.markdown(kpi_card("Total Fines Paid", _fmt_money(fines_paid_total, 0), "purple", sub="fines.status='paid'"), unsafe_allow_html=True)
    with f7:
        st.markdown(kpi_card("Outstanding Principal", _fmt_money(loans_outstanding, 0), "red", sub="loans.principal_current"), unsafe_allow_html=True)
    with f8:
        st.markdown(kpi_card("Cash Available", _fmt_money(cash_available, 0), "green", sub="foundation + payments + interest + fines − principal"), unsafe_allow_html=True)
    with f9:
        st.markdown(kpi_card("Net Available", _fmt_money(net_available, 0), "blue", sub="Cash Available + Current Pot"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    if cash_available_raw < 0:
        st.warning(
            f"⚠️ Cash Available RAW is negative ({cash_available_raw:,.0f}) before flooring to 0. "
            "This means outstanding loans exceed foundation cash-in (payments/interest/fines)."
        )

    st.divider()

    # --- Attendance (Chart Only) ---
    st.markdown("### ✅ Attendance • All-time Summary (Chart)")
    att_df = load_attendance_totals(read_sb, schema)
    render_attendance_chart(att_df)

    st.divider()

    # --- Loans ---
    st.markdown("### 💳 Loans")

    st.markdown(glass_open(), unsafe_allow_html=True)
    l1, l2, l3 = st.columns(3)
    with l1:
        st.markdown(kpi_card("Active Loans", str(int(loan_kpis["active_loans"])), "purple"), unsafe_allow_html=True)
    with l2:
        st.markdown(kpi_card("Principal Current", _fmt_money(loan_kpis["principal_active"], 0), "orange"), unsafe_allow_html=True)
    with l3:
        st.markdown(kpi_card("Total Due", _fmt_money(loan_kpis["total_due_active"], 0), "red"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    plan_df = build_repayment_plan(finance_sb, schema, last_payment_dates)
    if plan_df.empty:
        st.info("No active/open loans found.")
    else:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.markdown("#### 🗓️ Loan Repayment Plan")
        st.caption(f"Due = {DUE_DAYS} days from last payment (or borrow_date if never paid).")
        st.dataframe(plan_df, use_container_width=True, hide_index=True)
        st.markdown(glass_close(), unsafe_allow_html=True)

    # --- Debug ---
    with st.expander("🔎 Debug", expanded=False):
        st.write("Using read client:", "service" if sb_service is not None else "anon")
        st.write("app_state", state)
        st.write("session_note", session_note)
        st.write("current_session_id", current_session_id)
        st.write("next_member_id", next_member_id)
        st.write("beneficiary_id", beneficiary_id)
        st.write("beneficiary_name", beneficiary_name)
        st.write("current_pot", pot)
        st.write("members_paid", members_paid)
        st.write("foundation_total", foundation_total)
        st.write("payouts_total (informational)", payouts_total)
        st.write("interest_this_month (ledger)", interest_this_month)
        st.write("interest_all_time (ledger)", interest_all_time)
        st.write("loan_payments_total", repayments_total)
        st.write("fines_paid_total", fines_paid_total)
        st.write("loan_kpis", loan_kpis)
        st.write("cash_available_raw", cash_available_raw)
        st.write("cash_available (floored)", cash_available)
        st.write("net_available", net_available)
        st.write("attendance_rows", 0 if att_df is None else int(len(att_df)))
