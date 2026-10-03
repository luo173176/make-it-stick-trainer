"""Optional FastAPI front-end. The CLI is the reference implementation; this is
the same engine behind a single HTML page (``templates/index.html``).

``from __future__ import annotations`` is deliberately absent: FastAPI has to
resolve the request models declared inside ``create_app``, and stringified
annotations for locally-defined classes make it fall back to query params.
"""

import json
from dataclasses import asdict
from importlib import resources
from typing import Optional

from sqlalchemy import select

from . import db, generator, interleaver, reflection
from .models import Card, Topic, utcnow
from .scheduler import mastery_level, review_card
from .stats import compute_stats

WEB_MISSING = (
    "Web 界面需要额外依赖：pip install -e '.[web]'（安装 fastapi 与 uvicorn）。"
)


def index_html() -> str:
    return (
        resources.files("mist")
        .joinpath("templates/index.html")
        .read_text(encoding="utf-8")
    )


def _card_json(card: Card) -> dict:
    return {
        "id": card.id,
        "topic": card.topic.name if card.topic else "",
        "question": card.question,
        "difficulty": card.difficulty,
        "tags": card.tag_list,
        "source": card.source,
        "mastery": mastery_level(card),
        "interval": card.interval,
        "ease": round(card.ease, 2),
        "due_date": card.due_date.isoformat() if card.due_date else None,
    }


def _coverage_json(coverage: generator.Coverage) -> dict:
    return asdict(coverage) | {"percent": coverage.percent}


def create_app(engine):
    try:
        from fastapi import Body, Depends, FastAPI, HTTPException
        from fastapi.responses import HTMLResponse, JSONResponse
        from pydantic import BaseModel
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError(WEB_MISSING) from exc

    app = FastAPI(title="make-it-stick-trainer", version="0.1.0")

    def get_session():
        with db.session_scope(engine) as session:
            yield session

    class CheckRequest(BaseModel):
        card_id: int
        user_answer: str = ""

    class GradeRequest(BaseModel):
        card_id: int
        rating: int
        user_answer: str = ""
        coverage_summary: str = ""
        session_id: Optional[str] = None

    class ReflectRequest(BaseModel):
        session_id: str
        core_concept: str = ""
        connection: str = ""
        next_improvement: str = ""

    class CardRequest(BaseModel):
        topic: str
        question: str
        answer: str
        tags: str = ""
        source: str = ""
        difficulty: int = 3

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return index_html()

    @app.get("/api/queue")
    def queue(limit: int = 20, new_limit: int = 10, session=Depends(get_session)):
        plan = interleaver.build_review_plan(session, limit, new_limit=new_limit)
        return {
            "session_id": reflection.make_session_id(),
            "note": plan.note,
            "summary": interleaver.queue_summary(plan.cards),
            "weak_topics": plan.weak_topics,
            "cards": [_card_json(c) for c in plan.cards],
        }

    @app.post("/api/check")
    def check(payload: CheckRequest, session=Depends(get_session)):
        card = session.get(Card, payload.card_id)
        if card is None:
            raise HTTPException(404, "卡片不存在")
        coverage = generator.score_answer(card.answer, payload.user_answer)
        return {
            "answer": card.answer,
            "coverage": _coverage_json(coverage),
            "hint": generator.refinement_hint(coverage),
        }

    @app.post("/api/grade")
    def grade(payload: GradeRequest, session=Depends(get_session)):
        card = session.get(Card, payload.card_id)
        if card is None:
            raise HTTPException(404, "卡片不存在")
        try:
            review_card(
                session,
                card,
                payload.rating,
                user_answer=payload.user_answer,
                feedback=payload.coverage_summary,
                session_id=payload.session_id,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "interval": card.interval,
            "ease": round(card.ease, 2),
            "repetitions": card.repetitions,
            "lapses": card.lapses,
            "due_date": card.due_date.isoformat(),
            "mastery": mastery_level(card),
        }

    @app.post("/api/reflect")
    def reflect(payload: ReflectRequest, session=Depends(get_session)):
        saved = reflection.record_reflection(
            session,
            payload.session_id,
            payload.core_concept,
            payload.connection,
            payload.next_improvement,
        )
        return {"saved": saved is not None}

    @app.get("/api/stats")
    def stats_endpoint(session=Depends(get_session)):
        data = compute_stats(session)
        payload = asdict(data)
        payload["health_line"] = data.health_line
        payload["recent_days"] = [[str(day), count] for day, count in data.recent_days]
        payload["generated_at"] = data.generated_at.isoformat()
        return payload

    @app.get("/api/topics")
    def topics(session=Depends(get_session)):
        weakness = interleaver.topic_weakness(session)
        return [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "cards": len(t.cards),
                "weakness": round(weakness.get(t.id, 0.0), 3),
            }
            for t in session.scalars(select(Topic).order_by(Topic.name))
        ]

    @app.post("/api/cards")
    def add_card(payload: CardRequest, session=Depends(get_session)):
        if not payload.question.strip() or not payload.answer.strip():
            raise HTTPException(422, "question 与 answer 都不能为空")
        topic = session.scalar(select(Topic).where(Topic.name == payload.topic.strip()))
        if topic is None:
            topic = Topic(name=payload.topic.strip())
            session.add(topic)
            session.flush()
        card = Card(
            topic_id=topic.id,
            question=payload.question.strip(),
            answer=payload.answer.strip(),
            source=payload.source.strip(),
            tags=payload.tags.strip(),
            difficulty=max(1, min(5, payload.difficulty)),
            due_date=utcnow(),
        )
        session.add(card)
        session.flush()
        return _card_json(card)

    return app


def serve(engine, *, host: str = "127.0.0.1", port: int = 8000) -> int:
    try:
        import uvicorn
    except ImportError:
        print(json.dumps({"error": WEB_MISSING}, ensure_ascii=False))
        return 2
    app = create_app(engine)
    print(f"Web 界面： http://{host}:{port}  （Ctrl-C 退出）")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
