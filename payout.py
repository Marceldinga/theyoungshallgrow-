print("payout.py loaded")
# payout.py ✅ Njangi payout engine (SERVICE KEY)
from __future__ import annotations

from datetime import date, timedelta, datetime, timezone
import pandas as pd
import streamlit as st
from postgrest.exceptions import APIError

# -------------------------
# CONFIG (Njangi rules)
# -------------------------
EXPECTED_ACTIVE_MEMBERS = 17
BASE_CONTRIBUTION = 500
CONTRIBUTION_STEP = 500
ALLOWED_CONTRIB_KINDS = ["paid", "contributed"]
PAYOUT_SIG_REQUIRED = ["president", "beneficiary", "treasury", "surety"]


# -------------------------
# small helpers
# -------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def safe_select(sb, schema: str, table: str, cols: str="*", order_by: str|None=None,
                desc: bool=False, limit: int|None=None, **filters) -> list[dict]:
    try:
        q = sb.schema(schema).table(table).select(cols)
        for k, v in filters.items():
            if v is None:
                continue
            # supports equality filters only
            q = q.eq(k, v)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit is not None:
            q = q.limit(limit)
        return q.execute().data or []
    except APIError as e:
        st.error(f"Supabase read error: {schema}.{table}")
        st.code(str(e), language="text")
        return []
    except Exception as e:
        st.error(f"Unexpected read error: {schema}.{table}: {e}")
        return []

def safe_single(sb, schema: str, table: str, cols: str="*", **filters) -> dict:
    rows = safe_select(sb, schema, table, cols, limit=1, **filters)
    return rows[0] if rows else {}

def safe_insert(sb, schema: str, table: str, payload: dict) -> bool:
    try:
        sb.schema(schema).table(table).insert(payload).execute()
        return True
    except APIError as e:
        st.error(f"Supabase insert error: {schema}.{table}")
        st.code(str(e), language="text")
        return False
    except Exception as e:
        st.error(f"Unexpected insert error: {schema}.{table}: {e}")
        return False

def safe_update(sb, schema: str, table: str, payload: dict, where: dict) -> bool:
    try:
        q = sb.schema(schema).table(table).update(payload)
        for k, v in where.items():
            q = q.eq(k, v)
        q.execute()
        return True
    except APIError as e:
        st.error(f"Supabase update error: {schema}.{table}")
        st.code(str(e), language="text")
        return False
    except Exception as e:
        st.error(f"Unexpected update error: {schema}.{table}: {e}")
        return False


# -------------------------
# rotation helpers
# -------------------------
def next_unpaid_beneficiary(active_ids: list[int], already_paid_ids: set[int], start_idx: int) -> int:
    if not active_ids:
        return int(start_idx)

    active_sorted = sorted(set(int(x) for x in active_ids))
    start_idx = int(start_idx)

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
# signatures
# -------------------------
def get_signatures(sb, schema: str, entity_type: str, entity_id: int) -> pd.DataFrame:
    rows = safe_select(
        sb, schema, "signatures",
        "role,signer_name,signer_member_id,signed_at",
        order_by="signed_at",
        desc=False,
        entity_type=entity_type,
        entity_id=int(entity_id),
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["role", "signer_name", "signer_member_id", "signed_at"])
    return df

def missing_roles(df_sig: pd.DataFrame, required_roles: list[str]) -> list[str]:
    signed = set(df_sig["role"].tolist()) if df_sig is not None and not df_sig.empty else set()
    return [r for r in required_roles if r not in signed]


# -------------------------
# contributions (rotation-based)
# -------------------------
def fetch_rotation_contributions(sb, schema: str, payout_index: int) -> list[dict]:
    try:
        resp = (
            sb.schema(schema)
              .table("contributions_legacy")
              .select("member_id,amount,kind,payout_index")
              .eq("payout_index", int(payout_index))
              .in_("kind", ALLOWED_CONTRIB_KINDS)
              .limit(20000)
              .execute()
        )
        return resp.data or []
    except Exception:
        return []

def build_contribution_summary(active_members: list[tuple[int, str]], contrib_rows: list[dict]) -> list[dict]:
    per_member: dict[int, float] = {}
    for r in contrib_rows:
        mid = int(r.get("member_id") or 0)
        per_member[mid] = per_member.get(mid, 0.0) + float(r.get("amount") or 0)

    return [
        {"member_id": mid, "member_name": name, "contributed": float(per_member.get(mid, 0.0))}
        for (mid, name) in active_members
    ]

def validate_gate_1(active_members: list[tuple[int, str]]):
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


# -------------------------
# main render function (CONNECTS TO app.py)
# -------------------------
def render_payouts(sb_service, schema: str):
    st.header("Payouts (Njangi Rotation)")

    # ---- load members (YOUR REAL TABLE)
    mrows = safe_select(sb_service, schema, "members_legacy", "id,name,position", order_by="id")
    dfm = pd.DataFrame(mrows)
    if dfm.empty:
        st.warning("members_legacy is empty or not readable.")
        return

    dfm["id"] = pd.to_numeric(dfm["id"], errors="coerce")
    dfm = dfm.dropna(subset=["id"]).copy()
    dfm["id"] = dfm["id"].astype(int)
    dfm["name"] = dfm["name"].astype(str)

    active_members = [(int(r["id"]), str(r["name"])) for _, r in dfm.iterrows()]
    active_ids = [mid for mid, _ in active_members]

    # ---- get rotation index/date from current_season_view first (you have it), fallback to app_state
    season = safe_single(sb_service, schema, "current_season_view", "*")
    state = safe_single(sb_service, schema, "app_state", "*", id=1)

    rotation_idx = int(season.get("next_payout_index") or state.get("next_payout_index") or 1)
    rotation_date = season.get("next_payout_date") or state.get("next_payout_date")

    # ---- pot (prefer v_contribution_pot if exists)
    pot_row = safe_single(sb_service, schema, "v_contribution_pot", "*")
    pot_amount = float(pot_row.get("pot_amount") or 0.0) if pot_row else 0.0
    pot_idx = int(pot_row.get("next_payout_index") or rotation_idx) if pot_row else rotation_idx

    # ---- already paid (this rotation index)
    already_paid_ids: set[int] = set()
    payouts = safe_select(sb_service, schema, "payouts_legacy", "member_id,payout_index", limit=20000)
    for p in payouts:
        if int(p.get("payout_index") or -1) == int(pot_idx):
            try:
                already_paid_ids.add(int(p.get("member_id")))
            except Exception:
                pass

    # ---- choose beneficiary (uses member IDs rotation order; you can switch to position-based later)
    start_idx = int(state.get("rotation_start_index") or 1)
    beneficiary_id = next_unpaid_beneficiary(active_ids, already_paid_ids, start_idx)
    beneficiary_name = dfm.loc[dfm["id"] == beneficiary_id, "name"].iloc[0] if beneficiary_id in set(dfm["id"]) else f"Member {beneficiary_id}"

    # ---- gates
    gate1_ok, member_count, df_gate1 = validate_gate_1(active_members)

    contrib_rows = fetch_rotation_contributions(sb_service, schema, pot_idx)
    summary_rows = build_contribution_summary(active_members, contrib_rows)
    gate2_ok, df_problems = validate_gate_2(summary_rows)
    gate3_ok = (pot_amount > 0)

    # ---- display
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rotation Index", str(pot_idx))
    c2.metric("Payout Pot", f"{pot_amount:,.0f}")
    c3.metric("Beneficiary", f"{beneficiary_id} • {beneficiary_name}")
    c4.metric("Next Payout Date", str(rotation_date or "N/A"))

    st.divider()

    st.subheader("Gate Status")
    st.write(
        {
            "Gate1 (member count == 17)": gate1_ok,
            "Gate2 (everyone >=500 and multiple of 500)": gate2_ok,
            "Gate3 (pot > 0)": gate3_ok,
        }
    )

    with st.expander("Gate 1: Active members", expanded=False):
        st.dataframe(df_gate1, use_container_width=True)

    with st.expander("Gate 2: Contribution problems", expanded=False):
        if df_problems is None or df_problems.empty:
            st.success("No problems detected.")
        else:
            st.dataframe(df_problems, use_container_width=True)

    # ---- signature gate (optional but enforced if you click execute)
    with st.expander("Gate 4: Signatures (required)", expanded=False):
        df_sig = get_signatures(sb_service, schema, "payout", int(beneficiary_id))
        st.dataframe(df_sig, use_container_width=True)
        miss = missing_roles(df_sig, PAYOUT_SIG_REQUIRED)
        if miss:
            st.warning("Missing roles: " + ", ".join(miss))
        else:
            st.success("All required signatures are present.")

    st.divider()

    # ---- execute payout
    st.subheader("Execute payout")
    if st.button("✅ Execute payout now (Service Key)", use_container_width=True):
        # Gate 4: signatures
        df_sig = get_signatures(sb_service, schema, "payout", int(beneficiary_id))
        miss = missing_roles(df_sig, PAYOUT_SIG_REQUIRED)
        if miss:
            st.error("Payout blocked (missing signatures): " + ", ".join(miss))
            return
        if not gate1_ok:
            st.error(f"Payout blocked: active member count {member_count} != {EXPECTED_ACTIVE_MEMBERS}")
            return
        if not gate2_ok:
            st.error("Payout blocked: contribution rules not met for all members.")
            return
        if not gate3_ok:
            st.error("Payout blocked: pot is zero.")
            return

        payout_payload = {
            "member_id": int(beneficiary_id),
            "member_name": str(beneficiary_name),
            "payout_amount": float(pot_amount),
            "payout_date": str(date.today()),
            "payout_index": int(pot_idx),
            "created_at": now_iso(),
        }

        payout_logged = safe_insert(sb_service, schema, "payouts_legacy", payout_payload)

        # advance rotation pointer: +1 index, +14 days
        next_index = int(pot_idx) + 1
        next_date = (date.today() + timedelta(days=14)).isoformat()

        safe_update(
            sb_service, schema, "app_state",
            {
                "next_payout_index": next_index,
                "next_payout_date": next_date,
                "updated_at": now_iso(),
            },
            where={"id": 1},
        )

        st.success(f"Payout executed for {beneficiary_name}. Logged={payout_logged}. Next index={next_index}")
        st.cache_data.clear()
        st.rerun()
