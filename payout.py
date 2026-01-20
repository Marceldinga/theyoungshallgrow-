
# payout.py ✅ COMPLETE UPDATED SINGLE FILE (Njangi payout engine – SERVICE KEY)
# Works with your schema:
# - members_legacy (id, name, position)
# - app_state (id=1, next_payout_index, next_payout_date, rotation_start_index, rotation_start_date)
# - contributions_legacy (member_id, amount, kind, payout_index)
# - payouts_legacy (member_id, member_name, payout_amount, payout_date, payout_index, created_at)  [optional but recommended]
# - current_season_view / v_contribution_pot / signatures are OPTIONAL (will gracefully fallback if missing)

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
# Helpers
# -------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _is_table_missing_error(e: Exception) -> bool:
    msg = str(e).lower()
    return ("does not exist" in msg) or ("relation" in msg)

def safe_select(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    order_by: str | None = None,
    desc: bool = False,
    limit: int | None = None,
    **eq_filters,
) -> list[dict]:
    try:
        q = sb.schema(schema).table(table).select(cols)
        for k, v in eq_filters.items():
            if v is None:
                continue
            q = q.eq(k, v)
        if order_by:
            q = q.order(order_by, desc=desc)
        if limit is not None:
            q = q.limit(limit)
        resp = q.execute()
        return resp.data or []
    except APIError as e:
        # Show readable error, but don’t crash the page
        st.error(f"Supabase read error: {schema}.{table}")
        st.code(str(e), language="text")
        return []
    except Exception as e:
        st.error(f"Unexpected read error: {schema}.{table}: {e}")
        return []

def safe_single(sb, schema: str, table: str, cols: str = "*", **eq_filters) -> dict:
    rows = safe_select(sb, schema, table, cols, limit=1, **eq_filters)
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
# Rotation helpers
# -------------------------
def next_unpaid_beneficiary(active_ids: list[int], already_paid_ids: set[int], start_id: int) -> int:
    """
    Rotation across member IDs (id). Uses start_id as the start pointer.
    """
    if not active_ids:
        return int(start_id)

    active_sorted = sorted(set(int(x) for x in active_ids))
    start_id = int(start_id)

    if start_id not in active_sorted:
        bigger = [x for x in active_sorted if x >= start_id]
        start_id = bigger[0] if bigger else active_sorted[0]

    start_pos = active_sorted.index(start_id)
    rotation = active_sorted[start_pos:] + active_sorted[:start_pos]

    for mid in rotation:
        if mid not in already_paid_ids:
            return int(mid)

    return int(start_id)


# -------------------------
# Signatures (OPTIONAL table)
# -------------------------
def get_signatures(sb, schema: str, entity_type: str, entity_id: int) -> pd.DataFrame:
    # signatures table is optional; if missing, return empty (and you can choose to not enforce)
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
# Contributions checks
# -------------------------
def fetch_rotation_contributions(sb, schema: str, payout_index: int) -> list[dict]:
    """
    Reads contributions for a given payout_index.
    """
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
# Pot calculation (tries view first, falls back to sum)
# -------------------------
def compute_pot(sb, schema: str, payout_index: int) -> float:
    """
    Prefer v_contribution_pot if it exists; else sum contributions_legacy for payout_index.
    """
    # Try view (optional)
    pot_row = safe_single(sb, schema, "v_contribution_pot", "*")
    if pot_row:
        try:
            return float(pot_row.get("pot_amount") or 0.0)
        except Exception:
            pass

    # Fallback: sum contributions rows
    contrib_rows = fetch_rotation_contributions(sb, schema, payout_index)
    total = 0.0
    for r in contrib_rows:
        total += float(r.get("amount") or 0)
    return float(total)


# -------------------------
# Main UI (called from app.py)
# -------------------------
def render_payouts(sb_service, schema: str):
    st.header("Payouts (Njangi Rotation)")

    # ---------- Load members ----------
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

    # ---------- Load state ----------
    # Prefer current_season_view if it exists (optional)
    season = safe_single(sb_service, schema, "current_season_view", "*")
    state = safe_single(sb_service, schema, "app_state", "*", id=1)

    rotation_idx = int(season.get("next_payout_index") or state.get("next_payout_index") or 1)
    rotation_date = season.get("next_payout_date") or state.get("next_payout_date")
    start_id = int(state.get("rotation_start_index") or 1)

    # ---------- Compute pot ----------
    pot_amount = compute_pot(sb_service, schema, rotation_idx)

    # ---------- Find already-paid members for this payout_index ----------
    already_paid_ids: set[int] = set()
    payouts_rows = safe_select(sb_service, schema, "payouts_legacy", "member_id,payout_index", limit=20000)
    for p in payouts_rows:
        if int(p.get("payout_index") or -1) == int(rotation_idx):
            try:
                already_paid_ids.add(int(p.get("member_id")))
            except Exception:
                pass

    # ---------- Choose beneficiary ----------
    beneficiary_id = next_unpaid_beneficiary(active_ids, already_paid_ids, start_id)
    try:
        beneficiary_name = dfm.loc[dfm["id"] == beneficiary_id, "name"].iloc[0]
    except Exception:
        beneficiary_name = f"Member {beneficiary_id}"

    # ---------- Gates ----------
    gate1_ok, member_count, df_gate1 = validate_gate_1(active_members)

    contrib_rows = fetch_rotation_contributions(sb_service, schema, rotation_idx)
    summary_rows = build_contribution_summary(active_members, contrib_rows)
    gate2_ok, df_problems = validate_gate_2(summary_rows)
    gate3_ok = (float(pot_amount) > 0)

    # ---------- Display header KPIs ----------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rotation Index", str(rotation_idx))
    c2.metric("Pot Amount", f"{float(pot_amount):,.0f}")
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

    with st.expander("Gate 2: Contribution summary (this rotation)", expanded=False):
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)

    with st.expander("Gate 2: Contribution problems", expanded=False):
        if df_problems is None or df_problems.empty:
            st.success("No problems detected.")
        else:
            st.dataframe(df_problems, use_container_width=True)

    # ---------- Signatures (OPTIONAL enforcement) ----------
    st.subheader("Signatures (optional gate)")
    st.caption("If your signatures table is in use, you can enforce required roles before payout.")
    df_sig = get_signatures(sb_service, schema, "payout", int(beneficiary_id))
    if df_sig.empty:
        st.info("No signatures recorded (or signatures table not in use).")
    else:
        st.dataframe(df_sig, use_container_width=True)

    missing = missing_roles(df_sig, PAYOUT_SIG_REQUIRED) if not df_sig.empty else PAYOUT_SIG_REQUIRED
    enforce_signatures = st.toggle("Enforce signatures before payout", value=False)
    if enforce_signatures:
        if missing:
            st.warning("Missing roles: " + ", ".join(missing))
        else:
            st.success("All required signatures are present.")

    st.divider()

    # ---------- Execute payout ----------
    st.subheader("Execute payout")
    st.caption("This will insert into payouts_legacy and advance app_state.next_payout_index/date (+14 days).")

    if st.button("✅ Execute payout now (Service Key)", use_container_width=True):
        # Gate checks
        if enforce_signatures:
            if df_sig.empty:
                st.error("Payout blocked: No signatures present.")
                return
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

        # Log payout (payouts_legacy is expected; if missing, we still advance pointer)
        payout_payload = {
            "member_id": int(beneficiary_id),
            "member_name": str(beneficiary_name),
            "payout_amount": float(pot_amount),
            "payout_date": str(date.today()),
            "payout_index": int(rotation_idx),
            "created_at": now_iso(),
        }

        payout_logged = safe_insert(sb_service, schema, "payouts_legacy", payout_payload)

        # Advance pointer (bi-weekly)
        next_index = int(rotation_idx) + 1
        next_date = (date.today() + timedelta(days=14)).isoformat()

        safe_update(
            sb_service,
            schema,
            "app_state",
            {
                "next_payout_index": next_index,
                "next_payout_date": next_date,
                "updated_at": now_iso(),
            },
            where={"id": 1},
        )

        st.success(
            f"Payout executed for {beneficiary_name}. "
            f"Logged={payout_logged}. Next index={next_index}, next date={next_date}"
        )
        st.cache_data.clear()
        st.rerun()
