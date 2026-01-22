# dashboard_panel.py ✅ FULL UPGRADED (standard + beautiful bank dashboard)
# Adds:
# ✅ Finance KPI strip (dashboard_finance_view)
# ✅ Scope toggle (All-time vs This session placeholder)
# ✅ Pot progress bar (uses expected pot = members * 500 default)
# ✅ Meeting checklist (attendance/minutes/payout)
# ✅ Latest activity feed (from minutes/attendance + repayments)
# ✅ Clean layout sections + consistent formatting

from __future__ import annotations

from datetime import date
import streamlit as st
import pandas as pd


# -------------------------
# small helpers
# -------------------------
def safe_select(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    order_by: str | None = None,
    desc: bool = False,
    limit: int | None = None,
):
    try:
        q = sb.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit is not None:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception:
        return []


def safe_single(sb, schema: str, table: str, cols: str = "*", **eq_filters):
    try:
        q = sb.schema(schema).table(table).select(cols)
        for k, v in eq_filters.items():
            q = q.eq(k, v)
        return (q.limit(1).execute().data or [{}])[0]
    except Exception:
        return {}


def format_date(x) -> str:
    if not x:
        return "N/A"
    try:
        return str(x)[:10]
    except Exception:
        return str(x)


def fmt_money(x) -> str:
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return "0"


def _as_float(x) -> float:
    try:
        return float(x or 0)
    except Exception:
        return 0.0


# -------------------------
# Main dashboard render
# -------------------------
def render_dashboard(sb_anon, sb_service, schema: str):
    st.title("🏦 Meeting Dashboard")
    st.caption("Standard Njangi operations view — rotation, finance health, and meeting flow.")

    # =========================================================
    # Scope toggle (standard UX pattern)
    # =========================================================
    scope = st.radio("Scope", ["All-time", "This session"], horizontal=True, key="dash_scope")
    st.caption("Tip: Start with All-time. Use This session for meeting-only checks (optional).")

    # =========================================================
    # Members
    # =========================================================
    members = safe_select(sb_anon, schema, "members_legacy", "id,name,position", order_by="id")
    df_members = pd.DataFrame(members) if members else pd.DataFrame(columns=["id", "name", "position"])
    member_count = len(df_members) if not df_members.empty else 0

    # =========================================================
    # ✅ Canonical rotation view (single source of truth)
    # =========================================================
    dash = safe_single(sb_anon, schema, "dashboard_next_view", "*")

    next_idx = dash.get("next_payout_index")
    next_date = dash.get("next_payout_date")
    next_beneficiary = dash.get("next_beneficiary")
    session_number = dash.get("session_number")
    session_id = dash.get("session_id")
    rotation_start_date = dash.get("rotation_start_date")
    current_pot = dash.get("current_pot")
    already_paid = dash.get("already_paid")

    # =========================================================
    # ✅ Finance view (All-time truth)
    # =========================================================
    fin = safe_single(sb_anon, schema, "dashboard_finance_view", "*")

    # =========================================================
    # TOP KPI STRIP (Rotation)
    # =========================================================
    st.markdown("## 📌 Rotation Overview")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Members", f"{member_count:,}")
    c2.metric("Session #", str(session_number or "N/A"))
    c3.metric("Next Payout Index", str(next_idx or "N/A"))
    c4.metric("Next Payout Date", format_date(next_date))
    c5.metric("Pot Amount (this session)", fmt_money(current_pot) if current_pot not in (None, "") else "N/A")

    st.caption(
        f"Session UUID: {session_id or 'N/A'} • Rotation start: {format_date(rotation_start_date)} • "
        f"Already paid: {'Yes' if bool(already_paid) else 'No'}"
    )

    # =========================================================
    # FINANCE KPI STRIP (Standard bank dashboard look)
    # =========================================================
    st.markdown("## 💰 Finance Health")
    f1, f2, f3, f4, f5 = st.columns(5)
    f1.metric("Foundation Paid", fmt_money(fin.get("total_foundation_paid")))
    f2.metric("Foundation Unpaid", fmt_money(fin.get("total_foundation_unpaid")))
    f3.metric("Fines Paid", fmt_money(fin.get("total_fines_paid")))
    f4.metric("Fines Unpaid", fmt_money(fin.get("total_fines_unpaid")))
    f5.metric("Interest Generated", fmt_money(fin.get("total_interest_generated")))

    st.divider()

    # =========================================================
    # PROGRESS BAR (Pot funding)
    # =========================================================
    st.markdown("## 📈 Pot Funding Progress")
    base_contrib = 500  # default rule; adjust if needed
    expected_pot = member_count * base_contrib if member_count else 0
    cur_pot_val = _as_float(current_pot)
    if expected_pot > 0:
        pct = min(cur_pot_val / float(expected_pot), 1.0)
        st.progress(pct)
        st.caption(f"{fmt_money(cur_pot_val)} collected of expected {fmt_money(expected_pot)} (base {base_contrib} × {member_count} members)")
    else:
        st.info("Expected pot cannot be computed (no members loaded).")

    st.divider()

    # =========================================================
    # MAIN 2-COLUMN LAYOUT (Next Beneficiary + Ops)
    # =========================================================
    left, right = st.columns([1.2, 0.8])

    with left:
        st.markdown("## 🎯 Next Beneficiary")
        if next_beneficiary:
            st.success(str(next_beneficiary))
        else:
            st.info("N/A (will appear when rotation state is initialized)")

        st.markdown("## ✅ Meeting Checklist")
        # Attendance/minutes for TODAY (legacy tables)
        today_str = str(date.today())
        att_today = safe_select(sb_anon, schema, "meeting_attendance_legacy", "id", limit=1, meeting_date=today_str)  # may fail silently
        # fallback robust approach
        att_today_rows = (
            safe_select(sb_anon, schema, "meeting_attendance_legacy", "id,meeting_date", order_by="created_at", desc=True, limit=300)
        )
        att_today_ok = any(str(r.get("meeting_date", ""))[:10] == today_str for r in att_today_rows)

        minutes_rows = safe_select(sb_anon, schema, "meeting_minutes_legacy", "id,meeting_date", order_by="meeting_date", desc=True, limit=50)
        minutes_today_ok = any(str(r.get("meeting_date", ""))[:10] == today_str for r in minutes_rows)

        st.checkbox("Attendance recorded (today)", value=bool(att_today_ok), disabled=True)
        st.checkbox("Minutes saved (today)", value=bool(minutes_today_ok), disabled=True)
        st.checkbox("Payout executed (this session)", value=bool(already_paid), disabled=True)

        st.markdown("## 👥 Rotation Preview (Top 8)")
        if not df_members.empty:
            if "position" in df_members.columns and df_members["position"].notna().any():
                dfp = df_members.sort_values("position", ascending=True).head(8)
                cols = ["id", "name", "position"]
            else:
                dfp = df_members.sort_values("id", ascending=True).head(8)
                cols = ["id", "name"]
            st.dataframe(dfp[cols], use_container_width=True, hide_index=True)
        else:
            st.warning("No members loaded.")

    with right:
        st.markdown("## 🧭 Meeting Controls")
        st.caption("Service key required for write actions.")

        if not sb_service:
            st.warning("Service key not configured. Admin actions disabled.")
        else:
            if st.button("✅ Initialize app_state (id=1)", use_container_width=True):
                try:
                    sb_service.schema(schema).table("app_state").upsert({"id": 1, "next_payout_index": 1}).execute()
                    st.success("Initialized. Refresh dashboard.")
                    st.cache_data.clear()
                    st.cache_resource.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Init failed: {e}")

            st.markdown("### Quick Overrides")
            new_idx = st.number_input("Set next_payout_index", min_value=1, step=1, value=int(next_idx or 1))
            if st.button("💾 Save next_payout_index", use_container_width=True):
                try:
                    sb_service.schema(schema).table("app_state").upsert({"id": 1, "next_payout_index": int(new_idx)}).execute()
                    st.success("Saved. Refresh.")
                    st.cache_data.clear()
                    st.cache_resource.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Save failed: {e}")

            if st.button("🔄 Refresh data (clear cache)", use_container_width=True):
                st.cache_data.clear()
                st.cache_resource.clear()
                st.rerun()

    st.divider()

    # =========================================================
    # LATEST ACTIVITY FEED (Standard dashboard feature)
    # =========================================================
    st.markdown("## 🧾 Latest Activity")

    activity: list[dict] = []

    # Minutes
    for r in safe_select(sb_anon, schema, "meeting_minutes_legacy", "id,meeting_date,title,created_at,created_by", order_by="created_at", desc=True, limit=10):
        activity.append({
            "time": str(r.get("created_at") or "")[:19],
            "type": "Minutes",
            "detail": f"{str(r.get('meeting_date') or '')[:10]} • {r.get('title') or ''}",
            "by": r.get("created_by") or "",
        })

    # Attendance
    for r in safe_select(sb_anon, schema, "meeting_attendance_legacy", "id,meeting_date,legacy_member_id,status,created_at,created_by", order_by="created_at", desc=True, limit=10):
        activity.append({
            "time": str(r.get("created_at") or "")[:19],
            "type": "Attendance",
            "detail": f"{str(r.get('meeting_date') or '')[:10]} • Member {r.get('legacy_member_id')} • {r.get('status')}",
            "by": r.get("created_by") or "",
        })

    # Loan repayments (new strict table)
    for r in safe_select(sb_anon, schema, "loan_repayments", "id,loan_id,member_id,amount,paid_at,created_at", order_by="created_at", desc=True, limit=10):
        activity.append({
            "time": str(r.get("created_at") or r.get("paid_at") or "")[:19],
            "type": "Loan Repayment",
            "detail": f"Loan {r.get('loan_id')} • Member {r.get('member_id')} • {fmt_money(r.get('amount'))}",
            "by": "",
        })

    # Sort activity feed by time string (best effort)
    activity = sorted(activity, key=lambda x: x.get("time") or "", reverse=True)[:20]

    if not activity:
        st.info("No recent activity found.")
    else:
        st.dataframe(pd.DataFrame(activity), use_container_width=True, hide_index=True)

    st.divider()

    # =========================================================
    # Member lookup / details (standard)
    # =========================================================
    st.markdown("## 🔎 Member Lookup")
    if not df_members.empty:
        df_members = df_members.copy()
        df_members["label"] = df_members.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
        pick = st.selectbox("Select member", df_members["label"].tolist(), key="dash_member_pick")
        row = df_members[df_members["label"] == pick].iloc[0].to_dict()
        st.write("Selected member id:", row.get("id"))
        st.write("Selected member:", row.get("name"))
    else:
        st.info("No members available for lookup.")

    with st.expander("Member Registry (preview)"):
        if not df_members.empty:
            st.dataframe(df_members[["id", "name", "position"]], use_container_width=True, hide_index=True)
        else:
            st.info("No members to show.")
