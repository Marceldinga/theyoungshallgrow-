
# loans_ui.py ✅ COMPLETE UPDATED SINGLE FILE — NEW STANDARD (NO legacy, member_id only, request→approve with signatures)
# ✅ Uses NEW tables only:
#    - members (id, name, display_name, phone)
#    - loans (id, member_id, status, principal_current, principal, unpaid_interest, total_interest_generated, total_due,
#            borrow_date, last_paid_at, due_cycle_days, interest_rate_monthly, created_at, updated_at)
#    - loan_payments (id, loan_id, member_id, amount, paid_at, created_at, note)
#    - interest_ledger (id, loan_id, member_id, amount, interest_month='YYYY-MM-DD', created_at, note)
#    - loan_requests (id, requester_user_id, requester_member_id, surety_member_id, amount, status, created_at,
#                    decided_at, approved_loan_id, admin_note)
#    - signatures (entity_type, entity_id, role, signer_member_id, signer_name, signed_at)
#
# ✅ Governance:
#    - Member submits request (member_id only)
#    - Required signatures: borrower + surety + treasury (entity_type='loan_request', entity_id=request_id)
#    - Admin approves only if signatures complete + cap passes (handled in loans_core)
#
# ✅ Payments:
#    - Record payment directly into loan_payments via loans_core.record_payment (interest-first → principal)
#
# ✅ Interest:
#    - Manual "Accrue interest now" calls loans_core.accrue_monthly_interest (writes to interest_ledger)
#    - interest_ledger interest_month is cycle key 'YYYY-MM-DD', so "this month" uses startswith('YYYY-MM')
#
# ✅ No legacy apply / no applied_to_loan needed (new tables are idempotent by design)

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4, UUID

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

# Optional audit (safe)
try:
    from audit import audit
except Exception:
    def audit(*args, **kwargs):
        return None


# ============================================================
# TABLES
# ============================================================
MEMBERS_TABLE = "members"
LOANS_TABLE = "loans"
PAYMENTS_TABLE = "loan_payments"
REQUESTS_TABLE = "loan_requests"
SIGNATURES_TABLE = "signatures"
INTEREST_LEDGER_TABLE = "interest_ledger"

# Optional views (recommended for names/phones)
V_LOANS_WITH_MEMBER = "v_loans_with_member"
V_PAYMENTS_WITH_MEMBER = "v_loan_payments_with_member"

# Signatures settings (must match loans_core)
REQ_ENTITY_TYPE = "loan_request"
REQ_SIG_REQUIRED = ["borrower", "surety", "treasury"]


# ============================================================
# HELPERS
# ============================================================
def _apierror_message(e: Exception) -> str:
    if isinstance(e, APIError):
        payload = e.args[0] if getattr(e, "args", None) else {}
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("details") or payload.get("hint") or "APIError")
        return str(e)
    return str(e)


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
    return datetime.combine(d, datetime.min.time()).isoformat()


def _num(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def _month_key(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def _safe_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows or [])


def _table_exists(sb, schema: str, table_name: str) -> bool:
    try:
        sb.schema(schema).table(table_name).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def _read_members(sb, schema: str) -> pd.DataFrame:
    rows = (
        sb.schema(schema).table(MEMBERS_TABLE)
        .select("id,name,display_name,phone")
        .order("id", desc=False)
        .limit(5000)
        .execute().data or []
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["id", "name", "display_name", "phone", "label"])
    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    df["name"] = df["name"].astype(str)
    if "display_name" in df.columns:
        df["display_name"] = df["display_name"].astype(str).replace({"None": "", "nan": ""})
    else:
        df["display_name"] = ""
    if "phone" in df.columns:
        df["phone"] = df["phone"].astype(str).replace({"None": "", "nan": ""})
    else:
        df["phone"] = ""
    df["label"] = df.apply(lambda r: f"{int(r['id']):02d} • {(r['display_name'] or r['name'])}", axis=1)
    return df


def _signature_status(sb, schema: str, request_id: int) -> tuple[pd.DataFrame, list[str]]:
    """
    Returns (df_signatures, missing_roles)
    """
    try:
        rows = (
            sb.schema(schema).table(SIGNATURES_TABLE)
            .select("entity_type,entity_id,role,signer_member_id,signer_name,signed_at")
            .eq("entity_type", REQ_ENTITY_TYPE)
            .eq("entity_id", int(request_id))
            .order("signed_at", desc=False)
            .limit(1000)
            .execute().data or []
        )
    except Exception:
        rows = []

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["role", "signer_member_id", "signer_name", "signed_at"]), REQ_SIG_REQUIRED

    roles = df["role"].astype(str).str.lower().str.strip()
    ok = set(roles[pd.to_numeric(df["signer_member_id"], errors="coerce").notna()].tolist())
    missing = [r for r in REQ_SIG_REQUIRED if r.lower() not in ok]
    return df, missing


def _upsert_signature(sb, schema: str, request_id: int, role: str, signer_member_id: int, signer_name: str):
    core.insert_signature(
        sb, schema,
        entity_type=REQ_ENTITY_TYPE,
        entity_id=int(request_id),
        role=str(role).strip().lower(),
        signer_member_id=int(signer_member_id),
        signer_name=str(signer_name or "").strip(),
    )


# ============================================================
# KPIs (top of Loans page)
# ============================================================
def _render_kpis(sb, schema: str):
    try:
        loans = (
            sb.schema(schema).table(LOANS_TABLE)
            .select("id,status,total_due,principal_current,principal,unpaid_interest")
            .limit(20000).execute().data or []
        )
    except Exception:
        loans = []

    df = pd.DataFrame(loans)
    if df.empty:
        active_count = 0
        active_due = 0.0
        active_principal_current = 0.0
    else:
        df["status"] = df["status"].astype(str).str.lower().str.strip()
        df["total_due"] = pd.to_numeric(df.get("total_due"), errors="coerce").fillna(0.0)
        pc = pd.to_numeric(df.get("principal_current"), errors="coerce")
        p = pd.to_numeric(df.get("principal"), errors="coerce")
        df["principal_current_eff"] = pc.fillna(p).fillna(0.0)

        active = df[df["status"].isin(["open", "active"])]
        active_count = len(active)
        active_due = float(active["total_due"].sum())
        active_principal_current = float(active["principal_current_eff"].sum())

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Active loans", str(active_count))
    k2.metric("Principal current (active)", f"{active_principal_current:,.0f}")
    k3.metric("Total due (active)", f"{active_due:,.0f}")
    k4.metric("Cycle / Rate", "28 days • 5%")
    st.divider()


# ============================================================
# REQUESTS (member submits; admin approves with signatures)
# ============================================================
def _render_requests(sb, schema: str, actor: Actor):
    require(actor.role, "submit_request")
    st.subheader("Requests")
    st.caption("Member submits request (member_id only). Admin approves only after required signatures.")

    dfm = _read_members(sb, schema)
    if dfm.empty:
        st.warning("No members found in members.")
        return

    labels = dfm["label"].tolist()
    label_to_id = dict(zip(dfm["label"], dfm["id"]))

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
                    sb, schema,
                    borrower_id=borrower_id,
                    surety_id=surety_id,
                    amount=float(amount),
                    requester_user_id=str(actor.user_id),
                )
                audit(sb, "loan_request_created", "ok", {"request_id": int(req_id)}, actor_user_id=actor.user_id)
                st.success(f"Request submitted. ID = {req_id}")
                st.rerun()
            except Exception as e:
                st.error("Failed to create request.")
                st.code(_apierror_message(e), language="text")

    st.divider()
    st.markdown("### Pending requests")

    pending = core.list_pending_requests(sb, schema, limit=300)
    dfp = _safe_df(pending)
    if dfp.empty:
        st.info("No pending requests.")
        return

    show_cols = [c for c in ["id", "requester_member_id", "surety_member_id", "amount", "status", "created_at"] if c in dfp.columns]
    st.dataframe(dfp[show_cols] if show_cols else dfp, use_container_width=True, hide_index=True)

    # ---- Signature collection + approval (admin/treasury only) ----
    if actor.role not in (ROLE_ADMIN, ROLE_TREASURY):
        st.info("Only Admin/Treasury can manage signatures and approve requests.")
        return

    st.markdown("### Signatures & Approval")
    req_id = st.selectbox("Select request ID", dfp["id"].tolist(), key="req_pick_id")
    _ = dfp[dfp["id"] == req_id].iloc[0].to_dict()

    df_sig, missing = _signature_status(sb, schema, int(req_id))
    if df_sig.empty:
        st.warning(f"No signatures yet. Required: {', '.join(REQ_SIG_REQUIRED)}")
    else:
        st.caption("Signatures on file (entity_type=loan_request):")
        st.dataframe(df_sig, use_container_width=True, hide_index=True)
        if missing:
            st.warning("Missing signatures: " + ", ".join(missing))
        else:
            st.success("All required signatures are present.")

    st.markdown("#### Add/Update a signature (upsert by role)")
    role = st.selectbox("Role", REQ_SIG_REQUIRED, index=0, key="sig_role")
    signer_pick = st.selectbox("Signer member", labels, key="sig_signer_pick")
    signer_id = int(label_to_id[signer_pick])
    signer_row = dfm[dfm["id"] == signer_id].iloc[0].to_dict()
    signer_name = (signer_row.get("display_name") or signer_row.get("name") or "").strip()

    if st.button("✍️ Save Signature", use_container_width=True, key="sig_save_btn"):
        try:
            _upsert_signature(sb, schema, int(req_id), role=role, signer_member_id=signer_id, signer_name=signer_name)
            audit(sb, "loan_request_signature_saved", "ok",
                  {"request_id": int(req_id), "role": role, "signer_member_id": int(signer_id)},
                  actor_user_id=actor.user_id)
            st.success("Signature saved.")
            st.rerun()
        except Exception as e:
            st.error("Failed to save signature.")
            st.code(_apierror_message(e), language="text")

    st.divider()
    c1, c2 = st.columns(2)

    with c1:
        if st.button("✅ APPROVE REQUEST", type="primary", use_container_width=True, key="approve_req_btn"):
            try:
                loan_id = core.approve_loan_request(sb, schema, request_id=int(req_id), actor_user_id=str(actor.user_id))
                audit(sb, "loan_request_approved", "ok",
                      {"request_id": int(req_id), "loan_id": int(loan_id)},
                      actor_user_id=actor.user_id)
                st.success(f"Approved. Loan ID = {loan_id}")
                st.rerun()
            except Exception as e:
                st.error("Approval blocked.")
                st.code(_apierror_message(e), language="text")

    with c2:
        reason = st.text_input("Deny reason", value="Denied", key="deny_reason")
        if st.button("❌ DENY REQUEST", use_container_width=True, key="deny_req_btn"):
            try:
                core.deny_loan_request(sb, schema, request_id=int(req_id), reason=reason, actor_user_id=str(actor.user_id))
                audit(sb, "loan_request_denied", "ok",
                      {"request_id": int(req_id), "reason": reason},
                      actor_user_id=actor.user_id)
                st.warning("Denied.")
                st.rerun()
            except Exception as e:
                st.error("Deny failed.")
                st.code(_apierror_message(e), language="text")


# ============================================================
# LEDGER (Loans table)
# ============================================================
def _render_ledger(sb, schema: str, actor: Actor):
    require(actor.role, "view_ledger")
    st.subheader("Ledger (Loans)")
    st.caption("Loans are stored by member_id. Names/phones appear via views if available.")

    table = V_LOANS_WITH_MEMBER if _table_exists(sb, schema, V_LOANS_WITH_MEMBER) else LOANS_TABLE
    try:
        rows = (
            sb.schema(schema).table(table)
            .select("*")
            .order("updated_at", desc=True)
            .limit(2000)
            .execute().data or []
        )
    except Exception as e:
        st.error("Failed to load loans.")
        st.code(_apierror_message(e), language="text")
        return

    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No loans found.")
        return

    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("### Delinquency (DPD)")
    try:
        dfd = core.delinquency_table(sb, schema, limit=500)
        if dfd.empty:
            st.info("No delinquent active/open loans.")
        else:
            st.dataframe(dfd, use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning("Could not compute delinquency.")
        st.code(_apierror_message(e), language="text")


# ============================================================
# RECORD PAYMENT (direct to loan_payments)
# ============================================================
def _render_record_payment(sb, schema: str, actor: Actor):
    require(actor.role, "record_payment")
    st.subheader("Record Payment")
    st.caption("Records a payment into loan_payments and updates loan balances (interest-first).")

    table = V_LOANS_WITH_MEMBER if _table_exists(sb, schema, V_LOANS_WITH_MEMBER) else LOANS_TABLE

    # Try view columns; fallback if not present
    select_cols_view = "id,member_id,status,total_due,principal,principal_current,unpaid_interest,member_display_name,member_name"
    select_cols_base = "id,member_id,status,total_due,principal,principal_current,unpaid_interest"

    try:
        loans = (
            sb.schema(schema).table(table)
            .select(select_cols_view)
            .order("id", desc=True)
            .limit(2000)
            .execute().data or []
        )
    except Exception:
        loans = (
            sb.schema(schema).table(LOANS_TABLE)
            .select(select_cols_base)
            .order("id", desc=True)
            .limit(2000)
            .execute().data or []
        )

    df = pd.DataFrame(loans)
    if df.empty:
        st.warning("No loans found.")
        return

    def _lbl(r):
        due = _num(r.get("total_due"))
        pc = _num(r.get("principal_current") or r.get("principal"))
        ui = _num(r.get("unpaid_interest"))
        who = r.get("member_display_name") or r.get("member_name") or f"Member {r.get('member_id')}"
        return f"Loan {int(r['id'])} • {who} • {str(r.get('status') or '')} • Principal {pc:,.0f} • Interest {ui:,.0f} • Due {due:,.0f}"

    df["label"] = df.apply(_lbl, axis=1)
    pick = st.selectbox("Select loan", df["label"].tolist(), key="pay_pick_loan")
    loan_id = int(df[df["label"] == pick].iloc[0]["id"])

    amount = st.number_input("Amount", min_value=0.0, step=50.0, value=0.0, key="pay_amt")
    paid_on = st.date_input("Paid date", value=date.today(), key="pay_date")
    note = st.text_input("Note (optional)", value="Loan payment", key="pay_note")

    if st.button("💾 Save payment", type="primary", use_container_width=True, key="pay_save"):
        if float(amount) <= 0:
            st.error("Amount must be > 0.")
            st.stop()
        try:
            core.record_payment(
                sb, schema,
                loan_id=int(loan_id),
                amount=float(amount),
                paid_at=_to_iso(paid_on),
                note=note,
            )
            audit(sb, "loan_payment_recorded", "ok",
                  {"loan_id": int(loan_id), "amount": float(amount)},
                  actor_user_id=actor.user_id)
            st.success("Payment recorded and applied.")
            st.rerun()
        except Exception as e:
            st.error("Failed to record payment.")
            st.code(_apierror_message(e), language="text")

    st.divider()
    st.markdown("### Recent payments for selected loan")
    pay_table = V_PAYMENTS_WITH_MEMBER if _table_exists(sb, schema, V_PAYMENTS_WITH_MEMBER) else PAYMENTS_TABLE
    try:
        rows = (
            sb.schema(schema).table(pay_table)
            .select("*")
            .eq("loan_id", int(loan_id))
            .order("paid_at", desc=True)
            .limit(200)
            .execute().data or []
        )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning("Could not load payments.")
        st.code(_apierror_message(e), language="text")


# ============================================================
# INTEREST (ledger-based + accrual trigger)
# ============================================================
def _interest_ledger_totals(sb, schema: str) -> dict:
    out = {"all_time": 0.0, "this_month": 0.0, "last_row": None, "ok": False, "error": None}

    if not _table_exists(sb, schema, INTEREST_LEDGER_TABLE):
        out["error"] = f"Table {schema}.{INTEREST_LEDGER_TABLE} not found/readable."
        return out

    try:
        rows = (
            sb.schema(schema).table(INTEREST_LEDGER_TABLE)
            .select("amount,interest_month,created_at,loan_id,member_id,note")
            .order("created_at", desc=True)
            .limit(20000)
            .execute().data or []
        )

        df = pd.DataFrame(rows)
        if df.empty:
            out["ok"] = True
            return out

        df["amount"] = pd.to_numeric(df.get("amount"), errors="coerce").fillna(0.0)
        df["interest_month"] = df.get("interest_month").astype(str)

        mk = _month_key()  # YYYY-MM
        out["all_time"] = float(df["amount"].sum())

        # ✅ cycle keys are YYYY-MM-DD, so this month = prefix match
        out["this_month"] = float(df[df["interest_month"].str.startswith(mk)]["amount"].sum())

        out["last_row"] = rows[0] if rows else None
        out["ok"] = True
        return out

    except Exception as e:
        out["error"] = _apierror_message(e)
        return out


def _render_interest(sb, schema: str, actor: Actor):
    require(actor.role, "accrue_interest")
    st.subheader("Interest")
    st.caption("Interest is ledger-based (interest_ledger). This month is computed by YYYY-MM prefix.")

    totals = _interest_ledger_totals(sb, schema)
    mk = _month_key()

    c1, c2, c3 = st.columns(3)
    c1.metric("Interest this month (ledger)", f"{totals['this_month']:,.2f}")
    c2.metric("Interest all-time (ledger)", f"{totals['all_time']:,.2f}")
    last = totals.get("last_row") or {}
    c3.metric("Last cycle key", str(last.get("interest_month") or "—"))

    if totals.get("error"):
        st.info("Ledger check:")
        st.write(totals["error"])

    st.divider()

    if st.button("➕ Accrue interest now", use_container_width=True, key="accrue_interest_btn"):
        try:
            updated, added = core.accrue_monthly_interest(sb, schema, actor_user_id=str(actor.user_id))
            audit(sb, "interest_accrued", "ok",
                  {"updated": int(updated), "added": float(added), "cycle_prefix": mk},
                  actor_user_id=actor.user_id)

            if float(added) <= 0 and int(updated) <= 0:
                st.info("No changes made (no loans due yet or already accrued for this cycle).")
            else:
                st.success(f"Loans updated: {int(updated)} • Interest added: {float(added):,.2f}")

            st.rerun()

        except Exception as e:
            st.error("Interest accrual failed.")
            st.code(_apierror_message(e), language="text")

    st.divider()
    st.markdown("### Recent interest ledger rows")
    try:
        rows = (
            sb.schema(schema).table(INTEREST_LEDGER_TABLE)
            .select("*")
            .order("created_at", desc=True)
            .limit(200)
            .execute().data or []
        )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning("Could not load ledger rows.")
        st.code(_apierror_message(e), language="text")


# ============================================================
# STATEMENTS (PDFs)
# ============================================================
def _render_statements(sb, schema: str, actor: Actor):
    require(actor.role, "view_statements")
    st.subheader("Statements")

    if make_member_loan_statement_pdf is None:
        st.info("PDF engine not available (make_member_loan_statement_pdf import failed).")
        return

    dfm = _read_members(sb, schema)
    if dfm.empty:
        st.warning("No members found.")
        return

    pick = st.selectbox("Select member", dfm["label"].tolist(), key="stmt_pick_member")
    member_id = int(dfm[dfm["label"] == pick]["id"].iloc[0])

    m = dfm[dfm["id"] == member_id].iloc[0].to_dict()
    member = {
        "member_id": int(member_id),
        "name": str(m.get("name") or ""),
        "display_name": str(m.get("display_name") or ""),
        "phone": str(m.get("phone") or ""),
    }

    loans = core.list_member_loans(sb, schema, member_id=int(member_id), limit=2000)
    if not loans:
        st.info("No loans found for this member.")
        return

    loan_ids = [int(x.get("id")) for x in loans if str(x.get("id") or "").isdigit()]
    payments = []
    if loan_ids:
        try:
            pay_rows = (
                sb.schema(schema).table(PAYMENTS_TABLE)
                .select("*")
                .in_("loan_id", loan_ids)
                .order("paid_at", desc=True)
                .limit(5000)
                .execute().data or []
            )
            payments = pay_rows
        except Exception:
            payments = []

    st.caption("Preview (loans)")
    st.dataframe(pd.DataFrame(loans), use_container_width=True, hide_index=True)
    st.caption("Preview (payments)")
    st.dataframe(pd.DataFrame(payments), use_container_width=True, hide_index=True)

    if st.button("⬇️ Download Member Loan Statement (PDF)", use_container_width=True, key="dl_member_stmt"):
        try:
            pdf_bytes = make_member_loan_statement_pdf(
                brand="theyoungshallgrow",
                member=member,
                cycle_info={},
                loans=loans,
                payments=payments,
                currency="$",
                logo_path=None,
            )
            st.download_button(
                "✅ Click to download",
                pdf_bytes,
                file_name=f"loan_statement_member_{member_id}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key="dl_member_stmt_btn",
            )
        except Exception as e:
            st.error("Failed to generate statement PDF.")
            st.code(_apierror_message(e), language="text")


# ============================================================
# MAIN ENTRY
# ============================================================
def render_loans(sb_service, schema: str, actor_user_id: str = ""):
    # Table sanity (clean errors)
    required_tables = [MEMBERS_TABLE, LOANS_TABLE, PAYMENTS_TABLE, INTEREST_LEDGER_TABLE, REQUESTS_TABLE, SIGNATURES_TABLE]
    missing = [t for t in required_tables if not _table_exists(sb_service, schema, t)]
    if missing:
        st.error("Missing required table(s) for NEW Loans standard:")
        st.write(", ".join([f"{schema}.{t}" for t in missing]))
        st.stop()

    actor_user_uuid = actor_user_id if (actor_user_id and _is_uuid(actor_user_id)) else _get_or_make_session_uuid()
    actor = _actor_from_session(actor_user_uuid)

    st.header("Loans (Organizational Standard — NEW)")

    _render_kpis(sb_service, schema)

    sections = allowed_sections(actor.role) or []
    if not sections:
        st.warning("No sections available for your role.")
        return

    # Ensure defaults exist (won't break if RBAC uses different list)
    defaults = ["Requests", "Ledger", "Record Payment", "Interest", "Statements"]
    for s in defaults:
        if s not in sections:
            sections.append(s)

    # de-duplicate while preserving order
    seen = set()
    sections = [x for x in sections if not (x in seen or seen.add(x))]

    if "loans_menu" not in st.session_state or st.session_state["loans_menu"] not in sections:
        st.session_state["loans_menu"] = sections[0]

    section = st.selectbox("Loans menu", sections, key="loans_menu")

    if section == "Requests":
        _render_requests(sb_service, schema, actor); return
    if section == "Ledger":
        _render_ledger(sb_service, schema, actor); return
    if section == "Record Payment":
        _render_record_payment(sb_service, schema, actor); return
    if section == "Interest":
        _render_interest(sb_service, schema, actor); return
    if section == "Statements":
        _render_statements(sb_service, schema, actor); return

    st.info(f"Section '{section}' is enabled but not implemented in this UI file.")
