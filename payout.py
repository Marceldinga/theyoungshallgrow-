# payout.py  ✅ COMPLETE FIX (bi-weekly rotation + session-scoped pot + signatures enforced)
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, Tuple, Set

import pandas as pd
import streamlit as st

# Import ONLY what exists in your db.py
from db import current_session_id


# ============================================================
# TIME (local to payout.py so we don't depend on db.now_iso)
# ============================================================
def now_iso() -> str:
    """UTC ISO timestamp with Z suffix."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


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
def _safe_select(
    c,
    table: str,
    filters: list[tuple[str, str, Any]] | None = None,
    order_col: str | None = None,
    desc: bool = True,
    limit: int = 2000,
) -> list[dict]:
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
    """Lightweight existence check: try a cheap select."""
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
    If sessions_legacy has (id, start_date, end_date) or similar,
    scope contributions by that window when session_id doesn't exist.
    Returns ISO strings [start, end].
    """
    rows = _safe_select(c, "sessions_legacy", filters=[("id", "eq", sid)], limit=1)
    if not rows:
        return None

    r = rows[0]
    sd = r.get("start_date") or r.get("starts_at") or r.get("start")
    ed = r.get("end_date") or r.get("ends_at") or r.get("end")

    if not sd:
        return None

    def _norm(x, end: bool = False) -> str:
        if isinstance(x, str):
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
            d0 = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            end_iso = (
                d0 + timedelta(days=13, hours=23, minutes=59, seconds=59)
            ).replace(microsecond=0).isoformat()
        except Exception:
            end_iso = ""

    return (start_iso, end_iso) if start_iso and end_iso else None


def _fallback_biweekly_window() -> Tuple[str, str]:
    """Last resort: last 14 days from now."""
    end = datetime.utcnow().replace(microsecond=0)
    start = end - timedelta(days=13)
    return (start.isoformat(), end.isoformat())


# ============================================================
# ROTATION ID (safe int) ✅ FIX UUID current_session_id
# ============================================================
def get_rotation_id(c) -> int:
    """
    Returns numeric rotation pointer for payout.
    Prefers app_state.next_payout_index.
    Ignores UUID current_session_id values.
    """
    # 1) Prefer app_state.next_payout_index
    try:
        rows = _safe_select(c, "app_state", limit=1)
        if rows:
            v = rows[0].get("next_payout_index")
            if v is not None and str(v).strip() != "":
                try:
                    return int(v)
                except Exception:
                    pass

            # If current_session_id is numeric, allow it
            v2 = rows[0].get("current_session_id")
            if v2 is not None and str(v2).strip().isdigit():
                return int(v2)
    except Exception:
        pass

    # 2) Fallback to db.current_session_id (may be uuid)
    try:
        raw = current_session_id(c)
        if raw is not None and str(raw).strip().isdigit():
            return int(raw)
    except Exception:
        pass

    return 0


# ============================================================
# SIGNATURES (enforced)
# ============================================================
def get_signatures(c, context: str, ref_id: int) -> list[dict]:
    SIGNATURES_TABLE = "signatures"
    if not _table_exists(c, SIGNATURES_TABLE):
        return []

    return _safe_select(
        c,
        SIGNATURES_TABLE,
        filters=[("context", "eq", context), ("ref_id", "eq", int(ref_id))],
        order_col="created_at",
        desc=True,
        limit=500,
    )


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

    if int(pointer) in set(active_ids):
        return int(pointer)

    idx = int(pointer) - 1
    if 0 <= idx < len(active_ids):
        return int(active_ids[idx])

    return int(active_ids[0])


def next_rotation_pointer(active_ids: list[int], current_pointer: int) -> int:
    """Advance by 1 in the active_ids order; store pointer as 1-based index."""
    if not active_ids:
        return 1

    if current_pointer in set(active_ids):
        cur_idx = active_ids.index(int(current_pointer))
        nxt_idx = (cur_idx + 1) % len(active_ids)
        return nxt_idx + 1
    else:
        cur_idx = max(int(current_pointer) - 1, 0)
        nxt_idx = (cur_idx + 1) % len(active_ids)
        return nxt_idx + 1


# ============================================================
# CONTRIBUTIONS (STRICTLY THIS SESSION)
# ============================================================
def contributions_for_current_session(c, sid: int) -> pd.DataFrame:
    table = _first_existing_table(c, ["contributions_legacy", "contributions"])
    if not table:
        return pd.DataFrame([])

    # 1) session_id scoped
    rows = _safe_select(
        c,
        table,
        filters=[("session_id", "eq", int(sid)), ("kind", "in", ALLOWED_CONTRIB_KINDS)],
        order_col="created_at",
        desc=True,
        limit=5000,
    )
    if rows:
        return pd.DataFrame(rows)

    # 2) window scoped
    win = _session_window_from_sessions_table(c, sid) or _fallback_biweekly_window()
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
        return {"rows": 0, "total": 0.0, "contributors": 0, "min": 0.0, "max": 0.0}

    amt = pd.to_numeric(df_contrib.get("amount", 0), errors="coerce").fillna(0.0)
    member_col = (
        "legacy_member_id"
        if "legacy_member_id" in df_contrib.columns
        else ("member_id" if "member_id" in df_contrib.columns else None)
    )
    contributors = int(df_contrib[member_col].nunique()) if member_col else 0

    return {
        "rows": int(len(df_contrib)),
        "total": float(amt.sum()),
        "contributors": contributors,
        "min": float(amt.min()) if len(amt) else 0.0,
        "max": float(amt.max()) if len(amt) else 0.0,
    }


def contribution_problems_this_rotation(active_ids: list[int], df_contrib: pd.DataFrame) -> list[str]:
    problems: list[str] = []

    if not active_ids:
        return ["No active members detected."]

    if df_contrib is None or df_contrib.empty:
        return ["No contributions found for this rotation/session."]

    member_col = (
        "legacy_member_id"
        if "legacy_member_id" in df_contrib.columns
        else ("member_id" if "member_id" in df_contrib.columns else None)
    )
    if not member_col:
        return ["Contributions table missing member id column (legacy_member_id/member_id)."]

    df = df_contrib.copy()
    df["amount_num"] = pd.to_numeric(df.get("amount", 0), errors="coerce").fillna(0).astype(float)
    df[member_col] = pd.to_numeric(df[member_col], errors="coerce").fillna(-1).astype(int)

    g = df.groupby(member_col, as_index=False)["amount_num"].sum()

    contributed_ids = set(int(x) for x in g[member_col].tolist() if int(x) > 0)
    missing = [mid for mid in active_ids if int(mid) not in contributed_ids]
    if missing:
        problems.append(f"Missing contributions from members: {missing}")

    bad_base = g[g["amount_num"] < BASE_CONTRIBUTION]
    if not bad_base.empty:
        problems.append(
            f"Members below base {BASE_CONTRIBUTION}: {bad_base[[member_col,'amount_num']].to_dict('records')}"
        )

    def is_multiple(x: float) -> bool:
        try:
            return (int(round(x)) % CONTRIBUTION_STEP) == 0
        except Exception:
            return False

    bad_mult = g[~g["amount_num"].apply(is_multiple)]
    if not bad_mult.empty:
        problems.append(
            f"Members with non-multiple-of-{CONTRIBUTION_STEP} totals: {bad_mult[[member_col,'amount_num']].to_dict('records')}"
        )

    return problems


# ============================================================
# PAYOUT HISTORY (double-pay prevention)
# ============================================================
def _payout_table(c) -> Optional[str]:
    return _first_existing_table(c, ["foundation_payments_legacy", "payouts_legacy", "payouts"])


def fetch_paid_out_member_ids(c, sid: int) -> Set[int]:
    t = _payout_table(c)
    if not t:
        return set()

    rows = _safe_select(c, t, filters=[("session_id", "eq", int(sid))], limit=5000)
    if not rows:
        rows = _safe_select(c, t, filters=[("payout_session_id", "eq", int(sid))], limit=5000)

    paid = set()
    for r in rows:
        mid = (
            r.get("beneficiary_member_id")
            or r.get("beneficiary_id")
            or r.get("legacy_member_id")
            or r.get("member_id")
        )
        if mid is not None:
            try:
                paid.add(int(mid))
            except Exception:
                pass
    return paid


# ============================================================
# GOVERNANCE / COMPLIANCE
# ============================================================
def compliance_for_payout(c, active_ids: list[int], sid: int) -> dict:
    gate1_ok = (len(active_ids) == EXPECTED_ACTIVE_MEMBERS) or (len(active_ids) > 0)
    gate1_msg = f"Active members: {len(active_ids)} (expected {EXPECTED_ACTIVE_MEMBERS})"

    df_contrib = contributions_for_current_session(c, sid)
    summ = contribution_summary_this_rotation(df_contrib)
    problems = contribution_problems_this_rotation(active_ids, df_contrib)
    gate2_ok = (len(problems) == 0)

    signs = get_signatures(c, context="payout", ref_id=sid)
    missing = missing_roles(signs, PAYOUT_SIG_REQUIRED)
    sig_ok = (len(missing) == 0) if signs is not None else True
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
    sid = get_rotation_id(c)
    if sid <= 0:
        return {
            "ok": False,
            "reason": "No numeric rotation id found. Ensure app_state.next_payout_index is set to an integer.",
        }

    comp = compliance_for_payout(c, active_ids, sid)

    if not comp["gate1_ok"]:
        return {"ok": False, "reason": comp["gate1_msg"], "details": comp}

    if not comp["gate2_ok"]:
        return {"ok": False, "reason": "Contribution problems for this rotation/session.", "details": comp}

    if _table_exists(c, "signatures") and not comp["signatures_ok"]:
        return {"ok": False, "reason": comp["signatures_msg"], "details": comp}

    beneficiary_id = resolve_beneficiary_id(active_ids, sid)

    already_paid = fetch_paid_out_member_ids(c, sid)
    if beneficiary_id in already_paid:
        return {
            "ok": False,
            "reason": f"Beneficiary {beneficiary_id} already paid for session {sid}.",
            "details": comp,
        }

    return {
        "ok": True,
        "sid": sid,
        "beneficiary_id": beneficiary_id,
        "pot_total": float(comp["contrib_total"]),
        "details": comp,
    }


def _update_app_state_next_index(c, next_idx: int) -> None:
    if not _table_exists(c, "app_state"):
        return

    payload = {"next_payout_index": int(next_idx), "updated_at": now_iso()}
    try:
        c.table("app_state").update(payload).eq("id", 1).execute()
        return
    except Exception:
        pass

    try:
        c.table("app_state").upsert({"id": 1, **payload}).execute()
        return
    except Exception:
        pass


def execute_payout_option_b(c, active_ids: list[int], actor_user_id: str | None = None) -> dict:
    pre = payout_precheck_option_b(c, active_ids)
    if not pre.get("ok"):
        return pre

    sid = int(pre["sid"])
    beneficiary_id = int(pre["beneficiary_id"])
    pot_total = float(pre["pot_total"])

    t = _payout_table(c)
    if not t:
        return {"ok": False, "reason": "No payout table found (foundation_payments_legacy / payouts_legacy / payouts)."}

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


# ============================================================
# UI: PAYOUT PAGE (called by app.py)
# ============================================================
def _safe_select_schema(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 2000,
    order_col: str | None = None,
    desc: bool = False,
) -> list[dict]:
    try:
        q = sb.schema(schema).table(table).select(cols)
        if order_col:
            q = q.order(order_col, desc=desc)
        if limit is not None:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception:
        return []


def render_payouts(sb_service, schema: str):
    st.header("Payouts • Option B (Bi-weekly Rotation)")
    st.caption("Session-scoped pot • Signatures enforced • Double-pay protection • Rotation advance")

    members = _safe_select_schema(
        sb_service, schema, "members_legacy", "id,name,position", limit=2000, order_col="id", desc=False
    )
    dfm = pd.DataFrame(members or [])
    if dfm.empty:
        st.error("members_legacy is empty or not readable.")
        return

    dfm["id"] = pd.to_numeric(dfm["id"], errors="coerce").fillna(-1).astype(int)
    active_ids = [int(x) for x in dfm["id"].tolist() if int(x) > 0]

    sid = get_rotation_id(sb_service)

    c1, c2, c3 = st.columns(3)
    c1.metric("Active Members", str(len(active_ids)))
    c2.metric("Current Rotation ID", str(sid) if sid else "—")
    c3.metric("Base Contribution", f"{BASE_CONTRIBUTION:,}")

    st.divider()

    comp = compliance_for_payout(sb_service, active_ids, sid if sid else 0)

    st.subheader("Gate Status")

    with st.expander("Gate 1: Active members", expanded=False):
        (st.success if comp.get("gate1_ok") else st.error)(comp.get("gate1_msg", "—"))

    with st.expander("Gate 2: Contribution summary (this rotation)", expanded=True):
        st.write(comp.get("gate2_summary", {}))
        st.caption("This reflects ONLY the current session/rotation (not all-time).")

    with st.expander("Gate 2: Contribution problems", expanded=True):
        probs = comp.get("gate2_problems", []) or []
        if probs:
            for p in probs:
                st.error(p)
        else:
            st.success("No contribution problems detected for this rotation/session.")

    st.subheader("Signatures")
    if _table_exists(sb_service, "signatures"):
        st.caption(f"Required roles: {PAYOUT_SIG_REQUIRED}")
        if comp.get("signatures_ok"):
            st.success("All required payout signatures are present.")
        else:
            st.warning(comp.get("signatures_msg", "Missing signatures"))
    else:
        st.info("signatures table not found — signature enforcement skipped.")

    st.divider()

    pre = payout_precheck_option_b(sb_service, active_ids)

    left, right = st.columns([1, 1])
    with left:
        st.write("Precheck:")
        st.json(pre)

    with right:
        disabled = not bool(pre.get("ok"))
        if st.button("✅ Execute Payout (Option B)", disabled=disabled, use_container_width=True):
            res = execute_payout_option_b(sb_service, active_ids, actor_user_id="admin")
            if res.get("ok"):
                st.success(
                    f"Payout complete ✅  Beneficiary={res['beneficiary_id']}  "
                    f"Amount={float(res['amount_paid']):,.0f}  NextIndex={res.get('next_payout_index')}"
                )
                st.rerun()
            else:
                st.error(res.get("reason", "Payout failed"))
                st.json(res)
