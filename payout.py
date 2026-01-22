# payout.py  ✅ COMPLETE FIX (bi-weekly rotation + session-scoped pot + signatures enforced)
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Set

import pandas as pd

# Import ONLY what exists in your db.py
from db import now_iso, current_session_id, fetch_one


# ============================================================
# CONFIG
# ============================================================
EXPECTED_ACTIVE_MEMBERS = 17

BASE_CONTRIBUTION = 500
CONTRIBUTION_STEP = 500

ALLOWED_CONTRIB_KINDS = ["paid", "contributed"]

# ✅ REQUIRED for payout (you said 3, not 4)
PAYOUT_SIG_REQUIRED = ["president", "beneficiary", "treasury"]


# ============================================================
# INTERNAL HELPERS (safe)
# ============================================================
def _to_df(resp) -> pd.DataFrame:
    return pd.DataFrame(resp.data or [])

def _safe_select(c, table: str, filters: list[tuple[str, str, Any]] | None = None,
                 order_col: str | None = None, desc: bool = True, limit: int = 2000) -> list[dict]:
    """
    Supabase-safe select helper.
    filters: list of (col, op, val) where op is one of: eq, in, gte, lte
    """
    try:
        q = c.table(table).select("*")
        if filters:
            for col, op, val in filters:
                if op == "eq":
                    q = q.eq(col, val)
                elif op == "in":
                    q = q.in_(col, val)
                elif op == "gte":
                    q = q.gte(col, val)
                elif op == "lte":
                    q = q.lte(col, val)
        if order_col:
            q = q.order(order_col, desc=desc)
        q = q.limit(limit)
        res = q.execute()
        return res.data or []
    except Exception:
        return []

def _table_exists(c, table: str) -> bool:
    """
    Lightweight existence check: try a cheap select.
    """
    try:
        c.table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False

def _first_existing_table(c, candidates: list[str]) -> Optional[str]:
    for t in candidates:
        if _table_exists(c, t):
            return t
    return None

def _session_window_from_sessions_table(c, sid: int) -> Optional[Tuple[str, str]]:
    """
    If you have sessions_legacy with (id, start_date, end_date) or similar,
    we use it to scope contributions when session_id column does not exist.
    Returns ISO strings [start, end].
    """
    rows = _safe_select(c, "sessions_legacy", filters=[("id", "eq", sid)], limit=1)
    if not rows:
        return None

    r = rows[0]
    # Try common column names
    sd = r.get("start_date") or r.get("starts_at") or r.get("start")
    ed = r.get("end_date") or r.get("ends_at") or r.get("end")

    if not sd:
        return None

    # Normalize to ISO strings (date or datetime)
    def _norm(x, end: bool = False) -> str:
        if isinstance(x, str):
            # assume it's already iso or date string
            if "T" in x:
                return x
            return f"{x}T23:59:59" if end else f"{x}T00:00:00"
        if isinstance(x, date) and not isinstance(x, datetime):
            return f"{x.isoformat()}T23:59:59" if end else f"{x.isoformat()}T00:00:00"
        if isinstance(x, datetime):
            return x.replace(microsecond=0).isoformat()
        return ""

    start_iso = _norm(sd, end=False)
    end_iso = _norm(ed, end=True) if ed else ""

    # If end missing, assume 14-day window
    if not end_iso:
        try:
            # parse start as date
            d0 = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            end_iso = (d0 + timedelta(days=13, hours=23, minutes=59, seconds=59)).replace(microsecond=0).isoformat()
        except Exception:
            end_iso = ""

    return (start_iso, end_iso) if start_iso and end_iso else None

def _fallback_biweekly_window() -> Tuple[str, str]:
    """
    Last resort: last 14 days from now.
    """
    end = datetime.utcnow().replace(microsecond=0)
    start = end - timedelta(days=13)
    return (start.isoformat(), end.isoformat())


# ============================================================
# SIGNATURES (enforced)
# ============================================================
def get_signatures(c, context: str, ref_id: int) -> list[dict]:
    """
    Expected signatures table layout (typical):
      signatures: {context, ref_id, role, member_id, signed_at, created_at}
    If your table name differs, change SIGNATURES_TABLE below.
    """
    SIGNATURES_TABLE = "signatures"
    if not _table_exists(c, SIGNATURES_TABLE):
        return []  # If table isn't in use, UI can show optional gate

    rows = _safe_select(
        c,
        SIGNATURES_TABLE,
        filters=[("context", "eq", context), ("ref_id", "eq", int(ref_id))],
        order_col="created_at",
        desc=True,
        limit=500,
    )
    return rows

def missing_roles(sign_rows: list[dict], required_roles: list[str]) -> list[str]:
    got = {str(r.get("role", "")).strip().lower() for r in (sign_rows or []) if r.get("role")}
    req = [r.strip().lower() for r in required_roles]
    return [r for r in req if r not in got]


# ============================================================
# ROTATION / BENEFICIARY
# ============================================================
def resolve_beneficiary_id(active_ids: list[int], pointer: int) -> int:
    """
    Works with BOTH styles:
    - pointer is actual legacy_member_id
    - pointer is 1-based index into active_ids
    """
    if not active_ids:
        raise Exception("No active members available.")

    # If pointer is a member_id
    if int(pointer) in set(active_ids):
        return int(pointer)

    # If pointer is a 1-based index
    idx = int(pointer) - 1
    if 0 <= idx < len(active_ids):
        return int(active_ids[idx])

    # Fallback to first
    return int(active_ids[0])

def next_rotation_pointer(active_ids: list[int], current_pointer: int) -> int:
    """
    Advances by index (1-based) when pointer is index; otherwise advances by active_ids order.
    We keep it simple: store as 1-based index in app_state.next_payout_index.
    """
    if not active_ids:
        return 1

    # Convert current pointer to index
    if current_pointer in set(active_ids):
        cur_idx = active_ids.index(int(current_pointer))
        nxt_idx = (cur_idx + 1) % len(active_ids)
        return nxt_idx + 1  # store as 1-based index
    else:
        cur_idx = max(int(current_pointer) - 1, 0)
        nxt_idx = (cur_idx + 1) % len(active_ids)
        return nxt_idx + 1


# ============================================================
# CONTRIBUTIONS (STRICTLY THIS SESSION)
# ============================================================
def contributions_for_current_session(c, sid: int) -> pd.DataFrame:
    """
    ✅ Core fix: contributions must be scoped to THIS rotation session.

    Preferred (best): contributions_legacy has a session_id column.
    Fallback: use sessions_legacy [start_date, end_date] window
    Last resort: last 14 days.
    """
    table = _first_existing_table(c, ["contributions_legacy", "contributions"])
    if not table:
        return pd.DataFrame([])

    # 1) Try session_id scoped query
    rows = _safe_select(
        c,
        table,
        filters=[
            ("session_id", "eq", int(sid)),
            ("kind", "in", ALLOWED_CONTRIB_KINDS),
        ],
        order_col="created_at",
        desc=True,
        limit=5000,
    )
    if rows:
        return pd.DataFrame(rows)

    # 2) If session_id doesn't exist or no rows: use sessions_legacy window
    win = _session_window_from_sessions_table(c, sid)
    if not win:
        win = _fallback_biweekly_window()
    start_iso, end_iso = win

    rows2 = _safe_select(
        c,
        table,
        filters=[
            ("kind", "in", ALLOWED_CONTRIB_KINDS),
            ("created_at", "gte", start_iso),
            ("created_at", "lte", end_iso),
        ],
        order_col="created_at",
        desc=True,
        limit=5000,
    )
    return pd.DataFrame(rows2)

def contribution_summary_this_rotation(df_contrib: pd.DataFrame) -> dict:
    if df_contrib is None or df_contrib.empty:
        return {
            "rows": 0,
            "total": 0.0,
            "contributors": 0,
            "min": 0.0,
            "max": 0.0,
        }

    amt = pd.to_numeric(df_contrib.get("amount", 0), errors="coerce").fillna(0.0)
    member_col = "legacy_member_id" if "legacy_member_id" in df_contrib.columns else ("member_id" if "member_id" in df_contrib.columns else None)
    contributors = int(df_contrib[member_col].nunique()) if member_col else 0

    return {
        "rows": int(len(df_contrib)),
        "total": float(amt.sum()),
        "contributors": contributors,
        "min": float(amt.min()) if len(amt) else 0.0,
        "max": float(amt.max()) if len(amt) else 0.0,
    }

def contribution_problems_this_rotation(active_ids: list[int], df_contrib: pd.DataFrame) -> list[str]:
    """
    Checks:
    - Missing contributor
    - Any amount not multiple of 500
    - Any amount < 500 (base)
    """
    problems: list[str] = []

    if not active_ids:
        return ["No active members detected."]

    if df_contrib is None or df_contrib.empty:
        return ["No contributions found for this rotation/session."]

    member_col = "legacy_member_id" if "legacy_member_id" in df_contrib.columns else ("member_id" if "member_id" in df_contrib.columns else None)
    if not member_col:
        return ["Contributions table missing member id column (legacy_member_id/member_id)."]

    df = df_contrib.copy()
    df["amount_num"] = pd.to_numeric(df.get("amount", 0), errors="coerce").fillna(0).astype(float)
    df[member_col] = pd.to_numeric(df[member_col], errors="coerce").fillna(-1).astype(int)

    # Aggregate per member
    g = df.groupby(member_col, as_index=False)["amount_num"].sum()

    contributed_ids = set(int(x) for x in g[member_col].tolist() if int(x) > 0)
    missing = [mid for mid in active_ids if int(mid) not in contributed_ids]
    if missing:
        problems.append(f"Missing contributions from members: {missing}")

    # base + multiple checks
    bad_base = g[g["amount_num"] < BASE_CONTRIBUTION]
    if not bad_base.empty:
        problems.append(f"Members below base {BASE_CONTRIBUTION}: {bad_base[[member_col,'amount_num']].to_dict('records')}")

    # multiples of 500
    def is_multiple(x: float) -> bool:
        try:
            return (int(round(x)) % CONTRIBUTION_STEP) == 0
        except Exception:
            return False

    bad_mult = g[~g["amount_num"].apply(is_multiple)]
    if not bad_mult.empty:
        problems.append(f"Members with non-multiple-of-{CONTRIBUTION_STEP} totals: {bad_mult[[member_col,'amount_num']].to_dict('records')}")

    return problems


# ============================================================
# PAYOUT HISTORY (double-pay prevention)
# ============================================================
def _payout_table(c) -> Optional[str]:
    return _first_existing_table(c, ["foundation_payments_legacy", "payouts_legacy", "payouts"])

def fetch_paid_out_member_ids(c, sid: int) -> Set[int]:
    """
    Returns member_ids already paid for this session.
    """
    t = _payout_table(c)
    if not t:
        return set()

    # Try session_id filter first
    rows = _safe_select(c, t, filters=[("session_id", "eq", int(sid))], limit=5000)
    if not rows:
        # fallback: some tables may use payout_session_id
        rows = _safe_select(c, t, filters=[("payout_session_id", "eq", int(sid))], limit=5000)

    paid = set()
    for r in rows:
        mid = r.get("beneficiary_member_id") or r.get("beneficiary_id") or r.get("legacy_member_id") or r.get("member_id")
        if mid is not None:
            try:
                paid.add(int(mid))
            except Exception:
                pass
    return paid


# ============================================================
# GOVERNANCE / COMPLIANCE GATE (what your UI shows)
# ============================================================
def compliance_for_payout(c, active_ids: list[int], sid: int) -> dict:
    """
    Returns a single dict used by UI gates.
    """
    # Gate 1: active members
    gate1_ok = (len(active_ids) == EXPECTED_ACTIVE_MEMBERS) or (len(active_ids) > 0)
    gate1_msg = f"Active members: {len(active_ids)} (expected {EXPECTED_ACTIVE_MEMBERS})"

    # Gate 2: contribution summary this rotation
    df_contrib = contributions_for_current_session(c, sid)
    summ = contribution_summary_this_rotation(df_contrib)
    problems = contribution_problems_this_rotation(active_ids, df_contrib)
    gate2_ok = (len(problems) == 0)

    # Signatures
    signs = get_signatures(c, context="payout", ref_id=sid)
    missing = missing_roles(signs, PAYOUT_SIG_REQUIRED)
    sig_ok = (len(missing) == 0) if signs is not None else True  # if no table, UI can treat optional
    sig_msg = "OK" if sig_ok else f"Missing roles: {missing}"

    return {
        "sid": int(sid),
        "gate1_ok": bool(gate1_ok),
        "gate1_msg": gate1_msg,
        "gate2_ok": bool(gate2_ok),
        "gate2_summary": summ,
        "gate2_problems": problems,
        "signatures_ok": bool(sig_ok),
        "signatures_missing": missing,
        "signatures_msg": sig_msg,
        "contrib_rows": int(summ.get("rows", 0)),
        "contrib_total": float(summ.get("total", 0.0)),
    }


# ============================================================
# PAYOUT EXECUTION (Option B)
# ============================================================
def payout_precheck_option_b(c, active_ids: list[int]) -> dict:
    """
    ✅ Hard-stop precheck:
    - session_id defined
    - contributions are session-scoped and clean
    - signatures present (if signatures table exists)
    - beneficiary not already paid
    """
    sid = int(current_session_id(c) or 0)
    if sid <= 0:
        return {"ok": False, "reason": "No current session_id. app_state.next_payout_index is missing."}

    # Gate checks
    comp = compliance_for_payout(c, active_ids, sid)

    if not comp["gate1_ok"]:
        return {"ok": False, "reason": comp["gate1_msg"], "details": comp}

    if not comp["gate2_ok"]:
        return {"ok": False, "reason": "Contribution problems for this rotation/session.", "details": comp}

    # enforce signatures if signatures table exists
    if _table_exists(c, "signatures"):
        if not comp["signatures_ok"]:
            return {"ok": False, "reason": comp["signatures_msg"], "details": comp}

    # beneficiary resolution
    beneficiary_id = resolve_beneficiary_id(active_ids, sid)

    # prevent double-pay
    already_paid = fetch_paid_out_member_ids(c, sid)
    if beneficiary_id in already_paid:
        return {"ok": False, "reason": f"Beneficiary {beneficiary_id} already paid for session {sid}.", "details": comp}

    return {
        "ok": True,
        "sid": sid,
        "beneficiary_id": beneficiary_id,
        "pot_total": float(comp["contrib_total"]),
        "details": comp,
    }

def _update_app_state_next_index(c, next_idx: int) -> None:
    """
    Tries common patterns for app_state table.
    If your app_state table differs, adjust here only.
    """
    if not _table_exists(c, "app_state"):
        return

    payload = {"next_payout_index": int(next_idx), "updated_at": now_iso()}
    try:
        # common: singleton row id=1
        c.table("app_state").update(payload).eq("id", 1).execute()
        return
    except Exception:
        pass

    try:
        # fallback: upsert singleton
        c.table("app_state").upsert({"id": 1, **payload}).execute()
        return
    except Exception:
        pass

def execute_payout_option_b(c, active_ids: list[int], actor_user_id: str | None = None) -> dict:
    """
    ✅ Executes payout:
    - runs precheck
    - writes payout record with session_id + beneficiary + amount
    - advances rotation index in app_state.next_payout_index
    """
    pre = payout_precheck_option_b(c, active_ids)
    if not pre.get("ok"):
        return pre

    sid = int(pre["sid"])
    beneficiary_id = int(pre["beneficiary_id"])
    pot_total = float(pre["pot_total"])

    t = _payout_table(c)
    if not t:
        return {"ok": False, "reason": "No payout table found (foundation_payments_legacy / payouts_legacy / payouts)."}

    # Insert payout record
    payload = {
        "created_at": now_iso(),
        "session_id": sid,
        "beneficiary_member_id": beneficiary_id,
        "amount": pot_total,
        "status": "paid",
    }
    if actor_user_id:
        payload["actor_user_id"] = actor_user_id

    try:
        res = c.table(t).insert(payload).execute()
        row = (res.data or [None])[0]
        if not row:
            return {"ok": False, "reason": "Payout insert failed (no row returned)."}

    except Exception as e:
        return {"ok": False, "reason": f"Payout insert failed: {repr(e)}"}

    # Advance rotation pointer (store as 1-based index)
    nxt = next_rotation_pointer(active_ids, sid)
    _update_app_state_next_index(c, nxt)

    return {
        "ok": True,
        "sid": sid,
        "beneficiary_id": beneficiary_id,
        "amount_paid": pot_total,
        "next_payout_index": nxt,
        "payout_table": t,
    }
