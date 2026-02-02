
# ai_risk_panel.py ✅ NJANGI STANDARD (NO "legacy" anywhere)
# ✅ Uses ONLY new tables:
#   - contributions
#   - members
#   - loans
#   - loan_payments (optional; used for last payment date)
#   - payouts (optional)
#   - foundation_contributions
#   - fines (optional)
#
# ✅ Heuristic risk view (NO sklearn)
# ✅ Safe reads (won’t crash if columns/order columns missing)

from __future__ import annotations

import streamlit as st
import pandas as pd


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
    """
    Safe Supabase read that won't crash if an order_by column doesn't exist.
    If order_by fails, retry without ordering.
    """
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
    """
    Try common timestamp/id columns for ordering, then fallback to no-order.
    """
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
    return pd.to_numeric(s, errors="coerce").fillna(0)


# ============================================================
# Loaders (NEW tables only)
# ============================================================
def _load_contributions(sb_anon, sb_service, schema: str) -> pd.DataFrame:
    """
    Loads contributions safely (new table only).
    columns commonly: id, member_id, session_id, amount, paid_at, created_at, note
    """
    try_cols = [
        "id,member_id,session_id,amount,paid_at,created_at,note",
        "member_id,session_id,amount,paid_at,created_at",
        "*",
    ]

    for cols in try_cols:
        rows = _safe_select_autosort(sb_anon, schema, "contributions", cols=cols, limit=5000, desc=True)
        if rows:
            return pd.DataFrame(rows)

    if sb_service is not None:
        st.info("Anon could not read contributions. Trying service client…")
        for cols in try_cols:
            rows = _safe_select_autosort(sb_service, schema, "contributions", cols=cols, limit=5000, desc=True)
            if rows:
                return pd.DataFrame(rows)

    return pd.DataFrame()


def _load_members(sb_anon, sb_service, schema: str) -> pd.DataFrame:
    try_cols = ["id,name", "*"]
    for cols in try_cols:
        rows = _safe_select(sb_anon, schema, "members", cols=cols, limit=500, order_by="id", desc=False, silent=True)
        if rows:
            return pd.DataFrame(rows)
    if sb_service is not None:
        for cols in try_cols:
            rows = _safe_select(sb_service, schema, "members", cols=cols, limit=500, order_by="id", desc=False, silent=True)
            if rows:
                return pd.DataFrame(rows)
    return pd.DataFrame()


# ============================================================
# Main
# ============================================================
def render_ai_risk_panel(sb_anon, sb_service=None, schema: str = "public"):
    st.header("🤖 AI Risk Panel")
    st.caption("Fail-safe heuristic risk view (NO-SKLEARN). New tables only.")

    if not _table_exists(sb_anon, schema, "contributions"):
        st.error("Missing table: contributions")
        return
    if not _table_exists(sb_anon, schema, "members"):
        st.error("Missing table: members")
        return

    contrib = _load_contributions(sb_anon, sb_service, schema)
    if contrib.empty:
        st.error("No contributions returned.")
        st.caption("Fix: GRANT SELECT / RLS policy for anon on contributions (or use sb_service).")
        return

    if "member_id" not in contrib.columns:
        st.error("Contributions dataframe missing member_id.")
        st.write("Columns:", list(contrib.columns))
        return

    contrib["member_id"] = _to_int(contrib["member_id"])
    if "session_id" in contrib.columns:
        contrib["session_id"] = _to_int(contrib["session_id"])
    if "amount" in contrib.columns:
        contrib["amount"] = _to_num(contrib["amount"])

    # Members
    members = _load_members(sb_anon, sb_service, schema)
    if members.empty or "id" not in members.columns:
        st.error("members not readable.")
        return

    members["id"] = _to_int(members["id"])
    members["name"] = members.get("name", "").astype(str)
    members = members[members["id"] > 0].copy()
    members["label"] = members.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)

    pick = st.selectbox("Select member", members["label"].tolist())
    mid = int(members.loc[members["label"] == pick, "id"].iloc[0])

    # ---- Contributions for member
    m_contrib = contrib[contrib["member_id"] == mid].copy()

    c1, c2, c3 = st.columns(3)
    c1.metric("Contrib Records", f"{len(m_contrib):,}")
    c2.metric("Contrib Total", f"{float(m_contrib['amount'].sum() if 'amount' in m_contrib.columns else 0):,.0f}")
    c3.metric(
        "Last Contribution",
        str(m_contrib["created_at"].max()) if "created_at" in m_contrib.columns and len(m_contrib) else "—",
    )

    # ---- Risk from contributions
    risk = 0
    notes: list[str] = []

    if len(m_contrib) == 0:
        risk += 3
        notes.append("No contributions found for this member.")

    if "created_at" in m_contrib.columns and len(m_contrib):
        try:
            m_contrib["created_at"] = pd.to_datetime(m_contrib["created_at"], errors="coerce")
            last_dt = m_contrib["created_at"].max()
            if pd.notna(last_dt):
                last_naive = last_dt.tz_localize(None) if getattr(last_dt, "tzinfo", None) else last_dt
                days = (pd.Timestamp.utcnow() - last_naive).days
                if days > 20:
                    risk += 2
                    notes.append(f"No contribution in {days} days (possible missed bi-weekly cycle).")
        except Exception:
            pass

    # ---- Loans (NEW: loans)
    st.divider()
    st.subheader("Loans")

    if sb_service is None:
        st.info("Loans may require service key if RLS blocks anon reads.")
        loans_rows = _safe_select_autosort(sb_anon, schema, "loans", cols="*", limit=2000, desc=True)
    else:
        loans_rows = _safe_select_autosort(sb_service, schema, "loans", cols="*", limit=2000, desc=True)

    loans = pd.DataFrame(loans_rows)
    if loans.empty:
        st.caption("No rows returned from loans (or not readable).")
    else:
        if "member_id" in loans.columns:
            loans["member_id"] = _to_int(loans["member_id"])
            m_loans = loans[loans["member_id"] == mid].copy()
        else:
            m_loans = pd.DataFrame()

        if m_loans.empty:
            st.caption("No loans for this member.")
        else:
            for col in ["principal", "principal_current", "unpaid_interest", "total_due", "balance"]:
                if col in m_loans.columns:
                    m_loans[col] = _to_num(m_loans[col])

            # principal_current preferred
            pc_sum = float(m_loans["principal_current"].sum()) if "principal_current" in m_loans.columns else float(m_loans.get("principal", pd.Series([0])).sum())
            td_sum = float(m_loans["total_due"].sum()) if "total_due" in m_loans.columns else 0.0

            k1, k2, k3 = st.columns(3)
            k1.metric("Loans Count", f"{len(m_loans):,}")
            k2.metric("Principal Current (sum)", f"{pc_sum:,.0f}")
            k3.metric("Total Due (sum)", f"{td_sum:,.0f}")

            if pc_sum > 0:
                risk += 1
                notes.append("Outstanding principal detected.")

            if "status" in m_loans.columns:
                bad = m_loans["status"].astype(str).str.lower().isin(["delinquent", "default", "overdue"])
                if bad.any():
                    risk += 2
                    notes.append("Loan status indicates delinquency/default/overdue.")

            st.dataframe(m_loans.head(50), use_container_width=True, hide_index=True)

    # ---- Loan payments (optional)
    st.divider()
    st.subheader("Loan Payments (optional)")

    if _table_exists(sb_anon, schema, "loan_payments") or (sb_service is not None and _table_exists(sb_service, schema, "loan_payments")):
        pclient = sb_service if sb_service is not None else sb_anon
        pay_rows = _safe_select_autosort(pclient, schema, "loan_payments", cols="*", limit=3000, desc=True)
        dfp = pd.DataFrame(pay_rows)
        if not dfp.empty and "member_id" in dfp.columns:
            dfp["member_id"] = _to_int(dfp["member_id"])
            mp = dfp[dfp["member_id"] == mid].copy()
            if mp.empty:
                st.caption("No loan payments for this member.")
            else:
                if "amount" in mp.columns:
                    mp["amount"] = _to_num(mp["amount"])
                last_paid = mp["paid_at"].max() if "paid_at" in mp.columns else mp.get("created_at", pd.Series(["—"])).max()
                st.metric("Payments Count", f"{len(mp):,}")
                st.metric("Payments Total", f"{float(mp['amount'].sum() if 'amount' in mp.columns else 0):,.0f}")
                st.metric("Last Payment", str(last_paid))
                st.dataframe(mp.head(50), use_container_width=True, hide_index=True)
    else:
        st.caption("loan_payments table not found. Skipping.")

    # ---- Fines (optional)
    st.divider()
    st.subheader("Fines (optional)")

    if not _table_exists(sb_anon, schema, "fines") and (sb_service is None or not _table_exists(sb_service, schema, "fines")):
        st.caption("fines table not found. Skipping.")
    else:
        fclient = sb_service if sb_service is not None else sb_anon
        fines_rows = _safe_select_autosort(fclient, schema, "fines", cols="*", limit=2000, desc=True)
        fines = pd.DataFrame(fines_rows)
        if fines.empty:
            st.caption("No fines rows returned.")
        else:
            if "member_id" in fines.columns:
                fines["member_id"] = _to_int(fines["member_id"])
                mf = fines[fines["member_id"] == mid].copy()
                if mf.empty:
                    st.caption("No fines for this member.")
                else:
                    if "amount" in mf.columns:
                        mf["amount"] = _to_num(mf["amount"])
                        if float(mf["amount"].sum()) > 0:
                            risk += 1
                            notes.append("Member has recorded fines.")
                    st.dataframe(mf.head(50), use_container_width=True, hide_index=True)

    # ---- Foundation contributions (NEW)
    st.divider()
    st.subheader("Foundation Contributions")

    if sb_service is None:
        fclient = sb_anon
    else:
        fclient = sb_service

    if not _table_exists(fclient, schema, "foundation_contributions"):
        st.caption("foundation_contributions table not found. Skipping.")
    else:
        f_rows = _safe_select_autosort(fclient, schema, "foundation_contributions", cols="*", limit=5000, desc=True)
        fnd = pd.DataFrame(f_rows)
        if fnd.empty:
            st.caption("No foundation records returned.")
        else:
            if "member_id" in fnd.columns:
                fnd["member_id"] = _to_int(fnd["member_id"])
                mfnd = fnd[fnd["member_id"] == mid].copy()
            else:
                mfnd = pd.DataFrame()

            if mfnd.empty:
                st.caption("No foundation records for this member.")
            else:
                if "amount" in mfnd.columns:
                    mfnd["amount"] = _to_num(mfnd["amount"])

                fp1, fp2 = st.columns(2)
                fp1.metric("Foundation Records", f"{len(mfnd):,}")
                fp2.metric("Total Foundation Paid", f"{float(mfnd['amount'].sum() if 'amount' in mfnd.columns else 0):,.0f}")

                # Risk rule: no foundation contribution records
                if float(mfnd["amount"].sum()) <= 0:
                    risk += 1
                    notes.append("No foundation contribution amount recorded for this member (check missing base payments).")

                st.dataframe(mfnd.head(50), use_container_width=True, hide_index=True)

    # ---- Summary
    st.divider()
    st.subheader("Risk summary")
    st.progress(min(risk / 5, 1.0))
    st.write(f"**Risk score (0–5):** {min(risk, 5)}")

    if notes:
        for n in notes:
            st.warning(n)
    else:
        st.success("No obvious risk flags based on contributions/loans/fines/foundation.")

    with st.expander("Debug (columns)", expanded=False):
        st.write("contributions columns:", list(contrib.columns))
        st.write("members columns:", list(members.columns))
