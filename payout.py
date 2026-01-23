# payout.py ✅ UPDATED (NO VIEW — KPIs computed in Python)
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, Tuple, Set

import pandas as pd
import streamlit as st

# Import ONLY what exists in your db.py
from db import current_session_id


# ============================================================
# TIME
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

# ✅ REQUIRED payout signatures (3 roles)
PAYOUT_SIG_REQUIRED = ["president", "beneficiary", "treasury"]


# ============================================================
# INTERNAL HELPERS
# ============================================================
def _safe_select(
    c,
    table: str,
    filters: list[tuple[str, str, Any]] | None = None,
    order_col: str | None = None,
    desc: bool = True,
    limit: int = 2000,
) -> list[dict]:
    """Supabase-safe select helper."""
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


def _session_window_from_sessions_table(c, session_id: int) -> Optional[Tuple[str, str]]:
    """
    If sessions_legacy exists and has (id, start_date, end_date),
    return ISO strings [start, end] for that session.
    """
    rows = _safe_select(c, "sessions_legacy", filters=[("id", "eq", int(session_id))], limit=1)
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


def _fallback_biweekly_window_from_app_state(c) -> Tuple[str, str, Optional[str]]:
    """
    Fallback window using app_state.next_payout_date:
    end = next_payout_date 23:59:59
    start = end - 13 days 00:00:00
    Returns (start_iso, end_iso, next_payout_date_str_or_None)
    """
    npd_str = None
    try:
        rows = _safe_select(c, "app_state", limit=1)
        if rows:
            npd = rows[0].get("next_payout_date")
            if npd:
                npd_str = str(npd)
                # if date-like
                if "T" in npd_str:
                    # keep date part only
                    npd_str = npd_str.split("T")[0]
    except Exception:
        pass

    # if we have a date, build an end-of-day window
    if npd_str:
        try:
            end_dt = datetime.fromisoformat(npd_str).replace(hour=23, minute=59, second=59, microsecond=0)
            start_dt = (end_dt - timedelta(days=13)).replace(hour=0, minute=0, second=0, microsecond=0)
            return start_dt.isoformat(), end_dt.isoformat(), npd_str
        except Exception:
            pass

    # last resort: last 14 days from now
    end_dt = datetime.utcnow().replace(microsecond=0)
    start_dt = end_dt - timedelta(days=13)
    return start_dt.isoformat(), end_dt.isoformat(), None


# ============================================================
# ✅ SEPARATE IDs
#   - session_id: bi-weekly cycle key (for contributions/payout history)
#   - rotation_pointer: next beneficiary pointer
# ============================================================
def get_session_id(c) -> int:
    """Bi-weekly session id. Prefer db.current_session_id(c)."""
    try:
        raw = current_session_id(c)
        if raw is not None and str(raw).strip().isdigit():
            return int(raw)
    except Exception:
        pass

    try:
        rows = _safe_select(c, "app_state", limit=1)
        if rows:
            v = rows[0].get("current_session_id")
            if v is not None and str(v).strip().isdigit():
                return int(v)
    except Exception:
        pass

    return 0


def get_rotation_pointer(c) -> int:
    """Rotation pointer stored in app_state.next_payout_index."""
    try:
        rows = _safe_select(c, "app_state", limit=1)
        if rows:
            v = rows[0].get("next_payout_index")
            if v is not None and str(v).strip() != "":
                try:
                    x = int(v)
                    return x if x > 0 else 1
                except Exception:
                    pass
    except Exception:
        pass

    return 1


# ============================================================
# SIGNATURES
# ============================================================
def get_signatures(c, context: str, ref_id: int) -> list[dict]:
    if not _table_exists(c, "signatures"):
        return []
    return _safe_select(
        c,
        "signatures",
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
    Supports both:
    - pointer equals actual legacy_member_id
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
    """Advance by 1 in active_ids order; store pointer as 1-based index."""
    if not active_ids:
        return 1

    if current_pointer in set(active_ids):
        cur_idx = active_ids.index(int(current_pointer))
        nxt_idx = (cur_idx + 1) % len(active_ids)
        return nxt_idx + 1

    cur_idx = max(int(current_pointer) - 1, 0)
    nxt_idx = (cur_idx + 1) % len(active_ids)
    return nxt_idx + 1


# ============================================================
# CONTRIBUTIONS (session_id first, else bi-weekly window)
# ============================================================
def contributions_for_session(c, session_id: int) -> tuple[pd.DataFrame, dict]:
    """
    Returns: (df_contrib, meta)
    meta includes window start/end if fallback is used.
    """
    table = _first_existing_table(c, ["contributions_legacy", "contributions"])
    if not table:
        return pd.DataFrame([]), {"source": None}

    # 1) session_id scoped
    rows = _safe_select(
        c,
        table,
        filters=[("session_id", "eq", int(session_id)), ("kind", "in", ALLOWED_CONTRIB_KINDS)],
        order_col="created_at",
        desc=True,
        limit=8000,
    )
    if rows:
        return pd.DataFrame(rows), {"source": "session_id", "table": table}

    # 2) sessions_legacy window scoped
    win = _session_window_from_sessions_table(c, session_id)
    if win:
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
            limit=8000,
        )
        return pd.DataFrame(rows2), {"source": "sessions_legacy_window", "table": table, "start": start_iso, "end": end_iso}

    # 3) app_state fallback window
    start_iso, end_iso, npd = _fallback_biweekly_window_from_app_state(c)
    rows3 = _safe_select(
        c,
        table,
        filters=[
            ("kind", "in", ALLOWED_CONTRIB_KINDS),
            ("created_at", "gte", start_iso),
            ("created_at", "lte", end_iso),
        ],
        order_col="created_at",
        desc=True,
        limit=8000,
    )
    return pd.DataFrame(rows3), {"source": "app_state_window", "table": table, "start": start_iso, "end": end_iso, "next_payout_date": npd}


def contribution_summary(df_contrib: pd.DataFrame) -> dict:
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


def contribution_problems(active_ids: list[int], df_contrib: pd.DataFrame) -> list[str]:
    problems: list[str] = []

    if not active_ids:
        return ["No active members detected."]

    if df_contrib is None or df_contrib.empty:
        return ["No contributions found for this bi-weekly session."]

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
# PAYOUT HISTORY (✅ DO NOT USE foundation_payments_legacy)
# ============================================================
def _payout_table(c) -> Optional[str]:
    # ✅ prefer real payout tables first
    return _first_existing_table(c, ["payouts_legacy", "payouts"])


def fetch_paid_out_member_ids(c, session_id: int) -> Set[int]:
    t = _payout_table(c)
    if not t:
        return set()

    # try session_id columns
    rows = _safe_select(c, t, filters=[("session_id", "eq", int(session_id))], limit=8000)
    if not rows:
        rows = _safe_select(c, t, filters=[("payout_session_id", "eq", int(session_id))], limit=8000)

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


def _insert_payout_row(c, table: str, session_id: int, beneficiary_id: int, amount: float, actor_user_id: str | None):
    """
    Insert into payouts_legacy/payouts with best-effort payload variants.
    This avoids breaking if your payout table uses different column names.
    """
    base = {"created_at": now_iso(), "status": "paid"}
    if actor_user_id:
        base["actor_user_id"] = actor_user_id

    payloads = [
        # common modern
        {**base, "session_id": session_id, "beneficiary_member_id": beneficiary_id, "amount": amount},
        # legacy-ish
        {**base, "session_id": session_id, "legacy_member_id": beneficiary_id, "amount": amount},
        # some schemas
        {**base, "payout_session_id": session_id, "beneficiary_id": beneficiary_id, "amount": amount},
        # fallback: member_id
        {**base, "session_id": session_id, "member_id": beneficiary_id, "amount": amount},
    ]

    last_err = None
    for p in payloads:
        try:
            res = c.table(table).insert(p).execute()
            row = (res.data or [None])[0]
            if row:
                return row
        except Exception as e:
            last_err = e
            continue

    raise Exception(f"Insert failed for {table}: {repr(last_err)}")


# ============================================================
# GOVERNANCE / COMPLIANCE
# ============================================================
def compliance_for_payout(c, active_ids: list[int], session_id: int, rotation_pointer: int) -> dict:
    gate1_ok = (len(active_ids) == EXPECTED_ACTIVE_MEMBERS) or (len(active_ids) > 0)
    gate1_msg = f"Active members: {len(active_ids)} (expected {EXPECTED_ACTIVE_MEMBERS})"

    df_contrib, meta = contributions_for_session(c, session_id)
    summ = contribution_summary(df_contrib)
    problems = contribution_problems(active_ids, df_contrib)
    gate2_ok = (len(problems) == 0)

    # ✅ signatures tied to BI-WEEKLY SESSION
    signs = get_signatures(c, context="payout", ref_id=int(session_id)) if session_id else []
    missing = missing_roles(signs, PAYOUT_SIG_REQUIRED) if signs is not None else []
    sig_ok = (len(missing) == 0) if _table_exists(c, "signatures") else True
    sig_msg = "OK" if sig_ok else f"Missing roles: {missing}"

    beneficiary_id = resolve_beneficiary_id(active_ids, rotation_pointer) if rotation_pointer else 0

    return {
        "session_id": int(session_id),
        "rotation_pointer": int(rotation_pointer),
        "beneficiary_id": int(beneficiary_id) if beneficiary_id else 0,

        "gate1_ok": bool(gate1_ok),
        "gate1_msg": gate1_msg,

        "gate2_ok": bool(gate2_ok),
        "gate2_summary": summ,
        "gate2_problems": problems,
        "contrib_meta": meta,

        "signatures_ok": bool(sig_ok),
        "signatures_missing": missing,
        "signatures_msg": sig_msg,

        "contrib_rows": int(summ.get("rows", 0)),
        "contrib_total": float(summ.get("total", 0.0)),
    }


# ============================================================
# PRECHECK + EXECUTE
# ============================================================
def payout_precheck_option_b(c, active_ids: list[int]) -> dict:
    session_id = get_session_id(c)
    if session_id <= 0:
        return {"ok": False, "reason": "No numeric session_id found. Ensure current_session_id is an integer."}

    rotation_pointer = get_rotation_pointer(c)
    if rotation_pointer <= 0:
        rotation_pointer = 1

    comp = compliance_for_payout(c, active_ids, session_id=session_id, rotation_pointer=rotation_pointer)

    if not comp["gate1_ok"]:
        return {"ok": False, "reason": comp["gate1_msg"], "details": comp}

    if not comp["gate2_ok"]:
        return {"ok": False, "reason": "Contribution problems for this bi-weekly session.", "details": comp}

    if _table_exists(c, "signatures") and not comp["signatures_ok"]:
        return {"ok": False, "reason": comp["signatures_msg"], "details": comp}

    beneficiary_id = int(comp["beneficiary_id"]) if comp.get("beneficiary_id") else 0
    if beneficiary_id <= 0:
        return {"ok": False, "reason": "Could not resolve beneficiary from rotation pointer.", "details": comp}

    already_paid = fetch_paid_out_member_ids(c, session_id)
    if beneficiary_id in already_paid:
        return {
            "ok": False,
            "reason": f"Beneficiary {beneficiary_id} already paid for bi-weekly session {session_id}.",
            "details": comp,
        }

    return {
        "ok": True,
        "session_id": session_id,
        "rotation_pointer": rotation_pointer,
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

    session_id = int(pre["session_id"])
    rotation_pointer = int(pre["rotation_pointer"])
    beneficiary_id = int(pre["beneficiary_id"])
    pot_total = float(pre["pot_total"])

    t = _payout_table(c)
    if not t:
        return {"ok": False, "reason": "No payout table found (payouts_legacy / payouts)."}

    try:
        row = _insert_payout_row(c, t, session_id, beneficiary_id, pot_total, actor_user_id)
    except Exception as e:
        return {"ok": False, "reason": str(e)}

    # advance rotation pointer (NOT session id)
    nxt = next_rotation_pointer(active_ids, rotation_pointer)
    _update_app_state_next_index(c, nxt)

    return {
        "ok": True,
        "session_id": session_id,
        "rotation_pointer": rotation_pointer,
        "beneficiary_id": beneficiary_id,
        "amount_paid": pot_total,
        "next_payout_index": nxt,
        "payout_table": t,
        "payout_row": row,
    }


# ============================================================
# UI HELPERS
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


def _member_name_by_id(df_members: pd.DataFrame, mid: int) -> str:
    try:
        row = df_members.loc[df_members["id"] == int(mid)]
        if not row.empty:
            return str(row.iloc[0].get("name") or "").strip()
    except Exception:
        pass
    return ""


def compute_cycle_kpi_row(
    session_id: int,
    active_ids: list[int],
    beneficiary_id: int,
    beneficiary_name: str,
    next_payout_date: str | None,
    comp: dict,
) -> pd.DataFrame:
    """
    Builds the exact KPI table row in Python (no SQL view).
    """
    summ = (comp.get("gate2_summary") or {}) if isinstance(comp, dict) else {}
    meta = (comp.get("contrib_meta") or {}) if isinstance(comp, dict) else {}

    contributors = int(summ.get("contributors", 0))
    pot_total = float(comp.get("contrib_total", 0.0))
    rows_count = int(summ.get("rows", 0))
    missing = max(len(active_ids) - contributors, 0)

    already_paid = False  # filled by caller if needed

    df = pd.DataFrame([{
        "session_number": session_id,
        "pot_total": pot_total,
        "rows_count": rows_count,
        "contributors": contributors,
        "missing_contributors": missing,
        "beneficiary_id": beneficiary_id,
        "beneficiary_name": beneficiary_name,
        "next_payout_date": next_payout_date or meta.get("next_payout_date") or "—",
        "already_paid": already_paid,
        "contrib_source": meta.get("source", "—"),
        "window_start": meta.get("start", "—"),
        "window_end": meta.get("end", "—"),
    }])
    return df


# ============================================================
# UI: PAYOUT PAGE
# ============================================================
def render_payouts(sb_service, schema: str):
    st.title("Payouts • Option B (Bi-weekly Rotation)")
    st.caption("✅ No SQL views • ✅ Session-scoped pot • ✅ Signatures enforced • ✅ Double-pay protection • ✅ Rotation advance")

    members = _safe_select_schema(
        sb_service, schema, "members_legacy", "id,name,position", limit=2000, order_col="id", desc=False
    )
    dfm = pd.DataFrame(members or [])
    if dfm.empty:
        st.error("members_legacy is empty or not readable.")
        return

    dfm["id"] = pd.to_numeric(dfm["id"], errors="coerce").fillna(-1).astype(int)
    dfm["name"] = dfm.get("name", "").astype(str)

    active_ids = [int(x) for x in dfm["id"].tolist() if int(x) > 0]

    # app_state fields
    session_id = get_session_id(sb_service)
    rotation_pointer = get_rotation_pointer(sb_service)

    # beneficiary resolution (from pointer)
    beneficiary_id = resolve_beneficiary_id(active_ids, rotation_pointer) if rotation_pointer else 0
    beneficiary_name = _member_name_by_id(dfm, beneficiary_id)
    beneficiary_label = f"{beneficiary_id:02d} • {beneficiary_name}" if beneficiary_id else "—"

    # next payout date display (from app_state if present)
    next_payout_date = None
    try:
        arows = _safe_select(sb_service, "app_state", limit=1)
        if arows:
            npd = arows[0].get("next_payout_date")
            next_payout_date = str(npd) if npd else None
    except Exception:
        pass

    comp = compliance_for_payout(sb_service, active_ids, session_id=session_id, rotation_pointer=rotation_pointer)
    pre = payout_precheck_option_b(sb_service, active_ids)

    # already paid?
    already_paid_ids = fetch_paid_out_member_ids(sb_service, session_id) if session_id else set()
    already_paid = bool(beneficiary_id and beneficiary_id in already_paid_ids)

    # KPIs cards
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Active Members", str(len(active_ids)))
    c2.metric("Bi-weekly Session ID", str(session_id) if session_id else "—")
    c3.metric("Rotation Pointer", str(rotation_pointer) if rotation_pointer else "—")
    c4.metric("Current Beneficiary", beneficiary_label)
    c5.metric("Pot (this session)", f"{float(comp.get('contrib_total', 0.0)):,.0f}")

    st.divider()

    # ✅ Python KPI table (replaces current_season_view)
    st.subheader("KPIs — Current Cycle (Python)")
    kdf = compute_cycle_kpi_row(
        session_id=session_id,
        active_ids=active_ids,
        beneficiary_id=beneficiary_id,
        beneficiary_name=beneficiary_name,
        next_payout_date=next_payout_date,
        comp=comp,
    )
    kdf.loc[0, "already_paid"] = already_paid
    st.dataframe(kdf, use_container_width=True, hide_index=True)

    st.divider()

    # Gate status messages
    if comp.get("gate1_ok"):
        st.success(comp.get("gate1_msg", "Gate 1 OK"))
    else:
        st.error(comp.get("gate1_msg", "Gate 1 failed"))

    if comp.get("gate2_ok"):
        summ = comp.get("gate2_summary", {}) or {}
        st.success(
            f"Contributions OK • Contributors: {summ.get('contributors', 0)} • "
            f"Rows: {summ.get('rows', 0)} • Total: {float(comp.get('contrib_total', 0.0)):,.0f}"
        )
    else:
        st.error("Contribution problems detected for this bi-weekly session.")
        for p in (comp.get("gate2_problems", []) or []):
            st.warning(str(p))

    st.subheader("Signatures")
    if _table_exists(sb_service, "signatures"):
        missing = comp.get("signatures_missing", []) or []
        if not missing:
            st.success("All required payout signatures are present (for this bi-weekly session).")
        else:
            st.warning("Missing required signatures: " + ", ".join(missing))
            st.caption(f"Required roles: {PAYOUT_SIG_REQUIRED} • Context=payout • RefID=session_id ({session_id})")
    else:
        st.info("signatures table not found — signature enforcement skipped.")

    if already_paid:
        st.warning(f"Already paid: beneficiary {beneficiary_id} has a payout record for session {session_id}.")

    st.divider()

    disabled = not bool(pre.get("ok"))
    if st.button("✅ Execute Payout (Option B)", disabled=disabled, use_container_width=True):
        res = execute_payout_option_b(sb_service, active_ids, actor_user_id="admin")
        if res.get("ok"):
            st.success(
                f"Payout complete ✅  Session={res['session_id']}  Beneficiary={res['beneficiary_id']}  "
                f"Amount={float(res['amount_paid']):,.0f}  NextIndex={res.get('next_payout_index')}  "
                f"Table={res.get('payout_table')}"
            )
            st.rerun()
        else:
            st.error(res.get("reason", "Payout failed"))

    with st.expander("Debug details (optional)", expanded=False):
        st.write("Precheck JSON:")
        st.json(pre)
        st.write("Gate details JSON:")
        st.json(comp)
        st.write("Contributions sample (top 30):")
        try:
            dfc, meta = contributions_for_session(sb_service, session_id) if session_id else (pd.DataFrame([]), {})
            st.caption(f"Contribution source: {meta.get('source')}  table: {meta.get('table')}")
            if meta.get("start") and meta.get("end"):
                st.caption(f"Window: {meta.get('start')} → {meta.get('end')}")
            st.dataframe(dfc.head(30), use_container_width=True)
        except Exception as e:
            st.code(repr(e), language="text")
