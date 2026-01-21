
# audit_panel.py
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import date, timedelta


def _safe_select(sb_service, schema: str, table: str, cols: str = "*", limit: int = 500):
    try:
        rows = (
            sb_service.schema(schema)
            .table(table)
            .select(cols)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
        return rows
    except Exception as e:
        st.error(f"Failed reading {schema}.{table}")
        st.code(str(e), language="text")
        return []


def render_audit(sb_service, schema: str):
    st.header("Audit / Meeting Minutes")
    st.caption("Compliance view: filter audit_log, review actions, export to CSV.")

    if sb_service is None:
        st.warning("Service key not configured. Audit requires service access.")
        return

    # --- Filters
    col1, col2, col3 = st.columns([1.2, 1.2, 1.6])

    with col1:
        days_back = st.selectbox("Date range", [1, 7, 14, 30, 90, 180], index=3)
    with col2:
        status_filter = st.selectbox("Status", ["all", "ok", "fail"], index=0)
    with col3:
        action_contains = st.text_input("Action contains", value="", placeholder="e.g., payout, contribution, loan")

    start = date.today() - timedelta(days=int(days_back))
    end = date.today()

    # Pull a larger window then filter in pandas
    rows = _safe_select(sb_service, schema, "audit_log", "*", limit=2000)
    df = pd.DataFrame(rows)

    if df.empty:
        st.info("No audit_log entries found (or audit_log not readable).")
        return

    # ============================================================
    # ✅ FIX: timezone-safe datetime parsing + filtering
    # ============================================================
    if "created_at" in df.columns:
        # Force UTC to avoid tz-aware vs tz-naive comparison crash
        df["created_at_dt"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
        df = df.dropna(subset=["created_at_dt"]).copy()

        # Convert start/end to UTC too
        start_dt = pd.to_datetime(start, utc=True)
        end_dt = pd.to_datetime(end, utc=True) + pd.Timedelta(days=1)  # include end date

        df = df[(df["created_at_dt"] >= start_dt) & (df["created_at_dt"] <= end_dt)]
        df = df.sort_values("created_at_dt", ascending=False)
    else:
        st.warning("audit_log has no created_at column. Cannot filter by date reliably.")

    # Status filter
    if status_filter != "all" and "status" in df.columns:
        df = df[df["status"].astype(str).str.lower() == status_filter]

    # Action contains filter
    if action_contains.strip() and "action" in df.columns:
        s = action_contains.strip().lower()
        df = df[df["action"].astype(str).str.lower().str.contains(s)]

    # Display
    st.subheader("Filtered Results")
    show_cols = [
        c for c in [
            "created_at", "actor_email", "actor_role", "action",
            "table_name", "row_pk", "entity", "entity_id",
            "status", "details"
        ]
        if c in df.columns
    ]
    st.dataframe(df[show_cols] if show_cols else df, use_container_width=True, hide_index=True)

    # CSV export
    st.divider()
    st.subheader("Export")
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download CSV",
        csv_bytes,
        file_name=f"audit_log_{start.isoformat()}_to_{end.isoformat()}.csv",
        mime="text/csv",
        use_container_width=True,
    )
