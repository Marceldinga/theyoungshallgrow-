from __future__ import annotations

import os
from typing import Optional, Tuple

try:
    from supabase import create_client
except Exception as e:
    raise RuntimeError("Missing dependency: supabase-py. Add `supabase` to requirements.txt") from e


def _clean_env_value(v: Optional[str]) -> str:
    if not v:
        return ""
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        v = v[1:-1].strip()
    return v


def build_clients() -> Tuple[object | None, object | None, str]:
    """
    Returns:
        sb_anon, sb_service, init_error
    """
    url = _clean_env_value(os.getenv("SUPABASE_URL"))
    anon = _clean_env_value(os.getenv("SUPABASE_ANON_KEY"))
    service = _clean_env_value(os.getenv("SUPABASE_SERVICE_KEY"))

    sb_anon = None
    sb_service = None
    init_error = ""

    if not url:
        return None, None, "SUPABASE_URL missing"

    if anon:
        try:
            sb_anon = create_client(url, anon)
        except Exception as e:
            init_error = f"Anon key error: {e}"

    if service:
        try:
            sb_service = create_client(url, service)
        except Exception as e:
            if init_error:
                init_error = init_error + f" | Service key error: {e}"
            else:
                init_error = f"Service key error: {e}"

    return sb_anon, sb_service, init_error
