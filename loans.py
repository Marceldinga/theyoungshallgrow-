from datetime import date
from db import now_iso, fetch_one
from audit import audit
from payout import get_signatures, missing_roles

LOAN_SIG_REQUIRED = ["borrower", "surety", "treasury"]
MONTHLY_INTEREST_RATE = 0.05


def create_loan_request(c, requester_user_id: str, requester_member_id: int, surety_member_id: int,
                        amount: float, requester_name: str, surety_name: str, actor_user_id: str):
    payload = {
        "created_at": now_iso(),
        "requester_user_id": requester_user_id,
        "requester_member_id": int(requester_member_id),
        "surety_member_id": int(surety_member_id),
        "amount": float(amount),
        "status": "pending",
        "requester_name": requester_name,
        "surety_name": surety_name,
    }
    res = c.table("loan_requests").insert(payload).execute()
    row = (res.data or [None])[0]
    if not row:
        raise Exception("Loan request insert failed.")
    req_id = int(row["id"])
    audit(c, "loan_request_created", "ok", {"request_id": req_id, **payload}, actor_user_id=actor_user_id)
    return req_id


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

    df_sig = get_signatures(c, "loan", int(request_id))
    miss = missing_roles(df_sig, LOAN_SIG_REQUIRED)
    if miss:
        raise Exception("Approval blocked. Missing signatures: " + ", ".join(miss))

    member_id = int(req["requester_member_id"])
    amount = float(req["amount"])

    # Option 1: total_due=principal; interest accrues monthly
    loan_payload = {
        "member_id": member_id,
        "status": "active",
        "balance": amount,
        "total_due": amount,
        "total_interest_generated": 0.0,
        "accrued_interest": 0.0,
        "issued_at": now_iso(),
        "created_at": now_iso(),
    }
    loan_res = c.table("loans_legacy").insert(loan_payload).execute()
    loan_row = (loan_res.data or [None])[0]
    if not loan_row:
        raise Exception("Loan creation failed.")
    loan_id = int(loan_row["id"])

    # update request (ignore extra columns if they don't exist)
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


def apply_loan_payment(c, loan_legacy_id: int, amount: float, paid_on: str, actor_user_id: str | None = None):
    c.table("loan_payments").insert({
        "loan_legacy_id": int(loan_legacy_id),
        "amount": float(amount),
        "paid_on": str(paid_on),
    }).execute()

    loan = fetch_one(
        c.table("loans_legacy")
         .select("id,status,balance,total_due")
         .eq("id", int(loan_legacy_id))
    )
    if not loan:
        raise Exception("Loan not found.")

    total_due = float(loan.get("total_due") or 0)
    balance = float(loan.get("balance") or 0)

    new_total_due = max(0.0, total_due - float(amount))
    new_balance = max(0.0, balance - float(amount))
    new_status = "closed" if new_total_due <= 0.0001 else (loan.get("status") or "active")

    try:
        c.table("loans_legacy").update({
            "total_due": new_total_due,
            "balance": new_balance,
            "status": new_status,
            "updated_at": now_iso(),
        }).eq("id", int(loan_legacy_id)).execute()
    except Exception:
        c.table("loans_legacy").update({
            "total_due": new_total_due,
            "balance": new_balance,
            "status": new_status,
        }).eq("id", int(loan_legacy_id)).execute()

    audit(c, "loan_payment_applied", "ok",
          {"loan_id": loan_legacy_id, "amount": amount, "paid_on": paid_on, "new_total_due": new_total_due},
          actor_user_id=actor_user_id)


def accrue_monthly_interest(c, actor_user_id: str):
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

        try:
            c.table("loans_legacy").update({
                "accrued_interest": accrued,
                "total_due": total_due,
                "total_interest_generated": lifetime,
                "updated_at": now_iso(),
            }).eq("id", loan_id).execute()
        except Exception:
            c.table("loans_legacy").update({
                "accrued_interest": accrued,
                "total_due": total_due,
                "total_interest_generated": lifetime,
            }).eq("id", loan_id).execute()

        updated += 1
        interest_added_total += interest

    # snapshot
    lifetime_interest_total = 0.0
    for r in loans:
        lifetime_interest_total += float(r.get("total_interest_generated") or 0)

    c.table("loan_interest_snapshots").insert({
        "snapshot_date": str(date.today()),
        "lifetime_interest_generated": float(lifetime_interest_total),
    }).execute()

    audit(c, "monthly_interest_accrued", "ok",
          {"loans_updated": updated, "interest_added_total": interest_added_total},
          actor_user_id=actor_user_id)

    return updated, interest_added_total
