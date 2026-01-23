# loans_ui.py ✅ COMPLETE UPDATED (uses loan_repayments for loan-linked payments)
# Fixes:
# - Permission errors (RBAC perms match rbac.py)
# - Delinquency NaT crash (safe loan_id parsing)
# - Record Payment "Loan not found" (select existing loan)
# - ✅ Writes/reads loan repayments from loan_repayments (NOT historical repayments table)
# - ✅ Approve request shows clean DB trigger message (APIError P0001) instead of scary stack trace
# Includes:
# - Requests (create + list + signatures + admin approve/deny)
# - Ledger (loans_legacy table)
# - Record Payment (loan_repayments insert via core.record_payment_pending)
# - Confirm/Reject (schema locked; UI explains)
# - Interest (core.accrue_monthly_interest)
# - Delinquency (simple DPD)
# - Loan Statement (digital signature + SAFE PDF call)
# - Loan Repayment (Legacy) insert into loan_repayments_legacy

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4, UUID
import inspect

import streamlit as st
import pandas as pd

from postgrest.exceptions import APIError

from rbac import Actor, require, allowed_sections, ROLE_ADMIN, ROLE_TREASURY, ROLE_MEMBER
import loans_core as core

# Optional PDFs
try:
    from pdfs import make_member_loan_statement_pdf, make_loan_statements_zip
except Exception:
    make_member_loan_statement_pdf = None
    make_loan_statements_zip = None

# Optional audit
try:
    from audit import audit
except Exception:
    def audit(*args, **kwargs):
        return None

# ✅ Loan-linked repayments table (strict)
PAYMENTS_TABLE = "loan_repayments"
REPAY_LINK_COL = "loan_id"
REPAY_DATE_COL = "paid_at"


# ============================================================
# Helpers
# ============================================================
def _is_uuid(s: str) -> bool:
    try:
        UUID(str(s))
        return True
    except Exception:
        return False


def _get_or_make_session_uuid(key: str = "actor_user_uuid") -> str:
    v = str(st.session_state.get(key) or "").strip()
    if not v or not _is_uuid(v):
        st.session_state[key] = str(uuid4())
    return str(st.session_state[key])


def _actor_from_session(default_user_id: str) -> Actor:
    with st.sidebar.expander("🔐 Role (temporary)", expanded=False):
        role = st.selectbox("Role", [ROLE_ADMIN, ROLE_TREASURY, ROLE_MEMBER], index=0, key="actor_role")
        member_id = st.number_input(
            "Member ID (if member/treasury)",
            min_value=0, step=1, value=int(st.session_state.get("actor_member_id") or 0),
            key="actor_member_id",
        )
        name = st.text_input(
            "Name",
            value=str(st.session_state.get("actor_name") or ("admin" if role != ROLE_MEMBER else "member")),
            key="actor_name",
        )

    user_uuid = default_user_id if (default_user_id and _is_uuid(default_user_id)) else _get_or_make_session_uuid()

    return Actor(
        user_id=user_uuid,
        role=role,
        member_id=(int(member_id) if int(member_id) > 0 else None),
        name=(name.strip() or None),
    )


def _to_iso(d: date) -> str:
    return datetime.combine(d, datetime.min.time()).isoformat()


def _safe_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows or [])


def _apierror_message(e: Exception) -> str:
    """
    Extracts PostgREST / Supabase error payload message cleanly.
    """
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or "APIError")
        return str(e)
    return str(e)


def _build_statement_pdf(member: dict, mloans: list[dict], mpay: list[dict], statement_sig: dict | None) -> bytes:
    """
    Calls pdfs.make_member_loan_statement_pdf safely.
    If pdfs.py is old, it will ignore statement_signature.
    """
    if make_member_loan_statement_pdf is None:
        raise RuntimeError("PDF engine not available (make_member_loan_statement_pdf import failed).")

    sig = inspect.signature(make_member_loan_statement_pdf)
    kwargs = dict(
        brand="theyoungshallgrow",
        member=member,
        cycle_info={},
        loans=mloans,
        payments=mpay,
        currency="$",
        logo_path=None,
    )
    if "statement_signature" in sig.parameters:
        kwargs["statement_signature"] = statement_sig
    return make_member_loan_statement_pdf(**kwargs)


# ============================================================
# Repayments read helpers (loan_repayments)
# ============================================================
def get_repayments_for_loan_ids(sb_service, schema: str, loan_ids: list[int], limit: int = 5000) -> list[dict]:
    if not loan_ids:
        return []
    return (
        sb_service.schema(schema).table(PAYMENTS_TABLE)
        .select("*")
        .in_(REPAY_LINK_COL, [int(x) for x in loan_ids])
        .order(REPAY_DATE_COL, desc=True)
        .limit(int(limit))
        .execute().data
        or []
    )


# ============================================================
# Requests UI
# ============================================================
def _render_requests(sb_service, schema: str, actor: Actor):
    require(actor.role, "submit_request")

    st.subheader("Requests")

    members = (
        sb_service.schema(schema).table("members_legacy")
        .select("id,name")
        .order("id", desc=False)
        .limit(5000)
        .execute().data
        or []
    )
    dfm = _safe_df(members)
    if dfm.empty:
        st.warning("members_legacy is empty or not readable.")
        return

    dfm["id"] = pd.to_numeric(dfm["id"], errors="coerce").fillna(0).astype(int)
    dfm["name"] = dfm["name"].astype(str)
    dfm["label"] = dfm.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)
    labels = dfm["label"].tolist()
    label_to_id = dict(zip(dfm["label"], dfm["id"]))
    label_to_name = dict(zip(dfm["label"], dfm["name"]))

    st.markdown("### Create a loan request")
    with st.form("loan_request_create", clear_on_submit=True):
        borrower_pick = st.selectbox("Borrower", labels, key="req_borrower")
        surety_pick = st.selectbox("Surety", labels, key="req_surety")
        amount = st.number_input("Amount", min_value=0.0, step=50.0, value=0.0, key="req_amount")
        ok = st.form_submit_button("Submit request", use_container_width=True)

    if ok:
        borrower_id = int(label_to_id[borrower_pick])
        surety_id = int(label_to_id[surety_pick])

        if borrower_id == surety_id:
            st.error("Borrower and surety must be different.")
        elif float(amount) <= 0:
            st.error("Amount must be > 0.")
        else:
            try:
                req_id = core.create_loan_request(
                    sb_service, schema,
                    borrower_id=borrower_id,
                    borrower_name=str(label_to_name[borrower_pick]),
                    surety_id=surety_id,
                    surety_name=str(label_to_name[surety_pick]),
                    amount=float(amount),
                    requester_user_id=str(actor.user_id),
                )
                audit(sb_service, "loan_request_created", "ok", {"request_id": req_id}, actor_user_id=actor.user_id)
                st.success(f"Request submitted. ID = {req_id}")
            except Exception as e:
                st.error("Failed to create request.")
                st.code(_apierror_message(e), language="text")

    st.divider()
    st.markdown("### Pending requests")

    pending = core.list_pending_requests(sb_service, schema, limit=300)
    dfp = _safe_df(pending)
    if dfp.empty:
        st.info("No pending requests.")
        return

    st.dataframe(dfp, use_container_width=True, hide_index=True)

    req_ids = dfp["id"].tolist() if "id" in dfp.columns else []
    if not req_ids:
        return

    pick_req = st.selectbox("Select request ID", req_ids, key="req_pick")

    st.markdown("### Signatures for this request")
    st.caption("Required signatures for approval: borrower + surety + treasury.")
    df_sig = core.sig_df(sb_service, schema, "loan", int(pick_req))
    st.dataframe(df_sig, use_container_width=True, hide_index=True)

    require(actor.role, "sign_request")
    roles_allowed = ["borrower", "surety", "treasury"]
    sig_role = st.selectbox("Role to sign as", roles_allowed, key="req_sig_role")
    sig_name = st.text_input("Signer name", value=(actor.name or ""), key="req_sig_name")
    sig_member_id = st.number_input(
        "Signer member_id (required)",
        min_value=1, step=1, value=int(actor.member_id or 1),
        key="req_sig_mid"
    )

    if st.button("✍️ Add signature", use_container_width=True, key="req_add_sig"):
        try:
            core.insert_signature(
                sb_service, schema,
                entity_type="loan",
                entity_id=int(pick_req),
                role=str(sig_role),
                signer_name=str(sig_name or "").strip(),
                signer_member_id=int(sig_member_id),
            )
            audit(sb_service, "loan_request_signed", "ok", {"request_id": int(pick_req), "role": sig_role}, actor_user_id=actor.user_id)
            st.success("Signature saved.")
            st.rerun()
        except Exception as e:
            st.error("Failed to save signature.")
            st.code(_apierror_message(e), language="text")

    st.divider()

    if actor.role in (ROLE_ADMIN, ROLE_TREASURY):
        require(actor.role, "approve_deny")

        st.markdown("### Admin actions")
        c1, c2 = st.columns(2)

        with c1:
            if st.button("✅ Approve request", use_container_width=True, key="req_approve"):
                try:
                    loan_id = core.approve_loan_request(
                        sb_service, schema, int(pick_req), actor_user_id=str(actor.user_id)
                    )
                    audit(
                        sb_service, "loan_request_approved", "ok",
                        {"request_id": int(pick_req), "loan_id": loan_id},
                        actor_user_id=actor.user_id
                    )
                    st.success(f"Approved. Loan created: {loan_id}")
                    st.rerun()
                except APIError as e:
                    # ✅ Show the DB trigger message cleanly
                    st.error(_apierror_message(e))
                except Exception as e:
                    st.error("Approval blocked/failed.")
                    st.code(_apierror_message(e), language="text")

        with c2:
            reason = st.text_input("Deny reason", value="Not approved", key="req_deny_reason")
            if st.button("❌ Deny request", use_container_width=True, key="req_deny"):
                try:
                    core.deny_loan_request(sb_service, schema, int(pick_req), reason=reason)
                    audit(sb_service, "loan_request_denied", "ok", {"request_id": int(pick_req)}, actor_user_id=actor.user_id)
                    st.success("Denied.")
                    st.rerun()
                except Exception as e:
                    st.error("Deny failed.")
                    st.code(_apierror_message(e), language="text")


# ============================================================
# Ledger UI
# ============================================================
def _render_ledger(sb_service, schema: str, actor: Actor):
    require(actor.role, "view_ledger")

    st.subheader("Ledger (loans_legacy)")
    rows = (
        sb_service.schema(schema).table("loans_legacy")
        .select("*")
        .order("id", desc=True)
        .limit(2000)
        .execute().data
        or []
    )
    df = _safe_df(rows)
    if df.empty:
        st.info("No loans found.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


# ============================================================
# Record payment UI (loan_repayments)
# ============================================================
def _render_record_payment(sb_service, schema: str, actor: Actor):
    require(actor.role, "record_payment")

    st.subheader("Record Payment (loan_repayments)")

    loans = (
        sb_service.schema(schema).table("loans_legacy")
        .select("id,member_id,status,total_due,principal,principal_current,unpaid_interest")
        .order("id", desc=True)
        .limit(2000)
        .execute().data
        or []
    )
    df = pd.DataFrame(loans)
    if df.empty:
        st.warning("No loans found in loans_legacy. Cannot record repayment.")
        return

    def _lbl(r):
        due = float(r.get("total_due") or 0)
        pc = float(r.get("principal_current") or r.get("principal") or 0)
        ui = float(r.get("unpaid_interest") or 0)
        return (
            f"Loan {int(r['id'])} • Member {r.get('member_id')} • {str(r.get('status') or '')} • "
            f"Principal {pc:,.0f} • Interest {ui:,.0f} • Due {due:,.0f}"
        )

    df["label"] = df.apply(_lbl, axis=1)

    pick = st.selectbox("Select loan", df["label"].tolist(), key="pay_pick_loan")
    loan_id = int(df[df["label"] == pick].iloc[0]["id"])

    amount = st.number_input("Amount", min_value=0.0, step=50.0, value=0.0, key="pay_amt")
    paid_on = st.date_input("Paid date", value=date.today(), key="pay_date")
    note = st.text_input("Note (optional)", value="Loan repayment", key="pay_note")

    if st.button("💾 Save payment", use_container_width=True, key="pay_save"):
        try:
            core.record_payment_pending(
                sb_service,
                schema,
                loan_id=int(loan_id),
                amount=float(amount),
                paid_at=_to_iso(paid_on),
                recorded_by=str(actor.user_id),
                notes=note,  # core maps to 'note' column
            )
            audit(sb_service, "loan_payment_recorded", "ok", {"loan_id": int(loan_id), "amount": float(amount)}, actor_user_id=actor.user_id)
            st.success("Payment inserted into loan_repayments and loan balance updated.")
            st.rerun()
        except Exception as e:
            st.error("Failed to record payment.")
            st.code(_apierror_message(e), language="text")

    st.divider()
    st.markdown("### Recent loan_repayments for this loan")
    try:
        rows = (
            sb_service.schema(schema).table(PAYMENTS_TABLE)
            .select("*")
            .eq("loan_id", int(loan_id))
            .order("paid_at", desc=True)
            .limit(200)
            .execute().data
            or []
        )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning("Could not load loan_repayments.")
        st.code(_apierror_message(e), language="text")

    st.divider()
    st.markdown("### Loan balance (after payments)")
    try:
        loan_now = (
            sb_service.schema(schema).table("loans_legacy")
            .select("id,status,principal,principal_current,unpaid_interest,total_due,updated_at")
            .eq("id", int(loan_id)).limit(1)
            .execute().data or []
        )
        if loan_now:
            st.json(loan_now[0])
    except Exception:
        pass


def _render_confirm_payments(sb_service, schema: str, actor: Actor):
    require(actor.role, "confirm_payment")
    st.subheader("Confirm Payments")
    st.info("Not supported: you are using loan_repayments (direct inserts). Maker-checker requires a separate pending table.")


def _render_reject_payments(sb_service, schema: str, actor: Actor):
    require(actor.role, "reject_payment")
    st.subheader("Reject Payments")
    st.info("Not supported: you are using loan_repayments (direct inserts). Maker-checker requires a separate pending table.")


# ============================================================
# Interest UI
# ============================================================
def _render_interest(sb_service, schema: str, actor: Actor):
    require(actor.role, "accrue_interest")

    st.subheader("Interest")
    st.caption("Applies monthly interest (idempotent; duplicate-safe).")

    if st.button("➕ Accrue monthly interest", use_container_width=True, key="accrue_interest_btn"):
        try:
            updated, added = core.accrue_monthly_interest(sb_service, schema, actor_user_id=str(actor.user_id))
            audit(sb_service, "interest_accrued", "ok", {"updated": updated, "added": added}, actor_user_id=actor.user_id)
            st.success(f"Updated loans: {updated}, Interest added total: {added:,.2f}")
        except Exception as e:
            st.error("Interest accrual failed.")
            st.code(_apierror_message(e), language="text")


# ============================================================
# Delinquency UI (DPD) — reads loan_repayments
# ============================================================
def _render_delinquency(sb_service, schema: str, actor: Actor):
    require(actor.role, "view_delinquency")

    st.subheader("Delinquency (DPD)")

    loans = (
        sb_service.schema(schema).table("loans_legacy")
        .select("id,member_id,status,due_date,principal_current,total_due")
        .order("id", desc=True)
        .limit(5000)
        .execute().data
        or []
    )
    df = _safe_df(loans)
    if df.empty:
        st.info("No loans found.")
        return

    reps = (
        sb_service.schema(schema).table(PAYMENTS_TABLE)
        .select("loan_id,paid_at")
        .order("paid_at", desc=True)
        .limit(20000)
        .execute().data
        or []
    )
    dfr = _safe_df(reps)

    last_paid_map: dict[int, date] = {}
    if not dfr.empty:
        dfr["paid_at"] = pd.to_datetime(dfr["paid_at"], errors="coerce")
        dfr = dfr.dropna(subset=["paid_at"]).sort_values("paid_at", ascending=False)

        for _, r in dfr.iterrows():
            lid = pd.to_numeric(r.get("loan_id"), errors="coerce")
            if pd.isna(lid):
                continue
            lid = int(lid)
            if lid and lid not in last_paid_map:
                last_paid_map[lid] = r["paid_at"].date()

    df["last_paid_on"] = df["id"].apply(lambda x: last_paid_map.get(int(x)))
    df["dpd"] = df.apply(lambda r: core.compute_dpd(r.to_dict(), r.get("last_paid_on")), axis=1)

    st.dataframe(df.sort_values("dpd", ascending=False), use_container_width=True, hide_index=True)


# ============================================================
# Loan Statement UI
# ============================================================
def _render_statement(sb_service, schema: str, actor: Actor):
    require(actor.role, "loan_statement")

    st.subheader("Loan Statement (Preview + PDF Download)")

    mid = st.number_input(
        "Member ID",
        min_value=1, step=1,
        value=(actor.member_id or 1),
        key="stmt_member_id"
    )

    if actor.role == ROLE_MEMBER and actor.member_id and int(mid) != int(actor.member_id):
        st.warning("Members can only view their own statement.")
        return

    if st.button("Load Statement", use_container_width=True, key="stmt_load"):
        st.session_state["stmt_loaded_member_id"] = int(mid)

    loaded_mid = st.session_state.get("stmt_loaded_member_id")
    if not loaded_mid:
        return

    mrow = (
        sb_service.schema(schema).table("members_legacy")
        .select("id,name,position").eq("id", int(loaded_mid)).limit(1)
        .execute().data or []
    )
    mrow = mrow[0] if mrow else {}
    member = {
        "member_id": int(loaded_mid),
        "member_name": mrow.get("name") or f"Member {loaded_mid}",
        "position": mrow.get("position"),
    }

    mloans = (
        sb_service.schema(schema).table("loans_legacy")
        .select("*").eq("member_id", int(loaded_mid))
        .order("issued_at", desc=True).limit(5000)
        .execute().data or []
    )

    if not mloans:
        st.info("This member has no loans yet.")
        return

    loan_ids = [int(l["id"]) for l in mloans if l.get("id") is not None]
    mpay = get_repayments_for_loan_ids(sb_service, schema, loan_ids, limit=5000)

    st.markdown("### Loans")
    st.dataframe(pd.DataFrame(mloans), use_container_width=True, hide_index=True)
    st.markdown("### Loan Repayments (loan_repayments)")
    st.dataframe(pd.DataFrame(mpay), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Digital Signature (Statement)")

    df_loans = pd.DataFrame(mloans)
    df_loans["label"] = df_loans.apply(
        lambda r: f"Loan {int(r['id'])} • Status: {r.get('status','')} • Principal: {float(r.get('principal') or 0):,.0f}",
        axis=1
    )
    pick_loan_label = st.selectbox("Select loan to sign", df_loans["label"].tolist(), key="stmt_sign_pick_loan")
    sign_loan_id = int(df_loans[df_loans["label"] == pick_loan_label].iloc[0]["id"])

    existing_sig = core.get_statement_signature(sb_service, schema, sign_loan_id)
    if existing_sig:
        st.success(
            f"Signed by {existing_sig.get('signer_name')} "
            f"(Member ID {existing_sig.get('signer_member_id')}) "
            f"at {str(existing_sig.get('signed_at'))[:19]}"
        )
    else:
        sig_name = st.text_input("Signer name", value=(member.get("member_name") or ""), key="stmt_sig_name")
        confirm = st.checkbox("I confirm this is my digital signature", key="stmt_sig_confirm")
        if st.button("✍️ Sign Statement", use_container_width=True, key="stmt_sig_btn"):
            if not confirm:
                st.error("Please confirm the checkbox to sign.")
                st.stop()
            core.insert_statement_signature(
                sb_service,
                schema,
                loan_id=sign_loan_id,
                signer_member_id=int(member["member_id"]),
                signer_name=str(sig_name).strip(),
            )
            audit(sb_service, "statement_signed", "ok", {"loan_id": sign_loan_id}, actor_user_id=actor.user_id)
            st.success("Statement signed.")
            st.rerun()

    st.divider()
    st.markdown("### Download PDF")

    if make_member_loan_statement_pdf is None:
        st.warning("PDF engine not available. Ensure pdfs.py defines make_member_loan_statement_pdf.")
        return

    statement_sig = core.get_statement_signature(sb_service, schema, sign_loan_id)

    try:
        pdf_bytes = _build_statement_pdf(member=member, mloans=mloans, mpay=mpay, statement_sig=statement_sig)
    except Exception as e:
        st.error("PDF generation failed.")
        st.code(str(e), language="text")
        return

    st.download_button(
        "⬇️ Download Loan Statement (PDF)",
        pdf_bytes,
        file_name=f"loan_statement_{member['member_id']:02d}_{str(member['member_name']).replace(' ', '_')}.pdf",
        mime="application/pdf",
        use_container_width=True,
        key="dl_member_loan_statement_pdf",
    )


# ============================================================
# Legacy repayment insert UI
# ============================================================
def _render_legacy_repayment(sb_service, schema: str, actor: Actor):
    require(actor.role, "legacy_loan_repayment")

    st.subheader("Loan Repayment (Legacy) — Admin Insert")

    with st.form("legacy_repay_form", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        member_id = c1.number_input("Member ID", min_value=1, step=1, value=int(actor.member_id or 1))
        loan_id = c2.number_input("Loan ID (optional)", min_value=0, step=1, value=0)
        amount = c3.number_input("Amount", min_value=0.0, step=50.0, value=0.0)

        paid_date = st.date_input("Paid date", value=date.today())
        method = st.selectbox("Method", ["cash", "transfer", "zelle", "other"], index=0)
        note = st.text_area("Note (optional)", "")

        ok = st.form_submit_button("✅ Save legacy repayment", use_container_width=True)

    if not ok:
        return

    try:
        row = core.insert_legacy_loan_repayment(
            sb_service,
            schema,
            member_id=int(member_id),
            amount=float(amount),
            paid_at=_to_iso(paid_date),
            loan_id=(int(loan_id) if int(loan_id) > 0 else None),
            method=str(method),
            note=str(note or "").strip() or None,
            actor_user_id=str(actor.user_id),
        )
        audit(sb_service, "legacy_loan_repayment_inserted", "ok", {"member_id": int(member_id)}, actor_user_id=actor.user_id)
        st.success("Legacy repayment saved.")
        if row:
            st.json(row)
    except Exception as e:
        st.error("Insert into loan_repayments_legacy failed.")
        st.code(_apierror_message(e), language="text")


# ============================================================
# MAIN ENTRY
# ============================================================
def render_loans(sb_service, schema: str, actor_user_id: str = ""):
    actor_user_uuid = actor_user_id if (actor_user_id and _is_uuid(actor_user_id)) else _get_or_make_session_uuid()
    actor = _actor_from_session(actor_user_uuid)

    st.header("Loans (Organizational Standard)")

    loans_all = (
        sb_service.schema(schema).table("loans_legacy")
        .select("id,status,total_due")
        .limit(20000).execute().data or []
    )
    df_all = pd.DataFrame(loans_all)
    if df_all.empty:
        active_count, active_due = 0, 0.0
    else:
        df_all["status"] = df_all["status"].astype(str).str.lower().str.strip()
        df_all["total_due"] = pd.to_numeric(df_all.get("total_due"), errors="coerce").fillna(0)
        active = df_all[df_all["status"].isin(["open", "active"])]
        active_count = len(active)
        active_due = float(active["total_due"].sum())

    k1, k2, k3 = st.columns(3)
    k1.metric("Active loans", str(active_count))
    k2.metric("Total due (active)", f"{active_due:,.0f}")
    k3.metric("Monthly interest", "5%")
    st.divider()

    sections = allowed_sections(actor.role) or []
    if not sections:
        st.warning("No sections available for your role.")
        return

    if "loans_menu" not in st.session_state or st.session_state["loans_menu"] not in sections:
        st.session_state["loans_menu"] = sections[0]

    section = st.selectbox("Loans menu", sections, key="loans_menu")

    if section == "Requests":
        _render_requests(sb_service, schema, actor); return
    if section == "Ledger":
        _render_ledger(sb_service, schema, actor); return
    if section == "Record Payment":
        _render_record_payment(sb_service, schema, actor); return
    if section == "Confirm Payments":
        _render_confirm_payments(sb_service, schema, actor); return
    if section == "Reject Payments":
        _render_reject_payments(sb_service, schema, actor); return
    if section == "Interest":
        _render_interest(sb_service, schema, actor); return
    if section == "Delinquency":
        _render_delinquency(sb_service, schema, actor); return
    if section == "Loan Statement":
        _render_statement(sb_service, schema, actor); return
    if section == "Loan Repayment (Legacy)":
        _render_legacy_repayment(sb_service, schema, actor); return

    st.info(f"Section '{section}' is enabled but not implemented.")
