# ai_risk_panel.py  ✅ NO-SKLEARN + FAIL-SAFE (fixes KeyError: 'member_id')
from __future__ import annotations

import streamlit as st
import pandas as pd


def _safe_select(client, schema: str, table: str, select_cols: str = "*", limit: int = 2000, order_by: str = "created_at"):
    try:
        q = client.schema(schema).table(table).select(select_cols)
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
    """Guarantee df has 'member_id' column if any equivalent exists."""
    if df is None or df.empty:
        return df

    if "member_id" in df.columns:
        return df

    # common alternates
    candidates = [
        "legacy_member_id",
        "member_legacy_id",
        "memberId",
        "legacyMemberId",
        "member",
        "mid",
    ]
    for c in candidates:
        if c in df.columns:
            df = df.rename(columns={c: "member_id"})
            return df

    return df


def _to_int_safe(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(-1).astype(int)


def render_ai_risk_panel(sb_anon, sb_service=None, schema: str = "public"):
    st.header("🤖 AI Risk Panel")
    st.caption("Lightweight risk signals from contributions behavior (NO-SKLEARN).")

    # ---- choose source for contributions
    # If you have a view that joins member names, keep it.
    # But we MUST ensure member_id exists in the returned data.
    contrib_source = st.selectbox(
        "Contributions source",
        ["contributions_with_member", "contributions_legacy", "contributions"],
        index=0,
        help="Pick whichever exists in your schema. Must include member_id.",
    )

    # ---- load contributions (explicit columns if possible)
    # We explicitly request member_id so the dataframe always has it.
    # If your source is a view with different names, _ensure_member_id will map it.
    select_cols = "id, member_id, session_id, amount, kind, created_at, payout_index, payout_date, user_id, updated_at"

    rows = _safe_select(sb_anon, schema, contrib_source, select_cols=select_cols, limit=3000, order_by="created_at")
    contrib = pd.DataFrame(rows)
    contrib = _ensure_member_id(contrib)

    if contrib.empty:
        st.info("No contributions returned (or source not readable).")
        st.caption("If using a view, ensure anon has SELECT permission.")
        return

    if "member_id" not in contrib.columns:
        st.error("AI Risk Panel: 'member_id' is missing from the contributions dataframe.")
        st.write("Available columns:", list(contrib.columns))
        st.stop()

    # clean types
    contrib["member_id"] = _to_int_safe(contrib["member_id"])
    if "amount" in contrib.columns:
        contrib["amount"] = pd.to_numeric(contrib["amount"], errors="coerce").fillna(0)

    # ---- load members (for selection)
    # Use your legacy members table (matches your app.py)
    m_rows = _safe_select(sb_anon, schema, "members_legacy", select_cols="id,name,position", limit=500, order_by="id")
    members = pd.DataFrame(m_rows)

    if members.empty or "id" not in members.columns:
        st.warning("members_legacy not found/readable. Showing risk summary without member picker.")
        st.dataframe(contrib.head(200), use_container_width=True)
        return

    members["id"] = _to_int_safe(members["id"])
    members["name"] = members.get("name", "").astype(str)
    members["label"] = members.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)

    pick = st.selectbox("Select member", members["label"].tolist())
    mid = int(members.loc[members["label"] == pick, "id"].iloc[0])

    # ---- filter contributions for that member (THIS is where you had KeyError before)
    m_contrib = contrib[contrib["member_id"] == mid].copy()

    st.subheader("Member contributions")
    c1, c2, c3 = st.columns(3)
    c1.metric("Records", f"{len(m_contrib):,}")
    c2.metric("Total Amount", f"{float(m_contrib['amount'].sum() if 'amount' in m_contrib.columns else 0):,.0f}")
    c3.metric("Last Contribution", str(m_contrib["created_at"].max()) if "created_at" in m_contrib.columns and not m_contrib.empty else "—")

    # ---- simple risk flags (no sklearn)
    risk = 0
    notes = []

    if len(m_contrib) == 0:
        risk += 3
        notes.append("No contributions found for this member.")

    # optional: detect last 14 days activity (bi-weekly rhythm)
    if "created_at" in m_contrib.columns and not m_contrib.empty:
        try:
            m_contrib["created_at"] = pd.to_datetime(m_contrib["created_at"], errors="coerce")
            last_dt = m_contrib["created_at"].max()
            if pd.notna(last_dt):
                days = (pd.Timestamp.utcnow().tz_localize(None) - last_dt.tz_localize(None)).days if last_dt.tzinfo else (pd.Timestamp.utcnow() - last_dt).days
                if days > 20:
                    risk += 2
                    notes.append(f"No contribution in {days} days (possible missed bi-weekly cycle).")
        except Exception:
            pass

    # show
    st.subheader("Risk summary (heuristic)")
    st.progress(min(risk / 5, 1.0))
    st.write(f"**Risk score (0–5):** {min(risk, 5)}")

    if notes:
        for n in notes:
            st.warning(n)
    else:
        st.success("No obvious risk flags based on contributions.")

    st.divider()
    st.caption("Debug preview (first 50 rows)")
    st.dataframe(contrib.head(50), use_container_width=True, hide_index=True)
