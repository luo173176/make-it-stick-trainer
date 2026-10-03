from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from mist import db
from mist.models import Card, Topic, utcnow
from mist.scheduler import DEFAULT_EASE


@pytest.fixture
def engine():
    eng = db.build_engine("sqlite:///:memory:")
    db.init_engine(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with db.session_scope(engine) as sess:
        yield sess


@pytest.fixture
def make_topic(session):
    def _make(name: str, description: str = "") -> Topic:
        topic = session.scalar(select(Topic).where(Topic.name == name))
        if topic is not None:
            return topic
        topic = Topic(name=name, description=description)
        session.add(topic)
        session.flush()
        return topic

    return _make


@pytest.fixture
def make_card(session, make_topic):
    """Card factory: ``studied=True`` puts it in the due-review pool."""

    def _make(
        topic_name: str = "默认主题",
        question: str = "问题？",
        *,
        answer: str = "参考答案",
        studied: bool = False,
        due_offset_days: int = 0,
        ease: float = DEFAULT_EASE,
        interval: int = 0,
        repetitions: int = 0,
        lapses: int = 0,
        tags: str = "",
        source: str = "",
    ) -> Card:
        topic = make_topic(topic_name)
        moment = utcnow()
        card = Card(
            topic_id=topic.id,
            question=question,
            answer=answer,
            tags=tags,
            source=source,
            ease=ease,
            interval=interval,
            repetitions=repetitions,
            lapses=lapses,
            due_date=moment + timedelta(days=due_offset_days),
            last_reviewed_at=(moment - timedelta(days=1)) if studied else None,
        )
        session.add(card)
        session.flush()
        return card

    return _make


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0)
