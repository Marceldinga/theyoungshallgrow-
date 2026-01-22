# loans_ui.py ✅ UPDATED (adds Digital Statement Signing + passes signature to PDF)
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
    # ---- Requests ---- (same as before; omitted here for brevity)
    # ============================================================
    # KEEP YOUR EXISTING "Requests" CODE BLOCK HERE UNCHANGED
    # (The digital signature update is in the Loan Statement section below.)

    # ============================================================
    # ---- Ledger ----
    # ============================================================
    if section == "Ledger":
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
        notes = st.text_input("Notes (optional)", value="Repayment recorded", key="loan_pay_notes")

        if st.button("Record Repayment", use_container_width=True, key="loan_pay_record"):
            try:
                core.record_payment_pending(
                    sb_service, schema, int(loan_id), float(amount), str(paid_at),
                    recorded_by=actor.user_id,
                    notes=notes,
                )
                audit(sb_service, "repayment_recorded", "ok", {"loan_id": int(loan_id)}, actor_user_id=actor.user_id)
                st.success("Repayment recorded.")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    # ============================================================
    # ---- Confirm / Reject Payments ----
    # ============================================================
    elif section == "Confirm Payments":
        require(actor.role, "confirm_payment")
        st.subheader("Confirm Payments")
        st.info("Your repayments table has no 'status' column. Confirm workflow is not supported unless you add status fields.")

    elif section == "Reject Payments":
        require(actor.role, "reject_payment")
        st.subheader("Reject Payments")
        st.info("Your repayments table has no 'status' column. Reject workflow is not supported unless you add status fields.")

    # ============================================================
    # ---- Interest ---- (keep your existing block)
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
    # ---- Delinquency ---- (keep your existing block or view-based)
    # ============================================================
    elif section == "Delinquency":
        require(actor.role, "view_delinquency")
        st.subheader("Delinquency (DPD)")

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

            st.dataframe(df.sort_values("dpd", ascending=False), use_container_width=True, hide_index=True)
        except Exception:
            st.warning("DPD view not found. Create public.v_loan_dpd for best results.")

    # ============================================================
    # ---- Loan Statement ---- ✅ WITH DIGITAL SIGNATURE
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
        if not loaded_mid:
            return

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

        if not mloans:
            st.info("This member has no loans yet.")
            return

        loan_ids = [int(l["id"]) for l in mloans if l.get("id") is not None]
        mpay = get_repayments_for_loan_ids(sb_service, schema, loan_ids, limit=5000)

        st.markdown("### Loans")
        st.dataframe(pd.DataFrame(mloans), use_container_width=True, hide_index=True)
        st.markdown("### Repayments")
        st.dataframe(pd.DataFrame(mpay), use_container_width=True, hide_index=True)

        # totals header
        df_loans = pd.DataFrame(mloans)
        principal_out = float(pd.to_numeric(df_loans.get("principal_current"), errors="coerce").fillna(0).sum())
        unpaid_int = float(pd.to_numeric(df_loans.get("unpaid_interest"), errors="coerce").fillna(0).sum())
        total_due = float(pd.to_numeric(df_loans.get("total_due"), errors="coerce").fillna(0).sum())

        a1, a2, a3 = st.columns(3)
        a1.metric("Loans", f"{len(df_loans):,}")
        a2.metric("Principal Outstanding", f"{principal_out:,.0f}")
        a3.metric("Total Due", f"{total_due:,.0f}")

        # ------------------------------------------------------------
        # ✅ Digital signature (per-loan)
        # ------------------------------------------------------------
        st.divider()
        st.subheader("Digital Signature (Statement)")

        df_loans2 = df_loans.copy()
        df_loans2["label"] = df_loans2.apply(
            lambda r: f"Loan {int(r['id'])} • Status: {r.get('status','')} • Principal: {float(r.get('principal') or 0):,.0f}",
            axis=1
        )
        pick_loan_label = st.selectbox("Select loan to sign", df_loans2["label"].tolist(), key="stmt_sign_pick_loan")
        sign_loan_id = int(df_loans2[df_loans2["label"] == pick_loan_label].iloc[0]["id"])

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

        # ------------------------------------------------------------
        # PDF download (passes signature)
        # ------------------------------------------------------------
        st.divider()
        st.markdown("### Download PDF")

        if make_member_loan_statement_pdf is None:
            st.warning("PDF engine not available. Ensure pdfs.py defines make_member_loan_statement_pdf.")
            return

        statement_sig = core.get_statement_signature(sb_service, schema, sign_loan_id)

        pdf_bytes = make_member_loan_statement_pdf(
            brand="theyoungshallgrow",
            member=member,
            cycle_info={},
            loans=mloans,
            payments=mpay,
            statement_signature=statement_sig,  # ✅ NEW
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
