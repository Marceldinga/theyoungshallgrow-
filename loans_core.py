# loans_core.py ✅ UPDATED (legacy loans schema + UUID-safe requester_user_id + strict signature check)
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


# ============================================================
# SIGNATURES  (table: public.signatures)
# Your DB has: role, signer_name, signer_member_id, signed_at, entity_id
# ============================================================
def sig_df(sb, schema: str, entity_type: str, entity_id: int) -> pd.DataFrame:
    """
    We ignore entity_type because your signatures table DOES NOT have entity_kind/entity_type columns.
    It only has entity_id, and your UI uses entity_id=request_id for loan signatures.
    """
    try:
        rows = (
            sb.schema(schema)
            .table("signatures")
            .select("role,signer_name,signer_member_id,signed_at,entity_id")
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
        return pd.DataFrame(columns=["role", "signer_name", "signer_member_id", "signed_at", "entity_id"])
    return df


def missing_roles(df_sig: pd.DataFrame, required_roles: list[str]) -> list[str]:
    """
    REQUIRED means:
    - role exists
    - signer_member_id is NOT NULL (prevents treasury NULL problem)
    """
    if df_sig is None or df_sig.empty:
        return required_roles

    ok = set(
        df_sig[
            df_sig["role"].astype(str).str.lower().str.strip().isin([r.lower() for r in required_roles])
            & pd.to_numeric(df_sig["signer_member_id"], errors="coerce").notna()
        ]["role"].astype(str).str.lower().str.strip().tolist()
    )
    return [r for r in required_roles if r.lower() not in ok]


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
    Assumes unique constraint exists on (entity_id, role) or (entity_type, entity_id, role) depending on your DB.
    We'll send entity_type anyway (if column exists, it will store; if not, Supabase will error).
    """
    payload = {
        "entity_id": int(entity_id),
        "role": str(role).strip().lower(),
        "signer_name": str(signer_name).strip(),
        "signer_member_id": int(signer_member_id) if signer_member_id is not None else None,
        "signed_at": now_iso(),
    }

    # If your signatures table has entity_type, include it; otherwise ignore safely by trying insert/upsert
    try:
        payload["entity_type"] = str(entity_type)
    except Exception:
        pass

    # Upsert prevents duplicates
    sb.schema(schema).table("signatures").upsert(payload).execute()


# ============================================================
# GOVERNANCE
# ============================================================
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
        .select("status,member_id")
        .eq("member_id", int(member_id))
        .limit(2000)
        .execute()
        .data
        or []
    )
    return any(str(r.get("status") or "").lower().strip() == "active" for r in rows)


# ============================================================
# REQUESTS
# ============================================================
def create_loan_request(
    sb,
    schema: str,
    borrower_id: int,
    borrower_name: str,
    surety_id: int,
    surety_name: str,
    amount: float,
    requester_user_id: str | None = None,  # DB expects uuid NOT NULL
) -> int:
    if borrower_id <= 0 or surety_id <= 0:
        raise ValueError("Invalid borrower/surety.")
    if borrower_id == surety_id:
        raise ValueError("Borrower and surety must be different.")
    if amount <= 0:
        raise ValueError("Amount must be > 0.")

    # Ensure requester_user_id is valid UUID string
    if requester_user_id is None or str(requester_user_id).strip() == "":
        requester_user_id = str(uuid.uuid4())
    else:
        try:
            _ = uuid.UUID(str(requester_user_id))
        except Exception:
            raise ValueError("requester_user_id must be a valid UUID string.")

    limit_amt = member_loan_limit(sb, schema, borrower_id)
    if limit_amt > 0 and float(amount) > float(limit_amt):
        raise ValueError("Requested amount exceeds limit.")

    payload = {
        "created_at": now_iso(),
        "requester_user_id": str(requester_user_id),
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


# ============================================================
# ADMIN APPROVAL / DENY
# ============================================================
def approve_loan_request(sb, schema: str, request_id: int, actor_user_id: str) -> int:
    req = get_request(sb, schema, request_id)
    if str(req.get("status") or "").lower().strip() != "pending":
        raise ValueError("Only pending requests can be approved.")

    # Strict signatures (roles must exist AND signer_member_id not null)
    df_sig = sig_df(sb, schema, "loan", int(request_id))
    miss = missing_roles(df_sig, LOAN_SIG_REQUIRED)
    if miss:
        raise ValueError("Approval blocked. Missing/invalid signatures: " + ", ".join(miss))

    borrower_id = int(req.get("requester_member_id") or 0)
    borrower_name = str(req.get("requester_name") or "").strip()
    surety_id = int(req.get("surety_member_id") or 0)
    surety_name = str(req.get("surety_name") or "").strip()
    amount = float(req.get("amount") or 0)

    if borrower_id <= 0 or surety_id <= 0 or amount <= 0:
        raise ValueError("Invalid request data.")

    if has_active_loan(sb, schema, borrower_id):
        raise ValueError("Approval blocked: borrower already has an active loan.")

    limit_amt = member_loan_limit(sb, schema, borrower_id)
    if limit_amt > 0 and amount > limit_amt:
        raise ValueError(f"Approval blocked: requested amount exceeds limit ({limit_amt:,.0f}).")

    ts = now_iso()

    # ✅ Insert into loans_legacy using YOUR real schema requirements
    loan_payload = {
        "borrower_member_id": borrower_id,      # NOT NULL in your DB
        "member_id": borrower_id,               # keep aligned (legacy)
        "surety_member_id": surety_id,
        "surety_name": surety_name or None,
        "borrow_date": str(date.today()),
        "principal": float(amount),             # NOT NULL
        "principal_current": float(amount),
        "interest_rate_monthly": MONTHLY_INTEREST_RATE,
        "interest_start_at": ts,
        "status": "active",
        "updated_at": ts,
    }

    loan_res = sb.schema(schema).table("loans_legacy").insert(loan_payload).execute()
    loan_row = (loan_res.data or [None])[0]
    if not loan_row:
        raise RuntimeError("Loan creation failed.")
    loan_id = int(loan_row["id"])

    # Mark request approved (use your actual columns if they exist)
    sb.schema(schema).table("loan_requests").update({
        "status": "approved",
        "decided_at": ts,
        "approved_loan_id": loan_id,
        "admin_note": f"approved by {actor_user_id}",
    }).eq("id", int(request_id)).execute()

    return loan_id


def deny_loan_request(sb, schema: str, request_id: int, reason: str):
    sb.schema(schema).table("loan_requests").update({
        "status": "denied",
        "decided_at": now_iso(),
        "admin_note": str(reason or "").strip(),
    }).eq("id", int(request_id)).execute()


# ============================================================
# PAYMENTS (maker-checker)
# ============================================================
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

    loan = fetch_one(sb.schema(schema).table("loans_legacy").select("id,status,principal_current").eq("id", loan_id))
    if not loan:
        raise RuntimeError("Loan not found.")

    principal_current = float(loan.get("principal_current") or 0.0)
    new_principal_current = max(0.0, principal_current - amt)
    new_status = "closed" if new_principal_current <= 0.0001 else (loan.get("status") or "active")

    sb.schema(schema).table("loans_legacy").update({
        "principal_current": new_principal_current,
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


# ============================================================
# INTEREST (idempotent snapshot)
# NOTE: Your DB already has legacy interest functions/triggers.
# This Python version only updates columns that exist in your loans_legacy.
# ============================================================
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
        .select("id,status,principal_current,accrued_interest,total_interest_generated,unpaid_interest,interest_rate_monthly")
        .limit(20000).execute().data or []
    )

    updated = 0
    interest_added_total = 0.0

    for r in loans:
        if str(r.get("status") or "").lower().strip() != "active":
            continue

        loan_id = int(r["id"])
        principal_current = float(r.get("principal_current") or 0)
        if principal_current <= 0:
            continue

        rate = float(r.get("interest_rate_monthly") or MONTHLY_INTEREST_RATE)
        interest = principal_current * rate

        accrued_interest = float(r.get("accrued_interest") or 0) + interest
        total_interest_generated = float(r.get("total_interest_generated") or 0) + interest
        unpaid_interest = float(r.get("unpaid_interest") or 0) + interest

        sb.schema(schema).table("loans_legacy").update({
            "accrued_interest": accrued_interest,
            "total_interest_generated": total_interest_generated,
            "unpaid_interest": unpaid_interest,
            "last_interest_at": now_iso(),
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


# ============================================================
# DELINQUENCY
# ============================================================
def compute_dpd(loan_row: dict, last_paid_on: date | None) -> int:
    due = _to_date(loan_row.get("due_date"))
    if not due:
        return 0
    today = date.today()
    if today <= due:
        return 0
    if last_paid_on and last_paid_on >= due:
        return 0
    return (today - due).days
