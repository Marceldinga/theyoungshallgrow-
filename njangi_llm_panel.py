
# njangi_llm_panel.py
# ============================================================
# 🧠 NJANGI LLM PANEL + ✅ TRAINING (XGBoost) — ADVANCED UPDATE
# - NJANGI STANDARD (NO legacy)
# - Safe for Railway / Streamlit Cloud
# - Accepts sb_anon / sb_service / schema (matches app.py)
# - ✅ Lightweight “LLM” (NO OpenAI):
#     • Intent + Slots + Grounded answers from Supabase snapshots
#     • Uses selected member + loan filter as defaults
#     • Can parse member names from question
#     • Can introduce herself
# - ✅ ML training (XGBoost):
#     • label = 1 for active loans, 0 for closed loans
#     • ✅ NO sklearn required
#     • ✅ Safe stratified split for tiny datasets
#     • ✅ Fallback: trains on ALL data when too small to split
# ============================================================

from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd
import streamlit as st


# ============================================================
# Helpers
# ============================================================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_sum(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())


def _safe_count(df: pd.DataFrame) -> int:
    return int(len(df)) if df is not None and not df.empty else 0


def _try_read(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 2000,
    order_by: str | None = None,
    desc: bool = True,
):
    """Safe supabase read; returns list[dict]."""
    if sb is None:
        return []
    q = sb.schema(schema).table(table).select(cols)
    if order_by:
        q = q.order(order_by, desc=desc)
    if limit:
        q = q.limit(int(limit))
    return (q.execute().data or [])


def _to_numeric_cols(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


def _norm_status(x) -> str:
    s = str(x or "").strip().lower()
    if s in ("active", "open", "running", "current"):
        return "active"
    if s in ("closed", "paid", "completed", "settled", "done"):
        return "closed"
    if s in ("overdue", "late", "default", "delinquent"):
        return "overdue"
    return s or "unknown"


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


def _sigmoid(z: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-z))
    except Exception:
        return 0.5


def _bce_loss(y_true, y_prob, eps: float = 1e-9) -> float:
    # Binary cross-entropy
    n = len(y_true)
    if n == 0:
        return float("nan")
    s = 0.0
    for yt, yp in zip(y_true, y_prob):
        yp = max(eps, min(1.0 - eps, float(yp)))
        s += -(yt * math.log(yp) + (1 - yt) * math.log(1 - yp))
    return s / n


# ============================================================
# Lightweight Assistant v1 (kept for fallback)
# ============================================================
def _simple_answer(question: str) -> str:
    q = question.lower().strip()

    if any(k in q for k in ["introduce", "introduce yourself", "who are you", "your name"]):
        return _assistant_intro()

    if any(k in q for k in ["risk", "default", "overdue", "late", "delinquent"]):
        return (
            "Risk signals to watch:\n"
            "• Loans with status like 'overdue' / growing unpaid_interest\n"
            "• Long time since last payment (last_paid_at)\n"
            "• Many fines + inconsistent contributions\n"
            "Tip: Combine these into a score and flag top 5 members weekly."
        )

    if any(k in q for k in ["contribution", "pay", "deposit"]):
        return (
            "Contribution discipline tips:\n"
            "• Track contributions per session_id and highlight missing members\n"
            "• Rank top contributors by amount and consistency\n"
            "• Keep contributions in multiples of 500 (your rule) to simplify auditing"
        )

    if any(k in q for k in ["loan", "borrow", "interest", "principal"]):
        return (
            "Loan monitoring tips:\n"
            "• principal_current should trend down with payments\n"
            "• unpaid_interest should not grow for compliant borrowers\n"
            "• Watch last_paid_at; if > 30 days on active loans, follow up"
        )

    if any(k in q for k in ["minutes", "attendance"]):
        return (
            "Meeting operations:\n"
            "• Store minutes per session_id for traceability\n"
            "• Use attendance to justify fines (if your rules allow)\n"
            "• Produce a summary at the end of each session: present/absent + key decisions"
        )

    return (
        "I can help with:\n"
        "• Member risk insights (active + closed loans)\n"
        "• Contribution summaries\n"
        "• Loan monitoring tips\n"
        "Ask something like: 'Summarize active loans' or 'Show closed loans totals'."
    )


# ============================================================
# Lightweight Assistant v2: Intent + Slots + Grounded Answers
# ============================================================
def _normalize_text(s: str) -> str:
    return " ".join(str(s or "").lower().strip().split())


def _money(x: float) -> str:
    try:
        return f"${float(x):,.0f}"
    except Exception:
        return "$0"


def _assistant_intro() -> str:
    return (
        "Hi 👋🏾 I’m **Njangi Assistant** — a lightweight helper built into **theyoungshallgrow**.\n\n"
        "I don’t use external AI services (No OpenAI). I answer using your Njangi data:\n"
        "• **Members, Contributions, Loans, Fines**\n\n"
        "What you can ask me:\n"
        "• **'Loans summary'**, **'Active loans'**, **'Closed loans totals'**\n"
        "• **'Contribution summary'** or **'Contributions for Marcel'**\n"
        "• **'Fines summary'**\n"
        "• **'Risk for Donald'**\n\n"
        "Tip: Include words like **active / closed / all** to control the loan filter."
    )


def _pick_member_from_question(question: str, members_df: pd.DataFrame) -> tuple[int | None, str | None]:
    """
    Tries to find a member mentioned in the question by matching name/display_name tokens.
    Lightweight (no fuzzy libs).
    Returns (member_id, member_name) or (None, None).
    """
    if members_df is None or members_df.empty:
        return None, None

    q = _normalize_text(question)
    if not q:
        return None, None

    m = members_df.copy()
    if "display_name" in m.columns:
        m["member_name"] = m["display_name"].fillna("").astype(str)
        m.loc[m["member_name"].str.strip() == "", "member_name"] = m.get("name", "").astype(str)
    else:
        m["member_name"] = m.get("name", "").astype(str)

    candidates = []
    for _, r in m.iterrows():
        try:
            mid = int(r["id"])
        except Exception:
            continue
        name = str(r.get("member_name", "")).strip()
        if not name:
            continue
        candidates.append((len(name), mid, name, _normalize_text(name)))

    candidates.sort(reverse=True, key=lambda t: t[0])

    for _, mid, name, name_norm in candidates:
        if name_norm and name_norm in q:
            return mid, name
        toks = [t for t in name_norm.split() if len(t) >= 3]
        if toks and all(t in q for t in toks):
            return mid, name

    return None, None


def _detect_intent(question: str) -> str:
    q = _normalize_text(question)

    if any(k in q for k in ["introduce", "introduce yourself", "who are you", "your name"]):
        return "intro"

    if any(k in q for k in ["help", "what can you do", "commands", "examples"]):
        return "help"

    if any(k in q for k in ["minutes", "attendance", "meeting"]):
        return "minutes"

    if any(k in q for k in ["fine", "penalty"]):
        return "fines"

    if any(k in q for k in ["payout", "rotation", "who is next", "next payout"]):
        return "payouts"

    if any(k in q for k in ["contribution", "contrib", "deposit", "paid", "payment in"]):
        return "contributions"

    if any(k in q for k in ["risk", "default", "overdue", "late", "delinquent"]):
        return "risk"

    if any(k in q for k in ["loan", "borrow", "interest", "principal", "balance"]):
        return "loans"

    return "unknown"


def _answer_grounded(
    question: str,
    members_df: pd.DataFrame,
    contrib_df: pd.DataFrame,
    loans_df: pd.DataFrame,
    fines_df: pd.DataFrame,
    selected_member_id: int | None,
    selected_member_label: str | None,
    loan_filter: str,
) -> str:
    """
    Grounded assistant:
    - Uses selected member + loan filter as defaults
    - Tries to extract member from question and override selection
    - Returns a bank-style answer with real numbers
    """
    qraw = question.strip()
    if not qraw:
        return "Please type a question."

    intent = _detect_intent(qraw)

    if intent == "intro":
        return _assistant_intro()

    # slot: member (from question overrides UI)
    q_member_id, q_member_name = _pick_member_from_question(qraw, members_df)
    member_id = q_member_id if q_member_id is not None else selected_member_id
    member_name = q_member_name if q_member_name is not None else selected_member_label

    # slot: loan status filter from question overrides UI
    qn = _normalize_text(qraw)
    status_filter = loan_filter
    if "active" in qn:
        status_filter = "Active"
    elif "closed" in qn or "paid" in qn or "completed" in qn:
        status_filter = "Closed"
    elif "all" in qn:
        status_filter = "All"

    # prepare views (string-safe compare)
    if member_id is not None:
        mc = (
            contrib_df[contrib_df.get("member_id").astype(str) == str(member_id)].copy()
            if (contrib_df is not None and not contrib_df.empty and "member_id" in contrib_df.columns)
            else pd.DataFrame()
        )
        ml = (
            loans_df[loans_df.get("member_id").astype(str) == str(member_id)].copy()
            if (loans_df is not None and not loans_df.empty and "member_id" in loans_df.columns)
            else pd.DataFrame()
        )
        mf = (
            fines_df[fines_df.get("member_id").astype(str) == str(member_id)].copy()
            if (fines_df is not None and not fines_df.empty and "member_id" in fines_df.columns)
            else pd.DataFrame()
        )
    else:
        mc = contrib_df.copy() if contrib_df is not None else pd.DataFrame()
        ml = loans_df.copy() if loans_df is not None else pd.DataFrame()
        mf = fines_df.copy() if fines_df is not None else pd.DataFrame()

    # normalize + filter loans
    if ml is not None and not ml.empty:
        if "status_norm" not in ml.columns:
            ml["status_norm"] = ml.get("status", "").apply(_norm_status)
        if status_filter == "Active":
            ml = ml[ml["status_norm"] == "active"].copy()
        elif status_filter == "Closed":
            ml = ml[ml["status_norm"] == "closed"].copy()

    # aggregates
    total_contrib = _safe_sum(mc, "amount")
    principal_all = _safe_sum(ml, "principal")
    bal_all = _safe_sum(ml, "principal_current")
    unpaid_int = _safe_sum(ml, "unpaid_interest")
    fines_total = (
        _safe_sum(mf, "amount") if (mf is not None and not mf.empty and "amount" in mf.columns) else float(len(mf) if mf is not None else 0)
    )

    loan_count = _safe_count(ml)
    contrib_rows = _safe_count(mc)
    fine_rows = _safe_count(mf)

    who = f"**{member_name}**" if member_id is not None and member_name else "**All members**"

    if intent == "help":
        return (
            "Try asking:\n"
            "• **'Loans summary'** / **'Active loans'** / **'Closed loans totals'**\n"
            "• **'Contribution summary'** / **'Contributions for <member>'**\n"
            "• **'Fines summary'**\n"
            "• **'Risk for <member>'**\n\n"
            "Tip: include **active / closed / all** in your question to control the loan filter."
        )

    if intent == "contributions":
        return (
            f"Contribution summary for {who}:\n"
            f"• Total contributed: **{_money(total_contrib)}**\n"
            f"• Contribution rows: **{contrib_rows:,}**\n\n"
            "Operations:\n"
            "• Track missing contributors per session\n"
            "• Keep contributions in multiples of **500** (your rule) for easier auditing"
        )

    if intent == "loans":
        breakdown = ""
        if loans_df is not None and not loans_df.empty and "status_norm" in loans_df.columns:
            src = loans_df.copy()
            if member_id is not None and "member_id" in src.columns:
                src = src[src["member_id"].astype(str) == str(member_id)]
            vc = src["status_norm"].value_counts().to_dict()
            if vc:
                breakdown = "Status: " + ", ".join([f"{k}={int(v)}" for k, v in vc.items()])

        return (
            f"Loans summary for {who} (filter: **{status_filter}**):\n"
            f"• Loan rows: **{loan_count:,}**\n"
            f"• Total principal: **{_money(principal_all)}**\n"
            f"• Total balance (principal_current): **{_money(bal_all)}**\n"
            f"• Unpaid interest: **{_money(unpaid_int)}**\n"
            f"{('• ' + breakdown) if breakdown else ''}\n\n"
            "Monitoring tips:\n"
            "• **principal_current** should go down as payments are made\n"
            "• If **last_paid_at** is > 30 days on active loans, follow up"
        )

    if intent == "fines":
        return (
            f"Fines summary for {who}:\n"
            f"• Fine records: **{fine_rows:,}**\n"
            f"• Total fines: **{_money(fines_total)}**\n\n"
            "Tip: connect fines to attendance/minutes if your rules allow."
        )

    if intent == "risk":
        risk = 0
        if unpaid_int > 0:
            risk += 35
        if bal_all > 0 and total_contrib == 0:
            risk += 25
        if ml is not None and not ml.empty and "status_norm" in ml.columns:
            if ml["status_norm"].astype(str).str.contains("overdue|default|delinquent|late", case=False, na=False).any():
                risk += 45
        if fines_total > 0:
            risk += 10
        risk = min(100, risk)

        guidance = []
        if unpaid_int > 0:
            guidance.append("unpaid_interest > 0")
        if fines_total > 0:
            guidance.append("fines exist")
        if bal_all > 0 and total_contrib == 0:
            guidance.append("balance > 0 but no contributions recorded")
        if not guidance:
            guidance.append("no strong negative signals in snapshot")

        return (
            f"Risk view for {who} (from current DB snapshot):\n"
            f"• Balance: **{_money(bal_all)}** • Unpaid interest: **{_money(unpaid_int)}** • Fines: **{_money(fines_total)}**\n"
            f"• Quick risk score (rules): **{risk}/100**\n"
            f"Why: {', '.join(guidance)}.\n\n"
            "Next action:\n"
            "• If active loans exist and last payment is old, request a partial payment this session.\n"
            "• For model-based scoring, use **Training (XGBoost)** below."
        )

    if intent == "minutes":
        return (
            "Minutes & attendance guidance:\n"
            "• Store minutes per **session_id** for traceability\n"
            "• Attendance can drive fines (based on your rules)\n"
            "• End-of-session summary: present/absent + decisions + loans/payouts approved"
        )

    if intent == "payouts":
        return (
            "Payout guidance:\n"
            "• Track payout rotation index in app_state\n"
            "• Validate payout eligibility using contribution completeness\n"
            "• Export payout receipts for audit"
        )

    # fallback
    return (
        "I can answer with real numbers if you ask:\n"
        "• **Loans summary** / **Active loans** / **Closed loans**\n"
        "• **Contribution summary**\n"
        "• **Fines summary**\n"
        "• **Risk for <member>**\n"
        "Or type **help**."
    )


# ============================================================
# ML: build training dataset
# ============================================================
def _build_training_frame(
    members_df: pd.DataFrame,
    contrib_df: pd.DataFrame,
    loans_df: pd.DataFrame,
    fines_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Train on LOAN rows (not member rows).
    Label: active=1, closed=0
    Features per loan + member aggregates.
    """
    if loans_df is None or loans_df.empty:
        return pd.DataFrame()

    df = loans_df.copy()

    if "status_norm" not in df.columns:
        df["status_norm"] = df.get("status", "").apply(_norm_status)

    # labels: active vs closed only
    df = df[df["status_norm"].isin(["active", "closed"])].copy()
    if df.empty:
        return pd.DataFrame()

    df["y"] = (df["status_norm"] == "active").astype(int)

    # parse dates
    df["last_paid_dt"] = df.get("last_paid_at", None).apply(_parse_dt) if "last_paid_at" in df.columns else None
    df["created_dt"] = df.get("created_at", None).apply(_parse_dt) if "created_at" in df.columns else None

    def _ds(row):
        d = row.get("last_paid_dt")
        if d is None:
            d = row.get("created_dt")
        return _days_since(d)

    df["days_since_last_paid"] = df.apply(_ds, axis=1)
    df["days_since_last_paid"] = pd.to_numeric(df["days_since_last_paid"], errors="coerce")

    # median fallback (if all nan -> 0)
    if df["days_since_last_paid"].notna().any():
        med = float(df["days_since_last_paid"].median())
    else:
        med = 0.0
    df["days_since_last_paid"] = df["days_since_last_paid"].fillna(med)

    # numeric cols
    df = _to_numeric_cols(df, ["principal", "principal_current", "total_due", "unpaid_interest"])

    # member aggregates: contributions
    if contrib_df is not None and not contrib_df.empty and "member_id" in contrib_df.columns:
        c = contrib_df.copy()
        c = _to_numeric_cols(c, ["amount"])
        contrib_tot = c.groupby("member_id", dropna=False)["amount"].sum().reset_index().rename(columns={"amount": "member_contrib_total"})
    else:
        contrib_tot = pd.DataFrame(columns=["member_id", "member_contrib_total"])

    # member aggregates: fines
    if fines_df is not None and not fines_df.empty and "member_id" in fines_df.columns:
        f = fines_df.copy()
        if "amount" in f.columns:
            f = _to_numeric_cols(f, ["amount"])
            fines_tot = f.groupby("member_id", dropna=False)["amount"].sum().reset_index().rename(columns={"amount": "member_fines_total"})
        else:
            fines_tot = f.groupby("member_id", dropna=False).size().reset_index(name="member_fines_total")
    else:
        fines_tot = pd.DataFrame(columns=["member_id", "member_fines_total"])

    # member aggregates: loan counts (using df that already filtered to active/closed)
    loan_counts = df.groupby("member_id", dropna=False).size().reset_index(name="member_loan_count")

    # merge aggregates
    df = df.merge(contrib_tot, on="member_id", how="left")
    df = df.merge(fines_tot, on="member_id", how="left")
    df = df.merge(loan_counts, on="member_id", how="left")

    df["member_contrib_total"] = pd.to_numeric(df["member_contrib_total"], errors="coerce").fillna(0)
    df["member_fines_total"] = pd.to_numeric(df["member_fines_total"], errors="coerce").fillna(0)
    df["member_loan_count"] = pd.to_numeric(df["member_loan_count"], errors="coerce").fillna(1)

    # optional member name
    if members_df is not None and not members_df.empty and "id" in members_df.columns:
        m = members_df.copy()
        if "display_name" in m.columns:
            m["member_name"] = m["display_name"].fillna("").astype(str)
            m.loc[m["member_name"].str.strip() == "", "member_name"] = m.get("name", "").astype(str)
        else:
            m["member_name"] = m.get("name", "").astype(str)

        m = m.rename(columns={"id": "member_id"})[["member_id", "member_name"]].copy()
        df["member_id"] = pd.to_numeric(df["member_id"], errors="coerce")
        m["member_id"] = pd.to_numeric(m["member_id"], errors="coerce")
        df = df.merge(m, on="member_id", how="left")

    keep = [
        "id",
        "member_id",
        "member_name",
        "y",
        "principal",
        "principal_current",
        "total_due",
        "unpaid_interest",
        "days_since_last_paid",
        "member_contrib_total",
        "member_fines_total",
        "member_loan_count",
    ]
    keep = [c for c in keep if c in df.columns]
    df = df[keep].copy()

    for c in [
        "principal",
        "principal_current",
        "total_due",
        "unpaid_interest",
        "member_contrib_total",
        "member_fines_total",
        "member_loan_count",
        "days_since_last_paid",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # normalize types for ids
    if "member_id" in df.columns:
        df["member_id"] = pd.to_numeric(df["member_id"], errors="coerce")

    return df


def _train_xgboost(df: pd.DataFrame, seed: int = 42, test_size: float = 0.25):
    """
    Trains XGBoost classifier if available.
    ✅ NO sklearn required.
    ✅ Safe on tiny datasets:
      - Manual stratified split (keeps both classes in train/test)
      - Fallback: train on all rows if too small to split safely
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

    # require both classes overall
    y_all = df["y"].astype(int).values
    classes, counts = np.unique(y_all, return_counts=True)
    class_counts = {int(c): int(n) for c, n in zip(classes, counts)}
    if len(classes) < 2:
        return None, {"error": "Need both active(1) and closed(0) loans to train.", "class_counts": class_counts}, [], df

    feature_cols = [
        c
        for c in [
            "principal",
            "principal_current",
            "total_due",
            "unpaid_interest",
            "days_since_last_paid",
            "member_contrib_total",
            "member_fines_total",
            "member_loan_count",
        ]
        if c in df.columns
    ]
    if not feature_cols:
        return None, {"error": "No feature columns found."}, [], df

    X_all = df[feature_cols].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0).values

    model = xgb.XGBClassifier(
        n_estimators=250,
        max_depth=4,
        learning_rate=0.08,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=1.0,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=int(seed),
        n_jobs=1,
    )

    rng = np.random.default_rng(int(seed))

    # If dataset is tiny or a class is too small, do not split.
    # Need at least 2 rows per class to put 1 in train and 1 in test safely.
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
            "note": "Trained on ALL rows (dataset too small to split safely). Add more CLOSED loans for validation.",
        }
        return model, metrics, feature_cols, out

    # Manual stratified split (NO sklearn)
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

    # final guard
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
            "note": "Fallback: split produced one-class train. Trained on ALL rows.",
        }
        return model, metrics, feature_cols, out

    model.fit(X_train, y_train)

    # preds + metrics (NO sklearn)
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


# ============================================================
# Main UI
# ============================================================
def render_njangi_llm_panel(sb_anon=None, sb_service=None, schema: str = "public"):
    st.title("🧠 Njangi LLM (Lightweight) + Training")
    st.caption("Rule-based insights + grounded answers + optional ML training (XGBoost).")

    st.markdown("---")

    # ✅ Use service client when available (more reliable than anon for analytics)
    sb_read = sb_service if sb_service is not None else sb_anon

    # ---------- Load snapshots ----------
    members_df = pd.DataFrame(
        _try_read(sb_read, schema, "members", "id,name,display_name,phone", limit=5000, order_by="id", desc=False)
    )
    contrib_df = pd.DataFrame(
        _try_read(
            sb_read,
            schema,
            "contributions",
            "id,member_id,session_id,amount,paid_at,created_at",
            limit=5000,
            order_by="created_at",
            desc=True,
        )
    )
    loans_df = pd.DataFrame(
        _try_read(
            sb_read,
            schema,
            "loans",
            "id,member_id,principal,principal_current,total_due,unpaid_interest,last_paid_at,status,created_at",
            limit=5000,
            order_by="created_at",
            desc=True,
        )
    )
    fines_df = pd.DataFrame(_try_read(sb_read, schema, "fines", "*", limit=5000, order_by="created_at", desc=True))

    # numeric cleanup
    contrib_df = _to_numeric_cols(contrib_df, ["amount"])
    loans_df = _to_numeric_cols(loans_df, ["principal", "principal_current", "total_due", "unpaid_interest"])
    fines_df = _to_numeric_cols(fines_df, ["amount"])

    # normalize loan status
    if not loans_df.empty:
        loans_df["status_norm"] = loans_df.get("status", "").apply(_norm_status)

    # ---------- KPIs (top line) ----------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Members", f"{_safe_count(members_df):,}")
    c2.metric("Contrib rows", f"{_safe_count(contrib_df):,}")
    c3.metric("Loans rows", f"{_safe_count(loans_df):,}")
    c4.metric("Fines rows", f"{_safe_count(fines_df):,}")

    st.markdown("---")

    # ---------- Choose member ----------
    member_id = None
    member_label = None
    if not members_df.empty:
        if "display_name" in members_df.columns:
            members_df["member_name"] = members_df["display_name"].fillna("").astype(str)
            members_df.loc[members_df["member_name"].str.strip() == "", "member_name"] = members_df["name"].astype(str)
        else:
            members_df["member_name"] = members_df["name"].astype(str)

        members_df["label"] = members_df.apply(lambda r: f"{int(r['id']):02d} • {r['member_name']}", axis=1)
        pick = st.selectbox("Select member (optional)", ["(All members)"] + members_df["label"].tolist())
        if pick != "(All members)":
            row = members_df[members_df["label"] == pick].iloc[0]
            member_id = int(row["id"])
            member_label = str(row["member_name"])
    else:
        st.warning("Could not load members. Panel will still work with generic answers.")

    # ---------- Loans filter ----------
    st.subheader("🏦 Loans filter")
    loan_filter = st.radio("Show loans", ["All", "Active", "Closed"], horizontal=True)

    loans_view = loans_df.copy()
    if not loans_view.empty and "status_norm" in loans_view.columns:
        if loan_filter == "Active":
            loans_view = loans_view[loans_view["status_norm"] == "active"].copy()
        elif loan_filter == "Closed":
            loans_view = loans_view[loans_view["status_norm"] == "closed"].copy()

    # ---------- Snapshot ----------
    st.subheader("📌 Snapshot")

    if member_id is not None:
        mc = (
            contrib_df[contrib_df.get("member_id").astype(str) == str(member_id)].copy()
            if not contrib_df.empty and "member_id" in contrib_df.columns
            else pd.DataFrame()
        )
        ml_all = (
            loans_df[loans_df.get("member_id").astype(str) == str(member_id)].copy()
            if not loans_df.empty and "member_id" in loans_df.columns
            else pd.DataFrame()
        )
        ml = (
            loans_view[loans_view.get("member_id").astype(str) == str(member_id)].copy()
            if not loans_view.empty and "member_id" in loans_view.columns
            else pd.DataFrame()
        )
        mf = (
            fines_df[fines_df.get("member_id").astype(str) == str(member_id)].copy()
            if not fines_df.empty and "member_id" in fines_df.columns
            else pd.DataFrame()
        )

        total_contrib = _safe_sum(mc, "amount")
        total_principal_all = _safe_sum(ml_all, "principal")
        total_balance_all = _safe_sum(ml_all, "principal_current")
        unpaid_interest_all = _safe_sum(ml_all, "unpaid_interest")
        total_fines = _safe_sum(mf, "amount") if "amount" in mf.columns else float(len(mf))

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Total contributions", f"${total_contrib:,.0f}")
        s2.metric("Loans principal (ALL)", f"${total_principal_all:,.0f}")
        s3.metric("Balance (ALL)", f"${total_balance_all:,.0f}")
        s4.metric("Unpaid interest (ALL)", f"${unpaid_interest_all:,.0f}")

        st.caption(f"Member: **{member_label}** • Loan filter: **{loan_filter}** • Generated: {_now_iso()}")

        # simple heuristic
        risk = 0
        if unpaid_interest_all > 0:
            risk += 35
        if total_balance_all > 0 and total_contrib == 0:
            risk += 25
        if not ml_all.empty and "status_norm" in ml_all.columns:
            if ml_all["status_norm"].astype(str).str.contains("overdue|default|delinquent|late", case=False, na=False).any():
                risk += 45
        if total_fines > 0:
            risk += 10
        risk = min(100, risk)
        st.info(f"Quick heuristic risk score: **{risk}/100** (rules).")

    else:
        st.write("All-members snapshot:")
        st.write(f"• Total contributions amount: **${_safe_sum(contrib_df, 'amount'):,.0f}**")
        st.write(f"• Total loan principal ({loan_filter}): **${_safe_sum(loans_view, 'principal'):,.0f}**")
        st.write(f"• Total current balances ({loan_filter}): **${_safe_sum(loans_view, 'principal_current'):,.0f}**")
        st.write(f"• Total unpaid interest ({loan_filter}): **${_safe_sum(loans_view, 'unpaid_interest'):,.0f}**")

        if not loans_df.empty and "status_norm" in loans_df.columns:
            st.caption(
                "Loan status counts: "
                + ", ".join([f"{k}={int(v)}" for k, v in loans_df["status_norm"].value_counts().to_dict().items()])
            )

    st.markdown("---")

    # ============================================================
    # ✅ TRAINING SECTION (XGBoost)
    # ============================================================
    st.subheader("🧪 Training (XGBoost)")
    st.caption("Label: active=1, closed=0 (trained on loan rows). No sklearn required.")

    with st.expander("Training settings", expanded=False):
        seed = st.number_input("Random seed", min_value=0, max_value=999999, value=42, step=1)
        test_size = st.slider("Test size", min_value=0.10, max_value=0.50, value=0.25, step=0.05)
        run_train = st.button("🚀 Train model now")

    if run_train:
        train_df = _build_training_frame(members_df, contrib_df, loans_df, fines_df)

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

                # show "risk" as 1 - p_active (more intuitive)
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
                show_cols = [
                    c
                    for c in [
                        "id",
                        "member_id",
                        "member_name",
                        "y",
                        "p_active",
                        "principal_current",
                        "unpaid_interest",
                        "days_since_last_paid",
                    ]
                    if pred_df is not None and c in pred_df.columns
                ]
                if pred_df is not None and show_cols:
                    st.dataframe(pred_df[show_cols].head(25), width="stretch", hide_index=True)

    st.markdown("---")

    # ============================================================
    # ✅ Q&A (Advanced Assistant)
    # ============================================================
    st.subheader("💬 Ask the Njangi Assistant")

    # Friendly intro (button + optional always-on)
    colA, colB = st.columns([1, 2])
    with colA:
        intro_btn = st.button("👋 Introduce yourself")
    with colB:
        st.caption("Tip: Ask 'Loans summary', 'Risk for Donald', 'Active loans', 'Contribution summary', or 'help'.")

    if intro_btn:
        st.success("Assistant response")
        st.write(_assistant_intro())

    question = st.text_area("Type a question (e.g., 'Explain risk', 'Loan tips', 'Contribution summary')")

    if st.button("Analyze"):
        if not question.strip():
            st.warning("Please enter a question.")
        else:
            st.success("Assistant response")
            try:
                st.write(
                    _answer_grounded(
                        question=question,
                        members_df=members_df,
                        contrib_df=contrib_df,
                        loans_df=loans_df,
                        fines_df=fines_df,
                        selected_member_id=member_id,
                        selected_member_label=member_label,
                        loan_filter=loan_filter,
                    )
                )
            except Exception:
                # ultra-safe fallback (never crash the page)
                st.write(_simple_answer(question))

    st.caption("Lightweight assistant + Grounded answers + Training • XGBoost • Safe for Railway/Streamlit Cloud")
