from fastapi import HTTPException

from backend.core.constants import RELATIONS


def relation_guard(relation: str) -> None:
    if relation not in RELATIONS:
        raise HTTPException(status_code=400, detail=f"Relation not allowed: {relation}")
