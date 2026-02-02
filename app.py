
# dashboard_panel.py ✅ COMPLETE SINGLE FILE (NO SQL, NO legacy)
# 🚫 NO big page header here — app.py already renders the top header (prevents duplicate)
# ✅ Exports: render_dashboard (what app.py imports)
# ✅ Attendance: Present/Absent COUNTS (NOT percent) + dedupe by (member_id, session_id)
# ✅ Finance model preserved:
#    Cash Available = foundation + loan_payments + interest_ledger + fines_paid − outstanding_principal
#    Net Available  = Cash Available + Current Pot
# ✅ Auto-refresh when app_state changes
# ✅ No raw SQL; only Supabase client .table().select()

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

DUE_DAYS = 28


# ============================================================
# THEME (NO HEADER TEXT HERE)
# ============================================================
def inject_dashboard_theme():
    st.markdown(
        """
        <style>
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
            font-size: 26px;
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
        </style>
        """,
        unsafe_allow_html=True,
    )


def glass_open() -> str:
    return "<div class='glass'>"


def glass_close() -> str:
    return "</div>"


def kpi_card(label: str, value: str, color: str = "blue", sub: str | None = None) -> str:
    sub_html = f"<div class='kpi-sub'>{sub}</div>" if sub else ""
    return f"""
    <div class="kpi">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value {color}">{value}</div>
        {sub_html}
    </div>
    """


# ============================================================
# SAFE HELPERS (NO SQL)
# ============================================================
def safe_table(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int | None = 20000,
    order_by: str | None = None,
    desc: bool = True,
) -> List[dict]:
    try:
        q = sb.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit is not None:
            q = q.limit(int(limit))
        return q.execute().data or []
    except Exception:
        return []


def safe_table_order_fallback(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int | None = 20000,
    order_candidates: List[str] | None = None,
    desc: bool = True,
) -> List[dict]:
    order_candidates = order_candidates or []
    for c in order_candidates:
        try:
            rows = safe_table(sb, schema, table, cols=cols, limit=limit, order_by=c, desc=desc)
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


def table_exists(sb, schema: str, table: str) -> bool:
    try:
        sb.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def _num(x, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _fmt_money(x, decimals: int = 0) -> str:
    try:
        v = float(x)
        return f"{v:,.{decimals}f}" if decimals else f"{v:,.0f}"
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
# FINANCE COMPUTATIONS
# ============================================================
def compute_interest_ledger(sb, schema: str) -> Tuple[float, float]:
    if not table_exists(sb, schema, "interest_ledger"):
        return 0.0, 0.0

    rows = safe_table_order_fallback(
        sb, schema, "interest_ledger", "*",
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
        v = _num(r.get("amount"), 0.0)
        all_time += v
        im = str(r.get("interest_month") or "").strip()
        if im.startswith(month_prefix):
            this_month += v
    return float(this_month), float(all_time)


def compute_loan_payments(sb, schema: str) -> Tuple[float, Dict[int, date]]:
    if not table_exists(sb, schema, "loan_payments"):
        return 0.0, {}

    rows = safe_table_order_fallback(
        sb, schema, "loan_payments", "*",
        limit=20000,
        order_candidates=["paid_at", "created_at", "id"],
        desc=True,
    )

    total = 0.0
    last_by_loan: Dict[int, date] = {}
    for r in rows or []:
        total += _num(r.get("amount"), 0.0)
        try:
            lid = int(r.get("loan_id"))
        except Exception:
            continue
        d = _to_date(r.get("paid_at") or r.get("created_at"))
        if d is not None and (lid not in last_by_loan or d > last_by_loan[lid]):
            last_by_loan[lid] = d

    return float(total), last_by_loan


def compute_loans_kpis(sb, schema: str) -> Dict[str, Any]:
    if not table_exists(sb, schema, "loans"):
        return {"active_loans": 0, "principal_active": 0.0, "total_due_active": 0.0}

    rows = safe_table(sb, schema, "loans", "*", limit=20000)
    active = 0
    principal_sum = 0.0
    due_sum = 0.0

    for r in rows or []:
        status = str(r.get("status") or "").lower().strip()
        if status not in ("active", "open"):
            continue
        active += 1
        pc = _num(r.get("principal_current") or r.get("principal"), 0.0)
        principal_sum += pc
        due_sum += _num(r.get("total_due"), pc + _num(r.get("unpaid_interest"), 0.0))

    return {
        "active_loans": int(active),
        "principal_active": float(principal_sum),
        "total_due_active": float(due_sum),
    }


def sum_table_amount(sb, schema: str, table: str, amount_cols: List[str]) -> float:
    if not table_exists(sb, schema, table):
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
    if not table_exists(sb, schema, "fines"):
        return 0.0

    rows = safe_table_order_fallback(
        sb, schema, "fines", "*",
        limit=20000,
        order_candidates=["paid_at", "created_at", "id"],
        desc=True,
    )
    total = 0.0
    for r in rows or []:
        if str(r.get("status") or "").lower().strip() == "paid":
            total += _num(r.get("amount"), 0.0)
    return float(total)


def get_session_window(sb, schema: str, session_id: int) -> str:
    if not session_id:
        return "—"
    srow = safe_single(sb, schema, "sessions", "*", session_id=int(session_id))
    sd = srow.get("start_date")
    ed = srow.get("end_date")
    return f"{sd} → {ed}" if (sd and ed) else "—"


def build_repayment_plan(sb, schema: str, last_payment_dates: Dict[int, date]) -> pd.DataFrame:
    loans = safe_table(sb, schema, "loans", "*", limit=20000)
    out: List[dict] = []

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

        borrow_date = _to_date(r.get("borrow_date")) or _to_date(r.get("created_at")) or date.today()

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
    return df.sort_values("next_due_date", ascending=True) if not df.empty else df


# ============================================================
# ATTENDANCE (COUNTS + DEDUPE)
# ============================================================
def _dedupe_attendance_rows(dfa: pd.DataFrame) -> pd.DataFrame:
    if dfa is None or dfa.empty:
        return dfa

    for c in ("id", "member_id", "session_id", "present", "created_at"):
        if c not in dfa.columns:
            dfa[c] = None

    dfa["member_id"] = pd.to_numeric(dfa["member_id"], errors="coerce")
    dfa["session_id"] = pd.to_numeric(dfa["session_id"], errors="coerce")
    dfa["_created_at_sort"] = pd.to_datetime(dfa["created_at"], errors="coerce")
    dfa["_id_sort"] = pd.to_numeric(dfa["id"], errors="coerce")

    dfa = dfa.sort_values(["_created_at_sort", "_id_sort"], ascending=[False, False])
    dfa = dfa.drop_duplicates(subset=["member_id", "session_id"], keep="first")
    return dfa.drop(columns=["_created_at_sort", "_id_sort"], errors="ignore")


def load_attendance_counts(read_sb, schema: str) -> pd.DataFrame:
    members = safe_table(read_sb, schema, "members", "id,name,display_name,phone", limit=5000, order_by="id", desc=False)
    attendance = safe_table(read_sb, schema, "attendance", "id,member_id,session_id,present,created_at", limit=20000)

    dfm = pd.DataFrame(members or [])
    dfa = pd.DataFrame(attendance or [])

    if dfm.empty:
        return pd.DataFrame()

    for col in ("id", "name", "display_name", "phone"):
        if col not in dfm.columns:
            dfm[col] = None

    out = dfm.rename(columns={"id": "member_id"}).copy()
    out["member_name"] = out["display_name"].fillna("").astype(str).str.strip()
    out.loc[out["member_name"] == "", "member_name"] = out["name"].fillna("").astype(str).str.strip()

    if dfa.empty:
        out["present_count"] = 0
        out["absent_count"] = 0
        out["total_sessions"] = 0
        return out[["member_id", "member_name", "phone", "present_count", "absent_count", "total_sessions"]]

    dfa["present"] = dfa.get("present", False)
    dfa["present"] = dfa["present"].fillna(False).astype(bool)

    # ✅ dedupe duplicates
    dfa = _dedupe_attendance_rows(dfa)

    grp = (
        dfa.groupby("member_id", dropna=True)
        .agg(
            total_sessions=("session_id", "nunique"),
            present_count=("present", lambda s: int(s.sum())),
        )
        .reset_index()
    )
    grp["absent_count"] = (grp["total_sessions"] - grp["present_count"]).clip(lower=0).astype(int)

    out["member_id"] = pd.to_numeric(out["member_id"], errors="coerce")
    out = out.merge(grp[["member_id", "present_count", "absent_count", "total_sessions"]], on="member_id", how="left")

    out["present_count"] = out["present_count"].fillna(0).astype(int)
    out["absent_count"] = out["absent_count"].fillna(0).astype(int)
    out["total_sessions"] = out["total_sessions"].fillna(0).astype(int)

    return out[["member_id", "member_name", "phone", "present_count", "absent_count", "total_sessions"]]


def render_attendance_counts_chart(att_df: pd.DataFrame):
    if att_df is None or att_df.empty:
        st.info("No attendance data yet.")
        return

    df = att_df.copy()
    df["present_count"] = pd.to_numeric(df["present_count"], errors="coerce").fillna(0).astype(int)
    df["absent_count"] = pd.to_numeric(df["absent_count"], errors="coerce").fillna(0).astype(int)

    rank_by = st.selectbox("Rank members by", ["present_count", "absent_count"], index=0)
    max_n = max(5, min(50, len(df)))
    default_n = min(17, len(df))
    top_n = st.slider("Show top N members", min_value=5, max_value=max_n, value=default_n)

    df = df.sort_values(rank_by, ascending=False).head(int(top_n)).copy()
    chart_df = df.set_index("member_name")[["present_count", "absent_count"]].rename(
        columns={"present_count": "Present", "absent_count": "Absent"}
    )
    st.bar_chart(chart_df)


# ============================================================
# MAIN DASHBOARD ENTRY (NO DUPLICATE HEADER)
# ============================================================
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    """
    NOTE: This function intentionally DOES NOT render the big page header.
    app.py already renders the top bar header + refresh button.
    """
    inject_dashboard_theme()

    read_sb = sb_service if sb_service is not None else sb_anon
    finance_sb = sb_service if sb_service is not None else sb_anon

    # Auto-refresh if app_state changes
    _auto_refresh_if_state_changed(read_sb, schema)

    # --- App state ---
    state = safe_single(read_sb, schema, "app_state", "*", id=1)
    if not state:
        rows = safe_table(read_sb, schema, "app_state", "*", limit=1)
        state = rows[0] if rows else {}

    # --- Current session id (fallback to latest sessions.session_id) ---
    raw_cs = state.get("current_session_id")
    try:
        current_session_id = int(raw_cs) if raw_cs is not None and str(raw_cs).strip() != "" else None
    except Exception:
        current_session_id = None

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
    # Dedup safety
    seen_ids = set()
    dedup_members = []
    for m in members_rows or []:
        mid = m.get("id")
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        dedup_members.append(m)
    members_rows = dedup_members
    total_members = int(len(members_rows or []))

    # --- Beneficiary ---
    beneficiary_name = "—"
    beneficiary_id = next_member_id

    if table_exists(read_sb, schema, "v_next_beneficiary"):
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

    # --- Current pot (cycle contributions only) ---
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

    # --- KPIs (no big header) ---
    st.markdown(glass_open(), unsafe_allow_html=True)
    a1, a2, a3, a4 = st.columns(4)
    with a1:
        st.markdown(kpi_card("Session ID", str(current_session_id or "—"), "blue", sub=session_note), unsafe_allow_html=True)
    with a2:
        st.markdown(kpi_card("Session Window", window, "orange"), unsafe_allow_html=True)
    with a3:
        st.markdown(kpi_card("Total Members", str(total_members), "purple"), unsafe_allow_html=True)
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

    # --- Financial Summary ---
    foundation_total = sum_table_amount(finance_sb, schema, "foundation_contributions", ["amount"])
    payouts_total = sum_table_amount(finance_sb, schema, "payouts", ["payout_amount", "amount"])  # info only
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
        st.markdown(kpi_card("Foundation Total", _fmt_money(foundation_total, 0), "blue"), unsafe_allow_html=True)
    with f2:
        st.markdown(kpi_card("Payouts Total", _fmt_money(payouts_total, 0), "orange", sub="info only"), unsafe_allow_html=True)
    with f3:
        st.markdown(kpi_card("Interest This Month", _fmt_money(interest_this_month, 2), "green"), unsafe_allow_html=True)
    with f4:
        st.markdown(kpi_card("Interest All-time", _fmt_money(interest_all_time, 2), "green"), unsafe_allow_html=True)
    with f5:
        st.markdown(kpi_card("Loan Payments", _fmt_money(repayments_total, 0), "green"), unsafe_allow_html=True)
    with f6:
        st.markdown(kpi_card("Fines Paid", _fmt_money(fines_paid_total, 0), "purple"), unsafe_allow_html=True)
    with f7:
        st.markdown(kpi_card("Outstanding Principal", _fmt_money(loans_outstanding, 0), "red"), unsafe_allow_html=True)
    with f8:
        st.markdown(kpi_card("Cash Available", _fmt_money(cash_available, 0), "green"), unsafe_allow_html=True)
    with f9:
        st.markdown(kpi_card("Net Available", _fmt_money(net_available, 0), "blue"), unsafe_allow_html=True)
    st.markdown(glass_close(), unsafe_allow_html=True)

    if cash_available_raw < 0:
        st.warning(
            f"⚠️ Cash Available RAW is negative ({cash_available_raw:,.0f}) before flooring to 0. "
            "Outstanding loans exceed foundation cash-in."
        )

    st.divider()

    # --- Attendance (Counts) ---
    st.markdown("### ✅ Attendance • All-time Summary (Counts)")
    att_df = load_attendance_counts(read_sb, schema)
    render_attendance_counts_chart(att_df)

    st.divider()

    # --- Loans summary + repayment plan ---
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

    with st.expander("🔎 Debug", expanded=False):
        st.write("session_note", session_note)
        st.write("current_session_id", current_session_id)
        st.write("next_member_id", next_member_id)
        st.write("beneficiary_id", beneficiary_id)
        st.write("beneficiary_name", beneficiary_name)
        st.write("pot", pot)
        st.write("members_paid", members_paid)
        st.write("foundation_total", foundation_total)
        st.write("payouts_total(info)", payouts_total)
        st.write("interest_this_month", interest_this_month)
        st.write("interest_all_time", interest_all_time)
        st.write("loan_payments_total", repayments_total)
        st.write("fines_paid_total", fines_paid_total)
        st.write("loan_kpis", loan_kpis)
        st.write("cash_available_raw", cash_available_raw)
        st.write("cash_available", cash_available)
        st.write("net_available", net_available)


# ============================================================
# COMPATIBILITY (typo safety)
# ============================================================
def render_dashbaord(sb_anon, sb_service, schema: str = "public"):
    return render_dashboard(sb_anon, sb_service, schema=schema)


__all__ = ["render_dashboard", "render_dashbaord"]
