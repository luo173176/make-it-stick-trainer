"""Reflection and elaboration prompts (反思 / 细化)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from .models import Reflection, utcnow

CORE_CONCEPT = "今天最核心的概念/结论是什么？（用自己的话，一句话）"
CONNECTION = "它和你已知的什么东西有联系？（旧知识、别的主题、真实经历）"
NEXT_IMPROVEMENT = "下次练习你会怎么改进？（具体一个动作）"

REFLECTION_QUESTIONS: tuple[str, str, str] = (CORE_CONCEPT, CONNECTION, NEXT_IMPROVEMENT)


def make_session_id(now: Optional[datetime] = None) -> str:
    moment = now or utcnow()
    return f"{moment:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def record_reflection(
    session,
    session_id: str,
    core_concept: str,
    connection: str,
    next_improvement: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[Reflection]:
    """Store a reflection; blank answers are not recorded."""
    values = [(core_concept or "").strip(), (connection or "").strip(), (next_improvement or "").strip()]
    if not any(values):
        return None
    reflection = Reflection(
        session_id=session_id,
        core_concept=values[0],
        connection=values[1],
        next_improvement=values[2],
        created_at=now or utcnow(),
    )
    session.add(reflection)
    session.flush()
    return reflection


def recent_reflections(session, limit: int = 5) -> list[Reflection]:
    stmt = select(Reflection).order_by(Reflection.created_at.desc(), Reflection.id.desc()).limit(limit)
    return list(session.scalars(stmt))


def is_blank(reflection: Optional[Reflection]) -> bool:
    if reflection is None:
        return True
    return not any(
        (getattr(reflection, name) or "").strip()
        for name in ("core_concept", "connection", "next_improvement")
    )
