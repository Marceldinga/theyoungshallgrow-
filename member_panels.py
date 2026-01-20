import streamlit as st
from loans import create_loan_request
from payout import get_signatures, missing_roles

LOAN_SIG_REQUIRED = ["borrower", "surety", "treasury"]


def render_member_request_loan_tab(c, profile, user_email, member_labels, label_to_legacy_id, label_to_name,
                                   signature_box, show_api_error, actor_user_id: str):
    st.subheader("Request Loan (Member)")
    st.caption("Create request → borrower + surety + treasury signatures → admin approves")

    requester_member_id = int(profile["member_id"])
    requester_label = f"{requester_member_id} — {user_email}"

    col1, col2 = st.columns(2)
    with col1:
        surety_pick = st.selectbox("Select surety", member_labels, key="loan_req_surety_pick")
        surety_id = int(label_to_legacy_id[surety_pick])
        surety_name = str(label_to_name[surety_pick])
    with col2:
        amt = st.number_input("Loan amount", min_value=100.0, step=50.0, value=500.0, key="loan_req_amount")

    if st.button("Create Loan Request", use_container_width=True, key="loan_req_create_btn"):
        try:
            req_id = create_loan_request(
                c,
                requester_user_id=str(profile["id"]),
                requester_member_id=requester_member_id,
                surety_member_id=surety_id,
                amount=float(amt),
                requester_name=requester_label,
                surety_name=surety_name,
                actor_user_id=actor_user_id
            )
            st.success(f"Loan request created (ID {req_id}). Collect signatures below.")
            st.rerun()
        except Exception as e:
            show_api_error(e, "Create loan request failed")

    # Show latest request for this member
    try:
        req = (
            c.table("loan_requests")
             .select("id,created_at,amount,status,surety_member_id,surety_name")
             .eq("requester_member_id", requester_member_id)
             .order("created_at", desc=True)
             .limit(1)
             .execute()
             .data or []
        )
        req = req[0] if req else None
    except Exception:
        req = None

    if not req:
        st.info("No loan request yet.")
        return

    req_id = int(req["id"])
    st.divider()
    st.markdown(f"### Latest Request: **ID {req_id}** • status={req.get('status')} • amount={float(req.get('amount') or 0):,.0f}")

    st.markdown("### Required Signatures (Loan)")
    signature_box(c, "loan", req_id, "borrower",
                  default_name=requester_label,
                  signer_member_id=requester_member_id,
                  key_prefix="loan_sig")

    signature_box(c, "loan", req_id, "surety",
                  default_name=str(req.get("surety_name") or surety_name),
                  signer_member_id=int(req.get("surety_member_id") or surety_id),
                  key_prefix="loan_sig")

    signature_box(c, "loan", req_id, "treasury",
                  default_name=user_email,
                  signer_member_id=requester_member_id,
                  key_prefix="loan_sig")

    df_sig = get_signatures(c, "loan", req_id)
    miss = missing_roles(df_sig, LOAN_SIG_REQUIRED)
    if not miss:
        st.success("✅ All required loan signatures collected. Waiting for admin approval.")
    else:
        st.warning("⚠️ Missing: " + ", ".join(miss))
