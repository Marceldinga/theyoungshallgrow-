# ai_risk_panel.py ✅ WORKING NO-SKLEARN VERSION
from __future__ import annotations

import streamlit as st
import pandas as pd


def _safe_select(client, schema: str, table: str, cols: str = "*", limit: int = 2000, order_by: str = "created_at"):
    try:
        q = client.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=True)
        if limit:
            q = q.limit(limit)
        resp = q.execute()
        return resp.data or []
    except Exception as e:
        st.error(f"Failed reading {schema}.{table}")
        st.code(repr(e), language="text")
        return []


def _ensure_member_id(df: pd.DataFrame) -> pd.DataFrame:
    """If member_id isn't present, map common alternates -> member_id."""
    if df is None or df.empty:
        return df
    if "member_id" in df.columns:
        return df
    for c in ["legacy_member_id", "member_legacy_id", "memberId", "legacyMemberId"]:
        if c in df.columns:
            return df.rename(columns={c: "member_id"})
    return df


def render_ai_risk_panel(sb_anon, sb_service=None, schema: str = "public"):
    st.header("🤖 AI Risk Panel")
    st.caption("Fail-safe heuristic risk view (NO-SKLEARN).")

    # Pick a source that exists in your DB
    source = st.selectbox(
        "Contributions source",
        ["contributions_with_member", "contributions_legacy", "contributions"],
        index=0,
    )

    # IMPORTANT: request member_id explicitly
    cols = "id, member_id, session_id, amount, kind, created_at, payout_index, payout_date, user_id, updated_at"
    rows = _safe_select(sb_anon, schema, source, cols=cols, limit=3000, order_by="created_at")
    contrib = pd.DataFrame(rows)
    contrib = _ensure_member_id(contrib)

    if contrib.empty:
        st.info("No contributions returned (or source not readable).")
        st.caption("Make sure anon has SELECT permission on the table/view.")
        return

    if "member_id" not in contrib.columns:
        st.error("Missing 'member_id' in contributions dataframe.")
        st.write("Available columns:", list(contrib.columns))
        st.stop()

    # Members
    mrows = _safe_select(sb_anon, schema, "members_legacy", cols="id,name,position", limit=500, order_by="id")
    members = pd.DataFrame(mrows)

    if members.empty or "id" not in members.columns:
        st.warning("members_legacy not readable. Showing raw contributions instead.")
        st.dataframe(contrib.head(200), use_container_width=True, hide_index=True)
        return

    members["id"] = pd.to_numeric(members["id"], errors="coerce").fillna(-1).astype(int)
    members["name"] = members.get("name", "").astype(str)
    members = members[members["id"] >= 0].copy()
    members["label"] = members.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)

    pick = st.selectbox("Select member", members["label"].tolist())
    mid = int(members.loc[members["label"] == pick, "id"].iloc[0])

    # Filter (this is what crashed before)
    contrib["member_id"] = pd.to_numeric(contrib["member_id"], errors="coerce").fillna(-1).astype(int)
    contrib["amount"] = pd.to_numeric(contrib.get("amount", 0), errors="coerce").fillna(0)

    m_contrib = contrib[contrib["member_id"] == mid].copy()

    c1, c2, c3 = st.columns(3)
    c1.metric("Records", f"{len(m_contrib):,}")
    c2.metric("Total Amount", f"{float(m_contrib['amount'].sum()):,.0f}")
    c3.metric("Last Contribution", str(m_contrib["created_at"].max()) if "created_at" in m_contrib.columns and len(m_contrib) else "—")

    # Simple heuristic risk score
    risk = 0
    notes = []

    if len(m_contrib) == 0:
        risk += 3
        notes.append("No contributions found for this member.")

    if "created_at" in m_contrib.columns and len(m_contrib):
        try:
            m_contrib["created_at"] = pd.to_datetime(m_contrib["created_at"], errors="coerce")
            last_dt = m_contrib["created_at"].max()
            if pd.notna(last_dt):
                days = (pd.Timestamp.utcnow().tz_localize(None) - last_dt.tz_localize(None)).days if getattr(last_dt, "tzinfo", None) else (pd.Timestamp.utcnow() - last_dt).days
                if days > 20:
                    risk += 2
                    notes.append(f"No contribution in {days} days (possible missed bi-weekly cycle).")
        except Exception:
            pass

    st.subheader("Risk summary")
    st.progress(min(risk / 5, 1.0))
    st.write(f"**Risk score (0–5):** {min(risk, 5)}")

    if notes:
        for n in notes:
            st.warning(n)
    else:
        st.success("No obvious risk flags based on contributions.")

    st.divider()
    st.caption("Debug: contributions columns")
    st.write(list(contrib.columns))
    st.dataframe(contrib.head(50), use_container_width=True, hide_index=True)
