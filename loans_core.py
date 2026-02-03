
# loans_core.py ✅ COMPLETE SINGLE-FILE UPDATED
# -----------------------------------------------------------------------------
# ✅ FIX: loan_requests.member_name is NOT NULL → always populated (lookup + fallback)
# ✅ FIX: create_loan_request accepts ALL call styles (A/B/C)
# ✅ FIX: Adds signature helpers used by loans_ui.py:
#      - insert_signature()
#      - sig_df()
#      - missing_roles()
# ✅ FIX: approve_loan_request signature check now uses entity_type="loan_request" (matches UI)
# ✅ Maker–Checker: list_unconfirmed_payments() + confirm/reject pending queue
# ✅ Interest: ledger-based (interest_ledger unique(loan_id, interest_month))
# ✅ Loans book: loans_legacy
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
SIGNATURES_TABLE = "signatures"                      # signatures table

# Signatures: requests must use entity_type="loan_request" (matches loans_ui.py)
REQUEST_ENTITY_TYPE = "loan_request"
LOAN_SIG_REQUIRED = ["borrower", "surety", "treasury"]

# member name lookup candidates (try in this order)
MEMBER_NAME_TABLES = ["members", "member_registry", "members_legacy"]
MEMBER_ID_COL = "id"  # common; we also try "member_id" automatically


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


def _table_readable(sb, schema: str, table: str) -> bool:
    try:
        sb.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


# ============================================================
# SIGNATURES (✅ REQUIRED BY loans_ui.py)
# ============================================================
def sig_df(sb, schema: str, entity_type: str, entity_id: int) -> pd.DataFrame:
    """Return signatures for entity as DataFrame."""
    try:
        rows = (
            sb.schema(schema).table(SIGNATURES_TABLE)
            .select("entity_type,entity_id,role,signer_member_id,signer_name,signed_at")
            .eq("entity_type", str(entity_type))
            .eq("entity_id", int(entity_id))
            .order("signed_at", desc=False)
            .limit(1000)
            .execute().data or []
        )
    except Exception:
        rows = []
    return pd.DataFrame(rows or [])


def missing_roles(df: pd.DataFrame, required_roles: List[str]) -> List[str]:
    """
    A role counts as present if:
      - role matches (case-insensitive)
      - signer_member_id is not null
    """
    if df is None or df.empty:
        return list(required_roles)

    roles = df.get("role")
    if roles is None:
        return list(required_roles)

    role_series = roles.astype(str).str.lower().str.strip()
    signer_ok = pd.to_numeric(df.get("signer_member_id"), errors="coerce").notna()

    present = set(role_series[signer_ok].tolist())
    return [r for r in required_roles if str(r).lower().strip() not in present]


def insert_signature(
    sb,
    schema: str,
    *,
    entity_type: str,
    entity_id: int,
    role: str,
    signer_member_id: int,
    signer_name: str,
) -> bool:
    """
    Upsert a signature row by (entity_type, entity_id, role) if possible.
    If upsert isn't supported or no unique constraint exists, fallback:
      delete existing same key then insert.
    """
    et = str(entity_type).strip()
    eid = int(entity_id)
    r = str(role).strip().lower()
    smid = int(signer_member_id)
    sname = str(signer_name or "").strip()

    if not et:
        raise ValueError("entity_type is required.")
    if eid <= 0:
        raise ValueError("entity_id must be > 0.")
    if not r:
        raise ValueError("role is required.")
    if smid <= 0:
        raise ValueError("signer_member_id must be > 0.")

    payload = {
        "entity_type": et,
        "entity_id": eid,
        "role": r,
        "signer_member_id": smid,
        "signer_name": (sname or f"Member {smid}"),
        "signed_at": now_iso(),
    }
    payload = filter_payload_to_existing_columns(sb, schema, SIGNATURES_TABLE, payload)

    # Try native upsert first (requires unique constraint on entity_type,entity_id,role)
    try:
        sb.schema(schema).table(SIGNATURES_TABLE).upsert(
            payload,
            on_conflict="entity_type,entity_id,role"
        ).execute()
        return True
    except Exception:
        pass

    # Fallback: delete then insert
    try:
        sb.schema(schema).table(SIGNATURES_TABLE).delete() \
            .eq("entity_type", et).eq("entity_id", eid).eq("role", r).execute()
    except Exception:
        # if delete fails, still try insert (might be first time)
        pass

    sb.schema(schema).table(SIGNATURES_TABLE).insert(payload).execute()
    return True


# ============================================================
# MEMBER NAME LOOKUP (FIXES member_name NOT NULL)
# ============================================================
def _pick_first_key(row: dict, keys: List[str]) -> Optional[str]:
    for k in keys:
        v = row.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _lookup_member_name(sb, schema: str, member_id: int) -> Optional[str]:
    """Try to resolve a member name from likely tables/columns."""
    mid = int(member_id)
    if mid <= 0:
        return None

    name_cols_priority = ["member_name", "full_name", "name", "display_name", "first_name", "last_name"]

    for table in MEMBER_NAME_TABLES:
        cols = _get_table_columns(sb, schema, table)
        if not cols:
            continue

        id_col = "member_id" if "member_id" in cols else ("id" if "id" in cols else None)
        if not id_col:
            continue

        select_cols = [c for c in name_cols_priority if c in cols]
        if not select_cols:
            continue

        try:
            row = fetch_one(
                sb.schema(schema).table(table)
                .select(",".join([id_col] + select_cols))
                .eq(id_col, mid)
            )
            if not row:
                continue

            if "first_name" in row and "last_name" in row:
                fn = str(row.get("first_name") or "").strip()
                ln = str(row.get("last_name") or "").strip()
                full = (fn + " " + ln).strip()
                if full:
                    return full

            nm = _pick_first_key(row, select_cols)
            if nm:
                return nm
        except Exception:
            continue

    return None


def _ensure_member_name(sb, schema: str, member_id: int, member_name: Optional[str]) -> str:
    """Guarantees a non-empty string (loan_requests.member_name is NOT NULL)."""
    if member_name:
        s = str(member_name).strip()
        if s:
            return s

    looked = _lookup_member_name(sb, schema, member_id)
    if looked:
        return looked

    return f"Member {int(member_id)}"


# ============================================================
# REQUEST NORMALIZATION (keeps old UI code alive)
# ============================================================
def _normalize_request_row(r: dict) -> dict:
    out = dict(r)
    out.setdefault("requester_member_id", out.get("member_id"))
    out.setdefault("requester_name", out.get("member_name"))
    out.setdefault("amount", out.get("requested_amount"))
    out.setdefault("created_at", out.get("requested_at"))
    rid = out.get("id")
    out.setdefault("requester_user_id", f"req-{rid}" if rid is not None else str(uuid.uuid4()))
    out.setdefault("surety_name", None)
    return out


# ============================================================
# REQUESTS (✅ NEW TABLE SHAPE) — ACCEPTS borrower_id + requester_user_id
# ============================================================
def create_loan_request(
    sb,
    schema: str,
    *,
    borrower_id: Optional[int] = None,
    surety_id: Optional[int] = None,
    amount: Optional[float] = None,

    requester_user_id: Optional[str] = None,
    requester_member_id: Optional[int] = None,
    requester_name: Optional[str] = None,

    member_id: Optional[int] = None,
    member_name: Optional[str] = None,
    surety_member_id: Optional[int] = None,
    requested_amount: Optional[float] = None,

    purpose: str | None = None,
    duration_months: int | None = None,
    interest_rate: float | None = None,
    notes: str | None = None,
) -> int:
    """
    Inserts a row into loan_requests (NEW schema).
    Ensures member_name is never NULL.
    """

    b_id = borrower_id
    if b_id is None:
        b_id = requester_member_id if requester_member_id is not None else member_id

    s_id = surety_id
    if s_id is None:
        s_id = surety_member_id

    amt = amount if amount is not None else requested_amount

    if b_id is None or int(b_id) <= 0:
        raise ValueError("Invalid borrower/member id.")
    if s_id is None or int(s_id) <= 0:
        raise ValueError("Invalid surety id.")
    if amt is None or float(amt) <= 0:
        raise ValueError("Amount must be > 0.")

    resolved_name = _ensure_member_name(sb, schema, int(b_id), member_name or requester_name)

    extra_note = ""
    if requester_user_id:
        extra_note = f"[requester_user_id={str(requester_user_id).strip()}]"

    merged_notes = (str(notes).strip() if notes else "")
    if extra_note:
        merged_notes = (extra_note + ("\n" + merged_notes if merged_notes else "")).strip()
    if not merged_notes:
        merged_notes = None

    payload = {
        "member_id": int(b_id),
        "member_name": resolved_name,  # ✅ NEVER NULL
        "requested_amount": float(amt),
        "purpose": (str(purpose).strip() if purpose else None),
        "duration_months": int(duration_months) if duration_months is not None else None,
        "interest_rate": float(interest_rate) if interest_rate is not None else MONTHLY_INTEREST_RATE,
        "status": "pending",
        "requested_at": now_iso(),
        "notes": merged_notes,
        "surety_member_id": int(s_id),
        # IMPORTANT: your table DOES have requester_user_id in your loans_ui header comment;
        # but if PostgREST cache doesn't include it (or column doesn't exist), we filter it out safely.
        "requester_user_id": (str(requester_user_id).strip() if requester_user_id else None),
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
# GOVERNANCE + APPROVAL
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


def has_active_loan(sb, schema: str, member_id: int) -> bool:
    rows = (
        sb.schema(schema)
        .table(LOANS_TABLE)
        .select("status,member_id")
        .eq("member_id", int(member_id))
        .limit(20000)
        .execute()
        .data
        or []
    )
    return any(str(r.get("status") or "").lower().strip() in ("active", "open") for r in rows)


def approve_loan_request(sb, schema: str, request_id: int, actor_name: str) -> int:
    req = get_request(sb, schema, request_id)
    if str(req.get("status") or "").lower().strip() != "pending":
        raise ValueError("Only pending requests can be approved.")

    # ✅ FIX: signatures check must use entity_type="loan_request" (matches loans_ui.py)
    df_sig = sig_df(sb, schema, REQUEST_ENTITY_TYPE, int(request_id))
    miss = missing_roles(df_sig, LOAN_SIG_REQUIRED)
    if miss:
        raise ValueError("Approval blocked. Missing/invalid signatures: " + ", ".join(miss))

    borrower_id = int(req.get("member_id") or 0)
    surety_id = int(req.get("surety_member_id") or 0)
    amount = float(req.get("requested_amount") or 0)

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
# MAKER–CHECKER (pending payments)
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
# INTEREST (✅ Ledger-based)
# ============================================================
def accrue_monthly_interest(sb, schema: str, actor_user_id: str) -> Tuple[int, float]:
    month = _month_key()
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

    return updated, float(round(interest_added_total, 2))


# ============================================================
# DPD (fallback)
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
            .select("*")
            .order("updated_at", desc=True)
            .limit(int(limit))
            .execute().data or []
        )
    except Exception:
        loans = []

    out = []
    for r in loans:
        if str(r.get("status") or "").lower().strip() not in ("open", "active"):
            continue
        loan_id = int(r.get("id") or 0)
        if loan_id <= 0:
            continue
        last_paid = _get_last_paid_on(sb, schema, loan_id)
        rr = dict(r)
        rr["last_paid_on"] = str(last_paid) if last_paid else None
        rr["dpd"] = compute_dpd(r, last_paid)
        out.append(rr)

    df = pd.DataFrame(out)
    if not df.empty and "dpd" in df.columns:
        df = df.sort_values("dpd", ascending=False)
    return df


# ============================================================
# OPTIONAL: convenience for statements (if your UI calls these)
# ============================================================
def list_member_loans(sb, schema: str, member_id: int, limit: int = 2000) -> List[Dict[str, Any]]:
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
