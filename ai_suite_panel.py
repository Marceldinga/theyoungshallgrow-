
# ai_suite_panel.py ✅ COMPLETE FREE AI SUITE (NO API KEY) — ALL FEATURES
# ---------------------------------------------------------------------
# ✅ Includes EVERYTHING you requested:
# ✅ Risk scoring (Heuristic + XGBoost if installed)
# ✅ Reliability score (0–100) Njangi “credit score”
# ✅ Dropout risk (disengagement prediction)
# ✅ Fraud/Anomaly detection (outliers & suspicious patterns)
# ✅ Liquidity forecast (simple cashflow projection)
# ✅ Smart loan decision engine (Approve / Conditions / Reject)
# ✅ Alerts center (risk/liquidity/fraud flags)
# ✅ System Chat Assistant (free) — answers questions about your system using real tables
# ✅ Minutes generator (free) — auto writes meeting minutes using your data
# ✅ Optional Save minutes to DB if you create `minutes` table
#
# Designed to be imported into any Streamlit page (including ai_risk_panel.py).
# You pass your loaded DataFrames into `render_full_ai_suite_panel(...)`.
# ---------------------------------------------------------------------

from __future__ import annotations

import re
import numpy as np
import pandas as pd
import streamlit as st


# ============================================================
# Helpers
# ============================================================
def _clip01(x: float) -> float:
    try:
        return float(np.clip(float(x), 0.0, 1.0))
    except Exception:
        return 0.0


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


def _fmt_money(x) -> str:
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)


def _safe_insert(client, schema: str, table: str, row: dict) -> tuple[bool, str]:
    if client is None:
        return False, "No client provided."
    try:
        client.schema(schema).table(table).insert(row).execute()
        return True, "OK"
    except Exception as e:
        return False, repr(e)


def _table_exists(client, schema: str, table: str) -> bool:
    if client is None:
        return False
    try:
        client.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def _infer_member_name_col(members: pd.DataFrame) -> str | None:
    for c in ["name", "full_name", "member_name"]:
        if members is not None and not members.empty and c in members.columns:
            return c
    return None


def _member_map(members: pd.DataFrame) -> dict[int, str]:
    if members is None or members.empty or "id" not in members.columns:
        return {}
    name_col = _infer_member_name_col(members)
    if not name_col:
        return {}
    out: dict[int, str] = {}
    for _, r in members.iterrows():
        try:
            out[int(r["id"])] = str(r.get(name_col) or f"Member {int(r['id'])}")
        except Exception:
            pass
    return out


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
# Feature engineering (member-level)
# ============================================================
def build_member_features(
    members: pd.DataFrame,
    contrib: pd.DataFrame,
    loans: pd.DataFrame,
    payments: pd.DataFrame,
    payouts: pd.DataFrame,
    fines: pd.DataFrame,
    foundation: pd.DataFrame,
) -> pd.DataFrame:
    if members is None or members.empty or "id" not in members.columns:
        return pd.DataFrame()

    members2 = members.copy()
    members2["id"] = _to_int(members2["id"])
    members2 = members2[members2["id"] > 0].copy()
    now = _utc_now()

    base = pd.DataFrame({"member_id": members2["id"].astype(int)})

    # Contributions
    cfeat = base.copy()
    if contrib is not None and not contrib.empty and "member_id" in contrib.columns:
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

    # Loans
    lfeat = base.copy()
    if loans is not None and not loans.empty and "member_id" in loans.columns:
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

    # Payments
    pfeat = base.copy()
    if payments is not None and not payments.empty and "member_id" in payments.columns:
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

    # Payouts (supports payout_amount/payout_date or amount/created_at)
    poutfeat = base.copy()
    if payouts is not None and not payouts.empty and "member_id" in payouts.columns:
        po = payouts.copy()
        po["member_id"] = _to_int(po["member_id"])

        amt_col = "payout_amount" if "payout_amount" in po.columns else ("amount" if "amount" in po.columns else None)
        po["payout_amount_calc"] = _to_num(po[amt_col]) if amt_col else 0.0

        dt_col = "payout_date" if "payout_date" in po.columns else ("created_at" if "created_at" in po.columns else None)
        po["payout_dt_calc"] = _to_dt_utc(po[dt_col]) if dt_col else pd.NaT

        grp = po.groupby("member_id", dropna=False)
        poutfeat = poutfeat.merge(grp["payout_amount_calc"].count().rename("payout_count"), left_on="member_id", right_index=True, how="left")
        poutfeat = poutfeat.merge(grp["payout_amount_calc"].sum().rename("payout_total"), left_on="member_id", right_index=True, how="left")
        poutfeat = poutfeat.merge(grp["payout_dt_calc"].max().rename("payout_last_dt"), left_on="member_id", right_index=True, how="left")
    else:
        poutfeat["payout_count"] = 0
        poutfeat["payout_total"] = 0.0
        poutfeat["payout_last_dt"] = pd.NaT

    poutfeat["days_since_last_payout"] = _days_since(now, poutfeat["payout_last_dt"])

    # Fines
    ffeat = base.copy()
    if fines is not None and not fines.empty and "member_id" in fines.columns:
        f = fines.copy()
        f["member_id"] = _to_int(f["member_id"])
        f["amount"] = _to_num(f.get("amount", 0))
        grp = f.groupby("member_id", dropna=False)
        ffeat = ffeat.merge(grp["amount"].sum().rename("fine_total"), left_on="member_id", right_index=True, how="left")
        ffeat = ffeat.merge(grp["amount"].count().rename("fine_count"), left_on="member_id", right_index=True, how="left")
    else:
        ffeat["fine_total"] = 0.0
        ffeat["fine_count"] = 0

    # Foundation contributions
    fdfeat = base.copy()
    if foundation is not None and not foundation.empty and "member_id" in foundation.columns:
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

    X = (
        cfeat.merge(lfeat, on="member_id", how="left")
        .merge(pfeat, on="member_id", how="left")
        .merge(poutfeat, on="member_id", how="left")
        .merge(ffeat, on="member_id", how="left")
        .merge(fdfeat, on="member_id", how="left")
    )

    for dtcol in ["contrib_last_dt", "pay_last_dt", "foundation_last_dt", "loan_last_paid_dt", "payout_last_dt"]:
        if dtcol in X.columns:
            X.drop(columns=[dtcol], inplace=True)

    return _fill_feature_defaults(X)


# ============================================================
# Risk scoring (Heuristic)
# ============================================================
def compute_heuristic_risk(row: pd.Series) -> tuple[float, list[str]]:
    reasons: list[str] = []

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

    return float(np.clip(score, 0.0, 1.0)), reasons[:6]


# ============================================================
# Risk scoring (XGBoost if installed)
#   target: closed=0, active=1
# ============================================================
def _make_loan_ml_frame(loans: pd.DataFrame) -> pd.DataFrame:
    if loans is None or loans.empty or "member_id" not in loans.columns or "status" not in loans.columns:
        return pd.DataFrame()

    l = loans.copy()
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

    l["target"] = np.where(l["status"] == "closed", 0, 1).astype(int)

    cols = [
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
    out = l[cols].copy()
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
    cache = st.session_state.get("__ai_suite_xgb_cache__", {})

    if cache.get("fp") == fp and cache.get("model") is not None:
        return cache["model"], "OK (cached)"

    X = loans_ml[feature_cols].to_numpy(dtype=float)
    y = loans_ml["target"].to_numpy(dtype=int)

    vc = loans_ml["target"].value_counts().to_dict()
    if len(vc) < 2:
        return None, "ML needs both classes: closed and active."

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

    st.session_state["__ai_suite_xgb_cache__"] = {"fp": fp, "model": model}
    return model, "OK (trained)"


def xgb_risk_for_member(loans: pd.DataFrame, member_id: int, min_rows: int = 20) -> tuple[float | None, str]:
    loans_ml = _make_loan_ml_frame(loans)
    if loans_ml.empty:
        return None, "No loans for ML."

    if len(loans_ml) < int(min_rows):
        return None, f"Need at least {min_rows} loans for ML (currently {len(loans_ml)})."

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
# Reliability / Dropout / Fraud
# ============================================================
def reliability_score(row: pd.Series) -> tuple[int, list[str]]:
    return compute_reliability_score(row)


def compute_reliability_score(row: pd.Series) -> tuple[int, list[str]]:
    reasons: list[str] = []
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


def dropout_risk(row: pd.Series) -> tuple[float, list[str]]:
    reasons: list[str] = []
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


def fraud_anomaly_score(member_id: int, contrib: pd.DataFrame, loans: pd.DataFrame, payments: pd.DataFrame) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.05

    # Contribution outlier
    if contrib is not None and not contrib.empty and "member_id" in contrib.columns and "amount" in contrib.columns:
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
                reasons.append("Contribution amount is a strong outlier vs member history (3σ)")

    # Multiple recent loans
    if loans is not None and not loans.empty and "member_id" in loans.columns:
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

    # Payment outlier
    if payments is not None and not payments.empty and "member_id" in payments.columns and "amount" in payments.columns:
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


# ============================================================
# Liquidity forecast (simple)
# ============================================================
def liquidity_forecast_simple(
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
    if contrib is not None and not contrib.empty and "created_at" in contrib.columns:
        inflow = inflow.add(daily_sum(contrib, "created_at", "amount", +1.0), fill_value=0.0)
    if foundation is not None and not foundation.empty and "created_at" in foundation.columns:
        inflow = inflow.add(daily_sum(foundation, "created_at", "amount", +1.0), fill_value=0.0)
    if payments is not None and not payments.empty:
        dtc = "paid_at" if "paid_at" in payments.columns else ("created_at" if "created_at" in payments.columns else None)
        if dtc:
            inflow = inflow.add(daily_sum(payments, dtc, "amount", +1.0), fill_value=0.0)

    outflow = pd.Series(dtype=float)
    if loans is not None and not loans.empty:
        dtc = "borrow_date" if "borrow_date" in loans.columns else ("created_at" if "created_at" in loans.columns else None)
        principal_col = "principal" if "principal" in loans.columns else ("principal_current" if "principal_current" in loans.columns else None)
        if dtc and principal_col:
            outflow = outflow.add(daily_sum(loans, dtc, principal_col, +1.0), fill_value=0.0)

    if payouts is not None and not payouts.empty:
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


# ============================================================
# Loan decision engine + Alerts
# ============================================================
def smart_loan_decision(risk: float, reliability: int, liquidity_ok: bool, requested_amount: float) -> tuple[str, list[str]]:
    reasons: list[str] = []
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


def generate_alerts(member_name: str, final_risk: float, reliability: int, dropout: float, fraud: float, liquidity: dict) -> list[dict]:
    alerts: list[dict] = []

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

    if liquidity.get("ok"):
        if float(liquidity.get("avg_daily_net", 0.0)) < 0:
            alerts.append({"severity": "med", "type": "liquidity", "message": "System liquidity trend is negative (avg daily net outflow)."})
    else:
        alerts.append({"severity": "low", "type": "liquidity", "message": "Liquidity forecast unavailable (missing history)."})

    return alerts


# ============================================================
# Minutes generator
# ============================================================
def build_minutes_text(
    *,
    meeting_title: str,
    meeting_date: pd.Timestamp,
    location: str,
    chairperson: str,
    secretary: str,
    agenda: str,
    members: pd.DataFrame,
    contrib: pd.DataFrame,
    foundation: pd.DataFrame,
    loans: pd.DataFrame,
    payments: pd.DataFrame,
    payouts: pd.DataFrame,
    fines: pd.DataFrame,
    top_risky: list[dict],
    alerts: list[dict],
) -> str:
    contrib_total = float(_to_num(contrib.get("amount", 0)).sum()) if (contrib is not None and not contrib.empty and "amount" in contrib.columns) else 0.0
    foundation_total = float(_to_num(foundation.get("amount", 0)).sum()) if (foundation is not None and not foundation.empty and "amount" in foundation.columns) else 0.0
    fines_total = float(_to_num(fines.get("amount", 0)).sum()) if (fines is not None and not fines.empty and "amount" in fines.columns) else 0.0
    payments_total = float(_to_num(payments.get("amount", 0)).sum()) if (payments is not None and not payments.empty and "amount" in payments.columns) else 0.0

    payout_amt_col = (
        "payout_amount" if (payouts is not None and not payouts.empty and "payout_amount" in payouts.columns)
        else ("amount" if (payouts is not None and not payouts.empty and "amount" in payouts.columns) else None)
    )
    payouts_total = float(_to_num(payouts.get(payout_amt_col, 0)).sum()) if payout_amt_col else 0.0

    loan_count = int(len(loans)) if loans is not None else 0
    active_loans = 0
    closed_loans = 0
    loan_balance_sum = 0.0
    if loans is not None and not loans.empty:
        if "status" in loans.columns:
            s = loans["status"].astype(str).str.lower()
            active_loans = int((s == "active").sum())
            closed_loans = int((s == "closed").sum())
        bal_col = "principal_current" if "principal_current" in loans.columns else ("principal" if "principal" in loans.columns else None)
        if bal_col:
            loan_balance_sum = float(_to_num(loans[bal_col]).sum())

    member_count = int(len(members)) if members is not None and not members.empty else 0
    high_alerts = [a for a in (alerts or []) if a.get("severity") == "high"]
    med_alerts = [a for a in (alerts or []) if a.get("severity") == "med"]

    risk_lines = "\n".join(
        [f"- {r.get('name','Member')} ({r.get('member_id','?')}): {float(r.get('risk',0))*100:.1f}%"
         for r in (top_risky or [])]
    ) if top_risky else "- Not available"

    date_str = meeting_date.strftime("%Y-%m-%d")

    lines: list[str] = []
    lines.append(f"{meeting_title}")
    lines.append(f"Date: {date_str}")
    if location:
        lines.append(f"Location: {location}")
    if chairperson:
        lines.append(f"Chairperson: {chairperson}")
    if secretary:
        lines.append(f"Secretary: {secretary}")
    lines.append("")
    lines.append("1. Opening")
    lines.append(f"The meeting was called to order on {date_str}.")
    lines.append("")
    lines.append("2. Attendance")
    lines.append(f"Total registered members in system: {member_count}.")
    lines.append("")
    lines.append("3. Agenda")
    lines.append(agenda.strip() if agenda.strip() else "Treasury update, contributions, loans, payouts, fines, risk review, and resolutions.")
    lines.append("")
    lines.append("4. Treasury Summary (System Totals)")
    lines.append(f"- Contributions (total): {_fmt_money(contrib_total)}")
    lines.append(f"- Foundation contributions (total): {_fmt_money(foundation_total)}")
    lines.append(f"- Loan payments (total): {_fmt_money(payments_total)}")
    lines.append(f"- Payouts (total): {_fmt_money(payouts_total)}")
    lines.append(f"- Fines (total): {_fmt_money(fines_total)}")
    lines.append("")
    lines.append("5. Loans Summary")
    lines.append(f"- Total loans recorded: {loan_count}")
    lines.append(f"- Active loans: {active_loans}")
    lines.append(f"- Closed loans: {closed_loans}")
    lines.append(f"- Total outstanding principal (sum): {_fmt_money(loan_balance_sum)}")
    lines.append("")
    lines.append("6. Risk & Compliance Review")
    lines.append("Top risk members (heuristic):")
    lines.append(risk_lines)
    lines.append("")
    lines.append("Alerts raised:")
    if not alerts:
        lines.append("- None")
    else:
        if high_alerts:
            lines.append("High severity:")
            for a in high_alerts[:10]:
                lines.append(f"- {a.get('message','')}")
        if med_alerts:
            lines.append("Medium severity:")
            for a in med_alerts[:10]:
                lines.append(f"- {a.get('message','')}")
    lines.append("")
    lines.append("7. Resolutions / Action Items")
    lines.append("- Treasury to review any high-risk members and enforce loan conditions where necessary.")
    lines.append("- Members with overdue payment patterns should be contacted for repayment plan.")
    lines.append("- Continue monitoring liquidity trend before approving large loans.")
    lines.append("")
    lines.append("8. Closing")
    lines.append("The meeting was adjourned after completing all agenda items.")
    lines.append("")
    lines.append("Signatures:")
    lines.append(f"- Chairperson: ____________________   Date: {date_str}")
    lines.append(f"- Secretary:   ____________________   Date: {date_str}")

    return "\n".join(lines)


# ============================================================
# System Chat Assistant (FREE)
# ============================================================
def system_chat_answer(question: str, ctx: dict) -> str:
    q0 = (question or "").strip()
    ql = q0.lower()

    members = ctx.get("members", pd.DataFrame())
    contrib = ctx.get("contrib", pd.DataFrame())
    loans = ctx.get("loans", pd.DataFrame())
    payments = ctx.get("payments", pd.DataFrame())
    payouts = ctx.get("payouts", pd.DataFrame())
    fines = ctx.get("fines", pd.DataFrame())
    foundation = ctx.get("foundation", pd.DataFrame())
    top_risky = ctx.get("top_risky", [])
    alerts = ctx.get("alerts", [])
    minutes_text = ctx.get("minutes_text", "")

    id2name = _member_map(members)

    def _find_member_id(text: str) -> int | None:
        m = re.search(r"(member\s*|#)(\d+)", text.lower())
        if m:
            try:
                return int(m.group(2))
            except Exception:
                pass
        # try name contains
        name_col = _infer_member_name_col(members)
        if name_col and not members.empty:
            hits = members[members[name_col].astype(str).str.lower().str.contains(text.lower(), na=False)]
            if len(hits) == 1:
                return int(_to_int(hits["id"]).iloc[0])
        return None

    if ql in ("help", "?", "commands", "what can you do"):
        return (
            "### ✅ System Chat (Free)\n"
            "- `top risky`\n"
            "- `loan status`\n"
            "- `total contributions` / `total payouts` / `total fines` / `total payments` / `foundation total`\n"
            "- `summary member 5` or `summary Marcel`\n"
            "- `minutes` (shows latest generated minutes if available)\n"
        )

    if "minutes" in ql:
        if minutes_text:
            return "### 📝 Latest Generated Minutes\n" + minutes_text
        return "Minutes not generated yet. Open the **Minutes** tab and generate minutes first."

    if ("top" in ql and "risk" in ql) or "top risky" in ql or "highest risk" in ql:
        if not top_risky:
            return "Top risky list not available yet."
        out = "### 🔴 Top risky members\n"
        for r in top_risky:
            out += f"- {r.get('name','Member')} → {float(r.get('risk',0))*100:.1f}%\n"
        return out

    if "loan status" in ql or ("loans" in ql and "status" in ql):
        if loans is None or loans.empty or "status" not in loans.columns:
            return "Loans status not available."
        vc = loans["status"].astype(str).str.lower().value_counts()
        out = "### 📌 Loan status counts\n"
        for k, v in vc.items():
            out += f"- **{k}**: {int(v)}\n"
        return out

    # totals
    if "total contributions" in ql:
        if contrib is None or contrib.empty or "amount" not in contrib.columns:
            return "Contributions data not available."
        return f"### 💵 Total contributions\n**{_fmt_money(_to_num(contrib['amount']).sum())}**"

    if "total payouts" in ql:
        if payouts is None or payouts.empty:
            return "Payouts data not available."
        amt_col = "payout_amount" if "payout_amount" in payouts.columns else ("amount" if "amount" in payouts.columns else None)
        if not amt_col:
            return "Payout amount column not found."
        return f"### 🧾 Total payouts\n**{_fmt_money(_to_num(payouts[amt_col]).sum())}**"

    if "total fines" in ql:
        if fines is None or fines.empty or "amount" not in fines.columns:
            return "Fines data not available."
        return f"### 💸 Total fines\n**{_fmt_money(_to_num(fines['amount']).sum())}**"

    if "total payments" in ql:
        if payments is None or payments.empty or "amount" not in payments.columns:
            return "Payments data not available."
        return f"### ✅ Total loan payments\n**{_fmt_money(_to_num(payments['amount']).sum())}**"

    if "foundation total" in ql or ("total" in ql and "foundation" in ql):
        if foundation is None or foundation.empty or "amount" not in foundation.columns:
            return "Foundation contributions data not available."
        return f"### 🏦 Foundation total contributions\n**{_fmt_money(_to_num(foundation['amount']).sum())}**"

    # alerts
    if "alert" in ql:
        if not alerts:
            return "No alerts generated right now."
        out = "### 🚨 Alerts\n"
        for a in alerts[:25]:
            out += f"- **{a.get('severity','').upper()}** [{a.get('type','')}] — {a.get('message','')}\n"
        return out

    # member summary
    if "summary" in ql or "profile" in ql or "member" in ql:
        mid = _find_member_id(q0)
        if mid is None:
            return "I couldn’t identify the member. Try `summary member 5` or `summary <exact name>`."
        name = id2name.get(mid, f"Member {mid}")

        def _sum(df: pd.DataFrame, amt_col: str, mid_col: str = "member_id") -> float:
            if df is None or df.empty or amt_col not in df.columns or mid_col not in df.columns:
                return 0.0
            d = df.copy()
            d[mid_col] = _to_int(d[mid_col])
            return float(_to_num(d[d[mid_col] == int(mid)][amt_col]).sum())

        c_total = _sum(contrib, "amount")
        fd_total = _sum(foundation, "amount")
        f_total = _sum(fines, "amount")
        p_total = _sum(payments, "amount")
        po_amt_col = "payout_amount" if payouts is not None and "payout_amount" in payouts.columns else ("amount" if payouts is not None and "amount" in payouts.columns else None)
        po_total = _sum(payouts, po_amt_col) if po_amt_col else 0.0

        active_loans = 0
        bal_sum = 0.0
        if loans is not None and not loans.empty and "member_id" in loans.columns:
            l = loans.copy()
            l["member_id"] = _to_int(l["member_id"])
            lm = l[l["member_id"] == int(mid)].copy()
            if not lm.empty and "status" in lm.columns:
                active_loans = int((lm["status"].astype(str).str.lower() == "active").sum())
            bal_col = "principal_current" if "principal_current" in lm.columns else ("principal" if "principal" in lm.columns else None)
            if bal_col:
                bal_sum = float(_to_num(lm[bal_col]).sum())

        return (
            f"### 👤 Member Summary — {name} (ID {mid})\n"
            f"- Contributions: **{_fmt_money(c_total)}**\n"
            f"- Foundation: **{_fmt_money(fd_total)}**\n"
            f"- Loan payments: **{_fmt_money(p_total)}**\n"
            f"- Payouts: **{_fmt_money(po_total)}**\n"
            f"- Fines: **{_fmt_money(f_total)}**\n"
            f"- Active loans: **{active_loans}**\n"
            f"- Loan balance (sum): **{_fmt_money(bal_sum)}**\n"
        )

    return "Try `help`. I can answer totals, loan status, member summaries, alerts, top risky, and minutes."


# ============================================================
# UI: Render EVERYTHING
# ============================================================
def render_full_ai_suite_panel(
    *,
    members: pd.DataFrame,
    contributions: pd.DataFrame,
    loans: pd.DataFrame,
    loan_payments: pd.DataFrame,
    payouts: pd.DataFrame,
    fines: pd.DataFrame,
    foundation_contributions: pd.DataFrame,
    sessions: pd.DataFrame | None = None,
    schema: str = "public",
    sb_anon=None,
    sb_service=None,
    min_loans_for_ml: int = 20,
):
    """
    Call this from any Streamlit page after you load your tables into DataFrames.
    - No OpenAI required.
    - If xgboost is installed, ML risk becomes available automatically.
    """
    sessions = sessions if sessions is not None else pd.DataFrame()

    # Build features
    X = build_member_features(
        members=members,
        contrib=contributions,
        loans=loans,
        payments=loan_payments,
        payouts=payouts,
        fines=fines,
        foundation=foundation_contributions,
    )

    if X.empty:
        st.error("AI Suite: could not build features (check members table / IDs).")
        return

    name_col = _infer_member_name_col(members)
    members2 = members.copy()
    members2["id"] = _to_int(members2["id"])
    if name_col:
        members2[name_col] = members2[name_col].astype(str)
    else:
        members2["name"] = members2.get("name", "").astype(str)
        name_col = "name"

    members2 = members2[members2["id"] > 0].copy()
    members2["label"] = members2.apply(lambda r: f"{int(r['id']):02d} • {r.get(name_col,'')}", axis=1)

    st.header("🧠 Free AI Suite (NJANGI)")
    st.caption("Risk • Reliability • Dropout • Fraud • Liquidity • Loan Decisions • Alerts • System Chat • Minutes (no API key)")

    pick = st.selectbox("Select member", members2["label"].tolist())
    member_id = int(members2.loc[members2["label"] == pick, "id"].iloc[0])
    member_name = str(members2.loc[members2["id"] == member_id, name_col].iloc[0]) if name_col else f"Member {member_id}"

    row = X[X["member_id"] == int(member_id)]
    if row.empty:
        st.warning("No feature row for selected member.")
        return
    row1 = row.iloc[0]

    # Risk mode
    mode = st.radio("Risk mode", ["Heuristic", "ML (XGBoost)", "Hybrid"], horizontal=True)

    h_risk, h_reasons = compute_heuristic_risk(row1)
    ml_risk, ml_msg = xgb_risk_for_member(loans, member_id=member_id, min_rows=min_loans_for_ml)

    if mode == "Heuristic":
        final_risk = h_risk
    elif mode == "ML (XGBoost)":
        final_risk = ml_risk if ml_risk is not None else h_risk
    else:
        final_risk = h_risk if ml_risk is None else float(np.clip((h_risk + ml_risk) / 2.0, 0.0, 1.0))

    # Other scores
    rel, rel_reasons = reliability_score(row1)
    drop, drop_reasons = dropout_risk(row1)
    fraud, fraud_reasons = fraud_anomaly_score(member_id, contributions, loans, loan_payments)

    liq = liquidity_forecast_simple(contributions, foundation_contributions, loans, loan_payments, payouts, horizon_days=30)
    liquidity_ok = bool(liq.get("ok")) and float(liq.get("avg_daily_net", 0.0)) >= 0

    # loan decision
    req_amt = st.number_input("Test loan amount (for recommendation)", min_value=0.0, value=3000.0, step=500.0)
    decision, dec_reasons = smart_loan_decision(final_risk, rel, liquidity_ok, float(req_amt))

    # alerts
    alerts = generate_alerts(member_name, final_risk, rel, drop, fraud, liq)

    # top risky (heuristic across all)
    top_risky: list[dict] = []
    try:
        tmp = []
        id2name = _member_map(members2)
        for _, rr in X.iterrows():
            r, _ = compute_heuristic_risk(rr)
            mid2 = int(rr["member_id"])
            tmp.append({"member_id": mid2, "name": id2name.get(mid2, f"Member {mid2}"), "risk": float(r)})
        tmp.sort(key=lambda z: z["risk"], reverse=True)
        top_risky = tmp[:5]
    except Exception:
        top_risky = []

    # Minutes (defaults)
    meeting_title_default = "THE YOUNG SHALL GROW (NJANGI) — Meeting Minutes"
    agenda_default = "Treasury update, contributions, loans, payouts, fines, risk review, and resolutions."

    # Tabs
    tab_risk, tab_scores, tab_fraud, tab_liq, tab_loan, tab_alerts, tab_chat, tab_minutes = st.tabs([
        "📈 Risk",
        "✅ Reliability & Dropout",
        "🕵🏽 Fraud/Anomaly",
        "💰 Liquidity Forecast",
        "🧾 Loan Decision",
        "🚨 Alerts Center",
        "💬 System Chat",
        "📝 Minutes",
    ])

    with tab_risk:
        st.subheader("Risk prediction")
        st.metric("Final Risk", f"{final_risk*100:.1f}%")
        st.progress(float(np.clip(final_risk, 0.0, 1.0)))
        st.caption("Heuristic signals:")
        for r in h_reasons:
            st.write(f"• {r}")

        if mode in ["ML (XGBoost)", "Hybrid"]:
            if ml_risk is None:
                st.info(f"ML not ready: {ml_msg}")
            else:
                st.success(f"ML active: {ml_msg} • Member ML risk: {ml_risk*100:.1f}%")

        with st.expander("Member feature snapshot (no blanks)", expanded=False):
            snap = row.T
            snap.columns = ["value"]
            st.dataframe(snap, use_container_width=True)

    with tab_scores:
        c1, c2, c3 = st.columns(3)
        c1.metric("Reliability (0–100)", f"{rel}")
        c2.metric("Dropout Risk", f"{drop*100:.0f}%")
        c3.metric("Fraud/Anomaly", f"{fraud*100:.0f}%")
        st.write("**Reliability reasons**")
        for r in rel_reasons:
            st.write(f"• {r}")
        st.write("**Dropout risk reasons**")
        for r in drop_reasons:
            st.write(f"• {r}")

    with tab_fraud:
        st.metric("Fraud/Anomaly Score", f"{fraud*100:.0f}%")
        st.progress(float(np.clip(fraud, 0.0, 1.0)))
        if fraud_reasons:
            st.write("**Signals detected**")
            for r in fraud_reasons:
                st.write(f"• {r}")
        else:
            st.info("No anomaly signals detected from current data.")

    with tab_liq:
        if not liq.get("ok"):
            st.warning(liq.get("msg", "Liquidity forecast unavailable."))
        else:
            st.metric("Estimated Net Balance (approx)", f"{liq.get('balance_est', 0.0):,.0f}")
            st.metric("Avg Daily Net Flow (last ~30d)", f"{liq.get('avg_daily_net', 0.0):,.1f}")
            df_fc = pd.DataFrame({"date": liq["dates"], "forecast_balance": liq["forecast_balance"]})
            st.line_chart(df_fc.set_index("date"))

    with tab_loan:
        st.write(f"**Decision:** `{decision}`")
        for r in dec_reasons:
            st.write(f"• {r}")

    with tab_alerts:
        if not alerts:
            st.success("No alerts generated.")
        else:
            for a in alerts:
                sev = a.get("severity")
                msg = a.get("message", "")
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

    with tab_chat:
        st.caption("System Chat Assistant (free). Type `help` to see commands.")
        if "system_ai_msgs" not in st.session_state:
            st.session_state.system_ai_msgs = []

        # minutes text in context will be updated in Minutes tab (stored in session state)
        minutes_text = st.session_state.get("__latest_minutes_text__", "")

        chat_ctx = {
            "members": members2,
            "contrib": contributions,
            "loans": loans,
            "payments": loan_payments,
            "payouts": payouts,
            "fines": fines,
            "foundation": foundation_contributions,
            "top_risky": top_risky,
            "alerts": alerts,
            "minutes_text": minutes_text,
        }

        for role, msg in st.session_state.system_ai_msgs[-20:]:
            with st.chat_message(role):
                st.markdown(msg)

        q = st.chat_input("Ask about your system: totals, loan status, member summary, top risky, alerts, minutes…")
        if q:
            st.session_state.system_ai_msgs.append(("user", q))
            ans = system_chat_answer(q, chat_ctx)
            with st.chat_message("assistant"):
                st.markdown(ans)
            st.session_state.system_ai_msgs.append(("assistant", ans))

        with st.expander("🔎 Chat context (debug)", expanded=False):
            st.write(
                f"members={len(members2)} contrib={len(contributions)} loans={len(loans)} "
                f"payments={len(loan_payments)} payouts={len(payouts)} fines={len(fines)} foundation={len(foundation_contributions)}"
            )

    with tab_minutes:
        st.caption("Minutes generator (free). Produces copy/paste minutes from your real tables. Optional DB save if `minutes` table exists.")

        # Optional session filter (only affects tables that have session_id, like contributions/payouts)
        session_id = None
        if sessions is not None and not sessions.empty and "id" in sessions.columns:
            s = sessions.copy()
            s["id"] = _to_int(s["id"])
            date_col = "session_date" if "session_date" in s.columns else ("created_at" if "created_at" in s.columns else None)
            if date_col:
                s[date_col] = _to_dt_utc(s[date_col])
                s["label"] = s.apply(lambda r: f"Session {int(r['id'])} • {r[date_col].date() if pd.notna(r[date_col]) else ''}", axis=1)
                opts = ["All data (no session filter)"] + s["label"].tolist()
                sel = st.selectbox("Filter minutes by session (optional)", opts, index=0)
                if sel != "All data (no session filter)":
                    session_id = int(s.loc[s["label"] == sel, "id"].iloc[0])

        def filt_by_session(df: pd.DataFrame) -> pd.DataFrame:
            if session_id is None:
                return df
            if df is None or df.empty or "session_id" not in df.columns:
                return df
            d = df.copy()
            d["session_id"] = _to_int(d["session_id"])
            return d[d["session_id"] == int(session_id)].copy()

        contrib_f = filt_by_session(contributions)
        payouts_f = filt_by_session(payouts)

        meeting_title = st.text_input("Meeting title", value=meeting_title_default)
        meeting_date = st.date_input("Meeting date", value=pd.Timestamp.utcnow().date())
        location = st.text_input("Location (optional)", value="")
        chairperson = st.text_input("Chairperson (optional)", value="")
        secretary = st.text_input("Secretary (optional)", value="")
        agenda = st.text_area("Agenda (optional)", value=agenda_default)

        minutes_text = build_minutes_text(
            meeting_title=meeting_title,
            meeting_date=pd.Timestamp(meeting_date),
            location=location,
            chairperson=chairperson,
            secretary=secretary,
            agenda=agenda,
            members=members2,
            contrib=contrib_f,
            foundation=foundation_contributions,
            loans=loans,
            payments=loan_payments,
            payouts=payouts_f,
            fines=fines,
            top_risky=top_risky,
            alerts=alerts,
        )

        st.session_state["__latest_minutes_text__"] = minutes_text
        st.text_area("Generated Minutes (copy/paste)", value=minutes_text, height=420)

        # Optional DB save
        client = sb_service if sb_service is not None else sb_anon
        can_save = _table_exists(client, schema, "minutes")

        if can_save:
            st.info("A `minutes` table exists. You can save this minutes text to the database.")
            if st.button("💾 Save Minutes to DB"):
                row = {
                    "meeting_date": str(meeting_date),
                    "title": meeting_title,
                    "content": minutes_text,
                    "session_id": int(session_id) if session_id is not None else None,
                }
                ok, msg = _safe_insert(client, schema, "minutes", row)
                if ok:
                    st.success("Minutes saved.")
                else:
                    st.error("Failed to save minutes.")
                    st.code(msg, language="text")
        else:       st.caption("No `minutes` table found (or no DB client passed). Copy/paste the minutes text, or create a minutes table later.")
