# admin_panels.py ✅ ORGANIZATIONAL STANDARD ADMIN PANEL (SERVICE KEY) — UPDATED (Streamlit width=)
from __future__ import annotations

from datetime import datetime, timezone, date
import streamlit as st
import pandas as pd
from postgrest.exceptions import APIError


# ============================================================
# Helpers (org standard)
# ============================================================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _api_error_payload(e: Exception) -> dict:
    try:
        if isinstance(e, APIError) and getattr(e, "args", None) and isinstance(e.args[0], dict):
            return e.args[0]
    except Exception:
        pass
    return {"message": str(e)}

def show_api_error(e: Exception, title: str = "Supabase error"):
    st.error(title)
    st.code(str(_api_error_payload(e)), language="text")

def safe_select(sb_service, schema: str, table: str, cols: str="*", order_by: str|None=None, desc: bool=False, limit: int|None=None, **eq_filters):
    try:
        q = sb_service.schema(schema).table(table).select(cols)
        for k, v in eq_filters.items():
            if v is None:
                continue
            q = q.eq(k, v)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit is not None:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception as e:
        show_api_error(e, f"Read failed: {schema}.{table}")
        return []

def safe_single(sb_service, schema: str, table: str, cols: str="*", **eq_filters) -> dict:
    rows = safe_select(sb_service, schema, table, cols, limit=1, **eq_filters)
    return rows[0] if rows else {}

def safe_insert(sb_service, schema: str, table: str, payload: dict) -> bool:
    try:
        sb_service.schema(schema).table(table).insert(payload).execute()
        return True
    except Exception as e:
        show_api_error(e, f"Insert failed: {schema}.{table}")
        return False

def safe_upsert(sb_service, schema: str, table: str, payload: dict) -> bool:
    try:
        sb_service.schema(schema).table(table).upsert(payload).execute()
        return True
    except Exception as e:
        show_api_error(e, f"Upsert failed: {schema}.{table}")
        return False

def safe_update(sb_service, schema: str, table: str, payload: dict, where: dict) -> bool:
    try:
        q = sb_service.schema(schema).table(table).update(payload)
        for k, v in where.items():
            q = q.eq(k, v)
        q.execute()
        return True
    except Exception as e:
        show_api_error(e, f"Update failed: {schema}.{table}")
        return False

def is_multiple_of_500(x: int) -> bool:
    return x >= 500 and x % 500 == 0


# ============================================================
# Audit logging (organizational requirement)
# ============================================================
def audit_log(
    sb_service,
    schema: str,
    action: str,
    status: str,
    table_name: str = "",
    row_pk: str = "",
    entity: str = "",
    entity_id: str = "",
    details: str = "",
    payload: dict | None = None,
    actor_email: str = "",
    actor_role: str = "admin",
):
    """
    Writes to audit_log if present. If audit_log table is missing, it silently skips.
    """
    try:
        record = {
            "created_at": now_iso(),
            "actor_email": actor_email,
            "actor_role": actor_role,
            "action": action,
            "table_name": table_name,
            "row_pk": row_pk,
            "entity": entity,
            "entity_id": entity_id,
            "details": details,
            "status": status,
            "payload": payload or {},
        }
        sb_service.schema(schema).table("audit_log").insert(record).execute()
    except Exception:
        # Do not break admin workflow if audit table has slightly different schema
        pass


# ============================================================
# Data loaders
# ============================================================
def load_members(sb_service, schema: str) -> pd.DataFrame:
    rows = safe_select(sb_service, schema, "members_legacy", "id,name,position", order_by="id", desc=False, limit=5000)
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["id","name","position"])
    if not df.empty:
        df["id"] = pd.to_numeric(df["id"], errors="coerce")
        df = df.dropna(subset=["id"]).copy()
        df["id"] = df["id"].astype(int)
        df["name"] = df["name"].astype(str)
        if "position" in df.columns:
            df["position"] = pd.to_numeric(df["position"], errors="coerce")
    return df

def ensure_app_state(sb_service, schema: str) -> dict:
    """
    Ensures app_state has id=1 row.
    """
    state = safe_single(sb_service, schema, "app_state", "*", id=1)
    if state and any(state.values()):
        return state
    ok = safe_upsert(sb_service, schema, "app_state", {"id": 1, "next_payout_index": 1})
    if ok:
        return safe_single(sb_service, schema, "app_state", "*", id=1)
    return {}


# ============================================================
# Panels
# ============================================================
def panel_rotation_state(sb_service, schema: str, actor_email: str):
    st.subheader("Rotation State (Governed)")

    state = ensure_app_state(sb_service, schema)
    current_idx = int(state.get("next_payout_index") or 1)
    current_date = state.get("next_payout_date") or "N/A"
    rotation_start_index = state.get("rotation_start_index") or "N/A"
    rotation_start_date = state.get("rotation_start_date") or "N/A"

    st.info(
        f"**Current next_payout_index:** {current_idx}\n\n"
        f"**Current next_payout_date:** {current_date}\n\n"
        f"**rotation_start_index:** {rotation_start_index}\n\n"
        f"**rotation_start_date:** {rotation_start_date}"
    )

    st.markdown("### Change Control (Override)")
    st.caption("Organizational rule: every override requires confirmation + reason and is audit-logged.")

    new_idx = st.number_input("Set next_payout_index", min_value=1, step=1, value=current_idx)

    reason = st.text_input("Reason for override (required)", value="", placeholder="e.g., member absent, approved swap, correction")

    confirm = st.checkbox("I confirm this override is intentional and approved.")

    if st.button("💾 Save Rotation Override", width="stretch"):
        if not confirm:
            st.error("Confirmation required.")
            return
        if not reason.strip():
            st.error("Reason is required for organizational audit.")
            return

        ok = safe_upsert(sb_service, schema, "app_state", {"id": 1, "next_payout_index": int(new_idx), "updated_at": now_iso()})
        if ok:
            audit_log(
                sb_service, schema,
                action="override_next_payout_index",
                status="ok",
                table_name="app_state",
                row_pk="1",
                entity="rotation",
                entity_id=str(new_idx),
                details=f"Changed next_payout_index from {current_idx} to {int(new_idx)}. Reason: {reason}",
                payload={"from": current_idx, "to": int(new_idx), "reason": reason},
                actor_email=actor_email,
                actor_role="admin",
            )
            st.success("Rotation index updated.")
            st.cache_data.clear()
            st.rerun()


def panel_contributions(sb_service, schema: str, actor_email: str):
    st.subheader("Contributions (Admin Entry)")

    state = ensure_app_state(sb_service, schema)
    payout_index = int(state.get("next_payout_index") or 1)

    st.caption(f"Current payout_index (from app_state.id=1): **{payout_index}**")
    st.caption("Rule: amount must be **>= 500** and a **multiple of 500**. Kind must be paid/contributed.")

    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.warning("No members in members_legacy.")
        return

    dfm["label"] = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
    labels = dfm["label"].tolist()
    label_to_id = dict(zip(dfm["label"], dfm["id"]))
    label_to_name = dict(zip(dfm["label"], dfm["name"]))

    col1, col2 = st.columns([1, 1])

    with col1:
        pick = st.selectbox("Member", labels, key="contrib_member")
        amt = st.number_input("Amount", min_value=0, step=500, value=500, key="contrib_amount")
        kind = st.selectbox("Kind", ["paid", "contributed"], index=0, key="contrib_kind")

        mid = int(label_to_id[pick])
        mname = str(label_to_name[pick])

        if st.button("✅ Save Contribution", width="stretch", key="contrib_save"):
            if not is_multiple_of_500(int(amt)):
                st.error("Amount must be >= 500 and multiple of 500.")
                return

            payload = {
                "member_id": mid,
                "amount": int(amt),
                "kind": kind,
                "payout_index": payout_index,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }

            ok = safe_insert(sb_service, schema, "contributions_legacy", payload)
            if ok:
                audit_log(
                    sb_service, schema,
                    action="contribution_inserted",
                    status="ok",
                    table_name="contributions_legacy",
                    entity="contribution",
                    entity_id=str(mid),
                    details=f"Contribution recorded for member {mid} ({mname}) amount={int(amt)} kind={kind} payout_index={payout_index}",
                    payload=payload,
                    actor_email=actor_email,
                    actor_role="admin",
                )
                st.success("Contribution saved.")
                st.cache_data.clear()
                st.rerun()

    with col2:
        st.markdown("### Bulk Entry (optional)")
        st.caption("Enter amounts for many members, then Save Bulk. Zero means skip.")

        df_bulk = dfm[["id", "name"]].copy()
        df_bulk["amount"] = 0
        edited = st.data_editor(
            df_bulk,
            hide_index=True,
            width="stretch",
            column_config={
                "amount": st.column_config.NumberColumn("amount", step=500, min_value=0),
            },
            key="contrib_bulk_editor",
        )
        bulk_kind = st.selectbox("Bulk kind", ["paid", "contributed"], index=0, key="contrib_bulk_kind")

        if st.button("✅ Save Bulk Contributions", width="stretch", key="contrib_bulk_save"):
            errors = []
            saved = 0
            for _, r in edited.iterrows():
                mid = int(r["id"])
                mname = str(r["name"])
                amt = int(r["amount"] or 0)
                if amt <= 0:
                    continue
                if not is_multiple_of_500(amt):
                    errors.append(f"{mid} {mname}: invalid amount {amt}")
                    continue
                payload = {
                    "member_id": mid,
                    "amount": int(amt),
                    "kind": bulk_kind,
                    "payout_index": payout_index,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
                ok = safe_insert(sb_service, schema, "contributions_legacy", payload)
                if ok:
                    saved += 1

            if errors:
                st.error("Some rows failed:\n- " + "\n- ".join(errors))
            if saved:
                audit_log(
                    sb_service, schema,
                    action="bulk_contributions_inserted",
                    status="ok",
                    table_name="contributions_legacy",
                    details=f"Bulk contributions saved={saved} kind={bulk_kind} payout_index={payout_index}",
                    payload={"saved": saved, "kind": bulk_kind, "payout_index": payout_index},
                    actor_email=actor_email,
                    actor_role="admin",
                )
                st.success(f"Saved {saved} rows.")
                st.cache_data.clear()
                st.rerun()

    st.divider()
    st.markdown("### Contributions for current payout_index")
    rows = safe_select(
        sb_service, schema, "contributions_legacy",
        "member_id,amount,kind,payout_index,created_at",
        order_by="member_id", desc=False, limit=5000,
        payout_index=payout_index,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No contributions recorded for this payout_index yet.")
    else:
        st.dataframe(df, width="stretch", hide_index=True)


def panel_fines(sb_service, schema: str, actor_email: str):
    st.subheader("Fines (Admin)")

    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.warning("No members in members_legacy.")
        return

    dfm["label"] = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
    pick = st.selectbox("Member", dfm["label"].tolist(), key="fine_member")
    mid = int(dfm[dfm["label"] == pick]["id"].iloc[0])
    mname = str(dfm[dfm["label"] == pick]["name"].iloc[0])

    amount = st.number_input("Fine amount", min_value=0.0, step=10.0, value=30.0, key="fine_amount")
    reason = st.text_input("Reason", value="Late payment", key="fine_reason")
    status = st.selectbox("Status", ["unpaid", "paid", "waived"], index=0, key="fine_status")

    paid_at = None
    if status == "paid":
        paid_at = st.date_input("Paid at", value=date.today(), key="fine_paid_at").isoformat()

    if st.button("✅ Save Fine", width="stretch"):
        if amount <= 0:
            st.error("Fine amount must be > 0.")
            return

        payload = {
            "member_id": mid,
            "member_name": mname,
            "amount": float(amount),
            "reason": reason.strip(),
            "status": status,
            "paid_at": paid_at,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

        ok = safe_insert(sb_service, schema, "fines_legacy", payload)
        if ok:
            audit_log(
                sb_service, schema,
                action="fine_inserted",
                status="ok",
                table_name="fines_legacy",
                entity="fine",
                entity_id=str(mid),
                details=f"Fine recorded for {mid} ({mname}) amount={amount} status={status}",
                payload=payload,
                actor_email=actor_email,
                actor_role="admin",
            )
            st.success("Fine saved.")
            st.cache_data.clear()
            st.rerun()

    st.divider()
    st.markdown("### Recent fines")
    rows = safe_select(sb_service, schema, "fines_legacy", "*", order_by="created_at", desc=True, limit=300)
    st.dataframe(pd.DataFrame(rows) if rows else pd.DataFrame(), width="stretch", hide_index=True)


def panel_foundation(sb_service, schema: str, actor_email: str):
    st.subheader("Foundation Payments (Admin)")

    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.warning("No members in members_legacy.")
        return

    dfm["label"] = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
    pick = st.selectbox("Member", dfm["label"].tolist(), key="foundation_member")
    mid = int(dfm[dfm["label"] == pick]["id"].iloc[0])

    amount_paid = st.number_input("amount_paid", min_value=0.0, step=500.0, value=500.0, key="foundation_paid")
    amount_pending = st.number_input("amount_pending", min_value=0.0, step=500.0, value=0.0, key="foundation_pending")
    status = st.selectbox("Status", ["paid", "pending", "partial"], index=0, key="foundation_status")
    date_paid = st.date_input("date_paid", value=date.today(), key="foundation_date_paid").isoformat()
    notes = st.text_input("notes (optional)", value="", key="foundation_notes")

    if st.button("✅ Save Foundation Payment", width="stretch"):
        payload = {
            "member_id": mid,
            "amount_paid": float(amount_paid),
            "amount_pending": float(amount_pending),
            "status": status,
            "date_paid": date_paid,
            "notes": notes.strip(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        ok = safe_insert(sb_service, schema, "foundation_payments_legacy", payload)
        if ok:
            audit_log(
                sb_service, schema,
                action="foundation_payment_inserted",
                status="ok",
                table_name="foundation_payments_legacy",
                entity="foundation_payment",
                entity_id=str(mid),
                details=f"Foundation payment member_id={mid} paid={amount_paid} pending={amount_pending} status={status}",
                payload=payload,
                actor_email=actor_email,
                actor_role="admin",
            )
            st.success("Foundation payment saved.")
            st.cache_data.clear()
            st.rerun()

    st.divider()
    st.markdown("### Recent foundation payments")
    rows = safe_select(sb_service, schema, "foundation_payments_legacy", "*", order_by="created_at", desc=True, limit=300)
    st.dataframe(pd.DataFrame(rows) if rows else pd.DataFrame(), width="stretch", hide_index=True)


# ============================================================
# Main entry called from app router
# ============================================================
def render_admin(sb_service, schema: str, actor_email: str = ""):
    """
    Call this from app.py router:
        from admin_panels import render_admin
        render_admin(sb_service, schema, actor_email="...optional...")
    """
    st.header("Admin (Service Key)")
    st.caption("Organizational standard: governed changes, confirmations, and audit logs.")

    if not sb_service:
        st.warning("Service key not configured.")
        return

    # Top-level meeting admin: initialize state
    st.markdown("### System Initialization")
    if st.button("✅ Initialize app_state (id=1)", width="stretch"):
        ok = safe_upsert(sb_service, schema, "app_state", {"id": 1, "next_payout_index": 1, "updated_at": now_iso()})
        if ok:
            audit_log(
                sb_service, schema,
                action="init_app_state",
                status="ok",
                table_name="app_state",
                row_pk="1",
                details="Initialized app_state id=1",
                payload={"id": 1, "next_payout_index": 1},
                actor_email=actor_email,
                actor_role="admin",
            )
            st.success("Initialized.")
            st.cache_data.clear()
            st.rerun()

    st.divider()

    # Tabs inside Admin (clean organization)
    t1, t2, t3, t4 = st.tabs(["Rotation", "Contributions", "Fines", "Foundation"])

    with t1:
        panel_rotation_state(sb_service, schema, actor_email)

    with t2:
        panel_contributions(sb_service, schema, actor_email)

    with t3:
        panel_fines(sb_service, schema, actor_email)

    with t4:
        panel_foundation(sb_service, schema, actor_email)
