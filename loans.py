
# loans.py ✅ UPDATED (entry point)
from __future__ import annotations

from loans_ui import render_loans


def show_loans(sb_service, schema: str, actor_user_id: str = ""):
    """
    Entry point used by app.py (or router).

    Parameters
    ----------
    sb_service:
        Authenticated / service Supabase client
    schema:
        Supabase schema (e.g. "public")
    actor_user_id:
        Optional user identifier for audit attribution
    """
    return render_loans(sb_service, schema, actor_user_id=actor_user_id)
