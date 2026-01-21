
# rbac.py
from dataclasses import dataclass

ROLE_ADMIN = "admin"
ROLE_TREASURY = "treasury"
ROLE_MEMBER = "member"

@dataclass(frozen=True)
class Actor:
    user_id: str
    role: str
    member_id: int

def resolve_role_by_member_id(sb, schema: str, member_id: int) -> str:
    rows = (
        sb.schema(schema)
        .table("member_roles")
        .select("role,is_active")
        .eq("member_id", int(member_id))
        .limit(1)
        .execute()
        .data or []
    )
    if not rows:
        return ROLE_MEMBER
    r = rows[0]
    if r.get("is_active") is False:
        return ROLE_MEMBER
    role = str(r.get("role") or ROLE_MEMBER).lower().strip()
    return role if role in {ROLE_ADMIN, ROLE_TREASURY, ROLE_MEMBER} else ROLE_MEMBER
