
# payout.py ✅ COMPLETE SINGLE CODE (FINAL + WORKING + AUTO-ADVANCE + AUTO-REFRESH)
# ✅ signatures: id, entity_type, entity_id, role, signer_name, signer_member_id, signed_at
# ✅ payouts_legacy: id, member_id, member_name, payout_amount, payout_date, created_at, updated_at, payout_index
# ✅ payout allowed ON/AFTER app_state.next_payout_date
# ✅ prevents double-pay by checking payouts_legacy.payout_date within the cycle window
# ✅ after payout: PDF receipt with member contributions + totals + signatures
# ✅ FIX: next_payout_date now advances correctly to +14 days from the *scheduled payout day* of the paid cycle
# ✅ FIX: sessions_legacy lookup no longer assumes sessions_legacy.id is integer (your id is UUID)
# ✅ After payout -> clears Streamlit caches + st.rerun() so Dashboard updates automatically
# ✅ Keeps PDF available AFTER rerun via st.session_state

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Optional, Tuple, Set

import pandas as pd
import streamlit as st

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


def _parse_date_only(x: Any) -> Optional[date]:
    """Parse 'YYYY-MM-DD' or ISO datetime -> date."""
    if x is None:
        return None
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    if isinstance(x, datetime):
        return x.date()
    s = str(x).strip()
    if not s:
        return None
    if "T" in s:
        s = s.split("T")[0]
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


# ============================================================
# CONFIG
# ============================================================
EXPECTED_ACTIVE_MEMBERS = 17
BASE_CONTRIBUTION = 500
CONTRIBUTION_STEP = 500
ALLOWED_CONTRIB_KINDS = ["paid", "contributed"]

PAYOUT_SIG_REQUIRED = ["president", "beneficiary", "treasury"]
APP_STATE_PAYOUT_DATE_FIELD = "next_payout_date"

CYCLE_DAYS = 14  # ✅ bi-weekly


# ============================================================
# INTERNAL HELPERS (SUPABASE SAFE)
# ============================================================
def _safe_select(
    c,
    table: str,
    filters: list[tuple[str, str, Any]] | None = None,
    order_col: str | None = None,
    desc: bool = True,
    limit: int = 2000,
) -> list[dict]:
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
        return (q.execute().data or [])
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


# ============================================================
# SESSION WINDOW
# ============================================================
def _session_window_from_sessions_table(c, session_id: int) -> Optional[Tuple[str, str]]:
    """
    ✅ Your sessions_legacy.id is UUID, so we DO NOT query by id=int(session_id).
    We try common numeric columns:
      - session_number
      - session_id
    """
    if not _table_exists(c, "sessions_legacy"):
        return None

    # Try session_number first (most common)
    rows = _safe_select(c, "sessions_legacy", filters=[("session_number", "eq", int(session_id))], limit=1)
    if not rows:
        # Try session_id column (some schemas use this)
        rows = _safe_select(c, "sessions_legacy", filters=[("session_id", "eq", int(session_id))], limit=1)

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

    if not end_iso:
        try:
            d0 = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            end_iso = (d0 + timedelta(days=13, hours=23, minutes=59, seconds=59)).replace(microsecond=0).isoformat()
        except Exception:
            end_iso = ""

    return (start_iso, end_iso) if start_iso and end_iso else None


def _fallback_biweekly_window_from_app_state(c) -> Tuple[str, str, Optional[str]]:
    npd_str = None
    try:
        rows = _safe_select(c, "app_state", limit=1)
        if rows:
            npd = rows[0].get(APP_STATE_PAYOUT_DATE_FIELD)
            if npd:
                npd_str = str(npd)
                if "T" in npd_str:
                    npd_str = npd_str.split("T")[0]
    except Exception:
        pass

    if npd_str:
        try:
            end_dt = datetime.fromisoformat(npd_str).replace(hour=23, minute=59, second=59, microsecond=0)
            start_dt = (end_dt - timedelta(days=13)).replace(hour=0, minute=0, second=0, microsecond=0)
            return start_dt.isoformat(), end_dt.isoformat(), npd_str
        except Exception:
            pass

    end_dt = datetime.utcnow().replace(microsecond=0)
    start_dt = end_dt - timedelta(days=13)
    return start_dt.isoformat(), end_dt.isoformat(), None


def _get_app_state_payout_day(c) -> Optional[date]:
    try:
        rows = _safe_select(c, "app_state", limit=1)
        if rows:
            return _parse_date_only(rows[0].get(APP_STATE_PAYOUT_DATE_FIELD))
    except Exception:
        return None
    return None


def get_cycle_window(c, session_id: int) -> Tuple[str, str]:
    win = _session_window_from_sessions_table(c, session_id)
    if win:
        return win
    start_iso, end_iso, _ = _fallback_biweekly_window_from_app_state(c)
    return start_iso, end_iso


# ============================================================
# IDS
# ============================================================
def get_session_id(c) -> int:
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
# SIGNATURES (REAL SCHEMA)
# ============================================================
def get_signatures(c, entity_type: str, entity_id: int) -> list[dict]:
    if not _table_exists(c, "signatures"):
        return []
    return _safe_select(
        c,
        "signatures",
        filters=[("entity_type", "eq", entity_type), ("entity_id", "eq", int(entity_id))],
        order_col="signed_at",
        desc=True,
        limit=500,
    )


def missing_roles(sign_rows: list[dict], required_roles: list[str]) -> list[str]:
    got = {str(r.get("role", "")).strip().lower() for r in (sign_rows or []) if r.get("role")}
    req = [r.strip().lower() for r in required_roles]
    return [r for r in req if r not in got]


def insert_signature(
    c,
    entity_type: str,
    entity_id: int,
    role: str,
    signer_name: str,
    signer_member_id: int | None = None,
) -> None:
    if not _table_exists(c, "signatures"):
        raise Exception("signatures table not found.")

    payload = {
        "entity_type": str(entity_type),
        "entity_id": int(entity_id),
        "role": str(role),
        "signer_name": str(signer_name),
        "signed_at": now_iso(),
    }
    if signer_member_id is not None:
        payload["signer_member_id"] = int(signer_member_id)

    c.table("signatures").insert(payload).execute()


# ============================================================
# ROTATION
# ============================================================
def resolve_beneficiary_id(active_ids: list[int], pointer: int) -> int:
    if not active_ids:
        raise Exception("No active members available.")
    if int(pointer) in set(active_ids):
        return int(pointer)
    idx = int(pointer) - 1
    if 0 <= idx < len(active_ids):
        return int(active_ids[idx])
    return int(active_ids[0])


def next_rotation_pointer(active_ids: list[int], current_pointer: int) -> int:
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
# CONTRIBUTIONS
# ============================================================
def contributions_for_session(c, session_id: int) -> tuple[pd.DataFrame, dict]:
    table = _first_existing_table(c, ["contributions_legacy", "contributions"])
    if not table:
        return pd.DataFrame([]), {"source": None}

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
        return {"rows": 0, "total": 0.0, "contributors": 0}
    amt = pd.to_numeric(df_contrib.get("amount", 0), errors="coerce").fillna(0.0)
    member_col = "legacy_member_id" if "legacy_member_id" in df_contrib.columns else ("member_id" if "member_id" in df_contrib.columns else None)
    contributors = int(df_contrib[member_col].nunique()) if member_col else 0
    return {"rows": int(len(df_contrib)), "total": float(amt.sum()), "contributors": contributors}


def contribution_problems(active_ids: list[int], df_contrib: pd.DataFrame) -> list[str]:
    problems: list[str] = []
    if not active_ids:
        return ["No active members detected."]
    if df_contrib is None or df_contrib.empty:
        return ["No contributions found for this bi-weekly session."]

    member_col = "legacy_member_id" if "legacy_member_id" in df_contrib.columns else ("member_id" if "member_id" in df_contrib.columns else None)
    if not member_col:
        return ["Contributions missing member id column (legacy_member_id/member_id)."]

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
        problems.append(f"Members below base {BASE_CONTRIBUTION}: {bad_base[[member_col,'amount_num']].to_dict('records')}")

    def is_multiple(x: float) -> bool:
        try:
            return (int(round(x)) % CONTRIBUTION_STEP) == 0
        except Exception:
            return False

    bad_mult = g[~g["amount_num"].apply(is_multiple)]
    if not bad_mult.empty:
        problems.append(f"Non-multiple-of-{CONTRIBUTION_STEP} totals: {bad_mult[[member_col,'amount_num']].to_dict('records')}")
    return problems


# ============================================================
# PAYOUTS (REAL payouts_legacy schema)
# ============================================================
def _payout_table(c) -> Optional[str]:
    return _first_existing_table(c, ["payouts_legacy", "payouts"])


def fetch_paid_out_member_ids_for_window(c, session_id: int) -> Set[int]:
    t = _payout_table(c)
    if not t:
        return set()

    start_iso, end_iso = get_cycle_window(c, session_id)
    start_day = start_iso.split("T")[0]
    end_day = end_iso.split("T")[0]

    rows = _safe_select(
        c,
        t,
        filters=[("payout_date", "gte", start_day), ("payout_date", "lte", end_day)],
        order_col="payout_date",
        desc=True,
        limit=8000,
    )

    paid: Set[int] = set()
    for r in rows:
        mid = r.get("member_id")
        if mid is not None:
            try:
                paid.add(int(mid))
            except Exception:
                pass
    return paid


def _insert_payout_row(
    c,
    table: str,
    member_id: int,
    member_name: str,
    payout_amount: float,
    payout_date_iso: str,
    payout_index: int,
) -> dict:
    payload = {
        "member_id": int(member_id),
        "member_name": (member_name or "").strip() or f"Member {int(member_id):02d}",
        "payout_amount": float(payout_amount),
        "payout_date": payout_date_iso,  # actual date paid
        "payout_index": int(payout_index),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    res = c.table(table).insert(payload).execute()
    return (res.data or [None])[0] or payload


# ============================================================
# COMPLIANCE / PRECHECK / EXECUTE
# ============================================================
def compliance_for_payout(c, active_ids: list[int], session_id: int, rotation_pointer: int) -> dict:
    gate1_ok = (len(active_ids) == EXPECTED_ACTIVE_MEMBERS) or (len(active_ids) > 0)
    gate1_msg = f"Active members: {len(active_ids)} (expected {EXPECTED_ACTIVE_MEMBERS})"

    df_contrib, meta = contributions_for_session(c, session_id)
    summ = contribution_summary(df_contrib)
    problems = contribution_problems(active_ids, df_contrib)
    gate2_ok = (len(problems) == 0)

    sign_rows = get_signatures(c, "payout", int(session_id)) if session_id else []
    missing = missing_roles(sign_rows, PAYOUT_SIG_REQUIRED) if sign_rows is not None else []
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
        "contrib_total": float(summ.get("total", 0.0)),
    }


def payout_precheck_option_b(c, active_ids: list[int], allow_override: bool = False) -> dict:
    session_id = get_session_id(c)
    if session_id <= 0:
        return {"ok": False, "reason": "No numeric session_id found. Ensure current_session_id is an integer."}

    rotation_pointer = get_rotation_pointer(c) or 1
    comp = compliance_for_payout(c, active_ids, session_id=session_id, rotation_pointer=rotation_pointer)

    if not comp["gate1_ok"]:
        return {"ok": False, "reason": comp["gate1_msg"], "details": comp}
    if not comp["gate2_ok"]:
        return {"ok": False, "reason": "Contribution problems for this bi-weekly session.", "details": comp}

    payout_day = _get_app_state_payout_day(c)
    today = date.today()
    if payout_day is not None and today < payout_day and not allow_override:
        return {"ok": False, "reason": f"Payout allowed on {payout_day.isoformat()} (today is {today.isoformat()}).", "details": comp}

    if _table_exists(c, "signatures") and not comp["signatures_ok"] and not allow_override:
        return {"ok": False, "reason": comp["signatures_msg"], "details": comp}

    beneficiary_id = int(comp["beneficiary_id"]) if comp.get("beneficiary_id") else 0
    already_paid = fetch_paid_out_member_ids_for_window(c, session_id)
    if beneficiary_id in already_paid:
        return {"ok": False, "reason": f"Beneficiary {beneficiary_id} already paid in this cycle window.", "details": comp}

    return {
        "ok": True,
        "session_id": session_id,
        "rotation_pointer": rotation_pointer,
        "beneficiary_id": beneficiary_id,
        "pot_total": float(comp["contrib_total"]),
        "details": comp,
    }


def _update_app_state_next_index(c, next_idx: int, session_id: int, base_payout_day: Optional[date]) -> None:
    """
    ✅ Advances cycle state correctly:
      - next_payout_index -> next_idx
      - next_payout_date  -> (base_payout_day + 14 days)  ✅ FIXED
            base_payout_day = app_state.next_payout_date (the scheduled day for the cycle we just paid)
            so paying late won't push the schedule by a day.
      - current_session_id -> +1 IF column exists
    """
    if not _table_exists(c, "app_state"):
        return

    # Use scheduled payout day if available; fallback to today
    base = base_payout_day or date.today()
    new_day = base + timedelta(days=CYCLE_DAYS)

    rows = _safe_select(c, "app_state", limit=1)
    cur = rows[0] if rows else {}

    payload = {
        "next_payout_index": int(next_idx),
        APP_STATE_PAYOUT_DATE_FIELD: new_day.isoformat(),
        "updated_at": now_iso(),
    }

    # Try to advance current_session_id if present
    if cur.get("current_session_id") is not None and str(cur.get("current_session_id")).strip().isdigit():
        payload["current_session_id"] = int(cur["current_session_id"]) + 1
    else:
        payload["current_session_id"] = int(session_id) + 1  # may fail if column doesn't exist

    # Try update; if it fails because current_session_id doesn't exist, retry without it
    try:
        c.table("app_state").update(payload).eq("id", 1).execute()
        return
    except Exception:
        pass

    payload2 = {
        "next_payout_index": int(next_idx),
        APP_STATE_PAYOUT_DATE_FIELD: new_day.isoformat(),
        "updated_at": now_iso(),
    }
    try:
        c.table("app_state").update(payload2).eq("id", 1).execute()
        return
    except Exception:
        pass

    try:
        c.table("app_state").upsert({"id": 1, **payload2}).execute()
        return
    except Exception:
        pass


def execute_payout_option_b(
    c,
    active_ids: list[int],
    beneficiary_name: str,
    allow_override: bool = False,
) -> dict:
    pre = payout_precheck_option_b(c, active_ids, allow_override=allow_override)
    if not pre.get("ok"):
        return pre

    session_id = int(pre["session_id"])
    rotation_pointer = int(pre["rotation_pointer"])
    beneficiary_id = int(pre["beneficiary_id"])
    pot_total = float(pre["pot_total"])

    t = _payout_table(c)
    if not t:
        return {"ok": False, "reason": "No payout table found (payouts_legacy / payouts)."}

    # ✅ capture scheduled payout day BEFORE we change app_state
    scheduled_payout_day = _get_app_state_payout_day(c)

    payout_date_iso = date.today().isoformat()  # actual date paid
    payout_index = int(rotation_pointer)

    try:
        payout_row = _insert_payout_row(
            c,
            t,
            member_id=beneficiary_id,
            member_name=beneficiary_name,
            payout_amount=pot_total,
            payout_date_iso=payout_date_iso,
            payout_index=payout_index,
        )
    except Exception as e:
        return {"ok": False, "reason": str(e)}

    # ✅ advance pointer + next payout schedule (based on scheduled payout day)
    nxt = next_rotation_pointer(active_ids, rotation_pointer)
    _update_app_state_next_index(c, nxt, session_id=session_id, base_payout_day=scheduled_payout_day)

    return {
        "ok": True,
        "session_id": session_id,
        "rotation_pointer": rotation_pointer,
        "beneficiary_id": beneficiary_id,
        "amount_paid": pot_total,
        "next_payout_index": nxt,
        "payout_table": t,
        "payout_row": payout_row,
    }


# ============================================================
# PDF RECEIPT (FIXED: no duplicate member_id columns)
# ============================================================
def build_payout_receipt_pdf(
    *,
    group_name: str,
    session_id: int,
    payout_day: str | None,
    payout_date: str,
    beneficiary_id: int,
    beneficiary_name: str,
    contributions_df: pd.DataFrame,
    members_df: pd.DataFrame,
    signatures: list[dict] | None,
    total_paid: float,
) -> bytes:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    dfc = contributions_df.copy() if contributions_df is not None else pd.DataFrame([])
    dfm = members_df.copy() if members_df is not None else pd.DataFrame([])

    member_col = None
    if not dfc.empty:
        if "legacy_member_id" in dfc.columns:
            member_col = "legacy_member_id"
        elif "member_id" in dfc.columns:
            member_col = "member_id"

    if dfc.empty or member_col is None:
        contrib_summary = pd.DataFrame({"member_id": [], "amount": []})
    else:
        dfc = dfc.copy()
        dfc[member_col] = pd.to_numeric(dfc[member_col], errors="coerce").fillna(-1).astype(int)
        dfc["amount_num"] = pd.to_numeric(dfc.get("amount", 0), errors="coerce").fillna(0.0).astype(float)
        contrib_summary = (
            dfc.groupby(member_col, as_index=False)["amount_num"]
            .sum()
            .rename(columns={member_col: "member_id", "amount_num": "amount"})
        )

    if not dfm.empty and "id" in dfm.columns:
        dfm = dfm.copy()
        dfm["id"] = pd.to_numeric(dfm["id"], errors="coerce").fillna(-1).astype(int)
        dfm["name"] = dfm.get("name", "").astype(str)

        contrib_summary = contrib_summary.copy()
        contrib_summary["member_id"] = pd.to_numeric(contrib_summary["member_id"], errors="coerce").fillna(-1).astype(int)

        merged = dfm[["id", "name"]].merge(
            contrib_summary,
            how="left",
            left_on="id",
            right_on="member_id",
        )
        merged["amount"] = pd.to_numeric(merged.get("amount", 0), errors="coerce").fillna(0.0)

        contrib_summary = pd.DataFrame({
            "member_id": merged["id"].astype(int),
            "name": merged["name"].astype(str),
            "amount": merged["amount"].astype(float),
        })
    else:
        contrib_summary["name"] = ""
        contrib_summary = contrib_summary[["member_id", "name", "amount"]]

    total_pot = float(pd.to_numeric(contrib_summary["amount"], errors="coerce").fillna(0.0).sum())

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(f"<b>{group_name} — Payout Receipt</b>", styles["Title"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"<b>Session ID:</b> {session_id}", styles["Normal"]))
    if payout_day:
        story.append(Paragraph(f"<b>Scheduled payout day:</b> {payout_day}", styles["Normal"]))
    story.append(Paragraph(f"<b>Actual payout date:</b> {payout_date}", styles["Normal"]))
    story.append(Paragraph(f"<b>Beneficiary:</b> {beneficiary_id:02d} • {beneficiary_name}", styles["Normal"]))
    story.append(Paragraph(f"<b>Total amount received (pot):</b> {total_pot:,.0f}", styles["Normal"]))
    story.append(Paragraph(f"<b>Total amount paid to beneficiary:</b> {float(total_paid):,.0f}", styles["Normal"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Member Contributions</b>", styles["Heading2"]))
    story.append(Spacer(1, 6))

    table_data = [["#", "Member ID", "Member Name", "Amount Contributed"]]
    cs = contrib_summary.sort_values("member_id")
    for i, row in enumerate(cs.itertuples(index=False), start=1):
        mid = int(getattr(row, "member_id", 0))
        nm = str(getattr(row, "name", ""))
        am = float(getattr(row, "amount", 0.0))
        table_data.append([str(i), str(mid), nm, f"{am:,.0f}"])

    t = Table(table_data, colWidths=[28, 60, 240, 120])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.black),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.lightgrey]),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))

    story.append(Paragraph("<b>Signatures</b>", styles["Heading2"]))
    story.append(Spacer(1, 6))

    sig_rows = signatures or []
    if not sig_rows:
        story.append(Paragraph("No signatures recorded.", styles["Normal"]))
    else:
        sig_table = [["Role", "Signer Name", "Signer Member ID", "Signed At"]]
        for s in sig_rows:
            sig_table.append([
                str(s.get("role", "")),
                str(s.get("signer_name", "")),
                str(s.get("signer_member_id", "") if s.get("signer_member_id") is not None else ""),
                str(s.get("signed_at", "")),
            ])
        stbl = Table(sig_table, colWidths=[90, 200, 90, 140])
        stbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.black),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTSIZE", (0, 1), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(stbl)

    story.append(Spacer(1, 12))
    story.append(Paragraph(f"<i>Generated: {now_iso()}</i>", styles["Normal"]))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


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
    summ = (comp.get("gate2_summary") or {}) if isinstance(comp, dict) else {}
    meta = (comp.get("contrib_meta") or {}) if isinstance(comp, dict) else {}

    contributors = int(summ.get("contributors", 0))
    pot_total = float(comp.get("contrib_total", 0.0))
    rows_count = int(summ.get("rows", 0))
    missing = max(len(active_ids) - contributors, 0)

    return pd.DataFrame([{
        "session_number": session_id,
        "pot_total": pot_total,
        "rows_count": rows_count,
        "contributors": contributors,
        "missing_contributors": missing,
        "beneficiary_id": beneficiary_id,
        "beneficiary_name": beneficiary_name,
        "next_payout_date": next_payout_date or meta.get("next_payout_date") or "—",
        "already_paid": False,
        "contrib_source": meta.get("source", "—"),
        "window_start": meta.get("start", "—"),
        "window_end": meta.get("end", "—"),
    }])


# ============================================================
# UI: PAYOUT PAGE (ENTRYPOINT)
# ============================================================
def render_payouts(sb_service, schema: str):
    st.title("Payouts • Option B (Bi-weekly Rotation)")
    st.caption("✅ Payout ON payout day • ✅ Signatures • ✅ PDF receipt download after payout • ✅ Auto refresh to next cycle")

    # ✅ Keep last PDF available after rerun
    if st.session_state.get("last_payout_pdf") and st.session_state.get("last_payout_filename"):
        st.success("✅ Last payout receipt is ready (saved after auto-refresh).")
        st.download_button(
            "⬇️ Download Last Payout Receipt (PDF)",
            data=st.session_state["last_payout_pdf"],
            file_name=st.session_state["last_payout_filename"],
            mime="application/pdf",
            use_container_width=True,
            key="dl_last_payout_pdf",
        )
        st.divider()

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

    session_id = get_session_id(sb_service)
    rotation_pointer = get_rotation_pointer(sb_service)

    beneficiary_id = resolve_beneficiary_id(active_ids, rotation_pointer) if rotation_pointer else 0
    beneficiary_name = _member_name_by_id(dfm, beneficiary_id)
    beneficiary_label = f"{beneficiary_id:02d} • {beneficiary_name}" if beneficiary_id else "—"

    # payout day
    next_payout_date = None
    try:
        arows = _safe_select(sb_service, "app_state", limit=1)
        if arows:
            v = arows[0].get(APP_STATE_PAYOUT_DATE_FIELD)
            next_payout_date = str(v) if v else None
    except Exception:
        pass

    payout_day = _parse_date_only(next_payout_date)
    today = date.today()

    comp = compliance_for_payout(sb_service, active_ids, session_id=session_id, rotation_pointer=rotation_pointer)

    already_paid_ids = fetch_paid_out_member_ids_for_window(sb_service, session_id) if session_id else set()
    already_paid = bool(beneficiary_id and beneficiary_id in already_paid_ids)

    # KPI cards
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Active Members", str(len(active_ids)))
    c2.metric("Bi-weekly Session ID", str(session_id) if session_id else "—")
    c3.metric("Rotation Pointer", str(rotation_pointer) if rotation_pointer else "—")
    c4.metric("Current Beneficiary", beneficiary_label)
    c5.metric("Pot (this session)", f"{float(comp.get('contrib_total', 0.0)):,.0f}")

    st.divider()

    st.subheader("KPIs — Current Cycle (Python)")
    kdf = compute_cycle_kpi_row(session_id, active_ids, beneficiary_id, beneficiary_name, next_payout_date, comp)
    kdf.loc[0, "already_paid"] = already_paid
    st.dataframe(kdf, use_container_width=True, hide_index=True)

    if payout_day:
        st.info(f"📅 Payout day: {payout_day.isoformat()} • Today: {today.isoformat()} • Allowed: {'YES' if today >= payout_day else 'NO'}")
    else:
        st.info("📅 Payout day not set in app_state — payout day restriction disabled.")

    st.divider()

    # Gates
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

    # Signatures
    st.subheader("Signatures")
    if _table_exists(sb_service, "signatures"):
        sign_rows = get_signatures(sb_service, "payout", int(session_id)) if session_id else []
        missing = missing_roles(sign_rows, PAYOUT_SIG_REQUIRED)

        if not missing:
            st.success("All required payout signatures are present (for this session).")
        else:
            st.warning("Missing required signatures: " + ", ".join(missing))
            st.caption(f"Required roles: {PAYOUT_SIG_REQUIRED} • entity_type=payout • entity_id=session_id ({session_id})")

        with st.expander("✍️ Add signature (for this session)", expanded=True):
            role = st.selectbox("Role", options=PAYOUT_SIG_REQUIRED, index=0)
            signer_name = st.text_input("Signer name", value="")
            signer_member_id = st.number_input("Signer member ID (optional)", min_value=0, step=1, value=0)

            if st.button("Add signature", use_container_width=True):
                try:
                    if not signer_name.strip():
                        st.error("Signer name is required.")
                    else:
                        insert_signature(
                            sb_service,
                            entity_type="payout",
                            entity_id=int(session_id),
                            role=str(role),
                            signer_name=signer_name.strip(),
                            signer_member_id=int(signer_member_id) if signer_member_id > 0 else None,
                        )
                        st.success(f"Signature recorded: {role} ✅")
                        st.rerun()
                except Exception as e:
                    msg = str(e)
                    if "duplicate key value violates unique constraint" in msg:
                        st.warning(f"{role} already signed for this session.")
                    else:
                        st.error(msg)
    else:
        st.info("signatures table not found — signature enforcement skipped.")

    if already_paid:
        st.warning("Already paid detected in this cycle window (by payouts_legacy.payout_date filter).")

    st.divider()

    # Execute payout + PDF download
    force = st.checkbox("⚠️ Admin override (force payout)", value=False)
    pre = payout_precheck_option_b(sb_service, active_ids, allow_override=force)

    disabled = not bool(pre.get("ok"))
    if st.button("✅ Execute Payout (Option B)", disabled=disabled, use_container_width=True):
        res = execute_payout_option_b(sb_service, active_ids, beneficiary_name=beneficiary_name, allow_override=force)
        if res.get("ok"):
            df_contrib, _meta = contributions_for_session(sb_service, int(res["session_id"]))
            sigs = get_signatures(sb_service, "payout", int(res["session_id"]))

            pdf_bytes = build_payout_receipt_pdf(
                group_name="theyoungshallgrow",
                session_id=int(res["session_id"]),
                payout_day=(payout_day.isoformat() if payout_day else None),
                payout_date=date.today().isoformat(),
                beneficiary_id=int(res["beneficiary_id"]),
                beneficiary_name=beneficiary_name,
                contributions_df=df_contrib,
                members_df=dfm,
                signatures=sigs,
                total_paid=float(res["amount_paid"]),
            )

            filename = "payout_receipt_session_%s_beneficiary_%02d.pdf" % (
                int(res["session_id"]),
                int(res["beneficiary_id"]),
            )

            # ✅ store PDF to survive rerun
            st.session_state["last_payout_pdf"] = pdf_bytes
            st.session_state["last_payout_filename"] = filename

            # ✅ force app refresh so Dashboard updates immediately
            try:
                st.cache_data.clear()
                st.cache_resource.clear()
            except Exception:
                pass

            st.success("✅ Payout completed. Moving to next cycle...")
            st.rerun()

        else:
            st.error(res.get("reason", "Payout failed"))

    with st.expander("Debug details (optional)", expanded=False):
        st.write("Precheck JSON:")
        st.json(pre)
        st.write("Gate details JSON:")
        st.json(comp)
