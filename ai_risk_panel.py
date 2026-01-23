# ai_risk_panel.py  ✅ LEGACY ONLY
from __future__ import annotations

import streamlit as st
import pandas as pd


def _safe_select(sb, schema: str, table: str, cols: str = "*", limit: int = 5000, order_by: str | None = None):
    try:
        q = sb.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=True)
        if limit is not None:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception:
        return []


def _first_existing_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def render_ai_risk_panel(sb_anon, sb_service, schema: str):
    st.header("🤖 AI Risk Panel (Legacy)")
    st.caption("Rules-based risk scoring using legacy tables. (ML upgrade later.)")

    reader = sb_anon or sb_service
    if reader is None:
        st.error("No Supabase client available.")
        return

    # -----------------------------
    # LOAD LEGACY TABLES
    # -----------------------------
    members = _safe_select(reader, schema, "members_legacy", "*", limit=2000, order_by="id")
    contrib = _safe_select(reader, schema, "contributions_legacy", "*", limit=8000, order_by="created_at")
    loans = _safe_select(reader, schema, "loans_legacy", "*", limit=5000, order_by="created_at")
    repays = _safe_select(reader, schema, "loan_repayments_legacy", "*", limit=12000, order_by="paid_at")
    fines = _safe_select(reader, schema, "fines_legacy", "*", limit=8000, order_by="created_at")

    df_members = pd.DataFrame(members)
    df_contrib = pd.DataFrame(contrib)
    df_loans = pd.DataFrame(loans)
    df_repays = pd.DataFrame(repays)
    df_fines = pd.DataFrame(fines)

    if df_members.empty:
        st.info("No members found in members_legacy.")
        return

    # -----------------------------
    # DETECT MEMBER-ID COLUMNS (legacy might vary)
    # -----------------------------
    contrib_mid = _first_existing_col(df_contrib, ["member_id", "legacy_member_id", "id"]) if not df_contrib.empty else None
    loans_mid = _first_existing_col(df_loans, ["member_id", "requester_member_id", "borrower_member_id", "legacy_member_id"]) if not df_loans.empty else None
    repays_mid = _first_existing_col(df_repays, ["member_id", "payer_member_id", "legacy_member_id"]) if not df_repays.empty else None
    fines_mid = _first_existing_col(df_fines, ["member_id", "legacy_member_id", "id"]) if not df_fines.empty else None

    # -----------------------------
    # SCORE MEMBERS (rules-based)
    # score starts 100; subtract penalties
    # -----------------------------
    out = []

    for _, m in df_members.iterrows():
        mid = m.get("id") or m.get("legacy_member_id") or m.get("member_id")
        try:
            mid = int(mid)
        except Exception:
            continue

        name = str(m.get("name") or m.get("full_name") or f"Member {mid}")

        score = 100

        # Contributions penalty
        if contrib_mid and not df_contrib.empty:
            m_con = df_contrib[df_contrib[contrib_mid] == mid]
            # fewer total contributions => risk
            score -= min(30, max(0, 6 - len(m_con)) * 5)

        # Loans penalty
        if loans_mid and not df_loans.empty:
            m_loans = df_loans[df_loans[loans_mid] == mid]
            score -= min(35, len(m_loans) * 5)

            # If status exists, penalize bad statuses
            if "status" in df_loans.columns:
                bad = m_loans[m_loans["status"].isin(["defaulted", "delinquent", "late", "arrears"])]
                score -= min(60, len(bad) * 15)

        # Repayments penalty
        if repays_mid and not df_repays.empty:
            m_rep = df_repays[df_repays[repays_mid] == mid]
            # too few repayments relative to loans -> risk
            if loans_mid and not df_loans.empty:
                n_loans = len(df_loans[df_loans[loans_mid] == mid])
            else:
                n_loans = 0
            if n_loans > 0:
                score -= min(25, max(0, n_loans - len(m_rep)) * 5)

        # Fines penalty
        if fines_mid and not df_fines.empty:
            m_fines = df_fines[df_fines[fines_mid] == mid]
            score -= min(20, len(m_fines) * 3)

        score = max(0, min(100, score))

        if score >= 75:
            risk = "🟢 Low"
        elif score >= 50:
            risk = "🟡 Medium"
        else:
            risk = "🔴 High"

        out.append(
            {
                "member_id": mid,
                "member": name,
                "risk_score": int(score),
                "risk": risk,
                "contributions": int(len(df_contrib[df_contrib[contrib_mid] == mid])) if contrib_mid and not df_contrib.empty else 0,
                "loans": int(len(df_loans[df_loans[loans_mid] == mid])) if loans_mid and not df_loans.empty else 0,
                "repayments": int(len(df_repays[df_repays[repays_mid] == mid])) if repays_mid and not df_repays.empty else 0,
                "fines": int(len(df_fines[df_fines[fines_mid] == mid])) if fines_mid and not df_fines.empty else 0,
            }
        )

    df = pd.DataFrame(out).sort_values(["risk_score", "member"], ascending=[True, True])

    # -----------------------------
    # UI
    # -----------------------------
    c1, c2, c3 = st.columns(3)
    c1.metric("Members scored", f"{len(df):,}")
    c2.metric("High risk (<50)", f"{(df['risk_score'] < 50).sum():,}")
    c3.metric("Low risk (>=75)", f"{(df['risk_score'] >= 75).sum():,}")

    st.divider()
    st.subheader("Risk table")
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Recommendation")
    pick = st.selectbox("Select member", df["member"].tolist(), key="ai_pick_member_legacy")
    row = df[df["member"] == pick].iloc[0]

    st.write("**Risk:**", row["risk"])
    st.write("**Score:**", row["risk_score"])

    if row["risk_score"] >= 75:
        st.success("Approve normal terms ✅")
    elif row["risk_score"] >= 50:
        st.warning("Approve with guarantor / lower limit ⚠️")
    else:
        st.error("Reject or require strict conditions ❌")
