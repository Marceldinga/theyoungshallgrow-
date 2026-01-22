# loans_core.py ✅ UPDATED (requester_user_id UUID NOT NULL + signature upsert)
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
import uuid

import pandas as pd

MONTHLY_INTEREST_RATE = 0.05
LOAN_SIG_REQUIRED = ["borrower", "surety", "treasury"]


def now_iso() -> str:
    """UTC ISO string with Z suffix."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _month_key(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def _to_date(x) -> date | None:
    try:
        return date.fromisoformat(str(x)[:10])
    except Exception:
        return None


def fetch_one(query) -> dict | None:
    try:
        r = query.limit(1).execute()
        rows = getattr(r, "data", None) or []
        return rows[0] if rows else None
    except Exception:
        return None


# ---------- Signatures ----------
def sig_df(sb, schema: str, entity_type: str, entity_id: int) -> pd.DataFrame:
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
            .data
            or []
        )
    except Exception:
        rows = []

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["role", "signer_name", "signer_member_id", "signed_at"])
    return df


def missing_roles(df_sig: pd.DataFrame, required_roles: list[str]) -> list[str]:
    signed = set(df_sig["role"].tolist()) if df_sig is not None and not df_sig.empty else set()
    return [r for r in required_roles if r not in signed]


# ---------- Governance ----------
def member_loan_limit(sb, schema: str, member_id: int) -> float:
    row = (
        fetch_one(
            sb.schema(schema)
            .table("members_legacy")
            .select("foundation_contrib")
            .eq("id", int(member_id))
        )
        or {}
    )
    return max(0.0, float(row.get("foundation_contrib") or 0.0) * 2.0)


def has_active_loan(sb, schema: str, member_id: int) -> bool:
    rows = (
        sb.schema(schema)
        .table("loans_legacy")
        .select("status")
        .eq("member_id", int(member_id))
        .limit(2000)
        .execute()
        .data
        or []
    )
    return any(str(r.get("status") or "").lower().strip() == "active" for r in rows)


# ---------- Requests ----------
def create_loan_request(
    sb,
    schema: str,
    borrower_id: int,
    borrower_name: str,
    surety_id: int,
    surety_name: str,
    amount: float,
    requester_user_id: str | None = None,  # ✅ REQUIRED by DB (uuid NOT NULL)
) -> int:
    if borrower_id <= 0 or surety_id <= 0:
        raise ValueError("Invalid borrower/surety.")
    if borrower_id == surety_id:
        raise ValueError("Borrower and surety must be different.")
    if amount <= 0:
        raise ValueError("Amount must be > 0.")

    # ✅ Ensure requester_user_id is a valid UUID string
    if requester_user_id is None or str(requester_user_id).strip() == "":
        requester_user_id = str(uuid.uuid4())
    else:
        # validate format
        try:
            _ = uuid.UUID(str(requester_user_id))
        except Exception:
            raise ValueError("requester_user_id must be a valid UUID string.")

    limit_amt = member_loan_limit(sb, schema, borrower_id)
    if limit_amt > 0 and float(amount) > float(limit_amt):
        raise ValueError("Requested amount exceeds limit.")

    payload = {
        "created_at": now_iso(),
        "requester_user_id": str(requester_user_id),  # ✅ NOT NULL uuid column
        "requester_member_id": int(borrower_id),
        "requester_name": str(borrower_name),
        "surety_member_id": int(surety_id),
        "surety_name": str(surety_name),
        "amount": float(amount),
        "status": "pending",
    }

    res = sb.schema(schema).table("loan_requests").insert(payload).execute()
    row = (res.data or [None])[0]
    if not row:
        raise RuntimeError("Loan request insert failed.")
    return int(row["id"])


def list_pending_requests(sb, schema: str, limit: int = 300) -> list[dict]:
    return (
        sb.schema(schema)
        .table("loan_requests")
        .select("id,requester_user_id,requester_member_id,requester_name,surety_member_id,surety_name,amount,status,created_at")
        .eq("status", "pending")
        .order("created_at", desc=True)
        .limit(int(limit))
        .execute()
        .data
        or []
    )


def get_request(sb, schema: str, request_id: int) -> dict:
    rows = (
        sb.schema(schema)
        .table("loan_requests")
        .select("*")
        .eq("id", int(request_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise RuntimeError("Request not found.")
    return rows[0]


def insert_signature(
    sb,
    schema: str,
    entity_type: str,
    entity_id: int,
    role: str,
    signer_name: str,
    signer_member_id: int | None,
):
    """
    Uses UPSERT to avoid duplicate key errors when a role signs twice.
    Assumes unique constraint exists on (entity_type, entity_id, role).
    """
    payload = {
        "entity_type": str(entity_type),
        "entity_id": int(entity_id),
        "role": str(role).strip().lower(),
        "signer_name": str(signer_name).strip(),
        "signer_member_id": int(signer_member_id) if signer_member_id is not None else None,
        "signed_at": now_iso(),
    }
    # ✅ Upsert prevents duplicate constraint errors
    sb.schema(schema).table("signatures").upsert(payload).execute()


# ---------- Admin approval/deny (core) ----------
def approve_loan_request(sb, schema: str, request_id: int, actor_user_id: str) -> int:
    req = get_request(sb, schema, request_id)
    if str(req.get("status") or "").lower().strip() != "pending":
        raise ValueError("Only pending requests can be approved.")

    df_sig = sig_df(sb, schema, "loan", int(request_id))
    miss = missing_roles(df_sig, LOAN_SIG_REQUIRED)
    if miss:
        raise ValueError("Approval blocked. Missing signatures: " + ", ".join(miss))

    member_id = int(req.get("requester_member_id") or 0)
    amount = float(req.get("amount") or 0)
    if member_id <= 0 or amount <= 0:
        raise ValueError("Invalid request data.")

    if has_active_loan(sb, schema, member_id):
        raise ValueError("Approval blocked: member already has an active loan.")

    limit_amt = member_loan_limit(sb, schema, member_id)
    if limit_amt > 0 and amount > limit_amt:
        raise ValueError(f"Approval blocked: requested amount exceeds limit ({limit_amt:,.0f}).")

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
        "updated_at": issued,
    }

    loan_res = sb.schema(schema).table("loans_legacy").insert(loan_payload).execute()
    loan_row = (loan_res.data or [None])[0]
    if not loan_row:
        raise RuntimeError("Loan creation failed.")
    loan_id = int(loan_row["id"])

    sb.schema(schema).table("loan_requests").update({
        "status": "approved",
        "approved_at": now_iso(),
        "approved_loan_legacy_id": loan_id,
        "approved_by": actor_user_id,
    }).eq("id", int(request_id)).execute()

    return loan_id


def deny_loan_request(sb, schema: str, request_id: int, reason: str):
    sb.schema(schema).table("loan_requests").update({
        "status": "denied",
        "denied_at": now_iso(),
        "deny_reason": str(reason or "").strip(),
    }).eq("id", int(request_id)).execute()


# ---------- Payments (maker-checker) ----------
def record_payment_pending(sb, schema: str, loan_legacy_id: int, amount: float, paid_on: str, recorded_by: str):
    if amount <= 0:
        raise ValueError("Amount must be > 0.")
    sb.schema(schema).table("loan_payments").insert({
        "loan_legacy_id": int(loan_legacy_id),
        "amount": float(amount),
        "paid_on": str(paid_on),
        "status": "pending",
        "recorded_by": recorded_by,
        "note": "Recorded pending",
        "created_at": now_iso(),
    }).execute()


def _get_payment(sb, schema: str, payment_id: int) -> dict:
    pay = fetch_one(sb.schema(schema).table("loan_payments").select("*").eq("payment_id", int(payment_id)))
    if not pay:
        pay = fetch_one(sb.schema(schema).table("loan_payments").select("*").eq("id", int(payment_id)))
    if not pay:
        raise RuntimeError("Payment not found.")
    return pay


def confirm_payment(sb, schema: str, payment_id: int, confirmer: str):
    pay = _get_payment(sb, schema, payment_id)
    if str(pay.get("status") or "").lower().strip() != "pending":
        raise ValueError("Only pending payments can be confirmed.")

    loan_id = int(pay.get("loan_legacy_id") or 0)
    amt = float(pay.get("amount") or 0)
    if loan_id <= 0 or amt <= 0:
        raise ValueError("Invalid payment record.")

    loan = fetch_one(sb.schema(schema).table("loans_legacy").select("id,status,balance,total_due").eq("id", loan_id))
    if not loan:
        raise RuntimeError("Loan not found.")

    total_due = float(loan.get("total_due") or 0)
    balance = float(loan.get("balance") or 0)

    new_total_due = max(0.0, total_due - amt)
    new_balance = max(0.0, balance - amt)
    new_status = "closed" if new_total_due <= 0.0001 else (loan.get("status") or "active")

    sb.schema(schema).table("loans_legacy").update({
        "total_due": new_total_due,
        "balance": new_balance,
        "status": new_status,
        "updated_at": now_iso(),
    }).eq("id", loan_id).execute()

    key_col = "payment_id" if "payment_id" in pay else "id"
    sb.schema(schema).table("loan_payments").update({
        "status": "confirmed",
        "confirmed_by": confirmer,
        "confirmed_at": now_iso(),
    }).eq(key_col, int(payment_id)).execute()


def reject_payment(sb, schema: str, payment_id: int, rejecter: str, reason: str):
    pay = _get_payment(sb, schema, payment_id)
    if str(pay.get("status") or "").lower().strip() != "pending":
        raise ValueError("Only pending payments can be rejected.")

    key_col = "payment_id" if "payment_id" in pay else "id"
    sb.schema(schema).table("loan_payments").update({
        "status": "rejected",
        "rejected_by": rejecter,
        "rejected_at": now_iso(),
        "reject_reason": str(reason or "").strip(),
    }).eq(key_col, int(payment_id)).execute()


# ---------- Interest (idempotent) ----------
def accrue_monthly_interest(sb, schema: str, actor_user_id: str) -> tuple[int, float]:
    month = _month_key()

    existing = (
        sb.schema(schema).table("loan_interest_snapshots")
        .select("id,snapshot_month")
        .eq("snapshot_month", month)
        .limit(1).execute().data or []
    )
    if existing:
        return 0, 0.0

    loans = (
        sb.schema(schema).table("loans_legacy")
        .select("id,status,balance,accrued_interest,total_due,total_interest_generated")
        .limit(20000).execute().data or []
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

        sb.schema(schema).table("loans_legacy").update({
            "accrued_interest": accrued,
            "total_due": total_due,
            "total_interest_generated": lifetime,
            "updated_at": now_iso(),
        }).eq("id", loan_id).execute()

        updated += 1
        interest_added_total += interest

    lifetime_interest_total = sum(float(r.get("total_interest_generated") or 0) for r in loans)
    sb.schema(schema).table("loan_interest_snapshots").insert({
        "snapshot_date": str(date.today()),
        "snapshot_month": month,
        "lifetime_interest_generated": float(lifetime_interest_total),
        "created_at": now_iso(),
    }).execute()

    return updated, interest_added_total


# ---------- Delinquency ----------
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
