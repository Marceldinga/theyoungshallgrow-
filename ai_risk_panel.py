from __future__ import annotations
import streamlit as st
import pandas as pd
from sklearn.ensemble import RandomForestClassifier


def safe_select(sb, schema, table, limit=20000):
    try:
        return sb.schema(schema).table(table).select("*").limit(limit).execute().data or []
    except Exception:
        return []


def render_ai_risk_panel(sb_anon, sb_service, schema: str):
    st.header("🤖 AI Risk Panel (Legacy – Smart Model)")

    sb = sb_anon or sb_service
    if sb is None:
        st.error("No database client available.")
        return

    # --------------------------------------------------
    # LOAD DATA (LEGACY)
    # --------------------------------------------------
    members = pd.DataFrame(safe_select(sb, schema, "members_legacy"))
    contrib = pd.DataFrame(safe_select(sb, schema, "contributions_legacy"))
    loans = pd.DataFrame(safe_select(sb, schema, "loans_legacy"))
    repays = pd.DataFrame(safe_select(sb, schema, "loan_repayments_legacy"))
    fines = pd.DataFrame(safe_select(sb, schema, "fines_legacy"))

    if members.empty:
        st.warning("No members found.")
        return

    # --------------------------------------------------
    # FEATURE ENGINEERING (PER MEMBER)
    # --------------------------------------------------
    rows = []

    for _, m in members.iterrows():
        mid = int(m["id"])
        name = m.get("name", f"Member {mid}")

        m_contrib = contrib[contrib["member_id"] == mid]
        m_loans = loans[loans["borrower_member_id"] == mid]
        loan_ids = m_loans["id"].tolist()
        m_repays = repays[repays["loan_id"].isin(loan_ids)]
        m_fines = fines[fines["member_id"] == mid]

        n_contrib = len(m_contrib)
        n_loans = len(m_loans)
        n_repays = len(m_repays)
        n_fines = len(m_fines)

        sum_contrib = m_contrib["amount"].sum() if not m_contrib.empty else 0
        sum_loans = m_loans["principal"].sum() if not m_loans.empty else 0
        sum_repays = m_repays["amount"].sum() if not m_repays.empty else 0
        sum_fines = m_fines["amount"].sum() if not m_fines.empty else 0

        # default label (from loan status)
        defaulted = int(
            any(
                str(s).lower() in ["defaulted", "late", "arrears", "delinquent"]
                for s in m_loans.get("status", [])
            )
        )

        rows.append({
            "member_id": mid,
            "member": name,
            "n_contrib": n_contrib,
            "n_loans": n_loans,
            "n_repays": n_repays,
            "n_fines": n_fines,
            "sum_contrib": sum_contrib,
            "sum_loans": sum_loans,
            "sum_repays": sum_repays,
            "sum_fines": sum_fines,
            "defaulted": defaulted
        })

    df = pd.DataFrame(rows)

    # --------------------------------------------------
    # ML MODEL (REAL, BUT SAFE)
    # --------------------------------------------------
    X = df[
        ["n_contrib", "n_loans", "n_repays", "n_fines",
         "sum_contrib", "sum_loans", "sum_repays", "sum_fines"]
    ].fillna(0)

    y = df["defaulted"]

    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=7,
        random_state=42,
        class_weight="balanced"
    )
    model.fit(X, y)

    risk_prob = model.predict_proba(X)[:, 1]
    df["risk_score"] = (100 - (risk_prob * 100)).round().astype(int)

    def band(x):
        if x >= 75:
            return "🟢 Low"
        if x >= 50:
            return "🟡 Medium"
        return "🔴 High"

    df["risk"] = df["risk_score"].apply(band)

    # --------------------------------------------------
    # UI
    # --------------------------------------------------
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
    row = df[df["member"] == pick].iloc[0]

    st.write("**Risk:**", row["risk"])
    st.write("**Score:**", row["risk_score"])

    if row["risk_score"] >= 75:
        st.success("Approve normal terms ✅")
    elif row["risk_score"] >= 50:
        st.warning("Approve with guarantor / reduced limit ⚠️")
    else:
        st.error("Reject or apply strict conditions ❌")
