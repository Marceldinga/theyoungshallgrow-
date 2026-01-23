# ai_risk_panel.py ✅ UPDATED (adds foundation_payments_legacy to risk)
from __future__ import annotations

import streamlit as st
import pandas as pd


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
    If order_by fails, we retry without ordering.
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
    for c in ["created_at", "issued_at", "updated_at", "paid_at", "date_paid", "date", "start_date", "id"]:
        rows = _safe_select(
            client, schema, table, cols=cols, limit=limit, order_by=c, desc=desc, silent=True
        )
        if rows:
            return rows
    return _safe_select(client, schema, table, cols=cols, limit=limit, order_by=None, desc=desc, silent=True)


def _to_int(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(-1).astype(int)


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0)


def _load_contrib(sb_anon, sb_service, schema: str, source: str) -> pd.DataFrame:
    """
    Loads contributions safely for:
    - contributions_legacy (table: has id, payout_index, payout_date)
    - contributions_with_member (view: may NOT have id/payout_index/payout_date)
    """
    try_cols = [
        "id, member_id, session_id, amount, kind, created_at, payout_index, payout_date, user_id, updated_at",
        "member_id, amount, kind, created_at, session_id",
        "*",
    ]

    # 1) Try anon
    for cols in try_cols:
        rows = _safe_select_autosort(sb_anon, schema, source, cols=cols, limit=3000, desc=True)
        if rows:
            return pd.DataFrame(rows)

    # 2) Try service if anon blocked
    if sb_service is not None:
        st.info("Anon could not read contributions. Trying service client…")
        for cols in try_cols:
            rows = _safe_select_autosort(sb_service, schema, source, cols=cols, limit=3000, desc=True)
            if rows:
                return pd.DataFrame(rows)

    return pd.DataFrame()


def render_ai_risk_panel(sb_anon, sb_service=None, schema: str = "public"):
    st.header("🤖 AI Risk Panel")
    st.caption("Fail-safe heuristic risk view (NO-SKLEARN).")

    source = st.selectbox(
        "Contributions source (recommended: contributions_legacy)",
        ["contributions_legacy", "contributions_with_member"],
        index=0,
    )

    contrib = _load_contrib(sb_anon, sb_service, schema, source)

    if contrib.empty:
        st.error("No contributions returned.")
        st.caption("Fix: GRANT SELECT / RLS policy for anon on contributions_legacy (or use sb_service).")
        return

    if "member_id" not in contrib.columns:
        st.error("Contributions dataframe missing member_id.")
        st.write("Columns:", list(contrib.columns))
        return

    contrib["member_id"] = _to_int(contrib["member_id"])
    if "amount" in contrib.columns:
        contrib["amount"] = _to_num(contrib["amount"])

    # Members (try anon first, fallback to service)
    mrows = _safe_select(
        sb_anon,
        schema,
        "members_legacy",
        cols="id,name,position",
        limit=500,
        order_by="id",
        desc=False,
        silent=True,
    )
    if not mrows and sb_service is not None:
        mrows = _safe_select(
            sb_service,
            schema,
            "members_legacy",
            cols="id,name,position",
            limit=500,
            order_by="id",
            desc=False,
            silent=True,
        )

    members = pd.DataFrame(mrows)
    if members.empty or "id" not in members.columns:
        st.error("members_legacy not readable.")
        return

    members["id"] = _to_int(members["id"])
    members["name"] = members.get("name", "").astype(str)
    members = members[members["id"] >= 0].copy()
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

    # ---------------- LOANS (loans_legacy) ----------------
    st.divider()
    st.subheader("Loans (Legacy)")

    if sb_service is None:
        st.info("Loans need SUPABASE_SERVICE_KEY (service client).")
    else:
        loans_rows = _safe_select_autosort(sb_service, schema, "loans_legacy", cols="*", limit=2000, desc=True)
        loans = pd.DataFrame(loans_rows)

        if loans.empty:
            st.info("No rows returned from loans_legacy (or table not readable).")
        else:
            if "member_id" in loans.columns:
                loans["member_id"] = _to_int(loans["member_id"])
                m_loans = loans[loans["member_id"] == mid].copy()
            else:
                m_loans = pd.DataFrame()

            if m_loans.empty:
                st.info("No loans for this member.")
            else:
                for col in ["principal", "interest", "total_due", "balance"]:
                    if col in m_loans.columns:
                        m_loans[col] = _to_num(m_loans[col])

                k1, k2, k3 = st.columns(3)
                k1.metric("Loans Count", f"{len(m_loans):,}")
                k2.metric("Balance (sum)", f"{float(m_loans['balance'].sum() if 'balance' in m_loans.columns else 0):,.0f}")
                k3.metric("Total Due (sum)", f"{float(m_loans['total_due'].sum() if 'total_due' in m_loans.columns else 0):,.0f}")

                if "balance" in m_loans.columns and float(m_loans["balance"].sum()) > 0:
                    risk += 1
                    notes.append("Outstanding loan balance detected.")

                if "status" in m_loans.columns:
                    bad = m_loans["status"].astype(str).str.lower().isin(["delinquent", "default", "overdue"])
                    if bad.any():
                        risk += 2
                        notes.append("Loan status indicates delinquency/default/overdue.")

                st.dataframe(m_loans.head(50), use_container_width=True, hide_index=True)

    # ---------------- FINES ----------------
    st.divider()
    st.subheader("Fines")

    if sb_service is None:
        st.caption("Fines skipped (service key not set).")
    else:
        fines_rows = _safe_select_autosort(sb_service, schema, "fines_legacy", cols="*", limit=800, desc=True)
        fines = pd.DataFrame(fines_rows)

        if fines.empty:
            st.caption("No fines rows returned (or fines_legacy not readable).")
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
            else:
                st.caption("fines_legacy has no member_id column.")

    # ---------------- FOUNDATION PAYMENTS ----------------
    st.divider()
    st.subheader("Foundation (foundation_payments_legacy)")

    if sb_service is None:
        st.info("Foundation payments need SUPABASE_SERVICE_KEY (service client).")
    else:
        f_rows = _safe_select_autosort(sb_service, schema, "foundation_payments_legacy", cols="*", limit=2000, desc=True)
        fnd = pd.DataFrame(f_rows)

        if fnd.empty:
            st.caption("No rows returned from foundation_payments_legacy (or table not readable).")
        else:
            # normalize expected columns (from your screenshot: member_id, amount_paid, amount_pending, status, date_paid, notes, converted_to_loan)
            if "member_id" in fnd.columns:
                fnd["member_id"] = _to_int(fnd["member_id"])
                mfnd = fnd[fnd["member_id"] == mid].copy()
            else:
                mfnd = pd.DataFrame()

            if mfnd.empty:
                st.caption("No foundation records for this member.")
            else:
                if "amount_paid" in mfnd.columns:
                    mfnd["amount_paid"] = _to_num(mfnd["amount_paid"])
                if "amount_pending" in mfnd.columns:
                    mfnd["amount_pending"] = _to_num(mfnd["amount_pending"])

                # KPIs
                fp1, fp2, fp3 = st.columns(3)
                fp1.metric("Foundation Records", f"{len(mfnd):,}")
                fp2.metric(
                    "Total Paid",
                    f"{float(mfnd['amount_paid'].sum() if 'amount_paid' in mfnd.columns else 0):,.0f}",
                )
                fp3.metric(
                    "Total Pending",
                    f"{float(mfnd['amount_pending'].sum() if 'amount_pending' in mfnd.columns else 0):,.0f}",
                )

                # Risk rules (foundation)
                pending_sum = float(mfnd["amount_pending"].sum()) if "amount_pending" in mfnd.columns else 0.0
                if pending_sum > 0:
                    risk += 1
                    notes.append(f"Foundation has pending amount ({pending_sum:,.0f}).")

                if "status" in mfnd.columns:
                    not_paid = ~mfnd["status"].astype(str).str.lower().eq("paid")
                    if not_paid.any():
                        risk += 1
                        notes.append("Foundation has records not marked as paid.")

                if "converted_to_loan" in mfnd.columns:
                    conv = mfnd["converted_to_loan"].astype(str).str.lower().isin(["true", "t", "1", "yes"])
                    if conv.any():
                        risk += 1
                        notes.append("Some foundation payments were converted to loan.")

                st.dataframe(mfnd.head(50), use_container_width=True, hide_index=True)

    # ---------------- SUMMARY ----------------
    st.divider()
    st.subheader("Risk summary")
    st.progress(min(risk / 5, 1.0))
    st.write(f"**Risk score (0–5):** {min(risk, 5)}")

    if notes:
        for n in notes:
            st.warning(n)
    else:
        st.success("No obvious risk flags based on contributions/loans/fines/foundation.")

    st.caption("Debug: contributions columns")
    st.write(list(contrib.columns))
