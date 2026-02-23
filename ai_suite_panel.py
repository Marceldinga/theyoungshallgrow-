
# ai_suite_panel.py ✅ COMPLETE SINGLE FILE — NJANGI STANDARD (NO legacy)
# =============================================================================
# 🧠 NJANGI AI Suite — Advanced+ (No API Key)
# Risk • Reliability • Dropout • Fraud • Liquidity • Decisions • Alerts • Trends
# Segments • Stress Test • (Optional) ML (XGBoost if installed + enough data)
#
# ✅ FIXES YOUR CRASH (ValueError length mismatch):
#   - ALL feature vectors are built on a FULL members index (member_id)
#   - Aggregates from loans/contrib/payments etc are MERGED back (left join)
#   - No ".values" assignment from shorter group results
#
# ✅ Snapshot-first friendly:
#   - Uses RPC: fn_finance_snapshot() when available (fast)
#   - Falls back to table reads safely
#
# ✅ Works with your app.py:
#   ai_suite_panel.render_full_ai_suite_panel(sb_anon=..., sb_service=..., schema=...)
#
# Optional deps (safe if missing):
#   - scikit-learn (segmentation)
#   - xgboost (ML model)
#
# =============================================================================

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

# Supabase APIError (safe import)
try:
    from postgrest.exceptions import APIError
except Exception:
    APIError = Exception  # type: ignore

# Optional ML/segmentation deps
try:
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler
except Exception:
    KMeans = None  # type: ignore
    StandardScaler = None  # type: ignore

try:
    import xgboost as xgb
except Exception:
    xgb = None  # type: ignore


# =============================================================================
# SETTINGS
# =============================================================================
FEATURE_TTL = 60
SNAPSHOT_TTL = 10
MAX_ROWS_SCAN = 250_000  # safety cap
ACTIVE_STATUSES = {"active", "open", "ongoing", "overdue", "late", "running", "disbursed"}

APP_TITLE = "🧠 NJANGI AI Suite — Advanced+ (No API Key)"
APP_TAGLINE = "Risk • Reliability • Dropout • Fraud • Liquidity • Decisions • Alerts • Trends • Segments • Stress Test • Minutes Generator"


# =============================================================================
# Helpers
# =============================================================================
def _api_msg(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or payload)
        return str(payload)
    return str(e)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _throttle_db():
    slow = bool(st.session_state.get("_slow_mode_override", True))
    min_wait = float(st.session_state.get("MIN_SECONDS_BETWEEN_DB_CALLS_UI", 0.15))
    if not slow:
        return
    last = float(st.session_state.get("_last_db_call_ts", 0.0))
    now = time.time()
    wait = min_wait - (now - last)
    if wait > 0:
        time.sleep(wait)
    st.session_state["_last_db_call_ts"] = time.time()


def _to_num(s: Any) -> float:
    try:
        v = pd.to_numeric(s, errors="coerce")
        return 0.0 if pd.isna(v) else float(v)
    except Exception:
        return 0.0


def _days_since_utc(dt: Any) -> Optional[int]:
    if dt is None or str(dt).strip() == "":
        return None
    try:
        ts = pd.to_datetime(dt, errors="coerce", utc=True)
        if pd.isna(ts):
            return None
        delta = (pd.Timestamp.utcnow().tz_localize("UTC") - ts)
        return int(delta.total_seconds() // 86400)
    except Exception:
        return None


def _pick_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _safe_sum(df: pd.DataFrame, col: Optional[str]) -> float:
    if df is None or df.empty or not col or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())


def _safe_count(df: pd.DataFrame) -> int:
    return 0 if df is None or df.empty else int(len(df))


def _money0(x: Any) -> str:
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)


# =============================================================================
# Supabase Readers
# =============================================================================
def _sb_select(
    sb_anon,
    sb_service,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 10_000,
    order_by: Optional[str] = None,
    desc: bool = True,
    filters: Optional[List[Tuple[str, str, Any]]] = None,
) -> pd.DataFrame:
    sb = sb_service or sb_anon
    if sb is None:
        return pd.DataFrame()

    def _apply(q):
        if filters:
            for col, op, val in filters:
                if val is None:
                    continue
                if op == "eq":
                    q = q.eq(col, val)
                elif op == "gte":
                    q = q.gte(col, val)
                elif op == "lte":
                    q = q.lte(col, val)
                elif op == "ilike":
                    q = q.ilike(col, val)
        if order_by:
            q = q.order(order_by, desc=desc)
        return q

    try:
        _throttle_db()
        q = (sb.schema(schema).table(table).select(cols).limit(int(limit)))
        q = _apply(q)
        res = q.execute()
        return pd.DataFrame(getattr(res, "data", None) or [])
    except Exception:
        try:
            _throttle_db()
            q = (sb.table(table).select(cols).limit(int(limit)))
            q = _apply(q)
            res = q.execute()
            return pd.DataFrame(getattr(res, "data", None) or [])
        except Exception as e:
            st.warning(f"Could not read {schema}.{table}: {_api_msg(e)}")
            return pd.DataFrame()


def _rpc_finance_snapshot(sb_anon, sb_service, schema: str) -> Dict[str, Any]:
    sb = sb_service or sb_anon
    if sb is None:
        return {}
    try:
        _throttle_db()
        try:
            res = sb.schema(schema).rpc("fn_finance_snapshot", {}).execute()
        except Exception:
            res = sb.rpc("fn_finance_snapshot", {}).execute()
        data = getattr(res, "data", None)
        if not data:
            return {}
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


# =============================================================================
# Feature Engineering (FULL members index → safe merges)
# =============================================================================
def _normalize_members(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["member_id", "member_name"])

    id_col = _pick_col(df, ["id", "member_id"])
    if not id_col:
        return pd.DataFrame(columns=["member_id", "member_name"])

    # name preference: display_name > full_name/name
    disp = _pick_col(df, ["display_name"])
    nm = _pick_col(df, ["full_name", "name"])

    out = pd.DataFrame()
    out["member_id"] = df[id_col].astype(str)

    disp_clean = (
        df[disp].astype(str).replace(["None", "nan", "NaN", "NULL", "null"], "").fillna("").str.strip()
        if disp and disp in df.columns
        else pd.Series([""] * len(df))
    )
    nm_clean = (
        df[nm].astype(str).replace(["None", "nan", "NaN", "NULL", "null"], "").fillna("").str.strip()
        if nm and nm in df.columns
        else pd.Series([""] * len(df))
    )

    out["member_name"] = disp_clean.where(disp_clean != "", nm_clean).replace("", "(no name)")

    # stable sort by numeric id when possible
    out["_id_num"] = pd.to_numeric(out["member_id"], errors="coerce")
    out = out.sort_values(["_id_num", "member_id"], ascending=True).drop(columns=["_id_num"])

    return out.reset_index(drop=True)


def _agg_contributions(contrib: pd.DataFrame) -> pd.DataFrame:
    if contrib is None or contrib.empty:
        return pd.DataFrame(columns=["member_id", "contrib_total", "contrib_count", "last_contrib_days"])

    mid = _pick_col(contrib, ["member_id"])
    amt = _pick_col(contrib, ["amount"])
    dt = _pick_col(contrib, ["paid_at", "created_at"])
    if not mid:
        return pd.DataFrame(columns=["member_id", "contrib_total", "contrib_count", "last_contrib_days"])

    tmp = contrib.copy()
    tmp["member_id"] = tmp[mid].astype(str)
    tmp["_amt"] = pd.to_numeric(tmp[amt], errors="coerce").fillna(0) if amt and amt in tmp.columns else 0.0

    if dt and dt in tmp.columns:
        tmp["_dt"] = pd.to_datetime(tmp[dt], errors="coerce", utc=True)
        last_dt = tmp.groupby("member_id")["_dt"].max()
        last_days = (pd.Timestamp.utcnow().tz_localize("UTC") - last_dt).dt.days
        last_days = last_days.replace([np.inf, -np.inf], np.nan)
    else:
        last_days = pd.Series(index=tmp["member_id"].unique(), dtype="float64")

    g = tmp.groupby("member_id", dropna=False)
    out = pd.DataFrame({
        "member_id": g.size().index.astype(str),
        "contrib_total": g["_amt"].sum().values,
        "contrib_count": g.size().values,
    })
    out = out.merge(last_days.rename("last_contrib_days").reset_index(), on="member_id", how="left")
    out["last_contrib_days"] = pd.to_numeric(out["last_contrib_days"], errors="coerce")
    return out


def _agg_foundation(fnd: pd.DataFrame) -> pd.DataFrame:
    if fnd is None or fnd.empty:
        return pd.DataFrame(columns=["member_id", "foundation_total", "foundation_count"])
    mid = _pick_col(fnd, ["member_id"])
    amt = _pick_col(fnd, ["amount"])
    if not mid:
        return pd.DataFrame(columns=["member_id", "foundation_total", "foundation_count"])
    tmp = fnd.copy()
    tmp["member_id"] = tmp[mid].astype(str)
    tmp["_amt"] = pd.to_numeric(tmp[amt], errors="coerce").fillna(0) if amt and amt in tmp.columns else 0.0
    g = tmp.groupby("member_id", dropna=False)
    return pd.DataFrame({
        "member_id": g.size().index.astype(str),
        "foundation_total": g["_amt"].sum().values,
        "foundation_count": g.size().values,
    })


def _agg_fines(fines: pd.DataFrame) -> pd.DataFrame:
    if fines is None or fines.empty:
        return pd.DataFrame(columns=["member_id", "fines_total", "fines_count"])
    mid = _pick_col(fines, ["member_id"])
    amt = _pick_col(fines, ["amount"])
    if not mid:
        return pd.DataFrame(columns=["member_id", "fines_total", "fines_count"])
    tmp = fines.copy()
    tmp["member_id"] = tmp[mid].astype(str)
    tmp["_amt"] = pd.to_numeric(tmp[amt], errors="coerce").fillna(0) if amt and amt in tmp.columns else 0.0
    g = tmp.groupby("member_id", dropna=False)
    return pd.DataFrame({
        "member_id": g.size().index.astype(str),
        "fines_total": g["_amt"].sum().values,
        "fines_count": g.size().values,
    })


def _agg_loans(loans: pd.DataFrame) -> pd.DataFrame:
    cols = ["member_id", "active_loan_count", "active_loan_exposure", "overdue_flag_count", "total_due_like"]
    if loans is None or loans.empty:
        return pd.DataFrame(columns=cols)

    mid = _pick_col(loans, ["member_id"])
    if not mid:
        return pd.DataFrame(columns=cols)

    status = _pick_col(loans, ["status"])
    principal = _pick_col(loans, ["principal_current", "principal", "amount"])
    total_due = _pick_col(loans, ["total_due", "arrears", "balance_due", "amount_due"])
    unpaid = _pick_col(loans, ["unpaid_interest", "interest_due", "interest_unpaid"])

    tmp = loans.copy()
    tmp["member_id"] = tmp[mid].astype(str)

    stt = tmp[status].astype(str).str.lower().str.strip() if status and status in tmp.columns else ""
    tmp["_is_active"] = stt.isin(ACTIVE_STATUSES)
    tmp["_is_overdue"] = stt.isin({"overdue", "late"})

    # choose exposure from best available
    if principal and principal in tmp.columns:
        tmp["_exposure"] = pd.to_numeric(tmp[principal], errors="coerce").fillna(0)
    elif total_due and total_due in tmp.columns:
        tmp["_exposure"] = pd.to_numeric(tmp[total_due], errors="coerce").fillna(0)
    else:
        tmp["_exposure"] = 0.0

    tmp["_total_due"] = pd.to_numeric(tmp[total_due], errors="coerce").fillna(0) if total_due and total_due in tmp.columns else 0.0
    tmp["_unpaid_int"] = pd.to_numeric(tmp[unpaid], errors="coerce").fillna(0) if unpaid and unpaid in tmp.columns else 0.0

    g = tmp.groupby("member_id", dropna=False)

    out = pd.DataFrame({
        "member_id": g.size().index.astype(str),
        "active_loan_count": g["_is_active"].sum().values.astype(int),
        "overdue_flag_count": g["_is_overdue"].sum().values.astype(int),
        "active_loan_exposure": g.apply(lambda d: float(d.loc[d["_is_active"], "_exposure"].sum())).values,
        "total_due_like": g["_total_due"].sum().values,
        "unpaid_interest_like": g["_unpaid_int"].sum().values,
    })
    return out


def _agg_payments(pay: pd.DataFrame) -> pd.DataFrame:
    cols = ["member_id", "payment_total", "payment_count", "last_payment_days"]
    if pay is None or pay.empty:
        return pd.DataFrame(columns=cols)

    mid = _pick_col(pay, ["member_id"])
    amt = _pick_col(pay, ["amount"])
    dt = _pick_col(pay, ["paid_at", "created_at"])
    if not mid:
        return pd.DataFrame(columns=cols)

    tmp = pay.copy()
    tmp["member_id"] = tmp[mid].astype(str)
    tmp["_amt"] = pd.to_numeric(tmp[amt], errors="coerce").fillna(0) if amt and amt in tmp.columns else 0.0

    if dt and dt in tmp.columns:
        tmp["_dt"] = pd.to_datetime(tmp[dt], errors="coerce", utc=True)
        last_dt = tmp.groupby("member_id")["_dt"].max()
        last_days = (pd.Timestamp.utcnow().tz_localize("UTC") - last_dt).dt.days
    else:
        last_days = pd.Series(index=tmp["member_id"].unique(), dtype="float64")

    g = tmp.groupby("member_id", dropna=False)
    out = pd.DataFrame({
        "member_id": g.size().index.astype(str),
        "payment_total": g["_amt"].sum().values,
        "payment_count": g.size().values,
    })
    out = out.merge(last_days.rename("last_payment_days").reset_index(), on="member_id", how="left")
    out["last_payment_days"] = pd.to_numeric(out["last_payment_days"], errors="coerce")
    return out


def _agg_payouts(payouts: pd.DataFrame) -> pd.DataFrame:
    cols = ["member_id", "payout_count", "last_payout_days"]
    if payouts is None or payouts.empty:
        return pd.DataFrame(columns=cols)
    mid = _pick_col(payouts, ["member_id"])
    dt = _pick_col(payouts, ["paid_at", "created_at"])
    if not mid:
        return pd.DataFrame(columns=cols)
    tmp = payouts.copy()
    tmp["member_id"] = tmp[mid].astype(str)
    if dt and dt in tmp.columns:
        tmp["_dt"] = pd.to_datetime(tmp[dt], errors="coerce", utc=True)
        last_dt = tmp.groupby("member_id")["_dt"].max()
        last_days = (pd.Timestamp.utcnow().tz_localize("UTC") - last_dt).dt.days
    else:
        last_days = pd.Series(index=tmp["member_id"].unique(), dtype="float64")

    g = tmp.groupby("member_id", dropna=False)
    out = pd.DataFrame({
        "member_id": g.size().index.astype(str),
        "payout_count": g.size().values.astype(int),
    })
    out = out.merge(last_days.rename("last_payout_days").reset_index(), on="member_id", how="left")
    out["last_payout_days"] = pd.to_numeric(out["last_payout_days"], errors="coerce")
    return out


def build_member_features(
    members: pd.DataFrame,
    contrib: pd.DataFrame,
    loans: pd.DataFrame,
    payments: pd.DataFrame,
    payouts: pd.DataFrame,
    fines: pd.DataFrame,
    foundation: pd.DataFrame,
) -> pd.DataFrame:
    """
    ✅ Always returns full member frame with safe merged aggregates.
    """
    base = _normalize_members(members)
    if base.empty:
        return pd.DataFrame()

    # aggregates
    a_contrib = _agg_contributions(contrib)
    a_loans = _agg_loans(loans)
    a_pay = _agg_payments(payments)
    a_payout = _agg_payouts(payouts)
    a_fines = _agg_fines(fines)
    a_found = _agg_foundation(foundation)

    # merge all onto base member_id
    X = base.copy()
    for agg in [a_contrib, a_found, a_fines, a_loans, a_pay, a_payout]:
        if agg is None or agg.empty:
            continue
        X = X.merge(agg, on="member_id", how="left")

    # fill
    num_cols = [c for c in X.columns if c not in ("member_id", "member_name")]
    for c in num_cols:
        X[c] = pd.to_numeric(X[c], errors="coerce")

    for c in num_cols:
        if c.endswith("_days"):
            # days can be NaN if never happened
            X[c] = X[c].fillna(np.nan)
        else:
            X[c] = X[c].fillna(0.0)

    # derived signals
    X["no_payment_14d"] = (X.get("last_payment_days") >= 14).fillna(True).astype(int) if "last_payment_days" in X else 1
    X["no_payment_30d"] = (X.get("last_payment_days") >= 30).fillna(True).astype(int) if "last_payment_days" in X else 1
    X["no_contrib_14d"] = (X.get("last_contrib_days") >= 14).fillna(True).astype(int) if "last_contrib_days" in X else 1
    X["no_contrib_30d"] = (X.get("last_contrib_days") >= 30).fillna(True).astype(int) if "last_contrib_days" in X else 1

    # simple fraud-ish / behavior flags (heuristic)
    X["bad_status_flag"] = (X.get("overdue_flag_count", 0) > 0).astype(int)
    X["outstanding_loan_flag"] = (X.get("active_loan_exposure", 0) > 0).astype(int)

    return X


# =============================================================================
# Heuristic Scoring (Manifold-style: smooth blend of signals)
# =============================================================================
def score_members_heuristic(
    X: pd.DataFrame,
    w: Dict[str, float],
) -> pd.DataFrame:
    """
    Returns X with:
      - risk_score (0..100)
      - reliability_score (0..100)
      - dropout_score (0..100)
      - risk_band
    """
    if X is None or X.empty:
        return pd.DataFrame()

    df = X.copy()

    # Normalize helpers
    def zpos(series: pd.Series) -> pd.Series:
        s = pd.to_numeric(series, errors="coerce").fillna(0.0).astype(float)
        if s.max() <= 0:
            return s * 0
        return (s / (s.max() + 1e-9)).clip(0, 1)

    # Core components
    outstanding = zpos(df.get("active_loan_exposure", 0))
    total_due = zpos(df.get("total_due_like", 0))
    bad_status = zpos(df.get("bad_status_flag", 0))
    no_pay_30 = zpos(df.get("no_payment_30d", 0))
    no_pay_14 = zpos(df.get("no_payment_14d", 0))
    no_contrib_30 = zpos(df.get("no_contrib_30d", 0))
    no_contrib_14 = zpos(df.get("no_contrib_14d", 0))
    fines = zpos(df.get("fines_total", 0))

    contrib_total = zpos(df.get("contrib_total", 0))
    contrib_freq = zpos(df.get("contrib_count", 0))

    # "Manifold blend": smooth weighted sum → sigmoid-like squashing
    raw = (
        w.get("w_outstanding", 0.50) * outstanding
        + w.get("w_total_due", 0.30) * total_due
        + w.get("w_bad_status", 0.30) * bad_status
        + w.get("w_no_pay_30", 0.50) * no_pay_30
        + w.get("w_no_pay_14", 0.50) * no_pay_14
        + w.get("w_no_contrib_30", 0.50) * no_contrib_30
        + w.get("w_no_contrib_14", 0.50) * no_contrib_14
        + w.get("w_fines_cap", 0.50) * fines
        - w.get("w_bonus_total_contrib", 0.20) * contrib_total
        - w.get("w_bonus_contrib_freq", 0.20) * contrib_freq
    )

    # squash to 0..100 (smooth)
    risk_score = (1 / (1 + np.exp(-4 * (raw - 0.5)))) * 100
    df["risk_score"] = np.clip(risk_score, 0, 100).round(1)

    # Reliability: inverse-risk blended with contribution strength
    reli_raw = (0.60 * (1 - (df["risk_score"] / 100.0)) + 0.25 * contrib_total + 0.15 * contrib_freq)
    df["reliability_score"] = np.clip(reli_raw * 100, 0, 100).round(1)

    # Dropout: mainly contribution inactivity + low frequency
    drop_raw = 0.55 * no_contrib_30 + 0.25 * no_contrib_14 + 0.20 * (1 - contrib_freq)
    df["dropout_score"] = np.clip(drop_raw * 100, 0, 100).round(1)

    def band(s: float) -> str:
        if s >= 80:
            return "Critical"
        if s >= 60:
            return "High"
        if s >= 40:
            return "Elevated"
        if s >= 20:
            return "Moderate"
        return "Low"

    df["risk_band"] = df["risk_score"].apply(lambda x: band(float(x)))

    return df


# =============================================================================
# Stress Test
# =============================================================================
def stress_test(df: pd.DataFrame, drop_pct: float, pay_delay_days: int, fines_increase_pct: float, w: Dict[str, float]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    X = df.copy()

    # apply what-if perturbations
    if "contrib_total" in X:
        X["contrib_total"] = X["contrib_total"] * (1 - (float(drop_pct) / 100.0))
    if "last_payment_days" in X:
        X["last_payment_days"] = (pd.to_numeric(X["last_payment_days"], errors="coerce") + float(pay_delay_days)).fillna(np.nan)
        X["no_payment_14d"] = (X["last_payment_days"] >= 14).fillna(True).astype(int)
        X["no_payment_30d"] = (X["last_payment_days"] >= 30).fillna(True).astype(int)
    if "fines_total" in X:
        X["fines_total"] = X["fines_total"] * (1 + (float(fines_increase_pct) / 100.0))

    return score_members_heuristic(X, w)


# =============================================================================
# Segmentation (KMeans if available)
# =============================================================================
def segment_members(df: pd.DataFrame, k: int) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    if KMeans is None or StandardScaler is None:
        out["segment"] = "segmentation:missing_sklearn"
        return out

    feats = []
    for c in ["risk_score", "reliability_score", "dropout_score", "contrib_total", "active_loan_exposure", "fines_total"]:
        if c in out.columns:
            feats.append(c)

    if len(feats) < 2:
        out["segment"] = "segmentation:insufficient_features"
        return out

    Z = out[feats].copy()
    for c in feats:
        Z[c] = pd.to_numeric(Z[c], errors="coerce").fillna(0.0)

    scaler = StandardScaler()
    Zs = scaler.fit_transform(Z.values)

    km = KMeans(n_clusters=int(k), n_init=10, random_state=42)
    labels = km.fit_predict(Zs)
    out["segment"] = labels.astype(int)
    return out


# =============================================================================
# Optional ML (XGBoost) — only if installed + enough loans
# =============================================================================
def train_xgb_if_possible(df: pd.DataFrame, min_loans: int) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Trains a simple classifier for "high risk" label using engineered features.
    This is OPTIONAL and never blocks the UI.
    """
    if df is None or df.empty:
        return None, "ml:empty"
    if xgb is None:
        return None, "ml:xgboost_missing"

    # We need a proxy label: high risk if overdue_flag_count>0 or unpaid_interest_like>0 or risk_score>=60
    if "active_loan_count" not in df:
        return None, "ml:missing_active_loan_count"

    total_loans = int(pd.to_numeric(df.get("active_loan_count", 0), errors="coerce").fillna(0).sum())
    if total_loans < int(min_loans):
        return None, f"ml:insufficient_loans({total_loans}<{min_loans})"

    y = (
        (pd.to_numeric(df.get("overdue_flag_count", 0), errors="coerce").fillna(0) > 0)
        | (pd.to_numeric(df.get("unpaid_interest_like", 0), errors="coerce").fillna(0) > 0)
        | (pd.to_numeric(df.get("risk_score", 0), errors="coerce").fillna(0) >= 60)
    ).astype(int)

    # features
    feat_cols = [c for c in [
        "contrib_total", "contrib_count",
        "foundation_total",
        "fines_total", "fines_count",
        "active_loan_exposure", "active_loan_count",
        "total_due_like", "unpaid_interest_like",
        "payment_total", "payment_count",
        "last_contrib_days", "last_payment_days",
    ] if c in df.columns]

    X = df[feat_cols].copy()
    for c in feat_cols:
        X[c] = pd.to_numeric(X[c], errors="coerce").fillna(0.0)

    # guard
    if y.nunique() < 2:
        return None, "ml:label_single_class"

    dtrain = xgb.DMatrix(X.values, label=y.values, feature_names=feat_cols)
    params = {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "max_depth": 3,
        "eta": 0.15,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "seed": 42,
    }
    booster = xgb.train(params, dtrain, num_boost_round=60)

    preds = booster.predict(dtrain)
    out = {
        "feat_cols": feat_cols,
        "train_rows": int(len(df)),
        "pos_rate": float(y.mean()),
        "avg_pred": float(np.mean(preds)),
        "top_features": sorted(
            [(f, float(w)) for f, w in zip(feat_cols, booster.get_score(importance_type="weight").values())],
            key=lambda x: x[1],
            reverse=True,
        )[:8],
    }
    return out, "ml:trained"


# =============================================================================
# UI
# =============================================================================
@st.cache_data(ttl=FEATURE_TTL, show_spinner=False)
def build_member_features_cached(
    url_sig: str,
    schema: str,
    members_json: str,
    contrib_json: str,
    loans_json: str,
    pay_json: str,
    payouts_json: str,
    fines_json: str,
    foundation_json: str,
) -> pd.DataFrame:
    # cache key uses serialized JSON to avoid supabase client caching issues
    members = pd.read_json(members_json)
    contrib = pd.read_json(contrib_json)
    loans = pd.read_json(loans_json)
    pay = pd.read_json(pay_json)
    payouts = pd.read_json(payouts_json)
    fines = pd.read_json(fines_json)
    foundation = pd.read_json(foundation_json)
    return build_member_features(members, contrib, loans, pay, payouts, fines, foundation)


def _safe_json(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return pd.DataFrame().to_json()
    # cap to avoid huge cache payload
    if len(df) > MAX_ROWS_SCAN:
        df = df.head(MAX_ROWS_SCAN).copy()
    return df.to_json()


def _render_header():
    st.markdown(f"### {APP_TITLE}")
    st.caption(APP_TAGLINE)


def _render_settings() -> Dict[str, Any]:
    st.markdown("#### 🧠 AI Suite Settings")

    c1, c2 = st.columns([0.55, 0.45], gap="large")
    with c1:
        min_loans = st.number_input("Min loans for ML (XGBoost)", min_value=0, value=25, step=5)

    st.markdown("#### Heuristic weights (optional)")

    # weights (match your UI vibe)
    w_outstanding = st.slider("Outstanding loan weight", 0.0, 1.0, 0.50, 0.05)
    w_total_due = st.slider("Total due/arrears weight", 0.0, 1.0, 0.30, 0.05)
    w_bad_status = st.slider("Bad status per flag", 0.0, 1.0, 0.30, 0.05)
    w_no_pay_30 = st.slider("No payment ≥30d", 0.0, 1.0, 0.50, 0.05)
    w_no_pay_14 = st.slider("No payment ≥14d", 0.0, 1.0, 0.50, 0.05)
    w_no_contrib_30 = st.slider("No contrib ≥30d", 0.0, 1.0, 0.50, 0.05)
    w_no_contrib_14 = st.slider("No contrib ≥14d", 0.0, 1.0, 0.50, 0.05)
    w_fines_cap = st.slider("Fines cap weight", 0.0, 1.0, 0.50, 0.05)
    w_bonus_total = st.slider("Bonus: strong total contrib", 0.0, 1.0, 0.20, 0.05)
    w_bonus_freq = st.slider("Bonus: contrib frequency", 0.0, 1.0, 0.20, 0.05)

    st.markdown("#### 🧪 Stress Test (What-if)")
    drop_pct = st.slider("Contribution drop (%)", 0, 80, 0, 5)
    pay_delay = st.slider("Payment delay (extra days)", 0, 90, 0, 5)
    fines_inc = st.slider("Fines increase (%)", 0, 200, 0, 10)

    st.markdown("#### 🧩 Segmentation")
    k = st.slider("Number of segments (K)", 2, 6, 3, 1)

    return {
        "min_loans": int(min_loans),
        "weights": {
            "w_outstanding": float(w_outstanding),
            "w_total_due": float(w_total_due),
            "w_bad_status": float(w_bad_status),
            "w_no_pay_30": float(w_no_pay_30),
            "w_no_pay_14": float(w_no_pay_14),
            "w_no_contrib_30": float(w_no_contrib_30),
            "w_no_contrib_14": float(w_no_contrib_14),
            "w_fines_cap": float(w_fines_cap),
            "w_bonus_total_contrib": float(w_bonus_total),
            "w_bonus_contrib_freq": float(w_bonus_freq),
        },
        "stress": {"drop_pct": float(drop_pct), "pay_delay": int(pay_delay), "fines_inc": float(fines_inc)},
        "segments": int(k),
    }


def _render_top_kpis(sb_anon, sb_service, schema: str):
    snap = _rpc_finance_snapshot(sb_anon, sb_service, schema)
    if not snap:
        st.caption("Snapshot: rpc missing → KPIs will be computed from tables below.")
        return

    # support nested or flat
    totals = snap.get("totals") if isinstance(snap.get("totals"), dict) else {}
    total_contrib = totals.get("total_contributions") if totals else snap.get("total_contributions")
    foundation_total = totals.get("foundation_total") if totals else snap.get("foundation_total")
    total_fines = totals.get("total_fines") if totals else snap.get("total_fines")
    active_exposure = totals.get("active_loan_exposure") if totals else snap.get("active_loan_exposure")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Contributions", _money0(_to_num(total_contrib)))
    c2.metric("Foundation Total", _money0(_to_num(foundation_total)))
    c3.metric("Total Fines", _money0(_to_num(total_fines)))
    c4.metric("Active Loan Exposure", _money0(_to_num(active_exposure)))

    st.caption(f"Snapshot source: fn_finance_snapshot • fetched_at={_utc_iso()}")


def _render_alerts(df: pd.DataFrame):
    st.markdown("#### 🚨 Alerts (DB-grounded)")
    if df is None or df.empty:
        st.info("No data.")
        return

    crit = df[df["risk_band"].isin(["Critical", "High"])].copy()
    if crit.empty:
        st.success("No Critical/High risk members flagged by current heuristic.")
        return

    crit = crit.sort_values(["risk_score", "dropout_score"], ascending=False).head(15)
    st.dataframe(
        crit[["member_id", "member_name", "risk_band", "risk_score", "reliability_score", "dropout_score",
              "active_loan_exposure", "overdue_flag_count", "fines_total", "last_contrib_days", "last_payment_days"]],
        use_container_width=True,
        hide_index=True,
    )


def _render_trends(contrib: pd.DataFrame, payments: pd.DataFrame):
    st.markdown("#### 📈 Trends (last 90 days, if timestamps available)")
    cdt = _pick_col(contrib, ["paid_at", "created_at"]) if contrib is not None else None
    pdt = _pick_col(payments, ["paid_at", "created_at"]) if payments is not None else None

    colA, colB = st.columns(2)
    with colA:
        st.caption("Contributions")
        if contrib is None or contrib.empty or not cdt or cdt not in contrib.columns:
            st.info("No contribution timestamps available.")
        else:
            tmp = contrib.copy()
            tmp["_dt"] = pd.to_datetime(tmp[cdt], errors="coerce", utc=True)
            tmp = tmp.dropna(subset=["_dt"])
            tmp = tmp[tmp["_dt"] >= (pd.Timestamp.utcnow().tz_localize("UTC") - pd.Timedelta(days=90))]
            if tmp.empty:
                st.info("No contributions in the last 90 days (or timestamps missing).")
            else:
                amt = _pick_col(tmp, ["amount"])
                tmp["_amt"] = pd.to_numeric(tmp[amt], errors="coerce").fillna(0) if amt else 0.0
                by = tmp.groupby(tmp["_dt"].dt.date)["_amt"].sum().reset_index(name="total_amount")
                by = by.sort_values("_dt")
                st.line_chart(by.set_index("_dt")["total_amount"])

    with colB:
        st.caption("Loan Payments")
        if payments is None or payments.empty or not pdt or pdt not in payments.columns:
            st.info("No payment timestamps available.")
        else:
            tmp = payments.copy()
            tmp["_dt"] = pd.to_datetime(tmp[pdt], errors="coerce", utc=True)
            tmp = tmp.dropna(subset=["_dt"])
            tmp = tmp[tmp["_dt"] >= (pd.Timestamp.utcnow().tz_localize("UTC") - pd.Timedelta(days=90))]
            if tmp.empty:
                st.info("No payments in the last 90 days (or timestamps missing).")
            else:
                amt = _pick_col(tmp, ["amount"])
                tmp["_amt"] = pd.to_numeric(tmp[amt], errors="coerce").fillna(0) if amt else 0.0
                by = tmp.groupby(tmp["_dt"].dt.date)["_amt"].sum().reset_index(name="total_amount")
                by = by.sort_values("_dt")
                st.line_chart(by.set_index("_dt")["total_amount"])


def _render_minutes_generator(df_scored: pd.DataFrame, schema: str):
    st.markdown("#### 📝 Minutes Generator (DB-grounded, no LLM)")
    if df_scored is None or df_scored.empty:
        st.info("No data available.")
        return

    top_risk = df_scored.sort_values("risk_score", ascending=False).head(5)
    top_drop = df_scored.sort_values("dropout_score", ascending=False).head(5)

    lines: List[str] = []
    lines.append(f"Meeting Minutes (Auto) — {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"Schema: {schema}")
    lines.append("")
    lines.append("1) Control Tower Summary")
    lines.append(f"- Generated at (UTC): {_utc_iso()}")
    lines.append(f"- Members analyzed: {len(df_scored)}")
    lines.append("")
    lines.append("2) Highest Risk Members (Top 5)")
    for r in top_risk.itertuples(index=False):
        lines.append(f"- {r.member_id} • {r.member_name} — Risk {r.risk_score} ({r.risk_band}), Exposure {r.active_loan_exposure:,.0f}")
    lines.append("")
    lines.append("3) Dropout Watchlist (Top 5)")
    for r in top_drop.itertuples(index=False):
        lines.append(f"- {r.member_id} • {r.member_name} — Dropout {r.dropout_score}, Last contrib days {r.last_contrib_days}")
    lines.append("")
    lines.append("4) Recommended Actions")
    lines.append("- Follow up on overdue/late borrowers; enforce interest settlement policy.")
    lines.append("- For no-contrib/no-payment flags, apply reminders, fines, or borrowing freeze per bylaws.")
    lines.append("- Re-check liquidity pressure before new lending approvals.")

    st.text_area("Generated minutes (copy/paste)", value="\n".join(lines), height=260)


# =============================================================================
# MAIN ENTRY
# =============================================================================
def render_full_ai_suite_panel(sb_anon, sb_service=None, schema: str = "public") -> None:
    _render_header()

    # settings UI (matches your screenshot)
    cfg = _render_settings()
    w = cfg["weights"]

    st.divider()
    _render_top_kpis(sb_anon, sb_service, schema)
    st.divider()

    # Load core tables (safe columns; broad select to survive schema variation)
    with st.spinner("Loading Njangi data…"):
        members = _sb_select(sb_anon, sb_service, schema, "members", cols="*", limit=20_000, order_by="id", desc=False)
        contrib = _sb_select(sb_anon, sb_service, schema, "contributions", cols="*", limit=MAX_ROWS_SCAN, order_by="created_at", desc=True)
        loans = _sb_select(sb_anon, sb_service, schema, "loans", cols="*", limit=MAX_ROWS_SCAN, order_by="created_at", desc=True)
        payments = _sb_select(sb_anon, sb_service, schema, "loan_payments", cols="*", limit=MAX_ROWS_SCAN, order_by="created_at", desc=True)
        payouts = _sb_select(sb_anon, sb_service, schema, "payouts", cols="*", limit=MAX_ROWS_SCAN, order_by="created_at", desc=True)
        fines = _sb_select(sb_anon, sb_service, schema, "fines", cols="*", limit=MAX_ROWS_SCAN, order_by="created_at", desc=True)
        foundation = _sb_select(sb_anon, sb_service, schema, "foundation_contributions", cols="*", limit=MAX_ROWS_SCAN, order_by="created_at", desc=True)

    # Build features (cached)
    url_sig = f"{schema}:{id(sb_service or sb_anon)}"
    X = build_member_features_cached(
        url_sig=url_sig,
        schema=schema,
        members_json=_safe_json(members),
        contrib_json=_safe_json(contrib),
        loans_json=_safe_json(loans),
        pay_json=_safe_json(payments),
        payouts_json=_safe_json(payouts),
        fines_json=_safe_json(fines),
        foundation_json=_safe_json(foundation),
    )

    if X is None or X.empty:
        st.error("AI Suite: could not build features (members table empty or unreadable).")
        return

    # Score
    df_scored = score_members_heuristic(X, w)

    # Segments
    df_scored = segment_members(df_scored, cfg["segments"])

    # Stress test
    st.markdown("#### 🧪 Stress Test Output")
    st.caption("Applies your what-if knobs and recomputes risk smoothly (manifold blend).")
    st_df = stress_test(
        df_scored,
        drop_pct=cfg["stress"]["drop_pct"],
        pay_delay_days=cfg["stress"]["pay_delay"],
        fines_increase_pct=cfg["stress"]["fines_inc"],
        w=w,
    )
    st_df = segment_members(st_df, cfg["segments"])

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Avg Risk (baseline)", f"{float(df_scored['risk_score'].mean()):.1f}")
    with c2:
        st.metric("Avg Risk (stress)", f"{float(st_df['risk_score'].mean()):.1f}")
    with c3:
        delta = float(st_df["risk_score"].mean() - df_scored["risk_score"].mean())
        st.metric("Δ Risk", f"{delta:+.1f}")

    st.divider()

    # Main tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["📌 Leaderboard", "🚨 Alerts", "📈 Trends", "🧩 Segments", "📝 Minutes Generator"]
    )

    with tab1:
        st.markdown("#### 📌 Member Leaderboard (DB-grounded)")
        show_cols = [
            "member_id", "member_name",
            "risk_band", "risk_score", "reliability_score", "dropout_score",
            "contrib_total", "contrib_count",
            "active_loan_exposure", "active_loan_count",
            "overdue_flag_count", "unpaid_interest_like",
            "fines_total", "last_contrib_days", "last_payment_days",
            "segment",
        ]
        show_cols = [c for c in show_cols if c in df_scored.columns]
        st.dataframe(
            df_scored.sort_values(["risk_score", "dropout_score"], ascending=False)[show_cols],
            use_container_width=True,
            hide_index=True,
        )

        st.caption(f"Built at (UTC): {_utc_iso()} • Members: {len(df_scored)}")

    with tab2:
        _render_alerts(df_scored)

    with tab3:
        _render_trends(contrib, payments)

    with tab4:
        st.markdown("#### 🧩 Segmentation")
        if "segment" not in df_scored.columns:
            st.info("Segmentation is unavailable (install scikit-learn).")
        else:
            seg_summary = (
                df_scored.groupby("segment", dropna=False)
                .agg(
                    members=("member_id", "count"),
                    avg_risk=("risk_score", "mean"),
                    avg_reliability=("reliability_score", "mean"),
                    avg_dropout=("dropout_score", "mean"),
                    total_contrib=("contrib_total", "sum"),
                    total_exposure=("active_loan_exposure", "sum"),
                )
                .reset_index()
            )
            seg_summary["avg_risk"] = seg_summary["avg_risk"].round(1)
            seg_summary["avg_reliability"] = seg_summary["avg_reliability"].round(1)
            seg_summary["avg_dropout"] = seg_summary["avg_dropout"].round(1)

            st.dataframe(seg_summary, use_container_width=True, hide_index=True)

            st.markdown("**Members in selected segment**")
            seg_pick = st.selectbox("Segment", sorted(df_scored["segment"].dropna().unique().tolist()), index=0)
            seg_members = df_scored[df_scored["segment"] == seg_pick].sort_values("risk_score", ascending=False).head(50)
            cols = [c for c in ["member_id", "member_name", "risk_band", "risk_score", "contrib_total", "active_loan_exposure", "fines_total"] if c in seg_members.columns]
            st.dataframe(seg_members[cols], use_container_width=True, hide_index=True)

    with tab5:
        _render_minutes_generator(df_scored, schema=schema)

    st.divider()

    # Optional ML
    st.markdown("#### 🤖 Optional ML (XGBoost)")
    st.caption("Only runs if xgboost is installed AND enough loans exist. Never blocks the suite.")
    ml_info, ml_status = train_xgb_if_possible(df_scored, min_loans=cfg["min_loans"])

    if ml_info is None:
        st.info(f"ML status: {ml_status}")
        if ml_status == "ml:xgboost_missing":
            st.caption("If you want ML: add `xgboost` to requirements.txt.")
    else:
        st.success(f"ML status: {ml_status}")
        st.json(ml_info)

    # Debug
    with st.expander("🔎 Debug (feature columns)", expanded=False):
        st.write("Feature columns:", list(df_scored.columns))
        st.write("Row counts:")
        st.json({
            "members": _safe_count(members),
            "contributions": _safe_count(contrib),
            "loans": _safe_count(loans),
            "loan_payments": _safe_count(payments),
            "payouts": _safe_count(payouts),
            "fines": _safe_count(fines),
            "foundation_contributions": _safe_count(foundation),
        })
        st.caption("This panel is index-safe: all aggregates are merged onto full members list (no length mismatch).")
