# loans.py ✅ UPDATED (entry point)
from __future__ import annotations

from loans_ui import render_loans

def show_loans(sb_service, schema: str, actor_user_id: str = ""):
    """
    Entry point used by app.py (or router).
    sb_service: your authed/service client
    schema: e.g. "public" or your SUPABASE_SCHEMA
    actor_user_id: optional UUID from auth profile
    """
    return render_loans(sb_service, schema, actor_user_id=actor_user_id)
