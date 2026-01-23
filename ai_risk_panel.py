# ai_risk_panel.py
from __future__ import annotations

import streamlit as st
import pandas as pd


def _safe_select(sb, schema: str, table: str, cols: str = "*", limit: int = 5000, order_by: str | None = None):
    try:
        q = sb.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=True)
        q = q.limit(limit)
        return q.execute().data or []
    except Exception:
        return []


def render_ai_risk_panel(sb_anon, sb_service, schema: str):
    st.header("🤖 AI Risk Panel")
    st.caption("Phase 1: rules-based scoring (works now). Phase 2: replace with ML model.")

    if sb_anon is None:
        st.error("Anon client not available.")
        return

    # -----------------------------
    # Load members (your app uses members_legacy)
    # -----------------------------
    members = _safe_select(sb_anon, schema, "members_legacy", "id,name,position", limit=2000, order_by="id")
    df_members = pd.DataFrame(members)
    if df_members.empty:
        st.info("No members found in members_legacy.")
        return

    # -----------------------------
    # Try to load loan & payment tables (optional)
    # If they don't exist, scoring still works using contributions only.
    # -----------------------------
    loans = _safe_select(sb_anon, schema, "loans", "*", limit=5000, order_by="created_at")
    pays = _safe_select(sb_anon, schema, "loan_payments", "*", limit=8000, order_by="paid_at")

    df_loans = pd.DataFrame(loans) if loans else pd.DataFrame()
    df_pays = pd.DataFrame(pays) if pays else pd.DataFrame()

    # -----------------------------
    # Load contributions view (you already have contributions_with_member view)
    # -----------------------------
    contrib = _safe_select(sb_anon, schema, "contributions_with_member", "*", limit=5000, order_by="created_at")
    df_contrib = pd.DataFrame(contrib) if contrib else pd.DataFrame()

    # -----------------------------
    # Build scores
    # score starts 100; subtract penalties
    # -----------------------------
    scores = []
    for _, m in df_members.iterrows():
        mid = int(m.get("id"))
        name = str(m.get("name") or f"Member {mid}")

        score = 100

        # Contributions behavior (if view exists)
        if not df_contrib.empty:
            # try common member id column names
            member_col = None
            for c in ["member_id", "legacy_member_id", "id"]:
                if c in df_contrib.columns:
                    member_col = c
                    break

            if member_col is not None:
                m_con = df_contrib[df_contrib[member_col] == mid]
                # fewer recent contributions => higher risk
                score -= min(30, max(0, 5 - len(m_con)) * 5)

        # Loans behavior (if table exists)
        if not df_loans.empty:
            # try common member id columns
            loan_member_col = None
            for c in ["requester_member_id", "member_id", "legacy_member_id", "borrower_member_id"]:
                if c in df_loans.columns:
                    loan_member_col = c
                    break

            if loan_member_col is not None:
                m_loans = df_loans[df_loans[loan_member_col] == mid]
                score -= min(35, len(m_loans) * 5)

                if "status" in df_loans.columns:
                    bad = m_loans[m_loans["status"].isin(["defaulted", "delinquent", "late"])]
                    score -= min(60, len(bad) * 15)

        # Payment behavior (if table exists)
        if not df_pays.empty:
            pay_member_col = None
            for c in ["payer_member_id", "member_id", "legacy_member_id"]:
                if c in df_pays.columns:
                    pay_member_col = c
                    break

            if pay_member_col is not None:
                m_p = df_pays[df_pays[pay_member_col] == mid]
                # fewer payments => higher risk
                score -= min(25, max(0, 3 - len(m_p)) * 5)

        score = max(0, min(100, score))

        if score >= 75:
            risk_label = "🟢 Low"
        elif score >= 50:
            risk_label = "🟡 Medium"
        else:
            risk_label = "🔴 High"

        scores.append(
            {
                "member_id": mid,
                "member": name,
                "risk_score": int(score),
                "risk": risk_label,
            }
        )

    df_scores = pd.DataFrame(scores).sort_values(["risk_score", "member"], ascending=[True, True])

    # -----------------------------
    # UI
    # -----------------------------
    c1, c2, c3 = st.columns(3)
    c1.metric("Members Scored", f"{len(df_scores):,}")
    c2.metric("High Risk (<50)", f"{(df_scores['risk_score'] < 50).sum():,}")
    c3.metric("Low Risk (>=75)", f"{(df_scores['risk_score'] >= 75).sum():,}")

    st.divider()
    st.subheader("Risk Table")
    st.dataframe(df_scores, use_container_width=True, hide_index=True)

    st.subheader("Quick Recommendation")
    pick = st.selectbox("Select member", df_scores["member"].tolist(), key="ai_pick_member")
    row = df_scores[df_scores["member"] == pick].iloc[0]

    st.write("**Risk:**", row["risk"])
    st.write("**Score:**", row["risk_score"])

    if row["risk_score"] >= 75:
        st.success("Recommendation: Approve normal terms ✅")
    elif row["risk_score"] >= 50:
        st.warning("Recommendation: Approve with guarantor / lower limit ⚠️")
    else:
        st.error("Recommendation: Reject or require strict conditions ❌")

    st.info("Next step: replace rules with a trained ML model (predict_proba) once we finalize features.")
