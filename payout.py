
# payout.py
from __future__ import annotations

import pandas as pd
from datetime import date, timedelta

from db import now_iso, current_session_id, fetch_one

# -------------------------
# CONFIG (PAYOUT RULES)
# -------------------------
EXPECTED_ACTIVE_MEMBERS = 17

BASE_CONTRIBUTION = 500
CONTRIBUTION_STEP = 500

# Gate reads these kinds
ALLOWED_CONTRIB_KINDS = ["paid", "contributed"]

PAYOUT_SIG_REQUIRED = ["president", "beneficiary", "treasury", "surety"]


# -------------------------
# ROTATION HELPERS
# -------------------------
def next_unpaid_beneficiary(active_ids: list[int], already_paid_ids: set[int], start_idx: int) -> int:
    """
    Find the next beneficiary in rotation among active_ids,
    skipping IDs already paid.
    """
    if not active_ids:
        return int(start_idx)

    active_sorted = sorted(set(int(x) for x in active_ids))
    start_idx = int(start_idx)

    # Clamp start index into active set range
    if start_idx not in active_sorted:
        bigger = [x for x in active_sorted if x >= start_idx]
        start_idx = bigger[0] if bigger else active_sorted[0]

    start_pos = active_sorted.index(start_idx)
    rotation = active_sorted[start_pos:] + active_sorted[:start_pos]

    for mid in rotation:
        if mid not in already_paid_ids:
            return int(mid)

    return int(start_idx)


# -------------------------
# SIGNATURES
# -------------------------
def get_signatures(c, entity_type: str, entity_id: int) -> pd.DataFrame:
    try:
        rows = (
            c.table("signatures")
             .select("role,signer_name,signer_member_id,signed_at")
             .eq("entity_type", entity_type)
             .eq("entity_id", int(entity_id))
             .order("signed_at", desc=False)
             .limit(500)
             .execute()
             .data or []
        )
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["role", "signer_name", "signer_member_id", "signed_at"])
        return df
    except Exception:
        return pd.DataFrame(columns=["role", "signer_name", "signer_member_id", "signed_at"])


def missing_roles(df_sig: pd.DataFrame, required_roles: list[str]) -> list[str]:
    signed = set(df_sig["role"].tolist()) if df_sig is not None and not df_sig.empty else set()
    return [r for r in required_roles if r not in signed]


# -------------------------
# CONTRIBUTIONS (ROTATION-BASED)
# -------------------------
def fetch_rotation_contributions(c, payout_index: int):
    """
    Pull contributions for the CURRENT rotation (strict).
    """
    resp = (
        c.table("contributions_legacy")
         .select("member_id,amount,kind,payout_index")
         .eq("payout_index", int(payout_index))
         .in_("kind", ALLOWED_CONTRIB_KINDS)
         .limit(20000)
         .execute()
    )
    return resp.data or []


def build_contribution_summary(active_members, contrib_rows):
    per_member = {}
    for r in contrib_rows:
        mid = int(r.get("member_id") or 0)
        per_member[mid] = per_member.get(mid, 0.0) + float(r.get("amount") or 0)

    return [
        {"member_id": mid, "member_name": name, "contributed": float(per_member.get(mid, 0.0))}
        for (mid, name) in active_members
    ]


def fetch_current_rotation_pot(c):
    """
    Reads the canonical rotation pot from v_contribution_pot.
    Returns (payout_index, payout_date, pot_amount).
    """
    row = (
        c.table("v_contribution_pot")
         .select("next_payout_index,next_payout_date,pot_amount")
         .single()
         .execute()
         .data
    )
    idx = int(row.get("next_payout_index") or 1)
    dt = row.get("next_payout_date")
    pot = float(row.get("pot_amount") or 0)
    return idx, dt, pot


# -------------------------
# GATES
# -------------------------
def validate_gate_1(active_members):
    ok = (len(active_members) == EXPECTED_ACTIVE_MEMBERS)
    df = pd.DataFrame([{"member_id": mid, "member_name": name} for mid, name in active_members])
    return ok, len(active_members), df


def validate_gate_2(summary_rows, base=BASE_CONTRIBUTION, step=CONTRIBUTION_STEP):
    problems = []
    for r in summary_rows:
        amt = float(r.get("contributed") or 0)
        if amt < base:
            problems.append({**r, "issue": f"Below base {base}"})
        elif amt % step != 0:
            problems.append({**r, "issue": f"Not multiple of {step}"})
    return (len(problems) == 0, pd.DataFrame(problems))


def validate_gate_3_from_pot(pot_amount: float):
    return (pot_amount > 0, float(pot_amount))


# -------------------------
# PRECHECK + EXECUTE
# -------------------------
def payout_precheck_option_b(c, active_members, already_paid_ids: set[int], start_idx: int):
    # Keep session_id for legacy display only (not used for pot)
    session_id = current_session_id(c)

    active_ids_local = [mid for (mid, _) in active_members]
    beneficiary_id = next_unpaid_beneficiary(active_ids_local, already_paid_ids, int(start_idx))
    ben = fetch_one(c.table("member_registry").select("full_name").eq("legacy_member_id", int(beneficiary_id)))
    beneficiary_name = (ben or {}).get("full_name") or f"Member {beneficiary_id}"

    gate1_ok, _count, df_members = validate_gate_1(active_members)

    # Rotation pot (canonical)
    rotation_idx, rotation_payout_date, pot_amount = fetch_current_rotation_pot(c)

    # Rotation contributions summary for Gate 2
    contrib_rows = fetch_rotation_contributions(c, rotation_idx)
    summary_rows = build_contribution_summary(active_members, contrib_rows)

    gate2_ok, df_problems = validate_gate_2(summary_rows)
    gate3_ok, pot_amount2 = validate_gate_3_from_pot(pot_amount)

    return {
        "session_id": session_id,
        "rotation_index": rotation_idx,
        "rotation_payout_date": rotation_payout_date,
        "gate1_ok": bool(gate1_ok),
        "gate2_ok": bool(gate2_ok),
        "gate3_ok": bool(gate3_ok),
        "pot": float(pot_amount2),
        "summary_rows": summary_rows,
        "df_problems": df_problems,
        "active_members_df": df_members,
        "beneficiary_id": int(beneficiary_id),
        "beneficiary_name": beneficiary_name,
        "already_paid": sorted(list(already_paid_ids)),
        "reason": "",
    }


def execute_payout_option_b(c, active_members, already_paid_ids: set[int], start_idx: int):
    # Determine beneficiary
    active_ids_local = [mid for (mid, _) in active_members]
    beneficiary_id = next_unpaid_beneficiary(active_ids_local, already_paid_ids, int(start_idx))

    ben = fetch_one(
        c.table("member_registry")
         .select("legacy_member_id,full_name")
         .eq("legacy_member_id", int(beneficiary_id))
    )
    beneficiary_name = (ben or {}).get("full_name") or f"Member {beneficiary_id}"

    # Gate 4 signatures required
    payout_entity_id = int(beneficiary_id)
    df_sig = get_signatures(c, "payout", payout_entity_id)
    miss = missing_roles(df_sig, PAYOUT_SIG_REQUIRED)
    if miss:
        raise Exception("Payout blocked (missing signatures): " + ", ".join(miss))

    ok1, actual_count, _df_members = validate_gate_1(active_members)
    if not ok1:
        raise Exception(f"Payout blocked: active member count {actual_count} != {EXPECTED_ACTIVE_MEMBERS}.")

    # Canonical rotation pot
    rotation_idx, rotation_payout_date, pot_amount = fetch_current_rotation_pot(c)

    # Gate 2 check for current rotation
    contrib_rows = fetch_rotation_contributions(c, rotation_idx)
    summary_rows = build_contribution_summary(active_members, contrib_rows)

    ok2, _df_problems = validate_gate_2(summary_rows)
    if not ok2:
        raise Exception("Payout blocked: contribution rules not met for all active members (current rotation).")

    ok3, pot_amount = validate_gate_3_from_pot(pot_amount)
    if not ok3:
        raise Exception("Payout blocked: pot is zero for current rotation.")

    # Log payout (include rotation index)
    payout_payload = {
        "member_id": int(beneficiary_id),
        "member_name": str(beneficiary_name),
        "payout_amount": float(pot_amount),
        "payout_date": str(date.today()),
        "payout_index": int(rotation_idx),
        "created_at": now_iso(),
    }

    payout_logged = True
    try:
        c.table("payouts_legacy").insert(payout_payload).execute()
    except Exception:
        payout_logged = False

    # Advance rotation pointer strictly (+1 index, +14 days)
    c.table("app_state").update({
        "next_payout_index": int(rotation_idx) + 1,
        "next_payout_date": (date.fromisoformat(str(rotation_payout_date)) + timedelta(days=14)).isoformat()
        if rotation_payout_date else (date.today() + timedelta(days=14)).isoformat(),
    }).eq("id", 1).execute()

    return {
        "session_id": current_session_id(c),  # legacy (may change)
        "rotation_index_paid_out": int(rotation_idx),
        "beneficiary_legacy_member_id": int(beneficiary_id),
        "beneficiary_name": beneficiary_name,
        "pot_paid_out": float(pot_amount),
        "payout_logged": payout_logged,
        "next_payout_index": int(rotation_idx) + 1,
        "next_payout_date": (date.fromisoformat(str(rotation_payout_date)) + timedelta(days=14)).isoformat()
        if rotation_payout_date else (date.today() + timedelta(days=14)).isoformat(),
        "contribution_summary": summary_rows,
        "already_paid_members": sorted(list(already_paid_ids)),
        "payout_signature_entity_id": payout_entity_id,
        "payout_missing_signatures": [],
    }
