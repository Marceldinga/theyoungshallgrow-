# njangi_tools_registry.py
# Small, dependency-free registry so there is NO circular import.

from __future__ import annotations
from typing import Dict, Callable, Any

def build_read_tools() -> Dict[str, Callable[..., Any]]:
    """
    Return READ tools lazily.
    IMPORTANT: Imports happen INSIDE this function to avoid circular imports.
    """

    # Import ONLY low-level tool modules here (no njangi_llm_panel, no app.py)
    # Example: from njangi_db_tools import tool_members, tool_loans

    tools: Dict[str, Callable[..., Any]] = {}

    # Example placeholders (replace with your real tool functions)
    # tools["list_members"] = tool_members.list_members
    # tools["member_summary"] = tool_members.member_summary
    # tools["loan_status"] = tool_loans.loan_status

    return tools


def build_write_tools() -> Dict[str, Callable[..., Any]]:
    tools: Dict[str, Callable[..., Any]] = {}
    return tools
