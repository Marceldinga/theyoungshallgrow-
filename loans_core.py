
# loans_core.py ✅ COMPLETE SINGLE-FILE UPDATED — MATCHES YOUR REAL loans COLUMNS (NO LEGACY)
# -----------------------------------------------------------------------------
# ✅ CONFIRMED loans columns (from your screenshots):
#   id, member_id, session_id, status, principal, principal_current,
#   unpaid_interest, total_interest_generated, interest_rate_monthly,
#   total_due, borrow_date, due_cycle_days, last_paid_at, created_at,
#   updated_at, surety_member_id
#
# ✅ THIS VERSION GUARANTEES:
#   - ❌ NO accrued_interest
#   - ❌ NO total_paid
#   - ✅ Payment logic uses unpaid_interest only
#   - ✅ Interest accrual writes unpaid_interest + total_interest_generated + total_due
#   - ✅ loan_requests.status uses allowed values (pending/approved/rejected/cancelled)
#   - ✅ Signatures entity_type="loan_request"
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
# Tables (✅ MUST MATCH YOUR DB)
# ------------------------------------------------------------
LOANS_TABLE = "loans"
PAYMENTS_TABLE = "loan_payments"
PENDING_PAYMENTS_TABLE = "loan_repayments_pending"
REQUESTS_TABLE = "loan_requests"
INTEREST_LEDGER_TABLE = "interest_ledger"
SIGNATURES_TABLE = "signatures"
MEMBERS_TABLE = "members"

REPAY_LINK_COL = "loan_id"
REPAY_DATE_COL = "paid_at"

# Signatures
REQUEST_ENTITY_TYPE = "loan_request"
REQ_SIG_REQUIRED = ["borrower", "surety", "treasury"]

MEMBER_NAME_TABLES = ["members", "member_registry", "members_legacy"]


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
# SAFE COLUMN FILTERING
# ============================================================
def _get_table_columns(sb, schema: str, table: str) -> set[str]:
    """Infer columns from one row; returns empty set if table is empty/unreadable."""
    try:
        rows = (
            sb.schema(schema).table(table)
            .select("*")
            .limit(1)
            .execute().data or []
        )
        if not rows:
            return set()
        return set(rows[0].keys())
    except Exception:
        return set()


def filter_payload_to_existing_columns(sb, schema: str, table: str, payload: dict) -> dict:
    cols = _get_table_columns(sb, schema, table)
    if not cols:
        return payload
    return {k: v for k, v in payload.items() if k in cols}


# ============================================================
# SIGNATURES (required by loans_ui.py)
# ============================================================
def sig_df(sb, schema: str, entity_type: str, entity_id: int) -> pd.DataFrame:
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
    if df is None or df.empty:
        return list(required_roles)
    if "role" not in df.columns:
        return list(required_roles)

    role_series = df["role"].astype(str).str.lower().str.strip()
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
    Upsert by (entity_type, entity_id, role) if possible.
    Fallback: delete existing and insert.
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

    try:
        sb.schema(schema).table(SIGNATURES_TABLE).upsert(
            payload,
            on_conflict="entity_type,entity_id,role"
        ).execute()
        return True
    except Exception:
        pass

    try:
        sb.schema(schema).table(SIGNATURES_TABLE).delete() \
            .eq("entity_type", et).eq("entity_id", eid).eq("role", r).execute()
    except Exception:
        pass

    sb.schema(schema).table(SIGNATURES_TABLE).insert(payload).execute()
    return True


# ============================================================
# MEMBER NAME LOOKUP
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
    if member_name and str(member_name).strip():
        return str(member_name).strip()
    looked = _lookup_member_name(sb, schema, member_id)
    if looked:
        return looked
    return f"Member {int(member_id)}"


# ============================================================
# REQUEST NORMALIZATION
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
# REQUESTS
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
    b_id = borrower_id if borrower_id is not None else (requester_member_id if requester_member_id is not None else member_id)
    s_id = surety_id if surety_id is not None else surety_member_id
    amt = amount if amount is not None else requested_amount

    if b_id is None or int(b_id) <= 0:
        raise ValueError("Invalid borrower/member id.")
    if s_id is None or int(s_id) <= 0:
        raise ValueError("Invalid surety id.")
    if amt is None or float(amt) <= 0:
        raise ValueError("Amount must be > 0.")

    resolved_name = _ensure_member_name(sb, schema, int(b_id), member_name or requester_name)

    merged_notes = (str(notes).strip() if notes else "")
    if requester_user_id:
        tag = f"[requester_user_id={str(requester_user_id).strip()}]"
        merged_notes = (tag + ("\n" + merged_notes if merged_notes else "")).strip()
    if not merged_notes:
        merged_notes = None

    payload = {
        "member_id": int(b_id),
        "member_name": resolved_name,
        "requested_amount": float(amt),
        "purpose": (str(purpose).strip() if purpose else None),
        "duration_months": int(duration_months) if duration_months is not None else None,
        "interest_rate": float(interest_rate) if interest_rate is not None else MONTHLY_INTEREST_RATE,
        "status": "pending",
        "requested_at": now_iso(),
        "notes": merged_notes,
        "surety_member_id": int(s_id),
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
            sb.schema(schema).table(REQUESTS_TABLE)
            .select("*")
            .eq("status", "pending")
            .order("requested_at", desc=True)
            .limit(int(limit))
            .execute().data or []
        )
    except Exception:
        rows = []
    return [_normalize_request_row(r) for r in (rows or [])]


def get_request(sb, schema: str, request_id: int) -> dict:
    rows = (
        sb.schema(schema).table(REQUESTS_TABLE)
        .select("*")
        .eq("id", int(request_id))
        .limit(1)
        .execute().data or []
    )
    if not rows:
        raise RuntimeError("Request not found.")
    return _normalize_request_row(rows[0])


# ============================================================
# GOVERNANCE + APPROVAL (uses real view columns)
# ============================================================
def _get_totals_row(sb, schema: str, member_id: int) -> dict:
    rows = (
        sb.schema(schema)
        .table("member_contribution_totals")
        .select("member_id,contrib_total,foundation_total,total_contributed")
        .eq("member_id", int(member_id))
        .limit(1)
        .execute().data or []
    )
    if rows:
        r = rows[0]
        r.setdefault("member_id", int(member_id))
        r.setdefault("contrib_total", 0)
        r.setdefault("foundation_total", 0)
        if "total_contributed" not in r or r.get("total_contributed") is None:
            r["total_contributed"] = float(r.get("contrib_total") or 0) + float(r.get("foundation_total") or 0)
        return r

    return {"member_id": int(member_id), "contrib_total": 0, "foundation_total": 0, "total_contributed": 0}


def _capacity_from_row(r: dict) -> float:
    contrib = float(r.get("contrib_total") or 0)
    foundation = float(r.get("foundation_total") or 0)
    return contrib + CAP_MULT * foundation


def check_loan_qualification(sb, schema: str, borrower_id: int, surety_id: int, amount: float) -> dict:
    borrower = _get_totals_row(sb, schema, borrower_id)
    cap_b = _capacity_from_row(borrower)

    self_surety = int(borrower_id) == int(surety_id)
    if self_surety:
        cap_total = cap_b
        cap_s = None
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
        "rule": "cap = contrib_total + 0.70*foundation_total; cap_total = cap_b + cap_s (self-surety counts once)",
    }


def has_active_loan(sb, schema: str, member_id: int) -> bool:
    rows = (
        sb.schema(schema).table(LOANS_TABLE)
        .select("status,member_id")
        .eq("member_id", int(member_id))
        .limit(20000)
        .execute().data or []
    )
    return any(str(r.get("status") or "").lower().strip() in ("active", "open") for r in rows)


def approve_loan_request(sb, schema: str, request_id: int, actor_name: str) -> int:
    req = get_request(sb, schema, request_id)
    if str(req.get("status") or "").lower().strip() != "pending":
        raise ValueError("Only pending requests can be approved.")

    df_sig = sig_df(sb, schema, REQUEST_ENTITY_TYPE, int(request_id))
    miss = missing_roles(df_sig, REQ_SIG_REQUIRED)
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
            f"Loan rejected: principal {cap['amount']} exceeds capacity {cap['cap_total']:.2f} "
            f"(self_surety={cap['self_surety']}, rule={cap['rule']})"
        )

    ts = now_iso()
    loan_payload = {
        "member_id": borrower_id,
        "session_id": None,
        "surety_member_id": surety_id,
        "borrow_date": str(date.today()),
        "due_cycle_days": 28,
        "principal": float(amount),
        "principal_current": float(amount),
        "unpaid_interest": 0.0,
        "total_interest_generated": 0.0,
        "interest_rate_monthly": float(req.get("interest_rate") or MONTHLY_INTEREST_RATE),
        "total_due": float(amount),
        "status": "open",
        "last_paid_at": None,
        "created_at": ts,
        "updated_at": ts,
    }
    loan_payload = {k: v for k, v in loan_payload.items() if v is not None}
    loan_payload = filter_payload_to_existing_columns(sb, schema, LOANS_TABLE, loan_payload)

    loan_res = sb.schema(schema).table(LOANS_TABLE).insert(loan_payload).execute()
    loan_row = (loan_res.data or [None])[0]
    if not loan_row or "id" not in loan_row:
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
        "status": "rejected",  # ✅ matches DB CHECK
        "reviewed_by": str(actor_name or "").strip() or "admin",
        "reviewed_at": ts,
        "notes": (str(reason or "").strip() or "rejected"),
    }
    upd = filter_payload_to_existing_columns(sb, schema, REQUESTS_TABLE, upd)
    sb.schema(schema).table(REQUESTS_TABLE).update(upd).eq("id", int(request_id)).execute()


# ============================================================
# PAYMENTS — uses unpaid_interest ONLY
# ============================================================
def _apply_payment_to_loan_balances(sb, schema: str, loan: dict, loan_id: int, amount: float, paid_at: str):
    pay_amt = float(amount)

    unpaid_interest = float(loan.get("unpaid_interest") or 0.0)
    principal_current = float(loan.get("principal_current") or loan.get("principal") or 0.0)

    # Pay interest first
    unpaid_interest_new = unpaid_interest
    if unpaid_interest_new > 0:
        if pay_amt >= unpaid_interest_new:
            pay_amt -= unpaid_interest_new
            unpaid_interest_new = 0.0
        else:
            unpaid_interest_new = unpaid_interest_new - pay_amt
            pay_amt = 0.0

    # Remaining pays principal
    principal_new = max(principal_current - pay_amt, 0.0)

    total_due_new = principal_new + unpaid_interest_new
    close_now = (principal_new <= 0.0) and (unpaid_interest_new <= 0.0)

    update_payload = {
        "principal_current": float(principal_new),
        "unpaid_interest": float(unpaid_interest_new),
        "total_due": float(total_due_new),
        "updated_at": now_iso(),
        "last_paid_at": str(paid_at),
        "status": "closed" if close_now else None,
    }
    update_payload = {k: v for k, v in update_payload.items() if v is not None}
    update_payload = filter_payload_to_existing_columns(sb, schema, LOANS_TABLE, update_payload)

    sb.schema(schema).table(LOANS_TABLE).update(update_payload).eq("id", int(loan_id)).execute()


def record_payment(
    sb,
    schema: str,
    *,
    loan_id: int,
    amount: float,
    paid_at: str,
    note: str | None = None,
) -> bool:
    if float(amount) <= 0:
        raise ValueError("Amount must be > 0.")
    if int(loan_id) <= 0:
        raise ValueError("Invalid loan_id.")

    loan = fetch_one(
        sb.schema(schema).table(LOANS_TABLE)
        .select("id,member_id,status,principal,principal_current,unpaid_interest,total_due")
        .eq("id", int(loan_id))
    )
    if not loan:
        raise RuntimeError("Loan not found.")

    if str(loan.get("status") or "").lower().strip() in ("closed", "paid"):
        raise ValueError("Loan is already closed.")

    member_id = int(loan.get("member_id") or 0)

    pay_payload = {
        "loan_id": int(loan_id),
        "member_id": (member_id if member_id > 0 else None),
        "amount": float(amount),
        "paid_at": str(paid_at),
        "note": (str(note).strip() if note else None),
        "created_at": now_iso(),
    }
    pay_payload = {k: v for k, v in pay_payload.items() if v is not None}
    pay_payload = filter_payload_to_existing_columns(sb, schema, PAYMENTS_TABLE, pay_payload)

    sb.schema(schema).table(PAYMENTS_TABLE).insert(pay_payload).execute()
    _apply_payment_to_loan_balances(sb, schema, loan, int(loan_id), float(amount), str(paid_at))
    return True


# ============================================================
# MAKER–CHECKER (pending payments)
# ============================================================
def list_unconfirmed_payments(sb, schema: str, limit: int = 500) -> List[Dict[str, Any]]:
    try:
        return (
            sb.schema(schema).table(PENDING_PAYMENTS_TABLE)
            .select("*")
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(int(limit))
            .execute().data or []
        )
    except Exception:
        return []


def confirm_payment(sb, schema: str, pending_id: int, confirmer_user_id: str) -> bool:
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

    # insert into confirmed payments
    repay_payload = {
        "loan_id": int(loan_id),
        "member_id": int(pend.get("member_id") or 0) or None,
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
        .select("id,member_id,status,principal,principal_current,unpaid_interest,total_due")
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


def reject_payment(sb, schema: str, pending_id: int, rejecter_user_id: str, reason: str) -> bool:
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
# INTEREST (ledger-based) — writes to unpaid_interest + totals
# ============================================================
def accrue_monthly_interest(sb, schema: str, actor_user_id: str) -> Tuple[int, float]:
    month = _month_key()
    ts = now_iso()

    loans = (
        sb.schema(schema).table(LOANS_TABLE)
        .select("id,member_id,status,principal,principal_current,unpaid_interest,total_interest_generated,interest_rate_monthly,total_due")
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

        unpaid_interest_new = float(r.get("unpaid_interest") or 0) + float(interest)
        total_interest_generated_new = float(r.get("total_interest_generated") or 0) + float(interest)
        principal_curr = float(r.get("principal_current") or principal_current)
        total_due_new = principal_curr + unpaid_interest_new

        upd = {
            "unpaid_interest": unpaid_interest_new,
            "total_interest_generated": total_interest_generated_new,
            "total_due": total_due_new,
            "updated_at": ts,
        }
        upd = filter_payload_to_existing_columns(sb, schema, LOANS_TABLE, upd)
        sb.schema(schema).table(LOANS_TABLE).update(upd).eq("id", loan_id).execute()

        updated += 1
        interest_added_total += float(interest)

    return updated, float(round(interest_added_total, 2))


# ============================================================
# DPD / DELINQUENCY (fallback)
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
# Statements helper (used by loans_ui.py)
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
