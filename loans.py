
# loans.py ✅ FULL UPDATED SINGLE FILE (Everything included)
# Includes:
# - Member Loan Requests (submit + request register)
# - Loan Request Signatures inside Loans → Requests (borrower/surety/treasury)
# - Admin Approval/Deny helpers (used by admin_panels) with signature enforcement + governance rules
# - Maker–Checker Payments (pending → confirm/reject)
# - Monthly Interest Accrual (idempotent via snapshot_month)
# - Delinquency / DPD dashboard (based on confirmed payments)
# - Loan Statement (Preview + PDF download per member)
# - Download ALL Loan Statements ZIP (admin)
# - Mobile-friendly Loans menu (selectbox instead of tabs)

from __future__ import annotations

from datetime import date, datetime, timedelta
import streamlit as st
import pandas as pd

# ============================================================
# SAFE IMPORTS (so loans.py doesn't crash app.py if a dependency is missing)
# ============================================================
try:
    from db import now_iso, fetch_one
except Exception:
    def now_iso() -> str:
        return datetime.utcnow().isoformat()

    def fetch_one(q):
        try:
            r = q.limit(1).execute()
            rows = getattr(r, "data", None) or []
            return rows[0] if rows else None
        except Exception:
            return None

try:
    from audit import audit
except Exception:
    def audit(*args, **kwargs):
        return None

# payout.py signatures helpers may exist; fallback if not
try:
    from payout import get_signatures, missing_roles
except Exception:
    def get_signatures(*args, **kwargs):
        return pd.DataFrame(columns=["role", "signer_name", "signer_member_id", "signed_at"])

    def missing_roles(df_sig, required_roles):
        signed = set(df_sig["role"].tolist()) if df_sig is not None and not df_sig.empty else set()
        return [r for r in required_roles if r not in signed]

# PDFs (optional but recommended)
try:
    from pdfs import make_member_loan_statement_pdf, make_loan_statements_zip
except Exception:
    make_member_loan_statement_pdf = None
    make_loan_statements_zip = None


# ============================================================
# CONSTANTS / RULES
# ============================================================
MONTHLY_INTEREST_RATE = 0.05

# For loan approval (admin): borrower + surety + treasury required
LOAN_SIG_REQUIRED = ["borrower", "surety", "treasury"]
# For request signing UI
LOAN_REQUEST_SIG_REQUIRED = ["borrower", "surety", "treasury"]


# ============================================================
# SMALL UTILS
# ============================================================
def _month_key(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.year:04d}-{d.month:02d}"

def _to_date(x):
    try:
        return date.fromisoformat(str(x)[:10])
    except Exception:
        return None


# ============================================================
# SIGNATURE HELPERS (loan request signing UI)
# ============================================================
def _sig_df(sb, schema: str, entity_type: str, entity_id: int) -> pd.DataFrame:
    try:
        rows = (
            sb.schema(schema)
            .table("signatures")
            .select("role,signer_name,signer_member_id,signed_at")
            .eq("entity_type", entity_type)
            .eq("entity_id", int(entity_id))
            .order("signed_at", desc=False)
            .limit(500)
            .execute()
            .data or []
        )
    except Exception:
        rows = []
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["role", "signer_name", "signer_member_id", "signed_at"])
    return df

def _missing_roles(df_sig: pd.DataFrame, required_roles: list[str]) -> list[str]:
    signed = set(df_sig["role"].tolist()) if df_sig is not None and not df_sig.empty else set()
    return [r for r in required_roles if r not in signed]

def signature_box(
    sb,
    schema: str,
    entity_type: str,
    entity_id: int,
    role: str,
    default_name: str,
    signer_member_id: int | None,
    key_prefix: str,
):
    """
    Captures a single signature for (entity_type, entity_id, role).
    Prevents duplicates (if role already signed, just shows it).
    """
    df_sig = _sig_df(sb, schema, entity_type, entity_id)
    existing = df_sig[df_sig["role"] == role] if not df_sig.empty else pd.DataFrame()

    with st.container(border=True):
        st.markdown(f"**{role.upper()} SIGNATURE**")

        if not existing.empty:
            row = existing.iloc[-1]
            st.success(f"Signed by: {row.get('signer_name','')} • {str(row.get('signed_at',''))[:19]}")
            return True

        name = st.text_input(
            "Signer name",
            value=default_name or "",
            key=f"{key_prefix}_{entity_type}_{entity_id}_{role}_name",
        )
        confirm = st.checkbox(
            "I confirm this signature",
            key=f"{key_prefix}_{entity_type}_{entity_id}_{role}_confirm",
        )

        if st.button(
            "Sign",
            use_container_width=True,
            key=f"{key_prefix}_{entity_type}_{entity_id}_{role}_btn",
        ):
            if not confirm:
                st.error("Please confirm the signature checkbox.")
                st.stop()
            if not str(name).strip():
                st.error("Signer name is required.")
                st.stop()

            payload = {
                "entity_type": entity_type,
                "entity_id": int(entity_id),
                "role": role,
                "signer_name": str(name).strip(),
                "signer_member_id": int(signer_member_id) if signer_member_id is not None else None,
            }
            try:
                sb.schema(schema).table("signatures").insert(payload).execute()
                st.success("Signed.")
                st.rerun()
            except Exception as e:
                st.error("Failed to save signature.")
                st.code(str(e), language="text")

        return False


# ============================================================
# DATA ACCESS HELPERS
# ============================================================
def fetch_member_loans(sb, schema: str, member_id: int) -> list[dict]:
    return (
        sb.schema(schema)
        .table("loans_legacy")
        .select("*")
        .eq("member_id", int(member_id))
        .order("issued_at", desc=True)
        .limit(5000)
        .execute()
        .data or []
    )

def fetch_member_payments_for_loans(sb, schema: str, loan_ids: list[int]) -> list[dict]:
    if not loan_ids:
        return []
    # Your schema uses loan_legacy_id
    return (
        sb.schema(schema)
        .table("loan_payments")
        .select("*")
        .in_("loan_legacy_id", [int(x) for x in loan_ids])
        .order("paid_on", desc=True)
        .limit(5000)
        .execute()
        .data or []
    )

def fetch_member_payments(sb, schema: str, member_id: int) -> list[dict]:
    loans = fetch_member_loans(sb, schema, member_id)
    loan_ids = [int(l["id"]) for l in loans if l.get("id") is not None]
    return fetch_member_payments_for_loans(sb, schema, loan_ids)


# ============================================================
# GOVERNANCE RULES
# ============================================================
def _member_loan_limit(sb, schema: str, member_id: int) -> float:
    """
    Max loan = 2× foundation_contrib (organizational rule).
    """
    m = fetch_one(
        sb.schema(schema)
        .table("members_legacy")
        .select("foundation_contrib")
        .eq("id", int(member_id))
    ) or {}
    return max(0.0, float(m.get("foundation_contrib") or 0.0) * 2.0)

def _has_active_loan(sb, schema: str, member_id: int) -> bool:
    rows = (
        sb.schema(schema)
        .table("loans_legacy")
        .select("status")
        .eq("member_id", int(member_id))
        .limit(2000)
        .execute()
        .data or []
    )
    return any(str(r.get("status") or "").lower().strip() == "active" for r in rows)


# ============================================================
# ADMIN WORKFLOW HELPERS (called by admin_panels.py)
# ============================================================
def approve_loan_request(c, request_id: int, actor_user_id: str):
    """
    Approves a pending request, creates loans_legacy row.
    Enforces:
      - required signatures
      - one active loan per member
      - loan limit (2× foundation)
    """
    schema = "public"  # keep stable

    req = fetch_one(
        c.table("loan_requests")
        .select("*")
        .eq("id", int(request_id))
    )
    if not req:
        raise Exception("Request not found.")
    if str(req.get("status") or "").lower().strip() != "pending":
        raise Exception("Only pending requests can be approved.")

    df_sig = get_signatures(c, "loan", int(request_id))
    miss = missing_roles(df_sig, LOAN_SIG_REQUIRED)
    if miss:
        raise Exception("Approval blocked. Missing signatures: " + ", ".join(miss))

    member_id = int(req.get("requester_member_id") or 0)
    amount = float(req.get("amount") or 0)
    if member_id <= 0 or amount <= 0:
        raise Exception("Invalid request data.")

    if _has_active_loan(c, schema, member_id):
        raise Exception("Approval blocked: member already has an active loan.")

    limit_amt = _member_loan_limit(c, schema, member_id)
    if limit_amt > 0 and amount > limit_amt:
        raise Exception(f"Approval blocked: requested amount exceeds limit ({limit_amt:,.0f}).")

    issued = now_iso()
    due_date = (date.today() + timedelta(days=30)).isoformat()

    loan_payload = {
        "member_id": member_id,
        "status": "active",
        "balance": amount,
        "total_due": amount,
        "accrued_interest": 0.0,
        "total_interest_generated": 0.0,
        "issued_at": issued,
        "due_date": due_date,
        "created_at": issued,
    }

    loan_res = c.table("loans_legacy").insert(loan_payload).execute()
    loan_row = (loan_res.data or [None])[0]
    if not loan_row:
        raise Exception("Loan creation failed.")
    loan_id = int(loan_row["id"])

    try:
        c.table("loan_requests").update({
            "status": "approved",
            "approved_at": now_iso(),
            "approved_loan_legacy_id": loan_id,
        }).eq("id", int(request_id)).execute()
    except Exception:
        c.table("loan_requests").update({"status": "approved"}).eq("id", int(request_id)).execute()

    audit(c, "loan_request_approved", "ok", {"request_id": request_id, "loan_id": loan_id}, actor_user_id=actor_user_id)
    return loan_id

def deny_loan_request(c, request_id: int, reason: str, actor_user_id: str):
    try:
        c.table("loan_requests").update({
            "status": "denied",
            "denied_at": now_iso(),
            "deny_reason": str(reason or "").strip(),
        }).eq("id", int(request_id)).execute()
    except Exception:
        c.table("loan_requests").update({"status": "denied"}).eq("id", int(request_id)).execute()

    audit(c, "loan_request_denied", "ok", {"request_id": request_id, "reason": reason}, actor_user_id=actor_user_id)


# ============================================================
# MAKER–CHECKER PAYMENTS
# ============================================================
def record_loan_payment_pending(c, loan_legacy_id: int, amount: float, paid_on: str, recorded_by: str):
    c.table("loan_payments").insert({
        "loan_legacy_id": int(loan_legacy_id),
        "amount": float(amount),
        "paid_on": str(paid_on),
        "status": "pending",
        "recorded_by": recorded_by,
        "note": "Recorded pending",
    }).execute()

def confirm_loan_payment(c, payment_id: int, confirmer: str):
    pay = fetch_one(
        c.table("loan_payments")
        .select("*")
        .eq("payment_id", int(payment_id))
    )
    if not pay:
        raise Exception("Payment not found.")
    if str(pay.get("status") or "").lower().strip() != "pending":
        raise Exception("Only pending payments can be confirmed.")

    loan_id = int(pay.get("loan_legacy_id") or 0)
    amt = float(pay.get("amount") or 0)
    if loan_id <= 0 or amt <= 0:
        raise Exception("Invalid payment record.")

    loan = fetch_one(
        c.table("loans_legacy")
        .select("id,status,balance,total_due")
        .eq("id", loan_id)
    )
    if not loan:
        raise Exception("Loan not found.")

    total_due = float(loan.get("total_due") or 0)
    balance = float(loan.get("balance") or 0)

    new_total_due = max(0.0, total_due - amt)
    new_balance = max(0.0, balance - amt)
    new_status = "closed" if new_total_due <= 0.0001 else (loan.get("status") or "active")

    c.table("loans_legacy").update({
        "total_due": new_total_due,
        "balance": new_balance,
        "status": new_status,
        "updated_at": now_iso(),
    }).eq("id", loan_id).execute()

    c.table("loan_payments").update({
        "status": "confirmed",
        "confirmed_by": confirmer,
        "confirmed_at": now_iso(),
    }).eq("payment_id", int(payment_id)).execute()

def reject_loan_payment(c, payment_id: int, rejecter: str, reason: str):
    pay = fetch_one(
        c.table("loan_payments")
        .select("payment_id,status")
        .eq("payment_id", int(payment_id))
    )
    if not pay:
        raise Exception("Payment not found.")
    if str(pay.get("status") or "").lower().strip() != "pending":
        raise Exception("Only pending payments can be rejected.")

    c.table("loan_payments").update({
        "status": "rejected",
        "rejected_by": rejecter,
        "rejected_at": now_iso(),
        "reject_reason": str(reason or "").strip(),
    }).eq("payment_id", int(payment_id)).execute()


# ============================================================
# INTEREST (IDEMPOTENT)
# ============================================================
def accrue_monthly_interest(c, actor_user_id: str):
    month = _month_key()

    existing = (
        c.table("loan_interest_snapshots")
        .select("id,snapshot_month")
        .eq("snapshot_month", month)
        .limit(1)
        .execute()
        .data or []
    )
    if existing:
        return 0, 0.0

    loans = (
        c.table("loans_legacy")
        .select("id,status,balance,accrued_interest,total_due,total_interest_generated")
        .limit(20000)
        .execute()
        .data or []
    )

    updated = 0
    interest_added_total = 0.0

    for r in loans:
        if str(r.get("status") or "").lower().strip() != "active":
            continue

        loan_id = int(r["id"])
        balance = float(r.get("balance") or 0)
        if balance <= 0:
            continue

        interest = balance * MONTHLY_INTEREST_RATE
        accrued = float(r.get("accrued_interest") or 0) + interest
        total_due = float(r.get("total_due") or 0) + interest
        lifetime = float(r.get("total_interest_generated") or 0) + interest

        c.table("loans_legacy").update({
            "accrued_interest": accrued,
            "total_due": total_due,
            "total_interest_generated": lifetime,
            "updated_at": now_iso(),
        }).eq("id", loan_id).execute()

        updated += 1
        interest_added_total += interest

    lifetime_interest_total = sum(float(r.get("total_interest_generated") or 0) for r in loans)
    c.table("loan_interest_snapshots").insert({
        "snapshot_date": str(date.today()),
        "snapshot_month": month,
        "lifetime_interest_generated": float(lifetime_interest_total),
        "created_at": now_iso(),
    }).execute()

    audit(
        c,
        "monthly_interest_accrued",
        "ok",
        {"snapshot_month": month, "loans_updated": updated, "interest_added_total": interest_added_total},
        actor_user_id=actor_user_id,
    )
    return updated, interest_added_total


# ============================================================
# DELINQUENCY
# ============================================================
def compute_dpd(loan_row: dict, last_paid_on: date | None) -> int:
    due = _to_date(loan_row.get("due_date"))
    if not due:
        issued = _to_date(loan_row.get("issued_at"))
        if issued:
            due = issued + timedelta(days=30)
    if not due:
        return 0

    today = date.today()
    if today <= due:
        return 0

    if last_paid_on and last_paid_on >= due:
        return 0

    return (today - due).days


# ============================================================
# STREAMLIT UI (MOBILE-FRIENDLY)
# ============================================================
def render_loans(sb_service, schema: str, actor_user_id: str = "admin"):
    st.header("Loans (Organizational Standard)")

    # KPIs
    loans_all = (
        sb_service.schema(schema)
        .table("loans_legacy")
        .select("id,status,total_due")
        .limit(20000)
        .execute()
        .data or []
    )
    df_all = pd.DataFrame(loans_all)
    if df_all.empty:
        active_count = 0
        active_due = 0.0
    else:
        df_all["status"] = df_all["status"].astype(str)
        df_all["total_due"] = pd.to_numeric(df_all.get("total_due"), errors="coerce").fillna(0)
        active = df_all[df_all["status"].str.lower() == "active"]
        active_count = len(active)
        active_due = float(active["total_due"].sum())

    k1, k2, k3 = st.columns(3)
    k1.metric("Active loans", str(active_count))
    k2.metric("Total due (active)", f"{active_due:,.0f}")
    k3.metric("Monthly interest", "5%")

    st.divider()

    section = st.selectbox(
        "Loans menu",
        ["Requests", "Ledger", "Record Payment", "Confirm Payments", "Reject Payments", "Interest", "Delinquency", "Loan Statement"],
        index=0,
        key="loans_menu",
    )

    # -------------------------
    # REQUESTS (submit + sign)
    # -------------------------
    if section == "Requests":
        st.subheader("Loan Requests (Submit + Signatures)")
        st.caption("Submit a request, then Borrower/Surety/Treasury sign here. Admin approves later.")

        # Members list
        members = (
            sb_service.schema(schema)
            .table("members_legacy")
            .select("id,name")
            .order("id", desc=False)
            .limit(5000)
            .execute()
            .data or []
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

        limit_amt = _member_loan_limit(sb_service, schema, borrower_id)
        st.caption(f"Borrower loan limit (2× foundation): {limit_amt:,.0f}")

        if st.button("📩 Submit Loan Request", use_container_width=True, key="loan_req_submit"):
            if amount <= 0:
                st.error("Amount must be > 0.")
            elif borrower_id == surety_id:
                st.error("Borrower and surety must be different.")
            elif limit_amt > 0 and float(amount) > float(limit_amt):
                st.error("Requested amount exceeds limit.")
            else:
                res = sb_service.schema(schema).table("loan_requests").insert({
                    "created_at": now_iso(),
                    "requester_member_id": borrower_id,
                    "requester_name": borrower_name,
                    "surety_member_id": surety_id,
                    "surety_name": surety_name,
                    "amount": float(amount),
                    "status": "pending",
                }).execute()

                row = (res.data or [None])[0]
                if not row:
                    st.error("Loan request insert failed.")
                    return
                req_id = int(row["id"])
                st.session_state["loan_active_request_id"] = req_id
                audit(sb_service, "loan_request_created", "ok", {"request_id": req_id}, actor_user_id=actor_user_id)
                st.success(f"Request submitted. Request ID: {req_id}")
                st.rerun()

        st.divider()
        st.subheader("Sign Loan Request")

        # Pick request to sign
        req_id = st.session_state.get("loan_active_request_id")
        pending_rows = (
            sb_service.schema(schema)
            .table("loan_requests")
            .select("id,requester_member_id,requester_name,surety_member_id,surety_name,amount,status,created_at")
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(300)
            .execute()
            .data or []
        )
        dfp = pd.DataFrame(pending_rows)

        if dfp.empty and not req_id:
            st.info("No pending requests to sign.")
        else:
            if not req_id:
                dfp["label"] = dfp.apply(
                    lambda r: f"Req {int(r['id'])} • {r.get('requester_name','')} • {float(r['amount']):,.0f}",
                    axis=1
                )
                pick = st.selectbox("Select request to sign", dfp["label"].tolist(), key="loan_req_sign_pick")
                req_id = int(dfp[dfp["label"] == pick].iloc[0]["id"])

            req = (
                sb_service.schema(schema)
                .table("loan_requests")
                .select("*")
                .eq("id", int(req_id))
                .limit(1)
                .execute()
                .data or []
            )
            req = req[0] if req else {}

            if not req:
                st.warning("Request not found.")
            else:
                st.caption(f"Request ID: {req_id} • Amount: {float(req.get('amount') or 0):,.0f}")

                df_sig = _sig_df(sb_service, schema, "loan", int(req_id))
                st.dataframe(df_sig, use_container_width=True, hide_index=True)

                signature_box(
                    sb_service, schema, "loan", int(req_id),
                    role="borrower",
                    default_name=str(req.get("requester_name") or ""),
                    signer_member_id=int(req.get("requester_member_id") or 0) or None,
                    key_prefix="loan_sig",
                )

                signature_box(
                    sb_service, schema, "loan", int(req_id),
                    role="surety",
                    default_name=str(req.get("surety_name") or ""),
                    signer_member_id=int(req.get("surety_member_id") or 0) or None,
                    key_prefix="loan_sig",
                )

                signature_box(
                    sb_service, schema, "loan", int(req_id),
                    role="treasury",
                    default_name="Treasury",
                    signer_member_id=None,
                    key_prefix="loan_sig",
                )

                miss = _missing_roles(_sig_df(sb_service, schema, "loan", int(req_id)), LOAN_REQUEST_SIG_REQUIRED)
                if miss:
                    st.warning("Missing signatures: " + ", ".join(miss))
                else:
                    st.success("✅ All required signatures present. Admin can approve now.")

        st.divider()
        st.subheader("Recent Requests Register")
        st.dataframe(pd.DataFrame(pending_rows), use_container_width=True, hide_index=True)

    # -------------------------
    # LEDGER
    # -------------------------
    elif section == "Ledger":
        st.subheader("Loans Ledger")
        rows = sb_service.schema(schema).table("loans_legacy").select("*").order("issued_at", desc=True).limit(20000).execute().data or []
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # -------------------------
    # RECORD PAYMENT (Maker)
    # -------------------------
    elif section == "Record Payment":
        st.subheader("Record Payment (Maker → Pending)")
        loan_id = st.number_input("loan_legacy_id", min_value=1, step=1, value=1, key="loan_pay_loan_id")
        amount = st.number_input("amount", min_value=0.0, step=50.0, value=100.0, key="loan_pay_amount")
        paid_on = st.date_input("paid_on", value=date.today(), key="loan_pay_date")
        if st.button("Record Pending Payment", use_container_width=True, key="loan_pay_record"):
            if amount <= 0:
                st.error("Amount must be > 0.")
            else:
                record_loan_payment_pending(sb_service, int(loan_id), float(amount), str(paid_on), recorded_by=actor_user_id)
                audit(sb_service, "loan_payment_recorded_pending", "ok", {"loan_id": int(loan_id)}, actor_user_id=actor_user_id)
                st.success("Recorded pending. Checker must confirm.")
                st.rerun()

    # -------------------------
    # CONFIRM PAYMENTS (Checker)
    # -------------------------
    elif section == "Confirm Payments":
        st.subheader("Confirm Payments (Checker)")
        pending = sb_service.schema(schema).table("loan_payments").select("*").eq("status", "pending").order("paid_on", desc=True).limit(500).execute().data or []
        dfp = pd.DataFrame(pending)
        if dfp.empty:
            st.success("No pending payments.")
        else:
            st.dataframe(dfp, use_container_width=True, hide_index=True)
            pid = st.number_input("payment_id to confirm", min_value=1, step=1, value=int(dfp.iloc[0]["payment_id"]), key="pay_confirm_id")
            if st.button("✅ Confirm Selected Payment", use_container_width=True, key="pay_confirm_btn"):
                confirm_loan_payment(sb_service, int(pid), confirmer=actor_user_id)
                audit(sb_service, "loan_payment_confirmed", "ok", {"payment_id": int(pid)}, actor_user_id=actor_user_id)
                st.success("Confirmed and applied.")
                st.rerun()

    # -------------------------
    # REJECT PAYMENTS (Checker)
    # -------------------------
    elif section == "Reject Payments":
        st.subheader("Reject Payments (Checker)")
        pending = (
            sb_service.schema(schema)
            .table("loan_payments")
            .select("*")
            .eq("status", "pending")
            .order("paid_on", desc=True)
            .limit(500)
            .execute()
            .data or []
        )
        dfp = pd.DataFrame(pending)
        if dfp.empty:
            st.success("No pending payments to reject.")
        else:
            st.dataframe(dfp, use_container_width=True, hide_index=True)
            pid = st.number_input(
                "payment_id to reject",
                min_value=1,
                step=1,
                value=int(dfp.iloc[0]["payment_id"]),
                key="pay_reject_id"
            )
            reason = st.text_input("Reject reason", value="Invalid reference", key="pay_reject_reason")
            if st.button("❌ Reject Selected Payment", use_container_width=True, key="pay_reject_btn"):
                reject_loan_payment(sb_service, int(pid), rejecter=actor_user_id, reason=reason)
                audit(sb_service, "loan_payment_rejected", "ok", {"payment_id": int(pid), "reason": reason}, actor_user_id=actor_user_id)
                st.success("Rejected.")
                st.rerun()

    # -------------------------
    # INTEREST (Idempotent)
    # -------------------------
    elif section == "Interest":
        st.subheader("Monthly Interest Accrual (Idempotent)")
        st.caption("Runs ONCE per month. If already run, it will do nothing.")

        if st.button("Accrue Monthly Interest", use_container_width=True, key="loan_accrue"):
            updated, total = accrue_monthly_interest(sb_service, actor_user_id=actor_user_id)
            if updated == 0 and total == 0.0:
                st.info("Interest already accrued for this month.")
            else:
                st.success(f"Accrued interest on {updated} loans. Total added: {total:,.0f}")
            st.rerun()

        snaps = (
            sb_service.schema(schema)
            .table("loan_interest_snapshots")
            .select("*")
            .order("snapshot_date", desc=True)
            .limit(50)
            .execute()
            .data or []
        )
        st.dataframe(pd.DataFrame(snaps), use_container_width=True, hide_index=True)

    # -------------------------
    # DELINQUENCY (DPD)
    # -------------------------
    elif section == "Delinquency":
        st.subheader("Delinquency (DPD)")

        # Load active loans
        loans = (
            sb_service.schema(schema)
            .table("loans_legacy")
            .select("id,member_id,status,balance,total_due,issued_at,due_date")
            .limit(20000)
            .execute()
            .data or []
        )
        df_loans = pd.DataFrame(loans)

        if df_loans.empty:
            st.info("No loans found.")
            return

        # Last confirmed payment date per loan
        pays = (
            sb_service.schema(schema)
            .table("loan_payments")
            .select("loan_legacy_id,status,paid_on")
            .limit(20000)
            .execute()
            .data or []
        )
        df_pay = pd.DataFrame(pays)

        last_paid: dict[int, date] = {}
        if not df_pay.empty:
            try:
                df_pay["paid_on_dt"] = pd.to_datetime(df_pay["paid_on"], errors="coerce")
                df_pay = df_pay[df_pay["status"].astype(str).str.lower() == "confirmed"]
                for loan_id, grp in df_pay.groupby("loan_legacy_id"):
                    mx = grp["paid_on_dt"].max()
                    if pd.notna(mx):
                        last_paid[int(loan_id)] = mx.date()
            except Exception:
                pass

        rows = []
        for r in df_loans.to_dict("records"):
            if str(r.get("status") or "").lower().strip() != "active":
                continue
            lid = int(r["id"])
            dpd = compute_dpd(r, last_paid.get(lid))
            bucket = (
                "0" if dpd == 0 else
                ("1-14" if dpd <= 14 else
                 ("15-30" if dpd <= 30 else
                  ("31-60" if dpd <= 60 else "60+")))
            )
            rows.append({
                "loan_id": lid,
                "member_id": r.get("member_id"),
                "total_due": r.get("total_due"),
                "due_date": r.get("due_date"),
                "last_paid_on": str(last_paid.get(lid) or ""),
                "dpd": dpd,
                "bucket": bucket,
            })

        df_dpd = pd.DataFrame(rows)
        if df_dpd.empty:
            st.success("No active delinquent loans detected.")
        else:
            st.dataframe(df_dpd.sort_values(["bucket", "dpd"], ascending=[True, False]),
                         use_container_width=True, hide_index=True)

    # -------------------------
    # LOAN STATEMENT (Preview + PDF + ZIP)
    # -------------------------
    elif section == "Loan Statement":
        st.subheader("Loan Statement (Preview + PDF Download)")

        # Optional cycle info
        try:
            rot_rows = (
                sb_service.schema(schema)
                .table("v_dashboard_rotation")
                .select("next_payout_index,next_payout_date")
                .limit(1)
                .execute()
                .data or []
            )
            rot = rot_rows[0] if rot_rows else {}
        except Exception:
            rot = {}

        cycle_info = {
            "payout_index": rot.get("next_payout_index"),
            "payout_date": rot.get("next_payout_date"),
            "cycle_start": None,
            "cycle_end": None,
        }

        mid = st.number_input("Member ID", min_value=1, step=1, value=1, key="stmt_member_id")

        if st.button("Load Statement", use_container_width=True, key="stmt_load"):
            st.session_state["stmt_loaded_member_id"] = int(mid)

        loaded_mid = st.session_state.get("stmt_loaded_member_id")
        if loaded_mid:
            # member info
            try:
                mrow = (
                    sb_service.schema(schema)
                    .table("members_legacy")
                    .select("id,name,position")
                    .eq("id", int(loaded_mid))
                    .limit(1)
                    .execute()
                    .data or []
                )
                mrow = mrow[0] if mrow else {}
            except Exception:
                mrow = {}

            member = {
                "member_id": int(loaded_mid),
                "member_name": mrow.get("name") or f"Member {loaded_mid}",
                "position": mrow.get("position"),
            }

            mloans = fetch_member_loans(sb_service, schema, int(loaded_mid))
            loan_ids = [int(l["id"]) for l in mloans if l.get("id") is not None]
            mpay = fetch_member_payments_for_loans(sb_service, schema, loan_ids)

            st.markdown("### Loans")
            st.dataframe(pd.DataFrame(mloans), use_container_width=True, hide_index=True)

            st.markdown("### Payments")
            st.dataframe(pd.DataFrame(mpay), use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("### Download PDF")

            if make_member_loan_statement_pdf is None:
                st.warning("PDF engine not available. Ensure pdfs.py defines make_member_loan_statement_pdf.")
            else:
                pdf_bytes = make_member_loan_statement_pdf(
                    brand="theyoungshallgrow",
                    member=member,
                    cycle_info=cycle_info,
                    loans=mloans,
                    payments=mpay,
                    currency="$",
                    logo_path="assets/logo.png",
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
        st.markdown("### Admin: Download ALL Loan Statements (ZIP)")

        if make_loan_statements_zip is None:
            st.info("ZIP builder not available. Ensure pdfs.py defines make_loan_statements_zip.")
        else:
            if st.button("📦 Build ZIP for all members", use_container_width=True, key="dl_all_loan_zip_btn"):
                all_members = (
                    sb_service.schema(schema)
                    .table("members_legacy")
                    .select("id,name,position")
                    .order("id", desc=False)
                    .limit(5000)
                    .execute()
                    .data or []
                )

                member_statements = []
                for m in all_members:
                    member_id = int(m["id"])
                    mloans = fetch_member_loans(sb_service, schema, member_id)
                    loan_ids = [int(l["id"]) for l in mloans if l.get("id") is not None]
                    mpay = fetch_member_payments_for_loans(sb_service, schema, loan_ids)

                    member_statements.append({
                        "member": {"member_id": member_id, "member_name": m.get("name"), "position": m.get("position")},
                        "loans": mloans,
                        "payments": mpay,
                    })

                zip_bytes = make_loan_statements_zip(
                    brand="theyoungshallgrow",
                    cycle_info=cycle_info,
                    member_statements=member_statements,
                    currency="$",
                    logo_path="assets/logo.png",
                )

                st.download_button(
                    "⬇️ Download All Loan Statements (ZIP)",
                    zip_bytes,
                    file_name=f"loan_statements_index_{cycle_info.get('payout_index') or 'current'}.zip",
                    mime="application/zip",
                    use_container_width=True,
                    key="dl_all_loan_statements_zip",
        )
