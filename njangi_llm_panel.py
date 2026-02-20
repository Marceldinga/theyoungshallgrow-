
# njangi_llm_panel.py
# ============================================================
# 🧠 NJANGI LLM PANEL (Lightweight / No external LLM)
# - NJANGI STANDARD (NO legacy)
# - Safe for Railway / Streamlit Cloud
# - Accepts sb_anon / sb_service / schema (matches app.py)
# - ✅ Supports ACTIVE + CLOSED (and overdue/unknown) loans
# - ✅ Uses sb_service if available (better access), else sb_anon
# ============================================================

from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import datetime, timezone


# ============================================================
# Helpers
# ============================================================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_sum(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())


def _safe_count(df: pd.DataFrame) -> int:
    return int(len(df)) if df is not None and not df.empty else 0


def _try_read(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 2000,
    order_by: str | None = None,
    desc: bool = True,
):
    """Safe supabase read; returns list[dict]."""
    if sb is None:
        return []
    q = sb.schema(schema).table(table).select(cols)
    if order_by:
        q = q.order(order_by, desc=desc)
    if limit:
        q = q.limit(int(limit))
    return (q.execute().data or [])


def _to_numeric_cols(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


def _norm_status(x) -> str:
    s = str(x or "").strip().lower()
    if s in ("active", "open", "running", "current"):
        return "active"
    if s in ("closed", "paid", "completed", "settled", "done"):
        return "closed"
    if s in ("overdue", "late", "default", "delinquent"):
        return "overdue"
    return s or "unknown"


def _simple_answer(question: str) -> str:
    q = question.lower().strip()

    if any(k in q for k in ["risk", "default", "overdue", "late", "delinquent"]):
        return (
            "Risk signals to watch:\n"
            "• Loans with status like 'overdue' / growing unpaid_interest\n"
            "• Long time since last payment (last_paid_at)\n"
            "• Many fines + inconsistent contributions\n"
            "Tip: Combine these into a score and flag top 5 members weekly."
        )

    if any(k in q for k in ["contribution", "pay", "deposit"]):
        return (
            "Contribution discipline tips:\n"
            "• Track contributions per session_id and highlight missing members\n"
            "• Rank top contributors by amount and consistency\n"
            "• Keep contributions in multiples of 500 (your rule) to simplify auditing"
        )

    if any(k in q for k in ["loan", "borrow", "interest", "principal"]):
        return (
            "Loan monitoring tips:\n"
            "• principal_current should trend down with payments\n"
            "• unpaid_interest should not grow for compliant borrowers\n"
            "• Watch last_paid_at; if > 30 days on active loans, follow up"
        )

    if any(k in q for k in ["minutes", "attendance"]):
        return (
            "Meeting operations:\n"
            "• Store minutes per session_id for traceability\n"
            "• Use attendance to justify fines (if your rules allow)\n"
            "• Produce a summary at the end of each session: present/absent + key decisions"
        )

    return (
        "I can help with:\n"
        "• Member risk insights (active + closed loans)\n"
        "• Contribution summaries\n"
        "• Loan monitoring tips\n"
        "Ask something like: 'Summarize active loans' or 'Show closed loans totals'."
    )


# ============================================================
# Main UI
# ============================================================
def render_njangi_llm_panel(sb_anon=None, sb_service=None, schema: str = "public"):
    st.title("🧠 Njangi LLM (Lightweight)")
    st.caption("No external LLM. Uses simple rules + your NJANGI data (if readable).")

    st.markdown("---")

    # ✅ Use service client when available (more reliable than anon for analytics)
    sb_read = sb_service if sb_service is not None else sb_anon

    # ---------- Load snapshots ----------
    members_df = pd.DataFrame(
        _try_read(sb_read, schema, "members", "id,name,display_name,phone", limit=5000, order_by="id", desc=False)
    )
    contrib_df = pd.DataFrame(
        _try_read(
            sb_read,
            schema,
            "contributions",
            "id,member_id,session_id,amount,paid_at,created_at",
            limit=5000,
            order_by="created_at",
            desc=True,
        )
    )
    loans_df = pd.DataFrame(
        _try_read(
            sb_read,
            schema,
            "loans",
            "id,member_id,principal,principal_current,total_due,unpaid_interest,last_paid_at,status,created_at",
            limit=5000,
            order_by="created_at",
            desc=True,
        )
    )
    fines_df = pd.DataFrame(_try_read(sb_read, schema, "fines", "*", limit=5000, order_by="created_at", desc=True))

    # numeric cleanup
    contrib_df = _to_numeric_cols(contrib_df, ["amount"])
    loans_df = _to_numeric_cols(loans_df, ["principal", "principal_current", "total_due", "unpaid_interest"])
    fines_df = _to_numeric_cols(fines_df, ["amount"])

    # normalize loan status
    if not loans_df.empty:
        loans_df["status_norm"] = loans_df.get("status", "").apply(_norm_status)

    # ---------- KPIs (top line) ----------
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Members", f"{_safe_count(members_df):,}")
    with c2:
        st.metric("Contrib rows", f"{_safe_count(contrib_df):,}")
    with c3:
        st.metric("Loans rows", f"{_safe_count(loans_df):,}")
    with c4:
        st.metric("Fines rows", f"{_safe_count(fines_df):,}")

    st.markdown("---")

    # ---------- Choose member (if possible) ----------
    member_id = None
    member_label = None

    if not members_df.empty:
        if "display_name" in members_df.columns:
            members_df["member_name"] = members_df["display_name"].fillna("").astype(str)
            members_df.loc[members_df["member_name"].str.strip() == "", "member_name"] = members_df["name"].astype(str)
        else:
            members_df["member_name"] = members_df["name"].astype(str)

        members_df["label"] = members_df.apply(lambda r: f"{int(r['id']):02d} • {r['member_name']}", axis=1)

        pick = st.selectbox("Select member (optional)", ["(All members)"] + members_df["label"].tolist())
        if pick != "(All members)":
            row = members_df[members_df["label"] == pick].iloc[0]
            member_id = int(row["id"])
            member_label = str(row["member_name"])
    else:
        st.warning("Could not load members (RLS blocked or table missing). Panel will still work with generic answers.")

    # ---------- Loans filter (Active / Closed / All) ----------
    st.subheader("🏦 Loans filter")
    loan_filter = st.radio("Show loans", ["All", "Active", "Closed"], horizontal=True)

    loans_view = loans_df.copy()
    if not loans_view.empty and "status_norm" in loans_view.columns:
        if loan_filter == "Active":
            loans_view = loans_view[loans_view["status_norm"] == "active"].copy()
        elif loan_filter == "Closed":
            loans_view = loans_view[loans_view["status_norm"] == "closed"].copy()

    # ---------- Snapshot ----------
    st.subheader("📌 Snapshot")

    if member_id is not None:
        mc = (
            contrib_df[contrib_df.get("member_id").astype(str) == str(member_id)].copy()
            if not contrib_df.empty and "member_id" in contrib_df.columns
            else pd.DataFrame()
        )
        ml_all = (
            loans_df[loans_df.get("member_id").astype(str) == str(member_id)].copy()
            if not loans_df.empty and "member_id" in loans_df.columns
            else pd.DataFrame()
        )
        ml = (
            loans_view[loans_view.get("member_id").astype(str) == str(member_id)].copy()
            if not loans_view.empty and "member_id" in loans_view.columns
            else pd.DataFrame()
        )
        mf = (
            fines_df[fines_df.get("member_id").astype(str) == str(member_id)].copy()
            if not fines_df.empty and "member_id" in fines_df.columns
            else pd.DataFrame()
        )

        total_contrib = _safe_sum(mc, "amount")
        total_principal_all = _safe_sum(ml_all, "principal")
        total_balance_all = _safe_sum(ml_all, "principal_current")
        unpaid_interest_all = _safe_sum(ml_all, "unpaid_interest")

        total_principal_view = _safe_sum(ml, "principal")
        total_balance_view = _safe_sum(ml, "principal_current")
        unpaid_interest_view = _safe_sum(ml, "unpaid_interest")

        total_fines = _safe_sum(mf, "amount") if "amount" in mf.columns else float(len(mf))

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Total contributions", f"${total_contrib:,.0f}")
        s2.metric("Loans principal (ALL)", f"${total_principal_all:,.0f}")
        s3.metric("Balance (ALL)", f"${total_balance_all:,.0f}")
        s4.metric("Unpaid interest (ALL)", f"${unpaid_interest_all:,.0f}")

        st.caption(f"Member: **{member_label}** • Loan filter: **{loan_filter}** • Generated: {_now_iso()}")

        # Simple risk heuristic (use ALL loans for risk, not filtered view)
        risk = 0
        if unpaid_interest_all > 0:
            risk += 35
        if total_balance_all > 0 and total_contrib == 0:
            risk += 25
        if not ml_all.empty and "status_norm" in ml_all.columns:
            if ml_all["status_norm"].astype(str).str.contains("overdue|default", case=False, na=False).any():
                risk += 45
        if total_fines > 0:
            risk += 10
        risk = min(100, risk)

        st.info(f"Quick heuristic risk score: **{risk}/100** (not ML, just rules).")

        # Show filtered loan totals for transparency
        st.write(f"**Filtered loans totals ({loan_filter})**")
        k1, k2, k3 = st.columns(3)
        k1.metric("Principal", f"${total_principal_view:,.0f}")
        k2.metric("Balance", f"${total_balance_view:,.0f}")
        k3.metric("Unpaid interest", f"${unpaid_interest_view:,.0f}")

    else:
        st.write("All-members snapshot:")
        st.write(f"• Total contributions amount: **${_safe_sum(contrib_df, 'amount'):,.0f}**")
        st.write(f"• Total loan principal ({loan_filter}): **${_safe_sum(loans_view, 'principal'):,.0f}**")
        st.write(f"• Total current balances ({loan_filter}): **${_safe_sum(loans_view, 'principal_current'):,.0f}**")
        st.write(f"• Total unpaid interest ({loan_filter}): **${_safe_sum(loans_view, 'unpaid_interest'):,.0f}**")

        if not loans_df.empty and "status_norm" in loans_df.columns:
            st.caption(
                "Loan status counts: "
                + ", ".join([f"{k}={int(v)}" for k, v in loans_df["status_norm"].value_counts().to_dict().items()])
            )

    st.markdown("---")

    # ---------- Q&A ----------
    st.subheader("💬 Ask the Njangi Assistant")
    question = st.text_area("Type a question (e.g., 'Explain risk', 'Loan tips', 'Contribution summary')")

    if st.button("Analyze"):
        if not question.strip():
            st.warning("Please enter a question.")
        else:
            st.success("Assistant response")
            st.write(_simple_answer(question))

    st.caption("Lightweight assistant • No OpenAI • Safe for Railway/Streamlit Cloud")
