
# dashboard_panel.py ✅ COMPLETE SINGLE CODE (FINAL + AUTO-REFRESH ON CYCLE CHANGE + SINGLE POT OF TRUTH, NO SQL)
# ✅ Keeps your existing dashboard structure + theme
# ✅ Option A enforced: Current Pot = CURRENT ACTIVE SESSION contributions ONLY (v_current_cycle_kpis.cycle_total)
# ✅ Interest KPI: PAID interest (generated - unpaid) from loans_legacy (ACTIVE loans)
# ✅ Finance view still used (dashboard_finance_view) but we ALSO compute a "single pot of truth" from legacy tables
# ✅ NO SQL: reads legacy tables via Supabase + computes totals in Python
# ✅ Adds:
#    - Single Financial Pot of Truth (cash available)
#    - Borrowed (Active), Outstanding (Active)
#    - Loan Repayment Plan (estimated next due)

from __future__ import annotations

from datetime import date, datetime, timedelta
import streamlit as st
import pandas as pd


# ============================================================
# THEME (Dark dotted grid + glass KPI cards)
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

        .stButton button {
            border-radius: 14px !important;
            border: 1px solid rgba(255,255,255,0.10) !important;
            background: rgba(255,255,255,0.04) !important;
        }
        .stButton button:hover {
            border: 1px solid rgba(255,255,255,0.20) !important;
            background: rgba(255,255,255,0.06) !important;
        }

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
# Safe helpers
# ============================================================
def safe_view(sb, schema: str, name: str, limit: int = 1):
    try:
        q = sb.schema(schema).table(name).select("*")
        if limit is not None:
            q = q.limit(int(limit))
        return q.execute().data or []
    except Exception:
        return []


def safe_select_where(sb, schema: str, table: str, cols: str, where_col: str, where_val, limit: int = 1):
    try:
        q = sb.schema(schema).table(table).select(cols).eq(where_col, where_val)
        if limit is not None:
            q = q.limit(int(limit))
        return q.execute().data or []
    except Exception:
        return []


def safe_table(sb, schema: str, table: str, cols: str = "*", limit: int = 20000, order_by: str | None = None, desc: bool = True):
    """Read a table (not a view) safely; returns [] on error."""
    try:
        q = sb.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=desc)
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
    for k in keys:
        if row and k in row and row.get(k) not in (None, "", "null"):
            return row.get(k)
    return default


def _s(x) -> str | None:
    if x is None:
        return None
    s = str(x).strip()
    return s if s else None


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
# Interest paid (legacy inference)
# ============================================================
def compute_interest_paid_all_time(sb, schema: str = "public") -> float:
    """
    Paid interest is implied as:
      SUM(total_interest_generated - unpaid_interest) for ACTIVE loans only.
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


# ============================================================
# Auto-refresh when cycle changes
# ============================================================
def _read_cycle_stamp(sb, schema: str) -> str:
    rows = safe_view(sb, schema, "app_state", limit=1)
    r = rows[0] if rows else {}
    return "|".join(
        [
            str(_pick(r, "current_session_id", default="")),
            str(_pick(r, "next_payout_index", default="")),
            str(_pick(r, "next_payout_date", default="")),
            str(_pick(r, "updated_at", default="")),
        ]
    )


def _auto_refresh_if_cycle_changed(sb_anon, schema: str):
    stamp = _read_cycle_stamp(sb_anon, schema)
    prev = st.session_state.get("_cycle_stamp_dashboard")
    st.session_state["_cycle_stamp_dashboard"] = stamp
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
# ✅ SINGLE FINANCIAL POT OF TRUTH (NO SQL) — LEGACY TABLES
# ============================================================
def _sum_amount_rows(rows: list[dict], amount_keys: list[str], status_key: str | None = None) -> float:
    total = 0.0
    for r in rows:
        if status_key:
            s = str(r.get(status_key, "") or "").lower().strip()
            # accept paid if blank or paid/completed/true-ish
            if s not in ("", "paid", "completed", "ok", "yes", "true", "1"):
                continue
        val = None
        for k in amount_keys:
            if k in r:
                val = r.get(k)
                break
        total += _num(val, 0.0)
    return float(total)


def compute_loans_borrowed_balance_disbursed(sb, schema: str = "public") -> dict:
    """
    From loans_legacy (robust to column names):
      - borrowed_active (sum principal for ACTIVE loans)
      - outstanding_active (sum balance for ACTIVE loans)
      - loans_disbursed_all_time (sum principal for ALL loans)  -> cash OUT
    """
    rows = safe_table(sb, schema, "loans_legacy", "*", limit=20000)
    if not rows:
        return {"active_loans": 0, "borrowed_active": 0.0, "outstanding_active": 0.0, "disbursed_all_time": 0.0}

    borrowed_keys = ["loan_amount", "loan_amnt", "principal", "principal_amount", "amount_borrowed", "borrowed_amount", "amount"]
    balance_keys = ["outstanding_balance", "balance", "loan_balance", "remaining_balance", "amount_due", "total_due", "out_prncp"]

    active_loans = 0
    borrowed_active = 0.0
    outstanding_active = 0.0
    disbursed_all = 0.0

    for r in rows:
        status = str(r.get("status", "") or r.get("loan_status", "") or "").lower().strip()

        principal = None
        for k in borrowed_keys:
            if k in r:
                principal = r.get(k)
                break
        principal_num = _num(principal, 0.0)
        disbursed_all += principal_num

        if status != "active":
            continue

        active_loans += 1
        borrowed_active += principal_num

        bal = None
        for k in balance_keys:
            if k in r:
                bal = r.get(k)
                break

        if bal is None:
            repaid = _pick(r, "total_repaid", "repaid_amount", "amount_repaid", "total_payment", "total_paid", default=None)
            if repaid is not None:
                bal = principal_num - _num(repaid, 0.0)

        outstanding_active += max(_num(bal, 0.0), 0.0)

    return {
        "active_loans": int(active_loans),
        "borrowed_active": float(borrowed_active),
        "outstanding_active": float(outstanding_active),
        "disbursed_all_time": float(disbursed_all),
    }


def compute_repayments_legacy(sb, schema: str = "public") -> tuple[float, dict[int, date]]:
    """
    loan_repayments_legacy:
      - total repayments sum (cash IN)
      - last payment date per loan_id (for repayment plan)
    """
    rows = safe_table(sb, schema, "loan_repayments_legacy", "*", limit=20000, order_by="created_at", desc=True)
    if not rows:
        return 0.0, {}

    amount_keys = ["amount", "payment_amount", "paid_amount", "repayment_amount"]
    loan_id_keys = ["loan_id", "loanid", "legacy_loan_id"]
    date_keys = ["payment_date", "paid_at", "created_at", "repayment_date", "date"]

    total = 0.0
    last_by_loan: dict[int, date] = {}

    for r in rows:
        amt = None
        for k in amount_keys:
            if k in r:
                amt = r.get(k)
                break
        total += _num(amt, 0.0)

        lid = None
        for k in loan_id_keys:
            if k in r:
                lid = r.get(k)
                break
        try:
            lid_int = int(lid) if lid is not None and str(lid).strip().isdigit() else None
        except Exception:
            lid_int = None

        dval = None
        for k in date_keys:
            if k in r:
                dval = r.get(k)
                break
        d = _to_date(dval)

        if lid_int is not None and d is not None:
            if lid_int not in last_by_loan or d > last_by_loan[lid_int]:
                last_by_loan[lid_int] = d

    return float(total), last_by_loan


def build_loan_repayment_plan(sb, schema: str, last_payment_dates: dict[int, date]) -> pd.DataFrame:
    """
    Simple plan (NO SQL):
      next_due_date = last payment date + 30 days (else created_at/issue_date + 30)
    """
    loans = safe_table(sb, schema, "loans_legacy", "*", limit=20000)
    if not loans:
        return pd.DataFrame()

    borrowed_keys = ["loan_amount", "loan_amnt", "principal", "principal_amount", "amount_borrowed", "borrowed_amount", "amount"]
    balance_keys = ["outstanding_balance", "balance", "loan_balance", "remaining_balance", "amount_due", "total_due", "out_prncp"]
    created_keys = ["issue_date", "issued_at", "start_date", "created_at"]
    installment_keys = ["installment", "monthly_payment", "payment_amount"]

    out = []
    for r in loans:
        status = str(r.get("status", "") or r.get("loan_status", "") or "").lower().strip()
        if status != "active":
            continue

        loan_id = _pick(r, "id", "loan_id", "legacy_loan_id", default=None)
        try:
            loan_id_int = int(loan_id) if loan_id is not None and str(loan_id).strip().isdigit() else None
        except Exception:
            loan_id_int = None

        member_name = str(_pick(r, "member_name", "borrower_name", default="—")).strip() or "—"

        principal = None
        for k in borrowed_keys:
            if k in r:
                principal = r.get(k)
                break

        balance = None
        for k in balance_keys:
            if k in r:
                balance = r.get(k)
                break

        inst = None
        for k in installment_keys:
            if k in r:
                inst = r.get(k)
                break

        created_val = None
        for k in created_keys:
            if k in r:
                created_val = r.get(k)
                break
        created_date = _to_date(created_val) or date.today()

        if loan_id_int is not None and loan_id_int in last_payment_dates:
            last_paid = last_payment_dates[loan_id_int]
            next_due = last_paid + timedelta(days=30)
        else:
            last_paid = None
            next_due = created_date + timedelta(days=30)

        out.append({
            "loan_id": loan_id_int if loan_id_int is not None else (str(loan_id) if loan_id is not None else "—"),
            "member_name": member_name,
            "borrowed": _num(principal, 0.0),
            "outstanding_balance": _num(balance, 0.0),
            "installment": _num(inst, 0.0) if inst is not None else None,
            "last_paid": last_paid.isoformat() if isinstance(last_paid, date) else "—",
            "next_due_date": next_due.isoformat(),
        })

    df = pd.DataFrame(out)
    if df.empty:
        return df
    df = df.sort_values("next_due_date", ascending=True)
    return df


# ============================================================
# Dashboard renderer
# ============================================================
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    inject_dashboard_theme()
    _auto_refresh_if_cycle_changed(sb_anon, schema)

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
                safe_select_where(sb_anon, schema, "sessions_legacy", "start_date,end_date,session_number", "session_number", sid_int, limit=1)
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
    # 2) CURRENT POT (Option A) from your view
    # =========================================================
    kpis = (safe_view(sb_anon, schema, "v_current_cycle_kpis", limit=1) or [{}])[0]
    cycle_total_num = _num(_pick(kpis, "cycle_total", default=0.0), default=0.0)
    members_paid_num = int(_num(_pick(kpis, "members_paid", default=0), default=0))
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
    # 3) PAYOUT STATUS (kept)
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
            kpi_card("Payout Ready", "YES" if ready is True else ("NO" if ready is False else "—"), "green" if ready else "red"),
            unsafe_allow_html=True,
        )
    with s3:
        st.markdown(kpi_card("Missing Signatures", missing if missing else "—", "purple"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # =========================================================
    # 4) SINGLE FINANCIAL POT OF TRUTH (NO SQL) — LEGACY TABLES
    # =========================================================
    # Read legacy tables (paid totals)
    foundation_rows = safe_table(sb_anon, schema, "foundation_payments_legacy", "*", limit=20000)
    fines_rows = safe_table(sb_anon, schema, "fines_legacy", "*", limit=20000)

    foundation_paid = _sum_amount_rows(foundation_rows, ["amount", "paid_amount", "payment_amount"])
    fines_paid = _sum_amount_rows(fines_rows, ["amount", "fine_amount", "paid_amount"], status_key="status")

    interest_paid = compute_interest_paid_all_time(sb_anon, schema="public")

    loan_kpis = compute_loans_borrowed_balance_disbursed(sb_anon, schema=schema)
    loans_disbursed = float(loan_kpis["disbursed_all_time"])

    repayments_paid, last_payment_dates = compute_repayments_legacy(sb_anon, schema=schema)

    cash_available = foundation_paid + fines_paid + interest_paid + repayments_paid - loans_disbursed
    net_available = float(cash_available) + float(current_pot_num)

    st.markdown("### 🏦 Single Financial Pot of Truth (Legacy, No SQL)")

    st.markdown(glass_open(), unsafe_allow_html=True)
    f1, f2, f3, f4, f5, f6, f7 = st.columns(7)
    with f1:
        st.markdown(kpi_card("Foundation Paid", _fmt_money(foundation_paid, 0), "blue"), unsafe_allow_html=True)
    with f2:
        st.markdown(kpi_card("Fines Paid", _fmt_money(fines_paid, 0), "purple"), unsafe_allow_html=True)
    with f3:
        st.markdown(kpi_card("Interest Paid", _fmt_money(interest_paid, 2), "green"), unsafe_allow_html=True)
    with f4:
        st.markdown(kpi_card("Loan Repayments", _fmt_money(repayments_paid, 0), "green", sub="Cash IN"), unsafe_allow_html=True)
    with f5:
        st.markdown(kpi_card("Loans Disbursed", _fmt_money(loans_disbursed, 0), "red", sub="Cash OUT"), unsafe_allow_html=True)
    with f6:
        st.markdown(kpi_card("Cash Available", _fmt_money(cash_available, 0), "green",
                             sub="Foundation + Fines + Interest + Repayments − Disbursed"), unsafe_allow_html=True)
    with f7:
        st.markdown(kpi_card("Net Available", _fmt_money(net_available, 0), "blue",
                             sub="Cash Available + Current Pot"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    st.divider()

    # =========================================================
    # 5) LOANS: Borrowed + Balance + Repayment Plan
    # =========================================================
    st.markdown("### 💳 Loans Summary (Legacy)")

    active_loans = int(loan_kpis["active_loans"])
    borrowed_active = float(loan_kpis["borrowed_active"])
    outstanding_active = float(loan_kpis["outstanding_active"])

    st.markdown(glass_open(), unsafe_allow_html=True)
    l1, l2, l3 = st.columns(3)
    with l1:
        st.markdown(kpi_card("Active Loans", str(active_loans), "purple"), unsafe_allow_html=True)
    with l2:
        st.markdown(kpi_card("Borrowed (Active)", _fmt_money(borrowed_active, 0), "orange"), unsafe_allow_html=True)
    with l3:
        st.markdown(kpi_card("Outstanding (Active)", _fmt_money(outstanding_active, 0), "red"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    plan_df = build_loan_repayment_plan(sb_anon, schema=schema, last_payment_dates=last_payment_dates)
    if plan_df.empty:
        st.info("No active loans found (or loans_legacy not readable).")
    else:
        st.markdown(glass_open(), unsafe_allow_html=True)
        st.markdown("#### 🗓️ Loan Repayment Plan (Estimated)")
        st.caption("Next due = last payment date + 30 days (or loan created date + 30 days).")
        show_cols = ["loan_id", "member_name", "borrowed", "outstanding_balance", "installment", "last_paid", "next_due_date"]
        st.dataframe(plan_df[show_cols], use_container_width=True, hide_index=True)
        st.markdown(glass_close(), unsafe_allow_html=True)

    # =========================================================
    # DEBUG (kept)
    # =========================================================
    with st.expander("🔎 Debug (raw rows)", expanded=False):
        st.write("app_state stamp", st.session_state.get("_cycle_stamp_dashboard"))
        st.write("dashboard_next_view", dash)
        st.write("v_current_cycle_kpis", kpis)
        st.write("foundation_paid_rows", len(foundation_rows))
        st.write("fines_paid_rows", len(fines_rows))
        st.write("loan_kpis", loan_kpis)
        st.write("cash_available", cash_available)
        st.write("net_available", net_available)

    if sb_service is None:
        st.warning("Admin/write features disabled (no service key).")
    else:
        st.success("Admin/write features enabled.")
