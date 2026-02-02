
# dashboard_panel.py ✅ COMPLETE SINGLE CODE (NO SQL) — NJANGI STANDARD (NO "legacy")
# ✅ Dark theme + glass KPI cards
# ✅ Auto-refresh on cycle change (app_state stamp change)
# ✅ Uses sb_service for finance totals when available
#
# ✅ NEW TABLES ONLY:
#   - app_state            (id=1, current_session_id, next_member_id, optional next_payout_date, updated_at)
#   - sessions             (session_id OR id, start_date, end_date)
#   - members              (id, name)
#   - contributions         (member_id, session_id, amount, paid_at, created_at)
#   - foundation_contributions (member_id, session_id, amount, paid_at, created_at)
#   - loans                (id, member_id, status, principal_current, principal, unpaid_interest, total_interest_generated, total_due, borrow_date, created_at)
#   - loan_payments         (loan_id, member_id, amount, paid_at, created_at)
#   - payouts               (session_id, member_id, payout_amount, payout_date, created_at)
#   - v_next_beneficiary    (view) — optional
#
# ✅ Interest PAID source-of-truth (your DB model):
#   interest_paid = SUM(total_interest_generated - unpaid_interest) WHERE status IN ('active','open')
#
# ✅ Loan due rule:
#   next_due_date = (last_paid OR borrow_date) + 28 days
#
# ✅ Loan KPIs read principal_current (not principal)
#
# ✅ Financial KPIs (standard):
#   foundation_total_all_time = SUM(foundation_contributions.amount)
#   payouts_total_all_time    = SUM(payouts.payout_amount)
#   repayments_total_all_time = SUM(loan_payments.amount)
#   loans_outstanding_active  = SUM(loans.principal_current) for active/open
#   cash_available_raw        = foundation_total_all_time + repayments_total_all_time + interest_paid - loans_outstanding_active - payouts_total_all_time
#   cash_available            = max(cash_available_raw, 0)

from __future__ import annotations

from datetime import date, datetime, timedelta
import streamlit as st
import pandas as pd


# ============================================================
# CONFIG
# ============================================================
DUE_DAYS = 28  # loan due cycle


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
# SAFE HELPERS
# ============================================================
def safe_table(sb, schema: str, table: str, cols: str = "*", limit: int = 20000, order_by: str | None = None, desc: bool = True):
    try:
        q = sb.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit is not None:
            q = q.limit(int(limit))
        return q.execute().data or []
    except Exception:
        return []


def safe_table_order_fallback(sb, schema: str, table: str, cols: str = "*", limit: int = 20000, order_candidates: list[str] | None = None, desc: bool = True):
    order_candidates = order_candidates or []
    for c in order_candidates:
        rows = safe_table(sb, schema, table, cols=cols, limit=limit, order_by=c, desc=desc)
        if rows:
            return rows
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


# ============================================================
# AUTO-REFRESH ON STATE CHANGE
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


def _auto_refresh_if_state_changed(sb_anon, schema: str):
    stamp = _read_state_stamp(sb_anon, schema)
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
def compute_interest_paid(sb, schema: str) -> float:
    rows = safe_table(sb, schema, "loans", "*", limit=20000)
    if not rows:
        return 0.0
    total = 0.0
    for r in rows:
        status = str(r.get("status") or "").lower().strip()
        if status not in ("active", "open"):
            continue
        generated = _num(r.get("total_interest_generated"), 0.0)
        unpaid = _num(r.get("unpaid_interest"), 0.0)
        paid = generated - unpaid
        if paid < 0:
            paid = 0.0
        total += paid
    return float(total)


def compute_loan_payments(sb, schema: str) -> tuple[float, dict[int, date]]:
    rows = safe_table_order_fallback(sb, schema, "loan_payments", "*", limit=20000, order_candidates=["paid_at", "created_at", "id"], desc=True)
    if not rows:
        return 0.0, {}
    total = 0.0
    last_by_loan: dict[int, date] = {}
    for r in rows:
        total += _num(r.get("amount"), 0.0)
        lid = r.get("loan_id")
        try:
            lid_int = int(lid)
        except Exception:
            lid_int = None
        d = _to_date(r.get("paid_at") or r.get("created_at"))
        if lid_int is not None and d is not None:
            if lid_int not in last_by_loan or d > last_by_loan[lid_int]:
                last_by_loan[lid_int] = d
    return float(total), last_by_loan


def compute_loans_kpis(sb, schema: str) -> dict:
    rows = safe_table(sb, schema, "loans", "*", limit=20000)
    if not rows:
        return {"active_loans": 0, "principal_active": 0.0, "total_due_active": 0.0}
    active = 0
    principal_sum = 0.0
    total_due_sum = 0.0
    for r in rows:
        status = str(r.get("status") or "").lower().strip()
        if status not in ("active", "open"):
            continue
        active += 1
        pc = _num(r.get("principal_current") or r.get("principal"), 0.0)
        principal_sum += pc
        td = _num(r.get("total_due"), pc + _num(r.get("unpaid_interest"), 0.0))
        total_due_sum += td
    return {"active_loans": int(active), "principal_active": float(principal_sum), "total_due_active": float(total_due_sum)}


def sum_table_amount(sb, schema: str, table: str, amount_cols: list[str], where: dict | None = None) -> float:
    rows = safe_table(sb, schema, table, "*", limit=20000)
    if not rows:
        return 0.0
    total = 0.0
    for r in rows:
        if where:
            ok = True
            for k, v in where.items():
                if r.get(k) != v:
                    ok = False
                    break
            if not ok:
                continue
        val = None
        for c in amount_cols:
            if c in r:
                val = r.get(c)
                break
        total += _num(val, 0.0)
    return float(total)


def get_session_window(sb, schema: str, session_id: int) -> str:
    if not session_id:
        return "—"
    pk = "session_id"
    sample = safe_single(sb, schema, "sessions", "*")
    if sample and "session_id" not in sample and "id" in sample:
        pk = "id"
    srow = safe_single(sb, schema, "sessions", "*", **{pk: int(session_id)})
    sd = srow.get("start_date")
    ed = srow.get("end_date")
    if sd and ed:
        return f"{sd} → {ed}"
    return "—"


def build_repayment_plan(sb, schema: str, last_payment_dates: dict[int, date]) -> pd.DataFrame:
    loans = safe_table(sb, schema, "loans", "*", limit=20000)
    if not loans:
        return pd.DataFrame()

    out = []
    for r in loans:
        status = str(r.get("status") or "").lower().strip()
        if status not in ("active", "open"):
            continue

        loan_id = r.get("id")
        try:
            lid = int(loan_id)
        except Exception:
            continue

        # Prefer principal_current
        principal = _num(r.get("principal_current") or r.get("principal"), 0.0)
        unpaid_interest = _num(r.get("unpaid_interest"), 0.0)
        total_due = _num(r.get("total_due"), principal + unpaid_interest)

        # Borrow date preference order
        borrow_date = (
            _to_date(r.get("borrow_date"))
            or _to_date(r.get("issued_at"))
            or _to_date(r.get("created_at"))
            or date.today()
        )

        if lid in last_payment_dates:
            last_paid = last_payment_dates[lid]
            next_due = last_paid + timedelta(days=DUE_DAYS)
        else:
            last_paid = None
            next_due = borrow_date + timedelta(days=DUE_DAYS)

        out.append({
            "loan_id": lid,
            "member_id": r.get("member_id"),
            "principal_current": principal,
            "unpaid_interest": unpaid_interest,
            "total_due": total_due,
            "last_paid": last_paid.isoformat() if isinstance(last_paid, date) else "—",
            "next_due_date": next_due.isoformat(),
        })

    df = pd.DataFrame(out)
    if df.empty:
        return df
    return df.sort_values("next_due_date", ascending=True)


# ============================================================
# DASHBOARD
# ============================================================
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    inject_dashboard_theme()
    _auto_refresh_if_state_changed(sb_anon, schema)

    finance_sb = sb_service if sb_service is not None else sb_anon

    st.markdown("## 📊 Dashboard")

    # ---------- state + next beneficiary ----------
    state = safe_single(sb_anon, schema, "app_state", "*", id=1)
    current_session_id = state.get("current_session_id")
    next_member_id = state.get("next_member_id")

    # v_next_beneficiary is optional, but preferred
    next_name = "—"
    if safe_table(sb_anon, schema, "v_next_beneficiary", "*", limit=1):
        v = safe_single(sb_anon, schema, "v_next_beneficiary", "*")
        next_name = str(v.get("beneficiary_name") or v.get("member_name") or "—")
        next_member_id = v.get("beneficiary_id") or v.get("member_id") or next_member_id

    window = get_session_window(sb_anon, schema, int(current_session_id)) if str(current_session_id or "").isdigit() else "—"

    st.markdown(glass_open(), unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(kpi_card("Session ID", str(current_session_id or "—"), "blue"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card("Next Member ID", str(next_member_id or "—"), "purple"), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card("Next Beneficiary", str(next_name or "—"), "green"), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card("Session Window", window, "orange"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # ---------- current pot (contributions for current session) ----------
    pot = 0.0
    members_paid = 0
    if str(current_session_id or "").isdigit():
        crows = safe_table(finance_sb, schema, "contributions", "member_id,session_id,amount", limit=20000)
        dfc = pd.DataFrame(crows or [])
        if not dfc.empty:
            dfc["session_id"] = pd.to_numeric(dfc.get("session_id"), errors="coerce").fillna(-1).astype(int)
            dfc = dfc[dfc["session_id"] == int(current_session_id)].copy()
            dfc["amount"] = pd.to_numeric(dfc.get("amount"), errors="coerce").fillna(0.0)
            pot = float(dfc["amount"].sum())
            members_paid = int(dfc["member_id"].nunique()) if "member_id" in dfc.columns else 0

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

    # ---------- financial totals ----------
    foundation_total = sum_table_amount(finance_sb, schema, "foundation_contributions", ["amount"])
    payouts_total = sum_table_amount(finance_sb, schema, "payouts", ["payout_amount", "amount"])
    interest_paid = compute_interest_paid(finance_sb, schema)
    repayments_total, last_payment_dates = compute_loan_payments(finance_sb, schema)
    loan_kpis = compute_loans_kpis(finance_sb, schema)

    loans_outstanding = float(loan_kpis["principal_active"])
    cash_available_raw = foundation_total + repayments_total + interest_paid - loans_outstanding - payouts_total
    cash_available = max(cash_available_raw, 0.0)
    net_available = cash_available + float(pot)

    st.markdown("### 🏦 Financial Summary")

    st.markdown(glass_open(), unsafe_allow_html=True)
    f1, f2, f3, f4, f5, f6, f7 = st.columns(7)
    with f1:
        st.markdown(kpi_card("Foundation Total", _fmt_money(foundation_total, 0), "blue", sub="foundation_contributions"), unsafe_allow_html=True)
    with f2:
        st.markdown(kpi_card("Payouts Total", _fmt_money(payouts_total, 0), "orange", sub="payouts"), unsafe_allow_html=True)
    with f3:
        st.markdown(kpi_card("Interest Paid", _fmt_money(interest_paid, 0), "green", sub="loans: generated − unpaid"), unsafe_allow_html=True)
    with f4:
        st.markdown(kpi_card("Loan Payments", _fmt_money(repayments_total, 0), "green", sub="loan_payments"), unsafe_allow_html=True)
    with f5:
        st.markdown(kpi_card("Outstanding Principal", _fmt_money(loans_outstanding, 0), "red", sub="loans.principal_current"), unsafe_allow_html=True)
    with f6:
        st.markdown(kpi_card("Cash Available", _fmt_money(cash_available, 0), "green", sub="foundation + (payments+interest) − principal − payouts"), unsafe_allow_html=True)
    with f7:
        st.markdown(kpi_card("Net Available", _fmt_money(net_available, 0), "blue", sub="Cash Available + Current Pot"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    if cash_available_raw < 0:
        st.warning(
            f"⚠️ Cash Available RAW is negative ({cash_available_raw:,.0f}) before flooring to 0. "
            "This usually means outstanding loans + payouts exceed foundation + returns."
        )

    st.divider()

    # ---------- loans KPIs ----------
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

    # ---------- repayment plan ----------
    plan_df = build_repayment_plan(finance_sb, schema, last_payment_dates)
    if plan_df.empty:
        st.info("No active/open loans found.")
    else:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.markdown("#### 🗓️ Loan Repayment Plan")
        st.caption(f"Due = {DUE_DAYS} days from last payment (or borrow_date if never paid).")
        st.dataframe(plan_df, use_container_width=True, hide_index=True)
        st.markdown(glass_close(), unsafe_allow_html=True)

    # ---------- debug ----------
    with st.expander("🔎 Debug", expanded=False):
        st.write("app_state", state)
        st.write("foundation_total", foundation_total)
        st.write("payouts_total", payouts_total)
        st.write("interest_paid", interest_paid)
        st.write("loan_payments_total", repayments_total)
        st.write("loan_kpis", loan_kpis)
        st.write("current_pot", pot)
        st.write("cash_available_raw", cash_available_raw)
        st.write("cash_available", cash_available)
        st.write("net_available", net_available)

    if sb_service is None:
        st.warning("Write/admin features disabled (no service key).")
    else:
        st.success("Service key available.")
