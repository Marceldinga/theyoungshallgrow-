# rbac.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

ROLE_ADMIN = "admin"
ROLE_TREASURY = "treasury"
ROLE_MEMBER = "member"

# What each role can see/do in Loans
PERMISSIONS = {
    ROLE_ADMIN: {
        "view_ledger",
        "submit_request",
        "sign_request",
        "approve_deny",
        "record_payment",
        "confirm_payment",
        "reject_payment",
        "accrue_interest",
        "view_delinquency",
        "loan_statement",
        "download_all_statements",
    },
    ROLE_TREASURY: {
        "view_ledger",
        "submit_request",
        "sign_request",
        "record_payment",
        "confirm_payment",
        "reject_payment",
        "accrue_interest",
        "view_delinquency",
        "loan_statement",
        "download_all_statements",
    },
    ROLE_MEMBER: {
        "submit_request",
        "sign_request",
        "loan_statement",
    },
}

@dataclass(frozen=True)
class Actor:
    user_id: str
    role: str = ROLE_MEMBER
    member_id: int | None = None
    name: str | None = None

def can(actor_role: str, perm: str) -> bool:
    return perm in PERMISSIONS.get(actor_role, set())

def allowed_sections(actor_role: str) -> list[str]:
    # UI sections shown per role
    perms = PERMISSIONS.get(actor_role, set())
    sections = []
    if "submit_request" in perms or "sign_request" in perms:
        sections.append("Requests")
    if "view_ledger" in perms:
        sections.append("Ledger")
    if "record_payment" in perms:
        sections.append("Record Payment")
    if "confirm_payment" in perms:
        sections.append("Confirm Payments")
    if "reject_payment" in perms:
        sections.append("Reject Payments")
    if "accrue_interest" in perms:
        sections.append("Interest")
    if "view_delinquency" in perms:
        sections.append("Delinquency")
    if "loan_statement" in perms:
        sections.append("Loan Statement")
    return sections

def require(actor_role: str, perm: str):
    if not can(actor_role, perm):
        raise PermissionError(f"Permission denied: {perm} for role '{actor_role}'.")
