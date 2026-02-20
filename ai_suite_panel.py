# ai_suite_panel.py ✅ ADVANCED+ NJANGI AI SUITE (NO API KEY) — SINGLE FILE
# ------------------------------------------------------------------------------
# ✅ Includes:
#   • Risk scoring (Heuristic + XGBoost if installed) + Hybrid
#   • Reliability score (0–100)
#   • Dropout risk
#   • Fraud/Anomaly detection
#   • Liquidity forecast
#   • Smart loan decision engine
#   • Alerts Center
#   • System Chat Assistant (free, grounded)
#   • Minutes generator + Download + optional save to DB if `minutes` table exists
#
# ✅ NEW “Next-Level” Features:
#   • Early Warning Heatmap (all members)
#   • Risk Trend Over Time (selected member, last N days)
#   • Member Segmentation (K-Means clustering implemented with NumPy)
#   • What-If Stress Test (contrib drop + payment delays + fines shock)
#   • Interest Income Projection (simple)
#
# Streamlit 2025+ safe: uses width="stretch"
# Cache-safe: never caches supabase clients
# ------------------------------------------------------------------------------

from __future__ import annotations

import re
import time
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

W_STRETCH = "stretch"


# ============================================================
# Basic utilities
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


def _fmt_pct01(x: float) -> str:
    try:
        return f"{float(x) * 100:.1f}%"
    except Exception:
        return "—"


def _df_fingerprint(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "empty"
    try:
        cols = df.columns.tolist()
        h = pd.util.hash_pandas_object(df[cols], index=True).sum()
        return str(int(h))
    except Exception:
        return f"len={len(df)};cols={len(getattr(df,'columns',[]))}"


def _infer_member_name_col(members: pd.DataFrame) -> str | None:
    for c in ["display_name", "name", "full_name", "member_name"]:
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
        # keep dt columns if present
        if col.endswith("_dt"):
            continue
        if col.endswith("_count") or col.endswith("_n"):
            X[col] = pd.to_numeric(X[col], errors="coerce").fillna(0).astype(int)
        elif col.startswith("days_since_") or col.endswith("_days"):
            X[col] = pd.to_numeric(X[col], errors="coerce").fillna(999).astype(int)
        else:
            X[col] = pd.to_numeric(X[col], errors="coerce").fillna(0.0)
    return X


def _throttle(slow_mode: bool, min_interval_s: float = 0.12):
    if not slow_mode:
        return
    last = st.session_state.get("__ai_suite_last_tick__", 0.0)
    now = time.time()
    wait = (last + float(min_interval_s)) - now
    if wait > 0:
        time.sleep(wait)
    st.session_state["__ai_suite_last_tick__"] = time.time()


# ============================================================
# Optional DB helpers (Minutes saving only)
# ============================================================
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


# ============================================================
# Feature engineering (member-level)
#   Keeps *_last_dt columns for trend analysis
# ============================================================
@st.cache_data(ttl=180, show_spinner=False)
def build_member_features_cached(
    members: pd.DataFrame,
    contrib: pd.DataFrame,
    loans: pd.DataFrame,
    payments: pd.DataFrame,
    payouts: pd.DataFrame,
    fines: pd.DataFrame,
    foundation: pd.DataFrame,
    _fp_members: str,
    _fp_contrib: str,
    _fp_loans: str,
    _fp_payments: str,
    _fp_payouts: str,
    _fp_fines: str,
    _fp_foundation: str,
) -> pd.DataFrame:
    return build_member_features(members, contrib, loans, payments, payouts, fines, foundation)


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
            cfeat = cfeat.merge(c.groupby("member_id")["session_id"].nunique().rename("contrib_sessions_n"),
                                left_on="member_id", right_index=True, how="left")
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
        for col in ["principal", "principal_current", "total_due", "unpaid_interest", "interest_rate_monthly"]:
            if col in l.columns:
                l[col] = _to_num(l[col])

        l["status"] = l.get("status", "").astype(str).str.lower().fillna("")
        l["last_paid_at"] = _to_dt_utc(l.get("last_paid_at", pd.NaT))
        l["borrow_date"] = _to_dt_utc(l.get("borrow_date", l.get("created_at", pd.NaT)))

        if "principal_current" in l.columns:
            l["balance_calc"] = l["principal_current"]
        elif "principal" in l.columns:
            l["balance_calc"] = l["principal"]
        else:
            l["balance_calc"] = 0.0

        grp = l.groupby("member_id", dropna=False)
        lfeat = lfeat.merge(grp.size().rename("loan_count"), left_on="member_id", right_index=True, how="left")
        lfeat = lfeat.merge(grp["balance_calc"].sum().rename("loan_balance_sum"), left_on="member_id", right_index=True, how="left")
        lfeat = lfeat.merge(grp["borrow_date"].max().rename("loan_last_borrow_dt"), left_on="member_id", right_index=True, how="left")
        lfeat = lfeat.merge(grp["last_paid_at"].max().rename("loan_last_paid_dt"), left_on="member_id", right_index=True, how="left")

        if "unpaid_interest" in l.columns:
            lfeat = lfeat.merge(grp["unpaid_interest"].sum().rename("loan_unpaid_interest_sum"), left_on="member_id", right_index=True, how="left")
        else:
            lfeat["loan_unpaid_interest_sum"] = 0.0

        if "interest_rate_monthly" in l.columns:
            # simple weighted avg by principal_current/principal
            w = l["balance_calc"].replace(0, np.nan)
            rate_wavg = (l["interest_rate_monthly"] * w).groupby(l["member_id"]).sum() / w.groupby(l["member_id"]).sum()
            lfeat = lfeat.merge(rate_wavg.rename("loan_interest_rate_wavg"), left_on="member_id", right_index=True, how="left")
        else:
            lfeat["loan_interest_rate_wavg"] = 0.0

        if "total_due" in l.columns:
            lfeat = lfeat.merge(grp["total_due"].sum().rename("loan_total_due_sum"), left_on="member_id", right_index=True, how="left")
        else:
            lfeat["loan_total_due_sum"] = 0.0

        bad_tokens = ["delinquent", "default", "overdue", "late", "arrears", "past due", "past_due", "unpaid"]
        lfeat = lfeat.merge(
            grp["status"].apply(lambda s: sum(any(tok in str(v) for tok in bad_tokens) for v in s)).rename("loan_bad_status_count"),
            left_on="member_id", right_index=True, how="left",
        )
        lfeat["active_loan_count"] = grp["status"].apply(lambda s: int(sum(str(v) == "active" for v in s))).values
    else:
        lfeat["loan_count"] = 0
        lfeat["loan_balance_sum"] = 0.0
        lfeat["loan_total_due_sum"] = 0.0
        lfeat["loan_bad_status_count"] = 0
        lfeat["loan_last_paid_dt"] = pd.NaT
        lfeat["loan_last_borrow_dt"] = pd.NaT
        lfeat["loan_unpaid_interest_sum"] = 0.0
        lfeat["loan_interest_rate_wavg"] = 0.0
        lfeat["active_loan_count"] = 0

    # Payments
    pfeat = base.copy()
    if payments is not None and not payments.empty and "member_id" in payments.columns:
        p = payments.copy()
        p["member_id"] = _to_int(p["member_id"])
        p["amount"] = _to_num(p.get("amount", 0))
        dtc = "paid_at" if "paid_at" in p.columns else ("created_at" if "created_at" in p.columns else None)
        p["paid_at_calc"] = _to_dt_utc(p.get(dtc, pd.NaT)) if dtc else pd.NaT
        grp = p.groupby("member_id", dropna=False)
        pfeat = pfeat.merge(grp["amount"].count().rename("pay_count"), left_on="member_id", right_index=True, how="left")
        pfeat = pfeat.merge(grp["amount"].sum().rename("pay_total"), left_on="member_id", right_index=True, how="left")
        pfeat = pfeat.merge(grp["paid_at_calc"].max().rename("pay_last_dt"), left_on="member_id", right_index=True, how="left")
    else:
        pfeat["pay_count"] = 0
        pfeat["pay_total"] = 0.0
        pfeat["pay_last_dt"] = pd.NaT

    pfeat["days_since_last_payment"] = _days_since(now, pfeat["pay_last_dt"])

    # Payouts
    poutfeat = base.copy()
    if payouts is not None and not payouts.empty and "member_id" in payouts.columns:
        po = payouts.copy()
        po["member_id"] = _to_int(po["member_id"])
        amt_col = "payout_amount" if "payout_amount" in po.columns else ("amount" if "amount" in po.columns else None)
        dt_col = "payout_date" if "payout_date" in po.columns else ("created_at" if "created_at" in po.columns else None)
        po["payout_amount_calc"] = _to_num(po[amt_col]) if amt_col else 0.0
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

    # Foundation
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

    return _fill_feature_defaults(X)


# ============================================================
# Heuristic Risk
# ============================================================
def compute_heuristic_risk(row: pd.Series, *, cfg: dict[str, float] | None = None) -> tuple[float, list[str]]:
    cfg = cfg or {}
    reasons: list[str] = []

    loan_balance = float(row.get("loan_balance_sum", 0.0))
    total_due = float(row.get("loan_total_due_sum", 0.0))
    bad_status = int(row.get("loan_bad_status_count", 0))
    days_pay = int(row.get("days_since_last_payment", 999))
    days_contrib = int(row.get("days_since_last_contrib", 999))
    fine_total = float(row.get("fine_total", 0.0))
    contrib_total = float(row.get("contrib_total", 0.0))
    contrib_count = int(row.get("contrib_count", 0))

    w_loan = float(cfg.get("w_loan_balance", 0.20))
    w_due = float(cfg.get("w_total_due", 0.10))
    w_bad = float(cfg.get("w_bad_status", 0.10))
    w_pay30 = float(cfg.get("w_no_payment_30", 0.25))
    w_pay14 = float(cfg.get("w_no_payment_14", 0.15))
    w_contrib30 = float(cfg.get("w_no_contrib_30", 0.15))
    w_contrib14 = float(cfg.get("w_no_contrib_14", 0.08))
    w_fines = float(cfg.get("w_fines", 0.15))
    bonus_contrib_total = float(cfg.get("bonus_contrib_total", 0.05))
    bonus_contrib_freq = float(cfg.get("bonus_contrib_freq", 0.05))

    score = 0.0

    if loan_balance > 0:
        score += w_loan
        reasons.append("Has outstanding loan balance")

    if total_due > 0 and total_due >= max(loan_balance, 1.0) * 1.02:
        score += w_due
        reasons.append("Total due suggests interest/arrears")

    if bad_status > 0:
        score += min(0.30, w_bad * bad_status)
        reasons.append("Loan status flagged as overdue/delinquent/etc.")

    if loan_balance > 0:
        if days_pay >= 30:
            score += w_pay30
            reasons.append("No recent loan payment (≥30 days) while loan balance > 0")
        elif days_pay >= 14:
            score += w_pay14
            reasons.append("No recent loan payment (≥14 days) while loan balance > 0")

    if days_contrib >= 30:
        score += w_contrib30
        reasons.append("No recent contribution (≥30 days)")
    elif days_contrib >= 14:
        score += w_contrib14
        reasons.append("No recent contribution (≥14 days)")

    if fine_total > 0:
        score += min(w_fines, fine_total / 2000.0)
        reasons.append("Has fines")

    if contrib_total >= 2000:
        score -= bonus_contrib_total
        reasons.append("Strong contributions reduce risk")
    if contrib_count >= 6:
        score -= bonus_contrib_freq
        reasons.append("Consistent contribution frequency reduces risk")

    return float(np.clip(score, 0.0, 1.0)), reasons[:6]


# ============================================================
# XGBoost (optional)
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
        "member_id", "status",
        "principal", "principal_current", "interest_rate_monthly",
        "total_due", "unpaid_interest", "due_cycle_days",
        "loan_age_days", "days_since_last_payment",
        "target",
    ]
    out = l[cols].copy()
    out = out.replace([np.inf, -np.inf], np.nan).fillna(0)
    out = out[out["member_id"] > 0].copy()
    return out


def _xgb_get_or_train(loans_ml: pd.DataFrame):
    try:
        from xgboost import XGBClassifier
    except Exception as e:
        return None, f"xgboost not installed or failed to import: {repr(e)}"

    feature_cols = [
        "principal", "principal_current", "interest_rate_monthly",
        "total_due", "unpaid_interest", "due_cycle_days",
        "loan_age_days", "days_since_last_payment",
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
        "principal", "principal_current", "interest_rate_monthly",
        "total_due", "unpaid_interest", "due_cycle_days",
        "loan_age_days", "days_since_last_payment",
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
                reasons.append("Contribution is strong outlier vs history (3σ)")
            if mu > 0 and last_amt > (mu * 3.0):
                score += 0.15
                reasons.append("Latest contribution unusually large (≥3× avg)")

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
            if "status" in ml.columns:
                s = ml["status"].astype(str).str.lower()
                if int((s == "active").sum()) >= 2:
                    score += 0.15
                    reasons.append("Multiple active loans at same time")

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
                reasons.append("Payment outlier vs history (3σ)")

    return _clip01(score), reasons[:6]


# ============================================================
# Liquidity
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
# Smart loan decision + Alerts
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
# NEW: Risk trend over time (selected member)
#   Uses last activity dates and recomputes “days_since” across the past N days.
# ============================================================
def member_risk_trend(
    member_row: pd.Series,
    cfg: dict[str, float],
    *,
    days_back: int = 90,
) -> pd.DataFrame:
    now = _utc_now().normalize()
    dates = [now - pd.Timedelta(days=i) for i in range(days_back, -1, -1)]

    # pull last known dates from features
    last_contrib = member_row.get("contrib_last_dt", pd.NaT)
    last_pay = member_row.get("pay_last_dt", pd.NaT)

    # if missing, treat as very old
    last_contrib = pd.to_datetime(last_contrib, utc=True, errors="coerce")
    last_pay = pd.to_datetime(last_pay, utc=True, errors="coerce")

    out = []
    for d in dates:
        rr = member_row.copy()

        # recompute days_since based on historical date "d"
        if pd.notna(last_contrib):
            rr["days_since_last_contrib"] = int((d - last_contrib.normalize()).days)
        else:
            rr["days_since_last_contrib"] = 999

        if pd.notna(last_pay):
            rr["days_since_last_payment"] = int((d - last_pay.normalize()).days)
        else:
            rr["days_since_last_payment"] = 999

        risk, _ = compute_heuristic_risk(rr, cfg=cfg)
        out.append({"date": d.date(), "risk": float(risk)})

    return pd.DataFrame(out)


# ============================================================
# NEW: Simple Interest Income Projection
#   Uses unpaid_interest if present; otherwise estimates:
#     interest_rate_monthly * loan_balance_sum * months
# ============================================================
def interest_projection(member_row: pd.Series, horizon_days: int = 30) -> dict:
    bal = float(member_row.get("loan_balance_sum", 0.0))
    unpaid = float(member_row.get("loan_unpaid_interest_sum", 0.0))
    rate_m = float(member_row.get("loan_interest_rate_wavg", 0.0))

    months = float(horizon_days) / 30.0
    est_interest = bal * rate_m * months if (bal > 0 and rate_m > 0) else 0.0

    return {
        "horizon_days": int(horizon_days),
        "loan_balance_sum": bal,
        "unpaid_interest_sum": unpaid,
        "rate_monthly_wavg": rate_m,
        "estimated_interest_income": est_interest,
    }


# ============================================================
# NEW: K-Means clustering (pure NumPy) for segmentation
# ============================================================
def _kmeans_numpy(X: np.ndarray, k: int = 3, iters: int = 30, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = X.shape[0]
    if n == 0:
        return np.array([]), np.array([])

    # init centers from random points
    idx = rng.choice(n, size=min(k, n), replace=False)
    centers = X[idx].copy()

    for _ in range(iters):
        # assign
        dists = ((X[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels = dists.argmin(axis=1)

        # update
        new_centers = centers.copy()
        for j in range(centers.shape[0]):
            pts = X[labels == j]
            if len(pts) > 0:
                new_centers[j] = pts.mean(axis=0)
        if np.allclose(new_centers, centers, atol=1e-6):
            centers = new_centers
            break
        centers = new_centers

    # final assign
    dists = ((X[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
    labels = dists.argmin(axis=1)
    return labels, centers


def segmentation_clusters(features_df: pd.DataFrame, members_df: pd.DataFrame, k: int = 3) -> pd.DataFrame:
    if features_df is None or features_df.empty:
        return pd.DataFrame()

    id2name = _member_map(members_df)

    seg_cols = [
        "contrib_total",
        "contrib_count",
        "days_since_last_contrib",
        "loan_balance_sum",
        "loan_bad_status_count",
        "days_since_last_payment",
        "fine_total",
        "foundation_total",
    ]
    cols = [c for c in seg_cols if c in features_df.columns]
    if not cols:
        return pd.DataFrame()

    D = features_df[["member_id"] + cols].copy()
    D["member_id"] = _to_int(D["member_id"])
    for c in cols:
        D[c] = pd.to_numeric(D[c], errors="coerce").fillna(0.0)

    # standardize
    X = D[cols].to_numpy(dtype=float)
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd == 0, 1.0, sd)
    Xz = (X - mu) / sd

    labels, _ = _kmeans_numpy(Xz, k=int(max(2, min(k, len(D)))), iters=40, seed=42)
    if labels.size == 0:
        return pd.DataFrame()

    D["segment"] = labels.astype(int)
    D["name"] = D["member_id"].apply(lambda mid: id2name.get(int(mid), f"Member {int(mid)}"))
    return D[["member_id", "name", "segment"] + cols].sort_values(["segment", "member_id"])


# ============================================================
# NEW: Early warning heatmap (all members)
# ============================================================
def early_warning_table(
    X: pd.DataFrame,
    members_df: pd.DataFrame,
    cfg: dict[str, float],
) -> pd.DataFrame:
    id2name = _member_map(members_df)
    rows = []
    for _, rr in X.iterrows():
        r, _ = compute_heuristic_risk(rr, cfg=cfg)
        rel, _ = compute_reliability_score(rr)
        drop, _ = dropout_risk(rr)

        # fraud needs raw tables normally; we keep a lightweight proxy here
        # (you still get real fraud score on member detail tab)
        fraud_proxy = float(np.clip(0.05 + 0.12 * float(rr.get("loan_bad_status_count", 0)) + 0.05 * float(rr.get("fine_count", 0)), 0, 1))

        flags = []
        if r >= 0.70:
            flags.append("HIGH_RISK")
        if rel < 45:
            flags.append("LOW_RELIABILITY")
        if drop >= 0.70:
            flags.append("DROPOUT_RISK")
        if fraud_proxy >= 0.45:
            flags.append("ANOMALY_PROXY")

        rows.append({
            "member_id": int(rr.get("member_id", 0)),
            "name": id2name.get(int(rr.get("member_id", 0)), f"Member {int(rr.get('member_id',0))}"),
            "risk": float(r),
            "reliability": int(rel),
            "dropout": float(drop),
            "anomaly_proxy": float(fraud_proxy),
            "flags": ", ".join(flags) if flags else "",
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["risk", "dropout", "reliability"], ascending=[False, False, True])


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
# System Chat Assistant
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
        name_col = _infer_member_name_col(members)
        if name_col and members is not None and not members.empty:
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
            "- `alerts`\n"
            "- `minutes`\n"
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

    if "alerts" in ql or "alert" in ql:
        if not alerts:
            return "No alerts generated right now."
        out = "### 🚨 Alerts\n"
        for a in alerts[:25]:
            out += f"- **{a.get('severity','').upper()}** [{a.get('type','')}] — {a.get('message','')}\n"
        return out

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
    slow_mode: bool = True,
):
    sessions = sessions if sessions is not None else pd.DataFrame()

    with st.sidebar:
        st.subheader("🧠 AI Suite Settings")
        slow_mode = st.toggle("🐢 Slow Mode", value=bool(slow_mode))
        min_loans_for_ml = st.number_input("Min loans for ML (XGBoost)", min_value=5, value=int(min_loans_for_ml), step=1)

        st.divider()
        st.caption("Heuristic weights (optional)")
        w_loan_balance = st.slider("Outstanding loan weight", 0.0, 0.50, 0.20, 0.01)
        w_total_due = st.slider("Total due/arrears weight", 0.0, 0.30, 0.10, 0.01)
        w_bad_status = st.slider("Bad status per flag", 0.0, 0.30, 0.10, 0.01)
        w_no_payment_30 = st.slider("No payment ≥30d", 0.0, 0.50, 0.25, 0.01)
        w_no_payment_14 = st.slider("No payment ≥14d", 0.0, 0.50, 0.15, 0.01)
        w_no_contrib_30 = st.slider("No contrib ≥30d", 0.0, 0.50, 0.15, 0.01)
        w_no_contrib_14 = st.slider("No contrib ≥14d", 0.0, 0.50, 0.08, 0.01)
        w_fines = st.slider("Fines cap weight", 0.0, 0.50, 0.15, 0.01)
        bonus_contrib_total = st.slider("Bonus: strong total contrib", 0.0, 0.20, 0.05, 0.01)
        bonus_contrib_freq = st.slider("Bonus: contrib frequency", 0.0, 0.20, 0.05, 0.01)

        cfg = {
            "w_loan_balance": float(w_loan_balance),
            "w_total_due": float(w_total_due),
            "w_bad_status": float(w_bad_status),
            "w_no_payment_30": float(w_no_payment_30),
            "w_no_payment_14": float(w_no_payment_14),
            "w_no_contrib_30": float(w_no_contrib_30),
            "w_no_contrib_14": float(w_no_contrib_14),
            "w_fines": float(w_fines),
            "bonus_contrib_total": float(bonus_contrib_total),
            "bonus_contrib_freq": float(bonus_contrib_freq),
        }

        st.divider()
        st.subheader("🧪 Stress Test (What-if)")
        stress_contrib_drop = st.slider("Contribution drop (%)", 0, 80, 0, 5)
        stress_payment_delay = st.slider("Payment delay (extra days)", 0, 90, 0, 5)
        stress_fines_increase = st.slider("Fines increase (%)", 0, 200, 0, 10)

        st.divider()
        st.subheader("🧩 Segmentation")
        k = st.slider("Number of segments (K)", 2, 6, 3, 1)

    _throttle(slow_mode)

    X = build_member_features_cached(
        members=members,
        contrib=contributions,
        loans=loans,
        payments=loan_payments,
        payouts=payouts,
        fines=fines,
        foundation=foundation_contributions,
        _fp_members=_df_fingerprint(members),
        _fp_contrib=_df_fingerprint(contributions),
        _fp_loans=_df_fingerprint(loans),
        _fp_payments=_df_fingerprint(loan_payments),
        _fp_payouts=_df_fingerprint(payouts),
        _fp_fines=_df_fingerprint(fines),
        _fp_foundation=_df_fingerprint(foundation_contributions),
    )

    if X is None or X.empty:
        st.error("AI Suite: could not build features (check members / ids).")
        return

    members2 = members.copy()
    members2["id"] = _to_int(members2["id"])
    members2 = members2[members2["id"] > 0].copy()
    name_col = _infer_member_name_col(members2) or "name"
    if name_col not in members2.columns:
        members2["name"] = members2.get("name", "").astype(str)
        name_col = "name"
    members2[name_col] = members2[name_col].astype(str)
    members2["label"] = members2.apply(lambda r: f"{int(r['id']):02d} • {r.get(name_col,'')}", axis=1)

    st.header("🧠 NJANGI AI Suite — Advanced+")
    st.caption("Risk • Reliability • Dropout • Fraud • Liquidity • Decisions • Alerts • Trends • Segments • Stress Test")

    pick = st.selectbox("Select member", members2["label"].tolist(), index=0)
    member_id = int(members2.loc[members2["label"] == pick, "id"].iloc[0])
    member_name = str(members2.loc[members2["id"] == member_id, name_col].iloc[0])

    row = X[X["member_id"] == int(member_id)]
    if row.empty:
        st.warning("No feature row for selected member.")
        return
    row1 = row.iloc[0]

    # Risk modes
    mode = st.radio("Risk mode", ["Heuristic", "ML (XGBoost)", "Hybrid"], horizontal=True)

    h_risk, h_reasons = compute_heuristic_risk(row1, cfg=cfg)
    ml_risk, ml_msg = xgb_risk_for_member(loans, member_id=member_id, min_rows=int(min_loans_for_ml))

    if mode == "Heuristic":
        final_risk = h_risk
        risk_source = "Heuristic"
    elif mode == "ML (XGBoost)":
        final_risk = ml_risk if ml_risk is not None else h_risk
        risk_source = "ML" if ml_risk is not None else "Heuristic (fallback)"
    else:
        final_risk = h_risk if ml_risk is None else float(np.clip((h_risk + ml_risk) / 2.0, 0.0, 1.0))
        risk_source = "Hybrid" if ml_risk is not None else "Heuristic (fallback)"

    rel, rel_reasons = compute_reliability_score(row1)
    drop, drop_reasons = dropout_risk(row1)
    fraud, fraud_reasons = fraud_anomaly_score(member_id, contributions, loans, loan_payments)

    liq = liquidity_forecast_simple(contributions, foundation_contributions, loans, loan_payments, payouts, horizon_days=30)
    liquidity_ok = bool(liq.get("ok")) and float(liq.get("avg_daily_net", 0.0)) >= 0

    req_amt = st.number_input("Test loan amount (recommendation)", min_value=0.0, value=3000.0, step=500.0)
    decision, dec_reasons = smart_loan_decision(final_risk, rel, liquidity_ok, float(req_amt))
    alerts = generate_alerts(member_name, final_risk, rel, drop, fraud, liq)

    # Top risky (all)
    id2name = _member_map(members2)
    top_risky = []
    try:
        tmp = []
        for _, rr in X.iterrows():
            r, _ = compute_heuristic_risk(rr, cfg=cfg)
            mid = int(rr["member_id"])
            tmp.append({"member_id": mid, "name": id2name.get(mid, f"Member {mid}"), "risk": float(r)})
        tmp.sort(key=lambda z: z["risk"], reverse=True)
        top_risky = tmp[:5]
    except Exception:
        top_risky = []

    # KPI row
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Final Risk", _fmt_pct01(final_risk), help=f"Source: {risk_source}")
    k2.metric("Reliability", f"{rel}/100")
    k3.metric("Dropout Risk", _fmt_pct01(drop))
    k4.metric("Fraud/Anomaly", _fmt_pct01(fraud))

    # Tabs
    tab_risk, tab_heat, tab_trend, tab_seg, tab_stress, tab_liq, tab_loan, tab_alerts, tab_chat, tab_minutes = st.tabs([
        "📈 Risk (Member)",
        "🟧 Early Warning Heatmap",
        "📉 Risk Trend",
        "🧩 Segmentation",
        "🧪 Stress Test",
        "💰 Liquidity",
        "🧾 Loan Decision",
        "🚨 Alerts",
        "💬 System Chat",
        "📝 Minutes",
    ])

    with tab_risk:
        st.subheader(f"Risk prediction — {member_name}")
        st.metric("Final Risk", _fmt_pct01(final_risk))
        st.progress(float(np.clip(final_risk, 0.0, 1.0)))

        c1, c2 = st.columns(2)
        with c1:
            st.caption("Heuristic signals")
            for r in h_reasons:
                st.write(f"• {r}")
        with c2:
            st.caption("ML status")
            if mode in ["ML (XGBoost)", "Hybrid"]:
                if ml_risk is None:
                    st.info(f"ML not ready: {ml_msg}")
                else:
                    st.success(f"ML active: {ml_msg}")
                    st.write(f"• ML risk: **{_fmt_pct01(ml_risk)}**")
            else:
                st.write("• ML not selected.")

        st.write("**Reliability reasons**")
        for r in rel_reasons:
            st.write(f"• {r}")

        st.write("**Dropout reasons**")
        for r in drop_reasons:
            st.write(f"• {r}")

        st.write("**Fraud signals**")
        if fraud_reasons:
            for r in fraud_reasons:
                st.write(f"• {r}")
        else:
            st.caption("No strong anomaly signals detected.")

        with st.expander("Member feature snapshot", expanded=False):
            snap = row.T
            snap.columns = ["value"]
            st.dataframe(snap, width=W_STRETCH)

        st.divider()
        st.subheader("💸 Interest Projection")
        h = st.selectbox("Interest horizon", [30, 60, 90], index=0)
        proj = interest_projection(row1, horizon_days=int(h))
        st.write(f"- Loan balance: **{_fmt_money(proj['loan_balance_sum'])}**")
        st.write(f"- Unpaid interest (current): **{_fmt_money(proj['unpaid_interest_sum'])}**")
        st.write(f"- Weighted monthly rate: **{proj['rate_monthly_wavg']:.3f}**")
        st.write(f"- Estimated interest income ({h}d): **{_fmt_money(proj['estimated_interest_income'])}**")

    with tab_heat:
        st.subheader("Early Warning Heatmap (All Members)")
        df_warn = early_warning_table(X, members2, cfg=cfg)
        if df_warn.empty:
            st.info("Not enough data to generate early warning table.")
        else:
            # show risk as %
            df_show = df_warn.copy()
            df_show["risk_%"] = (df_show["risk"] * 100).round(1)
            df_show["dropout_%"] = (df_show["dropout"] * 100).round(0).astype(int)
            df_show["anomaly_%"] = (df_show["anomaly_proxy"] * 100).round(0).astype(int)
            df_show = df_show.drop(columns=["risk", "dropout", "anomaly_proxy"])
            st.dataframe(df_show, width=W_STRETCH)
            st.download_button(
                "⬇️ Download early warnings (CSV)",
                df_warn.to_csv(index=False).encode("utf-8"),
                file_name="early_warning_heatmap.csv",
                mime="text/csv",
            )

    with tab_trend:
        st.subheader("Risk Trend Over Time (Heuristic)")
        days_back = st.slider("Days back", 30, 180, 90, 10)
        df_tr = member_risk_trend(row1, cfg=cfg, days_back=int(days_back))
        if df_tr is None or df_tr.empty:
            st.info("Trend unavailable (missing date fields).")
        else:
            st.line_chart(df_tr.set_index("date"))
            st.caption("This trend is computed by re-evaluating risk using the same balances but changing the inactivity days across time.")

    with tab_seg:
        st.subheader("Member Segmentation (K-Means, No sklearn)")
        seg = segmentation_clusters(X, members2, k=int(k))
        if seg.empty:
            st.info("Segmentation unavailable (missing segmentation columns).")
        else:
            st.dataframe(seg, width=W_STRETCH)

            st.divider()
            st.caption("Segment summary (avg per segment)")
            cols = [c for c in seg.columns if c not in ("member_id", "name", "segment")]
            summary = seg.groupby("segment")[cols].mean(numeric_only=True).reset_index()
            st.dataframe(summary, width=W_STRETCH)

            st.download_button(
                "⬇️ Download segmentation (CSV)",
                seg.to_csv(index=False).encode("utf-8"),
                file_name="member_segmentation.csv",
                mime="text/csv",
            )

    with tab_stress:
        st.subheader("Stress Test (What-If)")
        st.caption("Simulate shocks and see how risk changes for this member.")

        rr = row1.copy()

        # apply stress
        rr["contrib_total"] = float(rr.get("contrib_total", 0.0)) * (1.0 - (stress_contrib_drop / 100.0))
        rr["contrib_count"] = int(rr.get("contrib_count", 0))
        rr["days_since_last_payment"] = int(rr.get("days_since_last_payment", 999)) + int(stress_payment_delay)
        rr["fine_total"] = float(rr.get("fine_total", 0.0)) * (1.0 + (stress_fines_increase / 100.0))

        stressed_risk, stressed_reasons = compute_heuristic_risk(rr, cfg=cfg)

        c1, c2 = st.columns(2)
        c1.metric("Current Risk", _fmt_pct01(final_risk))
        c2.metric("Stressed Risk", _fmt_pct01(stressed_risk))

        st.progress(float(np.clip(stressed_risk, 0.0, 1.0)))
        st.write("**Stressed signals**")
        for r in stressed_reasons:
            st.write(f"• {r}")

        st.caption("Use this to decide caps/conditions under tougher economic conditions.")

    with tab_liq:
        st.subheader("Liquidity Forecast (System-Level)")
        if not liq.get("ok"):
            st.warning(liq.get("msg", "Liquidity forecast unavailable."))
        else:
            c1, c2 = st.columns(2)
            c1.metric("Estimated Net Balance (approx)", f"{liq.get('balance_est', 0.0):,.0f}")
            c2.metric("Avg Daily Net Flow (~30d)", f"{liq.get('avg_daily_net', 0.0):,.1f}")

            df_fc = pd.DataFrame({"date": liq["dates"], "forecast_balance": liq["forecast_balance"]})
            st.line_chart(df_fc.set_index("date"))

    with tab_loan:
        st.subheader("Smart Loan Recommendation")
        st.write(f"**Member:** {member_name} (ID {member_id})")
        st.write(f"**Requested amount:** {_fmt_money(req_amt)}")
        st.write(f"**Decision:** `{decision}`")
        for r in dec_reasons:
            st.write(f"• {r}")

    with tab_alerts:
        st.subheader("Alerts Center")
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
            df_top = pd.DataFrame(top_risky)
            st.dataframe(df_top, width=W_STRETCH)
            st.download_button(
                "⬇️ Download top risky (CSV)",
                df_top.to_csv(index=False).encode("utf-8"),
                file_name="top_risky_members.csv",
                mime="text/csv",
            )

    with tab_chat:
        st.subheader("System Chat Assistant (Free)")
        st.caption("Type `help` to see commands. Grounded in your real tables.")
        if "system_ai_msgs" not in st.session_state:
            st.session_state.system_ai_msgs = []

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

        for role, msg in st.session_state.system_ai_msgs[-30:]:
            with st.chat_message(role):
                st.markdown(msg)

        q = st.chat_input("Ask: totals, loan status, member summary, alerts, top risky, minutes…")
        if q:
            st.session_state.system_ai_msgs.append(("user", q))
            ans = system_chat_answer(q, chat_ctx)
            with st.chat_message("assistant"):
                st.markdown(ans)
            st.session_state.system_ai_msgs.append(("assistant", ans))

    with tab_minutes:
        st.subheader("Minutes Generator (Free)")
        st.caption("Generates copy/paste minutes from your real tables. Optional DB save if `minutes` table exists.")

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

        meeting_title_default = "THE YOUNG SHALL GROW (NJANGI) — Meeting Minutes"
        agenda_default = "Treasury update, contributions, loans, payouts, fines, risk review, and resolutions."

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

        st.download_button(
            "⬇️ Download minutes (TXT)",
            minutes_text.encode("utf-8"),
            file_name=f"minutes_{pd.Timestamp(meeting_date).strftime('%Y%m%d')}.txt",
            mime="text/plain",
        )

        client = sb_service if sb_service is not None else sb_anon
        can_save = _table_exists(client, schema, "minutes")

        if can_save:
            st.info("A `minutes` table exists. You can save these minutes to the database.")
            if st.button("💾 Save Minutes to DB"):
                rowdb = {
                    "meeting_date": str(meeting_date),
                    "title": meeting_title,
                    "content": minutes_text,
                    "session_id": int(session_id) if session_id is not None else None,
                }
                ok, msg = _safe_insert(client, schema, "minutes", rowdb)
                if ok:
                    st.success("Minutes saved.")
                else:
                    st.error("Failed to save minutes.")
                    st.code(msg, language="text")
        else:
            st.caption("No `minutes` table found (or no DB client passed). Copy/paste the minutes text, or create a minutes table later.")
