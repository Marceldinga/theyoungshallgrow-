# audit_panel.py ✅ AI AUDIT PANEL + schema-safe audit() logger (NO OpenAI, NO SQL)
# ------------------------------------------------------------------------------
# ✅ Fixes: AttributeError("module 'audit_panel' has no attribute 'render_audit_panel'")
# ✅ Adds: AI Audit Copilot ("Young") for audit logs (local heuristics, no external LLM)
# ✅ Keeps: schema-safe audit logger with real cached optional column checks
# ✅ Cache-safe: Supabase client params are prefixed with _sb in @st.cache_data
# ------------------------------------------------------------------------------

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from typing import Any, Iterable, Tuple

import pandas as pd
import streamlit as st


# ==============================================================================
# Small utils
# ==============================================================================
W_STRETCH = "stretch"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(s: str) -> str:
    return " ".join(str(s or "").lower().strip().split())


def _safe_int(x, default: int = 0) -> int:
    try:
        return int(float(x))
    except Exception:
        return int(default)


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


def _api_msg(e: Exception) -> str:
    return repr(e)


def _table_exists(sb, schema: str, table: str) -> bool:
    try:
        sb.schema(schema).table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def safe_table(sb, schema: str, table: str, cols: str = "*", limit: int = 2000, order_by: str | None = None, desc: bool = True):
    try:
        q = sb.schema(schema).table(table).select(cols)
        if order_by:
            q = q.order(order_by, desc=desc)
        q = q.limit(int(limit))
        return q.execute().data or []
    except Exception:
        return []


def safe_table_filter(sb, schema: str, table: str, cols: str = "*", limit: int = 2000, order_by: str | None = None, desc: bool = True, **eq_filters):
    try:
        q = sb.schema(schema).table(table).select(cols)
        for k, v in (eq_filters or {}).items():
            if v is None:
                continue
            q = q.eq(k, v)
        if order_by:
            q = q.order(order_by, desc=desc)
        q = q.limit(int(limit))
        return q.execute().data or []
    except Exception:
        return []


# ==============================================================================
# ✅ Schema-safe audit logger (your updated logic) + real cached optional column checks
# ==============================================================================
def _cols_key(cols: Iterable[str]) -> Tuple[str, ...]:
    return tuple(sorted({c.strip() for c in cols if c and str(c).strip()}))


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

    Minimum required columns: created_at, action, status
    Optional columns: details (json/text), actor_user_id
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
        # audit must never break the app
        pass


def audit_cache_clear() -> None:
    _shadow_cache.clear()


# ==============================================================================
# ✅ Cached reads (Streamlit-safe: supabase client must be `_sb`)
# ==============================================================================
@st.cache_data(ttl=30, show_spinner=False)
def _load_audit_log_cached(schema: str, _sb, limit: int = 800) -> pd.DataFrame:
    """
    Loads audit_log rows (best-effort) without crashing.
    Uses a broad '*' select because schema varies.
    """
    if _sb is None or not _table_exists(_sb, schema, "audit_log"):
        return pd.DataFrame()

    rows = safe_table(_sb, schema, "audit_log", "*", limit=int(limit), order_by="created_at", desc=True)
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    return df


# ==============================================================================
# 🤖 AI AUDIT COPILOT ("Young") — local heuristic intelligence (no external LLM)
# ==============================================================================
def _young_intro() -> str:
    return (
        "Hi 👋🏾 I’m **Young (Audit Copilot)**.\n\n"
        "I read your **audit_log** and help you quickly understand what’s happening:\n"
        "• Most common actions\n"
        "• Error spikes / failure reasons (from details)\n"
        "• Recent changes and patterns\n\n"
        "Try:\n"
        "• **summarize today**\n"
        "• **top actions**\n"
        "• **show errors**\n"
        "• **why are we failing?**\n"
        "• **last 50 events summary**"
    )


def _coerce_audit_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()

    if "created_at" in out.columns:
        out["created_at_dt"] = pd.to_datetime(out["created_at"], errors="coerce", utc=True)
    else:
        out["created_at_dt"] = pd.NaT

    for c in ["action", "status", "actor_user_id"]:
        if c in out.columns:
            out[c] = out[c].astype(str).fillna("").str.strip()

    # parse details (best effort)
    if "details" in out.columns:
        def _try_json(x):
            if x is None:
                return {}
            s = str(x).strip()
            if not s:
                return {}
            try:
                return json.loads(s) if s.startswith("{") or s.startswith("[") else {"raw": s[:500]}
            except Exception:
                return {"raw": s[:500]}
        out["_details_obj"] = out["details"].apply(_try_json)
    else:
        out["_details_obj"] = [{} for _ in range(len(out))]

    return out


def _audit_metrics(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {"rows": 0, "ok": 0, "fail": 0, "unknown": 0}

    status = df["status"].astype(str).str.lower() if "status" in df.columns else pd.Series([], dtype=str)
    ok = int((status == "ok").sum()) if not status.empty else 0
    fail = int((status.isin(["fail", "error", "failed"])).sum()) if not status.empty else 0
    unk = int(len(df) - ok - fail)
    return {"rows": int(len(df)), "ok": ok, "fail": fail, "unknown": unk}


def _top_actions(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    if df is None or df.empty or "action" not in df.columns:
        return pd.DataFrame()
    vc = df["action"].astype(str).value_counts().head(n)
    return vc.reset_index().rename(columns={"index": "action", "action": "count"})


def _extract_error_reasons(df: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    """
    Pulls likely error messages from details.raw or details fields.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    # filter failure-ish
    if "status" in df.columns:
        stt = df["status"].astype(str).str.lower()
        df2 = df[stt.isin(["fail", "error", "failed"])].copy()
    else:
        df2 = df.copy()

    if df2.empty:
        return pd.DataFrame()

    reasons = []
    for _, r in df2.iterrows():
        det = r.get("_details_obj", {}) or {}
        # common keys
        cand = (
            det.get("error")
            or det.get("message")
            or det.get("reason")
            or det.get("raw")
            or ""
        )
        s = str(cand).strip()
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
        return df

    now = pd.Timestamp.now(tz="UTC")
    if mode == "Last 24h":
        return df[df["created_at_dt"] >= (now - pd.Timedelta(hours=24))].copy()
    if mode == "Today":
        start = now.normalize()
        return df[df["created_at_dt"] >= start].copy()
    if mode == "Last 7d":
        return df[df["created_at_dt"] >= (now - pd.Timedelta(days=7))].copy()
    return df


def _young_answer(question: str, df_raw: pd.DataFrame) -> str:
    q = question.strip()
    if not q:
        return "Type a question."
    qn = _normalize_text(q)

    df = _coerce_audit_df(df_raw)

    if any(k in qn for k in ["hi", "hello", "introduce", "who are you", "your name"]):
        return _young_intro()

    # window keywords
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
        lines = [
            f"Audit summary ({window or 'loaded window'}):",
            f"• Rows: **{m['rows']}**",
            f"• OK: **{m['ok']}** • Fail/Error: **{m['fail']}** • Unknown: **{m['unknown']}**",
        ]
        if not top.empty:
            lines.append("Top actions: " + ", ".join([f"{r['action']}({int(r['count'])})" for _, r in top.iterrows()]))
        # errors
        reasons = _extract_error_reasons(df, 3)
        if not reasons.empty:
            lines.append("Top failure reasons: " + ", ".join([f"{r['reason']}({int(r['count'])})" for _, r in reasons.iterrows()]))
        return "\n".join(lines)

    if "top action" in qn or "most common" in qn:
        top = _top_actions(df, 10)
        if top.empty:
            return "I can’t compute top actions (missing `action` column or no rows)."
        return "Top actions:\n" + "\n".join([f"• {r['action']}: {int(r['count'])}" for _, r in top.iterrows()])

    if "error" in qn or "fail" in qn or "failed" in qn:
        m = _audit_metrics(df)
        reasons = _extract_error_reasons(df, 8)
        out = [f"Failures detected: **{m['fail']}**"]
        if reasons.empty:
            out.append("No clear failure reasons found in `details`. If you store error messages in details.error/message, I can summarize them.")
        else:
            out.append("Top failure reasons:")
            out.extend([f"• {r['reason']} — {int(r['count'])}" for _, r in reasons.iterrows()])
        return "\n".join(out)

    if "why" in qn and ("fail" in qn or "error" in qn):
        reasons = _extract_error_reasons(df, 5)
        if reasons.empty:
            return (
                "I can’t see a specific reason inside `details`.\n"
                "Best practice: when you call `audit(..., status='fail', details={...})`, include keys like:\n"
                "• `error`\n• `message`\n• `table`\n• `operation`\n"
            )
        return "Most likely causes (from details):\n" + "\n".join([f"• {r['reason']} ({int(r['count'])})" for _, r in reasons.iterrows()])

    if "last" in qn and any(k in qn for k in ["50", "100", "20"]):
        n = 50
        for k in ["100", "50", "20", "10"]:
            if k in qn:
                n = int(k)
                break
        df2 = df.head(n)
        m = _audit_metrics(df2)
        top = _top_actions(df2, 5)
        return (
            f"Last {n} events:\n"
            f"• OK: {m['ok']} • Fail/Error: {m['fail']} • Unknown: {m['unknown']}\n"
            + (("Top actions: " + ", ".join([f"{r['action']}({int(r['count'])})" for _, r in top.iterrows()])) if not top.empty else "")
        )

    return (
        "Try asking:\n"
        "• **summarize today**\n"
        "• **top actions**\n"
        "• **show errors**\n"
        "• **why are we failing?**\n"
        "• **last 50 events summary**\n"
        "Or click the quick buttons."
    )


# ==============================================================================
# UI: Audit Panel + AI
# ==============================================================================
def render_audit_panel(sb_anon=None, sb_service=None, schema: str = "public"):
    st.title("🧾 Audit")
    st.caption("Audit log viewer + AI Copilot (Young). Schema-safe, no SQL, no external LLM.")

    sb = sb_service if sb_service is not None else sb_anon
    if sb is None:
        st.error("No Supabase client provided (sb_anon/sb_service).")
        return

    if not _table_exists(sb, schema, "audit_log"):
        st.warning("audit_log table is not readable (missing table or RLS blocks SELECT).")
        st.caption("Tip: allow SELECT on audit_log for your service role, or read via sb_service.")
        return

    # Controls
    c1, c2, c3, c4 = st.columns([0.24, 0.22, 0.28, 0.26])
    with c1:
        limit = st.selectbox("Rows to load", [200, 400, 800, 1200], index=2)
    with c2:
        window = st.selectbox("Time window", ["All loaded", "Today", "Last 24h", "Last 7d"], index=1)
    with c3:
        status_filter = st.selectbox("Status filter", ["All", "ok", "fail/error"], index=0)
    with c4:
        if st.button("🔄 Refresh", width=W_STRETCH):
            st.cache_data.clear()
            st.rerun()

    df_raw = _load_audit_log_cached(schema, sb, limit=int(limit))
    df = _coerce_audit_df(df_raw)

    if df.empty:
        st.info("No audit rows found yet.")
    else:
        # window filter
        dfw = _time_window_filter(df, window) if window != "All loaded" else df.copy()

        # status filter
        if status_filter == "ok" and "status" in dfw.columns:
            dfw = dfw[dfw["status"].astype(str).str.lower() == "ok"].copy()
        elif status_filter == "fail/error" and "status" in dfw.columns:
            stt = dfw["status"].astype(str).str.lower()
            dfw = dfw[stt.isin(["fail", "error", "failed"])].copy()

        # Metrics
        m = _audit_metrics(dfw)
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Rows", f"{m['rows']}")
        k2.metric("OK", f"{m['ok']}")
        k3.metric("Fail/Error", f"{m['fail']}")
        k4.metric("Unknown", f"{m['unknown']}")

        # Top actions & reasons
        top = _top_actions(dfw, 10)
        reasons = _extract_error_reasons(dfw, 8)

        left, right = st.columns([1.05, 0.95])
        with left:
            st.markdown("### 📌 Log table")
            show_cols = [c for c in ["created_at", "action", "status", "actor_user_id", "details"] if c in dfw.columns]
            if not show_cols:
                show_cols = list(dfw.columns)[:8]
            st.dataframe(dfw[show_cols].head(int(limit)), width=W_STRETCH, hide_index=True)

            # CSV export
            try:
                csv = dfw[show_cols].to_csv(index=False).encode("utf-8")
                st.download_button("⬇️ Download CSV", data=csv, file_name="audit_log_export.csv", mime="text/csv", width=W_STRETCH)
            except Exception:
                pass

        with right:
            st.markdown("### 🤖 Young — Audit Copilot")
            st.caption("Local AI (no OpenAI). Answers from your audit_log.")

            if "young_audit_chat" not in st.session_state:
                st.session_state["young_audit_chat"] = [{"role": "assistant", "content": _young_intro()}]

            for msg in st.session_state["young_audit_chat"][-12:]:
                with st.chat_message(msg["role"]):
                    st.write(msg["content"])

            # quick buttons
            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("📅 Summarize today", width=W_STRETCH):
                    q = "summarize today"
                    st.session_state["young_audit_chat"].append({"role": "user", "content": q})
                    st.session_state["young_audit_chat"].append({"role": "assistant", "content": _young_answer(q, dfw)})
                    st.rerun()
            with b2:
                if st.button("🔥 Top actions", width=W_STRETCH):
                    q = "top actions"
                    st.session_state["young_audit_chat"].append({"role": "user", "content": q})
                    st.session_state["young_audit_chat"].append({"role": "assistant", "content": _young_answer(q, dfw)})
                    st.rerun()
            with b3:
                if st.button("🚨 Show errors", width=W_STRETCH):
                    q = "show errors"
                    st.session_state["young_audit_chat"].append({"role": "user", "content": q})
                    st.session_state["young_audit_chat"].append({"role": "assistant", "content": _young_answer(q, dfw)})
                    st.rerun()

            user_q = st.chat_input("Ask Young about audit logs…")
            if user_q:
                st.session_state["young_audit_chat"].append({"role": "user", "content": user_q})
                st.session_state["young_audit_chat"].append({"role": "assistant", "content": _young_answer(user_q, dfw)})
                st.rerun()

            st.divider()
            st.markdown("#### Quick insights")
            if not top.empty:
                st.markdown("**Top actions:**")
                st.dataframe(top, width=W_STRETCH, hide_index=True)
            if not reasons.empty:
                st.markdown("**Top failure reasons:**")
                st.dataframe(reasons, width=W_STRETCH, hide_index=True)

    with st.expander("🧪 Debug", expanded=False):
        st.write("schema:", schema)
        st.write("using:", "service" if sb_service is not None else "anon")
        st.write("loaded rows:", 0 if df is None else len(df))
        st.write("shadow cache size (column probes):", len(_shadow_cache))


# ==============================================================================
# Compatibility export (typo safety)
# ==============================================================================
def render_audit(sb_anon=None, sb_service=None, schema: str = "public"):
    return render_audit_panel(sb_anon=sb_anon, sb_service=sb_service, schema=schema)


__all__ = ["render_audit_panel", "render_audit", "audit", "audit_cache_clear"]
