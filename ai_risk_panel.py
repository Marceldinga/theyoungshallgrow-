
# ai_risk_panel.py ✅ COMPLETE SINGLE-FILE — NJANGI STANDARD (NO legacy) — XGBoost ML + PAYOUTS FIXED
# + ✅ EXTRA AI SUITE (NO API KEY): Reliability • Dropout • Fraud/Anomaly • Liquidity Forecast • Loan Recommendation • Alerts • Local Chatbox
# ------------------------------------------------------------------------------
# ✅ Uses ONLY new tables:
#   - members
#   - contributions
#   - loans
#   - loan_payments (optional)
#   - foundation_contributions
#   - fines (optional)
#   - payouts (optional)  ✅ FIXED: payout_amount / payout_date
#
# ✅ Schema-safe for YOUR loans columns:
#    - principal, principal_current, total_due, unpaid_interest, last_paid_at,
#      borrow_date, due_cycle_days, interest_rate_monthly, status
# ✅ loan_payments may have member_id OR only loan_id -> auto-joins via loans to get member_id
# ✅ Always produces numbers (no blank NaNs in snapshot)
# ✅ Cache-safe: no unhashable supabase clients in cache args
# ✅ UTC-safe date math (no tz-naive vs tz-aware errors)
#
# ✅ ML uses XGBoost:
#    - Trains on loans rows: closed=0, active=1
#    - Scores member risk as MAX probability among their active loans
#    - Gate by MIN_LOANS_FOR_ML (default 20)
#    - Caches trained model in st.session_state using a dataframe fingerprint
#
# 🔧 REQUIREMENT (Railway):
#    Add to requirements.txt:  xgboost==2.0.3
# ------------------------------------------------------------------------------

from __future__ import annotations

import streamlit as st
import pandas as pd
import numpy as np


# =========================
# CONFIG
# =========================
MIN_LOANS_FOR_ML = 20  # set to 5 if you want ML to run immediately (unstable when tiny)
CACHE_TTL_SECONDS = 60


# ============================================================
# Safe Supabase reads
# ============================================================
def _api_msg(e: Exception) -> str:
    return repr(e)


def _safe_select(
    client,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 5000,
    order_by: str | None = None,
    desc: bool = True,
    silent: bool = False,
):
    try:
        q = client.schema(schema).table(table).select(cols)
        if order_by:
            try:
                q = q.order(order_by, desc=desc)
            except Exception:
                q = client.schema(schema).table(table).select(cols)

        if limit:
            q = q.limit(int(limit))

        resp = q.execute()
        return resp.data or []
    except Exception as e:
        if not silent:
            st.error(f"Failed reading {schema}.{table}")
            st.code(_api_msg(e), language="text")
        return []


def _safe_select_autosort(client, schema: str, table: str, cols: str = "*", limit: int = 5000, desc: bool = True):
    for c in ["updated_at", "created_at", "paid_at", "last_paid_at", "borrow_date", "payout_date", "id"]:
        rows = _safe_select(client, schema, table, cols=cols, limit=limit, order_by=c, desc=desc, silent=True)
        if rows:
            return rows
    return _safe_select(client, schema, table, cols=cols, limit=limit, order_by=None, desc=desc, silent=True)


def _table_exists(client, schema: str, table: str) -> bool:
    try:
        client.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


# ============================================================
# Pandas helpers
# ============================================================
def _to_int(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0).astype(int)


def _to_num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def _to_dt_utc(s) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", utc=True)


def _utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _days_since(now_utc: pd.Timestamp, dt_series) -> pd.Series:
    dt_utc = pd.to_datetime(dt_series, errors="coerce", utc=True)
    out = (now_utc - dt_utc).dt.days
    return out.fillna(999).astype(int)


def _fill_feature_defaults(X: pd.DataFrame) -> pd.DataFrame:
    X = X.copy()
    for col in X.columns:
        if col == "member_id":
            continue
        if col.endswith("_count") or col.endswith("_n"):
            X[col] = pd.to_numeric(X[col], errors="coerce").fillna(0).astype(int)
        elif col.startswith("days_since_") or col.endswith("_days"):
            X[col] = pd.to_numeric(X[col], errors="coerce").fillna(999).astype(int)
        else:
            X[col] = pd.to_numeric(X[col], errors="coerce").fillna(0.0)
    return X


# ============================================================
# Cache-safe loaders (NO unhashable clients in cache args)
# ============================================================
def _ensure_clients_in_state(sb_anon, sb_service):
    st.session_state["__sb_anon__"] = sb_anon
    st.session_state["__sb_service__"] = sb_service


def _get_client_from_state(use_service: bool = False):
    sb_anon = st.session_state.get("__sb_anon__")
    sb_service = st.session_state.get("__sb_service__")
    if use_service and sb_service is not None:
        return sb_service
    return sb_anon


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _load_table_cached(schema: str, table: str, cols: str = "*", limit: int = 5000, use_service: bool = False) -> pd.DataFrame:
    client = _get_client_from_state(use_service=use_service)
    if client is None:
        return pd.DataFrame()

    rows = _safe_select_autosort(client, schema, table, cols=cols, limit=limit, desc=True)

    # If anon is empty but service exists, auto-retry (helps when RLS blocks anon reads)
    if not rows and not use_service:
        sb_service = st.session_state.get("__sb_service__")
        if sb_service is not None:
            rows = _safe_select_autosort(sb_service, schema, table, cols=cols, limit=limit, desc=True)

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _load_table(sb_anon, sb_service, schema: str, table: str, cols: str = "*", limit: int = 5000) -> pd.DataFrame:
    _ensure_clients_in_state(sb_anon, sb_service)
    return _load_table_cached(schema=schema, table=table, cols=cols, limit=limit, use_service=False)


def _load_table_service(sb_anon, sb_service, schema: str, table: str, cols: str = "*", limit: int = 5000) -> pd.DataFrame:
    _ensure_clients_in_state(sb_anon, sb_service)
    return _load_table_cached(schema=schema, table=table, cols=cols, limit=limit, use_service=True)


# ============================================================
# Payment loader schema-safe
# ============================================================
def _load_payments_schema_safe(sb_anon, sb_service, schema: str, limit: int = 20000) -> pd.DataFrame:
    if sb_service is None:
        pay = _load_table(sb_anon, sb_service, schema, "loan_payments", cols="*", limit=limit)
    else:
        pay = _load_table_service(sb_anon, sb_service, schema, "loan_payments", cols="*", limit=limit)

    if pay.empty:
        return pay

    cols = set(pay.columns)
    if "member_id" in cols:
        keep = [c for c in ["member_id", "amount", "paid_at", "created_at", "loan_id"] if c in cols]
        return pay[keep].copy()

    if "loan_id" not in cols and "loans_id" in cols:
        pay = pay.rename(columns={"loans_id": "loan_id"})

    if "loan_id" not in pay.columns:
        return pd.DataFrame()

    loans_map = (
        _load_table_service(sb_anon, sb_service, schema, "loans", cols="id,member_id", limit=10000)
        if sb_service is not None
        else _load_table(sb_anon, sb_service, schema, "loans", cols="id,member_id", limit=10000)
    )

    if loans_map.empty or "id" not in loans_map.columns or "member_id" not in loans_map.columns:
        return pd.DataFrame()

    loans_map = loans_map.copy()
    loans_map["id"] = _to_int(loans_map["id"])
    loans_map["member_id"] = _to_int(loans_map["member_id"])

    pay2 = pay.copy()
    pay2["loan_id"] = _to_int(pay2["loan_id"])
    pay2 = pay2.merge(loans_map.rename(columns={"id": "loan_id"}), on="loan_id", how="left")

    if "amount" not in pay2.columns:
        pay2["amount"] = 0.0
    if "paid_at" not in pay2.columns:
        pay2["paid_at"] = pay2.get("created_at")

    keep = [c for c in ["member_id", "amount", "paid_at", "created_at", "loan_id"] if c in pay2.columns]
    pay2 = pay2[keep].copy()
    pay2 = pay2[pay2["member_id"].fillna(0).astype(int) > 0].copy()
    return pay2


# ============================================================
# Feature engineering per member
# ============================================================
def _build_member_features(
    members: pd.DataFrame,
    contrib: pd.DataFrame,
    loans: pd.DataFrame,
    payments: pd.DataFrame,
    payouts: pd.DataFrame,
    fines: pd.DataFrame,
    foundation: pd.DataFrame,
) -> pd.DataFrame:
    members = members.copy()
    members["id"] = _to_int(members["id"])
    members = members[members["id"] > 0].copy()
    now = _utc_now()

    base = pd.DataFrame({"member_id": members["id"].astype(int)})

    # ----------------------------
    # Contributions
    # ----------------------------
    cfeat = base.copy()
    if not contrib.empty and "member_id" in contrib.columns:
        c = contrib.copy()
        c["member_id"] = _to_int(c["member_id"])
        c["amount"] = _to_num(c.get("amount", 0))
        c["created_at"] = _to_dt_utc(c.get("created_at", pd.NaT))

        grp = c.groupby("member_id", dropna=False)
        cfeat = cfeat.merge(grp["amount"].sum().rename("contrib_total"), left_on="member_id", right_index=True, how="left")
        cfeat = cfeat.merge(grp["amount"].count().rename("contrib_count"), left_on="member_id", right_index=True, how="left")
        cfeat = cfeat.merge(grp["amount"].mean().rename("contrib_avg"), left_on="member_id", right_index=True, how="left")
        cfeat = cfeat.merge(grp["created_at"].max().rename("contrib_last_dt"), left_on="member_id", right_index=True, how="left")

        if "session_id" in c.columns:
            c["session_id"] = _to_int(c["session_id"])
            cfeat = cfeat.merge(
                c.groupby("member_id")["session_id"].nunique().rename("contrib_sessions_n"),
                left_on="member_id",
                right_index=True,
                how="left",
            )
    else:
        cfeat["contrib_total"] = 0.0
        cfeat["contrib_count"] = 0
        cfeat["contrib_avg"] = 0.0
        cfeat["contrib_sessions_n"] = 0
        cfeat["contrib_last_dt"] = pd.NaT

    cfeat["days_since_last_contrib"] = _days_since(now, cfeat["contrib_last_dt"])

    # ----------------------------
    # Loans
    # ----------------------------
    lfeat = base.copy()
    if not loans.empty and "member_id" in loans.columns:
        l = loans.copy()
        l["member_id"] = _to_int(l["member_id"])

        for col in ["principal", "principal_current", "total_due", "unpaid_interest"]:
            if col in l.columns:
                l[col] = _to_num(l[col])

        if "principal_current" in l.columns:
            l["balance_calc"] = l["principal_current"]
        elif "principal" in l.columns:
            l["balance_calc"] = l["principal"]
        else:
            l["balance_calc"] = 0.0

        l["status"] = l.get("status", "").astype(str).str.lower().fillna("")
        l["last_paid_at"] = _to_dt_utc(l.get("last_paid_at", pd.NaT))
        l["created_at"] = _to_dt_utc(l.get("created_at", pd.NaT))

        grp = l.groupby("member_id", dropna=False)
        lfeat = lfeat.merge(grp.size().rename("loan_count"), left_on="member_id", right_index=True, how="left")
        lfeat = lfeat.merge(grp["balance_calc"].sum().rename("loan_balance_sum"), left_on="member_id", right_index=True, how="left")

        if "principal_current" in l.columns:
            lfeat = lfeat.merge(grp["principal_current"].sum().rename("loan_principal_current_sum"), left_on="member_id", right_index=True, how="left")
        else:
            lfeat["loan_principal_current_sum"] = 0.0

        if "total_due" in l.columns:
            lfeat = lfeat.merge(grp["total_due"].sum().rename("loan_total_due_sum"), left_on="member_id", right_index=True, how="left")
        else:
            lfeat["loan_total_due_sum"] = 0.0

        bad_tokens = ["delinquent", "default", "overdue", "late", "arrears", "past due", "past_due", "unpaid"]
        lfeat = lfeat.merge(
            grp["status"].apply(lambda s: sum(any(tok in str(v) for tok in bad_tokens) for v in s)).rename("loan_bad_status_count"),
            left_on="member_id",
            right_index=True,
            how="left",
        )

        lfeat = lfeat.merge(grp["last_paid_at"].max().rename("loan_last_paid_dt"), left_on="member_id", right_index=True, how="left")
    else:
        lfeat["loan_count"] = 0
        lfeat["loan_balance_sum"] = 0.0
        lfeat["loan_principal_current_sum"] = 0.0
        lfeat["loan_total_due_sum"] = 0.0
        lfeat["loan_bad_status_count"] = 0
        lfeat["loan_last_paid_dt"] = pd.NaT

    # ----------------------------
    # Loan payments (repayments)
    # ----------------------------
    pfeat = base.copy()
    if not payments.empty and "member_id" in payments.columns:
        p = payments.copy()
        p["member_id"] = _to_int(p["member_id"])
        p["amount"] = _to_num(p.get("amount", 0))
        if "paid_at" in p.columns:
            p["paid_at"] = _to_dt_utc(p.get("paid_at", pd.NaT))
        else:
            p["paid_at"] = _to_dt_utc(p.get("created_at", pd.NaT))

        grp = p.groupby("member_id", dropna=False)
        pfeat = pfeat.merge(grp["amount"].count().rename("pay_count"), left_on="member_id", right_index=True, how="left")
        pfeat = pfeat.merge(grp["amount"].sum().rename("pay_total"), left_on="member_id", right_index=True, how="left")
        pfeat = pfeat.merge(grp["paid_at"].max().rename("pay_last_dt"), left_on="member_id", right_index=True, how="left")
    else:
        pfeat["pay_count"] = 0
        pfeat["pay_total"] = 0.0
        pfeat["pay_last_dt"] = pd.NaT

    pfeat["days_since_last_payment"] = _days_since(now, pfeat["pay_last_dt"])

    # ----------------------------
    # Payouts ✅ (your table uses payout_amount / payout_date)
    # ----------------------------
    poutfeat = base.copy()
    if not payouts.empty and "member_id" in payouts.columns:
        po = payouts.copy()
        po["member_id"] = _to_int(po["member_id"])

        amt_col = "payout_amount" if "payout_amount" in po.columns else ("amount" if "amount" in po.columns else None)
        if amt_col is None:
            po["payout_amount_calc"] = 0.0
        else:
            po["payout_amount_calc"] = _to_num(po[amt_col])

        dt_col = "payout_date" if "payout_date" in po.columns else ("created_at" if "created_at" in po.columns else None)
        if dt_col is None:
            po["payout_dt_calc"] = pd.NaT
        else:
            po["payout_dt_calc"] = _to_dt_utc(po[dt_col])

        grp = po.groupby("member_id", dropna=False)
        poutfeat = poutfeat.merge(grp["payout_amount_calc"].count().rename("payout_count"), left_on="member_id", right_index=True, how="left")
        poutfeat = poutfeat.merge(grp["payout_amount_calc"].sum().rename("payout_total"), left_on="member_id", right_index=True, how="left")
        poutfeat = poutfeat.merge(grp["payout_dt_calc"].max().rename("payout_last_dt"), left_on="member_id", right_index=True, how="left")
    else:
        poutfeat["payout_count"] = 0
        poutfeat["payout_total"] = 0.0
        poutfeat["payout_last_dt"] = pd.NaT

    poutfeat["days_since_last_payout"] = _days_since(now, poutfeat["payout_last_dt"])

    # ----------------------------
    # Fines
    # ----------------------------
    ffeat = base.copy()
    if not fines.empty and "member_id" in fines.columns:
        f = fines.copy()
        f["member_id"] = _to_int(f["member_id"])
        f["amount"] = _to_num(f.get("amount", 0))
        grp = f.groupby("member_id", dropna=False)
        ffeat = ffeat.merge(grp["amount"].sum().rename("fine_total"), left_on="member_id", right_index=True, how="left")
        ffeat = ffeat.merge(grp["amount"].count().rename("fine_count"), left_on="member_id", right_index=True, how="left")
    else:
        ffeat["fine_total"] = 0.0
        ffeat["fine_count"] = 0

    # ----------------------------
    # Foundation
    # ----------------------------
    fdfeat = base.copy()
    if not foundation.empty and "member_id" in foundation.columns:
        fd = foundation.copy()
        fd["member_id"] = _to_int(fd["member_id"])
        fd["amount"] = _to_num(fd.get("amount", 0))
        fd["created_at"] = _to_dt_utc(fd.get("created_at", pd.NaT))

        grp = fd.groupby("member_id", dropna=False)
        fdfeat = fdfeat.merge(grp["amount"].sum().rename("foundation_total"), left_on="member_id", right_index=True, how="left")
        fdfeat = fdfeat.merge(grp["amount"].count().rename("foundation_count"), left_on="member_id", right_index=True, how="left")
        fdfeat = fdfeat.merge(grp["created_at"].max().rename("foundation_last_dt"), left_on="member_id", right_index=True, how="left")
    else:
        fdfeat["foundation_total"] = 0.0
        fdfeat["foundation_count"] = 0
        fdfeat["foundation_last_dt"] = pd.NaT

    fdfeat["days_since_last_foundation"] = _days_since(now, fdfeat["foundation_last_dt"])

    # ----------------------------
    # Merge all features
    # ----------------------------
    X = (
        cfeat.merge(lfeat, on="member_id", how="left")
        .merge(pfeat, on="member_id", how="left")
        .merge(poutfeat, on="member_id", how="left")
        .merge(ffeat, on="member_id", how="left")
        .merge(fdfeat, on="member_id", how="left")
    )

    # Drop raw date cols (keep only days_since_* numbers)
    for dtcol in ["contrib_last_dt", "pay_last_dt", "foundation_last_dt", "loan_last_paid_dt", "payout_last_dt"]:
        if dtcol in X.columns:
            X.drop(columns=[dtcol], inplace=True)

    return _fill_feature_defaults(X)


# ============================================================
# Heuristic risk score
# ============================================================
def _compute_risk_score(row: pd.Series) -> tuple[float, list[str]]:
    reasons = []

    loan_balance = float(row.get("loan_balance_sum", 0.0))
    total_due = float(row.get("loan_total_due_sum", 0.0))
    bad_status = int(row.get("loan_bad_status_count", 0))
    days_pay = int(row.get("days_since_last_payment", 999))
    days_contrib = int(row.get("days_since_last_contrib", 999))
    fine_total = float(row.get("fine_total", 0.0))
    contrib_total = float(row.get("contrib_total", 0.0))
    contrib_count = int(row.get("contrib_count", 0))

    score = 0.0

    if loan_balance > 0:
        score += 0.20
        reasons.append("Has outstanding loan balance")

    if total_due > 0 and total_due >= max(loan_balance, 1.0) * 1.02:
        score += 0.10
        reasons.append("Total due indicates interest/arrears")

    if bad_status > 0:
        score += min(0.30, 0.10 * bad_status)
        reasons.append("Loan status flagged as overdue/delinquent/etc.")

    if days_pay >= 30 and loan_balance > 0:
        score += 0.25
        reasons.append("No recent loan payment (≥30 days) while loan balance > 0")
    elif days_pay >= 14 and loan_balance > 0:
        score += 0.15
        reasons.append("No recent loan payment (≥14 days) while loan balance > 0")

    if days_contrib >= 30:
        score += 0.15
        reasons.append("No recent contribution (≥30 days)")
    elif days_contrib >= 14:
        score += 0.08
        reasons.append("No recent contribution (≥14 days)")

    if fine_total > 0:
        score += min(0.15, fine_total / 2000.0)
        reasons.append("Has fines")

    if contrib_total >= 2000:
        score -= 0.05
        reasons.append("Strong contributions reduce risk")
    if contrib_count >= 6:
        score -= 0.05
        reasons.append("Consistent contribution frequency reduces risk")

    score = float(np.clip(score, 0.0, 1.0))
    return score, reasons[:6]


# ============================================================
# ✅ EXTRA AI SUITE (no API key) — scoring + liquidity + alerts + chat
# ============================================================
def _clip01(x: float) -> float:
    try:
        return float(np.clip(float(x), 0.0, 1.0))
    except Exception:
        return 0.0


def _compute_reliability_score(row: pd.Series) -> tuple[int, list[str]]:
    reasons = []
    score = 70.0

    contrib_count = int(row.get("contrib_count", 0))
    contrib_total = float(row.get("contrib_total", 0.0))
    days_contrib = int(row.get("days_since_last_contrib", 999))

    fine_count = int(row.get("fine_count", 0))
    fine_total = float(row.get("fine_total", 0.0))

    loan_balance = float(row.get("loan_balance_sum", 0.0))
    days_pay = int(row.get("days_since_last_payment", 999))
    bad_status = int(row.get("loan_bad_status_count", 0))

    if contrib_count >= 8:
        score += 12
        reasons.append("Consistent contributions (8+ records)")
    elif contrib_count >= 4:
        score += 6
        reasons.append("Moderate contribution consistency (4+ records)")
    else:
        score -= 8
        reasons.append("Low contribution history")

    if days_contrib >= 30:
        score -= 15
        reasons.append("No contribution in 30+ days")
    elif days_contrib >= 14:
        score -= 7
        reasons.append("No contribution in 14+ days")

    if contrib_total >= 5000:
        score += 6
        reasons.append("Strong total contributions")

    if fine_count > 0:
        score -= min(18, 3 * fine_count)
        reasons.append("Fines reduce reliability")
    if fine_total >= 1000:
        score -= 6
        reasons.append("High total fines")

    if loan_balance > 0:
        if days_pay >= 30:
            score -= 18
            reasons.append("Loan balance with no payment in 30+ days")
        elif days_pay >= 14:
            score -= 10
            reasons.append("Loan balance with no payment in 14+ days")
        else:
            score += 3
            reasons.append("Active loan with recent payment")

    if bad_status > 0:
        score -= min(15, 7 * bad_status)
        reasons.append("Overdue/delinquent status flags")

    score = int(np.clip(score, 0, 100))
    return score, reasons[:6]


def _compute_dropout_risk(row: pd.Series) -> tuple[float, list[str]]:
    reasons = []
    days_contrib = int(row.get("days_since_last_contrib", 999))
    contrib_count = int(row.get("contrib_count", 0))
    fine_count = int(row.get("fine_count", 0))

    risk = 0.15

    if contrib_count <= 2:
        risk += 0.20
        reasons.append("Very low contribution history")

    if days_contrib >= 60:
        risk += 0.45
        reasons.append("Inactive contributions for 60+ days")
    elif days_contrib >= 30:
        risk += 0.30
        reasons.append("Inactive contributions for 30+ days")
    elif days_contrib >= 14:
        risk += 0.15
        reasons.append("Inactive contributions for 14+ days")

    if fine_count >= 3:
        risk += 0.10
        reasons.append("Frequent fines indicate disengagement")

    return _clip01(risk), reasons[:6]


def _compute_fraud_anomaly_score(member_id: int, contrib: pd.DataFrame, loans: pd.DataFrame, payments: pd.DataFrame) -> tuple[float, list[str]]:
    reasons = []
    score = 0.05

    if not contrib.empty and "member_id" in contrib.columns and "amount" in contrib.columns:
        c = contrib.copy()
        c["member_id"] = _to_int(c["member_id"])
        c["amount"] = _to_num(c["amount"])
        mc = c[c["member_id"] == int(member_id)].copy()
        if len(mc) >= 6:
            mu = float(mc["amount"].mean())
            sd = float(mc["amount"].std(ddof=0) or 0.0)
            last_amt = float(mc["amount"].iloc[-1])
            if sd > 0 and abs(last_amt - mu) > 3 * sd:
                score += 0.35
                reasons.append("Contribution amount is an outlier vs member history (3σ)")

    if not loans.empty and "member_id" in loans.columns:
        l = loans.copy()
        l["member_id"] = _to_int(l["member_id"])
        l["borrow_date"] = _to_dt_utc(l.get("borrow_date", l.get("created_at", pd.NaT)))
        ml = l[l["member_id"] == int(member_id)].copy()
        if not ml.empty:
            now = _utc_now()
            recent14 = ml[(_days_since(now, ml["borrow_date"]) <= 14)].copy()
            if len(recent14) >= 2:
                score += 0.25
                reasons.append("Multiple loans created within last 14 days")

    if not payments.empty and "member_id" in payments.columns:
        p = payments.copy()
        p["member_id"] = _to_int(p["member_id"])
        p["amount"] = _to_num(p.get("amount", 0))
        mp = p[p["member_id"] == int(member_id)].copy()
        if len(mp) >= 6:
            mu = float(mp["amount"].mean())
            sd = float(mp["amount"].std(ddof=0) or 0.0)
            last_amt = float(mp["amount"].iloc[-1])
            if sd > 0 and abs(last_amt - mu) > 3 * sd:
                score += 0.20
                reasons.append("Payment amount is an outlier vs member history (3σ)")

    return _clip01(score), reasons[:6]


def _foundation_liquidity_forecast_simple(
    contrib: pd.DataFrame,
    foundation: pd.DataFrame,
    loans: pd.DataFrame,
    payments: pd.DataFrame,
    payouts: pd.DataFrame,
    horizon_days: int = 30,
) -> dict:
    now = _utc_now().normalize()

    def daily_sum(df: pd.DataFrame, dt_col: str, amt_col: str, sign: float) -> pd.Series:
        if df is None or df.empty or dt_col not in df.columns:
            return pd.Series(dtype=float)
        d = df.copy()
        d[dt_col] = _to_dt_utc(d[dt_col]).dt.normalize()
        d[amt_col] = _to_num(d.get(amt_col, 0))
        return d.groupby(dt_col)[amt_col].sum() * float(sign)

    inflow = pd.Series(dtype=float)
    if not contrib.empty and "created_at" in contrib.columns:
        inflow = inflow.add(daily_sum(contrib, "created_at", "amount", +1.0), fill_value=0.0)
    if not foundation.empty and "created_at" in foundation.columns:
        inflow = inflow.add(daily_sum(foundation, "created_at", "amount", +1.0), fill_value=0.0)
    if not payments.empty:
        dtc = "paid_at" if "paid_at" in payments.columns else ("created_at" if "created_at" in payments.columns else None)
        if dtc:
            inflow = inflow.add(daily_sum(payments, dtc, "amount", +1.0), fill_value=0.0)

    outflow = pd.Series(dtype=float)
    if not loans.empty:
        dtc = "borrow_date" if "borrow_date" in loans.columns else ("created_at" if "created_at" in loans.columns else None)
        principal_col = "principal" if "principal" in loans.columns else ("principal_current" if "principal_current" in loans.columns else None)
        if dtc and principal_col:
            outflow = outflow.add(daily_sum(loans, dtc, principal_col, +1.0), fill_value=0.0)

    if not payouts.empty:
        dtc = "payout_date" if "payout_date" in payouts.columns else ("created_at" if "created_at" in payouts.columns else None)
        amt_col = "payout_amount" if "payout_amount" in payouts.columns else ("amount" if "amount" in payouts.columns else None)
        if dtc and amt_col:
            outflow = outflow.add(daily_sum(payouts, dtc, amt_col, +1.0), fill_value=0.0)

    daily_net = inflow.sub(outflow, fill_value=0.0).sort_index()
    if daily_net.empty:
        return {"ok": False, "msg": "Not enough history for liquidity forecast."}

    balance_est = float(daily_net.sum())
    trailing = daily_net[daily_net.index >= (now - pd.Timedelta(days=30))]
    avg_daily_net = float(trailing.mean()) if not trailing.empty else float(daily_net.mean())

    dates = [now + pd.Timedelta(days=i) for i in range(1, int(horizon_days) + 1)]
    b = balance_est
    forecast_bal = []
    for _ in dates:
        b += avg_daily_net
        forecast_bal.append(b)

    return {
        "ok": True,
        "balance_est": balance_est,
        "avg_daily_net": avg_daily_net,
        "horizon_days": int(horizon_days),
        "dates": dates,
        "forecast_balance": forecast_bal,
    }


def _smart_loan_recommendation(risk: float, reliability: int, liquidity_ok: bool, requested_amount: float) -> tuple[str, list[str]]:
    reasons = []
    decision = "APPROVE"

    if not liquidity_ok:
        decision = "REJECT"
        reasons.append("Liquidity trend is weak: avoid new loans now.")

    if risk >= 0.70:
        decision = "REJECT"
        reasons.append("Very high risk score (≥70%).")

    if 0.45 <= risk < 0.70:
        decision = "APPROVE WITH CONDITIONS"
        reasons.append("Moderate-to-high risk: cap amount + require stronger surety.")

    if reliability < 45:
        decision = "REJECT"
        reasons.append("Low reliability score (<45).")
    elif reliability < 65 and decision == "APPROVE":
        decision = "APPROVE WITH CONDITIONS"
        reasons.append("Reliability moderate: require surety + lower cap.")

    if requested_amount >= 5000 and decision == "APPROVE":
        decision = "APPROVE WITH CONDITIONS"
        reasons.append("Large amount: recommend cap or split disbursement.")

    return decision, reasons[:6]


def _generate_ai_alerts(member_name: str, final_risk: float, reliability: int, dropout: float, fraud: float, liquidity_forecast: dict) -> list[dict]:
    alerts = []

    if final_risk >= 0.70:
        alerts.append({"severity": "high", "type": "default_risk", "message": f"{member_name}: High risk ({final_risk*100:.1f}%)."})
    elif final_risk >= 0.45:
        alerts.append({"severity": "med", "type": "default_risk", "message": f"{member_name}: Moderate risk ({final_risk*100:.1f}%)."})

    if reliability < 45:
        alerts.append({"severity": "high", "type": "reliability", "message": f"{member_name}: Low reliability ({reliability}/100)."})
    elif reliability < 65:
        alerts.append({"severity": "med", "type": "reliability", "message": f"{member_name}: Moderate reliability ({reliability}/100)."})

    if dropout >= 0.70:
        alerts.append({"severity": "med", "type": "dropout", "message": f"{member_name}: High dropout risk ({dropout*100:.0f}%)."})

    if fraud >= 0.60:
        alerts.append({"severity": "high", "type": "fraud", "message": f"{member_name}: Strong anomaly signals ({fraud*100:.0f}%)."})
    elif fraud >= 0.35:
        alerts.append({"severity": "med", "type": "fraud", "message": f"{member_name}: Mild anomaly signals ({fraud*100:.0f}%)."})

    if liquidity_forecast.get("ok"):
        if float(liquidity_forecast.get("avg_daily_net", 0.0)) < 0:
            alerts.append({"severity": "med", "type": "liquidity", "message": "System liquidity trend is negative (avg daily net outflow)."})
    else:
        alerts.append({"severity": "low", "type": "liquidity", "message": "Liquidity forecast unavailable (missing history)."})

    return alerts


def _local_chat_answer(question: str, context: dict) -> str:
    q = (question or "").lower().strip()

    if "top" in q and ("risk" in q or "risky" in q):
        top = context.get("top_risky", [])
        if not top:
            return "I don’t have enough risk data to compute top risky members."
        out = "### 🔴 Top risky members\n"
        for item in top:
            out += f"- {item['name']} → {item['risk']*100:.1f}%\n"
        return out

    if "liquid" in q or "foundation" in q or "cash" in q:
        lf = context.get("liquidity", {})
        if not lf.get("ok"):
            return f"Liquidity forecast not available: {lf.get('msg','missing data')}."
        return (
            "### 💰 Liquidity outlook (simple)\n"
            f"- Estimated net balance (approx): **{lf.get('balance_est', 0.0):,.0f}**\n"
            f"- Avg daily net flow (last ~30 days): **{lf.get('avg_daily_net', 0.0):,.1f}**\n"
            f"- Horizon: **{lf.get('horizon_days', 30)} days**\n"
        )

    if "alert" in q:
        alerts = context.get("alerts", [])
        if not alerts:
            return "No alerts generated right now."
        out = "### 🚨 Alerts\n"
        for a in alerts:
            out += f"- **{a['severity'].upper()}** [{a['type']}] — {a['message']}\n"
        return out

    if "recommend" in q or "approve" in q or "loan" in q:
        rec = context.get("loan_reco", None)
        if not rec:
            return "Loan recommendation is not available yet."
        out = f"### 🧾 Loan decision recommendation\n**Decision:** `{rec['decision']}`\n"
        for r in rec["reasons"]:
            out += f"- {r}\n"
        return out

    return (
        "I can help with: **top risky members**, **liquidity/foundation outlook**, **alerts**, and **loan recommendation**.\n\n"
        "Try: *'Top risky members'* or *'Is liquidity safe?'* or *'Any alerts?'*"
    )


# ============================================================
# ML dataset from loans (YOUR schema)
#   target: closed=0, active=1
# ============================================================
def _make_loan_ml_frame(loans: pd.DataFrame) -> pd.DataFrame:
    if loans is None or loans.empty:
        return pd.DataFrame()

    l = loans.copy()
    if "member_id" not in l.columns or "status" not in l.columns:
        return pd.DataFrame()

    now = _utc_now()

    l["member_id"] = _to_int(l["member_id"])
    l["status"] = l["status"].astype(str).str.lower().fillna("")

    l["principal"] = _to_num(l.get("principal", 0))
    l["principal_current"] = _to_num(l.get("principal_current", l["principal"]))
    l["total_due"] = _to_num(l.get("total_due", 0))
    l["unpaid_interest"] = _to_num(l.get("unpaid_interest", 0))
    l["interest_rate_monthly"] = _to_num(l.get("interest_rate_monthly", 0))
    l["due_cycle_days"] = _to_num(l.get("due_cycle_days", 0))

    l["borrow_date"] = _to_dt_utc(l.get("borrow_date", l.get("created_at", pd.NaT)))
    l["last_paid_at"] = _to_dt_utc(l.get("last_paid_at", pd.NaT))

    l["loan_age_days"] = _days_since(now, l["borrow_date"])
    l["days_since_last_payment"] = _days_since(now, l["last_paid_at"].fillna(l["borrow_date"]))

    # target
    l["target"] = np.where(l["status"] == "closed", 0, 1).astype(int)

    out = l[
        [
            "member_id",
            "status",
            "principal",
            "principal_current",
            "interest_rate_monthly",
            "total_due",
            "unpaid_interest",
            "due_cycle_days",
            "loan_age_days",
            "days_since_last_payment",
            "target",
        ]
    ].copy()

    out = out.replace([np.inf, -np.inf], np.nan).fillna(0)
    out = out[out["member_id"] > 0].copy()
    return out


def _df_fingerprint(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "empty"
    try:
        cols = df.columns.tolist()
        h = pd.util.hash_pandas_object(df[cols], index=True).sum()
        return str(int(h))
    except Exception:
        return str(len(df))


def _xgb_get_or_train(loans_ml: pd.DataFrame):
    try:
        from xgboost import XGBClassifier
    except Exception as e:
        return None, f"xgboost not installed or failed to import: {repr(e)}"

    feature_cols = [
        "principal",
        "principal_current",
        "interest_rate_monthly",
        "total_due",
        "unpaid_interest",
        "due_cycle_days",
        "loan_age_days",
        "days_since_last_payment",
    ]

    fp = _df_fingerprint(loans_ml[feature_cols + ["target"]].copy())
    cache = st.session_state.get("__xgb_cache__", {})

    if cache.get("fp") == fp and cache.get("model") is not None:
        return cache["model"], "OK (cached)"

    X = loans_ml[feature_cols].to_numpy(dtype=float)
    y = loans_ml["target"].to_numpy(dtype=int)

    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    scale_pos_weight = (n_neg / max(n_pos, 1))

    model = XGBClassifier(
        n_estimators=250,
        max_depth=3,
        learning_rate=0.08,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=1.0,
        random_state=42,
        objective="binary:logistic",
        eval_metric="logloss",
        scale_pos_weight=scale_pos_weight,
    )
    model.fit(X, y)

    st.session_state["__xgb_cache__"] = {"fp": fp, "model": model}
    return model, "OK (trained)"


def _xgb_risk_for_member(loans_ml: pd.DataFrame, member_id: int, min_rows: int = MIN_LOANS_FOR_ML) -> tuple[float | None, str]:
    if loans_ml is None or loans_ml.empty:
        return None, "No loans for ML."

    if len(loans_ml) < int(min_rows):
        return None, f"Need at least {min_rows} loans for ML (currently {len(loans_ml)})."

    vc = loans_ml["target"].value_counts().to_dict()
    if len(vc) < 2:
        return None, "ML needs both classes: closed and active."

    model, msg = _xgb_get_or_train(loans_ml)
    if model is None:
        return None, msg

    feature_cols = [
        "principal",
        "principal_current",
        "interest_rate_monthly",
        "total_due",
        "unpaid_interest",
        "due_cycle_days",
        "loan_age_days",
        "days_since_last_payment",
    ]

    mdf = loans_ml[loans_ml["member_id"] == int(member_id)].copy()
    if mdf.empty:
        return None, "Member has no loans."

    active = mdf[mdf["status"] == "active"]
    if not active.empty:
        mdf = active

    Xm = mdf[feature_cols].to_numpy(dtype=float)
    proba = model.predict_proba(Xm)[:, 1]

    risk = float(np.max(proba)) if len(proba) else None
    if risk is None:
        return None, "Unable to compute ML risk."

    return float(np.clip(risk, 0.0, 1.0)), msg


# ============================================================
# Main render
# ============================================================
def render_ai_risk_panel(sb_anon, sb_service=None, schema: str = "public"):
    st.header("🤖 AI Risk Panel")
    st.caption("NJANGI STANDARD • no legacy • Heuristic + ML (XGBoost) • payouts fixed • + Extra AI Suite + Local Chat (no API key).")

    _ensure_clients_in_state(sb_anon, sb_service)

    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("🔄 Refresh"):
            st.cache_data.clear()
            st.session_state.pop("__xgb_cache__", None)
            st.rerun()
    with c2:
        mode = st.radio("Risk mode", ["Heuristic", "ML (XGBoost)", "Hybrid"], horizontal=True)

    if not _table_exists(sb_anon, schema, "members"):
        st.error("Missing table: members")
        return

    # NOTE: If your members table uses full_name instead of name, change cols to "id,full_name"
    members = _load_table(sb_anon, sb_service, schema, "members", cols="id,name", limit=5000)
    if members.empty or "id" not in members.columns:
        st.error("members not readable.")
        return

    contrib = (
        _load_table(sb_anon, sb_service, schema, "contributions", cols="member_id,session_id,amount,created_at", limit=20000)
        if _table_exists(sb_anon, schema, "contributions")
        else pd.DataFrame()
    )

    loans_cols = (
        "id,member_id,status,principal,principal_current,total_due,unpaid_interest,"
        "last_paid_at,borrow_date,due_cycle_days,interest_rate_monthly,created_at"
    )
    loans = (
        _load_table_service(sb_anon, sb_service, schema, "loans", cols=loans_cols, limit=20000)
        if (sb_service is not None and _table_exists(sb_service, schema, "loans"))
        else (
            _load_table(sb_anon, sb_service, schema, "loans", cols=loans_cols, limit=20000)
            if _table_exists(sb_anon, schema, "loans")
            else pd.DataFrame()
        )
    )

    payments = (
        _load_payments_schema_safe(sb_anon, sb_service, schema=schema, limit=30000)
        if (_table_exists(sb_service, schema, "loan_payments") if sb_service is not None else _table_exists(sb_anon, schema, "loan_payments"))
        else pd.DataFrame()
    )

    payouts = (
        _load_table_service(sb_anon, sb_service, schema, "payouts", cols="member_id,session_id,payout_amount,payout_date,created_at", limit=20000)
        if (sb_service is not None and _table_exists(sb_service, schema, "payouts"))
        else (
            _load_table(sb_anon, sb_service, schema, "payouts", cols="member_id,session_id,payout_amount,payout_date,created_at", limit=20000)
            if _table_exists(sb_anon, schema, "payouts")
            else pd.DataFrame()
        )
    )

    fines = (
        _load_table_service(sb_anon, sb_service, schema, "fines", cols="member_id,amount,created_at", limit=20000)
        if (sb_service is not None and _table_exists(sb_service, schema, "fines"))
        else (
            _load_table(sb_anon, sb_service, schema, "fines", cols="member_id,amount,created_at", limit=20000)
            if _table_exists(sb_anon, schema, "fines")
            else pd.DataFrame()
        )
    )

    foundation = (
        _load_table(sb_anon, sb_service, schema, "foundation_contributions", cols="member_id,amount,created_at", limit=20000)
        if _table_exists(sb_anon, schema, "foundation_contributions")
        else pd.DataFrame()
    )

    with st.expander("🔎 Debug (rows loaded)", expanded=False):
        st.write("members:", len(members))
        st.write("contributions:", len(contrib))
        st.write("loans:", len(loans))
        st.write("loan_payments:", len(payments))
        st.write("payouts:", len(payouts))
        st.write("fines:", len(fines))
        st.write("foundation_contributions:", len(foundation))
        if not loans.empty and "status" in loans.columns:
            st.write("loan status counts:")
            st.dataframe(loans["status"].astype(str).str.lower().value_counts().reset_index(), use_container_width=True)

    X = _build_member_features(members, contrib, loans, payments, payouts, fines, foundation)

    m = members.copy()
    m["id"] = _to_int(m["id"])
    m["name"] = m.get("name", "").astype(str)
    m = m[m["id"] > 0].copy()
    m["label"] = m.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)

    if m.empty:
        st.warning("No members found.")
        return

    pick = st.selectbox("Select member", m["label"].tolist())
    mid = int(m.loc[m["label"] == pick, "id"].iloc[0])
    member_name = str(m.loc[m["id"] == mid, "name"].iloc[0]) if "name" in m.columns else f"Member {mid}"

    row = X[X["member_id"] == mid].copy()
    if row.empty:
        st.warning("No feature row for selected member.")
        return
    row1 = row.iloc[0]

    # Heuristic
    h_risk, reasons = _compute_risk_score(row1)

    # ML
    loans_ml = _make_loan_ml_frame(loans)
    ml_risk, ml_msg = _xgb_risk_for_member(loans_ml, member_id=mid, min_rows=MIN_LOANS_FOR_ML)

    # Final risk
    if mode == "Heuristic":
        final_risk = h_risk
    elif mode == "ML (XGBoost)":
        final_risk = ml_risk if ml_risk is not None else h_risk
    else:  # Hybrid
        final_risk = h_risk if ml_risk is None else float(np.clip((h_risk + ml_risk) / 2.0, 0.0, 1.0))

    # Display
    st.subheader("Risk prediction")
    st.metric("Predicted Risk", f"{final_risk * 100:.1f}%")
    st.progress(float(np.clip(final_risk, 0.0, 1.0)))

    if mode in ["ML (XGBoost)", "Hybrid"]:
        if ml_risk is None:
            st.info(f"ML not ready: {ml_msg}")
        else:
            st.caption(f"ML active: {ml_msg}")

    if reasons:
        st.caption("Heuristic signals:")
        for r in reasons:
            st.write(f"• {r}")

    st.divider()
    st.subheader("Member feature snapshot (no blanks)")
    snap = row.T
    snap.columns = ["value"]
    st.dataframe(snap, use_container_width=True)

    # ============================================================
    # ✅ EXTRA AI SUITE UI (single-file)
    # ============================================================
    st.divider()
    st.subheader("🧠 Extra AI Suite (Reliability • Dropout • Fraud • Liquidity • Loan Decision • Alerts • Chat)")

    reliability, rel_reasons = _compute_reliability_score(row1)
    dropout, drop_reasons = _compute_dropout_risk(row1)
    fraud, fraud_reasons = _compute_fraud_anomaly_score(mid, contrib, loans, payments)

    liquidity = _foundation_liquidity_forecast_simple(
        contrib=contrib,
        foundation=foundation,
        loans=loans,
        payments=payments,
        payouts=payouts,
        horizon_days=30,
    )
    liquidity_ok = bool(liquidity.get("ok")) and float(liquidity.get("avg_daily_net", 0.0)) >= 0

    amt = st.number_input("Test Loan Amount (for recommendation)", min_value=0.0, value=3000.0, step=500.0)
    decision, dec_reasons = _smart_loan_recommendation(
        risk=float(final_risk),
        reliability=int(reliability),
        liquidity_ok=bool(liquidity_ok),
        requested_amount=float(amt),
    )

    alerts = _generate_ai_alerts(
        member_name=member_name,
        final_risk=float(final_risk),
        reliability=int(reliability),
        dropout=float(dropout),
        fraud=float(fraud),
        liquidity_forecast=liquidity,
    )

    # Top risky members (heuristic)
    top_risky = []
    try:
        if X is not None and not X.empty:
            tmp = []
            for _, rr in X.iterrows():
                h, _ = _compute_risk_score(rr)
                mid2 = int(rr["member_id"])
                nm2 = str(m.loc[m["id"] == mid2, "name"].iloc[0]) if ("name" in m.columns and (m["id"] == mid2).any()) else f"Member {mid2}"
                tmp.append({"member_id": mid2, "name": nm2, "risk": float(h)})
            tmp.sort(key=lambda z: z["risk"], reverse=True)
            top_risky = tmp[:5]
    except Exception:
        top_risky = []

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "✅ Reliability & Dropout",
        "🕵🏽 Fraud/Anomaly",
        "💰 Liquidity Forecast",
        "🧾 Loan Recommendation",
        "🚨 Alerts Center",
        "💬 Local Chatbox",
    ])

    with tab1:
        cA, cB, cC = st.columns(3)
        cA.metric("Reliability (0–100)", f"{reliability}")
        cB.metric("Dropout Risk", f"{dropout*100:.0f}%")
        cC.metric("Fraud/Anomaly", f"{fraud*100:.0f}%")

        st.write("**Reliability reasons**")
        for r in rel_reasons:
            st.write(f"• {r}")

        st.write("**Dropout risk reasons**")
        for r in drop_reasons:
            st.write(f"• {r}")

    with tab2:
        st.metric("Fraud/Anomaly Score", f"{fraud*100:.0f}%")
        st.progress(float(np.clip(fraud, 0.0, 1.0)))
        if fraud_reasons:
            st.write("**Signals detected**")
            for r in fraud_reasons:
                st.write(f"• {r}")
        else:
            st.info("No anomaly signals detected from current data.")

        st.caption("Note: This is lightweight anomaly detection (fast + safe).")

    with tab3:
        if not liquidity.get("ok"):
            st.warning(liquidity.get("msg", "Liquidity forecast unavailable."))
        else:
            st.metric("Estimated Net Balance (approx)", f"{liquidity.get('balance_est', 0.0):,.0f}")
            st.metric("Avg Daily Net Flow (last ~30d)", f"{liquidity.get('avg_daily_net', 0.0):,.1f}")
            st.caption("Forecast is simple: linear projection using trailing average net flow.")
            df_fc = pd.DataFrame({"date": liquidity["dates"], "forecast_balance": liquidity["forecast_balance"]})
            st.line_chart(df_fc.set_index("date"))

    with tab4:
        st.write(f"**Decision:** `{decision}`")
        for r in dec_reasons:
            st.write(f"• {r}")
        st.caption("Policy engine uses Risk + Reliability + Liquidity trend.")

    with tab5:
        if not alerts:
            st.success("No alerts generated.")
        else:
            for a in alerts:
                sev = a["severity"]
                msg = a["message"]
                if sev == "high":
                    st.error(msg)
                elif sev == "med":
                    st.warning(msg)
                else:
                    st.info(msg)

        st.write("**Top 5 risky members (heuristic)**")
        if top_risky:
            st.dataframe(pd.DataFrame(top_risky), use_container_width=True)
        else:
            st.info("Not enough data to compute top risky members.")

    with tab6:
        st.caption("Local AI Chat (no API key). Answers only from computed context below.")
        if "local_ai_msgs" not in st.session_state:
            st.session_state.local_ai_msgs = []

        context = {
            "member_id": mid,
            "member_name": member_name,
            "final_risk": float(final_risk),
            "reliability": int(reliability),
            "dropout": float(dropout),
            "fraud": float(fraud),
            "liquidity": liquidity,
            "alerts": alerts,
            "loan_reco": {"decision": decision, "reasons": dec_reasons},
            "top_risky": top_risky,
        }

        for role, msg in st.session_state.local_ai_msgs[-20:]:
            with st.chat_message(role):
                st.markdown(msg)

        q = st.chat_input("Ask: 'Any alerts?'  'Is liquidity safe?'  'Top risky members'  'Loan recommendation'")
        if q:
            st.session_state.local_ai_msgs.append(("user", q))
            ans = _local_chat_answer(q, context)
            with st.chat_message("assistant"):
                st.markdown(ans)
            st.session_state.local_ai_msgs.append(("assistant", ans))

        with st.expander("🔎 Context (what chat uses)", expanded=False):
            st.json(context)
