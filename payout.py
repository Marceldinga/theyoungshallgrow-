
# payout.py ✅ COMPLETE SINGLE CODE (NJANGI STANDARD — NO "legacy")
# ✅ Uses ONLY new tables:
#    - app_state (id=1, current_session_id, next_member_id, optional next_payout_date)
#    - sessions (session_id OR id, start_date, end_date)
#    - members (id, name)
#    - contributions (member_id, session_id, amount, paid_at, ...)
#    - payouts (session_id, member_id, payout_amount/payout_amount, payout_date, payout_index?, created_at)
#    - signatures (optional)
#
# ✅ Payout logic (Njangi standard):
#    - Beneficiary = app_state.next_member_id (1..17)
#    - Session = app_state.current_session_id (integer)
#    - Pot = SUM(contributions.amount) for current_session_id
#    - Prevents double payout for same session (checks payouts where session_id = current_session_id)
#    - Auto-advance after payout:
#         next_member_id: 1..17 wrap
#         current_session_id: +1
#         next_payout_date: +14 days from scheduled day if column exists
#    - Generates PDF receipt (contrib + signatures) and keeps it after rerun (st.session_state)

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Optional, Tuple, Set

import pandas as pd
import streamlit as st


# ============================================================
# CONFIG
# ============================================================
EXPECTED_ACTIVE_MEMBERS = 17
CYCLE_DAYS = 14
PAYOUT_SIG_REQUIRED = ["president", "beneficiary", "treasury"]


# ============================================================
# TIME
# ============================================================
def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_date_only(x: Any) -> Optional[date]:
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
# SUPABASE SAFE HELPERS
# ============================================================
def _safe_select_schema(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    filters: list[tuple[str, str, Any]] | None = None,
    order_col: str | None = None,
    desc: bool = True,
    limit: int = 2000,
) -> list[dict]:
    try:
        q = sb.schema(schema).table(table).select(cols)
        if filters:
            for col, op, val in filters:
                if val is None:
                    continue
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


def _table_exists(sb, schema: str, table: str) -> bool:
    try:
        sb.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def _infer_columns(sb, schema: str, table: str) -> set[str]:
    """Best-effort: infer columns from one row; empty set if table empty/unreadable."""
    try:
        rows = sb.schema(schema).table(table).select("*").limit(1).execute().data or []
        if not rows:
            return set()
        return set(rows[0].keys())
    except Exception:
        return set()


def _filter_payload_to_existing_columns(cols: set[str], payload: dict) -> dict:
    if not cols:
        return payload
    return {k: v for k, v in payload.items() if k in cols}


# ============================================================
# SESSION WINDOW (optional)
# ============================================================
def _sessions_pk_col(sb, schema: str) -> str:
    sample = _safe_select_schema(sb, schema, "sessions", "*", limit=1)
    if not sample:
        return "session_id"
    return "session_id" if "session_id" in sample[0] else "id"


def get_cycle_window(sb, schema: str, session_id: int) -> tuple[str, str]:
    """
    Uses sessions table if available, else falls back to [today-13, today].
    """
    if _table_exists(sb, schema, "sessions"):
        pk = _sessions_pk_col(sb, schema)
        rows = _safe_select_schema(sb, schema, "sessions", "*", filters=[(pk, "eq", int(session_id))], limit=1)
        if rows:
            r = rows[0]
            sd = r.get("start_date")
            ed = r.get("end_date")

            def _norm(x, end: bool = False) -> str:
                if isinstance(x, str):
                    return f"{x}T23:59:59" if end else f"{x}T00:00:00"
                if isinstance(x, date) and not isinstance(x, datetime):
                    return f"{x.isoformat()}T23:59:59" if end else f"{x.isoformat()}T00:00:00"
                if isinstance(x, datetime):
                    return x.replace(microsecond=0).isoformat()
                return ""

            start_iso = _norm(sd, end=False)
            end_iso = _norm(ed, end=True)
            if start_iso and end_iso:
                return start_iso, end_iso

    end_dt = datetime.utcnow().replace(microsecond=0)
    start_dt = end_dt - timedelta(days=CYCLE_DAYS - 1)
    return start_dt.isoformat(), end_dt.isoformat()


# ============================================================
# APP STATE
# ============================================================
def get_app_state(sb, schema: str) -> dict:
    rows = _safe_select_schema(sb, schema, "app_state", "*", filters=[("id", "eq", 1)], limit=1)
    return rows[0] if rows else {}


def ensure_app_state(sb, schema: str) -> dict:
    state = get_app_state(sb, schema)
    if state and any(v is not None for v in state.values()):
        return state

    cols = _infer_columns(sb, schema, "app_state")
    payload = {"id": 1, "updated_at": now_iso()}
    if "current_session_id" in cols:
        payload["current_session_id"] = 1
    if "next_member_id" in cols:
        payload["next_member_id"] = 1
    if "next_payout_date" in cols:
        payload["next_payout_date"] = date.today().isoformat()

    sb.schema(schema).table("app_state").upsert(payload, on_conflict="id").execute()
    return get_app_state(sb, schema)


def _advance_app_state(sb, schema: str, new_session_id: int, new_next_member: int):
    cols = _infer_columns(sb, schema, "app_state")
    payload = {"updated_at": now_iso()}

    if "current_session_id" in cols:
        payload["current_session_id"] = int(new_session_id)
    if "next_member_id" in cols:
        payload["next_member_id"] = int(new_next_member)

    # advance payout date if present
    if "next_payout_date" in cols:
        cur = get_app_state(sb, schema)
        base = _parse_date_only(cur.get("next_payout_date")) or date.today()
        payload["next_payout_date"] = (base + timedelta(days=CYCLE_DAYS)).isoformat()

    sb.schema(schema).table("app_state").update(payload).eq("id", 1).execute()


# ============================================================
# MEMBERS
# ============================================================
def load_members(sb, schema: str) -> pd.DataFrame:
    rows = _safe_select_schema(sb, schema, "members", "id,name", order_col="id", desc=False, limit=5000)
    df = pd.DataFrame(rows or [])
    if df.empty:
        return pd.DataFrame(columns=["id", "name"])
    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    df = df[df["id"] > 0].copy()
    df["name"] = df["name"].astype(str)
    return df


def member_name(df_members: pd.DataFrame, member_id: int) -> str:
    try:
        r = df_members.loc[df_members["id"] == int(member_id)]
        if not r.empty:
            return str(r.iloc[0].get("name") or "").strip()
    except Exception:
        pass
    return f"Member {int(member_id):02d}"


# ============================================================
# CONTRIBUTIONS (pot)
# ============================================================
def contributions_for_session(sb, schema: str, session_id: int) -> pd.DataFrame:
    rows = _safe_select_schema(
        sb, schema, "contributions",
        "member_id,session_id,amount,paid_at,created_at,note",
        filters=[("session_id", "eq", int(session_id))],
        order_col="created_at",
        desc=True,
        limit=20000,
    )
    df = pd.DataFrame(rows or [])
    if df.empty:
        return df
    df["amount"] = pd.to_numeric(df.get("amount", 0), errors="coerce").fillna(0.0)
    df["member_id"] = pd.to_numeric(df.get("member_id", 0), errors="coerce").fillna(0).astype(int)
    return df


def pot_total(df_contrib: pd.DataFrame) -> float:
    if df_contrib is None or df_contrib.empty:
        return 0.0
    return float(pd.to_numeric(df_contrib.get("amount", 0), errors="coerce").fillna(0.0).sum())


# ============================================================
# PAYOUTS (new table)
# ============================================================
def payout_exists_for_session(sb, schema: str, session_id: int) -> bool:
    rows = _safe_select_schema(sb, schema, "payouts", "id,session_id", filters=[("session_id", "eq", int(session_id))], limit=1)
    return bool(rows)


def insert_payout(sb, schema: str, payload: dict) -> dict:
    cols = _infer_columns(sb, schema, "payouts")
    payload2 = _filter_payload_to_existing_columns(cols, payload)
    res = sb.schema(schema).table("payouts").insert(payload2, returning="representation").execute()
    return (res.data or [None])[0] or payload2


# ============================================================
# SIGNATURES (optional)
# ============================================================
def signatures_exist(sb, schema: str) -> bool:
    return _table_exists(sb, schema, "signatures")


def get_signatures(sb, schema: str, entity_type: str, entity_id: int) -> list[dict]:
    if not signatures_exist(sb, schema):
        return []
    return _safe_select_schema(
        sb, schema, "signatures",
        "*",
        filters=[("entity_type", "eq", str(entity_type)), ("entity_id", "eq", int(entity_id))],
        order_col="signed_at",
        desc=True,
        limit=500,
    )


def missing_roles(sign_rows: list[dict], required_roles: list[str]) -> list[str]:
    got = {str(r.get("role", "")).strip().lower() for r in (sign_rows or []) if r.get("role")}
    req = [r.strip().lower() for r in required_roles]
    return [r for r in req if r not in got]


def insert_signature(sb, schema: str, entity_type: str, entity_id: int, role: str, signer_name: str, signer_member_id: int | None = None):
    if not signatures_exist(sb, schema):
        raise Exception("signatures table not found.")
    payload = {
        "entity_type": str(entity_type),
        "entity_id": int(entity_id),
        "role": str(role).strip().lower(),
        "signer_name": str(signer_name).strip(),
        "signed_at": now_iso(),
    }
    if signer_member_id is not None:
        payload["signer_member_id"] = int(signer_member_id)
    sb.schema(schema).table("signatures").insert(payload).execute()


# ============================================================
# PDF RECEIPT
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

    if dfc.empty:
        contrib_summary = pd.DataFrame({"member_id": [], "amount": []})
    else:
        dfc = dfc.copy()
        dfc["member_id"] = pd.to_numeric(dfc.get("member_id", 0), errors="coerce").fillna(-1).astype(int)
        dfc["amount_num"] = pd.to_numeric(dfc.get("amount", 0), errors="coerce").fillna(0.0).astype(float)
        contrib_summary = (
            dfc.groupby("member_id", as_index=False)["amount_num"]
            .sum()
            .rename(columns={"amount_num": "amount"})
        )

    if not dfm.empty and "id" in dfm.columns:
        dfm = dfm.copy()
        dfm["id"] = pd.to_numeric(dfm["id"], errors="coerce").fillna(-1).astype(int)
        dfm["name"] = dfm.get("name", "").astype(str)

        merged = dfm[["id", "name"]].merge(contrib_summary, how="left", left_on="id", right_on="member_id")
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
# UI ENTRYPOINT
# ============================================================
def render_payouts(sb_service, schema: str):
    st.title("Payouts — Njangi Standard")
    st.caption("Bi-weekly payout • Beneficiary = app_state.next_member_id • Session = app_state.current_session_id")

    # keep last PDF after rerun
    if st.session_state.get("last_payout_pdf") and st.session_state.get("last_payout_filename"):
        st.success("✅ Last payout receipt is ready (kept after refresh).")
        st.download_button(
            "⬇️ Download Last Payout Receipt (PDF)",
            data=st.session_state["last_payout_pdf"],
            file_name=st.session_state["last_payout_filename"],
            mime="application/pdf",
            use_container_width=True,
            key="dl_last_payout_pdf",
        )
        st.divider()

    if not _table_exists(sb_service, schema, "app_state"):
        st.error("Missing table: app_state")
        return
    if not _table_exists(sb_service, schema, "payouts"):
        st.error("Missing table: payouts")
        return
    if not _table_exists(sb_service, schema, "contributions"):
        st.error("Missing table: contributions")
        return
    if not _table_exists(sb_service, schema, "members"):
        st.error("Missing table: members")
        return

    state = ensure_app_state(sb_service, schema)
    session_id = state.get("current_session_id")
    next_member_id = state.get("next_member_id")

    if session_id is None:
        st.warning("app_state.current_session_id is not set. Set it in Admin → Rotation.")
        return
    if next_member_id is None:
        st.warning("app_state.next_member_id is not set. Set it in Admin → Rotation.")
        return

    session_id = int(session_id)
    next_member_id = int(next_member_id)

    dfm = load_members(sb_service, schema)
    if dfm.empty:
        st.error("members table is empty.")
        return

    beneficiary_name = member_name(dfm, next_member_id)

    dfc = contributions_for_session(sb_service, schema, session_id)
    pot = pot_total(dfc)

    # payout date gate if column exists
    payout_day = None
    if "next_payout_date" in _infer_columns(sb_service, schema, "app_state"):
        payout_day = _parse_date_only(state.get("next_payout_date"))

    today = date.today()
    allowed_by_date = True
    if payout_day is not None and today < payout_day:
        allowed_by_date = False

    already_paid = payout_exists_for_session(sb_service, schema, session_id)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Members", f"{len(dfm):,}")
    c2.metric("Session ID", str(session_id))
    c3.metric("Beneficiary ID", f"{next_member_id:02d}")
    c4.metric("Beneficiary", beneficiary_name)
    c5.metric("Pot total", f"{pot:,.0f}")

    if payout_day:
        st.info(f"📅 Payout day: {payout_day.isoformat()} • Today: {today.isoformat()} • Allowed: {'YES' if allowed_by_date else 'NO'}")
    else:
        st.info("📅 next_payout_date is not set/available — date restriction disabled.")

    if already_paid:
        st.warning("⚠️ This session_id already has a payout recorded. Double payout is blocked.")

    st.divider()

    # Signatures (optional)
    if signatures_exist(sb_service, schema):
        st.subheader("Signatures (optional)")
        sign_rows = get_signatures(sb_service, schema, "payout", session_id)
        missing = missing_roles(sign_rows, PAYOUT_SIG_REQUIRED)

        if not missing:
            st.success("All required payout signatures are present (for this session).")
        else:
            st.warning("Missing required signatures: " + ", ".join(missing))

        with st.expander("✍️ Add signature (for this session)", expanded=True):
            role = st.selectbox("Role", options=PAYOUT_SIG_REQUIRED, index=0)
            signer_name = st.text_input("Signer name", value="")
            signer_member_id = st.number_input("Signer member ID (optional)", min_value=0, step=1, value=0)

            if st.button("Add signature", use_container_width=True):
                if not signer_name.strip():
                    st.error("Signer name is required.")
                else:
                    try:
                        insert_signature(
                            sb_service, schema,
                            entity_type="payout",
                            entity_id=session_id,
                            role=str(role),
                            signer_name=signer_name.strip(),
                            signer_member_id=int(signer_member_id) if signer_member_id > 0 else None,
                        )
                        st.success(f"Signature recorded: {role} ✅")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    st.divider()

    # Execute payout
    force = st.checkbox("⚠️ Admin override (force payout)", value=False)

    can_execute = True
    reason_block = ""

    if pot <= 0:
        can_execute = False
        reason_block = "Pot total is 0 (no contributions recorded for this session)."
    elif already_paid:
        can_execute = False
        reason_block = "Payout already recorded for this session."
    elif not allowed_by_date and not force:
        can_execute = False
        reason_block = "Payout day restriction: not allowed yet."
    else:
        # signatures gate if signatures table exists
        if signatures_exist(sb_service, schema):
            sign_rows = get_signatures(sb_service, schema, "payout", session_id)
            missing = missing_roles(sign_rows, PAYOUT_SIG_REQUIRED)
            if missing and not force:
                can_execute = False
                reason_block = f"Missing signatures: {missing}"

    if not can_execute:
        st.error(reason_block)

    if st.button("✅ Execute Payout", disabled=(not can_execute), use_container_width=True):
        # Insert payout row
        payout_payload = {
            "session_id": int(session_id),
            "member_id": int(next_member_id),
            "payout_amount": float(pot),
            "payout_date": today.isoformat(),
            "payout_index": int(next_member_id),  # position == member id (Njangi standard)
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        payout_row = insert_payout(sb_service, schema, payout_payload)

        # Generate PDF
        sigs = get_signatures(sb_service, schema, "payout", session_id) if signatures_exist(sb_service, schema) else []
        pdf_bytes = build_payout_receipt_pdf(
            group_name="theyoungshallgrow",
            session_id=session_id,
            payout_day=(payout_day.isoformat() if payout_day else None),
            payout_date=today.isoformat(),
            beneficiary_id=next_member_id,
            beneficiary_name=beneficiary_name,
            contributions_df=dfc,
            members_df=dfm,
            signatures=sigs,
            total_paid=float(pot),
        )
        filename = f"payout_receipt_session_{session_id}_beneficiary_{next_member_id:02d}.pdf"
        st.session_state["last_payout_pdf"] = pdf_bytes
        st.session_state["last_payout_filename"] = filename

        # Advance state (next member wrap 17->1, session +1)
        new_next_member = 1 if next_member_id >= EXPECTED_ACTIVE_MEMBERS else (next_member_id + 1)
        new_session_id = session_id + 1
        _advance_app_state(sb_service, schema, new_session_id=new_session_id, new_next_member=new_next_member)

        # Refresh
        try:
            st.cache_data.clear()
            st.cache_resource.clear()
        except Exception:
            pass

        st.success("✅ Payout completed. State advanced to next cycle.")
        st.rerun()

    with st.expander("Debug", expanded=False):
        st.write("app_state:", state)
        st.write("payout_row:", payout_row if "payout_row" in locals() else None)
        st.write("cycle_window:", get_cycle_window(sb_service, schema, session_id))
        st.write("contributions_rows:", len(dfc) if dfc is not None else 0)
