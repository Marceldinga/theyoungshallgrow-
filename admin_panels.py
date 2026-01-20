
# admin_panels.py
import json
import streamlit as st
import pandas as pd
from datetime import date

from db import now_iso, fetch_one, current_session_id, get_app_state  # ✅ added get_app_state
from audit import audit
from payout import (
    get_signatures,
    missing_roles,
    payout_precheck_option_b,
    execute_payout_option_b,
)
from loans import (
    approve_loan_request,
    deny_loan_request,
    apply_loan_payment,
    accrue_monthly_interest,
)
from pdfs import make_payout_receipt_pdf


def show_api_error(e: Exception, title="Supabase error"):
    st.error(title)
    st.code(repr(e))


def to_df(resp):
    return pd.DataFrame(resp.data or [])


def safe_select_autosort(c, table: str, limit=800):
    for col in ["created_at", "issued_at", "updated_at", "paid_at", "date_paid", "start_date"]:
        try:
            return c.table(table).select("*").order(col, desc=True).limit(limit).execute()
        except Exception:
            continue
    return c.table(table).select("*").limit(limit).execute()


# -------------------------
# SIGNATURE BOX
# -------------------------
def signature_box(
    c,
    entity_type: str,
    entity_id: int,
    role: str,
    default_name: str,
    signer_member_id,
    key_prefix: str
):
    df_sig = get_signatures(c, entity_type, entity_id)
    existing = df_sig[df_sig["role"] == role] if not df_sig.empty else pd.DataFrame()

    with st.container(border=True):
        st.markdown(f"**{role.upper()} SIGNATURE**")

        if not existing.empty:
            row = existing.iloc[-1]
            st.success(f"Signed by: {row.get('signer_name','')} • {str(row.get('signed_at',''))}")
            return True

        name = st.text_input(
            "Type full name",
            value=default_name or "",
            key=f"{key_prefix}_{entity_type}_{entity_id}_{role}_name"
        )
        confirm = st.checkbox(
            "I confirm this signature",
            key=f"{key_prefix}_{entity_type}_{entity_id}_{role}_confirm"
        )

        if st.button(
            "Sign",
            key=f"{key_prefix}_{entity_type}_{entity_id}_{role}_btn",
            use_container_width=True
        ):
            if not confirm:
                st.error("Please check confirmation box.")
                st.stop()
            if not name.strip():
                st.error("Name is required.")
                st.stop()
            try:
                c.table("signatures").insert({
                    "entity_type": entity_type,
                    "entity_id": int(entity_id),
                    "role": role,
                    "signer_name": name.strip(),
                    "signer_member_id": int(signer_member_id) if signer_member_id is not None else None,
                }).execute()
                st.success("Signed.")
                st.rerun()
            except Exception as e:
                show_api_error(e, "Signature failed")

        return False


# -------------------------
# ADMIN DATA INSERT HELPERS
# -------------------------
def admin_upsert_contribution(
    c,
    session_id: str,
    member_id: int,
    amount: int,
    kind: str,
    payout_index: int,  # ✅ new param
):
    # Keep legacy uniqueness per session+member if you want
    c.table("contributions_legacy").delete().eq("session_id", session_id).eq("member_id", int(member_id)).execute()

    c.table("contributions_legacy").insert({
        "session_id": session_id,
        "member_id": int(member_id),
        "amount": int(amount),
        "kind": str(kind).lower().strip(),
        "payout_index": int(payout_index),     # ✅ CRITICAL: tag rotation
        "created_at": now_iso(),
    }).execute()


def admin_add_fine(c, member_id: int, amount: float, status: str = "unpaid", note: str = ""):
    payload = {
        "member_id": int(member_id),
        "amount": float(amount),
        "status": str(status).lower().strip(),
        "note": note.strip(),
        "created_at": now_iso(),
    }
    try:
        c.table("fines_legacy").insert(payload).execute()
    except Exception:
        payload.pop("note", None)
        c.table("fines_legacy").insert(payload).execute()


def admin_add_foundation_payment(
    c,
    member_id: int,
    amount_paid: float = 0.0,
    amount_pending: float = 0.0,
    note: str = ""
):
    payload = {
        "member_id": int(member_id),
        "amount_paid": float(amount_paid),
        "amount_pending": float(amount_pending),
        "note": note.strip(),
        "created_at": now_iso(),
    }
    try:
        c.table("foundation_payments_legacy").insert(payload).execute()
    except Exception:
        payload.pop("note", None)
        c.table("foundation_payments_legacy").insert(payload).execute()


# -------------------------
# ADMIN PANELS
# -------------------------
def render_admin_contributions_panel(c, member_labels, label_to_legacy_id, df_registry):
    st.subheader("Contributions (Admin) — Current Rotation")
    session_id = current_session_id(c)
    if not session_id:
        st.error("No current session found in sessions_legacy.")
        return

    # ✅ Current rotation index from app_state (single source of truth)
    state = get_app_state(c)
    current_rotation = int(state.get("next_payout_index") or 1)

    st.caption(f"Current session_id: {session_id}")
    st.caption(f"Current rotation index: {current_rotation}")
    st.caption("Gate counts kind in ('paid','contributed'). Amount must be >=500 and multiple of 500.")

    col1, col2 = st.columns([1, 2])

    # Single entry
    with col1:
        pick = st.selectbox("Member", member_labels, key="contrib_admin_member_pick")
        mid = int(label_to_legacy_id[pick])
        amt = st.number_input("Amount", min_value=0, step=500, value=500, key="contrib_admin_amt")
        kind = st.selectbox("Kind", ["contributed", "paid"], index=0, key="contrib_admin_kind")

        if st.button("Save Contribution", use_container_width=True, key="contrib_admin_save"):
            if amt < 500 or (amt % 500 != 0):
                st.error("Amount must be >= 500 and a multiple of 500.")
            else:
                try:
                    admin_upsert_contribution(c, session_id, mid, int(amt), kind, payout_index=current_rotation)  # ✅
                    audit(c, "admin_contribution_saved", "ok", {
                        "member_id": mid, "amount": int(amt), "kind": kind, "payout_index": current_rotation
                    })
                    st.success("Saved.")
                    st.rerun()
                except Exception as e:
                    show_api_error(e, "Save contribution failed")

    # Bulk entry
    with col2:
        df_bulk = df_registry.copy()
        df_bulk = df_bulk[df_bulk["is_active"].isin([True, None])]
        df_bulk = df_bulk.rename(columns={"legacy_member_id": "member_id", "full_name": "member_name"})
        if df_bulk.empty:
            st.info("No active members.")
        else:
            df_bulk["amount"] = 0
            df_bulk = df_bulk[["member_id", "member_name", "amount"]].sort_values("member_id")

            edited = st.data_editor(
                df_bulk,
                hide_index=True,
                use_container_width=True,
                column_config={"amount": st.column_config.NumberColumn("amount", step=500, min_value=0)},
                key="contrib_admin_bulk_editor",
            )

            bulk_kind = st.selectbox("Bulk kind", ["contributed", "paid"], index=0, key="contrib_admin_bulk_kind")
            if st.button("Save Bulk", use_container_width=True, key="contrib_admin_bulk_save"):
                errors, saved = [], 0
                for _, r in edited.iterrows():
                    mid = int(r["member_id"])
                    amt = int(r["amount"] or 0)
                    if amt <= 0:
                        continue
                    if amt < 500 or (amt % 500 != 0):
                        errors.append(f"Member {mid}: invalid amount {amt}")
                        continue
                    try:
                        admin_upsert_contribution(c, session_id, mid, amt, bulk_kind, payout_index=current_rotation)  # ✅
                        saved += 1
                    except Exception as e:
                        errors.append(f"Member {mid}: {repr(e)}")

                if errors:
                    st.error("Some rows failed:\n- " + "\n- ".join(errors))
                if saved:
                    audit(c, "admin_contribution_bulk_saved", "ok", {
                        "rows_saved": saved, "kind": bulk_kind, "payout_index": current_rotation
                    })
                    st.success(f"Saved {saved} rows.")
                    st.rerun()

    st.divider()
    st.markdown("### Current Rotation Rows (strict pot uses payout_index)")
    try:
        rows = (
            c.table("contributions_legacy")
             .select("member_id,amount,kind,payout_index,created_at")
             .eq("payout_index", current_rotation)  # ✅ show current rotation rows
             .in_("kind", ["paid", "contributed"])
             .order("member_id", desc=False)
             .limit(5000)
             .execute()
             .data or []
        )
        df = pd.DataFrame(rows)
        if df.empty:
            st.warning("No paid/contributed contributions recorded for this rotation yet.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
    except Exception as e:
        show_api_error(e, "Could not load contributions_legacy")


def render_admin_foundation_panel(c, member_labels, label_to_legacy_id):
    st.subheader("Foundation Payments (Admin)")
    col1, col2 = st.columns([1, 2])

    with col1:
        pick = st.selectbox("Member", member_labels, key="found_admin_member_pick")
        mid = int(label_to_legacy_id[pick])
        paid = st.number_input("amount_paid", min_value=0.0, step=500.0, value=500.0, key="found_admin_paid")
        pending = st.number_input("amount_pending", min_value=0.0, step=500.0, value=0.0, key="found_admin_pending")
        note = st.text_input("Note (optional)", value="", key="found_admin_note")

        if st.button("Add Foundation Payment", use_container_width=True, key="found_admin_add"):
            try:
                admin_add_foundation_payment(c, mid, paid, pending, note=note)
                audit(
                    c,
                    "admin_foundation_payment_added",
                    "ok",
                    {"member_id": mid, "amount_paid": paid, "amount_pending": pending, "note": note},
                )
                st.success("Added.")
                st.rerun()
            except Exception as e:
                show_api_error(e, "Add foundation payment failed")

    with col2:
        st.markdown("### Recent Foundation Payments")
        try:
            df_fp = to_df(safe_select_autosort(c, "foundation_payments_legacy", limit=300))
            st.dataframe(df_fp, use_container_width=True, hide_index=True)
        except Exception as e:
            show_api_error(e, "Could not load foundation_payments_legacy")


def render_admin_fines_panel(c, member_labels, label_to_legacy_id):
    st.subheader("Fines (Admin)")
    col1, col2 = st.columns([1, 2])

    with col1:
        pick = st.selectbox("Member", member_labels, key="fine_admin_member_pick")
        mid = int(label_to_legacy_id[pick])
        amt = st.number_input("Fine amount", min_value=0.0, step=10.0, value=30.0, key="fine_admin_amt")
        status = st.selectbox("Status", ["unpaid", "paid"], index=0, key="fine_admin_status")
        note = st.text_input("Note (optional)", value="", key="fine_admin_note")

        if st.button("Add Fine", use_container_width=True, key="fine_admin_add"):
            if amt <= 0:
                st.error("Fine must be > 0.")
            else:
                try:
                    admin_add_fine(c, mid, amt, status=status, note=note)
                    audit(c, "admin_fine_added", "ok", {"member_id": mid, "amount": amt, "status": status, "note": note})
                    st.success("Added.")
                    st.rerun()
                except Exception as e:
                    show_api_error(e, "Add fine failed")

    with col2:
        st.markdown("### Recent Fines")
        try:
            df_f = to_df(safe_select_autosort(c, "fines_legacy", limit=300))
            st.dataframe(df_f, use_container_width=True, hide_index=True)
        except Exception as e:
            show_api_error(e, "Could not load fines_legacy")


def render_admin_loan_requests_workflow(c, actor_user_id: str):
    st.subheader("Loan Requests (Admin Workflow)")
    # (unchanged)
    try:
        rows = (
            c.table("loan_requests")
             .select("*")
             .order("created_at", desc=True)
             .limit(500)
             .execute()
             .data or []
        )
    except Exception as e:
        show_api_error(e, "Could not load loan_requests")
        return

    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No loan requests.")
        return

    st.dataframe(df, use_container_width=True, hide_index=True)

    pending = df[df["status"] == "pending"]
    if pending.empty:
        st.success("No pending requests.")
        return

    pending = pending.copy()
    pending["label"] = pending.apply(
        lambda r: f"Req {int(r['id'])} • member {int(r['requester_member_id'])} • {float(r['amount']):,.0f} • {str(r['created_at'])[:19]}",
        axis=1
    )

    pick = st.selectbox("Select pending request", pending["label"].tolist(), key="admin_lr_pick")
    req_row = pending[pending["label"] == pick].iloc[0]
    req_id = int(req_row["id"])

    st.divider()
    st.markdown(f"### Selected Request ID: **{req_id}**")

    df_sig = get_signatures(c, "loan", req_id)
    st.markdown("#### Signatures")
    st.dataframe(df_sig, use_container_width=True, hide_index=True)

    miss = missing_roles(df_sig, ["borrower", "surety", "treasury"])
    if miss:
        st.warning("Missing: " + ", ".join(miss))
    else:
        st.success("All required signatures present.")

    colA, colB = st.columns(2)

    with colA:
        if st.button("Approve Request", use_container_width=True, key="admin_lr_approve_btn"):
            try:
                loan_id = approve_loan_request(c, req_id, actor_user_id=actor_user_id)
                audit(c, "loan_request_approved", "ok", {"request_id": req_id, "loan_id": loan_id}, actor_user_id=actor_user_id)
                st.success(f"Approved. Created loan ID: {loan_id}")
                st.rerun()
            except Exception as e:
                show_api_error(e, "Approval failed")

    with colB:
        reason = st.text_input("Deny reason", value="Failed verification", key="admin_lr_deny_reason")
        if st.button("Deny Request", use_container_width=True, key="admin_lr_deny_btn"):
            try:
                deny_loan_request(c, req_id, reason, actor_user_id=actor_user_id)
                audit(c, "loan_request_denied", "ok", {"request_id": req_id, "reason": reason}, actor_user_id=actor_user_id)
                st.success("Denied.")
                st.rerun()
            except Exception as e:
                show_api_error(e, "Deny failed")


def render_admin_loan_repayments_panel(c, actor_user_id: str):
    st.subheader("Loan Repayments (Admin)")
    # (unchanged)
    try:
        loans = (
            c.table("loans_legacy")
             .select("id,member_id,status,balance,total_due,issued_at")
             .order("issued_at", desc=True)
             .limit(1000)
             .execute()
             .data or []
        )
    except Exception as e:
        show_api_error(e, "Could not load loans_legacy")
        return

    df_loans = pd.DataFrame(loans)
    if df_loans.empty:
        st.info("No loans found.")
        return

    df_loans["label"] = df_loans.apply(
        lambda r: f"Loan {int(r['id'])} • member {int(r['member_id'])} • {str(r.get('status',''))} • due {float(r.get('total_due',0)):,.0f}",
        axis=1
    )
    pick = st.selectbox("Select loan", df_loans["label"].tolist(), key="repay_pick")
    row = df_loans[df_loans["label"] == pick].iloc[0]
    loan_id = int(row["id"])

    col1, col2 = st.columns(2)
    with col1:
        amt = st.number_input("Payment amount", min_value=0.0, step=50.0, value=100.0, key="repay_amt")
    with col2:
        paid_on = st.date_input("Paid on", value=date.today(), key="repay_date")

    if st.button("Apply Payment", use_container_width=True, key="repay_apply_btn"):
        if amt <= 0:
            st.error("Amount must be > 0.")
        else:
            try:
                apply_loan_payment(c, loan_id, float(amt), str(paid_on), actor_user_id=actor_user_id)
                audit(c, "loan_payment_applied", "ok", {"loan_id": loan_id, "amount": amt, "paid_on": str(paid_on)}, actor_user_id=actor_user_id)
                st.success("Payment applied.")
                st.rerun()
            except Exception as e:
                show_api_error(e, "Apply payment failed")

    st.divider()
    st.markdown("### Recent Loan Payments")
    try:
        df_pay = to_df(safe_select_autosort(c, "loan_payments", limit=300))
        st.dataframe(df_pay, use_container_width=True, hide_index=True)
    except Exception as e:
        show_api_error(e, "Could not load loan_payments")


def render_admin_interest_accrual_panel(c, actor_user_id: str):
    st.subheader("Interest Accrual (Admin)")
    st.caption("Applies 5% monthly interest to ACTIVE loans and writes a snapshot.")

    if st.button("Accrue Monthly Interest Now", use_container_width=True, key="accrue_interest_btn"):
        try:
            updated, total = accrue_monthly_interest(c, actor_user_id=actor_user_id)
            audit(c, "monthly_interest_accrued", "ok", {"loans_updated": updated, "interest_added_total": total}, actor_user_id=actor_user_id)
            st.success(f"Accrued interest on {updated} loans. Total interest added: {total:,.0f}")
            st.rerun()
        except Exception as e:
            show_api_error(e, "Interest accrual failed")


# -------------------------
# PAYOUT TAB (OPTION B) - UPDATED GATE 3
# -------------------------
def render_payout_tab_option_b(
    c,
    member_labels,
    label_to_legacy_id,
    label_to_name,
    df_registry,
    state,
    already_paid_ids,
    profile,
    user_email,
    actor_user_id: str
):
    st.subheader("Payout (Option B)")
    st.caption("Gate 1: active=17 • Gate 2: each >=500 & multiple of 500 • Gate 3: pot>0 • Gate 4: signatures")

    raw_next_idx = int(state.get("next_payout_index") or 1)

    active_members = []
    for rr in (df_registry.to_dict("records") if not df_registry.empty else []):
        if rr.get("is_active") in (None, True):
            mid = int(rr.get("legacy_member_id") or 0)
            name = (rr.get("full_name") or f"Member {mid}").strip()
            active_members.append((mid, name))

    if already_paid_ids:
        st.info(f"Already paid members (payouts_legacy): {', '.join(map(str, sorted(list(already_paid_ids))))}")

    pre = payout_precheck_option_b(c, active_members, already_paid_ids, raw_next_idx)
    if not pre["session_id"]:
        st.error(pre.get("reason") or "No current session.")
        return

    st.caption(f"Current session_id: {pre['session_id']}")
    st.markdown(f"### Next Beneficiary: **{pre['beneficiary_id']} — {pre['beneficiary_name']}**")

    st.markdown("### Gate 1: Active members")
    st.dataframe(pre["active_members_df"], use_container_width=True, hide_index=True)
    st.success("✅ Gate 1 passed." if pre["gate1_ok"] else "❌ Gate 1 failed.")

    st.markdown("### Gate 2: Contribution rules (paid OR contributed, current session)")
    df_preview = pd.DataFrame(pre["summary_rows"])
    st.dataframe(df_preview, use_container_width=True, hide_index=True)
    if pre["gate2_ok"]:
        st.success("✅ Gate 2 passed.")
    else:
        st.warning("❌ Gate 2 failed:")
        st.dataframe(pre["df_problems"], use_container_width=True, hide_index=True)

    # ✅ Gate 3 uses strict rotation pot view (matches dashboard)
    st.markdown("### Gate 3: Pot total (current rotation)")
    try:
        pot_row = (
            c.table("v_contribution_pot")
             .select("next_payout_index,next_payout_date,pot_amount,pot_status")
             .single()
             .execute()
             .data
        )
        pot_amount = float(pot_row.get("pot_amount") or 0)
        gate3_ok = pot_amount > 0
        st.caption(f"Pot: {pot_amount:,.0f}")
        st.caption(f"Rotation #{pot_row.get('next_payout_index')} • Payout: {pot_row.get('next_payout_date')} • {pot_row.get('pot_status')}")
        st.success("✅ Gate 3 passed." if gate3_ok else "❌ Gate 3 failed (pot is zero).")
    except Exception as e:
        pot_amount = 0.0
        gate3_ok = False
        show_api_error(e, "Gate 3 failed: could not read v_contribution_pot")

    payout_ready = bool(pre["gate1_ok"] and pre["gate2_ok"] and gate3_ok)

    st.divider()

    st.markdown("### Gate 4: Required Signatures (Payout)")
    payout_entity_id = int(pre["beneficiary_id"])

    signature_box(
        c, "payout", payout_entity_id, "beneficiary",
        default_name=f"{pre['beneficiary_id']} — {pre['beneficiary_name']}",
        signer_member_id=int(pre["beneficiary_id"]),
        key_prefix="payout_sig"
    )

    st.caption("Select payout surety (required).")
    surety_pick = st.selectbox("Payout surety", member_labels, key="payout_surety_pick_updated")
    surety_id = int(label_to_legacy_id[surety_pick])
    surety_name = str(label_to_name[surety_pick])

    signature_box(
        c, "payout", payout_entity_id, "surety",
        default_name=surety_name,
        signer_member_id=int(surety_id),
        key_prefix="payout_sig"
    )

    st.caption("Select president (required).")
    pres_pick = st.selectbox("President", member_labels, key="payout_pres_pick_updated")
    president_id = int(label_to_legacy_id[pres_pick])
    president_name = str(label_to_name[pres_pick])

    signature_box(
        c, "payout", payout_entity_id, "president",
        default_name=president_name,
        signer_member_id=int(president_id),
        key_prefix="payout_sig"
    )

    signature_box(
        c, "payout", payout_entity_id, "treasury",
        default_name=user_email,
        signer_member_id=int(profile["member_id"]),
        key_prefix="payout_sig"
    )

    df_sig_now = get_signatures(c, "payout", payout_entity_id)
    miss = missing_roles(df_sig_now, ["president", "beneficiary", "treasury", "surety"])
    sig_ok = (len(miss) == 0)

    if sig_ok:
        st.success("✅ All payout signatures present.")
    else:
        st.warning("⚠️ Missing signatures: " + ", ".join(miss))

    st.divider()

    run_ok = payout_ready and sig_ok
    if st.button("Run Payout Now (Option B)", use_container_width=True, disabled=not run_ok, key="payout_run_btn_updated"):
        try:
            receipt = execute_payout_option_b(c, active_members, already_paid_ids, raw_next_idx)
            audit(c, "payout_executed", "ok", {"receipt": receipt}, actor_user_id=actor_user_id)

            st.success("Payout completed.")
            st.json(receipt)

            df_sum = pd.DataFrame(receipt.get("contribution_summary") or [])
            if not df_sum.empty:
                csv_bytes = df_sum.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "Download Contribution Summary CSV",
                    csv_bytes,
                    file_name="theyoungshallgrow_payout_contribution_summary.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="payout_dl_csv_updated"
                )

            pdf_bytes = make_payout_receipt_pdf("theyoungshallgrow", receipt)
            st.download_button(
                "Download Payout Receipt PDF",
                pdf_bytes,
                file_name="theyoungshallgrow_payout_receipt.pdf",
                mime="application/pdf",
                use_container_width=True,
                key="payout_dl_pdf_updated"
            )

            st.rerun()
        except Exception as e:
            show_api_error(e, "Payout failed")
