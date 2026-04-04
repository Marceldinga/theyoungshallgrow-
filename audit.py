
# audit_panel.py
# ==============================================================================
# AI AUDIT PANEL + schema-safe audit() logger
# - No OpenAI
# - No SQL
# - Streamlit-safe
# - Supabase-safe
# - Includes render_audit_panel() to fix AttributeError
# ==============================================================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Tuple

import pandas as pd
import streamlit as st


# ==============================================================================
# Small utils
# ==============================================================================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(s: str) -> str:
    return " ".join(str(s or "").lower().strip().split())


def _safe_dt(x) -> pd.Timestamp | None:
    try:
        if x is None or str(x).strip() == "":
            return None
        dt = pd.to_datetime(x, errors="coerce", utc=True)
        if pd.isna(dt):
            return None
        return dt
    except Exception:
        return None


def _table_exists(sb, schema: str, table: str) -> bool:
    try:
        sb.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def safe_table(
    sb,
    schema: str,
    table: str,
    cols: str = "*",
    limit: int = 2000,
    order_by: str | None = None,
    desc: bool = True,
):
    try:
        q = sb.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=desc)
        q = q.limit(int(limit))
        result = q.execute()
        return result.data or []
    except Exception:
        return []


# ==============================================================================
# Schema-safe audit logger
# ==============================================================================
def _cols_key(cols: Iterable[str]) -> Tuple[str, ...]:
    return tuple(sorted({str(c).strip() for c in cols if c and str(c).strip()}))


_shadow_cache: dict[tuple[str, str, Tuple[str, ...]], bool] = {}


def _has_columns(c, schema: str, table: str, cols: list[str]) -> bool:
    key = (schema, table, _cols_key(cols))
    if key in _shadow_cache:
        return _shadow_cache[key]

    try:
        sel = ",".join(key[2])
        if not sel:
            _shadow_cache[key] = False
            return False

        c.schema(schema).table(table).select(sel).limit(1).execute()
        _shadow_cache[key] = True
        return True
    except Exception:
        _shadow_cache[key] = False
        return False


def audit(
    c,
    action: str,
    status: str = "ok",
    details: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
    schema: str = "public",
) -> None:
    """
    Schema-safe audit logger.

    Required columns:
      - created_at
      - action
      - status

    Optional columns:
      - details
      - actor_user_id
    """
    try:
        if c is None:
            return

        if not _table_exists(c, schema, "audit_log"):
            return

        payload: dict[str, Any] = {
            "created_at": _now_iso(),
            "action": action,
            "status": status,
        }

        if _has_columns(c, schema, "audit_log", ["details"]):
            payload["details"] = json.dumps(details or {}, default=str)

        if actor_user_id is not None and _has_columns(c, schema, "audit_log", ["actor_user_id"]):
            payload["actor_user_id"] = actor_user_id

        c.schema(schema).table("audit_log").insert(payload).execute()

    except Exception:
        # audit logging should never break the app
        pass


def audit_cache_clear() -> None:
    _shadow_cache.clear()


# ==============================================================================
# Cached audit loader
# ==============================================================================
@st.cache_data(ttl=30, show_spinner=False)
def _load_audit_log_cached(schema: str, _sb, limit: int = 800) -> pd.DataFrame:
    if _sb is None or not _table_exists(_sb, schema, "audit_log"):
        return pd.DataFrame()

    rows = safe_table(
        _sb,
        schema,
        "audit_log",
        cols="*",
        limit=int(limit),
        order_by="created_at",
        desc=True,
    )

    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ==============================================================================
# Local audit AI helper ("Young")
# ==============================================================================
def _young_intro() -> str:
    return (
        "Hi 👋🏾 I’m **Young (Audit Copilot)**.\n\n"
        "I can help you understand your **audit_log** quickly:\n"
        "- most common actions\n"
        "- failures and likely reasons\n"
        "- recent activity summary\n\n"
        "Try:\n"
        "- **summarize today**\n"
        "- **top actions**\n"
        "- **show errors**\n"
        "- **why are we failing?**\n"
        "- **last 50 events summary**"
    )


def _coerce_audit_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()

    if "created_at" in out.columns:
        out["created_at_dt"] = pd.to_datetime(out["created_at"], errors="coerce", utc=True)
    else:
        out["created_at_dt"] = pd.NaT

    for col in ["action", "status", "actor_user_id"]:
        if col in out.columns:
            out[col] = out[col].astype(str).fillna("").str.strip()

    if "details" in out.columns:
        def _try_json(x):
            if x is None:
                return {}
            s = str(x).strip()
            if not s:
                return {}
            try:
                if s.startswith("{") or s.startswith("["):
                    return json.loads(s)
                return {"raw": s[:500]}
            except Exception:
                return {"raw": s[:500]}

        out["_details_obj"] = out["details"].apply(_try_json)
    else:
        out["_details_obj"] = [{} for _ in range(len(out))]

    return out


def _audit_metrics(df: pd.DataFrame) -> dict[str, int]:
    if df is None or df.empty:
        return {"rows": 0, "ok": 0, "fail": 0, "unknown": 0}

    if "status" in df.columns:
        status = df["status"].astype(str).str.lower()
    else:
        status = pd.Series([], dtype=str)

    ok = int((status == "ok").sum()) if not status.empty else 0
    fail = int((status.isin(["fail", "error", "failed"])).sum()) if not status.empty else 0
    unknown = int(len(df) - ok - fail)

    return {"rows": int(len(df)), "ok": ok, "fail": fail, "unknown": unknown}


def _top_actions(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    if df is None or df.empty or "action" not in df.columns:
        return pd.DataFrame()

    vc = df["action"].astype(str).value_counts().head(n)
    return vc.reset_index().rename(columns={"index": "action", "action": "count"})


def _extract_error_reasons(df: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    if "status" in df.columns:
        stt = df["status"].astype(str).str.lower()
        df2 = df[stt.isin(["fail", "error", "failed"])].copy()
    else:
        df2 = df.copy()

    if df2.empty:
        return pd.DataFrame()

    reasons = []

    for _, row in df2.iterrows():
        det = row.get("_details_obj", {}) or {}
        candidate = (
            det.get("error")
            or det.get("message")
            or det.get("reason")
            or det.get("raw")
            or ""
        )
        s = str(candidate).strip()
        if s:
            reasons.append(s[:240])

    if not reasons:
        return pd.DataFrame()

    vc = pd.Series(reasons).value_counts().head(n)
    return vc.reset_index().rename(columns={"index": "reason", 0: "count"})


def _time_window_filter(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    if "created_at_dt" not in df.columns:
        return df.copy()

    now = pd.Timestamp.now(tz="UTC")

    if mode == "Last 24h":
        return df[df["created_at_dt"] >= (now - pd.Timedelta(hours=24))].copy()

    if mode == "Today":
        start = now.normalize()
        return df[df["created_at_dt"] >= start].copy()

    if mode == "Last 7d":
        return df[df["created_at_dt"] >= (now - pd.Timedelta(days=7))].copy()

    return df.copy()


def _young_answer(question: str, df_raw: pd.DataFrame) -> str:
    q = str(question or "").strip()
    if not q:
        return "Type a question."

    qn = _normalize_text(q)
    df = _coerce_audit_df(df_raw)

    if any(k in qn for k in ["hi", "hello", "introduce", "who are you", "your name"]):
        return _young_intro()

    window = None
    if "today" in qn:
        window = "Today"
    elif "24" in qn or "last day" in qn:
        window = "Last 24h"
    elif "week" in qn or "7d" in qn:
        window = "Last 7d"

    if window:
        df = _time_window_filter(df, window)

    if "summarize" in qn or "summary" in qn:
        m = _audit_metrics(df)
        top = _top_actions(df, 5)
        reasons = _extract_error_reasons(df, 3)

        lines = [
            f"Audit summary ({window or 'loaded window'}):",
            f"- Rows: **{m['rows']}**",
            f"- OK: **{m['ok']}**",
            f"- Fail/Error: **{m['fail']}**",
            f"- Unknown: **{m['unknown']}**",
        ]

        if not top.empty:
            lines.append(
                "Top actions: " +
                ", ".join([f"{r['action']}({int(r['count'])})" for _, r in top.iterrows()])
            )

        if not reasons.empty:
            lines.append(
                "Top failure reasons: " +
                ", ".join([f"{r['reason']}({int(r['count'])})" for _, r in reasons.iterrows()])
            )

        return "\n".join(lines)

    if "top action" in qn or "top actions" in qn or "most common" in qn:
        top = _top_actions(df, 10)
        if top.empty:
            return "I can’t compute top actions because there are no rows or the `action` column is missing."

        return "Top actions:\n" + "\n".join(
            [f"- {r['action']}: {int(r['count'])}" for _, r in top.iterrows()]
        )

    if "error" in qn or "fail" in qn or "failed" in qn:
        m = _audit_metrics(df)
        reasons = _extract_error_reasons(df, 8)

        out = [f"Failures detected: **{m['fail']}**"]

        if reasons.empty:
            out.append("No clear failure reasons found inside `details`.")
        else:
            out.append("Top failure reasons:")
            out.extend([f"- {r['reason']} — {int(r['count'])}" for _, r in reasons.iterrows()])

        return "\n".join(out)

    if "why" in qn and ("fail" in qn or "error" in qn):
        reasons = _extract_error_reasons(df, 5)
        if reasons.empty:
            return (
                "I can’t see a specific reason in `details`.\n"
                "Best practice: when you log failures, include keys like:\n"
                "- `error`\n"
                "- `message`\n"
                "- `table`\n"
                "- `operation`"
            )

        return "Most likely causes:\n" + "\n".join(
            [f"- {r['reason']} ({int(r['count'])})" for _, r in reasons.iterrows()]
        )

    if "last" in qn and any(k in qn for k in ["100", "50", "20", "10"]):
        n = 50
        for k in ["100", "50", "20", "10"]:
            if k in qn:
                n = int(k)
                break

        df2 = df.head(n)
        m = _audit_metrics(df2)
        top = _top_actions(df2, 5)

        msg = (
            f"Last {n} events:\n"
            f"- OK: {m['ok']}\n"
            f"- Fail/Error: {m['fail']}\n"
            f"- Unknown: {m['unknown']}"
        )

        if not top.empty:
            msg += "\nTop actions: " + ", ".join(
                [f"{r['action']}({int(r['count'])})" for _, r in top.iterrows()]
            )

        return msg

    return (
        "Try asking:\n"
        "- **summarize today**\n"
        "- **top actions**\n"
        "- **show errors**\n"
        "- **why are we failing?**\n"
        "- **last 50 events summary**"
    )


# ==============================================================================
# Main UI function
# ==============================================================================
def render_audit_panel(sb_anon=None, sb_service=None, schema: str = "public"):
    st.title("🧾 Audit")
    st.caption("Audit log viewer + local AI Copilot. No SQL. No external LLM.")

    sb = sb_service if sb_service is not None else sb_anon

    if sb is None:
        st.error("No Supabase client provided.")
        return

    if not _table_exists(sb, schema, "audit_log"):
        st.warning("`audit_log` table is not readable or does not exist.")
        st.caption("Use a service client if RLS blocks reads.")
        return

    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])

    with c1:
        limit = st.selectbox("Rows to load", [200, 400, 800, 1200], index=2)

    with c2:
        window = st.selectbox("Time window", ["All loaded", "Today", "Last 24h", "Last 7d"], index=1)

    with c3:
        status_filter = st.selectbox("Status filter", ["All", "ok", "fail/error"], index=0)

    with c4:
        if st.button("🔄 Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    df_raw = _load_audit_log_cached(schema, sb, limit=int(limit))
    df = _coerce_audit_df(df_raw)

    if df.empty:
        st.info("No audit rows found yet.")
    else:
        dfw = _time_window_filter(df, window) if window != "All loaded" else df.copy()

        if status_filter == "ok" and "status" in dfw.columns:
            dfw = dfw[dfw["status"].astype(str).str.lower() == "ok"].copy()
        elif status_filter == "fail/error" and "status" in dfw.columns:
            stt = dfw["status"].astype(str).str.lower()
            dfw = dfw[stt.isin(["fail", "error", "failed"])].copy()

        m = _audit_metrics(dfw)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Rows", str(m["rows"]))
        k2.metric("OK", str(m["ok"]))
        k3.metric("Fail/Error", str(m["fail"]))
        k4.metric("Unknown", str(m["unknown"]))

        top = _top_actions(dfw, 10)
        reasons = _extract_error_reasons(dfw, 8)

        left, right = st.columns([1.1, 0.9])

        with left:
            st.markdown("### 📌 Log table")
            show_cols = [c for c in ["created_at", "action", "status", "actor_user_id", "details"] if c in dfw.columns]
            if not show_cols:
                show_cols = list(dfw.columns)[:8]

            st.dataframe(dfw[show_cols].head(int(limit)), use_container_width=True, hide_index=True)

            try:
                csv_data = dfw[show_cols].to_csv(index=False).encode("utf-8")
                st.download_button(
                    "⬇️ Download CSV",
                    data=csv_data,
                    file_name="audit_log_export.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            except Exception:
                pass

        with right:
            st.markdown("### 🤖 Young — Audit Copilot")
            st.caption("Answers based only on your audit_log.")

            if "young_audit_chat" not in st.session_state:
                st.session_state["young_audit_chat"] = [
                    {"role": "assistant", "content": _young_intro()}
                ]

            for msg in st.session_state["young_audit_chat"][-12:]:
                with st.chat_message(msg["role"]):
                    st.write(msg["content"])

            b1, b2, b3 = st.columns(3)

            with b1:
                if st.button("📅 Summarize today", use_container_width=True):
                    q = "summarize today"
                    st.session_state["young_audit_chat"].append({"role": "user", "content": q})
                    st.session_state["young_audit_chat"].append(
                        {"role": "assistant", "content": _young_answer(q, dfw)}
                    )
                    st.rerun()

            with b2:
                if st.button("🔥 Top actions", use_container_width=True):
                    q = "top actions"
                    st.session_state["young_audit_chat"].append({"role": "user", "content": q})
                    st.session_state["young_audit_chat"].append(
                        {"role": "assistant", "content": _young_answer(q, dfw)}
                    )
                    st.rerun()

            with b3:
                if st.button("🚨 Show errors", use_container_width=True):
                    q = "show errors"
                    st.session_state["young_audit_chat"].append({"role": "user", "content": q})
                    st.session_state["young_audit_chat"].append(
                        {"role": "assistant", "content": _young_answer(q, dfw)}
                    )
                    st.rerun()

            user_q = st.chat_input("Ask Young about audit logs…")
            if user_q:
                st.session_state["young_audit_chat"].append({"role": "user", "content": user_q})
                st.session_state["young_audit_chat"].append(
                    {"role": "assistant", "content": _young_answer(user_q, dfw)}
                )
                st.rerun()

            st.divider()
            st.markdown("#### Quick insights")

            if not top.empty:
                st.markdown("**Top actions**")
                st.dataframe(top, use_container_width=True, hide_index=True)

            if not reasons.empty:
                st.markdown("**Top failure reasons**")
                st.dataframe(reasons, use_container_width=True, hide_index=True)

    with st.expander("🧪 Debug", expanded=False):
        st.write("schema:", schema)
        st.write("using:", "service" if sb_service is not None else "anon")
        st.write("loaded rows:", 0 if df is None else len(df))
        st.write("shadow cache size:", len(_shadow_cache))


# ==============================================================================
# Compatibility alias
# ==============================================================================
def render_audit(sb_anon=None, sb_service=None, schema: str = "public"):
    return render_audit_panel(sb_anon=sb_anon, sb_service=sb_service, schema=schema)


__all__ = ["render_audit_panel", "render_audit", "audit", "audit_cache_clear"]
