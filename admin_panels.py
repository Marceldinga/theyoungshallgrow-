
# admin_panels.py ✅ UPDATED (NO LEGACY) — Members(+phone) + Rotation(session-based) + Contributions(ONE/member/session) + Foundation + Fines
# ✅ Uses NEW tables only:
#   - members(id,name,phone,created_at)
#   - app_state(id=1, current_session_id, next_member_id, updated_at, ...)
#   - sessions(id OR session_id, start_date, end_date)
#   - contributions(member_id, session_id, amount, paid_at, note, created_at, updated_at)  ✅ ONE PER MEMBER PER SESSION (UPSERT)
#   - foundation_contributions(member_id, session_id, amount, paid_at, note, created_at, updated_at) ✅ (UPSERT recommended)
#   - fines(member_id, amount, reason, status, paid_at, created_at, updated_at)  (if you have this table; otherwise it will show errors)
#   - audit_log (optional; silent if schema differs)
#
# ✅ Member identity: transactions store member_id only (no member_name stored)
# ✅ Members tab: add member name + phone; list members including phone
# ✅ Rotation tab: sets app_state.current_session_id and app_state.next_member_id (session-based, no payout_index)
# ✅ Contributions tab: saves contribution via UPSERT on (session_id, member_id) to prevent duplicates
# ✅ No duplicate tabs inside Admin

from __future__ import annotations

from datetime import datetime, timezone, date
import streamlit as st
import pandas as pd
from postgrest.exceptions import APIError


# ============================================================
# Helpers
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


def safe_select(sb_service, schema: str, table: str, cols: str = "*", order_by: str | None = None, desc: bool = False, limit: int | None = None, **eq_filters):
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


def safe_single(sb_service, schema: str, table: str, cols: str = "*", **eq_filters) -> dict:
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


def clean_name(name: str) -> str:
    # supports multiple names; collapses extra spaces
    return " ".join((name or "").strip().split())


def clean_phone(phone: str) -> str | None:
    p = (phone or "").strip()
    return p if p else None


# ============================================================
# Audit logging (optional; silent if schema differs)
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
        pass


# ============================================================
# Sessions PK detection (sessions.id OR sessions.session_id)
# ============================================================
def sessions_pk(sb_service, schema: str) -> str:
    sample = safe_single(sb_service, schema, "sessions", "*")
    if sample:
        if "session_id" in sample:
            return "session_id"
        if "id" in sample:
            return "id"
    return "id"


def load_sessions(sb_service, schema: str) -> pd.DataFrame:
    pk = sessions_pk(sb_service, schema)
    cols = f"{pk},start_date,end_date"
    rows = safe_select(sb_service, schema, "sessions", cols, order_by=pk, desc=False, limit=5000)
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=[pk, "start_date", "end_date"])
    if not df.empty:
        df[pk] = pd.to_numeric(df[pk], errors="coerce").fillna(0).astype(int)
    return df


# ============================================================
# Members
# ============================================================
def load_members(sb_service, schema: str) -> pd.DataFrame:
    rows = safe_select(sb_service, schema, "members", "id,name,phone,created_at", order_by="id", desc=False, limit=5000)
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["id", "name", "phone", "created_at"])
    if not df.empty:
        df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
        df["name"] = df["name"].astype(str)
        if "phone" in df.columns:
            df["phone"] = df["phone"].astype(str).replace({"None": "", "nan": ""})
    return df


def panel_members(sb_service, schema: str, actor_email: str):
    st.subheader("Members (Admin)")
    st.caption(f"Members table in use: **{schema}.members**")

    st.markdown("### Add New Member")
    name = st.text_input("Member name", value="", placeholder="e.g., Marcel Dinga", key="member_add_name")
    phone = st.text_input("Phone number", value="", placeholder="e.g., +1 405-845-8002", key="member_add_phone")

    if st.button("✅ Add Member", width="stretch", key="member_add_btn"):
        name_clean = clean_name(name)
        phone_clean = clean_phone(phone)

        if not name_clean:
            st.error("Member name is required.")
            return

        payload = {
            "name": name_clean,
            "phone": phone_clean,
            "created_at": now_iso(),
        }

        ok = safe_insert(sb_service, schema, "members", payload)
        if ok:
            audit_log(
                sb_service, schema,
                action="member_inserted",
                status="ok",
                table_name="members",
                entity="member",
                details=f"Added member name={name_clean} phone={phone_clean or ''}",
                payload=payload,
                actor_email=actor_email,
                actor_role="admin",
            )
            st.success("Member added.")
            st.cache_data.clear()
            st.rerun()

    st.divider()
    st.markdown("### Current Members")
    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.info("No members found.")
        return
    st.dataframe(dfm, width="stretch", hide_index=True)

    # Optional: edit phone quickly
    st.divider()
    st.markdown("### Update Member Phone (Quick Edit)")
    labels = [f"{int(r['id']):02d} • {r['name']}" for _, r in dfm.iterrows()]
    label_to_id = dict(zip(labels, dfm["id"].tolist()))
    pick = st.selectbox("Select member", labels, key="member_edit_pick")
    new_phone = st.text_input("New phone", value="", placeholder="e.g., +1 405-845-8002", key="member_edit_phone")

    if st.button("💾 Save Phone Update", width="stretch", key="member_edit_save"):
        mid = int(label_to_id[pick])
        phone_clean = clean_phone(new_phone)

        ok = safe_update(sb_service, schema, "members", {"phone": phone_clean}, {"id": mid})
        if ok:
            audit_log(
                sb_service, schema,
                action="member_phone_updated",
                status="ok",
                table_name="members",
                row_pk=str(mid),
                entity="member",
                entity_id=str(mid),
                details=f"Updated phone for member_id={mid} phone={phone_clean or ''}",
                payload={"id": mid, "phone": phone_clean},
                actor_email=actor_email,
                actor_role="admin",
            )
            st.success("Phone updated.")
            st.cache_data.clear()
            st.rerun()


# ============================================================
# app_state
# ============================================================
def ensure_app_state(sb_service, schema: str) -> dict:
    state = safe_single(sb_service, schema, "app_state", "*", id=1)
    if state and any(state.values()):
        return state
    ok = safe_upsert(sb_service, schema, "app_state", {"id": 1, "updated_at": now_iso()})
    if ok:
        return safe_single(sb_service, schema, "app_state", "*", id=1)
    return {}


# ============================================================
# Rotation (session-based)
# ============================================================
def panel_rotation_state(sb_service, schema: str, actor_email: str):
    st.subheader("Rotation State (Session-based)")

    state = ensure_app_state(sb_service, schema)
    current_session_id = state.get("current_session_id")
    next_member_id = state.get("next_member_id")
    updated_at = state.get("updated_at") or "N/A"

    st.info(
        f"**current_session_id:** {current_session_id}\n\n"
        f"**next_member_id:** {next_member_id}\n\n"
        f"**updated_at:** {updated_at}"
    )

    st.markdown("### Change Control (Override)")
    st.caption("Organizational rule: every override requires confirmation + reason and is audit-logged.")

    # pick session
    pk = sessions_pk(sb_service, schema)
    dfs = load_sessions(sb_service, schema)
    if dfs.empty:
        st.warning("No sessions found in sessions table.")
        return

    dfs["label"] = dfs.apply(lambda r: f"{int(r[pk]):04d} • {r.get('start_date','')} → {r.get('end_date','')}", axis=1)
    sess_pick = st.selectbox("Set current session", dfs["label"].tolist(), key="rot_session_pick")
    sess_id = int(dfs.loc[dfs["label"] == sess_pick, pk].iloc[0])

    # pick next member
    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.warning("No members found.")
        return
    dfm["label"] = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
    mem_pick = st.selectbox("Set next beneficiary (member)", dfm["label"].tolist(), key="rot_member_pick")
    mem_id = int(dfm.loc[dfm["label"] == mem_pick, "id"].iloc[0])

    reason = st.text_input("Reason for override (required)", value="", placeholder="e.g., new cycle started, correction", key="rot_reason")
    confirm = st.checkbox("I confirm this override is intentional and approved.", key="rot_confirm")

    if st.button("💾 Save Rotation Override", width="stretch", key="rot_save"):
        if not confirm:
            st.error("Confirmation required.")
            return
        if not reason.strip():
            st.error("Reason is required for organizational audit.")
            return

        payload = {
            "id": 1,
            "current_session_id": int(sess_id),
            "next_member_id": int(mem_id),
            "updated_at": now_iso(),
        }
        ok = safe_upsert(sb_service, schema, "app_state", payload)
        if ok:
            audit_log(
                sb_service, schema,
                action="override_rotation_state",
                status="ok",
                table_name="app_state",
                row_pk="1",
                entity="rotation",
                entity_id=f"session={sess_id},next_member={mem_id}",
                details=f"Set current_session_id={sess_id}, next_member_id={mem_id}. Reason: {reason}",
                payload={"from": {"current_session_id": current_session_id, "next_member_id": next_member_id}, "to": payload, "reason": reason},
                actor_email=actor_email,
                actor_role="admin",
            )
            st.success("Rotation state updated.")
            st.cache_data.clear()
            st.rerun()


# ============================================================
# Contributions (ONE per member per session) — UPSERT
# ============================================================
def panel_contributions(sb_service, schema: str, actor_email: str):
    st.subheader("Contributions (Admin Entry)")
    st.caption("Rule: ONE contribution per member per session. Saving again updates the existing row (UPSERT).")

    state = ensure_app_state(sb_service, schema)
    current_session_id = state.get("current_session_id")

    if current_session_id is None:
        st.warning("app_state.current_session_id is not set. Set it in Rotation tab first.")
        return

    st.caption(f"Current session_id (from app_state.id=1): **{current_session_id}**")
    st.caption("Rule: amount must be **>= 500** and a **multiple of 500**.")

    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.warning("No members found.")
        return

    dfm["label"] = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
    labels = dfm["label"].tolist()
    label_to_id = dict(zip(dfm["label"], dfm["id"]))

    col1, col2 = st.columns([1, 1])

    with col1:
        pick = st.selectbox("Member", labels, key="contrib_member")
        amt = st.number_input("Amount", min_value=0, step=500, value=500, key="contrib_amount")
        note = st.text_input("Note (optional)", value="", key="contrib_note")

        mid = int(label_to_id[pick])

        if st.button("✅ Save Contribution", width="stretch", key="contrib_save"):
            if not is_multiple_of_500(int(amt)):
                st.error("Amount must be >= 500 and multiple of 500.")
                return

            payload = {
                "member_id": mid,
                "session_id": int(current_session_id),
                "amount": int(amt),
                "paid_at": now_iso(),
                "note": note.strip() or None,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }

            ok = safe_upsert(sb_service, schema, "contributions", payload)
            if ok:
                audit_log(
                    sb_service, schema,
                    action="contribution_upserted",
                    status="ok",
                    table_name="contributions",
                    entity="contribution",
                    entity_id=str(mid),
                    details=f"Contribution upserted member_id={mid} session_id={current_session_id} amount={int(amt)}",
                    payload=payload,
                    actor_email=actor_email,
                    actor_role="admin",
                )
                st.success("Contribution saved (upsert).")
                st.cache_data.clear()
                st.rerun()

    with col2:
        st.markdown("### Bulk Entry (optional)")
        st.caption("Enter amounts for many members. Zero means skip. Saving updates existing rows (UPSERT).")

        df_bulk = dfm[["id", "name"]].copy()
        df_bulk["amount"] = 0
        edited = st.data_editor(
            df_bulk,
            hide_index=True,
            width="stretch",
            column_config={"amount": st.column_config.NumberColumn("amount", step=500, min_value=0)},
            key="contrib_bulk_editor",
        )

        if st.button("✅ Save Bulk Contributions", width="stretch", key="contrib_bulk_save"):
            errors = []
            saved = 0
            for _, r in edited.iterrows():
                mid = int(r["id"])
                amt = int(r["amount"] or 0)
                if amt <= 0:
                    continue
                if not is_multiple_of_500(amt):
                    errors.append(f"{mid} {r['name']}: invalid amount {amt}")
                    continue

                payload = {
                    "member_id": mid,
                    "session_id": int(current_session_id),
                    "amount": int(amt),
                    "paid_at": now_iso(),
                    "note": None,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
                ok = safe_upsert(sb_service, schema, "contributions", payload)
                if ok:
                    saved += 1

            if errors:
                st.error("Some rows failed:\n- " + "\n- ".join(errors))
            if saved:
                audit_log(
                    sb_service, schema,
                    action="bulk_contributions_upserted",
                    status="ok",
                    table_name="contributions",
                    details=f"Bulk contributions saved={saved} session_id={current_session_id}",
                    payload={"saved": saved, "session_id": current_session_id},
                    actor_email=actor_email,
                    actor_role="admin",
                )
                st.success(f"Saved {saved} rows (upsert).")
                st.cache_data.clear()
                st.rerun()

    st.divider()
    st.markdown("### Contributions for current session")
    rows = safe_select(
        sb_service, schema, "v_contributions_with_member",
        "member_id,member_name,session_id,amount,paid_at,created_at,note",
        order_by="member_id", desc=False, limit=5000,
        session_id=int(current_session_id),
    )
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No contributions recorded for this session yet.")
    else:
        st.dataframe(df, width="stretch", hide_index=True)


# ============================================================
# Fines (optional table: fines)
# ============================================================
def panel_fines(sb_service, schema: str, actor_email: str):
    st.subheader("Fines (Admin)")
    st.caption("If you don't have public.fines table, this section will show errors until created.")

    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.warning("No members found.")
        return

    dfm["label"] = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
    pick = st.selectbox("Member", dfm["label"].tolist(), key="fine_member")
    mid = int(dfm[dfm["label"] == pick]["id"].iloc[0])

    amount = st.number_input("Fine amount", min_value=0.0, step=10.0, value=30.0, key="fine_amount")
    reason = st.text_input("Reason", value="Late payment", key="fine_reason")
    status = st.selectbox("Status", ["unpaid", "paid", "waived"], index=0, key="fine_status")

    paid_at = None
    if status == "paid":
        paid_at = st.date_input("Paid at", value=date.today(), key="fine_paid_at").isoformat()

    if st.button("✅ Save Fine", width="stretch", key="fine_save"):
        if amount <= 0:
            st.error("Fine amount must be > 0.")
            return

        payload = {
            "member_id": mid,
            "amount": float(amount),
            "reason": reason.strip(),
            "status": status,
            "paid_at": paid_at,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

        ok = safe_insert(sb_service, schema, "fines", payload)
        if ok:
            audit_log(
                sb_service, schema,
                action="fine_inserted",
                status="ok",
                table_name="fines",
                entity="fine",
                entity_id=str(mid),
                details=f"Fine recorded member_id={mid} amount={amount} status={status}",
                payload=payload,
                actor_email=actor_email,
                actor_role="admin",
            )
            st.success("Fine saved.")
            st.cache_data.clear()
            st.rerun()

    st.divider()
    st.markdown("### Recent fines")
    rows = safe_select(sb_service, schema, "fines", "*", order_by="created_at", desc=True, limit=300)
    st.dataframe(pd.DataFrame(rows) if rows else pd.DataFrame(), width="stretch", hide_index=True)


# ============================================================
# Foundation contributions (session-based)
# ============================================================
def panel_foundation(sb_service, schema: str, actor_email: str):
    st.subheader("Foundation (Admin)")
    st.caption("Session-based foundation contributions. Saving again updates existing row (upsert recommended).")

    state = ensure_app_state(sb_service, schema)
    current_session_id = state.get("current_session_id")
    if current_session_id is None:
        st.warning("app_state.current_session_id is not set. Set it in Rotation tab first.")
        return

    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.warning("No members found.")
        return

    dfm["label"] = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
    pick = st.selectbox("Member", dfm["label"].tolist(), key="foundation_member")
    mid = int(dfm[dfm["label"] == pick]["id"].iloc[0])

    amount = st.number_input("Amount", min_value=0.0, step=500.0, value=500.0, key="foundation_amount")
    note = st.text_input("Note (optional)", value="", key="foundation_note")
    paid_at = st.date_input("Paid at", value=date.today(), key="foundation_paid_at").isoformat()

    if st.button("✅ Save Foundation Contribution", width="stretch", key="foundation_save"):
        if amount <= 0:
            st.error("Amount must be > 0.")
            return

        payload = {
            "member_id": mid,
            "session_id": int(current_session_id),
            "amount": float(amount),
            "paid_at": paid_at,
            "note": note.strip() or None,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

        ok = safe_upsert(sb_service, schema, "foundation_contributions", payload)
        if ok:
            audit_log(
                sb_service, schema,
                action="foundation_contribution_upserted",
                status="ok",
                table_name="foundation_contributions",
                entity="foundation_contribution",
                entity_id=str(mid),
                details=f"Foundation contribution upserted member_id={mid} session_id={current_session_id} amount={amount}",
                payload=payload,
                actor_email=actor_email,
                actor_role="admin",
            )
            st.success("Foundation contribution saved (upsert).")
            st.cache_data.clear()
            st.rerun()

    st.divider()
    st.markdown("### Foundation contributions for current session")
    rows = safe_select(
        sb_service, schema, "foundation_contributions",
        "member_id,session_id,amount,paid_at,created_at,note",
        order_by="member_id", desc=False, limit=5000,
        session_id=int(current_session_id),
    )
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No foundation contributions recorded for this session yet.")
    else:
        st.dataframe(df, width="stretch", hide_index=True)


# ============================================================
# Main entry called from app router
# ============================================================
def render_admin(sb_service, schema: str, actor_email: str = ""):
    st.header("Admin (Service Key)")
    st.caption("Organizational standard: governed changes, confirmations, and audit logs.")

    if not sb_service:
        st.warning("Service key not configured.")
        return

    # System init (ensure app_state.id=1 exists)
    st.markdown("### System Initialization")
    if st.button("✅ Initialize app_state (id=1)", width="stretch", key="init_app_state"):
        ok = safe_upsert(sb_service, schema, "app_state", {"id": 1, "updated_at": now_iso()})
        if ok:
            audit_log(
                sb_service, schema,
                action="init_app_state",
                status="ok",
                table_name="app_state",
                row_pk="1",
                details="Initialized app_state id=1",
                payload={"id": 1},
                actor_email=actor_email,
                actor_role="admin",
            )
            st.success("Initialized.")
            st.cache_data.clear()
            st.rerun()

    st.divider()

    # ✅ Unique tab names = no duplicates inside Admin
    t_members, t_rot, t_contrib, t_fines, t_found = st.tabs(["Members", "Rotation", "Contributions", "Fines", "Foundation"])

    with t_members:
        panel_members(sb_service, schema, actor_email)

    with t_rot:
        panel_rotation_state(sb_service, schema, actor_email)

    with t_contrib:
        panel_contributions(sb_service, schema, actor_email)

    with t_fines:
        panel_fines(sb_service, schema, actor_email)

    with t_found:
        panel_foundation(sb_service, schema, actor_email)
