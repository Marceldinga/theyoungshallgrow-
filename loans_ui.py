
# loans_ui.py ✅ COMPLETE SINGLE FILE — FINAL FIX (NO DOUBLE APPLY)
# ✅ Fixes legacy repayment save:
#   - ALWAYS writes member_id
#   - NEVER double-applies payment to principal_current
#   - Writes repayment ONLY into loan_repayments_legacy (legacy vault)
#   - Applies repayment ONCE to loans_legacy (interest-first, then principal)
#
# ✅ Fixes "interest_due generated should add to total amount borrow and update interest too":
#   - Syncs loans_legacy.total_due = principal_current (or principal) + unpaid_interest
#   - Optionally mirrors loans_legacy.interest = unpaid_interest (if column exists)
#
# ✅ Important:
# - Your DB stores interest on loans_legacy: unpaid_interest, total_interest_generated, etc.
# - Your dashboard computes interest_paid = total_interest_generated - unpaid_interest (active loans).
# - This file ensures the loan row is updated when a legacy repayment is saved, so principal_current moves correctly.

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4, UUID
import inspect

import streamlit as st
import pandas as pd
from postgrest.exceptions import APIError

from rbac import Actor, require, allowed_sections, ROLE_ADMIN, ROLE_TREASURY, ROLE_MEMBER
import loans_core as core

# Optional PDFs
try:
    from pdfs import make_member_loan_statement_pdf, make_loan_statements_zip
except Exception:
    make_member_loan_statement_pdf = None
    make_loan_statements_zip = None

# Optional audit
try:
    from audit import audit
except Exception:
    def audit(*args, **kwargs):
        return None


# ============================================================
# Tables / Columns
# ============================================================

# Auto table detection for "standard" confirmed repayments
PAYMENTS_TABLE_PRIMARY = "loan_repayments"
PAYMENTS_TABLE_FALLBACK = "loan_repayments_legacy"

REPAY_LINK_COL = "loan_id"
REPAY_DATE_COL = "paid_at"

# Maker–checker pending table (if you use it)
PAYMENTS_PENDING_TABLE = "loan_repayments_pending"

# Legacy repayments MUST go here to avoid double-apply across tables
LEGACY_REPAYMENTS_TABLE = "loan_repayments_legacy"

# Optional interest_ledger (UI only)
INTEREST_LEDGER_TABLE = "interest_ledger"   # public.interest_ledger
INTEREST_MONTH_FMT = "%Y-%m"


# ============================================================
# Helpers
# ============================================================
def _is_uuid(s: str) -> bool:
    try:
        UUID(str(s))
        return True
    except Exception:
        return False


def _get_or_make_session_uuid(key: str = "actor_user_uuid") -> str:
    v = str(st.session_state.get(key) or "").strip()
    if not v or not _is_uuid(v):
        st.session_state[key] = str(uuid4())
    return str(st.session_state[key])


def _actor_from_session(default_user_id: str) -> Actor:
    with st.sidebar.expander("🔐 Role (temporary)", expanded=False):
        role = st.selectbox("Role", [ROLE_ADMIN, ROLE_TREASURY, ROLE_MEMBER], index=0, key="actor_role")
        member_id = st.number_input(
            "Member ID (if member/treasury)",
            min_value=0, step=1, value=int(st.session_state.get("actor_member_id") or 0),
            key="actor_member_id",
        )
        name = st.text_input(
            "Name",
            value=str(st.session_state.get("actor_name") or ("admin" if role != ROLE_MEMBER else "member")),
            key="actor_name",
        )

    user_uuid = default_user_id if (default_user_id and _is_uuid(default_user_id)) else _get_or_make_session_uuid()

    return Actor(
        user_id=user_uuid,
        role=role,
        member_id=(int(member_id) if int(member_id) > 0 else None),
        name=(name.strip() or None),
    )


def _to_iso(d: date) -> str:
    # store as ISO datetime string; Supabase accepts it for timestamptz
    return datetime.combine(d, datetime.min.time()).isoformat()


def _safe_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows or [])


def _apierror_message(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or "APIError")
        return str(e)
    return str(e)


def _num(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def _month_key(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def _table_exists(sb_service, schema: str, table_name: str) -> bool:
    try:
        sb_service.schema(schema).table(table_name).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def _pick_payments_table(sb_service, schema: str) -> str:
    if _table_exists(sb_service, schema, PAYMENTS_TABLE_PRIMARY):
        return PAYMENTS_TABLE_PRIMARY
    return PAYMENTS_TABLE_FALLBACK


def _columns_exist(sb_service, schema: str, table_name: str, cols: list[str]) -> dict[str, bool]:
    out = {c: False for c in cols}
    for c in cols:
        try:
            sb_service.schema(schema).table(table_name).select(c).limit(1).execute()
            out[c] = True
        except Exception:
            out[c] = False
    return out


def _read_app_state(sb_service, schema: str) -> dict:
    try:
        rows = sb_service.schema(schema).table("app_state").select("*").limit(1).execute().data or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def _get_current_session_id(sb_service, schema: str):
    r = _read_app_state(sb_service, schema)
    return r.get("current_session_id") or None


# ============================================================
# ✅ Sync: total_due must include interest due
# ============================================================
def _sync_total_due_with_interest(sb_service, schema: str, only_active: bool = True) -> dict:
    """
    Ensures loans_legacy.total_due reflects principal_current (or principal) + unpaid_interest.
    Mirrors loans_legacy.interest = unpaid_interest if 'interest' column exists.
    """
    out = {"checked": 0, "updated": 0, "skipped": 0, "error": None}

    col_ok = _columns_exist(
        sb_service,
        schema,
        "loans_legacy",
        ["id", "status", "principal_current", "principal", "unpaid_interest", "total_due", "interest"],
    )

    needed = ["id"]
    if col_ok.get("status"): needed.append("status")
    if col_ok.get("principal_current"): needed.append("principal_current")
    if col_ok.get("principal"): needed.append("principal")
    if col_ok.get("unpaid_interest"): needed.append("unpaid_interest")
    if col_ok.get("total_due"): needed.append("total_due")
    if col_ok.get("interest"): needed.append("interest")

    try:
        rows = (
            sb_service.schema(schema).table("loans_legacy")
            .select(",".join(needed))
            .order("id", desc=False)
            .limit(20000)
            .execute().data
            or []
        )
    except Exception as e:
        out["error"] = _apierror_message(e)
        return out

    for r in rows:
        out["checked"] += 1
        status = str(r.get("status") or "").lower().strip()
        if only_active and status not in ("active", "open"):
            out["skipped"] += 1
            continue

        principal = _num(r.get("principal_current") or r.get("principal"))
        unpaid_interest = _num(r.get("unpaid_interest"))
        desired_total_due = principal + unpaid_interest

        current_total_due = _num(r.get("total_due"))
        update_payload = {}

        if col_ok.get("total_due") and abs(desired_total_due - current_total_due) > 1e-6:
            update_payload["total_due"] = float(desired_total_due)

        if col_ok.get("interest"):
            cur_interest = _num(r.get("interest"))
            if abs(cur_interest - unpaid_interest) > 1e-6:
                update_payload["interest"] = float(unpaid_interest)

        if not update_payload:
            continue

        try:
            sb_service.schema(schema).table("loans_legacy").update(update_payload).eq("id", int(r["id"])).execute()
            out["updated"] += 1
        except Exception:
            out["skipped"] += 1

    return out


# ============================================================
# ✅ Apply repayment to loan ONCE (Interest-first)
# ============================================================
def _apply_repayment_once(sb_service, schema: str, loan_id: int, pay_amount: float) -> dict:
    """
    Applies payment to loans_legacy exactly once:
      1) pays unpaid_interest first
      2) then reduces principal_current
      3) sets total_due = principal_current + unpaid_interest
      4) mirrors interest column if present
    """
    cols = _columns_exist(
        sb_service, schema, "loans_legacy",
        ["id", "principal_current", "principal", "unpaid_interest", "total_due", "interest"]
    )

    select_cols = ["id"]
    if cols.get("principal_current"): select_cols.append("principal_current")
    if cols.get("principal"): select_cols.append("principal")
    if cols.get("unpaid_interest"): select_cols.append("unpaid_interest")
    if cols.get("total_due"): select_cols.append("total_due")
    if cols.get("interest"): select_cols.append("interest")

    row = (
        sb_service.schema(schema).table("loans_legacy")
        .select(",".join(select_cols))
        .eq("id", int(loan_id)).limit(1).execute().data
        or []
    )
    if not row:
        return {"ok": False, "error": "Loan not found"}

    r = row[0]
    principal = _num(r.get("principal_current") or r.get("principal"))
    unpaid_interest = _num(r.get("unpaid_interest"))

    amt = max(float(pay_amount), 0.0)

    # Interest first
    interest_paid = min(unpaid_interest, amt)
    unpaid_interest_new = unpaid_interest - interest_paid
    amt_left = amt - interest_paid

    # Principal next
    principal_paid = min(principal, amt_left)
    principal_new = principal - principal_paid

    total_due_new = principal_new + unpaid_interest_new

    update_payload = {}
    if cols.get("principal_current"):
        update_payload["principal_current"] = float(principal_new)
    else:
        # If principal_current doesn't exist, we cannot persist principal reduction safely
        return {"ok": False, "error": "principal_current column missing on loans_legacy"}

    if cols.get("unpaid_interest"):
        update_payload["unpaid_interest"] = float(unpaid_interest_new)
    if cols.get("total_due"):
        update_payload["total_due"] = float(total_due_new)
    if cols.get("interest"):
        update_payload["interest"] = float(unpaid_interest_new)

    sb_service.schema(schema).table("loans_legacy").update(update_payload).eq("id", int(loan_id)).execute()

    return {
        "ok": True,
        "interest_paid": float(interest_paid),
        "principal_paid": float(principal_paid),
        "principal_new": float(principal_new),
        "unpaid_interest_new": float(unpaid_interest_new),
        "total_due_new": float(total_due_new),
    }


# ============================================================
# Interest Ledger totals (optional UI)
# ============================================================
def _interest_ledger_totals(sb_service, schema: str) -> dict:
    out = {"all_time": 0.0, "this_month": 0.0, "last_row": None, "ok": False, "error": None}

    if not _table_exists(sb_service, schema, INTEREST_LEDGER_TABLE):
        out["error"] = f"Table {schema}.{INTEREST_LEDGER_TABLE} not found/readable."
        return out

    try:
        col_ok = _columns_exist(
            sb_service, schema, INTEREST_LEDGER_TABLE,
            ["amount", "interest_month", "created_at", "loan_id", "member_id", "note"]
        )
        select_cols = ["amount", "interest_month", "created_at"]
        if col_ok.get("loan_id"): select_cols.append("loan_id")
        if col_ok.get("member_id"): select_cols.append("member_id")
        if col_ok.get("note"): select_cols.append("note")

        rows = (
            sb_service.schema(schema).table(INTEREST_LEDGER_TABLE)
            .select(",".join(select_cols))
            .order("created_at", desc=True)
            .limit(20000)
            .execute().data
            or []
        )

        df = pd.DataFrame(rows)
        if df.empty:
            out["ok"] = True
            return out

        df["amount"] = pd.to_numeric(df.get("amount"), errors="coerce").fillna(0.0)
        df["interest_month"] = df.get("interest_month").astype(str)

        mk = _month_key()
        out["all_time"] = float(df["amount"].sum())
        out["this_month"] = float(df[df["interest_month"] == mk]["amount"].sum())
        out["last_row"] = rows[0] if rows else None
        out["ok"] = True
        return out

    except Exception as e:
        out["error"] = _apierror_message(e)
        return out


def _build_statement_pdf(member: dict, mloans: list[dict], mpay: list[dict], statement_sig: dict | None) -> bytes:
    if make_member_loan_statement_pdf is None:
        raise RuntimeError("PDF engine not available (make_member_loan_statement_pdf import failed).")

    sig = inspect.signature(make_member_loan_statement_pdf)
    kwargs = dict(
        brand="theyoungshallgrow",
        member=member,
        cycle_info={},
        loans=mloans,
        payments=mpay,
        currency="$",
        logo_path=None,
    )
    if "statement_signature" in sig.parameters:
        kwargs["statement_signature"] = statement_sig
    return make_member_loan_statement_pdf(**kwargs)


# ============================================================
# Repayments read helpers
# ============================================================
def get_repayments_for_loan_ids(sb_service, schema: str, loan_ids: list[int], limit: int = 5000) -> list[dict]:
    if not loan_ids:
        return []
    payments_table = _pick_payments_table(sb_service, schema)
    return (
        sb_service.schema(schema).table(payments_table)
        .select("*")
        .in_(REPAY_LINK_COL, [int(x) for x in loan_ids])
        .order(REPAY_DATE_COL, desc=True)
        .limit(int(limit))
        .execute().data
        or []
    )


# ============================================================
# Requests UI
# ============================================================
def _render_requests(sb_service, schema: str, actor: Actor):
    require(actor.role, "submit_request")
    st.subheader("Requests")

    members = (
        sb_service.schema(schema).table("members_legacy")
        .select("id,name")
        .order("id", desc=False)
        .limit(5000)
        .execute().data
        or []
    )
    dfm = _safe_df(members)
    if dfm.empty:
        st.warning("members_legacy is empty or not readable.")
        return

    dfm["id"] = pd.to_numeric(dfm["id"], errors="coerce").fillna(0).astype(int)
    dfm["name"] = dfm["name"].astype(str)
    dfm["label"] = dfm.apply(lambda r: f'{int(r["id"]):02d} • {r["name"]}', axis=1)
    labels = dfm["label"].tolist()
    label_to_id = dict(zip(dfm["label"], dfm["id"]))
    label_to_name = dict(zip(dfm["label"], dfm["name"]))

    st.markdown("### Create a loan request")
    with st.form("loan_request_create", clear_on_submit=True):
        borrower_pick = st.selectbox("Borrower", labels, key="req_borrower")
        surety_pick = st.selectbox("Surety", labels, key="req_surety")
        amount = st.number_input("Amount", min_value=0.0, step=50.0, value=0.0, key="req_amount")
        ok = st.form_submit_button("Submit request", use_container_width=True)

    if ok:
        borrower_id = int(label_to_id[borrower_pick])
        surety_id = int(label_to_id[surety_pick])

        if borrower_id == surety_id:
            st.error("Borrower and surety must be different.")
        elif float(amount) <= 0:
            st.error("Amount must be > 0.")
        else:
            try:
                req_id = core.create_loan_request(
                    sb_service, schema,
                    borrower_id=borrower_id,
                    borrower_name=str(label_to_name[borrower_pick]),
                    surety_id=surety_id,
                    surety_name=str(label_to_name[surety_pick]),
                    amount=float(amount),
                    requester_user_id=str(actor.user_id),
                )
                audit(sb_service, "loan_request_created", "ok", {"request_id": req_id}, actor_user_id=actor.user_id)
                st.success(f"Request submitted. ID = {req_id}")
            except Exception as e:
                st.error("Failed to create request.")
                st.code(_apierror_message(e), language="text")

    st.divider()
    st.markdown("### Pending requests")

    pending = core.list_pending_requests(sb_service, schema, limit=300)
    dfp = _safe_df(pending)
    if dfp.empty:
        st.info("No pending requests.")
        return

    st.dataframe(dfp, use_container_width=True, hide_index=True)


# ============================================================
# Ledger UI
# ============================================================
def _render_ledger(sb_service, schema: str, actor: Actor):
    require(actor.role, "view_ledger")
    st.subheader("Ledger (loans_legacy)")

    rows = (
        sb_service.schema(schema).table("loans_legacy")
        .select("*")
        .order("id", desc=True)
        .limit(2000)
        .execute().data
        or []
    )
    df = _safe_df(rows)
    if df.empty:
        st.info("No loans found.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


# ============================================================
# Record payment UI (Maker -> pending)
# ============================================================
def _render_record_payment(sb_service, schema: str, actor: Actor):
    require(actor.role, "record_payment")
    payments_table = _pick_payments_table(sb_service, schema)

    st.subheader("Record Payment (Maker)")
    st.caption("Records a repayment as PENDING (maker–checker). Use 'Confirm Payments' to finalize.")

    loans = (
        sb_service.schema(schema).table("loans_legacy")
        .select("id,member_id,status,total_due,principal,principal_current,unpaid_interest")
        .order("id", desc=True)
        .limit(2000)
        .execute().data
        or []
    )
    df = pd.DataFrame(loans)
    if df.empty:
        st.warning("No loans found in loans_legacy. Cannot record repayment.")
        return

    def _lbl(r):
        due = _num(r.get("total_due"))
        pc = _num(r.get("principal_current") or r.get("principal"))
        ui = _num(r.get("unpaid_interest"))
        return (
            f"Loan {int(r['id'])} • Member {r.get('member_id')} • {str(r.get('status') or '')} • "
            f"Principal {pc:,.0f} • Interest {ui:,.0f} • Due {due:,.0f}"
        )

    df["label"] = df.apply(_lbl, axis=1)
    pick = st.selectbox("Select loan", df["label"].tolist(), key="pay_pick_loan")
    loan_id = int(df[df["label"] == pick].iloc[0]["id"])

    amount = st.number_input("Amount", min_value=0.0, step=50.0, value=0.0, key="pay_amt")
    paid_on = st.date_input("Paid date", value=date.today(), key="pay_date")
    note = st.text_input("Note (optional)", value="Loan repayment", key="pay_note")

    if st.button("💾 Save pending payment", use_container_width=True, key="pay_save"):
        if float(amount) <= 0:
            st.error("Amount must be > 0.")
            st.stop()
        try:
            core.record_payment_pending(
                sb_service,
                schema,
                loan_id=int(loan_id),
                amount=float(amount),
                paid_at=_to_iso(paid_on),
                recorded_by=str(actor.user_id),
                notes=note,
            )
            audit(sb_service, "loan_payment_pending_created", "ok",
                  {"loan_id": int(loan_id), "amount": float(amount)}, actor_user_id=actor.user_id)
            st.success("Saved as PENDING. Go to 'Confirm Payments' to finalize.")
            st.rerun()
        except Exception as e:
            st.error("Failed to record pending payment.")
            st.code(_apierror_message(e), language="text")

    st.divider()
    st.markdown(f"### Recent CONFIRMED repayments for this loan ({payments_table})")
    try:
        rows = (
            sb_service.schema(schema).table(payments_table)
            .select("*")
            .eq("loan_id", int(loan_id))
            .order("paid_at", desc=True)
            .limit(200)
            .execute().data
            or []
        )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning("Could not load confirmed repayments.")
        st.code(_apierror_message(e), language="text")


# ============================================================
# Confirm Payments UI (Checker)
# ============================================================
def _render_confirm_payments(sb_service, schema: str, actor: Actor):
    require(actor.role, "confirm_payments")
    st.subheader("✅ Confirm Payments (Checker)")
    st.caption("Approve/reject pending repayments (maker–checker).")

    if not _table_exists(sb_service, schema, PAYMENTS_PENDING_TABLE):
        st.warning(f"Missing table: {schema}.{PAYMENTS_PENDING_TABLE}.")
        st.info("If you don't use maker–checker, use 'Loan Repayment (Legacy)'.")
        return

    try:
        pending = (
            sb_service.schema(schema).table(PAYMENTS_PENDING_TABLE)
            .select("*")
            .eq("status", "pending")
            .order("paid_at", desc=False)
            .limit(1000)
            .execute().data
            or []
        )
    except Exception as e:
        st.error("Failed to load pending repayments.")
        st.code(_apierror_message(e), language="text")
        return

    dfp = pd.DataFrame(pending)
    if dfp.empty:
        st.success("No pending payments to confirm.")
        return

    st.dataframe(dfp, use_container_width=True, hide_index=True)

    if "id" not in dfp.columns:
        st.warning("Pending table has no 'id' column. Cannot confirm.")
        return

    pick_id = st.selectbox("Select pending payment ID", dfp["id"].tolist(), key="confirm_pick_id")
    row = dfp[dfp["id"] == pick_id].iloc[0].to_dict()

    col1, col2 = st.columns(2)

    with col1:
        if st.button("✅ CONFIRM", type="primary", use_container_width=True, key="btn_confirm_payment"):
            try:
                if hasattr(core, "confirm_payment_pending"):
                    core.confirm_payment_pending(sb_service, schema, pending_id=int(pick_id), actor_user_id=str(actor.user_id))
                else:
                    try:
                        sb_service.rpc("confirm_loan_repayment", {"pending_id": int(pick_id)}).execute()
                    except Exception:
                        payments_table = _pick_payments_table(sb_service, schema)
                        ins = row.copy()
                        ins.pop("id", None)
                        ins.pop("status", None)
                        sb_service.schema(schema).table(payments_table).insert(ins).execute()
                        sb_service.schema(schema).table(PAYMENTS_PENDING_TABLE).update({"status": "confirmed"}).eq("id", int(pick_id)).execute()

                audit(sb_service, "loan_payment_confirmed", "ok", {"pending_id": int(pick_id)}, actor_user_id=actor.user_id)
                _sync_total_due_with_interest(sb_service, schema, only_active=True)
                st.success("Confirmed.")
                st.rerun()
            except Exception as e:
                st.error("Confirm failed.")
                st.code(_apierror_message(e), language="text")

    with col2:
        reason = st.text_input("Reject reason", value="Rejected", key="reject_reason")
        if st.button("❌ REJECT", use_container_width=True, key="btn_reject_payment"):
            try:
                if hasattr(core, "reject_payment_pending"):
                    core.reject_payment_pending(sb_service, schema, pending_id=int(pick_id), reason=reason, actor_user_id=str(actor.user_id))
                else:
                    sb_service.schema(schema).table(PAYMENTS_PENDING_TABLE).update(
                        {"status": "rejected", "rejected_reason": reason}
                    ).eq("id", int(pick_id)).execute()

                audit(sb_service, "loan_payment_rejected", "ok", {"pending_id": int(pick_id), "reason": reason}, actor_user_id=actor.user_id)
                st.warning("Rejected.")
                st.rerun()
            except Exception as e:
                st.error("Reject failed.")
                st.code(_apierror_message(e), language="text")


# ============================================================
# Loan Repayment (Legacy) — INSERT + APPLY ONCE
# ============================================================
def _render_legacy_repayment(sb_service, schema: str, actor: Actor):
    require(actor.role, "legacy_repayment")

    st.subheader("💵 Loan Repayment (Legacy)")
    st.caption(f"Directly records repayments into: {LEGACY_REPAYMENTS_TABLE} (then applies once to loans_legacy).")

    loans = (
        sb_service.schema(schema).table("loans_legacy")
        .select("id,member_id,status,total_due,principal,principal_current,unpaid_interest")
        .order("id", desc=True)
        .limit(2000)
        .execute().data
        or []
    )
    df = pd.DataFrame(loans)
    if df.empty:
        st.warning("No loans found in loans_legacy.")
        return

    def _lbl(r):
        due = _num(r.get("total_due"))
        pc = _num(r.get("principal_current") or r.get("principal"))
        ui = _num(r.get("unpaid_interest"))
        return (
            f"Loan {int(r['id'])} • Member {r.get('member_id')} • {str(r.get('status') or '')} • "
            f"Principal {pc:,.0f} • Interest {ui:,.0f} • Due {due:,.0f}"
        )

    df["label"] = df.apply(_lbl, axis=1)
    pick = st.selectbox("Select loan", df["label"].tolist(), key="legacy_pick_loan")
    row = df[df["label"] == pick].iloc[0].to_dict()

    loan_id = int(row["id"])
    member_id = row.get("member_id")
    try:
        member_id = int(member_id) if member_id is not None else None
    except Exception:
        member_id = None

    amount = st.number_input("Amount", min_value=0.0, step=50.0, value=0.0, key="legacy_amt")
    paid_on = st.date_input("Paid date", value=date.today(), key="legacy_date")
    note = st.text_input("Note (optional)", value="Legacy loan repayment", key="legacy_note")

    if st.button("💾 Save repayment (legacy)", type="primary", use_container_width=True, key="legacy_save"):
        if float(amount) <= 0:
            st.error("Amount must be > 0.")
            st.stop()

        # Always derive member_id from loans_legacy if missing
        if member_id is None:
            try:
                r2 = (
                    sb_service.schema(schema).table("loans_legacy")
                    .select("member_id")
                    .eq("id", int(loan_id))
                    .limit(1)
                    .execute().data
                    or []
                )
                member_id = int(r2[0]["member_id"]) if r2 and r2[0].get("member_id") is not None else None
            except Exception:
                member_id = None

        if member_id is None:
            st.error("❌ Cannot save repayment: member_id is missing for this loan in loans_legacy.")
            st.stop()

        session_id = _get_current_session_id(sb_service, schema)

        payload = {
            "loan_id": int(loan_id),
            "member_id": int(member_id),
            "amount": float(amount),
            "paid_at": _to_iso(paid_on),
            "recorded_by": str(actor.user_id),
            "notes": note,
        }

        # add session_id if the column exists
        try:
            ok = _columns_exist(sb_service, schema, LEGACY_REPAYMENTS_TABLE, ["session_id"]).get("session_id", False)
            if ok and session_id:
                payload["session_id"] = session_id
        except Exception:
            pass

        # Prevent accidental double-click in the same rerun
        lock_key = f"_legacy_saved_{loan_id}_{payload['paid_at']}_{payload['amount']}"
        if st.session_state.get(lock_key):
            st.warning("Already saved (prevented duplicate click).")
            st.stop()
        st.session_state[lock_key] = True

        try:
            # ✅ Always insert repayment into legacy table (single source)
            sb_service.schema(schema).table(LEGACY_REPAYMENTS_TABLE).insert(payload).execute()

            # ✅ Apply repayment ONCE to loans_legacy
            res = _apply_repayment_once(sb_service, schema, loan_id=int(loan_id), pay_amount=float(amount))
            if res.get("ok"):
                _sync_total_due_with_interest(sb_service, schema, only_active=True)
                st.success(
                    f"Saved. Applied → Interest paid: {res['interest_paid']:,.0f}, "
                    f"Principal paid: {res['principal_paid']:,.0f}. "
                    f"New principal: {res['principal_new']:,.0f}, New due: {res['total_due_new']:,.0f}"
                )
            else:
                st.warning(f"Saved repayment row, but loan not updated: {res.get('error')}")

            audit(sb_service, "loan_payment_legacy_saved", "ok",
                  {"loan_id": int(loan_id), "member_id": int(member_id), "amount": float(amount)},
                  actor_user_id=actor.user_id)

            st.rerun()

        except Exception as e:
            st.session_state[lock_key] = False
            st.error("Failed to save legacy repayment.")
            st.code(_apierror_message(e), language="text")

    st.divider()
    st.markdown(f"### Recent repayments ({LEGACY_REPAYMENTS_TABLE})")
    try:
        rows = (
            sb_service.schema(schema).table(LEGACY_REPAYMENTS_TABLE)
            .select("*")
            .eq("loan_id", int(loan_id))
            .order("paid_at", desc=True)
            .limit(200)
            .execute().data
            or []
        )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning("Could not load repayments.")
        st.code(_apierror_message(e), language="text")


# ============================================================
# Interest UI
# ============================================================
def _render_interest(sb_service, schema: str, actor: Actor):
    require(actor.role, "accrue_interest")
    st.subheader("Interest")

    st.caption("Loans store interest in loans_legacy (unpaid_interest, total_interest_generated, etc.).")
    st.caption("After accrual, we sync total_due = principal_current + unpaid_interest.")

    totals = _interest_ledger_totals(sb_service, schema)
    mk = _month_key()

    c1, c2, c3 = st.columns(3)
    c1.metric("Interest this month (ledger)", f"{totals['this_month']:,.2f}")
    c2.metric("Interest all-time (ledger)", f"{totals['all_time']:,.2f}")
    last = totals.get("last_row") or {}
    c3.metric("Last accrual month (ledger)", str(last.get("interest_month") or "—"))

    if totals.get("error"):
        st.info("Ledger not available (ok):")
        st.write(totals["error"])

    st.divider()

    if st.button("➕ Accrue monthly interest", use_container_width=True, key="accrue_interest_btn"):
        try:
            updated, added = core.accrue_monthly_interest(sb_service, schema, actor_user_id=str(actor.user_id))
            audit(sb_service, "interest_accrued", "ok",
                  {"updated": int(updated), "added": float(added), "month": mk},
                  actor_user_id=actor.user_id)

            sync = _sync_total_due_with_interest(sb_service, schema, only_active=True)

            if float(added) <= 0 and int(updated) <= 0:
                st.info(f"No changes made (already accrued for {mk} or no eligible loans).")
            else:
                st.success(f"Updated loans: {int(updated)} • Interest added: {float(added):,.2f}")

            if sync.get("error"):
                st.warning("Interest accrued, but could not sync total_due/interest columns.")
                st.code(str(sync["error"]), language="text")
            else:
                st.caption("Synced loan totals so Due includes interest:")
                st.write(sync)

            st.rerun()

        except Exception as e:
            st.error("Interest accrual failed.")
            st.code(_apierror_message(e), language="text")


# ============================================================
# Delinquency UI (DPD)
# ============================================================
def _render_delinquency(sb_service, schema: str, actor: Actor):
    require(actor.role, "view_delinquency")
    payments_table = _pick_payments_table(sb_service, schema)

    st.subheader("Delinquency (DPD)")
    st.caption(f"Using repayments source: {payments_table}")

    loans = (
        sb_service.schema(schema).table("loans_legacy")
        .select("id,member_id,status,due_date,principal_current,total_due")
        .order("id", desc=True)
        .limit(5000)
        .execute().data
        or []
    )
    df = _safe_df(loans)
    if df.empty:
        st.info("No loans found.")
        return

    reps = (
        sb_service.schema(schema).table(payments_table)
        .select("loan_id,paid_at")
        .order("paid_at", desc=True)
        .limit(20000)
        .execute().data
        or []
    )
    dfr = _safe_df(reps)

    last_paid_map: dict[int, date] = {}
    if not dfr.empty:
        dfr["paid_at"] = pd.to_datetime(dfr.get("paid_at"), errors="coerce")
        dfr = dfr.dropna(subset=["paid_at"]).sort_values("paid_at", ascending=False)

        for _, r in dfr.iterrows():
            lid = pd.to_numeric(r.get("loan_id"), errors="coerce")
            if pd.isna(lid):
                continue
            lid = int(lid)
            if lid and lid not in last_paid_map:
                last_paid_map[lid] = r["paid_at"].date()

    df["last_paid_on"] = df["id"].apply(lambda x: last_paid_map.get(int(x)))
    df["dpd"] = df.apply(lambda r: core.compute_dpd(r.to_dict(), r.get("last_paid_on")), axis=1)

    st.dataframe(df.sort_values("dpd", ascending=False), use_container_width=True, hide_index=True)


# ============================================================
# Loan Statement UI
# ============================================================
def _render_statement(sb_service, schema: str, actor: Actor):
    require(actor.role, "loan_statement")
    payments_table = _pick_payments_table(sb_service, schema)

    st.subheader("Loan Statement (Preview + PDF Download)")
    st.caption(f"Payments source: {payments_table}")

    mid = st.number_input(
        "Member ID",
        min_value=1, step=1,
        value=(actor.member_id or 1),
        key="stmt_member_id"
    )

    if actor.role == ROLE_MEMBER and actor.member_id and int(mid) != int(actor.member_id):
        st.warning("Members can only view their own statement.")
        return

    if st.button("Load Statement", use_container_width=True, key="stmt_load"):
        st.session_state["stmt_loaded_member_id"] = int(mid)

    loaded_mid = st.session_state.get("stmt_loaded_member_id")
    if not loaded_mid:
        return

    mrow = (
        sb_service.schema(schema).table("members_legacy")
        .select("id,name,position").eq("id", int(loaded_mid)).limit(1)
        .execute().data or []
    )
    mrow = mrow[0] if mrow else {}
    member = {
        "member_id": int(loaded_mid),
        "member_name": mrow.get("name") or f"Member {loaded_mid}",
        "position": mrow.get("position"),
    }

    mloans = (
        sb_service.schema(schema).table("loans_legacy")
        .select("*").eq("member_id", int(loaded_mid))
        .order("issued_at", desc=True).limit(5000)
        .execute().data or []
    )

    if not mloans:
        st.info("This member has no loans yet.")
        return

    loan_ids = [int(l["id"]) for l in mloans if l.get("id") is not None]
    mpay = get_repayments_for_loan_ids(sb_service, schema, loan_ids, limit=5000)

    st.markdown("### Loans")
    st.dataframe(pd.DataFrame(mloans), use_container_width=True, hide_index=True)
    st.markdown(f"### Loan Repayments ({payments_table})")
    st.dataframe(pd.DataFrame(mpay), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("### Download PDF")

    if make_member_loan_statement_pdf is None:
        st.warning("PDF engine not available. Ensure pdfs.py defines make_member_loan_statement_pdf.")
        return

    statement_sig = None
    try:
        if hasattr(core, "get_statement_signature"):
            statement_sig = core.get_statement_signature(sb_service, schema, int(mloans[0]["id"]))
    except Exception:
        statement_sig = None

    try:
        pdf_bytes = _build_statement_pdf(member=member, mloans=mloans, mpay=mpay, statement_sig=statement_sig)
    except Exception as e:
        st.error("PDF generation failed.")
        st.code(str(e), language="text")
        return

    st.download_button(
        "⬇️ Download Loan Statement (PDF)",
        pdf_bytes,
        file_name=f"loan_statement_{member['member_id']:02d}_{str(member['member_name']).replace(' ', '_')}.pdf",
        mime="application/pdf",
        use_container_width=True,
        key="dl_member_loan_statement_pdf",
    )


# ============================================================
# MAIN ENTRY
# ============================================================
def render_loans(sb_service, schema: str, actor_user_id: str = ""):
    actor_user_uuid = actor_user_id if (actor_user_id and _is_uuid(actor_user_id)) else _get_or_make_session_uuid()
    actor = _actor_from_session(actor_user_uuid)

    st.header("Loans (Organizational Standard)")

    # KPIs
    loans_all = (
        sb_service.schema(schema).table("loans_legacy")
        .select("id,status,total_due")
        .limit(20000).execute().data or []
    )
    df_all = pd.DataFrame(loans_all)
    if df_all.empty:
        active_count, active_due = 0, 0.0
    else:
        df_all["status"] = df_all["status"].astype(str).str.lower().str.strip()
        df_all["total_due"] = pd.to_numeric(df_all.get("total_due"), errors="coerce").fillna(0)
        active = df_all[df_all["status"].isin(["open", "active"])]
        active_count = len(active)
        active_due = float(active["total_due"].sum())

    k1, k2, k3 = st.columns(3)
    k1.metric("Active loans", str(active_count))
    k2.metric("Total due (active)", f"{active_due:,.0f}")
    k3.metric("Monthly interest", "5%")
    st.divider()

    sections = allowed_sections(actor.role) or []
    if not sections:
        st.warning("No sections available for your role.")
        return

    if "loans_menu" not in st.session_state or st.session_state["loans_menu"] not in sections:
        st.session_state["loans_menu"] = sections[0]

    section = st.selectbox("Loans menu", sections, key="loans_menu")

    if section == "Requests":
        _render_requests(sb_service, schema, actor); return
    if section == "Ledger":
        _render_ledger(sb_service, schema, actor); return
    if section == "Record Payment":
        _render_record_payment(sb_service, schema, actor); return
    if section == "Confirm Payments":
        _render_confirm_payments(sb_service, schema, actor); return
    if section == "Loan Repayment (Legacy)":
        _render_legacy_repayment(sb_service, schema, actor); return
    if section == "Interest":
        _render_interest(sb_service, schema, actor); return
    if section == "Delinquency":
        _render_delinquency(sb_service, schema, actor); return
    if section == "Loan Statement":
        _render_statement(sb_service, schema, actor); return

    st.info(f"Section '{section}' is enabled but not implemented.")
