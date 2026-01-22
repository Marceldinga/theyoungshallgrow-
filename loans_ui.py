# loans_ui.py ✅ UPDATED (safe PDF call: supports old or new pdfs.py without crashing)
from __future__ import annotations

from datetime import date
from uuid import uuid4, UUID
import inspect

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


def _build_statement_pdf(
    member: dict,
    mloans: list[dict],
    mpay: list[dict],
    statement_sig: dict | None,
) -> bytes:
    """
    Calls pdfs.make_member_loan_statement_pdf safely.
    If pdfs.py is still the old version, it will ignore statement_signature.
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

    # NOTE: keep your existing Requests / Ledger / Payments / Interest / Delinquency blocks.
    # The update below is ONLY for the Loan Statement PDF call.

    # ============================================================
    # ---- Loan Statement ---- ✅ WITH DIGITAL SIGNATURE
    # ============================================================
    if section == "Loan Statement":
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

        # ------------------------------------------------------------
        # ✅ Digital signature (per-loan)
        # ------------------------------------------------------------
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

        # ------------------------------------------------------------
        # PDF download (SAFE: works with old or new pdfs.py)
        # ------------------------------------------------------------
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
