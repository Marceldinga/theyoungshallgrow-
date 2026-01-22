# loans_ui.py ✅ UPDATED (repayments schema locked: loan_id + paid_at, no status)
from __future__ import annotations

from datetime import date
from uuid import uuid4, UUID

import streamlit as st
import pandas as pd

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

PAYMENTS_TABLE = "repayments"
REPAY_LINK_COL = "loan_id"   # ✅ confirmed
REPAY_DATE_COL = "paid_at"   # ✅ confirmed


def _is_uuid(s: str) -> bool:
    try:
        UUID(str(s))
        return True
    except Exception:
        return False


def _get_or_make_session_uuid(key: str = "actor_user_uuid") -> str:
    """Guarantee a valid UUID string even without Supabase Auth."""
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


# ============================================================
# ✅ REPAYMENTS READ HELPERS (LOCKED to your schema)
# repayments columns include: id, loan_id, member_id, amount, paid_at, amount_paid, notes, created_at, etc.
# There is NO repayments.status and NO paid_on.
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


def get_repayments_for_member(sb_service, schema: str, member_id: int, limit: int = 5000) -> list[dict]:
    return (
        sb_service.schema(schema).table(PAYMENTS_TABLE)
        .select("*")
        .eq("member_id", int(member_id))
        .order(REPAY_DATE_COL, desc=True)
        .limit(int(limit))
        .execute().data
        or []
    )


def render_loans(sb_service, schema: str, actor_user_id: str = ""):
    actor_user_uuid = actor_user_id if (actor_user_id and _is_uuid(actor_user_id)) else _get_or_make_session_uuid()
    actor = _actor_from_session(actor_user_uuid)

    st.header("Loans (Organizational Standard)")

    # ============================================================
    # KPIs (visible to all) - treat OPEN + ACTIVE as "active"
    # loans_legacy.status default is 'open'
    # ============================================================
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

    # ============================================================
    # Menu
    # ============================================================
    sections = allowed_sections(actor.role)
    if not sections:
        st.warning("No sections available for your role.")
        return

    if "loans_menu" not in st.session_state:
        st.session_state["loans_menu"] = sections[0]

    section = st.selectbox(
        "Loans menu",
        sections,
        index=sections.index(st.session_state["loans_menu"]) if st.session_state["loans_menu"] in sections else 0,
        key="loans_menu",
    )

    # ============================================================
    # ---- Requests ---- (unchanged; uses loans_core for requests/signatures)
    # ============================================================
    if section == "Requests":
        st.subheader("Loan Requests (Submit + Signatures)")
        st.caption("Submit a request, then Borrower/Surety/Treasury sign here. Admin approves later.")

        if actor.role == ROLE_ADMIN:
            st.divider()
            st.subheader("Admin Approval (Approve / Deny)")

            pending_rows = core.list_pending_requests(sb_service, schema, limit=300)
            df_pending = pd.DataFrame(pending_rows)

            if df_pending.empty:
                st.success("No pending loan requests.")
            else:
                df_pending["label"] = df_pending.apply(
                    lambda r: f"Req {int(r['id'])} • {r.get('requester_name','')} • {float(r['amount']):,.0f}",
                    axis=1
                )

                pick_req = st.selectbox("Select pending request", df_pending["label"].tolist(), key="admin_pick_loan_req")
                req_id_admin = int(df_pending[df_pending["label"] == pick_req].iloc[0]["id"])
                req = core.get_request(sb_service, schema, req_id_admin)

                st.caption(
                    f"Request #{req_id_admin} | "
                    f"Borrower: {req.get('requester_name')} | "
                    f"Surety: {req.get('surety_name')} | "
                    f"Amount: {float(req.get('amount') or 0):,.0f}"
                )

                df_sig_admin = core.sig_df(sb_service, schema, "loan", req_id_admin)
                st.dataframe(df_sig_admin, use_container_width=True, hide_index=True)

                miss = core.missing_roles(df_sig_admin, core.LOAN_SIG_REQUIRED)
                if miss:
                    st.warning("Missing signatures: " + ", ".join(miss))
                else:
                    st.success("All required signatures present.")

                colA, colB = st.columns(2)

                with colA:
                    if st.button("✅ Approve Loan Request", use_container_width=True, key=f"admin_approve_{req_id_admin}"):
                        try:
                            loan_id = core.approve_loan_request(sb_service, schema, req_id_admin, actor_user_id=actor.user_id)
                            audit(sb_service, "loan_request_approved", "ok",
                                  {"request_id": req_id_admin, "loan_id": loan_id}, actor_user_id=actor.user_id)
                            st.success(f"Loan approved. loan_legacy_id = {loan_id}")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

                with colB:
                    deny_reason = st.text_input("Deny reason", value="Not approved", key=f"deny_reason_{req_id_admin}")
                    if st.button("❌ Deny Loan Request", use_container_width=True, key=f"admin_deny_{req_id_admin}"):
                        try:
                            core.deny_loan_request(sb_service, schema, req_id_admin, reason=deny_reason)
                            audit(sb_service, "loan_request_denied", "ok",
                                  {"request_id": req_id_admin, "reason": deny_reason}, actor_user_id=actor.user_id)
                            st.success("Loan request denied.")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

        # Submit Request UI
        st.divider()
        require(actor.role, "submit_request")

        members = (
            sb_service.schema(schema).table("members_legacy")
            .select("id,name")
            .order("id", desc=False)
            .limit(5000).execute().data or []
        )
        dfm = pd.DataFrame(members)
        if dfm.empty:
            st.warning("No members found.")
            return

        dfm["id"] = pd.to_numeric(dfm["id"], errors="coerce").fillna(0).astype(int)
        dfm["name"] = dfm["name"].astype(str)
        labels = dfm.apply(lambda r: f"{int(r['id']):02d} • {r['name']}", axis=1).tolist()
        label_to_id = dict(zip(labels, dfm["id"].tolist()))
        label_to_name = dict(zip(labels, dfm["name"].tolist()))

        borrower_pick = st.selectbox("Borrower", labels, key="loan_req_borrower")
        surety_pick = st.selectbox("Surety", labels, key="loan_req_surety")

        borrower_id = int(label_to_id[borrower_pick])
        borrower_name = str(label_to_name[borrower_pick])
        surety_id = int(label_to_id[surety_pick])
        surety_name = str(label_to_name[surety_pick])

        amount = st.number_input("Amount", min_value=0.0, step=50.0, value=500.0, key="loan_req_amount")

        if borrower_id == surety_id:
            st.warning("Borrower and surety must be different members.")

        limit_amt = core.member_loan_limit(sb_service, schema, borrower_id)
        st.caption(f"Borrower loan limit (2× foundation): {limit_amt:,.0f}")

        if st.button("📩 Submit Loan Request", use_container_width=True, key="loan_req_submit"):
            try:
                req_id = core.create_loan_request(
                    sb_service, schema,
                    borrower_id, borrower_name,
                    surety_id, surety_name,
                    float(amount),
                    requester_user_id=actor.user_id,
                )
                st.session_state["loan_active_request_id"] = req_id
                audit(sb_service, "loan_request_created", "ok", {"request_id": req_id}, actor_user_id=actor.user_id)
                st.success(f"Request submitted. Request ID: {req_id}")
                st.rerun()
            except Exception as e:
                st.error(str(e))

        # Sign Request UI (unchanged)
        st.divider()
        st.subheader("Sign Loan Request")
        require(actor.role, "sign_request")

        req_id = st.session_state.get("loan_active_request_id")
        pending_rows = core.list_pending_requests(sb_service, schema, limit=300)
        dfp = pd.DataFrame(pending_rows)

        if dfp.empty and not req_id:
            st.info("No pending requests to sign.")
            return

        if not req_id:
            dfp["label"] = dfp.apply(
                lambda r: f"Req {int(r['id'])} • {r.get('requester_name','')} • {float(r['amount']):,.0f}",
                axis=1
            )
            pick = st.selectbox("Select request to sign", dfp["label"].tolist(), key="loan_req_sign_pick")
            req_id = int(dfp[dfp["label"] == pick].iloc[0]["id"])

        req = core.get_request(sb_service, schema, int(req_id))
        st.caption(f"Request ID: {req_id} • Amount: {float(req.get('amount') or 0):,.0f}")

        df_sig = core.sig_df(sb_service, schema, "loan", int(req_id))
        st.dataframe(df_sig, use_container_width=True, hide_index=True)

        def signature_box(role: str, default_name: str, signer_member_id: int | None, key_prefix: str):
            existing = df_sig[df_sig["role"] == role] if not df_sig.empty else pd.DataFrame()
            with st.container(border=True):
                st.markdown(f"**{role.upper()} SIGNATURE**")

                if not existing.empty:
                    row = existing.iloc[-1]
                    st.success(f"Signed by: {row.get('signer_name','')} • {str(row.get('signed_at',''))[:19]}")
                    return

                name = st.text_input("Signer name", value=default_name or "", key=f"{key_prefix}_{req_id}_{role}_name")
                confirm = st.checkbox("I confirm this signature", key=f"{key_prefix}_{req_id}_{role}_confirm")
                if st.button("Sign", use_container_width=True, key=f"{key_prefix}_{req_id}_{role}_btn"):
                    if not confirm:
                        st.error("Please confirm the signature checkbox.")
                        st.stop()
                    if not str(name).strip():
                        st.error("Signer name is required.")
                        st.stop()

                    core.insert_signature(
                        sb_service, schema, "loan", int(req_id),
                        role=role,
                        signer_name=str(name).strip(),
                        signer_member_id=signer_member_id,
                    )
                    st.success("Signed.")
                    st.rerun()

        signature_box("borrower", str(req.get("requester_name") or ""), int(req.get("requester_member_id") or 0) or None, "loan_sig")
        signature_box("surety", str(req.get("surety_name") or ""), int(req.get("surety_member_id") or 0) or None, "loan_sig")

        treasury_signer_mid = actor.member_id if actor.role == ROLE_TREASURY else None
        treasury_default_name = (actor.name or "Treasury") if actor.role == ROLE_TREASURY else "Treasury"
        signature_box("treasury", treasury_default_name, int(treasury_signer_mid) if treasury_signer_mid else None, "loan_sig")

        miss = core.missing_roles(core.sig_df(sb_service, schema, "loan", int(req_id)), core.LOAN_SIG_REQUIRED)
        if miss:
            st.warning("Missing signatures: " + ", ".join(miss))
        else:
            st.success("✅ All required signatures present. Admin can approve now.")

        st.divider()
        st.subheader("Recent Requests Register")
        st.dataframe(pd.DataFrame(pending_rows), use_container_width=True, hide_index=True)

    # ============================================================
    # ---- Ledger ----
    # ============================================================
    elif section == "Ledger":
        require(actor.role, "view_ledger")
        st.subheader("Loans Ledger")
        rows = (
            sb_service.schema(schema).table("loans_legacy")
            .select("*").order("issued_at", desc=True).limit(20000)
            .execute().data or []
        )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ============================================================
    # ---- Record Payment ----
    # ============================================================
    elif section == "Record Payment":
        require(actor.role, "record_payment")
        st.subheader("Record Payment (Record into repayments)")

        loan_id = st.number_input("loan_id", min_value=1, step=1, value=1, key="loan_pay_loan_id")
        amount = st.number_input("amount", min_value=0.0, step=50.0, value=100.0, key="loan_pay_amount")
        paid_at = st.date_input("paid_at (date)", value=date.today(), key="loan_pay_date")

        if st.button("Record Repayment", use_container_width=True, key="loan_pay_record"):
            try:
                # ✅ this must match loans_core.py (updated to insert into repayments.loan_id and repayments.paid_at)
                core.record_payment_pending(
                    sb_service, schema, int(loan_id), float(amount), str(paid_at),
                    recorded_by=actor.user_id
                )
                audit(sb_service, "repayment_recorded", "ok", {"loan_id": int(loan_id)}, actor_user_id=actor.user_id)
                st.success("Repayment recorded.")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    # ============================================================
    # ---- Confirm Payments ----
    # ============================================================
    elif section == "Confirm Payments":
        require(actor.role, "confirm_payment")
        st.subheader("Confirm Payments")

        st.info("Your repayments table has no 'status' column. Use Record Payment to create repayments directly.")
        st.write("If you need maker-checker, add a status column OR create a separate pending table.")

    # ============================================================
    # ---- Reject Payments ----
    # ============================================================
    elif section == "Reject Payments":
        require(actor.role, "reject_payment")
        st.subheader("Reject Payments")

        st.info("Your repayments table has no 'status' column, so reject/confirm workflow cannot work as-is.")
        st.write("If you want maker-checker, we can add: status, confirmed_by, confirmed_at, rejected_by, rejected_at.")

    # ============================================================
    # ---- Interest ----
    # ============================================================
    elif section == "Interest":
        require(actor.role, "accrue_interest")
        st.subheader("Monthly Interest Accrual (Idempotent)")
        st.caption("Runs ONCE per month. If already run, it will do nothing.")

        if st.button("Accrue Monthly Interest", use_container_width=True, key="loan_accrue"):
            try:
                updated, total = core.accrue_monthly_interest(sb_service, schema, actor_user_id=actor.user_id)
                if updated == 0 and total == 0.0:
                    st.info("Interest already accrued for this month.")
                else:
                    st.success(f"Accrued interest on {updated} loans. Total added: {total:,.0f}")
                st.rerun()
            except Exception as e:
                st.error(str(e))

        snaps = (
            sb_service.schema(schema).table("loan_interest_snapshots")
            .select("*").order("snapshot_date", desc=True).limit(50)
            .execute().data or []
        )
        st.dataframe(pd.DataFrame(snaps), use_container_width=True, hide_index=True)

    # ============================================================
    # ---- Delinquency ---- (use SQL view if you created it)
    # ============================================================
    elif section == "Delinquency":
        require(actor.role, "view_delinquency")
        st.subheader("Delinquency (DPD)")

        # Prefer the SQL view (recommended). If it doesn't exist, fallback to a simple calc.
        try:
            rows = (
                sb_service.schema(schema).table("v_loan_dpd")
                .select("*")
                .limit(20000).execute().data or []
            )
            df = pd.DataFrame(rows)
            if df.empty:
                st.info("No rows in v_loan_dpd.")
                return

            df["dpd"] = pd.to_numeric(df.get("dpd"), errors="coerce").fillna(0).astype(int)
            df = df[df["status"].astype(str).str.lower().str.strip().isin(["open", "active"])]

            c1, c2, c3 = st.columns(3)
            c1.metric("Active/Open loans", f"{len(df):,}")
            c2.metric("Delinquent (DPD>0)", f"{len(df[df['dpd']>0]):,}")
            c3.metric("Max DPD", f"{int(df['dpd'].max()) if len(df)>0 else 0:,}")

            st.dataframe(
                df.sort_values("dpd", ascending=False),
                use_container_width=True,
                hide_index=True
            )
        except Exception:
            st.warning("DPD view not found. Create public.v_loan_dpd for best results.")

    # ============================================================
    # ---- Loan Statement ----
    # ============================================================
    elif section == "Loan Statement":
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
        if loaded_mid:
            # member info
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

            # loans
            mloans = (
                sb_service.schema(schema).table("loans_legacy")
                .select("*").eq("member_id", int(loaded_mid))
                .order("issued_at", desc=True).limit(5000)
                .execute().data or []
            )

            # ✅ Friendly behavior when there are no loans
            if not mloans:
                st.info("This member has no loans yet.")
                return

            loan_ids = [int(l["id"]) for l in mloans if l.get("id") is not None]

            # repayments (by loan_id)
            mpay = get_repayments_for_loan_ids(sb_service, schema, loan_ids, limit=5000)

            st.markdown("### Loans")
            st.dataframe(pd.DataFrame(mloans), use_container_width=True, hide_index=True)

            st.markdown("### Repayments")
            st.dataframe(pd.DataFrame(mpay), use_container_width=True, hide_index=True)

            # totals header (safe zeros)
            df_loans = pd.DataFrame(mloans)
            principal_total = float(pd.to_numeric(df_loans.get("principal"), errors="coerce").fillna(0).sum())
            principal_out = float(pd.to_numeric(df_loans.get("principal_current"), errors="coerce").fillna(0).sum())
            unpaid_int = float(pd.to_numeric(df_loans.get("unpaid_interest"), errors="coerce").fillna(0).sum())
            total_due = float(pd.to_numeric(df_loans.get("total_due"), errors="coerce").fillna(0).sum())

            a1, a2, a3, a4 = st.columns(4)
            a1.metric("Loans", f"{len(df_loans):,}")
            a2.metric("Principal Outstanding", f"{principal_out:,.0f}")
            a3.metric("Unpaid Interest", f"{unpaid_int:,.0f}")
            a4.metric("Total Due", f"{total_due:,.0f}")

            st.divider()
            st.markdown("### Download PDF")

            if make_member_loan_statement_pdf is None:
                st.warning("PDF engine not available. Ensure pdfs.py defines make_member_loan_statement_pdf.")
            else:
                pdf_bytes = make_member_loan_statement_pdf(
                    brand="theyoungshallgrow",
                    member=member,
                    cycle_info={},
                    loans=mloans,
                    payments=mpay,
                    currency="$",
                    logo_path=None,
                )
                st.download_button(
                    "⬇️ Download Loan Statement (PDF)",
                    pdf_bytes,
                    file_name=f"loan_statement_{member['member_id']:02d}_{str(member['member_name']).replace(' ', '_')}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key="dl_member_loan_statement_pdf",
                )

        st.divider()

        if actor.role in (ROLE_ADMIN, ROLE_TREASURY):
            if make_loan_statements_zip is None:
                st.info("ZIP builder not available. Ensure pdfs.py defines make_loan_statements_zip.")
            else:
                st.markdown("### Admin/Treasury: Download ALL Loan Statements (ZIP)")
                if st.button("📦 Build ZIP for all members", use_container_width=True, key="dl_all_loan_zip_btn"):
                    all_members = (
                        sb_service.schema(schema).table("members_legacy")
                        .select("id,name,position").order("id", desc=False).limit(5000)
                        .execute().data or []
                    )
                    member_statements = []
                    for m in all_members:
                        member_id = int(m["id"])
                        mloans = (
                            sb_service.schema(schema).table("loans_legacy")
                            .select("*").eq("member_id", member_id).order("issued_at", desc=True)
                            .limit(5000).execute().data or []
                        )
                        if not mloans:
                            continue
                        loan_ids = [int(l["id"]) for l in mloans if l.get("id") is not None]
                        mpay = get_repayments_for_loan_ids(sb_service, schema, loan_ids, limit=5000)
                        member_statements.append({
                            "member": {"member_id": member_id, "member_name": m.get("name"), "position": m.get("position")},
                            "loans": mloans,
                            "payments": mpay,
                        })

                    zip_bytes = make_loan_statements_zip(
                        brand="theyoungshallgrow",
                        cycle_info={},
                        member_statements=member_statements,
                        currency="$",
                        logo_path=None,
                    )
                    st.download_button(
                        "⬇️ Download All Loan Statements (ZIP)",
                        zip_bytes,
                        file_name="loan_statements_all.zip",
                        mime="application/zip",
                        use_container_width=True,
                        key="dl_all_loan_statements_zip",
                    )
