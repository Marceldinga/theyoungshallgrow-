# ai_suite_panel.py ✅ EXTRA AI SUITE (NO API KEY)
# ------------------------------------------------------------
# Adds:
# - Reliability score (0–100)
# - Dropout risk (0–1)
# - Fraud/Anomaly score (0–1)
# - Simple liquidity forecast
# - Smart loan recommendation
# - Alerts center
# - Local chatbox (answers from computed context)
#
# Designed to be imported into ai_risk_panel.py
# ------------------------------------------------------------

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st


def _clip01(x: float) -> float:
    try:
        return float(np.clip(float(x), 0.0, 1.0))
    except Exception:
        return 0.0


def _to_int(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0).astype(int)


def _to_num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def _to_dt_utc(s) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", utc=True)


def _utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _days_since(now_utc: pd.Timestamp, dt_series) -> pd.Series:
    dt_utc = pd.to_datetime(dt_series, errors="coerce", utc=True)
    out = (now_utc - dt_utc).dt.days
    return out.fillna(999).astype(int)


# ============================================================
# Scores
# ============================================================
def compute_reliability_score(row: pd.Series) -> tuple[int, list[str]]:
    reasons = []
    score = 70.0

    contrib_count = int(row.get("contrib_count", 0))
    contrib_total = float(row.get("contrib_total", 0.0))
    days_contrib = int(row.get("days_since_last_contrib", 999))

    fine_count = int(row.get("fine_count", 0))
    fine_total = float(row.get("fine_total", 0.0))

    loan_balance = float(row.get("loan_balance_sum", 0.0))
    days_pay = int(row.get("days_since_last_payment", 999))
    bad_status = int(row.get("loan_bad_status_count", 0))

    if contrib_count >= 8:
        score += 12
        reasons.append("Consistent contributions (8+ records)")
    elif contrib_count >= 4:
        score += 6
        reasons.append("Moderate contribution consistency (4+ records)")
    else:
        score -= 8
        reasons.append("Low contribution history")

    if days_contrib >= 30:
        score -= 15
        reasons.append("No contribution in 30+ days")
    elif days_contrib >= 14:
        score -= 7
        reasons.append("No contribution in 14+ days")

    if contrib_total >= 5000:
        score += 6
        reasons.append("Strong total contributions")

    if fine_count > 0:
        score -= min(18, 3 * fine_count)
        reasons.append("Fines reduce reliability")
    if fine_total >= 1000:
        score -= 6
        reasons.append("High total fines")

    if loan_balance > 0:
        if days_pay >= 30:
            score -= 18
            reasons.append("Loan balance with no payment in 30+ days")
        elif days_pay >= 14:
            score -= 10
            reasons.append("Loan balance with no payment in 14+ days")
        else:
            score += 3
            reasons.append("Active loan with recent payment")

    if bad_status > 0:
        score -= min(15, 7 * bad_status)
        reasons.append("Overdue/delinquent status flags")

    score = int(np.clip(score, 0, 100))
    return score, reasons[:6]


def compute_dropout_risk(row: pd.Series) -> tuple[float, list[str]]:
    reasons = []
    days_contrib = int(row.get("days_since_last_contrib", 999))
    contrib_count = int(row.get("contrib_count", 0))
    fine_count = int(row.get("fine_count", 0))

    risk = 0.15

    if contrib_count <= 2:
        risk += 0.20
        reasons.append("Very low contribution history")

    if days_contrib >= 60:
        risk += 0.45
        reasons.append("Inactive contributions for 60+ days")
    elif days_contrib >= 30:
        risk += 0.30
        reasons.append("Inactive contributions for 30+ days")
    elif days_contrib >= 14:
        risk += 0.15
        reasons.append("Inactive contributions for 14+ days")

    if fine_count >= 3:
        risk += 0.10
        reasons.append("Frequent fines indicate disengagement")

    return _clip01(risk), reasons[:6]


def compute_fraud_anomaly_score(
    member_id: int,
    contrib: pd.DataFrame,
    loans: pd.DataFrame,
    payments: pd.DataFrame,
) -> tuple[float, list[str]]:
    reasons = []
    score = 0.05

    if not contrib.empty and "member_id" in contrib.columns and "amount" in contrib.columns:
        c = contrib.copy()
        c["member_id"] = _to_int(c["member_id"])
        c["amount"] = _to_num(c["amount"])
        mc = c[c["member_id"] == int(member_id)].copy()
        if len(mc) >= 6:
            mu = float(mc["amount"].mean())
            sd = float(mc["amount"].std(ddof=0) or 0.0)
            last_amt = float(mc["amount"].iloc[-1])
            if sd > 0 and abs(last_amt - mu) > 3 * sd:
                score += 0.35
                reasons.append("Contribution amount is a strong outlier vs member history (3σ)")

    if not loans.empty and "member_id" in loans.columns:
        l = loans.copy()
        l["member_id"] = _to_int(l["member_id"])
        l["borrow_date"] = _to_dt_utc(l.get("borrow_date", l.get("created_at", pd.NaT)))
        ml = l[l["member_id"] == int(member_id)].copy()
        if not ml.empty:
            now = _utc_now()
            recent14 = ml[(_days_since(now, ml["borrow_date"]) <= 14)].copy()
            if len(recent14) >= 2:
                score += 0.25
                reasons.append("Multiple loans created within last 14 days")

    if not payments.empty and "member_id" in payments.columns:
        p = payments.copy()
        p["member_id"] = _to_int(p["member_id"])
        p["amount"] = _to_num(p.get("amount", 0))
        mp = p[p["member_id"] == int(member_id)].copy()
        if len(mp) >= 6:
            mu = float(mp["amount"].mean())
            sd = float(mp["amount"].std(ddof=0) or 0.0)
            last_amt = float(mp["amount"].iloc[-1])
            if sd > 0 and abs(last_amt - mu) > 3 * sd:
                score += 0.20
                reasons.append("Payment amount is an outlier vs member history (3σ)")

    return _clip01(score), reasons[:6]


# ============================================================
# Liquidity Forecast (simple)
# ============================================================
def foundation_liquidity_forecast_simple(
    contrib: pd.DataFrame,
    foundation: pd.DataFrame,
    loans: pd.DataFrame,
    payments: pd.DataFrame,
    payouts: pd.DataFrame,
    horizon_days: int = 30,
) -> dict:
    now = _utc_now().normalize()

    def daily_sum(df: pd.DataFrame, dt_col: str, amt_col: str, sign: float) -> pd.Series:
        if df is None or df.empty or dt_col not in df.columns:
            return pd.Series(dtype=float)
        d = df.copy()
        d[dt_col] = _to_dt_utc(d[dt_col]).dt.normalize()
        d[amt_col] = _to_num(d.get(amt_col, 0))
        return d.groupby(dt_col)[amt_col].sum() * float(sign)

    inflow = pd.Series(dtype=float)
    if not contrib.empty and "created_at" in contrib.columns:
        inflow = inflow.add(daily_sum(contrib, "created_at", "amount", +1.0), fill_value=0.0)
    if not foundation.empty and "created_at" in foundation.columns:
        inflow = inflow.add(daily_sum(foundation, "created_at", "amount", +1.0), fill_value=0.0)
    if not payments.empty:
        dtc = "paid_at" if "paid_at" in payments.columns else ("created_at" if "created_at" in payments.columns else None)
        if dtc:
            inflow = inflow.add(daily_sum(payments, dtc, "amount", +1.0), fill_value=0.0)

    outflow = pd.Series(dtype=float)
    if not loans.empty:
        dtc = "borrow_date" if "borrow_date" in loans.columns else ("created_at" if "created_at" in loans.columns else None)
        principal_col = "principal" if "principal" in loans.columns else ("principal_current" if "principal_current" in loans.columns else None)
        if dtc and principal_col:
            outflow = outflow.add(daily_sum(loans, dtc, principal_col, +1.0), fill_value=0.0)

    if not payouts.empty:
        dtc = "payout_date" if "payout_date" in payouts.columns else ("created_at" if "created_at" in payouts.columns else None)
        amt_col = "payout_amount" if "payout_amount" in payouts.columns else ("amount" if "amount" in payouts.columns else None)
        if dtc and amt_col:
            outflow = outflow.add(daily_sum(payouts, dtc, amt_col, +1.0), fill_value=0.0)

    daily_net = inflow.sub(outflow, fill_value=0.0).sort_index()
    if daily_net.empty:
        return {"ok": False, "msg": "Not enough history for liquidity forecast."}

    balance_est = float(daily_net.sum())
    trailing = daily_net[daily_net.index >= (now - pd.Timedelta(days=30))]
    avg_daily_net = float(trailing.mean()) if not trailing.empty else float(daily_net.mean())

    dates = [now + pd.Timedelta(days=i) for i in range(1, int(horizon_days) + 1)]
    b = balance_est
    forecast_bal = []
    for _ in dates:
        b += avg_daily_net
        forecast_bal.append(b)

    return {
        "ok": True,
        "balance_est": balance_est,
        "avg_daily_net": avg_daily_net,
        "horizon_days": int(horizon_days),
        "dates": dates,
        "forecast_balance": forecast_bal,
    }


# ============================================================
# Loan Recommendation + Alerts
# ============================================================
def smart_loan_recommendation(risk: float, reliability: int, liquidity_ok: bool, requested_amount: float) -> tuple[str, list[str]]:
    reasons = []
    decision = "APPROVE"

    if not liquidity_ok:
        decision = "REJECT"
        reasons.append("Liquidity trend is weak: avoid new loans now.")

    if risk >= 0.70:
        decision = "REJECT"
        reasons.append("Very high risk score (≥70%).")

    if 0.45 <= risk < 0.70:
        decision = "APPROVE WITH CONDITIONS"
        reasons.append("Moderate-to-high risk: cap amount + require stronger surety.")

    if reliability < 45:
        decision = "REJECT"
        reasons.append("Low reliability score (<45).")
    elif reliability < 65 and decision == "APPROVE":
        decision = "APPROVE WITH CONDITIONS"
        reasons.append("Reliability moderate: require surety + lower cap.")

    if requested_amount >= 5000 and decision == "APPROVE":
        decision = "APPROVE WITH CONDITIONS"
        reasons.append("Large amount: recommend cap or split disbursement.")

    return decision, reasons[:6]


def generate_ai_alerts(
    member_name: str,
    final_risk: float,
    reliability: int,
    dropout: float,
    fraud: float,
    liquidity_forecast: dict,
) -> list[dict]:
    alerts = []

    if final_risk >= 0.70:
        alerts.append({"severity": "high", "type": "default_risk", "message": f"{member_name}: High risk ({final_risk*100:.1f}%)."})
    elif final_risk >= 0.45:
        alerts.append({"severity": "med", "type": "default_risk", "message": f"{member_name}: Moderate risk ({final_risk*100:.1f}%)."})

    if reliability < 45:
        alerts.append({"severity": "high", "type": "reliability", "message": f"{member_name}: Low reliability ({reliability}/100)."})
    elif reliability < 65:
        alerts.append({"severity": "med", "type": "reliability", "message": f"{member_name}: Moderate reliability ({reliability}/100)."})

    if dropout >= 0.70:
        alerts.append({"severity": "med", "type": "dropout", "message": f"{member_name}: High dropout risk ({dropout*100:.0f}%)."})

    if fraud >= 0.60:
        alerts.append({"severity": "high", "type": "fraud", "message": f"{member_name}: Strong anomaly signals ({fraud*100:.0f}%)."})
    elif fraud >= 0.35:
        alerts.append({"severity": "med", "type": "fraud", "message": f"{member_name}: Mild anomaly signals ({fraud*100:.0f}%)."})

    if liquidity_forecast.get("ok"):
        if float(liquidity_forecast.get("avg_daily_net", 0.0)) < 0:
            alerts.append({"severity": "med", "type": "liquidity", "message": "System liquidity trend is negative (avg daily net outflow)."})
    else:
        alerts.append({"severity": "low", "type": "liquidity", "message": "Liquidity forecast unavailable (missing history)."})

    return alerts


# ============================================================
# Local Chat Answer
# ============================================================
def local_chat_answer(question: str, context: dict) -> str:
    q = (question or "").lower().strip()

    if "top" in q and ("risk" in q or "risky" in q):
        top = context.get("top_risky", [])
        if not top:
            return "I don’t have enough risk data to compute top risky members."
        out = "### 🔴 Top risky members\n"
        for item in top:
            out += f"- {item['name']} → {item['risk']*100:.1f}%\n"
        return out

    if "liquid" in q or "foundation" in q or "cash" in q:
        lf = context.get("liquidity", {})
        if not lf.get("ok"):
            return f"Liquidity forecast not available: {lf.get('msg','missing data')}."
        return (
            "### 💰 Liquidity outlook (simple)\n"
            f"- Estimated net balance (approx): **{lf.get('balance_est', 0.0):,.0f}**\n"
            f"- Avg daily net flow (last ~30 days): **{lf.get('avg_daily_net', 0.0):,.1f}**\n"
            f"- Horizon: **{lf.get('horizon_days', 30)} days**\n"
        )

    if "alert" in q:
        alerts = context.get("alerts", [])
        if not alerts:
            return "No alerts generated right now."
        out = "### 🚨 Alerts\n"
        for a in alerts:
            out += f"- **{a['severity'].upper()}** [{a['type']}] — {a['message']}\n"
        return out

    if "recommend" in q or "approve" in q or "loan" in q:
        rec = context.get("loan_reco", None)
        if not rec:
            return "Loan recommendation is not available yet."
        out = f"### 🧾 Loan decision recommendation\n**Decision:** `{rec['decision']}`\n"
        for r in rec["reasons"]:
            out += f"- {r}\n"
        return out

    return (
        "I can help with: **top risky members**, **liquidity/foundation outlook**, **alerts**, and **loan recommendation**.\n\n"
        "Try: *'Top risky members'* or *'Is liquidity safe?'* or *'Any alerts?'*"
    )


# ============================================================
# Main panel renderer to embed in ai_risk_panel.py
# ============================================================
def render_ai_suite_panel(
    *,
    member_id: int,
    member_name: str,
    final_risk: float,
    row_features: pd.Series,
    X_all_members: pd.DataFrame,
    members_df: pd.DataFrame,
    contrib: pd.DataFrame,
    loans: pd.DataFrame,
    payments: pd.DataFrame,
    payouts: pd.DataFrame,
    foundation: pd.DataFrame,
):
    st.subheader("🧠 Extra AI Suite (Reliability • Dropout • Fraud • Liquidity • Loan Decision • Alerts • Chat)")

    reliability, rel_reasons = compute_reliability_score(row_features)
    dropout, drop_reasons = compute_dropout_risk(row_features)
    fraud, fraud_reasons = compute_fraud_anomaly_score(member_id, contrib, loans, payments)

    liquidity = foundation_liquidity_forecast_simple(
        contrib=contrib,
        foundation=foundation,
        loans=loans,
        payments=payments,
        payouts=payouts,
        horizon_days=30,
    )
    liquidity_ok = bool(liquidity.get("ok")) and float(liquidity.get("avg_daily_net", 0.0)) >= 0

    amt = st.number_input("Test Loan Amount (for recommendation)", min_value=0.0, value=3000.0, step=500.0)
    decision, dec_reasons = smart_loan_recommendation(
        risk=float(final_risk),
        reliability=int(reliability),
        liquidity_ok=bool(liquidity_ok),
        requested_amount=float(amt),
    )

    alerts = generate_ai_alerts(
        member_name=member_name,
        final_risk=float(final_risk),
        reliability=int(reliability),
        dropout=float(dropout),
        fraud=float(fraud),
        liquidity_forecast=liquidity,
    )

    # top risky (heuristic approximation: uses final_risk inputs elsewhere; here we just rank by your computed risk feature if present)
    top_risky = []
    try:
        if X_all_members is not None and not X_all_members.empty:
            tmp = X_all_members.copy()
            # if ai_risk_panel already computed heuristic risk per row, pass that instead.
            # fallback: rank by loan_balance_sum + inactivity
            tmp["rank_score"] = (
                pd.to_numeric(tmp.get("loan_balance_sum", 0), errors="coerce").fillna(0)
                + 0.5 * pd.to_numeric(tmp.get("days_since_last_payment", 0), errors="coerce").fillna(0)
                + 0.3 * pd.to_numeric(tmp.get("days_since_last_contrib", 0), errors="coerce").fillna(0)
            )
            tmp = tmp.sort_values("rank_score", ascending=False).head(5)
            for _, rr in tmp.iterrows():
                mid2 = int(rr["member_id"])
                nm2 = None
                if members_df is not None and not members_df.empty and "id" in members_df.columns:
                    mrow = members_df[members_df["id"].astype(int) == mid2]
                    if not mrow.empty:
                        nm2 = str(mrow.iloc[0].get("name") or mrow.iloc[0].get("full_name") or f"Member {mid2}")
                top_risky.append({"member_id": mid2, "name": nm2 or f"Member {mid2}", "risk": 0.0})
    except Exception:
        top_risky = []

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "✅ Reliability & Dropout",
        "🕵🏽 Fraud/Anomaly",
        "💰 Liquidity Forecast",
        "🧾 Loan Recommendation",
        "🚨 Alerts Center",
        "💬 Local Chatbox",
    ])

    with tab1:
        cA, cB, cC = st.columns(3)
        cA.metric("Reliability (0–100)", f"{reliability}")
        cB.metric("Dropout Risk", f"{dropout*100:.0f}%")
        cC.metric("Fraud/Anomaly", f"{fraud*100:.0f}%")

        st.write("**Reliability reasons**")
        for r in rel_reasons:
            st.write(f"• {r}")

        st.write("**Dropout risk reasons**")
        for r in drop_reasons:
            st.write(f"• {r}")

    with tab2:
        st.metric("Fraud/Anomaly Score", f"{fraud*100:.0f}%")
        st.progress(float(np.clip(fraud, 0.0, 1.0)))
        if fraud_reasons:
            st.write("**Signals detected**")
            for r in fraud_reasons:
                st.write(f"• {r}")
        else:
            st.info("No anomaly signals detected from current data.")

        st.caption("Note: lightweight anomaly detection (fast).")

    with tab3:
        if not liquidity.get("ok"):
            st.warning(liquidity.get("msg", "Liquidity forecast unavailable."))
        else:
            st.metric("Estimated Net Balance (approx)", f"{liquidity.get('balance_est', 0.0):,.0f}")
            st.metric("Avg Daily Net Flow (last ~30d)", f"{liquidity.get('avg_daily_net', 0.0):,.1f}")

            df_fc = pd.DataFrame({"date": liquidity["dates"], "forecast_balance": liquidity["forecast_balance"]})
            st.line_chart(df_fc.set_index("date"))

    with tab4:
        st.write(f"**Decision:** `{decision}`")
        for r in dec_reasons:
            st.write(f"• {r}")

    with tab5:
        if not alerts:
            st.success("No alerts generated.")
        else:
            for a in alerts:
                sev = a["severity"]
                msg = a["message"]
                if sev == "high":
                    st.error(msg)
                elif sev == "med":
                    st.warning(msg)
                else:
                    st.info(msg)

        if top_risky:
            st.write("**Top (approx) risky members**")
            st.dataframe(pd.DataFrame(top_risky), use_container_width=True)

    with tab6:
        st.caption("Local AI Chat (no API key).")
        if "local_ai_msgs" not in st.session_state:
            st.session_state.local_ai_msgs = []

        context = {
            "member_id": member_id,
            "member_name": member_name,
            "final_risk": float(final_risk),
            "reliability": int(reliability),
            "dropout": float(dropout),
            "fraud": float(fraud),
            "liquidity": liquidity,
            "alerts": alerts,
            "loan_reco": {"decision": decision, "reasons": dec_reasons},
            "top_risky": top_risky,
        }

        for role, msg in st.session_state.local_ai_msgs[-20:]:
            with st.chat_message(role):
                st.markdown(msg)

        q = st.chat_input("Ask: 'Any alerts?'  'Is liquidity safe?'  'Loan recommendation'")
        if q:
            st.session_state.local_ai_msgs.append(("user", q))
            ans = local_chat_answer(q, context)
            with st.chat_message("assistant"):
                st.markdown(ans)
            st.session_state.local_ai_msgs.append(("assistant", ans))

        with st.expander("🔎 Context (debug)", expanded=False):
            st.json(context)
