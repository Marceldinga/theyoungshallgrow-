# njangi_llm_panel.py
# ==============================================================================
# 👩🏾‍💼 YOUNG — NJANGI “DASHBOARD COPILOT” (SMART • GROUNDED • MODERN)
# ------------------------------------------------------------------------------
# ✅ Single-file module (drop-in)
# ✅ NJANGI STANDARD tables (NO legacy)
# ✅ Safe for Railway / Streamlit Cloud
# ✅ Accepts: sb_anon / sb_service / schema (matches your app.py)
#
# ✅ What’s new (advanced “modern” feature):
#   1) **Young** (assistant persona) runs as a **chat copilot** on the dashboard
#   2) Loads ALL key data snapshots (members, sessions, loans, payments, interest,
#      contributions, foundation, fines, attendance, minutes, payouts/app_state, signatures)
#   3) Answers “almost any question” with:
#      - Intent + slot detection (member, loan_id, session_id)
#      - Generic dataframe QA (totals, counts, top lists, overdue, summaries)
#      - Smart insights / alerts (overdue loans, missing contributors, anomalies)
#   4) Optional Internet search (Tavily) with **privacy guard**:
#      - Never web-search member/finance questions unless you explicitly force it
#
# ✅ ML training (XGBoost, NO sklearn required):
#   - label=1 for active loans, 0 for closed loans
#   - tiny-data safe split + fallback train-on-all
# ==============================================================================

from __future__ import annotations

import math
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st


# ==============================================================================
# Helpers (safe + formatting)
# ==============================================================================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _greeting() -> str:
    # Streamlit runs server-side; use local time best-effort
    h = datetime.now().hour
    if h < 12:
        return "Good morning"
    if h < 18:
        return "Good afternoon"
    return "Good evening"


def _normalize_text(s: str) -> str:
    return " ".join(str(s or "").lower().strip().split())


def _money(x: float) -> str:
    try:
        return f"${float(x):,.0f}"
    except Exception:
        return "$0"


def _safe_sum(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())


def _safe_count(df: pd.DataFrame) -> int:
    return int(len(df)) if df is not None and not df.empty else 0


def _to_numeric_cols(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


def _parse_dt(s) -> pd.Timestamp | None:
    try:
        if s is None or str(s).strip() == "":
            return None
        dt = pd.to_datetime(s, errors="coerce", utc=True)
        if pd.isna(dt):
            return None
        return dt
    except Exception:
        return None


def _days_since(ts: pd.Timestamp | None) -> float:
    if ts is None:
        return float("nan")
    now = pd.Timestamp.now(tz="UTC")
    return float((now - ts).total_seconds() / 86400.0)


def _bce_loss(y_true, y_prob, eps: float = 1e-9) -> float:
    n = len(y_true)
    if n == 0:
        return float("nan")
    s = 0.0
    for yt, yp in zip(y_true, y_prob):
        yp = max(eps, min(1.0 - eps, float(yp)))
        s += -(yt * math.log(yp) + (1 - yt) * math.log(1 - yp))
    return s / n


def _norm_status(x) -> str:
    s = str(x or "").strip().lower()
    if s in ("active", "open", "running", "current"):
        return "active"
    if s in ("closed", "paid", "completed", "settled", "done"):
        return "closed"
    if s in ("overdue", "late", "default", "delinquent"):
        return "overdue"
    return s or "unknown"


def _try_read(sb, schema: str, table: str, cols: str = "*", limit: int = 2000, order_by: str | None = None, desc: bool = True):
    """Safe supabase read; returns list[dict]."""
    if sb is None:
        return []
    q = sb.schema(schema).table(table).select(cols)
    if order_by:
        q = q.order(order_by, desc=desc)
    if limit:
        q = q.limit(int(limit))
    try:
        return (q.execute().data or [])
    except Exception:
        # fallback: try '*' if column list fails
        try:
            q2 = sb.schema(schema).table(table).select("*").limit(int(limit))
            if order_by:
                q2 = q2.order(order_by, desc=desc)
            return (q2.execute().data or [])
        except Exception:
            return []


# ==============================================================================
# “Young” persona + welcome
# ==============================================================================
def _young_intro() -> str:
    return (
        f"{_greeting()} 👋🏾 I’m **Young** — your **Njangi Dashboard Copilot** for **theyoungshallgrow**.\n\n"
        "I’m grounded on **your Supabase data** (members, sessions, contributions, loans, payments, interest, fines, attendance, minutes, payouts).\n\n"
        "Ask me anything like:\n"
        "• *Loans summary* / *Active loans* / *Overdue loans*\n"
        "• *Contribution summary* / *Who hasn’t paid this session?*\n"
        "• *Foundation total* / *Interest collected this month*\n"
        "• *Fines summary* / *Attendance vs fines*\n"
        "• *Risk for Donald* / *Top 5 risky members*\n"
        "• *Show member Marcel loans* / *loan 12 status*\n\n"
        "If you want Internet help, say **web:** at the start (example: `web: Maryland cosmetology license requirements`)."
    )


def _young_welcome_card(project_hint: str = "") -> None:
    st.markdown(
        f"""
        <div style="padding:14px;border-radius:16px;border:1px solid rgba(255,255,255,.12);
                    background:rgba(255,255,255,.04);">
          <div style="font-size:18px;font-weight:700;">👩🏾‍💼 Young is online</div>
          <div style="opacity:.9;margin-top:6px;">
            { _greeting() } — I’m watching your Njangi data and ready to answer questions.
            <br/>{project_hint}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ==============================================================================
# Data Hub (loads “all data” snapshots Young can use)
# - no st.cache_data with sb client; use session_state in-memory cache
# ==============================================================================
TABLE_SPECS = [
    # name, columns (best-effort), order_by, numeric_cols
    ("members", "id,name,display_name,phone,created_at", "id", []),
    ("sessions", "id,session_date,cycle_index,title,created_at", "created_at", []),
    ("contributions", "id,member_id,session_id,amount,paid_at,created_at", "created_at", ["amount"]),
    ("foundation_contributions", "id,member_id,session_id,amount,paid_at,created_at", "created_at", ["amount"]),
    ("loans", "id,member_id,principal,principal_current,total_due,unpaid_interest,last_paid_at,status,borrow_date,due_cycle_days,interest_rate_monthly,created_at", "created_at",
     ["principal", "principal_current", "total_due", "unpaid_interest", "due_cycle_days", "interest_rate_monthly"]),
    ("loan_payments", "id,loan_id,member_id,amount,paid_at,created_at", "created_at", ["amount"]),
    ("interest_ledger", "id,loan_id,member_id,amount,posted_at,created_at", "created_at", ["amount"]),
    ("fines", "*", "created_at", ["amount"]),
    ("attendance", "*", "created_at", []),
    ("minutes", "*", "created_at", []),
    ("app_state", "*", "created_at", []),
    ("signatures", "*", "created_at", []),
]


def _hub_key(schema: str, table: str) -> str:
    return f"hub::{schema}::{table}"


def _load_hub(sb_read, schema: str, slow_mode: bool = True, limit: int = 5000) -> dict[str, pd.DataFrame]:
    """
    Returns dict of {table_name: df}.
    Uses st.session_state as a simple cache. Refresh clears this cache.
    """
    if "young_hub_cache" not in st.session_state:
        st.session_state["young_hub_cache"] = {}

    cache: dict[str, pd.DataFrame] = st.session_state["young_hub_cache"]
    out: dict[str, pd.DataFrame] = {}

    for (table, cols, order_by, num_cols) in TABLE_SPECS:
        key = _hub_key(schema, table)
        if key in cache:
            df = cache[key]
            out[table] = df
            continue

        if slow_mode:
            time.sleep(0.10)

        rows = _try_read(sb_read, schema, table, cols=cols, limit=limit, order_by=order_by, desc=True)
        df = pd.DataFrame(rows or [])
        if not df.empty:
            # normalize
            if table == "loans":
                df["status_norm"] = df.get("status", "").apply(_norm_status)
            df = _to_numeric_cols(df, num_cols)

        cache[key] = df
        out[table] = df

    return out


def _clear_hub(schema: str):
    if "young_hub_cache" not in st.session_state:
        return
    cache: dict[str, Any] = st.session_state["young_hub_cache"]
    for table, *_ in TABLE_SPECS:
        cache.pop(_hub_key(schema, table), None)


# ==============================================================================
# Slot detection (member / loan_id / session_id)
# ==============================================================================
def _build_member_labels(members_df: pd.DataFrame) -> pd.DataFrame:
    if members_df is None or members_df.empty:
        return pd.DataFrame()

    m = members_df.copy()
    if "display_name" in m.columns:
        m["member_name"] = m["display_name"].fillna("").astype(str)
        m.loc[m["member_name"].str.strip() == "", "member_name"] = m.get("name", "").astype(str)
    else:
        m["member_name"] = m.get("name", "").astype(str)
    m["member_name_norm"] = m["member_name"].fillna("").astype(str).map(_normalize_text)
    return m


def _pick_member_from_question(question: str, members_df: pd.DataFrame) -> tuple[int | None, str | None]:
    if members_df is None or members_df.empty:
        return None, None

    q = _normalize_text(question)
    if not q:
        return None, None

    m = _build_member_labels(members_df)
    if m.empty or "id" not in m.columns:
        return None, None

    # longest-name-first contains match
    candidates = []
    for _, r in m.iterrows():
        try:
            mid = int(r["id"])
        except Exception:
            continue
        name = str(r.get("member_name", "")).strip()
        name_norm = str(r.get("member_name_norm", "")).strip()
        if not name_norm:
            continue
        candidates.append((len(name_norm), mid, name, name_norm))
    candidates.sort(reverse=True, key=lambda t: t[0])

    for _, mid, name, name_norm in candidates:
        if name_norm in q:
            return mid, name
        toks = [t for t in name_norm.split() if len(t) >= 3]
        if toks and all(t in q for t in toks):
            return mid, name

    return None, None


def _pick_int_from_text(question: str, label: str) -> int | None:
    """
    Finds patterns like:
      loan 12, loan_id=12, session 5, session_id 5
    """
    q = _normalize_text(question)
    m = re.search(rf"{re.escape(label)}\s*[:=#]?\s*(\d+)", q)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


# ==============================================================================
# Intent detection (expanded)
# ==============================================================================
def _detect_intent(question: str) -> str:
    q = _normalize_text(question)

    if q.startswith("web:") or q.startswith("internet:") or q.startswith("tavily:"):
        return "web"

    if any(k in q for k in ["introduce", "who are you", "your name", "young"]):
        return "intro"
    if any(k in q for k in ["help", "what can you do", "commands", "examples"]):
        return "help"

    if any(k in q for k in ["overdue", "late", "delinquent", "default"]):
        return "overdue"
    if any(k in q for k in ["risk", "score", "risky"]):
        return "risk"

    if any(k in q for k in ["loan", "borrow", "interest", "principal", "balance"]):
        return "loans"
    if any(k in q for k in ["payment", "repay", "paid back", "loan payment"]):
        return "loan_payments"
    if any(k in q for k in ["interest ledger", "interest collected", "interest paid"]):
        return "interest"

    if any(k in q for k in ["contribution", "contrib", "deposit", "paid", "payment in"]):
        return "contributions"
    if any(k in q for k in ["foundation"]):
        return "foundation"

    if any(k in q for k in ["fine", "penalty"]):
        return "fines"
    if any(k in q for k in ["attendance", "present", "absent"]):
        return "attendance"
    if any(k in q for k in ["minutes", "meeting"]):
        return "minutes"
    if any(k in q for k in ["payout", "rotation", "who is next", "next payout"]):
        return "payouts"
    if any(k in q for k in ["session", "cycle"]):
        return "sessions"

    # generic questions over data
    if any(k in q for k in ["total", "sum", "count", "how many", "top", "list", "show", "latest", "recent", "average", "max", "min"]):
        return "generic"

    return "unknown"


# ==============================================================================
# Generic dataframe QA (smart fallback)
# ==============================================================================
def _df_best_date_col(df: pd.DataFrame) -> str | None:
    for c in ["paid_at", "posted_at", "session_date", "borrow_date", "last_paid_at", "created_at", "updated_at", "date"]:
        if c in df.columns:
            return c
    return None


def _df_top_n(df: pd.DataFrame, col: str, n: int = 10, asc: bool = False) -> pd.DataFrame:
    if df is None or df.empty or col not in df.columns:
        return pd.DataFrame()
    s = pd.to_numeric(df[col], errors="coerce")
    tmp = df.copy()
    tmp["_sort"] = s
    tmp = tmp.dropna(subset=["_sort"])
    tmp = tmp.sort_values("_sort", ascending=asc).drop(columns=["_sort"])
    return tmp.head(n)


def _maybe_filter_member(df: pd.DataFrame, member_id: int | None) -> pd.DataFrame:
    if df is None or df.empty or member_id is None:
        return df
    for c in ["member_id", "member", "memberid"]:
        if c in df.columns:
            return df[df[c].astype(str) == str(member_id)].copy()
    return df


def _who_label(member_id: int | None, member_name: str | None) -> str:
    if member_id is not None and member_name:
        return f"**{member_name}**"
    if member_id is not None:
        return f"**member_id={member_id}**"
    return "**All members**"


def _answer_generic(question: str, hub: dict[str, pd.DataFrame], member_id: int | None, member_name: str | None) -> tuple[str, pd.DataFrame | None]:
    """
    Returns (text_answer, optional_dataframe_to_show)
    """
    q = _normalize_text(question)
    who = _who_label(member_id, member_name)

    # choose a target table by keywords
    table = None
    if any(k in q for k in ["loan"]):
        table = "loans"
    elif any(k in q for k in ["contrib"]):
        table = "contributions"
    elif "foundation" in q:
        table = "foundation_contributions"
    elif "fine" in q:
        table = "fines"
    elif any(k in q for k in ["attendance", "present", "absent"]):
        table = "attendance"
    elif "minutes" in q:
        table = "minutes"
    elif any(k in q for k in ["interest"]):
        table = "interest_ledger"
    elif "payment" in q:
        table = "loan_payments"
    elif "session" in q or "cycle" in q:
        table = "sessions"
    else:
        # default: try loans first (most asked)
        table = "loans"

    df = hub.get(table, pd.DataFrame())
    df = _maybe_filter_member(df, member_id)

    if df is None or df.empty:
        return (f"I couldn’t find data in `{table}` for {who}. If the table is empty or not in your DB, I’ll still answer other questions.", None)

    # totals
    if any(k in q for k in ["total", "sum"]) and any(k in q for k in ["amount", "contrib", "foundation", "fine", "interest", "payment"]):
        amt_col = None
        for c in ["amount", "paid_amount", "value"]:
            if c in df.columns:
                amt_col = c
                break
        if amt_col:
            total = _safe_sum(df, amt_col)
            return (f"For {who} in `{table}`: total **{amt_col}** = **{_money(total)}** (rows={len(df):,}).", None)

    # counts
    if any(k in q for k in ["count", "how many", "rows"]):
        return (f"For {who} in `{table}`: I see **{len(df):,}** rows.", None)

    # top N by amount/principal/balance
    if "top" in q or "highest" in q:
        n = 10
        m = re.search(r"top\s+(\d+)", q)
        if m:
            try:
                n = max(1, min(50, int(m.group(1))))
            except Exception:
                n = 10

        candidate_cols = [c for c in ["amount", "principal_current", "principal", "total_due", "unpaid_interest"] if c in df.columns]
        if not candidate_cols:
            return (f"I can list top items, but `{table}` has no numeric columns like amount/principal/balance.", None)
        col = candidate_cols[0]
        top_df = _df_top_n(df, col, n=n, asc=False)
        return (f"Top **{n}** rows in `{table}` for {who} by **{col}**:", top_df)

    # latest / recent
    if any(k in q for k in ["latest", "recent", "last"]):
        dcol = _df_best_date_col(df)
        if not dcol:
            return (f"I can’t find a date column in `{table}` to sort by recent.", None)
        tmp = df.copy()
        tmp["_dt"] = pd.to_datetime(tmp[dcol], errors="coerce", utc=True)
        tmp = tmp.dropna(subset=["_dt"]).sort_values("_dt", ascending=False).drop(columns=["_dt"])
        return (f"Most recent rows in `{table}` for {who} (sorted by `{dcol}`):", tmp.head(15))

    # list / show
    if any(k in q for k in ["list", "show"]):
        return (f"Here are rows from `{table}` for {who}:", df.head(25))

    # fallback summary
    cols_preview = ", ".join(list(df.columns)[:12]) + (" ..." if len(df.columns) > 12 else "")
    return (f"I can help more if you say **total / count / top / latest / list**. `{table}` columns I see: {cols_preview}", None)


# ==============================================================================
# Grounded answers (specialized routes)
# ==============================================================================
def _answer_grounded(question: str, hub: dict[str, pd.DataFrame], selected_member_id: int | None, selected_member_label: str | None, loan_filter: str) -> tuple[str, pd.DataFrame | None]:
    qraw = question.strip()
    if not qraw:
        return ("Please type a question.", None)

    intent = _detect_intent(qraw)

    members_df = hub.get("members", pd.DataFrame())
    loans_df = hub.get("loans", pd.DataFrame())
    contrib_df = hub.get("contributions", pd.DataFrame())
    foundation_df = hub.get("foundation_contributions", pd.DataFrame())
    fines_df = hub.get("fines", pd.DataFrame())
    pay_df = hub.get("loan_payments", pd.DataFrame())
    interest_df = hub.get("interest_ledger", pd.DataFrame())
    sessions_df = hub.get("sessions", pd.DataFrame())

    # slot: member from question overrides UI
    q_member_id, q_member_name = _pick_member_from_question(qraw, members_df)
    member_id = q_member_id if q_member_id is not None else selected_member_id
    member_name = q_member_name if q_member_name is not None else selected_member_label
    who = _who_label(member_id, member_name)

    # slot: ids
    loan_id = _pick_int_from_text(qraw, "loan")
    session_id = _pick_int_from_text(qraw, "session")

    # slot: loan filter from question overrides UI
    qn = _normalize_text(qraw)
    status_filter = loan_filter
    if "active" in qn:
        status_filter = "Active"
    elif "closed" in qn or "paid" in qn or "completed" in qn:
        status_filter = "Closed"
    elif "all" in qn:
        status_filter = "All"

    if intent == "intro":
        return (_young_intro(), None)

    if intent == "help":
        return (
            "Here are examples you can ask Young:\n"
            "• **Loans summary** / **Active loans** / **Overdue loans**\n"
            "• **Contribution summary** / **Who hasn’t paid this session?**\n"
            "• **Foundation total** / **Interest collected this month**\n"
            "• **Fines summary**\n"
            "• **Risk for Donald** / **Top 5 risky members**\n"
            "• **Show latest payments**\n"
            "• **session 12 summary**\n\n"
            "Internet help:\n"
            "• Start your question with **web:** to force an Internet search.\n",
            None,
        )

    # Member-scoped frames
    mc = _maybe_filter_member(contrib_df, member_id)
    mf = _maybe_filter_member(fines_df, member_id)
    mfd = _maybe_filter_member(foundation_df, member_id)
    ml_all = _maybe_filter_member(loans_df, member_id)
    mpay = _maybe_filter_member(pay_df, member_id)
    mint = _maybe_filter_member(interest_df, member_id)

    # normalize + filter loans
    ml = ml_all.copy() if ml_all is not None else pd.DataFrame()
    if ml is not None and not ml.empty:
        if "status_norm" not in ml.columns:
            ml["status_norm"] = ml.get("status", "").apply(_norm_status)
        if status_filter == "Active":
            ml = ml[ml["status_norm"] == "active"].copy()
        elif status_filter == "Closed":
            ml = ml[ml["status_norm"] == "closed"].copy()

    # loan by id
    if loan_id is not None and not loans_df.empty and "id" in loans_df.columns:
        one = loans_df[loans_df["id"].astype(str) == str(loan_id)].copy()
        if one.empty:
            return (f"I couldn’t find **loan {loan_id}** in your loans table.", None)
        return (f"Here is **loan {loan_id}**:", one.head(1))

    # session by id
    if session_id is not None:
        # attempt “session summary”
        c_sess = contrib_df.copy() if contrib_df is not None else pd.DataFrame()
        f_sess = foundation_df.copy() if foundation_df is not None else pd.DataFrame()
        if not c_sess.empty and "session_id" in c_sess.columns:
            c_sess = c_sess[c_sess["session_id"].astype(str) == str(session_id)]
        if not f_sess.empty and "session_id" in f_sess.columns:
            f_sess = f_sess[f_sess["session_id"].astype(str) == str(session_id)]

        contrib_total = _safe_sum(c_sess, "amount")
        foundation_total = _safe_sum(f_sess, "amount")
        rows_c = _safe_count(c_sess)
        rows_f = _safe_count(f_sess)

        # list missing contributors if sessions exist + contributions exist
        missing_note = ""
        if not members_df.empty and not c_sess.empty and "member_id" in c_sess.columns:
            paid_ids = set(c_sess["member_id"].astype(str).tolist())
            m = _build_member_labels(members_df)
            if "id" in m.columns:
                all_ids = m["id"].astype(str).tolist()
                missing_ids = [i for i in all_ids if i not in paid_ids]
                if missing_ids:
                    miss_names = m[m["id"].astype(str).isin(missing_ids)]["member_name"].tolist()
                    missing_note = "Missing contributors (based on contributions rows): " + ", ".join(miss_names[:20]) + (" …" if len(miss_names) > 20 else "")

        sess_title = ""
        if not sessions_df.empty and "id" in sessions_df.columns:
            srow = sessions_df[sessions_df["id"].astype(str) == str(session_id)]
            if not srow.empty:
                sess_title = f" — {str(srow.iloc[0].get('title') or srow.iloc[0].get('session_date') or '').strip()}"

        return (
            f"Session **{session_id}** summary{sess_title}:\n"
            f"• Contributions total: **{_money(contrib_total)}** (rows={rows_c:,})\n"
            f"• Foundation total: **{_money(foundation_total)}** (rows={rows_f:,})\n"
            f"{('• ' + missing_note) if missing_note else ''}",
            None,
        )

    # Specialized intents
    if intent == "contributions":
        total_contrib = _safe_sum(mc, "amount")
        return (
            f"Contribution summary for {who}:\n"
            f"• Total contributed: **{_money(total_contrib)}**\n"
            f"• Contribution rows: **{_safe_count(mc):,}**\n\n"
            "Rule reminder: contributions should be **multiples of 500** (your system rule).",
            None,
        )

    if intent == "foundation":
        total_f = _safe_sum(mfd, "amount")
        return (
            f"Foundation summary for {who}:\n"
            f"• Total foundation contributed: **{_money(total_f)}**\n"
            f"• Foundation rows: **{_safe_count(mfd):,}**",
            None,
        )

    if intent == "loans":
        principal = _safe_sum(ml, "principal")
        bal = _safe_sum(ml, "principal_current")
        unpaid_int = _safe_sum(ml, "unpaid_interest")
        loan_count = _safe_count(ml)

        breakdown = ""
        if not loans_df.empty and "status_norm" in loans_df.columns:
            src = loans_df.copy()
            if member_id is not None and "member_id" in src.columns:
                src = src[src["member_id"].astype(str) == str(member_id)]
            vc = src["status_norm"].value_counts().to_dict()
            if vc:
                breakdown = "Status counts: " + ", ".join([f"{k}={int(v)}" for k, v in vc.items()])

        return (
            f"Loans summary for {who} (filter: **{status_filter}**):\n"
            f"• Loan rows: **{loan_count:,}**\n"
            f"• Total principal: **{_money(principal)}**\n"
            f"• Total balance (principal_current): **{_money(bal)}**\n"
            f"• Unpaid interest: **{_money(unpaid_int)}**\n"
            f"{('• ' + breakdown) if breakdown else ''}\n\n"
            "Tip: If **last_paid_at** is > 30 days on active loans, follow up this session.",
            None,
        )

    if intent == "loan_payments":
        if mpay is None or mpay.empty:
            return (f"I don’t see loan payment rows for {who}.", None)
        return (f"Here are the most recent loan payments for {who}:", mpay.head(20))

    if intent == "interest":
        total_i = _safe_sum(mint, "amount")
        if mint is None or mint.empty:
            return (f"I don’t see interest ledger rows for {who}.", None)
        return (
            f"Interest summary for {who}:\n"
            f"• Total interest recorded: **{_money(total_i)}**\n"
            f"• Rows: **{len(mint):,}**\n\n"
            "Tip: Interest ledger is best as the single source of truth for interest reporting.",
            mint.head(20),
        )

    if intent == "fines":
        fines_total = _safe_sum(mf, "amount") if (mf is not None and not mf.empty and "amount" in mf.columns) else float(_safe_count(mf))
        return (
            f"Fines summary for {who}:\n"
            f"• Fine records: **{_safe_count(mf):,}**\n"
            f"• Total fines: **{_money(fines_total)}**",
            None,
        )

    if intent == "overdue":
        if ml_all is None or ml_all.empty:
            return (f"I can’t see loans for {who}.", None)
        tmp = ml_all.copy()
        if "status_norm" not in tmp.columns:
            tmp["status_norm"] = tmp.get("status", "").apply(_norm_status)
        od = tmp[tmp["status_norm"].isin(["overdue", "late", "default", "delinquent"])].copy()
        if od.empty:
            # heuristic overdue: active + last_paid_at old
            if "last_paid_at" in tmp.columns:
                t2 = tmp[tmp["status_norm"] == "active"].copy()
                t2["_lp"] = pd.to_datetime(t2["last_paid_at"], errors="coerce", utc=True)
                t2["_days"] = (pd.Timestamp.now(tz="UTC") - t2["_lp"]).dt.total_seconds() / 86400.0
                od = t2[t2["_days"] > 30].drop(columns=["_lp", "_days"], errors="ignore")
        if od.empty:
            return (f"I don’t see overdue signals for {who} right now.", None)
        show_cols = [c for c in ["id", "member_id", "principal_current", "unpaid_interest", "last_paid_at", "status", "status_norm"] if c in od.columns]
        return (f"Overdue / late loans for {who}:", od[show_cols].head(25) if show_cols else od.head(25))

    if intent == "risk":
        # Hybrid: heuristic risk (always available)
        total_contrib = _safe_sum(mc, "amount")
        bal = _safe_sum(ml_all, "principal_current")
        unpaid_int = _safe_sum(ml_all, "unpaid_interest")
        fines_total = _safe_sum(mf, "amount") if (mf is not None and not mf.empty and "amount" in mf.columns) else 0.0

        risk = 0
        if unpaid_int > 0:
            risk += 35
        if bal > 0 and total_contrib == 0:
            risk += 25
        if ml_all is not None and not ml_all.empty:
            tmp = ml_all.copy()
            if "status_norm" not in tmp.columns:
                tmp["status_norm"] = tmp.get("status", "").apply(_norm_status)
            if tmp["status_norm"].astype(str).str.contains("overdue|default|delinquent|late", case=False, na=False).any():
                risk += 45
            # last_paid_at age
            if "last_paid_at" in tmp.columns:
                lp = pd.to_datetime(tmp["last_paid_at"], errors="coerce", utc=True)
                if lp.notna().any():
                    age = (pd.Timestamp.now(tz="UTC") - lp.max()).total_seconds() / 86400.0
                    if age > 45:
                        risk += 10
        if fines_total > 0:
            risk += 10
        risk = min(100, risk)

        return (
            f"Risk view for {who} (from current DB snapshot):\n"
            f"• Balance: **{_money(bal)}** • Unpaid interest: **{_money(unpaid_int)}** • Fines: **{_money(fines_total)}**\n"
            f"• Quick risk score (heuristic): **{risk}/100**\n\n"
            "If you want model-based risk, run **Training (XGBoost)** below and I’ll show top-risk members.",
            None,
        )

    if intent == "sessions":
        if sessions_df is None or sessions_df.empty:
            return ("I don’t see a `sessions` table (or it’s empty).", None)
        return ("Here are recent sessions:", sessions_df.head(20))

    if intent == "payouts":
        app_state = hub.get("app_state", pd.DataFrame())
        if app_state is None or app_state.empty:
            return (
                "Payout guidance:\n"
                "• Track payout rotation index in **app_state** (example: `next_payout_index`)\n"
                "• Validate eligibility using contribution completeness\n"
                "• Export payout receipts for audit",
                None,
            )
        return ("Here is your `app_state` snapshot (look for payout rotation fields):", app_state.head(50))

    if intent == "minutes":
        minutes_df = hub.get("minutes", pd.DataFrame())
        if minutes_df is None or minutes_df.empty:
            return (
                "Minutes guidance:\n"
                "• Store minutes per **session_id** for traceability\n"
                "• End-of-session summary: decisions, loans approved, payouts, attendance\n",
                None,
            )
        return ("Here are recent minutes rows:", minutes_df.head(20))

    if intent == "attendance":
        att = hub.get("attendance", pd.DataFrame())
        if att is None or att.empty:
            return (
                "Attendance guidance:\n"
                "• Track attendance per **session_id**\n"
                "• If your rules allow: derive fines from absences/late arrivals\n",
                None,
            )
        return ("Here are recent attendance rows:", att.head(25))

    if intent == "generic":
        return _answer_generic(qraw, hub, member_id, member_name)

    return (
        "Ask me using these patterns:\n"
        "• **total / count / top / latest / list** + (loans / contributions / fines / foundation / interest / payments)\n"
        "Examples:\n"
        "• `top 5 members by unpaid_interest`\n"
        "• `latest contributions`\n"
        "• `count active loans`\n"
        "Or type **help**.",
        None,
    )


# ==============================================================================
# Internet Search (Tavily) — optional (privacy guarded)
# ==============================================================================
def _has_tavily_key() -> bool:
    return bool(os.getenv("TAVILY_API_KEY", "").strip())


@st.cache_data(ttl=3600, show_spinner=False)
def _tavily_search_cached(query: str, search_depth: str = "basic", max_results: int = 5) -> dict:
    import requests

    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return {"error": "Missing TAVILY_API_KEY in environment variables."}

    url = "https://api.tavily.com/search"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {"query": query, "search_depth": search_depth, "max_results": int(max_results)}

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code != 200:
            return {"error": f"Tavily error {r.status_code}: {r.text[:400]}"}
        j = r.json()
        return j if isinstance(j, dict) else {"raw": r.text}
    except Exception as e:
        return {"error": f"Request failed: {repr(e)}"}


def _format_web_answer(tav: dict) -> tuple[str, list[dict]]:
    if not isinstance(tav, dict):
        return ("I couldn’t read the web results.", [])
    if "error" in tav:
        return (f"Internet search failed: {tav['error']}", [])

    results = tav.get("results", []) or []
    if not results:
        return ("I searched the web but didn’t find clear results. Try rephrasing.", [])

    bullets, sources = [], []
    for r in results[:5]:
        title = str(r.get("title", "") or "").strip()
        url = str(r.get("url", "") or "").strip()
        content = str(r.get("content", "") or "").strip()
        score = r.get("score", None)

        if content:
            bullets.append(f"• {content[:230].rstrip()}…")
        sources.append({"title": title, "url": url, "score": score})

    summary = "Here’s what I found online (top results):\n" + "\n".join(bullets[:3])
    return (summary, sources)


def _privacy_block_web(question: str) -> bool:
    """
    Privacy guard: block web if question looks like Njangi finance/member data.
    Allow if user forces with `web:`
    """
    q = _normalize_text(question)
    if q.startswith("web:") or q.startswith("internet:") or q.startswith("tavily:"):
        return False
    sensitive_keywords = [
        "member", "members", "contribution", "contrib", "loan", "loans", "fine", "fines",
        "payout", "attendance", "minutes", "principal", "balance", "unpaid", "interest",
        "session", "theyoungshallgrow", "njangi",
        # and common IDs patterns
        "member_id", "loan_id", "session_id",
    ]
    return any(k in q for k in sensitive_keywords)


# ==============================================================================
# ML: training dataset + XGBoost
# ==============================================================================
def _build_training_frame(hub: dict[str, pd.DataFrame]) -> pd.DataFrame:
    loans_df = hub.get("loans", pd.DataFrame())
    members_df = hub.get("members", pd.DataFrame())
    contrib_df = hub.get("contributions", pd.DataFrame())
    fines_df = hub.get("fines", pd.DataFrame())

    if loans_df is None or loans_df.empty:
        return pd.DataFrame()

    df = loans_df.copy()
    if "status_norm" not in df.columns:
        df["status_norm"] = df.get("status", "").apply(_norm_status)

    df = df[df["status_norm"].isin(["active", "closed"])].copy()
    if df.empty:
        return pd.DataFrame()

    df["y"] = (df["status_norm"] == "active").astype(int)

    df["last_paid_dt"] = df.get("last_paid_at", None).apply(_parse_dt) if "last_paid_at" in df.columns else None
    df["created_dt"] = df.get("created_at", None).apply(_parse_dt) if "created_at" in df.columns else None

    def _ds(row):
        d = row.get("last_paid_dt")
        if d is None:
            d = row.get("created_dt")
        return _days_since(d)

    df["days_since_last_paid"] = df.apply(_ds, axis=1)
    df["days_since_last_paid"] = pd.to_numeric(df["days_since_last_paid"], errors="coerce")
    med = float(df["days_since_last_paid"].median()) if df["days_since_last_paid"].notna().any() else 0.0
    df["days_since_last_paid"] = df["days_since_last_paid"].fillna(med)

    df = _to_numeric_cols(df, ["principal", "principal_current", "total_due", "unpaid_interest"])

    if contrib_df is not None and not contrib_df.empty and "member_id" in contrib_df.columns:
        c = _to_numeric_cols(contrib_df.copy(), ["amount"])
        contrib_tot = c.groupby("member_id", dropna=False)["amount"].sum().reset_index().rename(columns={"amount": "member_contrib_total"})
    else:
        contrib_tot = pd.DataFrame(columns=["member_id", "member_contrib_total"])

    if fines_df is not None and not fines_df.empty and "member_id" in fines_df.columns:
        f = fines_df.copy()
        if "amount" in f.columns:
            f = _to_numeric_cols(f, ["amount"])
            fines_tot = f.groupby("member_id", dropna=False)["amount"].sum().reset_index().rename(columns={"amount": "member_fines_total"})
        else:
            fines_tot = f.groupby("member_id", dropna=False).size().reset_index(name="member_fines_total")
    else:
        fines_tot = pd.DataFrame(columns=["member_id", "member_fines_total"])

    loan_counts = df.groupby("member_id", dropna=False).size().reset_index(name="member_loan_count")

    df = df.merge(contrib_tot, on="member_id", how="left")
    df = df.merge(fines_tot, on="member_id", how="left")
    df = df.merge(loan_counts, on="member_id", how="left")

    for c in ["member_contrib_total", "member_fines_total", "member_loan_count"]:
        df[c] = pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0)

    # names
    if members_df is not None and not members_df.empty and "id" in members_df.columns:
        m = _build_member_labels(members_df).rename(columns={"id": "member_id"})[["member_id", "member_name"]].copy()
        df["member_id"] = pd.to_numeric(df["member_id"], errors="coerce")
        m["member_id"] = pd.to_numeric(m["member_id"], errors="coerce")
        df = df.merge(m, on="member_id", how="left")

    keep = [
        "id", "member_id", "member_name", "y",
        "principal", "principal_current", "total_due", "unpaid_interest",
        "days_since_last_paid", "member_contrib_total", "member_fines_total", "member_loan_count",
    ]
    keep = [c for c in keep if c in df.columns]
    df = df[keep].copy()

    for c in ["principal", "principal_current", "total_due", "unpaid_interest", "days_since_last_paid", "member_contrib_total", "member_fines_total", "member_loan_count"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    return df


def _train_xgboost(df: pd.DataFrame, seed: int = 42, test_size: float = 0.25):
    """
    ✅ NO sklearn required.
    ✅ tiny-data safe split + fallback train-on-all
    Returns: (model, metrics_dict, feature_cols, df_with_preds)
    """
    try:
        import numpy as np
        import xgboost as xgb
    except Exception as e:
        return None, {"error": f"XGBoost not installed: {repr(e)}"}, [], df

    if df is None or df.empty:
        return None, {"error": "No training data."}, [], df
    if "y" not in df.columns:
        return None, {"error": "Missing label column 'y'."}, [], df

    y_all = df["y"].astype(int).values
    classes, counts = np.unique(y_all, return_counts=True)
    class_counts = {int(c): int(n) for c, n in zip(classes, counts)}
    if len(classes) < 2:
        return None, {"error": "Need both active(1) and closed(0) loans to train.", "class_counts": class_counts}, [], df

    feature_cols = [c for c in [
        "principal", "principal_current", "total_due", "unpaid_interest",
        "days_since_last_paid", "member_contrib_total", "member_fines_total", "member_loan_count",
    ] if c in df.columns]
    if not feature_cols:
        return None, {"error": "No feature columns found."}, [], df

    X_all = df[feature_cols].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0).values

    model = xgb.XGBClassifier(
        n_estimators=280,
        max_depth=4,
        learning_rate=0.07,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=1.0,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=int(seed),
        n_jobs=1,
    )

    rng = np.random.default_rng(int(seed))

    # tiny set -> train all
    if len(y_all) < 8 or min(counts) < 2:
        model.fit(X_all, y_all)
        out = df.copy()
        out["p_active"] = model.predict_proba(X_all)[:, 1]
        metrics = {
            "n_rows": int(len(df)),
            "n_train": int(len(df)),
            "n_test": 0,
            "accuracy_test": float("nan"),
            "logloss_test": float("nan"),
            "pos_rate": float(df["y"].mean()),
            "class_counts": class_counts,
            "note": "Trained on ALL rows (dataset too small to split). Add more CLOSED loans for validation.",
        }
        return model, metrics, feature_cols, out

    # manual stratified split
    idx0 = np.where(y_all == 0)[0]
    idx1 = np.where(y_all == 1)[0]
    rng.shuffle(idx0)
    rng.shuffle(idx1)

    def _n_test(n: int) -> int:
        k = int(round(n * float(test_size)))
        k = max(1, k)
        k = min(n - 1, k)
        return k

    n0_test = _n_test(len(idx0))
    n1_test = _n_test(len(idx1))

    test_idx = np.concatenate([idx0[:n0_test], idx1[:n1_test]])
    train_idx = np.concatenate([idx0[n0_test:], idx1[n1_test:]])
    rng.shuffle(test_idx)
    rng.shuffle(train_idx)

    X_train, y_train = X_all[train_idx], y_all[train_idx]
    X_test, y_test = X_all[test_idx], y_all[test_idx]

    # if split breaks class balance, fallback
    if len(np.unique(y_train)) < 2:
        model.fit(X_all, y_all)
        out = df.copy()
        out["p_active"] = model.predict_proba(X_all)[:, 1]
        metrics = {
            "n_rows": int(len(df)),
            "n_train": int(len(df)),
            "n_test": 0,
            "accuracy_test": float("nan"),
            "logloss_test": float("nan"),
            "pos_rate": float(df["y"].mean()),
            "class_counts": class_counts,
            "note": "Fallback: split produced one-class training. Trained on ALL rows.",
        }
        return model, metrics, feature_cols, out

    model.fit(X_train, y_train)

    p_test = model.predict_proba(X_test)[:, 1]
    yhat_test = (p_test >= 0.5).astype(int)
    acc = float((yhat_test == y_test).mean()) if len(y_test) else float("nan")
    loss = _bce_loss(list(map(int, y_test.tolist())), list(map(float, p_test.tolist())))

    out = df.copy()
    out["p_active"] = model.predict_proba(X_all)[:, 1]

    metrics = {
        "n_rows": int(len(df)),
        "n_train": int(len(train_idx)),
        "n_test": int(len(test_idx)),
        "accuracy_test": acc,
        "logloss_test": loss,
        "pos_rate": float(df["y"].mean()),
        "class_counts": class_counts,
        "note": "Manual stratified split (no sklearn).",
    }
    return model, metrics, feature_cols, out


# ==============================================================================
# UI — Young Copilot Panel
# ==============================================================================
def render_njangi_llm_panel(sb_anon=None, sb_service=None, schema: str = "public"):
    st.title("👩🏾‍💼 Young — Dashboard Copilot + Training")
    st.caption("Smart grounded Q&A over ALL Njangi data + optional Internet (Tavily) + XGBoost training.")

    sb_read = sb_service if sb_service is not None else sb_anon

    # ---------------- Settings ----------------
    with st.sidebar:
        st.subheader("⚙️ Young settings")
        slow_mode = st.checkbox("🐢 Slow Mode (reduce DB pressure)", value=True)
        limit = st.slider("Max rows per table", min_value=500, max_value=10000, value=5000, step=500)
        if st.button("🔄 Refresh ALL snapshots"):
            _clear_hub(schema=schema)
            st.success("Snapshots cleared. Reloading on next render.")
        st.markdown("---")
        st.caption("Internet search uses `TAVILY_API_KEY` env var (Railway Shared Variables).")
        st.caption("Privacy: Young will NOT web-search Njangi/member finance questions unless you force `web:`.")

    # ---------------- Load data hub ----------------
    hub = _load_hub(sb_read=sb_read, schema=schema, slow_mode=slow_mode, limit=int(limit))

    members_df = hub.get("members", pd.DataFrame())
    contrib_df = hub.get("contributions", pd.DataFrame())
    loans_df = hub.get("loans", pd.DataFrame())
    fines_df = hub.get("fines", pd.DataFrame())
    foundation_df = hub.get("foundation_contributions", pd.DataFrame())
    payments_df = hub.get("loan_payments", pd.DataFrame())
    interest_df = hub.get("interest_ledger", pd.DataFrame())

    # ---------------- KPIs ----------------
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Members", f"{_safe_count(members_df):,}")
    k2.metric("Contrib rows", f"{_safe_count(contrib_df):,}")
    k3.metric("Loans rows", f"{_safe_count(loans_df):,}")
    k4.metric("Payments rows", f"{_safe_count(payments_df):,}")
    k5.metric("Fines rows", f"{_safe_count(fines_df):,}")

    st.markdown("---")

    # ---------------- Choose member + loan filter ----------------
    member_id = None
    member_label = None

    if not members_df.empty and "id" in members_df.columns:
        m = _build_member_labels(members_df)
        m["label"] = m.apply(lambda r: f"{int(r['id']):02d} • {r.get('member_name','')}", axis=1)
        pick = st.selectbox("Select member (optional)", ["(All members)"] + m["label"].tolist())
        if pick != "(All members)":
            row = m[m["label"] == pick].iloc[0]
            member_id = int(row["id"])
            member_label = str(row.get("member_name") or "").strip()
    else:
        st.warning("Could not load members. Young will still answer general questions.")

    loan_filter = st.radio("Loans filter", ["All", "Active", "Closed"], horizontal=True)

    # ---------------- Welcome card ----------------
    project_hint = f"Schema: `{schema}` • Generated: `{_now_iso()}`"
    _young_welcome_card(project_hint=project_hint)

    st.markdown("---")

    # ==============================================================================
    # Smart Insights (modern dashboard assistant behavior)
    # ==============================================================================
    st.subheader("✨ Young Insights")
    cA, cB, cC = st.columns(3)

    # Insight 1: overdue / late signals
    with cA:
        st.caption("Overdue / late signals")
        if loans_df is None or loans_df.empty:
            st.write("No loans data.")
        else:
            tmp = loans_df.copy()
            if "status_norm" not in tmp.columns:
                tmp["status_norm"] = tmp.get("status", "").apply(_norm_status)
            od = tmp[tmp["status_norm"].isin(["overdue", "late", "default", "delinquent"])].copy()
            if od.empty and "last_paid_at" in tmp.columns:
                t2 = tmp[tmp["status_norm"] == "active"].copy()
                lp = pd.to_datetime(t2["last_paid_at"], errors="coerce", utc=True)
                t2["_days"] = (pd.Timestamp.now(tz="UTC") - lp).dt.total_seconds() / 86400.0
                od = t2[t2["_days"] > 30].drop(columns=["_days"], errors="ignore")
            st.write(f"Count: **{len(od):,}**")
            if not od.empty:
                show_cols = [c for c in ["id", "member_id", "principal_current", "unpaid_interest", "last_paid_at", "status"] if c in od.columns]
                st.dataframe(od[show_cols].head(7) if show_cols else od.head(7), width="stretch", hide_index=True)

    # Insight 2: totals
    with cB:
        st.caption("Totals (all members)")
        st.write(f"Contributions: **{_money(_safe_sum(contrib_df, 'amount'))}**")
        st.write(f"Foundation: **{_money(_safe_sum(foundation_df, 'amount'))}**")
        st.write(f"Unpaid interest: **{_money(_safe_sum(loans_df, 'unpaid_interest'))}**")
        st.write(f"Fines: **{_money(_safe_sum(fines_df, 'amount'))}**")

    # Insight 3: top risky by heuristic
    with cC:
        st.caption("Top risk (heuristic)")
        if loans_df is None or loans_df.empty or "member_id" not in loans_df.columns:
            st.write("Not enough loan data.")
        else:
            tmp = loans_df.copy()
            tmp["status_norm"] = tmp.get("status", "").apply(_norm_status)
            tmp = _to_numeric_cols(tmp, ["principal_current", "unpaid_interest"])
            # heuristic risk per loan row
            tmp["risk_h"] = 0.0
            tmp.loc[tmp["unpaid_interest"] > 0, "risk_h"] += 0.35
            tmp.loc[tmp["principal_current"] > 0, "risk_h"] += 0.25
            tmp.loc[tmp["status_norm"].isin(["overdue", "late", "default", "delinquent"]), "risk_h"] += 0.45
            bym = tmp.groupby("member_id", dropna=False)["risk_h"].max().reset_index().sort_values("risk_h", ascending=False).head(7)
            if not bym.empty and not members_df.empty and "id" in members_df.columns:
                m = _build_member_labels(members_df)[["id", "member_name"]].copy().rename(columns={"id": "member_id"})
                bym["member_id"] = pd.to_numeric(bym["member_id"], errors="coerce")
                m["member_id"] = pd.to_numeric(m["member_id"], errors="coerce")
                bym = bym.merge(m, on="member_id", how="left")
            st.dataframe(bym, width="stretch", hide_index=True)

    st.markdown("---")

    # ==============================================================================
    # Training (XGBoost)
    # ==============================================================================
    st.subheader("🧪 Training (XGBoost)")
    st.caption("Label: active=1, closed=0 (trained on loan rows). NO sklearn required.")

    with st.expander("Training settings", expanded=False):
        seed = st.number_input("Random seed", min_value=0, max_value=999999, value=42, step=1)
        test_size = st.slider("Test size", min_value=0.10, max_value=0.50, value=0.25, step=0.05)
        run_train = st.button("🚀 Train model now")

    if run_train:
        train_df = _build_training_frame(hub)

        if train_df.empty:
            st.error("No training data found. Need loans with statuses that normalize to 'active' and 'closed'.")
        else:
            model, metrics, feature_cols, pred_df = _train_xgboost(train_df, seed=int(seed), test_size=float(test_size))

            if model is None:
                st.error("Training failed.")
                st.code(metrics.get("error", "Unknown error"), language="text")
                if "class_counts" in metrics:
                    st.caption(f"class_counts: {metrics['class_counts']}")
            else:
                st.success("Training complete ✅")

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Rows", f"{metrics['n_rows']:,}")
                m2.metric("Pos rate (active)", f"{metrics['pos_rate']:.2f}")
                m3.metric("Test accuracy", "—" if str(metrics["accuracy_test"]) == "nan" else f"{metrics['accuracy_test']:.2f}")
                m4.metric("Test logloss", "—" if str(metrics["logloss_test"]) == "nan" else f"{metrics['logloss_test']:.3f}")
                st.caption(metrics.get("note", ""))

                st.caption("Features used:")
                st.code(", ".join(feature_cols), language="text")

                if pred_df is not None and not pred_df.empty and "member_id" in pred_df.columns and "p_active" in pred_df.columns:
                    tmp = pred_df.copy()
                    tmp["member_id"] = pd.to_numeric(tmp["member_id"], errors="coerce")
                    tmp["risk_score"] = 1.0 - pd.to_numeric(tmp["p_active"], errors="coerce").fillna(0.5)

                    by_member = (
                        tmp.groupby(["member_id", "member_name"], dropna=False)["risk_score"]
                        .max()
                        .sort_values(ascending=False)
                        .reset_index()
                        .rename(columns={"risk_score": "risk_max"})
                    )
                    st.markdown("### 🔥 Top risk (model) — max risk among loans (1 - p_active)")
                    st.dataframe(by_member.head(15), width="stretch", hide_index=True)

                st.markdown("### 🔎 Sample predictions (loan rows)")
                show_cols = [c for c in ["id", "member_id", "member_name", "y", "p_active", "principal_current", "unpaid_interest", "days_since_last_paid"] if pred_df is not None and c in pred_df.columns]
                if pred_df is not None and show_cols:
                    st.dataframe(pred_df[show_cols].head(25), width="stretch", hide_index=True)

    st.markdown("---")

    # ==============================================================================
    # Young Chat Copilot (modern assistant UI)
    # ==============================================================================
    st.subheader("💬 Chat with Young")

    # init chat memory
    if "young_chat" not in st.session_state:
        st.session_state["young_chat"] = []
        st.session_state["young_chat"].append({"role": "assistant", "content": _young_intro()})

    topbar1, topbar2, topbar3 = st.columns([1, 1, 2])
    with topbar1:
        if st.button("🧹 Clear chat"):
            st.session_state["young_chat"] = [{"role": "assistant", "content": _young_intro()}]
    with topbar2:
        if st.button("👋 Welcome"):
            st.session_state["young_chat"].append({"role": "assistant", "content": _young_intro()})
    with topbar3:
        st.caption("Tip: Use `web:` to force Internet answers. Otherwise Young stays grounded on your DB.")

    # render chat history
    for msg in st.session_state["young_chat"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # input
    user_text = st.chat_input("Ask Young a question… (example: “Active loans summary”, “session 12 summary”, “web: what is XGBoost?”)")
    if user_text:
        st.session_state["young_chat"].append({"role": "user", "content": user_text})

        # decide route
        intent = _detect_intent(user_text)
        force_web = intent == "web"
        allow_web = _has_tavily_key() and (force_web or (not _privacy_block_web(user_text)))

        with st.chat_message("assistant"):
            if allow_web and (force_web or user_text.strip().lower().startswith("web:")):
                q = user_text.strip()
                q = re.sub(r"^(web:|internet:|tavily:)\s*", "", q, flags=re.IGNORECASE).strip()
                tav = _tavily_search_cached(query=q, search_depth="basic", max_results=5)
                summary, sources = _format_web_answer(tav)
                st.markdown(summary)
                if sources:
                    st.markdown("**Sources:**")
                    for s in sources[:5]:
                        title = s.get("title") or s.get("url") or "Source"
                        url = s.get("url") or ""
                        if url:
                            st.markdown(f"- [{title}]({url})")
                        else:
                            st.markdown(f"- {title}")
                assistant_text = summary
            else:
                answer, df_show = _answer_grounded(
                    question=user_text,
                    hub=hub,
                    selected_member_id=member_id,
                    selected_member_label=member_label,
                    loan_filter=loan_filter,
                )
                st.markdown(answer)
                if df_show is not None and not df_show.empty:
                    st.dataframe(df_show, width="stretch", hide_index=True)
                assistant_text = answer

        st.session_state["young_chat"].append({"role": "assistant", "content": assistant_text})

    st.caption("Young Copilot • Grounded DB answers • Optional Internet (Tavily) • XGBoost training • Safe for Railway/Streamlit Cloud")
