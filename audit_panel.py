
# audit_panel.py ✅ UPDATED (NO SQL) — robust audit_log viewer + export
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

import pandas as pd
import streamlit as st


# ============================================================
# SAFE SUPABASE READERS
# ============================================================
def _safe_select_order_fallback(
    sb_service,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 2000,
    order_candidates: Optional[List[str]] = None,
    desc: bool = True,
):
    """
    Try ordering by a list of candidate columns.
    If ordering fails (missing column), fall back gracefully.
    """
    order_candidates = order_candidates or ["created_at", "updated_at", "paid_at", "payout_date", "id"]

    last_err = None
    for col in order_candidates:
        try:
            rows = (
                sb_service.schema(schema)
                .table(table)
                .select(cols)
                .order(col, desc=desc)
                .limit(int(limit))
                .execute()
                .data
                or []
            )
            return rows
        except Exception as e:
            last_err = e

    # Final fallback: no order
    try:
        rows = (
            sb_service.schema(schema)
            .table(table)
            .select(cols)
            .limit(int(limit))
            .execute()
            .data
            or []
        )
        return rows
    except Exception as e:
        st.error(f"Failed reading {schema}.{table}")
        st.code(str(last_err or e), language="text")
        return []


def _best_datetime_column(df: pd.DataFrame) -> Optional[str]:
    """
    Choose the best datetime-like column available for filtering.
    """
    candidates = [
        "created_at",
        "createdAt",
        "timestamp",
        "time",
        "updated_at",
        "paid_at",
        "payout_date",
        "borrow_date",
        "date",
    ]
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _coerce_datetime_utc(s: pd.Series) -> pd.Series:
    """
    Parse datetimes safely; force UTC to avoid tz-aware vs tz-naive crashes.
    """
    return pd.to_datetime(s, errors="coerce", utc=True)


# ============================================================
# UI
# ============================================================
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
        action_contains = st.text_input(
            "Action contains",
            value="",
            placeholder="e.g., payout, contribution, loan",
        )

    start = date.today() - timedelta(days=int(days_back))
    end = date.today()

    # --- Load rows (robust ordering)
    rows = _safe_select_order_fallback(
        sb_service,
        schema,
        "audit_log",
        cols="*",
        limit=3000,
        order_candidates=["created_at", "updated_at", "id"],
        desc=True,
    )

    df = pd.DataFrame(rows)

    if df.empty:
        st.info("No audit_log entries found (or audit_log not readable).")
        return

    # ============================================================
    # DATE FILTERING (timezone-safe)
    # ============================================================
    dt_col = _best_datetime_column(df)

    if dt_col:
        df["_dt"] = _coerce_datetime_utc(df[dt_col])
        df = df.dropna(subset=["_dt"]).copy()

        start_dt = pd.to_datetime(start, utc=True)
        end_dt = pd.to_datetime(end, utc=True) + pd.Timedelta(days=1)  # include end date

        df = df[(df["_dt"] >= start_dt) & (df["_dt"] <= end_dt)].copy()
        df = df.sort_values("_dt", ascending=False)
    else:
        st.warning("No datetime column found (created_at/updated_at/etc). Date filter skipped.")

    # ============================================================
    # STATUS FILTER
    # ============================================================
    if status_filter != "all" and "status" in df.columns:
        df["status_norm"] = df["status"].astype(str).str.lower().fillna("")
        df = df[df["status_norm"] == status_filter].copy()

    # ============================================================
    # ACTION CONTAINS FILTER
    # ============================================================
    if action_contains.strip() and "action" in df.columns:
        s = action_contains.strip().lower()
        df["action_norm"] = df["action"].astype(str).str.lower().fillna("")
        df = df[df["action_norm"].str.contains(s, na=False)].copy()

    # ============================================================
    # QUICK KPIs
    # ============================================================
    total_rows = int(len(df))
    ok_rows = int((df.get("status", pd.Series([])).astype(str).str.lower() == "ok").sum()) if "status" in df.columns else 0
    fail_rows = int((df.get("status", pd.Series([])).astype(str).str.lower() == "fail").sum()) if "status" in df.columns else 0

    k1, k2, k3 = st.columns(3)
    with k1:
        st.metric("Records", total_rows)
    with k2:
        st.metric("OK", ok_rows)
    with k3:
        st.metric("Fail", fail_rows)

    # ============================================================
    # DISPLAY
    # ============================================================
    st.subheader("Filtered Results")

    preferred_cols = [
        dt_col if dt_col in df.columns else None,
        "actor_email",
        "actor_role",
        "action",
        "table_name",
        "row_pk",
        "entity",
        "entity_id",
        "status",
        "details",
    ]
    show_cols = [c for c in preferred_cols if c and c in df.columns]

    if show_cols:
        st.dataframe(df[show_cols], use_container_width=True, hide_index=True)
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

    # ============================================================
    # EXPORT (uses filtered df)
    # ============================================================
    st.divider()
    st.subheader("Export")
    export_df = df.drop(columns=[c for c in ["_dt", "status_norm", "action_norm"] if c in df.columns], errors="ignore")
    csv_bytes = export_df.to_csv(index=False).encode("utf-8")

    st.download_button(
        "⬇️ Download CSV",
        csv_bytes,
        file_name=f"audit_log_{start.isoformat()}_to_{end.isoformat()}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    # ============================================================
    # DEBUG (optional)
    # ============================================================
    with st.expander("🔎 Debug", expanded=False):
        st.write("schema", schema)
        st.write("rows_loaded", len(rows))
        st.write("datetime_column_used", dt_col)
        st.write("filtered_rows", len(df))
