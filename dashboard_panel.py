
# dashboard_panel.py ✅ FINAL SINGLE FILE (NO SQL, NO legacy)
# 🚫 NO page title here — app.py owns the header
# ✅ Attendance = Present / Absent COUNTS (deduped)
# ✅ Finance model preserved
# ✅ Auto-refresh on app_state change
# ✅ Safe import: render_dashboard exists

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

DUE_DAYS = 28


# ============================================================
# THEME (panel-level only, no header)
# ============================================================
def inject_dashboard_theme():
    st.markdown(
        """
        <style>
        .glass {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 18px;
            padding: 18px;
            box-shadow: 0 14px 45px rgba(0,0,0,0.45);
            backdrop-filter: blur(10px);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def glass_open() -> str:
    return "<div class='glass'>"


def glass_close() -> str:
    return "</div>"


# ============================================================
# SAFE HELPERS (NO SQL)
# ============================================================
def safe_table(sb, schema: str, table: str, cols="*", limit=20000, order_by=None, desc=True):
    try:
        q = sb.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=desc)
        q = q.limit(limit)
        return q.execute().data or []
    except Exception:
        return []


def safe_single(sb, schema: str, table: str, **eq):
    try:
        q = sb.schema(schema).table(table).select("*")
        for k, v in eq.items():
            q = q.eq(k, v)
        q = q.limit(1)
        r = q.execute().data
        return r[0] if r else {}
    except Exception:
        return {}


def table_exists(sb, schema: str, table: str) -> bool:
    try:
        sb.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def num(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


# ============================================================
# AUTO REFRESH (app_state)
# ============================================================
def _state_stamp(sb, schema: str) -> str:
    r = safe_single(sb, schema, "app_state", id=1)
    return "|".join(str(r.get(k) or "") for k in ["current_session_id", "next_member_id", "updated_at"])


def auto_refresh(sb, schema: str):
    stamp = _state_stamp(sb, schema)
    prev = st.session_state.get("_dash_state")
    st.session_state["_dash_state"] = stamp
    if prev and prev != stamp:
        st.cache_data.clear()
        st.rerun()


# ============================================================
# ATTENDANCE (COUNTS + DEDUPE)
# ============================================================
def dedupe_attendance(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df["_ts"] = pd.to_datetime(df.get("created_at"), errors="coerce")
    df["_id"] = pd.to_numeric(df.get("id"), errors="coerce")
    df = df.sort_values(["_ts", "_id"], ascending=[False, False])
    df = df.drop_duplicates(["member_id", "session_id"], keep="first")
    return df.drop(columns=["_ts", "_id"], errors="ignore")


def load_attendance_counts(sb, schema: str) -> pd.DataFrame:
    members = safe_table(sb, schema, "members", "id,name,display_name,phone", limit=5000)
    attendance = safe_table(sb, schema, "attendance", "*", limit=20000)

    dfm = pd.DataFrame(members)
    dfa = pd.DataFrame(attendance)

    if dfm.empty:
        return pd.DataFrame()

    dfm["member_name"] = dfm["display_name"].fillna(dfm["name"])
    if dfa.empty:
        dfm["present_count"] = 0
        dfm["absent_count"] = 0
        dfm["total_sessions"] = 0
        return dfm

    dfa["present"] = dfa.get("present", False).astype(bool)
    dfa = dedupe_attendance(dfa)

    grp = (
        dfa.groupby("member_id")
        .agg(
            total_sessions=("session_id", "nunique"),
            present_count=("present", "sum"),
        )
        .reset_index()
    )
    grp["absent_count"] = grp["total_sessions"] - grp["present_count"]

    out = dfm.merge(grp, left_on="id", right_on="member_id", how="left")
    out = out.fillna(0)

    return out[["member_name", "present_count", "absent_count", "total_sessions"]]


def render_attendance_chart(df: pd.DataFrame):
    if df.empty:
        st.info("No attendance data.")
        return

    top_n = st.slider("Show top N members", 5, min(50, len(df)), min(17, len(df)))
    df = df.sort_values("present_count", ascending=False).head(top_n)

    chart = df.set_index("member_name")[["present_count", "absent_count"]]
    chart.columns = ["Present", "Absent"]
    st.bar_chart(chart)


# ============================================================
# DASHBOARD ENTRY (NO HEADER HERE)
# ============================================================
def render_dashboard(sb_anon, sb_service, schema: str = "public"):
    inject_dashboard_theme()

    read_sb = sb_service or sb_anon
    auto_refresh(read_sb, schema)

    # --- Session info ---
    state = safe_single(read_sb, schema, "app_state", id=1)
    current_session_id = state.get("current_session_id")

    if not current_session_id:
        rows = safe_table(read_sb, schema, "sessions", "session_id", limit=1, order_by="session_id")
        current_session_id = rows[0]["session_id"] if rows else None

    # --- Attendance section ---
    st.markdown("### ✅ Attendance • All-time (Counts)")
    att_df = load_attendance_counts(read_sb, schema)
    render_attendance_chart(att_df)

    # --- Spacer for future panels ---
    st.markdown(glass_open(), unsafe_allow_html=True)
    st.caption("Financial KPIs continue below (header handled by app.py)")
    st.markdown(glass_close(), unsafe_allow_html=True)


# Typo safety
def render_dashbaord(sb_anon, sb_service, schema: str = "public"):
    return render_dashboard(sb_anon, sb_service, schema)


__all__ = ["render_dashboard", "render_dashbaord"]
