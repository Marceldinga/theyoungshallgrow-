# loans_core.py ✅ COMPLETE UPDATED (loan_repayments + interest duplicate-key fix + repayment updates balances)
# Fixes:
# - signatures.entity_type NOT NULL for statement signing (STATEMENT_ENTITY_TYPE)
# - ✅ Uses loan_repayments table for loan-linked repayments (loan_id required)
# - ✅ Repayment updates loan balances (principal_current / unpaid_interest / total_due if columns exist)
# - Legacy repayments insert into loan_repayments_legacy:
#     * filters payload keys when possible
#     * retries by removing missing columns (fixes PGRST204 schema-cache errors)
# - ✅ Interest accrual duplicate-key fix:
#     * checks snapshot_month OR snapshot_date (today)
#     * uses upsert for snapshot row (prevents unique constraint errors)
#
# Works with loans_ui.py + rbac.py updated permissions.

from __future__ import annotations

from datetime import date, datetime, timezone
import uuid
import pandas as pd

MONTHLY_INTEREST_RATE = 0.05
LOAN_SIG_REQUIRED = ["borrower", "surety", "treasury"]

# ------------------------------------------------------------
# ✅ Loan-linked repayments table (strict)
# ------------------------------------------------------------
PAYMENTS_TABLE = "loan_repayments"
REPAY_LINK_COL = "loan_id"   # ✅
REPAY_DATE_COL = "paid_at"   # ✅

# ------------------------------------------------------------
# STATEMENT SIGNING (signatures.entity_type is NOT NULL)
# ------------------------------------------------------------
STATEMENT_SIG_ROLE = "member_statement"
STATEMENT_ENTITY_TYPE = "loan_statement"  # ✅ REQUIRED


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
# SIGNATURES (table: public.signatures)
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
    payload = {
        "entity_type": str(entity_type),
        "entity_id": int(entity_id),
        "role": str(role).strip().lower(),
        "signer_name": str(signer_name).strip(),
        "signer_member_id": int(signer_member_id) if signer_member_id is not None else None,
        "signed_at": now_iso(),
    }
    sb.schema(schema).table("signatures").upsert(payload).execute()
    return True


# ============================================================
# ✅ DIGITAL STATEMENT SIGNING (Loan Statement)
# ============================================================
def insert_statement_signature(
    sb,
    schema: str,
    loan_id: int,
    signer_member_id: int,
    signer_name: str,
):
    if int(loan_id) <= 0:
        raise ValueError("Invalid loan_id.")
    if int(signer_member_id) <= 0:
        raise ValueError("Invalid signer_member_id.")
    if not str(signer_name).strip():
        raise ValueError("Signer name is required.")

    payload = {
        "entity_type": STATEMENT_ENTITY_TYPE,
        "entity_id": int(loan_id),
        "role": STATEMENT_SIG_ROLE,
        "signer_name": str(signer_name).strip(),
        "signer_member_id": int(signer_member_id),
        "signed_at": now_iso(),
    }

    sb.schema(schema).table("signatures").upsert(payload).execute()
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
    if borrower_id <= 0 or surety_id <= 0:
        raise ValueError("Invalid borrower/surety.")
    if borrower_id == surety_id:
        raise ValueError("Borrower and surety must be different.")
    if amount <= 0:
        raise ValueError("Amount must be > 0.")

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

    limit_amt = member_loan_limit(sb, schema, borrower_id)
    if limit_amt > 0 and amount > limit_amt:
        raise ValueError(f"Approval blocked: requested amount exceeds limit ({limit_amt:,.0f}).")

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

    loan_res = sb.schema(schema).table("loans_legacy").insert(loan_payload).execute()
    loan_row = (loan_res.data or [None])[0]
    if not loan_row:
        raise RuntimeError("Loan creation failed.")
    loan_id = int(loan_row["id"])

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
# LOAN REPAYMENTS (loan_repayments) ✅ + update balances
# ============================================================
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
    Inserts into loan_repayments (loan-linked payments) and updates loans_legacy balances.
    - Writes note -> loan_repayments.note
    - Updates loans_legacy principal_current/unpaid_interest/total_due if those columns exist
    """
    if amount <= 0:
        raise ValueError("Amount must be > 0.")
    if int(loan_id) <= 0:
        raise ValueError("Invalid loan_id.")

    loan = fetch_one(
        sb.schema(schema).table("loans_legacy")
        .select("id,member_id,principal,principal_current,unpaid_interest,accrued_interest,total_due")
        .eq("id", int(loan_id))
    )
    if not loan:
        raise RuntimeError("Loan not found for repayment.")

    member_id = int(loan.get("member_id") or 0)
    if member_id <= 0:
        raise RuntimeError("Loan has invalid member_id; loan_repayments.member_id is NOT NULL.")

    # 1) Insert loan repayment (note column)
    repay_payload = {
        "loan_id": int(loan_id),
        "member_id": int(member_id),
        "amount": float(amount),
        "paid_at": str(paid_at),
        "note": (str(notes or "").strip() or None),
        "created_at": now_iso(),
    }
    repay_payload = filter_payload_to_existing_columns(sb, schema, PAYMENTS_TABLE, repay_payload)

    sb.schema(schema).table(PAYMENTS_TABLE).insert(repay_payload).execute()

    # 2) Apply payment to balances (interest first, then principal)
    pay_amt = float(amount)

    unpaid_interest = float(loan.get("unpaid_interest") or 0.0)
    accrued_interest = float(loan.get("accrued_interest") or 0.0)

    principal_current = loan.get("principal_current")
    if principal_current is None:
        principal_current = loan.get("principal")
    principal_current = float(principal_current or 0.0)

    # pay unpaid interest first
    unpaid_interest_new = unpaid_interest
    if unpaid_interest_new > 0:
        if pay_amt >= unpaid_interest_new:
            pay_amt -= unpaid_interest_new
            unpaid_interest_new = 0.0
        else:
            unpaid_interest_new = unpaid_interest_new - pay_amt
            pay_amt = 0.0

    # then reduce principal
    principal_new = max(principal_current - pay_amt, 0.0)

    # total_due = principal + interest (prefer unpaid_interest if table uses it)
    total_due_new = principal_new + (unpaid_interest_new if ("unpaid_interest" in loan) else accrued_interest)

    update_payload = {
        "principal_current": float(principal_new),
        "unpaid_interest": float(unpaid_interest_new),
        "total_due": float(total_due_new),
        "updated_at": now_iso(),
        "last_paid_at": str(paid_at),
    }
    update_payload = filter_payload_to_existing_columns(sb, schema, "loans_legacy", update_payload)

    try:
        if update_payload:
            sb.schema(schema).table("loans_legacy").update(update_payload).eq("id", int(loan_id)).execute()
    except Exception:
        # repayment is recorded; don't crash the UI
        pass

    return True


def confirm_payment(sb, schema: str, payment_id: int, confirmer: str):
    raise RuntimeError("confirm_payment not supported: loan_repayments is direct insert (no status columns).")


def reject_payment(sb, schema: str, payment_id: int, rejecter: str, reason: str):
    raise RuntimeError("reject_payment not supported: loan_repayments is direct insert (no status columns).")


# ============================================================
# ✅ LEGACY REPAYMENTS INSERT (loan_repayments_legacy)
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

    table = "loan_repayments_legacy"

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

    payload = filter_payload_to_existing_columns(sb, schema, table, payload)

    for _ in range(6):
        try:
            res = sb.schema(schema).table(table).insert(payload).execute()
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

    # Already ran this month OR already has a snapshot today -> no-op
    existing = (
        sb.schema(schema).table("loan_interest_snapshots")
        .select("id,snapshot_month,snapshot_date")
        .or_(f"snapshot_month.eq.{month},snapshot_date.eq.{today_str}")
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
    ts = now_iso()

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

        sb.schema(schema).table("loans_legacy").update({
            "accrued_interest": accrued_interest,
            "total_interest_generated": total_interest_generated,
            "unpaid_interest": unpaid_interest,
            "last_interest_at": ts,
            "updated_at": ts,
        }).eq("id", loan_id).execute()

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

    for _ in range(6):
        try:
            sb.schema(schema).table("loan_interest_snapshots").upsert(snapshot_payload).execute()
            break
        except Exception as e:
            new_payload, changed = _drop_missing_column_from_postgrest_error(snapshot_payload, e)
            if changed:
                snapshot_payload = new_payload
                continue
            raise

    return updated, interest_added_total


# ============================================================
# DELINQUENCY (fallback; SQL view is preferred)
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
