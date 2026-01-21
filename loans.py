
# loans.py ✅ COMPLETE / ORGANIZATIONAL STANDARD (All features + Loan Statement PDF)
from __future__ import annotations

from datetime import date, datetime, timedelta
import streamlit as st
import pandas as pd

# --- Safe imports for core helpers (fallback if db.py doesn't include them)
try:
    from db import now_iso, fetch_one
except Exception:
    def now_iso() -> str:
        return datetime.utcnow().isoformat()

    def fetch_one(q):
        # q is a supabase query builder; execute and return first row
        try:
            resp = q.limit(1).execute()
            rows = getattr(resp, "data", None) or []
            return rows[0] if rows else None
        except Exception:
            return None

from audit import audit
from payout import get_signatures, missing_roles

# PDF utilities (must exist in pdfs.py)
try:
    from pdfs import make_member_loan_statement_pdf, make_loan_statements_zip
except Exception:
    make_member_loan_statement_pdf = None
    make_loan_statements_zip = None

LOAN_SIG_REQUIRED = ["borrower", "surety", "treasury"]
MONTHLY_INTEREST_RATE = 0.05


# ============================================================
# DATA ACCESS HELPERS
# ============================================================
def fetch_member_loans(sb_service, schema: str, member_id: int) -> list[dict]:
    try:
        return (
            sb_service.schema(schema)
            .table("loans_legacy")
            .select("id,member_id,status,balance,total_due,accrued_interest,total_interest_generated,issued_at,due_date,created_at,updated_at")
            .eq("member_id", int(member_id))
            .order("issued_at", desc=True)
            .limit(5000)
            .execute()
            .data
            or []
        )
    except Exception:
        return (
            sb_service.schema(schema)
            .table("loans_legacy")
            .select("id,member_id,status,balance,total_due,issued_at,due_date")
            .eq("member_id", int(member_id))
            .order("issued_at", desc=True)
            .limit(5000)
            .execute()
            .data
            or []
        )


def fetch_member_payments_for_loans(sb_service, schema: str, loan_ids: list[int]) -> list[dict]:
    if not loan_ids:
        return []

    # Your schema uses loan_legacy_id
    try:
        return (
            sb_service.schema(schema)
            .table("loan_payments")
            .select("payment_id,loan_legacy_id,amount,status,paid_at,paid_on,recorded_by,confirmed_by,confirmed_at,rejected_by,rejected_at,reject_reason,note")
            .in_("loan_legacy_id", [int(x) for x in loan_ids])
            .order("paid_on", desc=True)
            .limit(5000)
            .execute()
            .data
            or []
        )
    except Exception:
        return (
            sb_service.schema(schema)
            .table("loan_payments")
            .select("payment_id,loan_legacy_id,amount,paid_on,note")
            .in_("loan_legacy_id", [int(x) for x in loan_ids])
            .order("paid_on", desc=True)
            .limit(5000)
            .execute()
            .data
            or []
        )


def fetch_member_payments(sb_service, schema: str, member_id: int) -> list[dict]:
    loans = fetch_member_loans(sb_service, schema, member_id)
    loan_ids = [int(l["id"]) for l in loans if l.get("id") is not None]
    return fetch_member_payments_for_loans(sb_service, schema, loan_ids)


def _month_key(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def _to_date(x) -> date | None:
    if not x:
        return None
    try:
        if isinstance(x, date):
            return x
        return date.fromisoformat(str(x)[:10])
    except Exception:
        return None


# ============================================================
# GOVERNANCE RULES (Loan limits)
# ============================================================
def _member_loan_limit(sb_service, schema: str, member_id: int) -> float:
    """
    Standard rule: max loan = 2x foundation_contrib
    Adjust multiplier if you want.
    """
    try:
        m = fetch_one(
            sb_service.schema(schema)
            .table("members_legacy")
            .select("id,foundation_contrib")
            .eq("id", int(member_id))
        )
    except Exception:
        m = None

    base = float((m or {}).get("foundation_contrib") or 0.0)
    return max(0.0, base * 2.0)


def _has_active_loan(sb_service, schema: str, member_id: int) -> bool:
    rows = (
        sb_service.schema(schema)
        .table("loans_legacy")
        .select("id,status")
        .eq("member_id", int(member_id))
        .limit(2000)
        .execute()
        .data
        or []
    )
    for r in rows:
        if str(r.get("status") or "").lower().strip() == "active":
            return True
    return False


# ============================================================
# LOAN REQUEST WORKFLOW (logic)
# ============================================================
def approve_loan_request(c, request_id: int, actor_user_id: str):
    req = fetch_one(
        c.table("loan_requests")
        .select("id,requester_member_id,amount,status,created_at")
        .eq("id", int(request_id))
    )
    if not req:
        raise Exception("Request not found.")
    if str(req.get("status")) != "pending":
        raise Exception("Only pending requests can be approved.")

    # Signatures required
    df_sig = get_signatures(c, "loan", int(request_id))
    miss = missing_roles(df_sig, LOAN_SIG_REQUIRED)
    if miss:
        raise Exception("Approval blocked. Missing signatures: " + ", ".join(miss))

    member_id = int(req["requester_member_id"])
    amount = float(req["amount"])

    # Governance rules
    if _has_active_loan(c, "public", member_id):
        raise Exception("Approval blocked: member already has an active loan.")
    limit_amt = _member_loan_limit(c, "public", member_id)
    if limit_amt > 0 and amount > limit_amt:
        raise Exception(f"Approval blocked: requested amount {amount:,.0f} exceeds limit {limit_amt:,.0f}.")

    # Issue loan
    issued = now_iso()
    due_date = (date.today() + timedelta(days=30)).isoformat()  # monthly cycle

    loan_payload = {
        "member_id": member_id,
        "status": "active",
        "balance": amount,
        "total_due": amount,
        "total_interest_generated": 0.0,
        "accrued_interest": 0.0,
        "issued_at": issued,
        "due_date": due_date,
        "created_at": issued,
    }

    loan_res = c.table("loans_legacy").insert(loan_payload).execute()
    loan_row = (loan_res.data or [None])[0]
    if not loan_row:
        raise Exception("Loan creation failed.")
    loan_id = int(loan_row["id"])

    # Update request
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
            "deny_reason": reason.strip(),
        }).eq("id", int(request_id)).execute()
    except Exception:
        c.table("loan_requests").update({"status": "denied"}).eq("id", int(request_id)).execute()

    audit(c, "loan_request_denied", "ok", {"request_id": request_id, "reason": reason}, actor_user_id=actor_user_id)


# ============================================================
# MAKER–CHECKER PAYMENTS (pending -> confirm/reject)
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
        .select("payment_id,loan_legacy_id,amount,status,paid_on")
        .eq("payment_id", int(payment_id))
    )
    if not pay:
        raise Exception("Payment not found.")
    if str(pay.get("status") or "").lower() != "pending":
        raise Exception("Only pending payments can be confirmed.")

    loan_id = int(pay["loan_legacy_id"])
    amt = float(pay["amount"])

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
    if str(pay.get("status") or "").lower() != "pending":
        raise Exception("Only pending payments can be rejected.")

    c.table("loan_payments").update({
        "status": "rejected",
        "rejected_by": rejecter,
        "rejected_at": now_iso(),
        "reject_reason": reason.strip(),
    }).eq("payment_id", int(payment_id)).execute()


# ============================================================
# INTEREST ACCRUAL (Idempotent)
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
# DELINQUENCY / DPD
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
# STREAMLIT UI
# ============================================================
def render_loans(sb_service, schema: str, actor_user_id: str = "admin"):
    st.header("Loans (Organizational Standard)")

    # Load loans + payments for DPD
    loan_rows = (
        sb_service.schema(schema)
        .table("loans_legacy")
        .select("id,member_id,status,balance,total_due,accrued_interest,total_interest_generated,issued_at,due_date")
        .limit(20000)
        .execute()
        .data or []
    )
    df_loans = pd.DataFrame(loan_rows)

    payments_rows = (
        sb_service.schema(schema)
        .table("loan_payments")
        .select("payment_id,loan_legacy_id,amount,status,paid_on")
        .limit(20000)
        .execute()
        .data or []
    )
    df_pay = pd.DataFrame(payments_rows)

    # KPIs
    active_count = 0
    active_due = 0.0
    if not df_loans.empty:
        df_loans["status"] = df_loans["status"].astype(str)
        df_loans["total_due"] = pd.to_numeric(df_loans["total_due"], errors="coerce").fillna(0)
        active = df_loans[df_loans["status"].str.lower() == "active"]
        active_count = len(active)
        active_due = float(active["total_due"].sum())

    k1, k2, k3 = st.columns(3)
    k1.metric("Active loans", str(active_count))
    k2.metric("Total due (active)", f"{active_due:,.0f}")
    k3.metric("Monthly interest rate", "5%")

    st.divider()

    tab_req, tab_ledger, tab_pay, tab_confirm, tab_interest, tab_dpd, tab_stmt = st.tabs(
        ["Requests", "Ledger", "Record Payment", "Confirm Payments", "Interest", "Delinquency", "Loan Statement"]
    )

    # ---------------- Requests
    with tab_req:
        st.subheader("Loan Requests (Maker–Checker Approval)")
        try:
            req_rows = (
                sb_service.schema(schema)
                .table("loan_requests")
                .select("*")
                .order("created_at", desc=True)
                .limit(500)
                .execute()
                .data or []
            )
        except Exception as e:
            st.error(f"Could not load loan_requests: {e}")
            req_rows = []

        df_req = pd.DataFrame(req_rows)
        if df_req.empty:
            st.info("No loan requests found.")
        else:
            st.dataframe(df_req, use_container_width=True, hide_index=True)
            pending = df_req[df_req["status"].astype(str) == "pending"].copy()
            if pending.empty:
                st.success("No pending requests.")
            else:
                pending["label"] = pending.apply(
                    lambda r: f"Req {int(r['id'])} • member {int(r['requester_member_id'])} • {float(r['amount']):,.0f}",
                    axis=1
                )
                pick = st.selectbox("Select pending request", pending["label"].tolist(), key="loan_req_pick")
                row = pending[pending["label"] == pick].iloc[0]
                req_id = int(row["id"])

                df_sig = get_signatures(sb_service, "loan", req_id)
                st.markdown("#### Signatures")
                st.dataframe(df_sig, use_container_width=True, hide_index=True)

                miss = missing_roles(df_sig, LOAN_SIG_REQUIRED)
                if miss:
                    st.warning("Missing: " + ", ".join(miss))
                else:
                    st.success("All required signatures present.")

                cA, cB = st.columns(2)
                with cA:
                    if st.button("Approve", use_container_width=True, key="loan_req_approve"):
                        try:
                            loan_id = approve_loan_request(sb_service, req_id, actor_user_id=actor_user_id)
                            st.success(f"Approved. Created loan ID: {loan_id}")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))
                with cB:
                    reason = st.text_input("Deny reason", value="Failed verification", key="loan_req_deny_reason")
                    if st.button("Deny", use_container_width=True, key="loan_req_deny"):
                        try:
                            deny_loan_request(sb_service, req_id, reason, actor_user_id=actor_user_id)
                            st.success("Denied.")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

    # ---------------- Ledger
    with tab_ledger:
        st.subheader("Loans Ledger")
        if df_loans.empty:
            st.info("No loans found.")
        else:
            st.dataframe(df_loans.sort_values("id", ascending=False), use_container_width=True, hide_index=True)

    # ---------------- Record Payment (Maker)
    with tab_pay:
        st.subheader("Record Payment (Maker) → PENDING")
        loan_id = st.number_input("loan_legacy_id", min_value=1, step=1, value=1, key="loan_pay_loan_id")
        amount = st.number_input("amount", min_value=0.0, step=50.0, value=100.0, key="loan_pay_amount")
        paid_on = st.date_input("paid_on", value=date.today(), key="loan_pay_date")

        if st.button("Record Pending Payment", use_container_width=True, key="loan_pay_record"):
            if amount <= 0:
                st.error("Amount must be > 0.")
            else:
                try:
                    record_loan_payment_pending(sb_service, int(loan_id), float(amount), str(paid_on), recorded_by=actor_user_id)
                    audit(sb_service, "loan_payment_recorded_pending", "ok",
                          {"loan_id": int(loan_id), "amount": float(amount), "paid_on": str(paid_on)},
                          actor_user_id=actor_user_id)
                    st.success("Payment recorded as PENDING. A checker must confirm it.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

    # ---------------- Confirm Payments (Checker)
    with tab_confirm:
        st.subheader("Confirm / Reject Payments (Checker)")
        pending = (
            sb_service.schema(schema)
            .table("loan_payments")
            .select("payment_id,loan_legacy_id,amount,paid_on,status,recorded_by")
            .eq("status", "pending")
            .order("paid_on", desc=True)
            .limit(500)
            .execute()
            .data or []
        )
        df_pending = pd.DataFrame(pending)
        if df_pending.empty:
            st.success("No pending payments.")
        else:
            st.dataframe(df_pending, use_container_width=True, hide_index=True)

            df_pending["label"] = df_pending.apply(
                lambda r: f"Pay {int(r['payment_id'])} • Loan {int(r['loan_legacy_id'])} • {float(r['amount']):,.0f} • {str(r['paid_on'])}",
                axis=1
            )
            pick = st.selectbox("Select pending payment", df_pending["label"].tolist(), key="pay_confirm_pick")
            row = df_pending[df_pending["label"] == pick].iloc[0]
            pid = int(row["payment_id"])

            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Confirm Payment", use_container_width=True, key="pay_confirm_btn"):
                    try:
                        confirm_loan_payment(sb_service, pid, confirmer=actor_user_id)
                        audit(sb_service, "loan_payment_confirmed", "ok", {"payment_id": pid}, actor_user_id=actor_user_id)
                        st.success("Confirmed and applied to loan.")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
            with c2:
                reason = st.text_input("Reject reason", value="Invalid reference", key="pay_reject_reason")
                if st.button("❌ Reject Payment", use_container_width=True, key="pay_reject_btn"):
                    try:
                        reject_loan_payment(sb_service, pid, rejecter=actor_user_id, reason=reason)
                        audit(sb_service, "loan_payment_rejected", "ok", {"payment_id": pid, "reason": reason}, actor_user_id=actor_user_id)
                        st.success("Rejected.")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    # ---------------- Interest (Idempotent)
    with tab_interest:
        st.subheader("Monthly Interest Accrual (Idempotent)")
        st.caption("This runs ONCE per month. If already run, it will do nothing.")

        if st.button("Accrue Monthly Interest", use_container_width=True, key="loan_accrue"):
            try:
                updated, total = accrue_monthly_interest(sb_service, actor_user_id=actor_user_id)
                if updated == 0 and total == 0.0:
                    st.info("Interest already accrued for this month.")
                else:
                    st.success(f"Accrued interest on {updated} loans. Total added: {total:,.0f}")
                st.rerun()
            except Exception as e:
                st.error(str(e))

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

    # ---------------- Delinquency
    with tab_dpd:
        st.subheader("Delinquency (DPD)")
        if df_loans.empty:
            st.info("No loans.")
        else:
            last_paid = {}
            if not df_pay.empty:
                try:
                    dfp = df_pay.copy()
                    dfp["paid_on_dt"] = pd.to_datetime(dfp["paid_on"], errors="coerce")
                    dfp = dfp[dfp["status"].astype(str).str.lower() == "confirmed"]
                    for loan_id, grp in dfp.groupby("loan_legacy_id"):
                        mx = grp["paid_on_dt"].max()
                        if pd.notna(mx):
                            last_paid[int(loan_id)] = mx.date()
                except Exception:
                    pass

            rows = []
            for r in df_loans.to_dict("records"):
                if str(r.get("status") or "").lower() != "active":
                    continue
                lid = int(r["id"])
                dpd = compute_dpd(r, last_paid.get(lid))
                bucket = "0" if dpd == 0 else ("1-14" if dpd <= 14 else ("15-30" if dpd <= 30 else ("31-60" if dpd <= 60 else "60+")))
                rows.append({
                    "loan_id": lid,
                    "member_id": r.get("member_id"),
                    "balance": r.get("balance"),
                    "total_due": r.get("total_due"),
                    "due_date": r.get("due_date"),
                    "last_paid_on": str(last_paid.get(lid) or ""),
                    "dpd": dpd,
                    "bucket": bucket,
                })

            df_dpd = pd.DataFrame(rows)
            st.dataframe(df_dpd.sort_values(["bucket", "dpd"], ascending=[True, False]), use_container_width=True, hide_index=True)

    # ---------------- Loan Statement (Preview + PDF + ZIP)
    with tab_stmt:
        st.subheader("Loan Statement (Preview + PDF Download)")

        # cycle info (optional)
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

        member_id = st.number_input("member_id", min_value=1, step=1, value=1, key="stmt_member_id")

        if st.button("Load Statement", use_container_width=True, key="stmt_load"):
            st.session_state["stmt_loaded_member_id"] = int(member_id)

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

            loans = fetch_member_loans(sb_service, schema, int(loaded_mid))
            loan_ids = [int(l["id"]) for l in loans if l.get("id") is not None]
            payments = fetch_member_payments_for_loans(sb_service, schema, loan_ids)

            st.markdown("### Loans")
            st.dataframe(pd.DataFrame(loans), use_container_width=True, hide_index=True)

            st.markdown("### Payments")
            st.dataframe(pd.DataFrame(payments), use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("### Download PDF")

            if make_member_loan_statement_pdf is None:
                st.warning("PDF engine not available. Ensure pdfs.py defines make_member_loan_statement_pdf.")
            else:
                pdf_bytes = make_member_loan_statement_pdf(
                    brand="theyoungshallgrow",
                    member=member,
                    cycle_info=cycle_info,
                    loans=loans,
                    payments=payments,
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
                    mid = int(m["id"])
                    mloans = fetch_member_loans(sb_service, schema, mid)
                    mids = [int(l["id"]) for l in mloans if l.get("id") is not None]
                    mpay = fetch_member_payments_for_loans(sb_service, schema, mids)

                    member_statements.append({
                        "member": {"member_id": mid, "member_name": m.get("name"), "position": m.get("position")},
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
