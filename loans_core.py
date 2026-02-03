
# loans_core.py ✅ COMPLETE SINGLE-FILE UPDATED (NEW loan_requests schema + Maker–Checker + Ledger Interest + compute_dpd)
# -----------------------------------------------------------------------------
# ✅ FIXED: create_loan_request accepts borrower_id / surety_id / amount (so your UI won't crash)
# ✅ ALSO supports new-table params: member_id, member_name, surety_member_id, requested_amount
# ✅ loan_requests matches your NEW table (member_id, member_name, requested_amount, surety_member_id, ...)
# ✅ COMPAT: adds OLD alias keys (requester_member_id, requester_name, amount, created_at, requester_user_id)
# ✅ Maker–Checker: list_unconfirmed_payments() + confirm/reject pending queue
# ✅ Interest: writes to interest_ledger (idempotent by unique(loan_id, interest_month))
# ✅ Loans table: uses loans_legacy as your loan book
# ✅ compute_dpd + delinquency_table fallback
# -----------------------------------------------------------------------------

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
import uuid

import pandas as pd
from postgrest.exceptions import APIError

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

INTEREST_LEDGER_TABLE = "interest_ledger"            # public.interest_ledger
INTEREST_SNAPSHOTS_TABLE = "loan_interest_snapshots" # optional

LOANS_TABLE = "loans_legacy"                         # loan book
REQUESTS_TABLE = "loan_requests"                     # NEW requests table

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
    Handles messages like: Could not find the 'xxx' column of 'table' in the schema cache
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


def _table_readable(sb, schema: str, table: str) -> bool:
    try:
        sb.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


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
        return pd.DataFrame(
            columns=["entity_type", "role", "signer_name", "signer_member_id", "signed_at", "entity_id"]
        )
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
    """Upsert is idempotent via on_conflict="entity_type,entity_id,role"."""
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
        .table(LOANS_TABLE)
        .select("status,member_id")
        .eq("member_id", int(member_id))
        .limit(2000)
        .execute()
        .data
        or []
    )
    return any(str(r.get("status") or "").lower().strip() in ("active", "open") for r in rows)


# ============================================================
# REQUEST NORMALIZATION (keeps old UI code alive)
# ============================================================
def _normalize_request_row(r: dict) -> dict:
    """
    Normalize NEW loan_requests row to include OLD alias keys:
      requester_member_id, requester_name, amount, created_at, requester_user_id
    So older loans_ui.py that still references them won't crash.
    """
    out = dict(r)

    # NEW -> OLD aliases
    out.setdefault("requester_member_id", out.get("member_id"))
    out.setdefault("requester_name", out.get("member_name"))
    out.setdefault("amount", out.get("requested_amount"))
    out.setdefault("created_at", out.get("requested_at"))

    # requester_user_id no longer exists; provide stable placeholder
    rid = out.get("id")
    out.setdefault("requester_user_id", f"req-{rid}" if rid is not None else str(uuid.uuid4()))

    # surety_name no longer exists in new table
    out.setdefault("surety_name", None)

    return out


# ============================================================
# REQUESTS (✅ NEW TABLE SHAPE) — FIXED FOR borrower_id
# ============================================================
def create_loan_request(
    sb,
    schema: str,
    *,
    # ✅ UI style (this is what your page calls)
    borrower_id: Optional[int] = None,
    surety_id: Optional[int] = None,
    amount: Optional[float] = None,

    # ✅ NEW table style (direct)
    member_id: Optional[int] = None,
    member_name: Optional[str] = None,
    surety_member_id: Optional[int] = None,
    requested_amount: Optional[float] = None,

    # optional extras
    purpose: str | None = None,
    duration_months: int | None = None,
    interest_rate: float | None = None,
    notes: str | None = None,
) -> int:
    """
    Inserts a row into loan_requests (NEW schema).

    Accepts BOTH calling styles:
      A) create_loan_request(..., borrower_id=, surety_id=, amount=)
      B) create_loan_request(..., member_id=, member_name=, surety_member_id=, requested_amount=)
    """

    b_id = borrower_id if borrower_id is not None else member_id
    s_id = surety_id if surety_id is not None else surety_member_id
    amt = amount if amount is not None else requested_amount

    if b_id is None or int(b_id) <= 0:
        raise ValueError("Invalid borrower/member id.")
    if s_id is None or int(s_id) <= 0:
        raise ValueError("Invalid surety id.")
    if amt is None or float(amt) <= 0:
        raise ValueError("Amount must be > 0.")

    payload = {
        "member_id": int(b_id),
        "member_name": (str(member_name).strip() if member_name else None),
        "requested_amount": float(amt),
        "purpose": (str(purpose).strip() if purpose else None),
        "duration_months": int(duration_months) if duration_months is not None else None,
        "interest_rate": float(interest_rate) if interest_rate is not None else MONTHLY_INTEREST_RATE,
        "status": "pending",
        "requested_at": now_iso(),
        "notes": (str(notes).strip() if notes else None),
        "surety_member_id": int(s_id),
    }

    payload = {k: v for k, v in payload.items() if v is not None}
    payload = filter_payload_to_existing_columns(sb, schema, REQUESTS_TABLE, payload)

    res = sb.schema(schema).table(REQUESTS_TABLE).insert(payload).execute()
    row = (res.data or [None])[0]
    if not row or "id" not in row:
        raise RuntimeError("Loan request insert failed.")
    return int(row["id"])


def list_pending_requests(sb, schema: str, limit: int = 300) -> list[dict]:
    try:
        rows = (
            sb.schema(schema)
            .table(REQUESTS_TABLE)
            .select("*")
            .eq("status", "pending")
            .order("requested_at", desc=True)
            .limit(int(limit))
            .execute()
            .data
            or []
        )
    except Exception:
        rows = []
    return [_normalize_request_row(r) for r in (rows or [])]


def list_requests(sb, schema: str, limit: int = 500) -> list[dict]:
    try:
        rows = (
            sb.schema(schema)
            .table(REQUESTS_TABLE)
            .select("*")
            .order("requested_at", desc=True)
            .limit(int(limit))
            .execute()
            .data
            or []
        )
    except Exception:
        rows = []
    return [_normalize_request_row(r) for r in (rows or [])]


def get_request(sb, schema: str, request_id: int) -> dict:
    rows = (
        sb.schema(schema)
        .table(REQUESTS_TABLE)
        .select("*")
        .eq("id", int(request_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise RuntimeError("Request not found.")
    return _normalize_request_row(rows[0])


# ============================================================
# ADMIN APPROVAL / DENY
# ============================================================
def approve_loan_request(sb, schema: str, request_id: int, actor_name: str) -> int:
    req = get_request(sb, schema, request_id)
    if str(req.get("status") or "").lower().strip() != "pending":
        raise ValueError("Only pending requests can be approved.")

    df_sig = sig_df(sb, schema, "loan", int(request_id))
    miss = missing_roles(df_sig, LOAN_SIG_REQUIRED)
    if miss:
        raise ValueError("Approval blocked. Missing/invalid signatures: " + ", ".join(miss))

    borrower_id = int(req.get("member_id") or req.get("requester_member_id") or 0)
    surety_id = int(req.get("surety_member_id") or 0)
    amount = float(req.get("requested_amount") or req.get("amount") or 0)

    if borrower_id <= 0 or surety_id <= 0 or amount <= 0:
        raise ValueError("Invalid request data.")

    if has_active_loan(sb, schema, borrower_id):
        raise ValueError("Approval blocked: borrower already has an active/open loan.")

    cap = check_loan_qualification(sb, schema, borrower_id, surety_id, amount)
    if not cap["ok"]:
        raise ValueError(
            f"Loan rejected: principal {cap['amount']} exceeds combined capacity {cap['cap_total']:.2f} "
            f"(borrower {cap['cap_borrower']:.2f}"
            + ("" if cap["self_surety"] else f", surety {cap['cap_surety']:.2f}")
            + f", rule={cap['rule']})"
        )

    ts = now_iso()
    loan_payload = {
        "borrower_member_id": borrower_id,
        "member_id": borrower_id,
        "surety_member_id": surety_id,
        "borrow_date": str(date.today()),
        "principal": float(amount),
        "principal_current": float(amount),
        "interest_rate_monthly": float(req.get("interest_rate") or MONTHLY_INTEREST_RATE),
        "interest_start_at": ts,
        "status": "open",
        "updated_at": ts,
    }
    loan_payload = filter_payload_to_existing_columns(sb, schema, LOANS_TABLE, loan_payload)

    loan_res = sb.schema(schema).table(LOANS_TABLE).insert(loan_payload).execute()
    loan_row = (loan_res.data or [None])[0]
    if not loan_row:
        raise RuntimeError("Loan creation failed.")
    loan_id = int(loan_row["id"])

    upd = {
        "status": "approved",
        "reviewed_by": str(actor_name or "").strip() or "admin",
        "reviewed_at": ts,
        "approved_at": ts,
        "notes": (str(req.get("notes") or "").strip() + f"\napproved by {actor_name} | cap_total={cap['cap_total']:.2f}").strip(),
    }
    upd = filter_payload_to_existing_columns(sb, schema, REQUESTS_TABLE, upd)
    sb.schema(schema).table(REQUESTS_TABLE).update(upd).eq("id", int(request_id)).execute()

    return loan_id


def deny_loan_request(sb, schema: str, request_id: int, actor_name: str, reason: str):
    ts = now_iso()
    upd = {
        "status": "denied",
        "reviewed_by": str(actor_name or "").strip() or "admin",
        "reviewed_at": ts,
        "notes": (str(reason or "").strip() or "denied"),
    }
    upd = filter_payload_to_existing_columns(sb, schema, REQUESTS_TABLE, upd)
    sb.schema(schema).table(REQUESTS_TABLE).update(upd).eq("id", int(request_id)).execute()


# ============================================================
# MAKER–CHECKER READ HELPERS
# ============================================================
def list_unconfirmed_payments(sb, schema: str, limit: int = 500) -> List[Dict[str, Any]]:
    try:
        return (
            sb.schema(schema)
            .table(PENDING_PAYMENTS_TABLE)
            .select("*")
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(int(limit))
            .execute()
            .data
            or []
        )
    except Exception:
        return []


def list_rejected_pending_payments(sb, schema: str, limit: int = 500) -> List[Dict[str, Any]]:
    try:
        return (
            sb.schema(schema)
            .table(PENDING_PAYMENTS_TABLE)
            .select("*")
            .eq("status", "rejected")
            .order("checked_at", desc=True)
            .limit(int(limit))
            .execute()
            .data
            or []
        )
    except Exception:
        return []


# ============================================================
# REPAYMENTS — Maker–Checker
# ============================================================
def _apply_payment_to_loan_balances(sb, schema: str, loan: dict, loan_id: int, amount: float, paid_at: str):
    pay_amt = float(amount)

    unpaid_interest = float(loan.get("unpaid_interest") or 0.0)
    accrued_interest = float(loan.get("accrued_interest") or 0.0)

    principal_current = loan.get("principal_current")
    if principal_current is None:
        principal_current = loan.get("principal")
    principal_current = float(principal_current or 0.0)

    total_paid_old = float(loan.get("total_paid") or 0.0)

    unpaid_interest_new = unpaid_interest
    if unpaid_interest_new > 0:
        if pay_amt >= unpaid_interest_new:
            pay_amt -= unpaid_interest_new
            unpaid_interest_new = 0.0
        else:
            unpaid_interest_new = unpaid_interest_new - pay_amt
            pay_amt = 0.0

    principal_new = max(principal_current - pay_amt, 0.0)

    interest_component = unpaid_interest_new if "unpaid_interest" in loan else accrued_interest
    total_due_new = principal_new + float(interest_component or 0.0)

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
    update_payload = filter_payload_to_existing_columns(sb, schema, LOANS_TABLE, update_payload)

    if update_payload:
        sb.schema(schema).table(LOANS_TABLE).update(update_payload).eq("id", int(loan_id)).execute()


def record_payment_pending(
    sb,
    schema: str,
    loan_id: int,
    amount: float,
    paid_at: str,
    recorded_by: str | None = None,
    notes: str | None = None,
):
    if amount <= 0:
        raise ValueError("Amount must be > 0.")
    if int(loan_id) <= 0:
        raise ValueError("Invalid loan_id.")

    loan = fetch_one(
        sb.schema(schema).table(LOANS_TABLE)
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
    if int(pending_id) <= 0:
        raise ValueError("Invalid pending_id.")

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

    loan = fetch_one(
        sb.schema(schema).table(LOANS_TABLE)
        .select("id,member_id,principal,principal_current,unpaid_interest,accrued_interest,total_due,total_paid,status")
        .eq("id", int(loan_id))
    )
    if loan:
        _apply_payment_to_loan_balances(sb, schema, loan, loan_id, amount, paid_at)

    upd = {
        "status": "confirmed",
        "checker_user_id": str(confirmer_user_id),
        "checked_at": now_iso(),
    }
    upd = filter_payload_to_existing_columns(sb, schema, PENDING_PAYMENTS_TABLE, upd)
    sb.schema(schema).table(PENDING_PAYMENTS_TABLE).update(upd).eq("id", int(pending_id)).execute()

    return True


def reject_payment(sb, schema: str, pending_id: int, rejecter_user_id: str, reason: str):
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
# LEGACY REPAYMENTS INSERT (loan_repayments_legacy) — safe retry
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
# INTEREST (✅ Ledger-based + optional snapshot)
# ============================================================
def accrue_monthly_interest(sb, schema: str, actor_user_id: str) -> Tuple[int, float]:
    month = _month_key()
    today_str = str(date.today())
    ts = now_iso()

    loans = (
        sb.schema(schema).table(LOANS_TABLE)
        .select("id,member_id,status,principal,principal_current,accrued_interest,total_interest_generated,unpaid_interest,interest_rate_monthly")
        .limit(20000).execute().data or []
    )

    updated = 0
    interest_added_total = 0.0

    for r in loans:
        if str(r.get("status") or "").lower().strip() not in ("active", "open"):
            continue

        loan_id = int(r["id"])
        member_id = int(r.get("member_id") or 0)

        principal_current = float(r.get("principal_current") or r.get("principal") or 0.0)
        if principal_current <= 0:
            continue

        rate = float(r.get("interest_rate_monthly") or MONTHLY_INTEREST_RATE)
        interest = round(principal_current * rate, 2)
        if interest <= 0:
            continue

        ledger_payload = {
            "loan_id": loan_id,
            "member_id": (member_id if member_id > 0 else None),
            "amount": float(interest),
            "interest_month": month,
            "note": f"monthly interest {month}",
            "created_at": ts,
        }
        ledger_payload = {k: v for k, v in ledger_payload.items() if v is not None}
        ledger_payload = filter_payload_to_existing_columns(sb, schema, INTEREST_LEDGER_TABLE, ledger_payload)

        try:
            sb.schema(schema).table(INTEREST_LEDGER_TABLE).insert(ledger_payload).execute()
        except APIError as e:
            msg = str(e)
            if "duplicate key value" in msg or "uq_interest_ledger_loan_month" in msg:
                continue
            raise

        accrued_interest = float(r.get("accrued_interest") or 0) + float(interest)
        total_interest_generated = float(r.get("total_interest_generated") or 0) + float(interest)
        unpaid_interest = float(r.get("unpaid_interest") or 0) + float(interest)

        upd = {
            "accrued_interest": accrued_interest,
            "total_interest_generated": total_interest_generated,
            "unpaid_interest": unpaid_interest,
            "last_interest_at": ts,
            "updated_at": ts,
        }
        upd = filter_payload_to_existing_columns(sb, schema, LOANS_TABLE, upd)
        sb.schema(schema).table(LOANS_TABLE).update(upd).eq("id", loan_id).execute()

        updated += 1
        interest_added_total += float(interest)

    if _table_readable(sb, schema, INTEREST_SNAPSHOTS_TABLE):
        try:
            try:
                led = (
                    sb.schema(schema).table(INTEREST_LEDGER_TABLE)
                    .select("amount")
                    .limit(200000).execute().data or []
                )
                lifetime_total = sum(float(x.get("amount") or 0) for x in led)
            except Exception:
                lifetime_total = float(interest_added_total)

            snapshot_payload = {
                "snapshot_date": today_str,
                "snapshot_month": month,
                "lifetime_interest_generated": float(lifetime_total),
                "created_at": ts,
                "actor_user_id": actor_user_id,
            }

            snapshot_payload = {k: v for k, v in snapshot_payload.items() if v is not None}
            snapshot_payload = filter_payload_to_existing_columns(sb, schema, INTEREST_SNAPSHOTS_TABLE, snapshot_payload)

            for _ in range(6):
                try:
                    sb.schema(schema).table(INTEREST_SNAPSHOTS_TABLE).upsert(
                        snapshot_payload,
                        on_conflict="snapshot_month",
                    ).execute()
                    break
                except Exception as e:
                    new_payload, changed = _drop_missing_column_from_postgrest_error(snapshot_payload, e)
                    if changed:
                        snapshot_payload = new_payload
                        continue
                    try:
                        sb.schema(schema).table(INTEREST_SNAPSHOTS_TABLE).upsert(
                            snapshot_payload,
                            on_conflict="snapshot_date",
                        ).execute()
                        break
                    except Exception:
                        raise
        except Exception:
            pass

    return updated, float(round(interest_added_total, 2))


# ============================================================
# DELINQUENCY (fallback; SQL view is preferred)
# ============================================================
def _parse_due_date(loan_row: dict) -> Optional[date]:
    for k in ("due_date", "next_due_date", "expected_due_date", "payment_due_date"):
        d = _to_date(loan_row.get(k))
        if d:
            return d
    return None


def _get_last_paid_on(sb, schema: str, loan_id: int) -> Optional[date]:
    try:
        rows = (
            sb.schema(schema).table(PAYMENTS_TABLE)
            .select(REPAY_DATE_COL)
            .eq(REPAY_LINK_COL, int(loan_id))
            .order(REPAY_DATE_COL, desc=True)
            .limit(1)
            .execute().data or []
        )
        if rows:
            return _to_date(rows[0].get(REPAY_DATE_COL))
    except Exception:
        pass

    try:
        rows = (
            sb.schema(schema).table(LEGACY_PAYMENTS_TABLE)
            .select(REPAY_DATE_COL)
            .eq(REPAY_LINK_COL, int(loan_id))
            .order(REPAY_DATE_COL, desc=True)
            .limit(1)
            .execute().data or []
        )
        if rows:
            return _to_date(rows[0].get(REPAY_DATE_COL))
    except Exception:
        pass

    return None


def compute_dpd(loan_row: dict, last_paid_on: Optional[date]) -> int:
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
    try:
        loans = (
            sb.schema(schema).table(LOANS_TABLE)
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
# SIMPLE READ HELPERS
# ============================================================
def list_loans(sb, schema: str, limit: int = 500) -> List[Dict[str, Any]]:
    try:
        return (
            sb.schema(schema).table(LOANS_TABLE)
            .select("*")
            .order("updated_at", desc=True)
            .limit(int(limit))
            .execute().data or []
        )
    except Exception:
        return []


def get_loan(sb, schema: str, loan_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        sb.schema(schema).table(LOANS_TABLE)
        .select("*")
        .eq("id", int(loan_id))
    )


def list_member_loans(sb, schema: str, member_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    try:
        return (
            sb.schema(schema).table(LOANS_TABLE)
            .select("*")
            .eq("member_id", int(member_id))
            .order("updated_at", desc=True)
            .limit(int(limit))
            .execute().data or []
        )
    except Exception:
        return []


def list_pending_payments(sb, schema: str, limit: int = 500) -> List[Dict[str, Any]]:
    """All pending table rows (any status)."""
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
