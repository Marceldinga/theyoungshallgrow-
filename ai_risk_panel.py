# ai_risk_panel.py ✅ LEGACY SAFE (NO sklearn)
from __future__ import annotations
import streamlit as st
import pandas as pd


def safe_select(sb, schema, table, limit=20000):
    try:
        return sb.schema(schema).table(table).select("*").limit(limit).execute().data or []
    except Exception:
        return []


def compute_risk_score(row) -> int:
    """
    Strong Njangi-specific risk model (no ML dependency).
    """
    score = 100

    # Contributions (consistency)
    if row["n_contrib"] < 6:
        score -= (6 - row["n_contrib"]) * 5

    # Loans exposure
    score -= row["n_loans"] * 6

    # Repayment gap
    gap = max(0, row["n_loans"] - row["n_repays"])
    score -= gap * 8

    # Fines discipline
    score -= row["n_fines"] * 4

    # Amount-based risk
    if row["sum_loans"] > row["sum_repays"]:
        score -= min(20, (row["sum_loans"] - row["sum_repays"]) / 500)

    return max(0, min(100, int(score)))


def risk_band(score: int) -> str:
    if score >= 75:
        return "🟢 Low"
    if score >= 50:
        return "🟡 Medium"
    return "🔴 High"


def render_ai_risk_panel(sb_anon, sb_service, schema: str):
    st.header("🤖 AI Risk Panel (Legacy – Safe Mode)")
    st.caption("Production-safe risk model (no external ML dependency).")

    sb = sb_anon or sb_service
    if sb is None:
        st.error("No database client available.")
        return

    members = pd.DataFrame(safe_select(sb, schema, "members_legacy"))
    contrib = pd.DataFrame(safe_select(sb, schema, "contributions_legacy"))
    loans = pd.DataFrame(safe_select(sb, schema, "loans_legacy"))
    repays = pd.DataFrame(safe_select(sb, schema, "loan_repayments_legacy"))
    fines = pd.DataFrame(safe_select(sb, schema, "fines_legacy"))

    rows = []

    for _, m in members.iterrows():
        mid = int(m["id"])
        name = m.get("name", f"Member {mid}")

        m_contrib = contrib[contrib["member_id"] == mid]
        m_loans = loans[loans["borrower_member_id"] == mid]
        loan_ids = m_loans["id"].tolist()
        m_repays = repays[repays["loan_id"].isin(loan_ids)]
        m_fines = fines[fines["member_id"] == mid]

        row = {
            "member_id": mid,
            "member": name,
            "n_contrib": len(m_contrib),
            "n_loans": len(m_loans),
            "n_repays": len(m_repays),
            "n_fines": len(m_fines),
            "sum_contrib": m_contrib["amount"].sum() if not m_contrib.empty else 0,
            "sum_loans": m_loans["principal"].sum() if not m_loans.empty else 0,
            "sum_repays": m_repays["amount"].sum() if not m_repays.empty else 0,
        }

        row["risk_score"] = compute_risk_score(row)
        row["risk"] = risk_band(row["risk_score"])

        rows.append(row)

    df = pd.DataFrame(rows).sort_values("risk_score")

    c1, c2, c3 = st.columns(3)
    c1.metric("Members Scored", len(df))
    c2.metric("High Risk", (df["risk"] == "🔴 High").sum())
    c3.metric("Low Risk", (df["risk"] == "🟢 Low").sum())

    st.divider()
    st.subheader("Risk Table")
    st.dataframe(
        df[[
            "member_id", "member", "risk_score", "risk",
            "n_contrib", "n_loans", "n_repays", "n_fines"
        ]],
        use_container_width=True,
        hide_index=True
    )

    st.subheader("Recommendation")
    pick = st.selectbox("Select member", df["member"].tolist())
    r = df[df["member"] == pick].iloc[0]

    st.write("**Risk:**", r["risk"])
    st.write("**Score:**", r["risk_score"])

    if r["risk_score"] >= 75:
        st.success("Approve normal terms ✅")
    elif r["risk_score"] >= 50:
        st.warning("Approve with guarantor / reduced limit ⚠️")
    else:
        st.error("Reject or apply strict conditions ❌")
