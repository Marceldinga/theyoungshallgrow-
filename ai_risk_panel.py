
# ai_risk_panel.py ✅ COMPLETE SINGLE CODE — NJANGI STANDARD (NO legacy) — NO sklearn
# ------------------------------------------------------------------------------
# ✅ Uses ONLY new tables:
#   - members
#   - contributions
#   - loans
#   - loan_payments (optional)
#   - foundation_contributions
#   - fines (optional)
#
# ✅ NO sklearn dependency (fixes ModuleNotFoundError)
# ✅ Schema-safe:
#    - loans has NO "balance" column in your DB -> uses principal_current as balance
#    - loan_payments may have member_id OR only loan_id -> auto-joins via loans to get member_id
# ✅ Always produces numbers (no blank NaNs in snapshot)
# ✅ Cache-safe: no unhashable supabase clients in cache args
# ✅ UTC-safe date math
# ------------------------------------------------------------------------------

from __future__ import annotations

import streamlit as st
import pandas as pd
import numpy as np


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
    for c in ["updated_at", "created_at", "paid_at", "last_paid_at", "borrow_date", "id"]:
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
    return (now_utc - dt_utc).dt.days


def _fill_feature_defaults(X: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure snapshot never shows blanks.
    - counts -> 0
    - sums/amounts -> 0.0
    - days_since -> large number if missing? (we use 999)
    """
    X = X.copy()
    for col in X.columns:
        if col == "member_id":
            continue
        if col.endswith("_count") or col.endswith("_n"):
            X[col] = pd.to_numeric(X[col], errors="coerce").fillna(0).astype(int)
        elif col.startswith("days_since_"):
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


@st.cache_data(ttl=60, show_spinner=False)
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
# Payment loader that supports either:
#   loan_payments(member_id, amount, paid_at)
#   OR loan_payments(loan_id, amount, paid_at)  -> join loans to get member_id
# ============================================================
def _load_payments_schema_safe(sb_anon, sb_service, schema: str, limit: int = 20000) -> pd.DataFrame:
    if sb_service is None:
        # anon might still read it; try anon first
        pay = _load_table(sb_anon, sb_service, schema, "loan_payments", cols="*", limit=limit)
    else:
        pay = _load_table_service(sb_anon, sb_service, schema, "loan_payments", cols="*", limit=limit)

    if pay.empty:
        return pay

    cols = set(pay.columns)
    if "member_id" in cols:
        # Great — direct member payments
        keep = [c for c in ["member_id", "amount", "paid_at", "created_at", "loan_id"] if c in cols]
        return pay[keep].copy()

    # No member_id: try loan_id join
    if "loan_id" not in cols and "loans_id" in cols:
        pay = pay.rename(columns={"loans_id": "loan_id"})

    if "loan_id" not in pay.columns:
        # Can't map
        return pd.DataFrame()

    # Load loans map (id -> member_id)
    loans_map = _load_table_service(
        sb_anon,
        sb_service,
        schema,
        "loans",
        cols="id,member_id",
        limit=10000,
    ) if sb_service is not None else _load_table(sb_anon, sb_service, schema, "loans", cols="id,member_id", limit=10000)

    if loans_map.empty or "id" not in loans_map.columns or "member_id" not in loans_map.columns:
        return pd.DataFrame()

    loans_map = loans_map.copy()
    loans_map["id"] = _to_int(loans_map["id"])
    loans_map["member_id"] = _to_int(loans_map["member_id"])

    pay2 = pay.copy()
    pay2["loan_id"] = _to_int(pay2["loan_id"])
    pay2 = pay2.merge(loans_map.rename(columns={"id": "loan_id"}), on="loan_id", how="left")

    # ensure amount + paid_at columns exist
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
    fines: pd.DataFrame,
    foundation: pd.DataFrame,
) -> pd.DataFrame:
    members = members.copy()
    members["id"] = _to_int(members["id"])
    members = members[members["id"] > 0].copy()
    now = _utc_now()

    base = pd.DataFrame({"member_id": members["id"].astype(int)})

    # ------------------------
    # Contributions
    # ------------------------
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

    # ------------------------
    # Loans (YOUR SCHEMA)
    # - balance doesn't exist -> use principal_current as "balance"
    # ------------------------
    lfeat = base.copy()
    if not loans.empty and "member_id" in loans.columns:
        l = loans.copy()
        l["member_id"] = _to_int(l["member_id"])

        # numeric fields from your table
        for col in ["principal", "principal_current", "total_due", "unpaid_interest"]:
            if col in l.columns:
                l[col] = _to_num(l[col])

        # derived "balance"
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

        # bad status count (tune tokens to your statuses)
        bad_tokens = ["delinquent", "default", "overdue", "late", "arrears", "past due", "past_due", "unpaid"]
        lfeat = lfeat.merge(
            grp["status"].apply(lambda s: sum(any(tok in str(v) for tok in bad_tokens) for v in s)).rename("loan_bad_status_count"),
            left_on="member_id",
            right_index=True,
            how="left",
        )

        # last paid date from loans table
        lfeat = lfeat.merge(grp["last_paid_at"].max().rename("loan_last_paid_dt"), left_on="member_id", right_index=True, how="left")
    else:
        lfeat["loan_count"] = 0
        lfeat["loan_balance_sum"] = 0.0
        lfeat["loan_principal_current_sum"] = 0.0
        lfeat["loan_total_due_sum"] = 0.0
        lfeat["loan_bad_status_count"] = 0
        lfeat["loan_last_paid_dt"] = pd.NaT

    # ------------------------
    # Payments (schema-safe)
    # ------------------------
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

    # Prefer payment last dt; fall back to loans.last_paid_at if no payments
    pfeat["days_since_last_payment"] = _days_since(now, pfeat["pay_last_dt"])

    # ------------------------
    # Fines
    # ------------------------
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

    # ------------------------
    # Foundation contributions
    # ------------------------
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

    # ------------------------
    # Combine
    # ------------------------
    X = (
        cfeat.merge(lfeat, on="member_id", how="left")
        .merge(pfeat, on="member_id", how="left")
        .merge(ffeat, on="member_id", how="left")
        .merge(fdfeat, on="member_id", how="left")
    )

    # Drop internal dt columns but keep days_since_*
    for dtcol in ["contrib_last_dt", "pay_last_dt", "foundation_last_dt", "loan_last_paid_dt"]:
        if dtcol in X.columns:
            X.drop(columns=[dtcol], inplace=True)

    X = _fill_feature_defaults(X)
    return X


# ============================================================
# Simple heuristic risk score (NO ML libs)
# ============================================================
def _compute_risk_score(row: pd.Series) -> tuple[float, list[str]]:
    """
    Returns (risk_0_to_1, reasons[])
    """
    reasons = []

    loan_balance = float(row.get("loan_balance_sum", 0.0))
    total_due = float(row.get("loan_total_due_sum", 0.0))
    bad_status = int(row.get("loan_bad_status_count", 0))
    days_pay = int(row.get("days_since_last_payment", 999))
    days_contrib = int(row.get("days_since_last_contrib", 999))
    fine_total = float(row.get("fine_total", 0.0))
    contrib_total = float(row.get("contrib_total", 0.0))
    contrib_count = int(row.get("contrib_count", 0))

    # base
    score = 0.0

    # Loan pressure
    if loan_balance > 0:
        score += 0.20
        reasons.append("Has outstanding loan balance")

    if total_due > 0 and total_due >= max(loan_balance, 1.0) * 1.02:
        score += 0.10
        reasons.append("Total due indicates interest/arrears")

    if bad_status > 0:
        score += min(0.30, 0.10 * bad_status)
        reasons.append("Loan status flagged as overdue/delinquent/etc.")

    # Recency signals
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

    # Fines
    if fine_total > 0:
        score += min(0.15, fine_total / 2000.0)  # scale gently
        reasons.append("Has fines")

    # Stabilizers (reduce risk)
    if contrib_total >= 2000:
        score -= 0.05
        reasons.append("Strong contributions reduce risk")
    if contrib_count >= 6:
        score -= 0.05
        reasons.append("Consistent contribution frequency reduces risk")

    score = float(np.clip(score, 0.0, 1.0))
    return score, reasons[:6]


# ============================================================
# Main render
# ============================================================
def render_ai_risk_panel(sb_anon, sb_service=None, schema: str = "public"):
    st.header("🤖 AI Risk Panel (Heuristic, No ML libs)")
    st.caption("NJANGI STANDARD • no legacy • schema-safe (matches your loans columns).")

    _ensure_clients_in_state(sb_anon, sb_service)

    if not _table_exists(sb_anon, schema, "members"):
        st.error("Missing table: members")
        return

    members = _load_table(sb_anon, sb_service, schema, "members", cols="id,name", limit=5000)
    if members.empty or "id" not in members.columns:
        st.error("members not readable.")
        return

    # Load contributions
    contrib = _load_table(sb_anon, sb_service, schema, "contributions", cols="member_id,session_id,amount,created_at", limit=20000) \
        if _table_exists(sb_anon, schema, "contributions") else pd.DataFrame()

    # Load loans (prefer service if available)
    loans = _load_table_service(
        sb_anon,
        sb_service,
        schema,
        "loans",
        cols="id,member_id,status,principal,principal_current,total_due,unpaid_interest,last_paid_at,created_at",
        limit=20000,
    ) if (sb_service is not None and _table_exists(sb_service, schema, "loans")) else (
        _load_table(sb_anon, sb_service, schema, "loans",
                    cols="id,member_id,status,principal,principal_current,total_due,unpaid_interest,last_paid_at,created_at",
                    limit=20000)
        if _table_exists(sb_anon, schema, "loans") else pd.DataFrame()
    )

    # Load payments schema-safe
    payments = _load_payments_schema_safe(sb_anon, sb_service, schema=schema, limit=30000) \
        if (_table_exists(sb_service, schema, "loan_payments") if sb_service is not None else _table_exists(sb_anon, schema, "loan_payments")) else pd.DataFrame()

    fines = _load_table_service(sb_anon, sb_service, schema, "fines", cols="member_id,amount,created_at", limit=20000) \
        if (sb_service is not None and _table_exists(sb_service, schema, "fines")) else (
            _load_table(sb_anon, sb_service, schema, "fines", cols="member_id,amount,created_at", limit=20000)
            if _table_exists(sb_anon, schema, "fines") else pd.DataFrame()
        )

    foundation = _load_table(sb_anon, sb_service, schema, "foundation_contributions", cols="member_id,amount,created_at", limit=20000) \
        if _table_exists(sb_anon, schema, "foundation_contributions") else pd.DataFrame()

    # Debug block (optional)
    with st.expander("🔎 Debug (rows loaded)", expanded=False):
        st.write("members:", len(members))
        st.write("contributions:", len(contrib))
        st.write("loans:", len(loans))
        st.write("loan_payments:", len(payments))
        st.write("fines:", len(fines))
        st.write("foundation_contributions:", len(foundation))
        if not loans.empty and "status" in loans.columns:
            st.write("loan status counts:")
            st.dataframe(loans["status"].astype(str).str.lower().value_counts().reset_index(), use_container_width=True)

    # Build features
    X = _build_member_features(members, contrib, loans, payments, fines, foundation)

    # Member picker
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

    row = X[X["member_id"] == mid].copy()
    if row.empty:
        st.warning("No feature row for selected member.")
        return

    row1 = row.iloc[0]

    # Risk score
    risk, reasons = _compute_risk_score(row1)

    st.subheader("Risk prediction")
    st.metric("Predicted Risk", f"{risk * 100:.1f}%")
    st.progress(float(np.clip(risk, 0.0, 1.0)))

    if reasons:
        st.caption("Why this risk score:")
        for r in reasons:
            st.write(f"• {r}")

    st.divider()
    st.subheader("Member feature snapshot (no blanks)")
    snap = row.T
    snap.columns = ["value"]
    st.dataframe(snap, use_container_width=True)
