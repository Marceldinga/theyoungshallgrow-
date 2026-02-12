
# admin_panels.py ✅ COMPLETE SINGLE CODE (NO LEGACY) — UPDATED DESIGN (Fintech Glass + Better Layout)
# ------------------------------------------------------------------------------
# ✅ Uses NEW tables only:
#   - members(id,name,phone,created_at)
#   - app_state(id=1, current_session_id, next_member_id, updated_at, ...)
#   - sessions(id OR session_id, start_date, end_date)
#   - contributions(member_id, session_id, amount, paid_at, note, created_at, updated_at)  ✅ ONE PER MEMBER PER SESSION (UPSERT)
#   - foundation_contributions(member_id, session_id, amount, paid_at, note, created_at, updated_at) ✅ (UPSERT)
#   - fines(member_id, amount, reason, status, paid_at, created_at, updated_at)  (optional)
#   - audit_log (optional; silent if schema differs)
#
# ✅ FIXED PHONE ISSUE:
#   - Normalizes phone before saving: "+1 405-845-8002" -> "+14058458002"
#   - Allows NULL phone
#   - Validates format: optional +, 10–15 digits
#
# ✅ UI / DESIGN UPDATE:
#   - Consistent "glass card" sections
#   - Cleaner headers + small captions
#   - Two-column layouts where helpful
#   - KPI-style metrics for state (session/next member)
#   - Form-based submits for fewer accidental writes
#   - Clear "Danger/Override" styling hints (text-based)
# ------------------------------------------------------------------------------

from __future__ import annotations

from datetime import datetime, timezone, date
import re
import streamlit as st
import pandas as pd
from postgrest.exceptions import APIError


# ============================================================
# UI helpers (design)
# ============================================================
def glass_open(title: str | None = None, subtitle: str | None = None) -> None:
    if title:
        st.markdown(f"### {title}")
    if subtitle:
        st.caption(subtitle)
    st.markdown("<div class='glass'>", unsafe_allow_html=True)


def glass_close() -> None:
    st.markdown("</div>", unsafe_allow_html=True)


def section_divider() -> None:
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    st.divider()


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


def safe_select(
    sb_service,
    schema: str,
    table: str,
    cols: str = "*",
    order_by: str | None = None,
    desc: bool = False,
    limit: int | None = None,
    **eq_filters,
):
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
    return " ".join((name or "").strip().split())


# -----------------------------
# ✅ Phone normalization/validation
# -----------------------------
_PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")


def clean_phone(phone: str) -> str | None:
    """
    Normalize to digits/+ only (E.164-ish) or None.
    Examples:
      "+1 405-845-8002" -> "+14058458002"
      "(405) 845-8002"  -> "4058458002"
      "" -> None
    """
    p = (phone or "").strip()
    if not p:
        return None
    p = re.sub(r"[^0-9+]", "", p)
    return p or None


def is_valid_phone(phone: str | None) -> bool:
    return phone is None or bool(_PHONE_RE.match(phone))


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
    glass_open("Members", f"Table: {schema}.members • Add, list, and update phone numbers (E.164-style).")

    # --- Add + Quick Edit in two columns
    col_add, col_edit = st.columns([1, 1], gap="large")

    with col_add:
        st.markdown("#### Add new member")
        with st.form("member_add_form", clear_on_submit=True):
            name = st.text_input("Member name", value="", placeholder="e.g., Marcel Dinga")
            phone = st.text_input("Phone (optional)", value="", placeholder="e.g., +1 405-845-8002")
            phone_clean_preview = clean_phone(phone)
            if phone.strip():
                st.caption(f"Will save as: `{phone_clean_preview}`")

            add = st.form_submit_button("✅ Add Member", use_container_width=True)

        if add:
            name_clean = clean_name(name)
            phone_clean = clean_phone(phone)

            if not name_clean:
                st.error("Member name is required.")
            elif not is_valid_phone(phone_clean):
                st.error("Invalid phone format. Use 10–15 digits with optional + (e.g., +14058458002).")
            else:
                payload = {"name": name_clean, "phone": phone_clean, "created_at": now_iso()}
                ok = safe_insert(sb_service, schema, "members", payload)
                if ok:
                    audit_log(
                        sb_service,
                        schema,
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

    with col_edit:
        st.markdown("#### Quick phone update")
        dfm = load_members(sb_service, schema)
        if dfm.empty:
            st.info("No members yet. Add your first member on the left.")
        else:
            dfm["label"] = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
            with st.form("member_phone_form", clear_on_submit=True):
                pick = st.selectbox("Select member", dfm["label"].tolist())
                new_phone = st.text_input("New phone (optional)", value="", placeholder="e.g., +1 405-845-8002")
                if new_phone.strip():
                    st.caption(f"Will save as: `{clean_phone(new_phone)}`")
                save = st.form_submit_button("💾 Save Phone", use_container_width=True)

            if save:
                mid = int(dfm.loc[dfm["label"] == pick, "id"].iloc[0])
                phone_clean = clean_phone(new_phone)
                if not is_valid_phone(phone_clean):
                    st.error("Invalid phone format. Use 10–15 digits with optional + (e.g., +14058458002).")
                else:
                    ok = safe_update(sb_service, schema, "members", {"phone": phone_clean}, {"id": mid})
                    if ok:
                        audit_log(
                            sb_service,
                            schema,
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

    section_divider()
    st.markdown("#### Current members")
    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.info("No members found.")
    else:
        st.dataframe(dfm, use_container_width=True, hide_index=True)

    glass_close()


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
    glass_open("Rotation (Session-based)", "Sets app_state.current_session_id and app_state.next_member_id (no legacy payout_index).")

    state = ensure_app_state(sb_service, schema)
    current_session_id = state.get("current_session_id")
    next_member_id = state.get("next_member_id")
    updated_at = state.get("updated_at") or "N/A"

    k1, k2, k3 = st.columns(3)
    k1.metric("Current session_id", f"{current_session_id}" if current_session_id is not None else "—")
    k2.metric("Next member_id", f"{next_member_id}" if next_member_id is not None else "—")
    k3.metric("Updated", str(updated_at)[:19])

    section_divider()
    st.markdown("#### Override (requires reason + confirmation)")
    st.caption("Every override is audit logged. Use this only for approved corrections.")

    pk = sessions_pk(sb_service, schema)
    dfs = load_sessions(sb_service, schema)
    if dfs.empty:
        st.warning("No sessions found in sessions table.")
        glass_close()
        return

    dfs["label"] = dfs.apply(lambda r: f"{int(r[pk]):04d} • {r.get('start_date','')} → {r.get('end_date','')}", axis=1)

    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.warning("No members found.")
        glass_close()
        return
    dfm["label"] = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)

    with st.form("rotation_override_form", clear_on_submit=False):
        colA, colB = st.columns([1, 1], gap="large")
        with colA:
            sess_pick = st.selectbox("Set current session", dfs["label"].tolist())
        with colB:
            mem_pick = st.selectbox("Set next beneficiary (member)", dfm["label"].tolist())

        reason = st.text_input("Reason (required)", value="", placeholder="e.g., new cycle started, correction")
        confirm = st.checkbox("I confirm this override is intentional and approved.")

        save = st.form_submit_button("💾 Save Rotation Override", use_container_width=True)

    if save:
        if not confirm:
            st.error("Confirmation required.")
        elif not reason.strip():
            st.error("Reason is required for audit.")
        else:
            sess_id = int(dfs.loc[dfs["label"] == sess_pick, pk].iloc[0])
            mem_id = int(dfm.loc[dfm["label"] == mem_pick, "id"].iloc[0])

            payload = {"id": 1, "current_session_id": int(sess_id), "next_member_id": int(mem_id), "updated_at": now_iso()}
            ok = safe_upsert(sb_service, schema, "app_state", payload)
            if ok:
                audit_log(
                    sb_service,
                    schema,
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

    glass_close()


# ============================================================
# Contributions (ONE per member per session) — UPSERT
# ============================================================
def panel_contributions(sb_service, schema: str, actor_email: str):
    glass_open("Contributions", "Rule: ONE contribution per member per session (UPSERT on (session_id, member_id)).")

    state = ensure_app_state(sb_service, schema)
    current_session_id = state.get("current_session_id")

    if current_session_id is None:
        st.warning("app_state.current_session_id is not set. Set it in Rotation tab first.")
        glass_close()
        return

    st.caption(f"Current session_id: **{current_session_id}** • Amount must be **>= 500** and a **multiple of 500**.")

    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.warning("No members found.")
        glass_close()
        return

    dfm["label"] = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
    labels = dfm["label"].tolist()
    label_to_id = dict(zip(dfm["label"], dfm["id"]))

    col1, col2 = st.columns([1, 1], gap="large")

    with col1:
        st.markdown("#### Single entry")
        with st.form("contrib_single_form", clear_on_submit=True):
            pick = st.selectbox("Member", labels)
            amt = st.number_input("Amount", min_value=0, step=500, value=500)
            note = st.text_input("Note (optional)", value="")
            save = st.form_submit_button("✅ Save Contribution", use_container_width=True)

        if save:
            mid = int(label_to_id[pick])
            if not is_multiple_of_500(int(amt)):
                st.error("Amount must be >= 500 and multiple of 500.")
            else:
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
                        sb_service,
                        schema,
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
        st.markdown("#### Bulk entry (optional)")
        st.caption("Enter amounts for many members. Zero means skip. Saving updates existing rows (UPSERT).")

        df_bulk = dfm[["id", "name"]].copy()
        df_bulk["amount"] = 0
        edited = st.data_editor(
            df_bulk,
            hide_index=True,
            use_container_width=True,
            column_config={"amount": st.column_config.NumberColumn("amount", step=500, min_value=0)},
            key="contrib_bulk_editor",
        )

        if st.button("✅ Save Bulk Contributions", use_container_width=True, key="contrib_bulk_save"):
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
                    sb_service,
                    schema,
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

    section_divider()
    st.markdown("#### Contributions for current session")

    # Prefer view if exists; otherwise show raw contributions for that session
    rows = safe_select(
        sb_service,
        schema,
        "v_contributions_with_member",
        "member_id,member_name,session_id,amount,paid_at,created_at,note",
        order_by="member_id",
        desc=False,
        limit=5000,
        session_id=int(current_session_id),
    )
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No contributions recorded for this session yet (or view not available). Showing raw contributions table instead.")
        raw = safe_select(
            sb_service,
            schema,
            "contributions",
            "member_id,session_id,amount,paid_at,created_at,updated_at,note",
            order_by="member_id",
            desc=False,
            limit=5000,
            session_id=int(current_session_id),
        )
        st.dataframe(pd.DataFrame(raw) if raw else pd.DataFrame(), use_container_width=True, hide_index=True)
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

    glass_close()


# ============================================================
# Fines (optional table: fines)
# ============================================================
def panel_fines(sb_service, schema: str, actor_email: str):
    glass_open("Fines", "Optional table: fines. If missing, create it or this panel will error.")

    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.warning("No members found.")
        glass_close()
        return

    dfm["label"] = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
    pick = st.selectbox("Member", dfm["label"].tolist(), key="fine_member")
    mid = int(dfm[dfm["label"] == pick]["id"].iloc[0])

    col1, col2 = st.columns([1, 1], gap="large")
    with col1:
        amount = st.number_input("Fine amount", min_value=0.0, step=10.0, value=30.0, key="fine_amount")
        reason = st.text_input("Reason", value="Late payment", key="fine_reason")
    with col2:
        status = st.selectbox("Status", ["unpaid", "paid", "waived"], index=0, key="fine_status")
        paid_at = None
        if status == "paid":
            paid_at = st.date_input("Paid at", value=date.today(), key="fine_paid_at").isoformat()

    if st.button("✅ Save Fine", use_container_width=True, key="fine_save"):
        if amount <= 0:
            st.error("Fine amount must be > 0.")
        else:
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
                    sb_service,
                    schema,
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

    section_divider()
    st.markdown("#### Recent fines")
    rows = safe_select(sb_service, schema, "fines", "*", order_by="created_at", desc=True, limit=300)
    st.dataframe(pd.DataFrame(rows) if rows else pd.DataFrame(), use_container_width=True, hide_index=True)

    glass_close()


# ============================================================
# Foundation contributions (session-based) — UPSERT
# ============================================================
def panel_foundation(sb_service, schema: str, actor_email: str):
    glass_open("Foundation", "Session-based foundation contributions (UPSERT).")

    state = ensure_app_state(sb_service, schema)
    current_session_id = state.get("current_session_id")
    if current_session_id is None:
        st.warning("app_state.current_session_id is not set. Set it in Rotation tab first.")
        glass_close()
        return

    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.warning("No members found.")
        glass_close()
        return

    dfm["label"] = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1)
    pick = st.selectbox("Member", dfm["label"].tolist(), key="foundation_member")
    mid = int(dfm[dfm["label"] == pick]["id"].iloc[0])

    col1, col2 = st.columns([1, 1], gap="large")
    with col1:
        amount = st.number_input("Amount", min_value=0.0, step=500.0, value=500.0, key="foundation_amount")
        note = st.text_input("Note (optional)", value="", key="foundation_note")
    with col2:
        paid_at = st.date_input("Paid at", value=date.today(), key="foundation_paid_at").isoformat()

    if st.button("✅ Save Foundation Contribution", use_container_width=True, key="foundation_save"):
        if amount <= 0:
            st.error("Amount must be > 0.")
        else:
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
                    sb_service,
                    schema,
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

    section_divider()
    st.markdown("#### Foundation contributions for current session")
    rows = safe_select(
        sb_service,
        schema,
        "foundation_contributions",
        "member_id,session_id,amount,paid_at,created_at,note",
        order_by="member_id",
        desc=False,
        limit=5000,
        session_id=int(current_session_id),
    )
    st.dataframe(pd.DataFrame(rows) if rows else pd.DataFrame(), use_container_width=True, hide_index=True)

    glass_close()


# ============================================================
# Main entry called from app router
# ============================================================
def render_admin(sb_service, schema: str, actor_email: str = ""):
    st.markdown("## 🛠️ Admin Console")
    st.caption("Governed changes • confirmations • audit logging • no legacy tables")

    if not sb_service:
        st.warning("Service key not configured.")
        return

    # System init (ensure app_state.id=1 exists)
    glass_open("System initialization", "Creates app_state(id=1) if it does not exist.")
    if st.button("✅ Initialize app_state (id=1)", use_container_width=True, key="init_app_state"):
        ok = safe_upsert(sb_service, schema, "app_state", {"id": 1, "updated_at": now_iso()})
        if ok:
            audit_log(
                sb_service,
                schema,
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
    glass_close()

    section_divider()

    # ✅ Unique tab names (no duplicates)
    t_members, t_rot, t_contrib, t_fines, t_found = st.tabs(
        ["👥 Members", "🔁 Rotation", "💵 Contributions", "⚠️ Fines", "🏦 Foundation"]
    )

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
