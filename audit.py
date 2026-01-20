import json

def audit(c, action: str, status: str = "ok", details: dict | None = None, actor_user_id: str | None = None):
    """
    Schema-safe audit logger:
    Your schema_check only requires: id, created_at, action, status.
    If audit_log has optional columns like details/actor_user_id, we write them.
    """
    try:
        payload = {
            "created_at": _now_iso(),
            "action": action,
            "status": status,
        }

        # optional fields: only include if columns exist
        if _has_columns(c, "audit_log", ["details"]):
            payload["details"] = json.dumps(details or {}, default=str)
        if _has_columns(c, "audit_log", ["actor_user_id"]):
            payload["actor_user_id"] = actor_user_id

        c.table("audit_log").insert(payload).execute()
    except Exception:
        pass


def _has_columns(c, table: str, cols: list[str]) -> bool:
    try:
        sel = ",".join(cols)
        c.table(table).select(sel).limit(1).execute()
        return True
    except Exception:
        return False


def _now_iso():
    # avoid import cycles (db.py has now_iso too)
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
