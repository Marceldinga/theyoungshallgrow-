import os
import streamlit as st
import pandas as pd
from datetime import datetime, timezone

from supabase import create_client


APP_BRAND = "theyoungshallgrow"
EXPECTED_ACTIVE_MEMBERS = 17

ALLOWED_CONTRIB_KINDS = ["paid", "contributed"]


def get_secret(key: str):
    try:
        return st.secrets.get(key)
    except Exception:
        return os.getenv(key)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def money(x):
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)


def show_missing_secrets_or_stop():
    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_ANON_KEY")
    if not url or not key:
        st.error("Missing SUPABASE_URL / SUPABASE_ANON_KEY in Streamlit Secrets.")
        st.stop()
    return url, key


SUPABASE_URL, SUPABASE_ANON_KEY = show_missing_secrets_or_stop()
sb_public = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def authed_client():
    c = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    sess = st.session_state.get("session")
    if sess:
        c.auth.set_session(sess.access_token, sess.refresh_token)
    return c


def fetch_one(qb):
    try:
        res = qb.limit(1).execute()
        rows = res.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def to_df(resp):
    return pd.DataFrame(resp.data or [])


def has_columns(c, table: str, cols: list[str]) -> bool:
    try:
        sel = ",".join(cols)
        c.table(table).select(sel).limit(1).execute()
        return True
    except Exception:
        return False


def schema_check_or_stop(c):
    required = [
        ("member_registry", ["legacy_member_id", "full_name", "is_active"]),
        ("profiles", ["id", "role", "approved", "member_id"]),
        ("app_state", ["id", "next_payout_index", "next_payout_date"]),
        ("contributions_legacy", ["session_id", "member_id", "amount", "kind"]),
        ("foundation_payments_legacy", ["member_id", "amount_paid", "amount_pending"]),
        ("loans_legacy", ["id", "member_id", "status", "balance", "total_due", "total_interest_generated", "issued_at", "created_at", "accrued_interest"]),
        ("fines_legacy", ["member_id", "amount", "status"]),
        ("payouts_legacy", ["member_id", "member_name", "payout_amount", "payout_date", "created_at"]),
        ("audit_log", ["id", "created_at", "action", "status"]),
        ("loan_interest_snapshots", ["snapshot_date", "lifetime_interest_generated"]),
        ("loan_requests", ["id", "created_at", "requester_user_id", "requester_member_id", "surety_member_id", "amount", "status"]),
        ("signatures", ["entity_type", "entity_id", "role", "signer_name", "signed_at"]),
        ("loan_payments", ["loan_legacy_id", "amount", "paid_on"]),
        ("sessions_legacy", ["id", "start_date"]),
    ]

    missing = []
    for tbl, cols in required:
        if not has_columns(c, tbl, cols):
            missing.append({"table": tbl, "required_columns": ", ".join(cols)})

    if missing:
        st.error("Schema check failed. Missing tables/columns:")
        st.table(pd.DataFrame(missing))
        st.stop()


def get_app_state(c):
    return fetch_one(c.table("app_state").select("*").eq("id", 1)) or {}


def current_session_id(c):
    row = fetch_one(c.table("sessions_legacy").select("id,start_date").order("start_date", desc=True))
    return (row or {}).get("id")


def load_member_registry(c):
    resp = c.table("member_registry").select("legacy_member_id,full_name,is_active").order("legacy_member_id").execute()
    rows = resp.data or []
    df = pd.DataFrame(rows)

    labels, label_to_legacy, label_to_name = [], {}, {}
    for r in rows:
        mid = int(r.get("legacy_member_id"))
        name = (r.get("full_name") or f"Member {mid}").strip()
        active = r.get("is_active", True)
        tag = "" if active in (None, True) else " (inactive)"
        label = f"{mid} — {name}{tag}"
        labels.append(label)
        label_to_legacy[label] = mid
        label_to_name[label] = name

    return labels, label_to_legacy, label_to_name, df


def sum_contribution_pot(c) -> float:
    sid = current_session_id(c)
    if not sid:
        return 0.0
    resp = (
        c.table("contributions_legacy")
        .select("amount,kind")
        .eq("session_id", sid)
        .in_("kind", ALLOWED_CONTRIB_KINDS)
        .limit(20000)
        .execute()
    )
    return sum(float(r.get("amount") or 0) for r in (resp.data or []))


def sum_total_contributions_alltime(c) -> float:
    resp = c.table("contributions_legacy").select("amount").limit(20000).execute()
    return sum(float(r.get("amount") or 0) for r in (resp.data or []))


def foundation_totals(c):
    resp = c.table("foundation_payments_legacy").select("amount_paid,amount_pending").limit(20000).execute()
    paid = sum(float(r.get("amount_paid") or 0) for r in (resp.data or []))
    pending = sum(float(r.get("amount_pending") or 0) for r in (resp.data or []))
    return paid, pending, paid + pending


def loans_portfolio_totals(c):
    resp = c.table("loans_legacy").select("status,total_due,balance,total_interest_generated").limit(20000).execute()
    active_count = 0
    sum_due = 0.0
    sum_principal = 0.0
    sum_all_interest = 0.0
    for r in (resp.data or []):
        sum_all_interest += float(r.get("total_interest_generated") or 0)
        if str(r.get("status") or "").lower().strip() == "active":
            active_count += 1
            sum_due += float(r.get("total_due") or 0)
            sum_principal += float(r.get("balance") or 0)
    return active_count, sum_due, sum_principal, sum_all_interest


def fines_totals(c):
    resp = c.table("fines_legacy").select("amount,status").limit(20000).execute()
    total = 0.0
    unpaid = 0.0
    for r in (resp.data or []):
        amt = float(r.get("amount") or 0)
        total += amt
        stt = str(r.get("status") or "").lower().strip()
        if stt not in ("paid", "cleared", "settled"):
            unpaid += amt
    return total, unpaid


def fetch_paid_out_member_ids(c) -> set[int]:
    try:
        resp = c.table("payouts_legacy").select("member_id").limit(20000).execute()
        return set(int(r["member_id"]) for r in (resp.data or []) if r.get("member_id") is not None)
    except Exception:
        return set()


def next_unpaid_beneficiary(active_member_ids: list[int], already_paid: set[int], start_idx: int) -> int:
    if not active_member_ids:
        return start_idx
    if start_idx not in active_member_ids:
        start_idx = active_member_ids[0]
    n = len(active_member_ids)
    start_pos = active_member_ids.index(start_idx)
    for offset in range(n):
        cand = active_member_ids[(start_pos + offset) % n]
        if cand not in already_paid:
            return cand
    return start_idx
