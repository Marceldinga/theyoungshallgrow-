# health_panel.py
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import date


def _try_select(sb, schema: str, table: str, cols: str = "*", limit: int = 1):
    """
    Returns (ok: bool, message: str, rows: list[dict]).
    """
    try:
        rows = (
            sb.schema(schema)
            .table(table)
            .select(cols)
            .limit(limit)
            .execute()
            .data
            or []
        )
        return True, f"OK ({len(rows)} row(s) sample)", rows
    except Exception as e:
        return False, str(e), []


def render_health(sb_anon, sb_service, schema: str):
    st.header("System Health")
    st.caption("Operational checks for dashboard/views/state. Use this page to diagnose blank screens quickly.")

    checks = []

    # --- Key / client checks
    checks.append({
        "Check": "Service key configured",
        "Status": "PASS" if sb_service is not None else "FAIL",
        "Details": "Service client available" if sb_service is not None else "No service key → Admin/Payout/Loans disabled"
    })

    # --- Dashboard canonical view
    ok, msg, _ = _try_select(sb_anon, schema, "v_dashboard_rotation", "*", 1)
    checks.append({
        "Check": "Anon can read v_dashboard_rotation",
        "Status": "PASS" if ok else "FAIL",
        "Details": msg
    })

    # --- Contributions view
    ok, msg, _ = _try_select(sb_anon, schema, "contributions_with_member", "*", 1)
    checks.append({
        "Check": "Anon can read contributions_with_member",
        "Status": "PASS" if ok else "FAIL",
        "Details": msg
    })

    # --- Members table
    ok, msg, rows = _try_select(sb_anon, schema, "members_legacy", "id,name,position", 1)
    checks.append({
        "Check": "Anon can read members_legacy",
        "Status": "PASS" if ok else "FAIL",
        "Details": msg
    })

    # --- app_state singleton exists (service read preferred)
    if sb_service is not None:
        ok, msg, rows = _try_select(sb_service, schema, "app_state", "*", 5)
        details = msg
        # Try to confirm id=1 exists
        if ok:
            has_id1 = any(str(r.get("id")) == "1" for r in rows)
            details = msg + ("; id=1 found" if has_id1 else "; id=1 NOT found (init required)")
        checks.append({
            "Check": "Service can read app_state (id=1 expected)",
            "Status": "PASS" if ok else "FAIL",
            "Details": details
        })
    else:
        checks.append({
            "Check": "Service can read app_state (id=1 expected)",
            "Status": "SKIP",
            "Details": "No service client"
        })

    # --- sessions_legacy active session check
    # NOTE: your sessions_legacy has only id/start_date/end_date/status
    ok, msg, rows = _try_select(sb_anon, schema, "sessions_legacy", "id,start_date,end_date,status", 50)
    if ok:
        active_count = sum(1 for r in rows if str(r.get("status") or "").lower() == "active")
        msg = f"OK; active sessions={active_count}, total checked={len(rows)}"
    checks.append({
        "Check": "sessions_legacy readable + has active session",
        "Status": "PASS" if ok else "FAIL",
        "Details": msg
    })

    df = pd.DataFrame(checks)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Quick Fixes")

    st.markdown(
        """
**If Dashboard shows blanks (`—`)**  
- Ensure `v_dashboard_rotation` exists and `GRANT SELECT` to `anon`.

**If Contributions page is empty**  
- Ensure `contributions_with_member` exists and `GRANT SELECT` to `anon`.
- Confirm it joins `contributions_legacy.member_id` to `members_legacy.id`.

**If Admin/Payout/Loans disabled**  
- Add `SUPABASE_SERVICE_KEY` in secrets.

**If app_state missing id=1**  
- Use Admin → Initialize app_state (id=1).
"""
    )
