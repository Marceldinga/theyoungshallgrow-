
# health_panel.py ✅ UPDATED (NEW TABLES ONLY, schema-safe + fallbacks)
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import date


# ============================================================
# SAFE SELECT
# ============================================================
def _try_select(sb, schema: str, table: str, cols: str = "*", limit: int = 1):
    """
    Returns (ok: bool, message: str, rows: list[dict]).
    Never raises.
    """
    try:
        rows = (
            sb.schema(schema)
            .table(table)
            .select(cols)
            .limit(int(limit))
            .execute()
            .data
            or []
        )
        return True, f"OK ({len(rows)} row(s) sample)", rows
    except Exception as e:
        return False, str(e), []


def _table_exists_readable(sb, schema: str, table: str) -> bool:
    ok, _, _ = _try_select(sb, schema, table, "*", 1)
    return ok


def _pick_sessions_pk(sb, schema: str) -> str:
    """
    sessions might be keyed by session_id or id. Detect safely.
    """
    ok, _, rows = _try_select(sb, schema, "sessions", "*", 1)
    if not ok or not rows:
        return "session_id"
    sample = rows[0]
    if "session_id" in sample:
        return "session_id"
    if "id" in sample:
        return "id"
    return "session_id"


# ============================================================
# PANEL
# ============================================================
def render_health(sb_anon, sb_service, schema: str):
    st.header("System Health")
    st.caption("Operational checks for dashboard/state/views. Use this page to diagnose blanks quickly.")

    checks: list[dict] = []

    # --- Service Key / client check
    checks.append({
        "Check": "Service key configured",
        "Status": "PASS" if sb_service is not None else "FAIL",
        "Details": "Service client available" if sb_service is not None else "No service key → admin/write features disabled",
    })

    # --- Core tables (anon readable)
    for t, cols in [
        ("members", "id,name"),
        ("sessions", "*"),
        ("contributions", "member_id,session_id,amount,paid_at,created_at"),
        ("foundation_contributions", "member_id,session_id,amount,paid_at,created_at"),
        ("loans", "id,member_id,status,principal_current,unpaid_interest,total_interest_generated,total_due,borrow_date,created_at"),
        ("loan_payments", "loan_id,member_id,amount,paid_at,created_at"),
        ("payouts", "session_id,member_id,payout_amount,payout_date,created_at"),
    ]:
        ok, msg, _ = _try_select(sb_anon, schema, t, cols, 1)
        checks.append({
            "Check": f"Anon can read {t}",
            "Status": "PASS" if ok else "FAIL",
            "Details": msg,
        })

    # --- Optional view: v_next_beneficiary
    ok, msg, _ = _try_select(sb_anon, schema, "v_next_beneficiary", "*", 1)
    checks.append({
        "Check": "Anon can read v_next_beneficiary (optional)",
        "Status": "PASS" if ok else "WARN",
        "Details": msg if ok else "Optional: dashboard will fall back to members/app_state without it",
    })

    # --- app_state (service read preferred)
    if sb_service is not None:
        ok, msg, rows = _try_select(sb_service, schema, "app_state", "*", 5)
        details = msg
        if ok:
            has_id1 = any(str(r.get("id")) == "1" for r in rows)
            if has_id1:
                r1 = next((r for r in rows if str(r.get("id")) == "1"), {})
                details = (
                    f"{msg}; id=1 found; "
                    f"current_session_id={r1.get('current_session_id')}, "
                    f"next_member_id={r1.get('next_member_id')}, "
                    f"updated_at={r1.get('updated_at')}"
                )
            else:
                details = msg + "; id=1 NOT found (init required)"
        checks.append({
            "Check": "Service can read app_state (id=1 expected)",
            "Status": "PASS" if ok else "FAIL",
            "Details": details,
        })
    else:
        checks.append({
            "Check": "Service can read app_state (id=1 expected)",
            "Status": "SKIP",
            "Details": "No service client",
        })

    # --- Validate current session exists (based on app_state.current_session_id)
    session_pk = _pick_sessions_pk(sb_anon, schema)

    current_session_ok = False
    current_session_details = "Skipped (app_state not available)"

    if sb_service is not None:
        ok_state, _, state_rows = _try_select(sb_service, schema, "app_state", "*", 5)
        if ok_state and state_rows:
            r1 = next((r for r in state_rows if str(r.get("id")) == "1"), {})
            csid = r1.get("current_session_id")
            if csid is None or str(csid).strip() == "":
                current_session_ok = False
                current_session_details = "app_state.current_session_id is empty"
            else:
                ok_sess, msg_sess, sess_rows = _try_select(sb_anon, schema, "sessions", "*", 50)
                if ok_sess:
                    # does the session exist?
                    exists = any(str(r.get(session_pk)) == str(csid) for r in sess_rows)
                    current_session_ok = bool(exists)
                    current_session_details = (
                        f"{msg_sess}; current_session_id={csid}; "
                        f"match_on={session_pk}; exists={exists}"
                    )
                else:
                    current_session_ok = False
                    current_session_details = f"Cannot read sessions: {msg_sess}"

    checks.append({
        "Check": "Current session id points to an existing sessions row",
        "Status": "PASS" if current_session_ok else "WARN",
        "Details": current_session_details,
    })

    # --- Basic data sanity (optional)
    # contributions present?
    ok_c, msg_c, crows = _try_select(sb_anon, schema, "contributions", "amount,session_id,member_id", 200)
    if ok_c:
        total_amt = 0.0
        for r in crows:
            try:
                total_amt += float(r.get("amount") or 0)
            except Exception:
                pass
        msg_c = f"{msg_c}; sample_total_amount={total_amt:,.0f}"
    checks.append({
        "Check": "Contributions sample sanity",
        "Status": "PASS" if ok_c else "WARN",
        "Details": msg_c,
    })

    # loans present?
    ok_l, msg_l, lrows = _try_select(sb_anon, schema, "loans", "status,principal_current,principal", 200)
    if ok_l:
        active = 0
        for r in lrows:
            s = str(r.get("status") or "").lower().strip()
            if s in ("active", "open"):
                active += 1
        msg_l = f"{msg_l}; active/open_in_sample={active}"
    checks.append({
        "Check": "Loans sample sanity",
        "Status": "PASS" if ok_l else "WARN",
        "Details": msg_l,
    })

    # --- Render results
    df = pd.DataFrame(checks)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Quick Fixes (NEW SYSTEM)")

    st.markdown(
        f"""
**If Dashboard shows blanks (`—`)**  
- Confirm `members`, `sessions`, `contributions` are readable by **anon**.
- Confirm `app_state` has **id=1** and a valid `current_session_id`.

**If Current Pot is 0 but you have contributions**  
- Check `contributions.session_id` matches `app_state.current_session_id`.
- Verify session PK: this app auto-detects `{session_pk}`.

**If “Current Beneficiary” is blank**  
- Optional: create `v_next_beneficiary` (recommended).
- Or ensure `app_state.next_member_id` matches an existing `members.id`.

**If Admin features are disabled**  
- Add `SUPABASE_SERVICE_KEY` in secrets (Railway / Streamlit secrets).

**If app_state id=1 missing**  
- Insert/initialize one row with `id=1` and set `current_session_id`.
"""
    )
