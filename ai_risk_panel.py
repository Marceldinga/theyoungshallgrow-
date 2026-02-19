# ai_risk_panel.py ✅ NJANGI STANDARD (ML + ANOMALY FALLBACK, NO legacy)
# ✅ Uses ONLY new tables:
#   - members
#   - contributions
#   - loans
#   - loan_payments (optional)
#   - foundation_contributions
#   - fines (optional)
#
# ✅ If labels allow: Supervised Logistic Regression risk probability
# ✅ If labels are single-class: IsolationForest anomaly risk (always works)
# ✅ Safe Supabase reads (won’t crash if order_by column missing)
# ✅ Streamlit cache FIXED (no unhashable Supabase clients in st.cache_data)
# ✅ Timezone FIX: all datetime math uses tz-aware UTC

from __future__ import annotations

import streamlit as st
import pandas as pd
import numpy as np

from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler, RobustScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import IsolationForest


# ============================================================
# Safe Supabase reads
# ============================================================
def _safe_select(
    client,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 2000,
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
            q = q.limit(limit)

        resp = q.execute()
        return resp.data or []
    except Exception as e:
        if not silent:
            st.error(f"Failed reading {schema}.{table}")
            st.code(str(e), language="text")
        return []


def _safe_select_autosort(
    client,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 2000,
    desc: bool = True,
):
    for c in ["created_at", "updated_at", "paid_at", "payout_date", "borrow_date", "start_date", "id"]:
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
def _to_int(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(-1).astype(int)


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def _utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _to_dt_utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", utc=True)


def _days_since(now_utc: pd.Timestamp, dt_series) -> pd.Series:
    dt_utc = pd.to_datetime(dt_series, errors="coerce", utc=True)
    return (now_utc - dt_utc).dt.days


# ============================================================
# Cache-safe loaders (NO unhashable clients in cache args)
# ============================================================
def _pick_client(sb_anon, sb_service):
    return sb_service if sb_service is not None else sb_anon


def _ensure_clients_in_state(sb_anon, sb_service):
    st.session_state["__sb_anon__"] = sb_anon
    st.session_state["__sb_service__"] = sb_service


def _get_client_from_state(use_service: bool = False):
    sb_anon = st.session_state.get("__sb_anon__")
    sb_service = st.session_state.get("__sb_service__")
    if use_service and sb_service is not None:
        return sb_service
    return sb_anon


@st.cache_data(ttl=60, show_spinner=False)
def _load_table_cached(
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 5000,
    use_service: bool = False,
) -> pd.DataFrame:
    client = _get_client_from_state(use_service=use_service)
    if client is None:
        return pd.DataFrame()

    rows = _safe_select_autosort(client, schema, table, cols=cols, limit=limit, desc=True)

    # If anon empty and service exists, retry automatically
    if not rows and not use_service:
        sb_service = st.session_state.get("__sb_service__")
        if sb_service is not None:
            rows = _safe_select_autosort(sb_service, schema, table, cols=cols, limit=limit, desc=True)

    return pd.DataFrame(rows)


def _load_table(sb_anon, sb_service, schema: str, table: str, cols: str = "*", limit: int = 5000) -> pd.DataFrame:
    _ensure_clients_in_state(sb_anon, sb_service)
    return _load_table_cached(schema=schema, table=table, cols=cols, limit=limit, use_service=False)


def _load_table_service(sb_anon, sb_service, schema: str, table: str, cols: str = "*", limit: int = 5000) -> pd.DataFrame:
    _ensure_clients_in_state(sb_anon, sb_service)
    return _load_table_cached(schema=schema, table=table, cols=cols, limit=limit, use_service=True)


# ============================================================
# Feature engineering per member
# ============================================================
def _build_member_features(
    members: pd.DataFrame,
    contrib: pd.DataFrame,
    loans: pd.DataFrame,
    payments: pd.DataFrame,
    fines: pd.DataFrame,
    foundation: pd.DataFrame,
) -> pd.DataFrame:
    members = members.copy()
    members["id"] = _to_int(members["id"])
    members = members[members["id"] > 0].copy()

    now = _utc_now()

    # contributions features
    cfeat = pd.DataFrame({"member_id": members["id"]})
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
            cfeat = cfeat.merge(c.groupby("member_id")["session_id"].nunique().rename("contrib_sessions_n"),
                                left_on="member_id", right_index=True, how="left")
    else:
        cfeat["contrib_total"] = np.nan
        cfeat["contrib_count"] = np.nan
        cfeat["contrib_avg"] = np.nan
        cfeat["contrib_last_dt"] = pd.NaT
        cfeat["contrib_sessions_n"] = np.nan

    cfeat["days_since_last_contrib"] = _days_since(now, cfeat["contrib_last_dt"])

    # loans features
    lfeat = pd.DataFrame({"member_id": members["id"]})
    if not loans.empty and "member_id" in loans.columns:
        l = loans.copy()
        l["member_id"] = _to_int(l["member_id"])
        for col in ["principal", "principal_current", "balance", "total_due", "unpaid_interest"]:
            if col in l.columns:
                l[col] = _to_num(l[col])
        l["status"] = l.get("status", "").astype(str).str.lower()

        grp = l.groupby("member_id", dropna=False)
        lfeat = lfeat.merge(grp.size().rename("loan_count"), left_on="member_id", right_index=True, how="left")
        if "balance" in l.columns:
            lfeat = lfeat.merge(grp["balance"].sum().rename("loan_balance_sum"), left_on="member_id", right_index=True, how="left")
        else:
            lfeat["loan_balance_sum"] = np.nan
        if "principal_current" in l.columns:
            lfeat = lfeat.merge(grp["principal_current"].sum().rename("loan_principal_current_sum"),
                                left_on="member_id", right_index=True, how="left")
        else:
            lfeat["loan_principal_current_sum"] = np.nan
        if "total_due" in l.columns:
            lfeat = lfeat.merge(grp["total_due"].sum().rename("loan_total_due_sum"), left_on="member_id", right_index=True, how="left")
        else:
            lfeat["loan_total_due_sum"] = np.nan

        lfeat = lfeat.merge(
            grp["status"].apply(lambda s: s.isin(["delinquent", "default", "overdue"]).sum()).rename("loan_bad_status_count"),
            left_on="member_id",
            right_index=True,
            how="left",
        )
    else:
        lfeat["loan_count"] = np.nan
        lfeat["loan_balance_sum"] = np.nan
        lfeat["loan_principal_current_sum"] = np.nan
        lfeat["loan_total_due_sum"] = np.nan
        lfeat["loan_bad_status_count"] = np.nan

    # payments features
    pfeat = pd.DataFrame({"member_id": members["id"]})
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
        pfeat["pay_count"] = np.nan
        pfeat["pay_total"] = np.nan
        pfeat["pay_last_dt"] = pd.NaT

    pfeat["days_since_last_payment"] = _days_since(now, pfeat["pay_last_dt"])

    # fines features
    ffeat = pd.DataFrame({"member_id": members["id"]})
    if not fines.empty and "member_id" in fines.columns:
        f = fines.copy()
        f["member_id"] = _to_int(f["member_id"])
        f["amount"] = _to_num(f.get("amount", 0))
        grp = f.groupby("member_id", dropna=False)
        ffeat = ffeat.merge(grp["amount"].sum().rename("fine_total"), left_on="member_id", right_index=True, how="left")
        ffeat = ffeat.merge(grp["amount"].count().rename("fine_count"), left_on="member_id", right_index=True, how="left")
    else:
        ffeat["fine_total"] = np.nan
        ffeat["fine_count"] = np.nan

    # foundation features
    fdfeat = pd.DataFrame({"member_id": members["id"]})
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
        fdfeat["foundation_total"] = np.nan
        fdfeat["foundation_count"] = np.nan
        fdfeat["foundation_last_dt"] = pd.NaT

    fdfeat["days_since_last_foundation"] = _days_since(now, fdfeat["foundation_last_dt"])

    X = (
        cfeat.merge(lfeat, on="member_id", how="left")
        .merge(pfeat, on="member_id", how="left")
        .merge(ffeat, on="member_id", how="left")
        .merge(fdfeat, on="member_id", how="left")
    )

    for dtcol in ["contrib_last_dt", "pay_last_dt", "foundation_last_dt"]:
        if dtcol in X.columns:
            X.drop(columns=[dtcol], inplace=True)

    return X


# ============================================================
# Labels (status + fallback behavior)
# ============================================================
def _make_label_from_loans(loans: pd.DataFrame, members: pd.DataFrame, X: pd.DataFrame | None = None) -> pd.Series:
    members_ids = _to_int(members["id"])
    y = pd.Series(0, index=members_ids, dtype=int)

    if not loans.empty and "member_id" in loans.columns:
        l = loans.copy()
        l["member_id"] = _to_int(l["member_id"])
        l["status_raw"] = l.get("status", "").astype(str).str.strip().str.lower()

        def _norm_status(s: str) -> str:
            if s in ("", "none", "nan"):
                return "unknown"
            bad_tokens = ["overdue", "past due", "past_due", "delinquent", "default", "late", "arrears", "unpaid"]
            if any(tok in s for tok in bad_tokens):
                return "bad"
            good_tokens = ["paid", "cleared", "closed", "settled", "complete", "completed", "repaid"]
            if any(tok in s for tok in good_tokens):
                return "good"
            active_tokens = ["active", "open", "running", "current", "approved", "disbursed"]
            if any(tok in s for tok in active_tokens):
                return "active"
            return "unknown"

        l["status_norm"] = l["status_raw"].apply(_norm_status)
        bad_members = l.loc[l["status_norm"] == "bad", "member_id"].unique()
        y.loc[y.index.isin(bad_members)] = 1

    # fallback if single-class
    if len(pd.unique(y)) < 2 and X is not None and not X.empty:
        tmp = X.set_index("member_id", drop=False)
        bal = pd.to_numeric(tmp.get("loan_balance_sum", 0), errors="coerce").fillna(0)
        dsp = pd.to_numeric(tmp.get("days_since_last_payment", 0), errors="coerce").fillna(0)
        dsc = pd.to_numeric(tmp.get("days_since_last_contrib", 0), errors="coerce").fillna(0)
        # Slightly more sensitive so you get 2 classes with small data
        fallback_flag = ((bal > 0) & (dsp >= 14)) | (dsc >= 30)
        y.loc[y.index.isin(tmp.index)] = fallback_flag.astype(int)

    return y.reset_index(drop=True)


# ============================================================
# Models
# ============================================================
@st.cache_resource(show_spinner=False)
def _train_supervised(X: pd.DataFrame, y: pd.Series):
    feature_cols = [c for c in X.columns if c != "member_id"]
    Xmat = X[feature_cols]

    model = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=400, class_weight="balanced")),
        ]
    )

    if len(pd.unique(y)) < 2:
        return None, feature_cols, None

    min_class = int(y.value_counts().min())
    n_splits = min(5, max(2, min_class))
    if n_splits < 2:
        return None, feature_cols, None

    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    scores = cross_val_score(model, Xmat, y, cv=cv, scoring="roc_auc")
    model.fit(Xmat, y)
    return model, feature_cols, float(np.mean(scores))


@st.cache_resource(show_spinner=False)
def _train_anomaly(X: pd.DataFrame):
    feature_cols = [c for c in X.columns if c != "member_id"]
    Xmat = X[feature_cols]

    # Impute + robust scaling helps with heavy-tailed amounts
    prep = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", RobustScaler(with_centering=True, with_scaling=True)),
        ]
    )
    Xp = prep.fit_transform(Xmat)

    # contamination: small dataset -> keep conservative, but not too tiny
    n = max(1, X.shape[0])
    contamination = float(np.clip(2.0 / n, 0.05, 0.25))  # between 5% and 25%

    iso = IsolationForest(
        n_estimators=300,
        contamination=contamination,
        random_state=42,
    )
    iso.fit(Xp)

    return (prep, iso, feature_cols, contamination)


def _anomaly_to_risk_percent(prep, iso, X_row: pd.DataFrame, feature_cols: list[str]) -> float:
    """
    IsolationForest:
      decision_function higher = more normal
      We'll convert to "risk" by ranking within dataset later.
    Here we compute a raw anomaly score and squash to 0..1.
    """
    Xp = prep.transform(X_row[feature_cols])
    # score_samples: higher = more normal; lower = more abnormal
    s = float(iso.score_samples(Xp)[0])
    return s


# ============================================================
# Main
# ============================================================
def render_ai_risk_panel(sb_anon, sb_service=None, schema: str = "public"):
    st.header("🤖 AI Risk Panel (Machine Learning)")
    st.caption("ML risk probability using NJANGI STANDARD tables only (no legacy).")

    _ensure_clients_in_state(sb_anon, sb_service)

    if not _table_exists(sb_anon, schema, "members"):
        st.error("Missing table: members")
        return

    members = _load_table(sb_anon, sb_service, schema, "members", cols="id,name", limit=1000)
    if members.empty or "id" not in members.columns:
        st.error("members not readable.")
        return

    contrib = (
        _load_table(sb_anon, sb_service, schema, "contributions", cols="member_id,session_id,amount,created_at", limit=10000)
        if _table_exists(sb_anon, schema, "contributions")
        else pd.DataFrame()
    )

    client_for_secure = _pick_client(sb_anon, sb_service)

    loans = (
        _load_table_service(
            sb_anon,
            sb_service,
            schema,
            "loans",
            cols="member_id,status,principal,principal_current,balance,total_due,unpaid_interest,created_at",
            limit=10000,
        )
        if _table_exists(client_for_secure, schema, "loans")
        else pd.DataFrame()
    )

    payments = (
        _load_table_service(
            sb_anon,
            sb_service,
            schema,
            "loan_payments",
            cols="member_id,amount,paid_at,created_at",
            limit=20000,
        )
        if _table_exists(client_for_secure, schema, "loan_payments")
        else pd.DataFrame()
    )

    fines = (
        _load_table_service(
            sb_anon,
            sb_service,
            schema,
            "fines",
            cols="member_id,amount,created_at",
            limit=10000,
        )
        if _table_exists(client_for_secure, schema, "fines")
        else pd.DataFrame()
    )

    foundation = (
        _load_table(sb_anon, sb_service, schema, "foundation_contributions", cols="member_id,amount,created_at", limit=20000)
        if _table_exists(sb_anon, schema, "foundation_contributions")
        else pd.DataFrame()
    )

    # Optional debug
    with st.expander("🔎 Debug: Loan status distribution", expanded=False):
        if not loans.empty and "status" in loans.columns:
            st.dataframe(
                loans["status"]
                .astype(str)
                .str.lower()
                .value_counts()
                .reset_index()
                .rename(columns={"index": "status", "status": "count"}),
                use_container_width=True,
            )
        else:
            st.caption("No loans.status column or loans table empty.")

    # Build features + label
    X = _build_member_features(members, contrib, loans, payments, fines, foundation)
    y = _make_label_from_loans(loans, members, X=X)

    # member picker
    members = members.copy()
    members["id"] = _to_int(members["id"])
    members["name"] = members.get("name", "").astype(str)
    members = members[members["id"] > 0].copy()
    members["label"] = members.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)

    if members.empty:
        st.warning("No members found.")
        return

    pick = st.selectbox("Select member", members["label"].tolist())
    mid = int(members.loc[members["label"] == pick, "id"].iloc[0])

    row = X[X["member_id"] == mid].copy()
    if row.empty:
        st.warning("No feature row for selected member.")
        return

    st.subheader("Risk prediction")

    # Try supervised first
    model, feature_cols, auc = _train_supervised(X, y)

    if model is not None:
        proba = float(model.predict_proba(row[feature_cols])[0, 1])
        st.metric("Predicted Risk (Supervised)", f"{proba * 100:.1f}%")
        st.progress(float(np.clip(proba, 0.0, 1.0)))
        st.caption(f"Mode: Supervised Logistic Regression • CV ROC-AUC (rough): {auc:.3f}" if auc is not None else "Mode: Supervised Logistic Regression")

        # coefficient drivers
        try:
            clf = model.named_steps["clf"]
            coefs = pd.Series(clf.coef_[0], index=feature_cols).sort_values(key=np.abs, ascending=False)
            st.write("Top drivers (coefficients):")
            st.dataframe(coefs.head(12).to_frame("coef"), use_container_width=True)
        except Exception:
            pass

    else:
        # Anomaly fallback (ALWAYS works)
        prep, iso, feature_cols, contamination = _train_anomaly(X)

        # Compute anomaly scores for all members to turn into percentile risk
        Xmat_all = X[feature_cols].copy()
        Xp_all = prep.transform(Xmat_all)
        scores_all = iso.score_samples(Xp_all)  # higher = more normal
        scores_all = pd.Series(scores_all, index=X["member_id"].values)

        # Convert to risk = percentile of abnormality
        # lower score => higher risk
        rank = scores_all.rank(method="average", ascending=True)  # 1 = most abnormal
        risk_pct = (rank / rank.max()).clip(0, 1)

        this_risk = float(risk_pct.loc[mid]) if mid in risk_pct.index else float(risk_pct.mean())

        st.metric("Predicted Risk (Anomaly)", f"{this_risk * 100:.1f}%")
        st.progress(float(np.clip(this_risk, 0.0, 1.0)))
        st.caption(f"Mode: IsolationForest anomaly detection • expected high-risk share ≈ {contamination*100:.0f}%")

        with st.expander("Why anomaly mode?", expanded=False):
            st.write(
                "Your loan labels are single-class (all good or all bad), so supervised ML can't train. "
                "Anomaly detection ranks members by unusual patterns in contributions/loans/payments/fines."
            )

        # Show top anomalies
        top = (
            pd.DataFrame({"member_id": scores_all.index, "anomaly_score": scores_all.values, "risk": risk_pct.values})
            .sort_values("risk", ascending=False)
            .head(10)
        )
        st.write("Top risk (anomaly ranking):")
        st.dataframe(top, use_container_width=True)

    st.divider()
    st.subheader("Member feature snapshot")
    snap = row[["member_id"] + feature_cols].T
    snap.columns = ["value"]
    st.dataframe(snap, use_container_width=True)
