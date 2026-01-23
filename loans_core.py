# loans_core.py ✅ COMPLETE SINGLE-FILE UPDATED (FIXED IMPORT + COMPLETES compute_dpd)
# What’s updated (per your latest decisions):
# 1) ✅ DROP OLD RULE completely.
#    New qualification rule ONLY:
#      cap(member) = contrib_total + 0.70*(foundation_paid_total + foundation_pending_total)
#      qualify if (cap(borrower) + cap(surety)) >= requested_amount
#      - self-surety allowed and counted ONCE (cap_total = cap_borrower)
#
# 2) ✅ Fix duplicate signature key error:
#    signatures upsert now uses on_conflict="entity_type,entity_id,role"
#
# 3) ✅ Maker–Checker for repayments:
#    - Maker inserts into loan_repayments_pending (status=pending)
#    - Checker confirms -> inserts into loan_repayments + updates loans_legacy balances
#    - Checker rejects -> marks pending rejected
#
# 4) ✅ Repayment confirmation updates balances (interest first, then principal) if columns exist
#
# 5) ✅ Interest accrual duplicate-key safe snapshot (month/date guard + upsert conflict)
#
# 6) ✅ FIXED: compute_dpd() signature + full implementation (was causing loans.py to fail import)
#
# Notes:
# - Assumes these tables exist:
#   - member_contribution_totals(member_id, contrib_total, foundation_paid_total, foundation_pending_total)
#   - loan_requests, loans_legacy, signatures
#   - loan_repayments_pending (maker-checker queue)
#   - loan_repayments (confirmed payments)
#   - loan_interest_snapshots
# - If a table/column is missing, this code tries to degrade gracefully.

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional, Tuple, List, Dict, Any
import uuid
import pandas as pd

MONTHLY_INTEREST_RATE = 0.05
CAP_MULT = 0.70

LOAN_SIG_REQUIRED = ["borrower", "surety", "treasury"]

# ------------------------------------------------------------
# Tables
# ------------------------------------------------------------
PAYMENTS_TABLE = "loan_repayments"                   # confirmed
PENDING_PAYMENTS_TABLE = "loan_repayments_pending"   # maker-checker pending
LEGACY_PAYMENTS_TABLE = "loan_repayments_legacy"

REPAY_LINK_COL = "loan_id"
REPAY_DATE_COL = "paid_at"

# ------------------------------------------------------------
# STATEMENT SIGNING (signatures.entity_type is NOT NULL)
# ------------------------------------------------------------
STATEMENT_SIG_ROLE = "member_statement"
STATEMENT_ENTITY_TYPE = "loan_statement"


# ============================================================
# TIME + DB HELPERS
# ============================================================
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
# SAFE COLUMN FILTERING + POSTGREST MISSING-COLUMN RETRY
# ============================================================
def _get_table_columns(sb, schema: str, table: str) -> set[str]:
    """Infer columns from one row; returns empty set if table is empty/unreadable."""
    try:
        rows = (
            sb.schema(schema)
            .table(table)
            .select("*")
            .limit(1)
            .execute()
            .data
            or []
        )
        if not rows:
            return set()
        return set(rows[0].keys())
    except Exception:
        return set()


def filter_payload_to_existing_columns(sb, schema: str, table: str, payload: dict) -> dict:
    """Filter keys to existing columns when we can infer them; otherwise return payload."""
    cols = _get_table_columns(sb, schema, table)
    if not cols:
        return payload
    return {k: v for k, v in payload.items() if k in cols}


def _drop_missing_column_from_postgrest_error(payload: dict, e: Exception) -> tuple[dict, bool]:
    """
    If PostgREST says a column doesn't exist, remove it and return (new_payload, changed=True).
    Example message:
      "Could not find the 'actor_user_id' column of 'loan_repayments_legacy' in the schema cache"
    """
    msg = str(e)
    if "Could not find the '" in msg and "' column of '" in msg:
        try:
            missing = msg.split("Could not find the '", 1)[1].split("' column", 1)[0]
            if missing in payload:
                new_payload = dict(payload)
                new_payload.pop(missing, None)
                return new_payload, True
        except Exception:
            return payload, False
    return payload, False


# ============================================================
# SIGNATURES (table: signatures) — duplicate-key safe
# ============================================================
def sig_df(sb, schema: str, entity_type: str, entity_id: int) -> pd.DataFrame:
    """signatures.entity_type is NOT NULL, so we must filter by entity_type."""
    try:
        rows = (
            sb.schema(schema)
            .table("signatures")
            .select("entity_type,role,signer_name,signer_member_id,signed_at,entity_id")
            .eq("entity_type", str(entity_type))
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
        return pd.DataFrame(columns=["entity_type", "role", "signer_name", "signer_member_id", "signed_at", "entity_id"])
    return df


def missing_roles(df_sig: pd.DataFrame, required_roles: list[str]) -> list[str]:
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
    ✅ Fixes uq_signatures_once duplicates by setting on_conflict correctly.
    Upsert = idempotent: same role signs same entity again -> updates signed_at/name.
    """
    payload = {
        "entity_type": str(entity_type),
        "entity_id": int(entity_id),
        "role": str(role).strip().lower(),
        "signer_name": str(signer_name).strip(),
        "signer_member_id": int(signer_member_id) if signer_member_id is not None else None,
        "signed_at": now_iso(),
    }
    sb.schema(schema).table("signatures").upsert(
        payload,
        on_conflict="entity_type,entity_id,role",
    ).execute()
    return True


def insert_statement_signature(
    sb,
    schema: str,
    loan_id: int,
    signer_member_id: int,
    signer_name: str,
):
    payload = {
        "entity_type": STATEMENT_ENTITY_TYPE,
        "entity_id": int(loan_id),
        "role": STATEMENT_SIG_ROLE,
        "signer_name": str(signer_name).strip(),
        "signer_member_id": int(signer_member_id),
        "signed_at": now_iso(),
    }
    sb.schema(schema).table("signatures").upsert(
        payload,
        on_conflict="entity_type,entity_id,role",
    ).execute()
    return True


def get_statement_signature(sb, schema: str, loan_id: int) -> dict | None:
    rows = (
        sb.schema(schema).table("signatures")
        .select("entity_type,role,signer_name,signer_member_id,signed_at,entity_id")
        .eq("entity_type", STATEMENT_ENTITY_TYPE)
        .eq("entity_id", int(loan_id))
        .eq("role", STATEMENT_SIG_ROLE)
        .order("signed_at", desc=True)
        .limit(1)
        .execute().data or []
    )
    return rows[0] if rows else None


# ============================================================
# NEW LOAN CAPACITY RULE (ONLY RULE)
# ============================================================
def _get_totals_row(sb, schema: str, member_id: int) -> dict:
    rows = (
        sb.schema(schema)
        .table("member_contribution_totals")
        .select("member_id,contrib_total,foundation_paid_total,foundation_pending_total")
        .eq("member_id", int(member_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if rows:
        return rows[0]
    return {
        "member_id": int(member_id),
        "contrib_total": 0,
        "foundation_paid_total": 0,
        "foundation_pending_total": 0,
    }


def _capacity_from_row(r: dict) -> float:
    contrib = float(r.get("contrib_total") or 0)
    f_paid = float(r.get("foundation_paid_total") or 0)
    f_pending = float(r.get("foundation_pending_total") or 0)
    return contrib + CAP_MULT * (f_paid + f_pending)


def check_loan_qualification(sb, schema: str, borrower_id: int, surety_id: int, amount: float) -> dict:
    """
    Qualify if:
      - self surety: cap_total = cap_borrower
      - else: cap_total = cap_borrower + cap_surety
      - cap(member) = contrib_total + 0.70*(foundation_paid_total + foundation_pending_total)
    """
    borrower = _get_totals_row(sb, schema, borrower_id)
    cap_b = _capacity_from_row(borrower)

    self_surety = int(borrower_id) == int(surety_id)
    if self_surety:
        cap_total = cap_b
        cap_s = None
        surety = None
    else:
        surety = _get_totals_row(sb, schema, surety_id)
        cap_s = _capacity_from_row(surety)
        cap_total = cap_b + cap_s

    ok = float(amount) <= float(cap_total)

    return {
        "ok": ok,
        "amount": float(amount),
        "self_surety": self_surety,
        "cap_borrower": cap_b,
        "cap_surety": cap_s,
        "cap_total": cap_total,
        "borrower_totals": borrower,
        "surety_totals": surety,
        "rule": "cap = contrib_total + 0.70*(foundation_paid_total + foundation_pending_total); cap_total = cap_b + cap_s (self-surety counts once)",
    }


# ============================================================
# GOVERNANCE (other than capacity)
# ============================================================
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
    return any(str(r.get("status") or "").lower().strip() in ("active", "open") for r in rows)


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
    requester_user_id: str | None = None,
) -> int:
    """
    ✅ self-surety allowed (borrower_id == surety_id allowed)
    """
    if borrower_id <= 0 or surety_id <= 0:
        raise ValueError("Invalid borrower/surety.")
    if amount <= 0:
        raise ValueError("Amount must be > 0.")

    if requester_user_id is None or str(requester_user_id).strip() == "":
        requester_user_id = str(uuid.uuid4())
    else:
        try:
            _ = uuid.UUID(str(requester_user_id))
        except Exception:
            raise ValueError("requester_user_id must be a valid UUID string.")

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
    """
    ✅ Uses ONLY the new borrower+surety capacity rule via member_contribution_totals.
    ✅ Treasury signs per borrowing event because request_id changes each time.
    """
    req = get_request(sb, schema, request_id)
    if str(req.get("status") or "").lower().strip() != "pending":
        raise ValueError("Only pending requests can be approved.")

    # Signatures are stored with entity_type='loan' and entity_id=request_id
    df_sig = sig_df(sb, schema, "loan", int(request_id))
    miss = missing_roles(df_sig, LOAN_SIG_REQUIRED)
    if miss:
        raise ValueError("Approval blocked. Missing/invalid signatures: " + ", ".join(miss))

    borrower_id = int(req.get("requester_member_id") or 0)
    surety_id = int(req.get("surety_member_id") or 0)
    surety_name = str(req.get("surety_name") or "").strip()
    amount = float(req.get("amount") or 0)

    if borrower_id <= 0 or surety_id <= 0 or amount <= 0:
        raise ValueError("Invalid request data.")

    if has_active_loan(sb, schema, borrower_id):
        raise ValueError("Approval blocked: borrower already has an active/open loan.")

    # ✅ NEW RULE ONLY
    cap = check_loan_qualification(sb, schema, borrower_id, surety_id, amount)
    if not cap["ok"]:
        raise ValueError(
            f"Loan rejected: principal {cap['amount']} exceeds combined capacity {cap['cap_total']:.3f} "
            f"(borrower {cap['cap_borrower']:.3f}"
            + ("" if cap["self_surety"] else f", surety {cap['cap_surety']:.3f}")
            + f", rule={cap['rule']})"
        )

    ts = now_iso()
    loan_payload = {
        "borrower_member_id": borrower_id,
        "member_id": borrower_id,
        "surety_member_id": surety_id,
        "surety_name": surety_name or None,
        "borrow_date": str(date.today()),
        "principal": float(amount),
        "principal_current": float(amount),
        "interest_rate_monthly": MONTHLY_INTEREST_RATE,
        "interest_start_at": ts,
        "status": "open",
        "updated_at": ts,
    }
    loan_payload = filter_payload_to_existing_columns(sb, schema, "loans_legacy", loan_payload)

    loan_res = sb.schema(schema).table("loans_legacy").insert(loan_payload).execute()
    loan_row = (loan_res.data or [None])[0]
    if not loan_row:
        raise RuntimeError("Loan creation failed.")
    loan_id = int(loan_row["id"])

    sb.schema(schema).table("loan_requests").update({
        "status": "approved",
        "decided_at": ts,
        "approved_loan_id": loan_id,
        "admin_note": f"approved by {actor_user_id} | cap_total={cap['cap_total']:.3f}",
    }).eq("id", int(request_id)).execute()

    return loan_id


def deny_loan_request(sb, schema: str, request_id: int, reason: str):
    sb.schema(schema).table("loan_requests").update({
        "status": "denied",
        "decided_at": now_iso(),
        "admin_note": str(reason or "").strip(),
    }).eq("id", int(request_id)).execute()


# ============================================================
# REPAYMENTS — Maker–Checker
# ============================================================
def _apply_payment_to_loan_balances(sb, schema: str, loan: dict, loan_id: int, amount: float, paid_at: str):
    """
    Apply payment interest-first then principal. Updates known columns if present.
    Safe: if some columns do not exist, we filter payload.
    """
    pay_amt = float(amount)

    unpaid_interest = float(loan.get("unpaid_interest") or 0.0)
    accrued_interest = float(loan.get("accrued_interest") or 0.0)

    principal_current = loan.get("principal_current")
    if principal_current is None:
        principal_current = loan.get("principal")
    principal_current = float(principal_current or 0.0)

    total_paid_old = float(loan.get("total_paid") or 0.0)

    # pay interest first
    unpaid_interest_new = unpaid_interest
    if unpaid_interest_new > 0:
        if pay_amt >= unpaid_interest_new:
            pay_amt -= unpaid_interest_new
            unpaid_interest_new = 0.0
        else:
            unpaid_interest_new = unpaid_interest_new - pay_amt
            pay_amt = 0.0

    # then principal
    principal_new = max(principal_current - pay_amt, 0.0)

    # total_due: use unpaid_interest if present, else accrued_interest
    interest_component = unpaid_interest_new if "unpaid_interest" in loan else accrued_interest
    total_due_new = principal_new + float(interest_component or 0.0)

    # status close if fully paid
    close_now = (principal_new <= 0.0) and (unpaid_interest_new <= 0.0)

    update_payload = {
        "principal_current": float(principal_new),
        "unpaid_interest": float(unpaid_interest_new),
        "total_due": float(total_due_new),
        "total_paid": float(total_paid_old + float(amount)),
        "updated_at": now_iso(),
        "last_paid_at": str(paid_at),
        "status": "closed" if close_now else None,
        "closed_at": now_iso() if close_now else None,
    }
    update_payload = {k: v for k, v in update_payload.items() if v is not None}
    update_payload = filter_payload_to_existing_columns(sb, schema, "loans_legacy", update_payload)

    if update_payload:
        sb.schema(schema).table("loans_legacy").update(update_payload).eq("id", int(loan_id)).execute()


def record_payment_pending(
    sb,
    schema: str,
    loan_id: int,
    amount: float,
    paid_at: str,
    recorded_by: str | None = None,
    notes: str | None = None,
):
    """
    MAKER step:
      - Inserts into loan_repayments_pending (status=pending)
      - Does NOT change loans_legacy yet
    """
    if amount <= 0:
        raise ValueError("Amount must be > 0.")
    if int(loan_id) <= 0:
        raise ValueError("Invalid loan_id.")

    loan = fetch_one(
        sb.schema(schema).table("loans_legacy")
        .select("id,member_id,status")
        .eq("id", int(loan_id))
    )
    if not loan:
        raise RuntimeError("Loan not found for repayment.")
    if str(loan.get("status") or "").lower().strip() in ("closed", "paid"):
        raise ValueError("Loan is already closed.")

    member_id = int(loan.get("member_id") or 0)
    if member_id <= 0:
        raise RuntimeError("Loan has invalid member_id.")

    payload = {
        "loan_id": int(loan_id),
        "member_id": int(member_id),
        "amount": float(amount),
        "paid_at": str(paid_at),
        "status": "pending",
        "maker_user_id": (str(recorded_by).strip() if recorded_by else None),
        "note": (str(notes or "").strip() or None),
        "created_at": now_iso(),
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    payload = filter_payload_to_existing_columns(sb, schema, PENDING_PAYMENTS_TABLE, payload)

    sb.schema(schema).table(PENDING_PAYMENTS_TABLE).insert(payload).execute()
    return True


def confirm_payment(sb, schema: str, pending_id: int, confirmer_user_id: str):
    """
    CHECKER step:
      - Inserts into loan_repayments (confirmed)
      - Updates loans_legacy balances
      - Marks pending row confirmed
    """
    if int(pending_id) <= 0:
        raise ValueError("Invalid pending_id.")

    # Read pending row
    pend = fetch_one(
        sb.schema(schema).table(PENDING_PAYMENTS_TABLE)
        .select("*")
        .eq("id", int(pending_id))
    )
    if not pend:
        raise RuntimeError("Pending payment not found.")
    if str(pend.get("status") or "").lower().strip() != "pending":
        raise ValueError("Only pending payments can be confirmed.")

    loan_id = int(pend.get("loan_id") or 0)
    amount = float(pend.get("amount") or 0)
    paid_at = str(pend.get("paid_at") or "").strip()
    if loan_id <= 0 or amount <= 0 or not paid_at:
        raise RuntimeError("Pending payment has invalid data.")

    # Insert confirmed payment
    repay_payload = {
        "loan_id": int(loan_id),
        "member_id": int(pend.get("member_id") or 0),
        "amount": float(amount),
        "paid_at": str(paid_at),
        "note": (str(pend.get("note") or "").strip() or None),
        "created_at": now_iso(),
    }
    repay_payload = {k: v for k, v in repay_payload.items() if v is not None}
    repay_payload = filter_payload_to_existing_columns(sb, schema, PAYMENTS_TABLE, repay_payload)

    sb.schema(schema).table(PAYMENTS_TABLE).insert(repay_payload).execute()

    # Update loan balances
    loan = fetch_one(
        sb.schema(schema).table("loans_legacy")
        .select("id,member_id,principal,principal_current,unpaid_interest,accrued_interest,total_due,total_paid,status")
        .eq("id", int(loan_id))
    )
    if loan:
        _apply_payment_to_loan_balances(sb, schema, loan, loan_id, amount, paid_at)

    # Mark pending confirmed
    upd = {
        "status": "confirmed",
        "checker_user_id": str(confirmer_user_id),
        "checked_at": now_iso(),
    }
    upd = filter_payload_to_existing_columns(sb, schema, PENDING_PAYMENTS_TABLE, upd)
    sb.schema(schema).table(PENDING_PAYMENTS_TABLE).update(upd).eq("id", int(pending_id)).execute()

    return True


def reject_payment(sb, schema: str, pending_id: int, rejecter_user_id: str, reason: str):
    """
    CHECKER reject:
      - Marks pending row rejected
      - Does not write to loan_repayments or loans_legacy
    """
    if int(pending_id) <= 0:
        raise ValueError("Invalid pending_id.")

    pend = fetch_one(
        sb.schema(schema).table(PENDING_PAYMENTS_TABLE)
        .select("id,status")
        .eq("id", int(pending_id))
    )
    if not pend:
        raise RuntimeError("Pending payment not found.")
    if str(pend.get("status") or "").lower().strip() != "pending":
        raise ValueError("Only pending payments can be rejected.")

    upd = {
        "status": "rejected",
        "checker_user_id": str(rejecter_user_id),
        "checked_at": now_iso(),
        "note": (str(reason or "").strip() or None),
    }
    upd = {k: v for k, v in upd.items() if v is not None}
    upd = filter_payload_to_existing_columns(sb, schema, PENDING_PAYMENTS_TABLE, upd)

    sb.schema(schema).table(PENDING_PAYMENTS_TABLE).update(upd).eq("id", int(pending_id)).execute()
    return True


# ============================================================
# LEGACY REPAYMENTS INSERT (loan_repayments_legacy)
# ============================================================
def insert_legacy_loan_repayment(
    sb,
    schema: str,
    member_id: int,
    amount: float,
    paid_at: str,
    loan_id: int | None = None,
    method: str | None = None,
    note: str | None = None,
    actor_user_id: str | None = None,
) -> dict | None:
    """
    Inserts into loan_repayments_legacy (legacy table).
    We don't assume columns; we filter when possible and retry dropping missing columns.
    """
    if int(member_id) <= 0:
        raise ValueError("Invalid member_id.")
    if float(amount) <= 0:
        raise ValueError("Amount must be > 0.")
    if not str(paid_at).strip():
        raise ValueError("paid_at is required.")

    payload = {
        "loan_id": int(loan_id) if loan_id else None,
        "member_id": int(member_id),
        "amount": float(amount),
        "paid_at": str(paid_at),
        "note": (str(note or "").strip() or None),
        # optional audit fields (may not exist)
        "recorded_by": actor_user_id,
        "actor_user_id": actor_user_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "method": str(method).strip() if method else None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    payload = filter_payload_to_existing_columns(sb, schema, LEGACY_PAYMENTS_TABLE, payload)

    for _ in range(6):
        try:
            res = sb.schema(schema).table(LEGACY_PAYMENTS_TABLE).insert(payload).execute()
            return (res.data or [None])[0]
        except Exception as e:
            new_payload, changed = _drop_missing_column_from_postgrest_error(payload, e)
            if changed:
                payload = new_payload
                continue
            raise


# ============================================================
# INTEREST (idempotent snapshot) ✅ duplicate-key safe
# ============================================================
def accrue_monthly_interest(sb, schema: str, actor_user_id: str) -> tuple[int, float]:
    month = _month_key()
    today_str = str(date.today())
    ts = now_iso()

    # Already ran this month OR already has a snapshot today -> no-op
    try:
        existing = (
            sb.schema(schema).table("loan_interest_snapshots")
            .select("id,snapshot_month,snapshot_date")
            .or_(f"snapshot_month.eq.{month},snapshot_date.eq.{today_str}")
            .limit(1).execute().data or []
        )
    except Exception:
        existing = []

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
        if str(r.get("status") or "").lower().strip() not in ("active", "open"):
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

        upd = {
            "accrued_interest": accrued_interest,
            "total_interest_generated": total_interest_generated,
            "unpaid_interest": unpaid_interest,
            "last_interest_at": ts,
            "updated_at": ts,
        }
        upd = filter_payload_to_existing_columns(sb, schema, "loans_legacy", upd)
        sb.schema(schema).table("loans_legacy").update(upd).eq("id", loan_id).execute()

        updated += 1
        interest_added_total += interest

    lifetime_interest_total = sum(float(r.get("total_interest_generated") or 0) for r in loans)

    snapshot_payload = {
        "snapshot_date": today_str,
        "snapshot_month": month,
        "lifetime_interest_generated": float(lifetime_interest_total),
        "created_at": ts,
        "actor_user_id": actor_user_id,  # may not exist; drop if PostgREST complains
    }

    # Safe upsert (some schemas enforce uniqueness on snapshot_month or snapshot_date)
    for _ in range(6):
        try:
            sb.schema(schema).table("loan_interest_snapshots").upsert(
                snapshot_payload,
                on_conflict="snapshot_month",
            ).execute()
            break
        except Exception as e:
            # try alternative on_conflict if snapshot_month isn't unique
            msg = str(e)
            if "on_conflict" in msg or "constraint" in msg:
                try:
                    sb.schema(schema).table("loan_interest_snapshots").upsert(
                        snapshot_payload,
                        on_conflict="snapshot_date",
                    ).execute()
                    break
                except Exception:
                    pass

            new_payload, changed = _drop_missing_column_from_postgrest_error(snapshot_payload, e)
            if changed:
                snapshot_payload = new_payload
                continue
            raise

    return updated, interest_added_total


# ============================================================
# DELINQUENCY (fallback; SQL view is preferred)
# ============================================================
def _parse_due_date(loan_row: dict) -> Optional[date]:
    """Try common due-date fields; returns None if unavailable."""
    for k in ("due_date", "next_due_date", "expected_due_date", "payment_due_date"):
        v = loan_row.get(k)
        d = _to_date(v)
        if d:
            return d
    return None


def _get_last_paid_on(sb, schema: str, loan_id: int) -> Optional[date]:
    """Find last paid date from confirmed repayments table; fallback to legacy if needed."""
    # confirmed
    try:
        rows = (
            sb.schema(schema)
            .table(PAYMENTS_TABLE)
            .select(REPAY_DATE_COL)
            .eq(REPAY_LINK_COL, int(loan_id))
            .order(REPAY_DATE_COL, desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            return _to_date(rows[0].get(REPAY_DATE_COL))
    except Exception:
        pass

    # legacy fallback
    try:
        rows = (
            sb.schema(schema)
            .table(LEGACY_PAYMENTS_TABLE)
            .select(REPAY_DATE_COL)
            .eq(REPAY_LINK_COL, int(loan_id))
            .order(REPAY_DATE_COL, desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        if rows:
            return _to_date(rows[0].get(REPAY_DATE_COL))
    except Exception:
        pass

    return None


def compute_dpd(loan_row: dict, last_paid_on: Optional[date]) -> int:
    """
    ✅ FIXED signature + full implementation.
    Days Past Due (DPD) – simple fallback:
      - If loan status is closed/paid/completed -> 0
      - If no due date -> 0
      - Reference date = last_paid_on if provided else today
      - dpd = max((ref_date - due_date).days, 0)
    """
    try:
        status = str(loan_row.get("status", "")).lower().strip()
        if status in ("closed", "paid", "completed", "settled"):
            return 0

        due_date = _parse_due_date(loan_row)
        if not due_date:
            return 0

        ref_date = last_paid_on if last_paid_on is not None else date.today()
        dpd = (ref_date - due_date).days
        return int(dpd) if dpd > 0 else 0
    except Exception:
        return 0


def delinquency_table(sb, schema: str, limit: int = 500) -> pd.DataFrame:
    """
    Returns a DF with dpd for open/active loans.
    If your SQL views already handle DPD, use those instead.
    """
    try:
        loans = (
            sb.schema(schema).table("loans_legacy")
            .select("id,member_id,status,principal,principal_current,unpaid_interest,total_due,due_date,next_due_date,expected_due_date,payment_due_date,borrow_date,updated_at")
            .order("updated_at", desc=True)
            .limit(int(limit))
            .execute().data or []
        )
    except Exception:
        loans = []

    if not loans:
        return pd.DataFrame()

    out = []
    for r in loans:
        if str(r.get("status") or "").lower().strip() not in ("open", "active"):
            continue
        loan_id = int(r.get("id") or 0)
        if loan_id <= 0:
            continue
        last_paid = _get_last_paid_on(sb, schema, loan_id)
        dpd = compute_dpd(r, last_paid)
        rr = dict(r)
        rr["last_paid_on"] = str(last_paid) if last_paid else None
        rr["dpd"] = int(dpd)
        out.append(rr)

    df = pd.DataFrame(out)
    if not df.empty and "dpd" in df.columns:
        df = df.sort_values("dpd", ascending=False)
    return df


# ============================================================
# SIMPLE READ HELPERS (used by loans_ui / loans.py)
# ============================================================
def list_loans(sb, schema: str, limit: int = 500) -> List[Dict[str, Any]]:
    try:
        return (
            sb.schema(schema).table("loans_legacy")
            .select("*")
            .order("updated_at", desc=True)
            .limit(int(limit))
            .execute().data or []
        )
    except Exception:
        return []


def get_loan(sb, schema: str, loan_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        sb.schema(schema).table("loans_legacy")
        .select("*")
        .eq("id", int(loan_id))
    )


def list_member_loans(sb, schema: str, member_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    try:
        return (
            sb.schema(schema).table("loans_legacy")
            .select("*")
            .eq("member_id", int(member_id))
            .order("updated_at", desc=True)
            .limit(int(limit))
            .execute().data or []
        )
    except Exception:
        return []


def list_pending_payments(sb, schema: str, limit: int = 500) -> List[Dict[str, Any]]:
    try:
        return (
            sb.schema(schema).table(PENDING_PAYMENTS_TABLE)
            .select("*")
            .order("created_at", desc=True)
            .limit(int(limit))
            .execute().data or []
        )
    except Exception:
        return []


def list_confirmed_payments(sb, schema: str, loan_id: int, limit: int = 500) -> List[Dict[str, Any]]:
    try:
        return (
            sb.schema(schema).table(PAYMENTS_TABLE)
            .select("*")
            .eq("loan_id", int(loan_id))
            .order("paid_at", desc=True)
            .limit(int(limit))
            .execute().data or []
        )
    except Exception:
        return []


def loan_statement_df(sb, schema: str, member_id: int) -> pd.DataFrame:
    """
    Statement: loans + repayments for a member (simple, UI-friendly).
    If you already have statement SQL views, use them; this is a fallback.
    """
    loans = list_member_loans(sb, schema, member_id, limit=2000)
    if not loans:
        return pd.DataFrame()

    rows = []
    for ln in loans:
        loan_id = int(ln.get("id") or 0)
        pays = list_confirmed_payments(sb, schema, loan_id, limit=5000)
        total_paid = sum(float(p.get("amount") or 0) for p in pays)
        rows.append({
            "loan_id": loan_id,
            "member_id": int(ln.get("member_id") or 0),
            "status": ln.get("status"),
            "borrow_date": ln.get("borrow_date"),
            "principal": ln.get("principal"),
            "principal_current": ln.get("principal_current"),
            "unpaid_interest": ln.get("unpaid_interest"),
            "total_due": ln.get("total_due"),
            "total_paid_confirmed": total_paid,
            "last_paid_at": ln.get("last_paid_at") or (pays[0].get("paid_at") if pays else None),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["loan_id"], ascending=False)
    return df
