# ai_risk_panel.py ✅ NJANGI STANDARD (ML VERSION, NO legacy)
# ✅ Uses ONLY new tables:
#   - members
#   - contributions
#   - loans
#   - loan_payments (optional)
#   - foundation_contributions
#   - fines (optional)
#
# ✅ Machine Learning risk probability (Logistic Regression)
# ✅ Safe Supabase reads (won’t crash if order_by column missing)
# ✅ Streamlit cache FIXED (no unhashable Supabase clients in st.cache_data)

from __future__ import annotations

import streamlit as st
import pandas as pd
import numpy as np

from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression


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
                # retry without ordering
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
    # try typical timestamp / id columns
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


def _to_dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


# ============================================================
# Cache-safe loaders (NO unhashable clients in cache args)
# ============================================================
def _pick_client(sb_anon, sb_service):
    return sb_service if sb_service is not None else sb_anon


def _ensure_clients_in_state(sb_anon, sb_service):
    # store clients in session_state so cached functions don't receive them as args
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
    """
    Cached read keyed ONLY by hashable params.
    Supabase clients are read from st.session_state to avoid UnhashableParamError.
    """
    client = _get_client_from_state(use_service=use_service)
    if client is None:
        return pd.DataFrame()

    rows = _safe_select_autosort(client, schema, table, cols=cols, limit=limit, desc=True)

    # If anon returned empty and service exists, optionally retry with service
    if not rows and not use_service:
        sb_service = st.session_state.get("__sb_service__")
        if sb_service is not None:
            rows = _safe_select_autosort(sb_service, schema, table, cols=cols, limit=limit, desc=True)

    return pd.DataFrame(rows)


def _load_table(sb_anon, sb_service, schema: str, table: str, cols: str = "*", limit: int = 5000) -> pd.DataFrame:
    """
    Wrapper keeping your original call style but cache-safe.
    """
    _ensure_clients_in_state(sb_anon, sb_service)
    return _load_table_cached(schema=schema, table=table, cols=cols, limit=limit, use_service=False)


def _load_table_service(sb_anon, sb_service, schema: str, table: str, cols: str = "*", limit: int = 5000) -> pd.DataFrame:
    """
    Forces service client inside cached loader when available.
    """
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
    # normalize member ids
    members = members.copy()
    members["id"] = _to_int(members["id"])
    members = members[members["id"] > 0].copy()

    now = pd.Timestamp.utcnow()

    # ----------------------------
    # contributions features
    # ----------------------------
    cfeat = pd.DataFrame({"member_id": members["id"]})
    if not contrib.empty and "member_id" in contrib.columns:
        c = contrib.copy()
        c["member_id"] = _to_int(c["member_id"])
        c["amount"] = _to_num(c.get("amount", 0))
        c["created_at"] = _to_dt(c.get("created_at", pd.NaT))

        grp = c.groupby("member_id", dropna=False)
        c_sum = grp["amount"].sum()
        c_cnt = grp["amount"].count()
        c_avg = grp["amount"].mean()
        c_last = grp["created_at"].max()

        cfeat = cfeat.merge(c_sum.rename("contrib_total"), left_on="member_id", right_index=True, how="left")
        cfeat = cfeat.merge(c_cnt.rename("contrib_count"), left_on="member_id", right_index=True, how="left")
        cfeat = cfeat.merge(c_avg.rename("contrib_avg"), left_on="member_id", right_index=True, how="left")
        cfeat = cfeat.merge(c_last.rename("contrib_last_dt"), left_on="member_id", right_index=True, how="left")

        if "session_id" in c.columns:
            c["session_id"] = _to_int(c["session_id"])
            sess_n = c.groupby("member_id")["session_id"].nunique()
            cfeat = cfeat.merge(sess_n.rename("contrib_sessions_n"), left_on="member_id", right_index=True, how="left")
    else:
        cfeat["contrib_total"] = np.nan
        cfeat["contrib_count"] = np.nan
        cfeat["contrib_avg"] = np.nan
        cfeat["contrib_last_dt"] = pd.NaT
        cfeat["contrib_sessions_n"] = np.nan

    cfeat["days_since_last_contrib"] = (now - pd.to_datetime(cfeat["contrib_last_dt"], errors="coerce")).dt.days

    # ----------------------------
    # loans features
    # ----------------------------
    lfeat = pd.DataFrame({"member_id": members["id"]})
    if not loans.empty and "member_id" in loans.columns:
        l = loans.copy()
        l["member_id"] = _to_int(l["member_id"])
        for col in ["principal", "principal_current", "balance", "total_due", "unpaid_interest"]:
            if col in l.columns:
                l[col] = _to_num(l[col])

        l["status"] = l.get("status", "").astype(str).str.lower()

        grp = l.groupby("member_id", dropna=False)
        l_cnt = grp.size()
        bal = grp["balance"].sum() if "balance" in l.columns else pd.Series(dtype=float)
        pc = grp["principal_current"].sum() if "principal_current" in l.columns else pd.Series(dtype=float)
        td = grp["total_due"].sum() if "total_due" in l.columns else pd.Series(dtype=float)
        bad = grp["status"].apply(lambda s: s.isin(["delinquent", "default", "overdue"]).sum())

        lfeat = lfeat.merge(l_cnt.rename("loan_count"), left_on="member_id", right_index=True, how="left")
        if not bal.empty:
            lfeat = lfeat.merge(bal.rename("loan_balance_sum"), left_on="member_id", right_index=True, how="left")
        if not pc.empty:
            lfeat = lfeat.merge(pc.rename("loan_principal_current_sum"), left_on="member_id", right_index=True, how="left")
        if not td.empty:
            lfeat = lfeat.merge(td.rename("loan_total_due_sum"), left_on="member_id", right_index=True, how="left")
        lfeat = lfeat.merge(bad.rename("loan_bad_status_count"), left_on="member_id", right_index=True, how="left")
    else:
        lfeat["loan_count"] = np.nan
        lfeat["loan_balance_sum"] = np.nan
        lfeat["loan_principal_current_sum"] = np.nan
        lfeat["loan_total_due_sum"] = np.nan
        lfeat["loan_bad_status_count"] = np.nan

    # ----------------------------
    # payments features
    # ----------------------------
    pfeat = pd.DataFrame({"member_id": members["id"]})
    if not payments.empty and "member_id" in payments.columns:
        p = payments.copy()
        p["member_id"] = _to_int(p["member_id"])
        p["amount"] = _to_num(p.get("amount", 0))
        p["paid_at"] = _to_dt(p.get("paid_at", pd.NaT))
        grp = p.groupby("member_id", dropna=False)
        p_cnt = grp["amount"].count()
        p_sum = grp["amount"].sum()
        p_last = grp["paid_at"].max()

        pfeat = pfeat.merge(p_cnt.rename("pay_count"), left_on="member_id", right_index=True, how="left")
        pfeat = pfeat.merge(p_sum.rename("pay_total"), left_on="member_id", right_index=True, how="left")
        pfeat = pfeat.merge(p_last.rename("pay_last_dt"), left_on="member_id", right_index=True, how="left")
    else:
        pfeat["pay_count"] = np.nan
        pfeat["pay_total"] = np.nan
        pfeat["pay_last_dt"] = pd.NaT

    pfeat["days_since_last_payment"] = (now - pd.to_datetime(pfeat["pay_last_dt"], errors="coerce")).dt.days

    # ----------------------------
    # fines features
    # ----------------------------
    ffeat = pd.DataFrame({"member_id": members["id"]})
    if not fines.empty and "member_id" in fines.columns:
        f = fines.copy()
        f["member_id"] = _to_int(f["member_id"])
        f["amount"] = _to_num(f.get("amount", 0))
        grp = f.groupby("member_id", dropna=False)
        f_sum = grp["amount"].sum()
        f_cnt = grp["amount"].count()
        ffeat = ffeat.merge(f_sum.rename("fine_total"), left_on="member_id", right_index=True, how="left")
        ffeat = ffeat.merge(f_cnt.rename("fine_count"), left_on="member_id", right_index=True, how="left")
    else:
        ffeat["fine_total"] = np.nan
        ffeat["fine_count"] = np.nan

    # ----------------------------
    # foundation features
    # ----------------------------
    fdfeat = pd.DataFrame({"member_id": members["id"]})
    if not foundation.empty and "member_id" in foundation.columns:
        fd = foundation.copy()
        fd["member_id"] = _to_int(fd["member_id"])
        fd["amount"] = _to_num(fd.get("amount", 0))
        fd["created_at"] = _to_dt(fd.get("created_at", pd.NaT))
        grp = fd.groupby("member_id", dropna=False)
        fd_sum = grp["amount"].sum()
        fd_cnt = grp["amount"].count()
        fd_last = grp["created_at"].max()

        fdfeat = fdfeat.merge(fd_sum.rename("foundation_total"), left_on="member_id", right_index=True, how="left")
        fdfeat = fdfeat.merge(fd_cnt.rename("foundation_count"), left_on="member_id", right_index=True, how="left")
        fdfeat = fdfeat.merge(fd_last.rename("foundation_last_dt"), left_on="member_id", right_index=True, how="left")
    else:
        fdfeat["foundation_total"] = np.nan
        fdfeat["foundation_count"] = np.nan
        fdfeat["foundation_last_dt"] = pd.NaT

    fdfeat["days_since_last_foundation"] = (now - pd.to_datetime(fdfeat["foundation_last_dt"], errors="coerce")).dt.days

    # merge all
    X = (
        cfeat.merge(lfeat, on="member_id", how="left")
        .merge(pfeat, on="member_id", how="left")
        .merge(ffeat, on="member_id", how="left")
        .merge(fdfeat, on="member_id", how="left")
    )

    # drop raw datetime columns from model input
    for dtcol in ["contrib_last_dt", "pay_last_dt", "foundation_last_dt"]:
        if dtcol in X.columns:
            X.drop(columns=[dtcol], inplace=True)

    return X


def _make_label_from_loans(loans: pd.DataFrame, members: pd.DataFrame) -> pd.Series:
    members_ids = _to_int(members["id"])
    y = pd.Series(0, index=members_ids, dtype=int)

    if loans.empty or "member_id" not in loans.columns:
        return y.reset_index(drop=True)

    l = loans.copy()
    l["member_id"] = _to_int(l["member_id"])
    l["status"] = l.get("status", "").astype(str).str.lower()

    bad_members = l.loc[l["status"].isin(["delinquent", "default", "overdue"]), "member_id"].unique()
    y.loc[y.index.isin(bad_members)] = 1
    return y.reset_index(drop=True)


# ============================================================
# Model training (cached safely)
# ============================================================
@st.cache_resource(show_spinner=False)
def _train_model_cached(X: pd.DataFrame, y: pd.Series):
    feature_cols = [c for c in X.columns if c != "member_id"]
    Xmat = X[feature_cols]

    model = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=400, class_weight="balanced")),
        ]
    )

    # If only one class exists, can't train supervised
    if len(pd.unique(y)) < 2:
        return None, feature_cols, None

    # Guard for tiny class counts
    min_class = int(y.value_counts().min())
    n_splits = min(5, max(2, min_class))
    if n_splits < 2:
        return None, feature_cols, None

    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    scores = cross_val_score(model, Xmat, y, cv=cv, scoring="roc_auc")

    model.fit(Xmat, y)
    return model, feature_cols, float(np.mean(scores))


def _train_model(X: pd.DataFrame, y: pd.Series):
    # wrapper to keep future flexibility
    return _train_model_cached(X, y)


# ============================================================
# Main
# ============================================================
def render_ai_risk_panel(sb_anon, sb_service=None, schema: str = "public"):
    st.header("🤖 AI Risk Panel (Machine Learning)")
    st.caption("ML risk probability using NJANGI STANDARD tables only (no legacy).")

    # Ensure clients are available for cached reads
    _ensure_clients_in_state(sb_anon, sb_service)

    # Minimal required table
    if not _table_exists(sb_anon, schema, "members"):
        st.error("Missing table: members")
        return

    # Load data (cached, hash-safe)
    members = _load_table(sb_anon, sb_service, schema, "members", cols="id,name", limit=1000)
    if members.empty or "id" not in members.columns:
        st.error("members not readable.")
        return

    # Optional tables (use anon existence check; if RLS blocks anon, we will still try service reads when loading)
    contrib = (
        _load_table(sb_anon, sb_service, schema, "contributions", cols="member_id,session_id,amount,created_at", limit=10000)
        if _table_exists(sb_anon, schema, "contributions")
        else pd.DataFrame()
    )

    # For tables often protected by RLS, prefer service if available
    client_for_secure = _pick_client(sb_anon, sb_service)

    loans = (
        _load_table_service(sb_anon, sb_service, schema, "loans",
                            cols="member_id,status,principal,principal_current,balance,total_due,unpaid_interest,created_at",
                            limit=10000)
        if _table_exists(client_for_secure, schema, "loans")
        else pd.DataFrame()
    )

    payments = (
        _load_table_service(sb_anon, sb_service, schema, "loan_payments",
                            cols="member_id,amount,paid_at,created_at",
                            limit=20000)
        if _table_exists(client_for_secure, schema, "loan_payments")
        else pd.DataFrame()
    )

    fines = (
        _load_table_service(sb_anon, sb_service, schema, "fines",
                            cols="member_id,amount,created_at",
                            limit=10000)
        if _table_exists(client_for_secure, schema, "fines")
        else pd.DataFrame()
    )

    foundation = (
        _load_table(sb_anon, sb_service, schema, "foundation_contributions", cols="member_id,amount,created_at", limit=20000)
        if _table_exists(sb_anon, schema, "foundation_contributions")
        else pd.DataFrame()
    )

    # Build features + label
    X = _build_member_features(members, contrib, loans, payments, fines, foundation)
    y = _make_label_from_loans(loans, members)

    model, feature_cols, auc = _train_model(X, y)

    # UI select member
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
    if model is None:
        st.warning("Not enough labeled data to train supervised ML (only one class found or too few examples).")
        st.caption("Fix: Ensure loans.status has both good and bad examples (e.g., 'overdue' vs 'active/paid').")
    else:
        proba = float(model.predict_proba(row[feature_cols])[0, 1])
        st.metric("Predicted Risk (probability)", f"{proba * 100:.1f}%")
        st.progress(float(np.clip(proba, 0.0, 1.0)))

        if auc is not None:
            st.caption(f"Model CV ROC-AUC (rough): {auc:.3f} (small-data estimate)")

        # explain drivers (simple coefficient view)
        try:
            clf = model.named_steps["clf"]
            coefs = pd.Series(clf.coef_[0], index=feature_cols).sort_values(key=np.abs, ascending=False)
            st.write("Top drivers (coefficients):")
            st.dataframe(coefs.head(12).to_frame("coef"), use_container_width=True)
        except Exception:
            pass

    st.divider()
    st.subheader("Member feature snapshot")
    snap = row[["member_id"] + feature_cols].T
    snap.columns = ["value"]
    st.dataframe(snap, use_container_width=True)
