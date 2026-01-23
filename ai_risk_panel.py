# ai_risk_panel.py ✅ UPDATED (uses real tables: contributions_legacy, loans; optional fines)
from __future__ import annotations

import streamlit as st
import pandas as pd


def _safe_select(
    client,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 2000,
    order_by: str | None = "created_at",
    desc: bool = True,
):
    try:
        q = client.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit:
            q = q.limit(limit)
        resp = q.execute()
        return resp.data or []
    except Exception as e:
        st.error(f"Failed reading {schema}.{table}")
        st.code(repr(e), language="text")
        return []


def _to_int(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(-1).astype(int)


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0)


def render_ai_risk_panel(sb_anon, sb_service=None, schema: str = "public"):
    st.header("🤖 AI Risk Panel")
    st.caption("Fail-safe heuristic risk view (NO-SKLEARN).")

    # ✅ REAL sources (based on your DB screenshots)
    # contributions_with_member is optional; it breaks if it doesn't have id.
    contrib_source = st.selectbox(
        "Contributions source (recommended: contributions_legacy)",
        ["contributions_legacy", "contributions_with_member", "contributions"],
        index=0,
    )

    # ---------- LOAD CONTRIBUTIONS ----------
    # ✅ DO NOT assume the view has "id" — use fallback lists
    try_cols = [
        # for tables like contributions_legacy (has id)
        "id, member_id, session_id, amount, kind, created_at, payout_index, payout_date, user_id, updated_at",
        # for views that might not have id
        "member_id, session_id, amount, kind, created_at, payout_index, payout_date, user_id, updated_at",
        # minimum
        "member_id, amount, kind, created_at, session_id",
        "*",
    ]

    rows = []
    for cols in try_cols:
        rows = _safe_select(sb_anon, schema, contrib_source, cols=cols, limit=3000, order_by="created_at", desc=True)
        if rows:
            break

    contrib = pd.DataFrame(rows)

    if contrib.empty:
        st.info("No contributions returned (or source not readable).")
        st.caption("Use contributions_legacy or grant SELECT on the view/table to anon.")
        return

    if "member_id" not in contrib.columns:
        st.error("Contributions dataframe missing 'member_id'.")
        st.write("Available columns:", list(contrib.columns))
        st.stop()

    contrib["member_id"] = _to_int(contrib["member_id"])
    if "amount" in contrib.columns:
        contrib["amount"] = _to_num(contrib["amount"])

    # ---------- LOAD MEMBERS ----------
    mrows = _safe_select(sb_anon, schema, "members_legacy", cols="id,name,position", limit=500, order_by="id", desc=False)
    members = pd.DataFrame(mrows)

    if members.empty or "id" not in members.columns:
        st.warning("members_legacy not readable. Showing raw contributions instead.")
        st.dataframe(contrib.head(200), use_container_width=True, hide_index=True)
        return

    members["id"] = _to_int(members["id"])
    members["name"] = members.get("name", "").astype(str)
    members = members[members["id"] >= 0].copy()
    members["label"] = members.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)

    pick = st.selectbox("Select member", members["label"].tolist())
    mid = int(members.loc[members["label"] == pick, "id"].iloc[0])

    # ---------- MEMBER CONTRIBUTIONS ----------
    m_contrib = contrib[contrib["member_id"] == mid].copy()

    c1, c2, c3 = st.columns(3)
    c1.metric("Contrib Records", f"{len(m_contrib):,}")
    c2.metric("Contrib Total", f"{float(m_contrib['amount'].sum() if 'amount' in m_contrib.columns else 0):,.0f}")
    c3.metric("Last Contribution", str(m_contrib["created_at"].max()) if "created_at" in m_contrib.columns and len(m_contrib) else "—")

    # ---------- CONTRIBUTION-BASED RISK ----------
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

    # ---------- LOANS (REAL TABLE: loans) ----------
    st.divider()
    st.subheader("Loans")

    if sb_service is None:
        st.info("Loans need SUPABASE_SERVICE_KEY (sb_service). Add it to enable loan analytics.")
    else:
        loans_rows = _safe_select(sb_service, schema, "loans", cols="*", limit=2000, order_by="created_at", desc=True)
        loans = pd.DataFrame(loans_rows)

        if loans.empty:
            st.warning("No rows returned from loans (or table not readable).")
        else:
            # your table has member_id
            if "member_id" in loans.columns:
                loans["member_id"] = _to_int(loans["member_id"])
                m_loans = loans[loans["member_id"] == mid].copy()
            else:
                m_loans = pd.DataFrame()

            if m_loans.empty:
                st.info("No loans found for this member.")
            else:
                # numeric fields from your screenshot
                for col in ["principal", "interest", "total_due", "balance"]:
                    if col in m_loans.columns:
                        m_loans[col] = _to_num(m_loans[col])

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Loans Count", f"{len(m_loans):,}")
                k2.metric("Principal (sum)", f"{float(m_loans['principal'].sum() if 'principal' in m_loans.columns else 0):,.0f}")
                k3.metric("Balance (sum)", f"{float(m_loans['balance'].sum() if 'balance' in m_loans.columns else 0):,.0f}")
                k4.metric("Total Due (sum)", f"{float(m_loans['total_due'].sum() if 'total_due' in m_loans.columns else 0):,.0f}")

                # loan-based risk heuristics
                if "balance" in m_loans.columns and float(m_loans["balance"].sum()) > 0:
                    risk += 1
                    notes.append("Outstanding loan balance detected.")

                if "status" in m_loans.columns:
                    bad = m_loans["status"].astype(str).str.lower().isin(["delinquent", "default", "overdue"])
                    if bad.any():
                        risk += 2
                        notes.append("Loan status indicates delinquency/default/overdue.")

                st.dataframe(m_loans.head(50), use_container_width=True, hide_index=True)

    # ---------- FINES (optional; try a few likely names safely) ----------
    st.divider()
    st.subheader("Fines (optional)")

    fines_found = False
    if sb_service is None:
        st.caption("Fines read skipped (service key not set).")
    else:
        for fines_table in ["fines_with_member", "fines_legacy", "fines"]:
            fines_rows = _safe_select(sb_service, schema, fines_table, cols="*", limit=500, order_by="created_at", desc=True)
            df_f = pd.DataFrame(fines_rows)
            if df_f.empty:
                continue

            # must have member_id to filter
            if "member_id" not in df_f.columns:
                continue

            df_f["member_id"] = _to_int(df_f["member_id"])
            m_f = df_f[df_f["member_id"] == mid].copy()
            fines_found = True

            if m_f.empty:
                st.caption(f"{fines_table}: no fines for this member.")
            else:
                if "amount" in m_f.columns:
                    m_f["amount"] = _to_num(m_f["amount"])
                    if float(m_f["amount"].sum()) > 0:
                        risk += 1
                        notes.append("Member has recorded fines.")

                st.markdown(f"**Source:** `{fines_table}`")
                st.dataframe(m_f.head(50), use_container_width=True, hide_index=True)

            break

    if not fines_found:
        st.caption("No fines table/view found (or not readable).")

    # ---------- FINAL SUMMARY ----------
    st.divider()
    st.subheader("Risk summary")
    st.progress(min(risk / 5, 1.0))
    st.write(f"**Risk score (0–5):** {min(risk, 5)}")

    if notes:
        for n in notes:
            st.warning(n)
    else:
        st.success("No obvious risk flags based on contributions/loans.")

    st.caption("Debug: contributions columns")
    st.write(list(contrib.columns))
