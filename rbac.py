
# rbac.py ✅ COMPLETE UPDATED (matches your existing permission names)
# Fixes your errors:
# - Permission denied: requests/ledger/interest/delinquency for role 'admin'
# - Aligns loans_ui.py require(...) calls with the PERMISSIONS you already use
#
# Key idea:
# loans_ui.py should call require(role, "<one of these perms>"):
#   view_ledger, submit_request, sign_request, approve_deny,
#   record_payment, confirm_payment, reject_payment, accrue_interest,
#   view_delinquency, loan_statement, download_all_statements, legacy_loan_repayment

from __future__ import annotations

from dataclasses import dataclass

ROLE_ADMIN = "admin"
ROLE_TREASURY = "treasury"
ROLE_MEMBER = "member"

VALID_ROLES = {ROLE_ADMIN, ROLE_TREASURY, ROLE_MEMBER}

# What each role can see/do in Loans
PERMISSIONS: dict[str, set[str]] = {
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
        "legacy_loan_repayment",   # ✅ legacy repayments insert screen
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
        "legacy_loan_repayment",   # ✅ allow treasury too
    },
    ROLE_MEMBER: {
        "submit_request",
        "sign_request",
        "loan_statement",
        # Optional: allow members to view ledger read-only
        # "view_ledger",
    },
}


@dataclass(frozen=True)
class Actor:
    user_id: str
    role: str = ROLE_MEMBER
    member_id: int | None = None
    name: str | None = None


def normalize_role(role: str | None) -> str:
    r = (role or ROLE_MEMBER).strip().lower()
    return r if r in VALID_ROLES else ROLE_MEMBER


def resolve_role_by_member_id(sb, schema: str, member_id: int) -> str:
    """
    Looks up role from public.member_roles using member_id.
    Falls back to 'member' if no record or inactive.
    """
    try:
        rows = (
            sb.schema(schema)
            .table("member_roles")
            .select("role,is_active")
            .eq("member_id", int(member_id))
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        rows = []

    if not rows:
        return ROLE_MEMBER

    r = rows[0] or {}
    if r.get("is_active") is False:
        return ROLE_MEMBER

    return normalize_role(r.get("role"))


def can(actor_role: str, perm: str) -> bool:
    return perm in PERMISSIONS.get(normalize_role(actor_role), set())


def require(actor_role: str, perm: str):
    if not can(actor_role, perm):
        raise PermissionError(f"Permission denied: {perm} for role '{actor_role}'.")


def allowed_sections(actor_role: str) -> list[str]:
    """
    Returns the Loans UI menu sections allowed for the actor role.
    Order matters (mobile-friendly).
    """
    perms = PERMISSIONS.get(normalize_role(actor_role), set())
    sections: list[str] = []

    # Requests screen exists if user can submit/sign/approve
    if {"submit_request", "sign_request", "approve_deny"} & perms:
        sections.append("Requests")

    if "view_ledger" in perms:
        sections.append("Ledger")

    if "record_payment" in perms:
        sections.append("Record Payment")

    # Maker–checker confirmation
    if "confirm_payment" in perms:
        sections.append("Confirm Payments")

    # Optional separate reject menu (only if your UI actually has this section)
    if "reject_payment" in perms:
        sections.append("Reject Payments")

    if "accrue_interest" in perms:
        sections.append("Interest")

    if "view_delinquency" in perms:
        sections.append("Delinquency")

    if "loan_statement" in perms:
        sections.append("Loan Statement")

    # Admin/Treasury-only legacy direct insert
    if "legacy_loan_repayment" in perms:
        sections.append("Loan Repayment (Legacy)")

    return sections
