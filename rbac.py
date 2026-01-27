
# rbac.py ✅ COMPLETE UPDATED (adds aliases to match loans_ui.py + your existing permission names)
# Fixes:
# - Permission denied: legacy_repayment for role 'admin'
# - Permission denied: confirm_payments for role 'admin'
# - Keeps your canonical permission names:
#   view_ledger, submit_request, sign_request, approve_deny,
#   record_payment, confirm_payment, reject_payment, accrue_interest,
#   view_delinquency, loan_statement, download_all_statements, legacy_loan_repayment
#
# ✅ KEY UPGRADE:
# Adds PERMISSION_ALIASES so loans_ui.py can call:
#   confirm_payments -> confirm_payment
#   legacy_repayment -> legacy_loan_repayment
#   (and a few other safe mappings)

from __future__ import annotations

from dataclasses import dataclass

ROLE_ADMIN = "admin"
ROLE_TREASURY = "treasury"
ROLE_MEMBER = "member"

VALID_ROLES = {ROLE_ADMIN, ROLE_TREASURY, ROLE_MEMBER}

# ============================================================
# Canonical permissions by role
# ============================================================
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
        "legacy_loan_repayment",
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
        "legacy_loan_repayment",
    },
    ROLE_MEMBER: {
        "submit_request",
        "sign_request",
        "loan_statement",
        # optional:
        # "view_ledger",
    },
}

# ============================================================
# Aliases to support older/alternate perm names used in UI files
# ============================================================
PERMISSION_ALIASES: dict[str, str] = {
    # loans_ui.py aliases (your current crash)
    "confirm_payments": "confirm_payment",
    "legacy_repayment": "legacy_loan_repayment",

    # common pluralization / variations (safe)
    "confirm_payment": "confirm_payment",
    "reject_payments": "reject_payment",
    "confirm_payment_pending": "confirm_payment",
    "reject_payment_pending": "reject_payment",

    # legacy alternative wording
    "legacy_payment": "legacy_loan_repayment",
    "legacy_repay": "legacy_loan_repayment",
}

def _canon_perm(perm: str) -> str:
    p = (perm or "").strip()
    return PERMISSION_ALIASES.get(p, p)


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
    role = normalize_role(actor_role)
    canon = _canon_perm(perm)
    return canon in PERMISSIONS.get(role, set())


def require(actor_role: str, perm: str):
    canon = _canon_perm(perm)
    if not can(actor_role, canon):
        raise PermissionError(f"Permission denied: {perm} for role '{actor_role}'.")


def allowed_sections(actor_role: str) -> list[str]:
    """
    Returns the Loans UI menu sections allowed for the actor role.
    MUST match the section strings used in loans_ui.py render_loans().
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

    # ✅ loans_ui.py implements Confirm Payments (checker)
    if "confirm_payment" in perms:
        sections.append("Confirm Payments")

    # ❗ Only include Reject Payments if you actually implemented a section for it in loans_ui.py
    # If your loans_ui.py currently shows "enabled but not implemented" for Reject Payments,
    # comment this out to stop showing it in the menu.
    # if "reject_payment" in perms:
    #     sections.append("Reject Payments")

    if "accrue_interest" in perms:
        sections.append("Interest")

    if "view_delinquency" in perms:
        sections.append("Delinquency")

    if "loan_statement" in perms:
        sections.append("Loan Statement")

    if "legacy_loan_repayment" in perms:
        sections.append("Loan Repayment (Legacy)")

    return sections
