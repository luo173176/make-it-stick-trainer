"""HTTP contract tests for the optional FastAPI layer.

These exist because ``web.py`` once failed only at request time: postponed
annotations made FastAPI resolve the nested request models as query params, so
every POST returned 422 while the module imported cleanly. Import checks are
not enough — the routes have to be called.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from mist import db, interleaver
from mist.models import Card, Reflection, Review, Topic, utcnow
from mist.web import create_app

pytest.importorskip("fastapi", reason="pip install -e '.[web]'")
try:
    # Starlette raises RuntimeError (not ImportError) when no HTTP client backend
    # is installed, so a bare importorskip would take the whole suite down.
    from fastapi.testclient import TestClient
except (ImportError, RuntimeError) as exc:  # pragma: no cover - depends on extras
    pytest.skip(f"TestClient 需要 httpx2/httpx：pip install -e '.[dev]'（{exc}）", allow_module_level=True)


@pytest.fixture
def app_engine():
    engine = db.build_engine("sqlite:///:memory:")
    db.init_engine(engine)
    with db.session_scope(engine) as session:
        topic = Topic(name="检索练习")
        session.add(topic)
        session.flush()
        session.add(
            Card(
                topic_id=topic.id,
                question="为什么自测比重读有效？",
                answer="自测强化提取路径，重读只产生流畅度错觉",
                tags="错觉,自测",
            )
        )
    return engine


@pytest.fixture
def client(app_engine):
    with TestClient(create_app(app_engine)) as test_client:
        yield test_client


def first_card_id(client) -> int:
    return client.get("/api/queue").json()["cards"][0]["id"]


def test_home_page_serves_the_single_html_file(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "认知天性" in response.text
    assert "mist web" in response.text  # the file:// guard tells users how to start the backend


def test_queue_returns_cards_without_leaking_answers(client):
    payload = client.get("/api/queue").json()

    assert payload["session_id"]
    assert len(payload["cards"]) == 1
    assert payload["cards"][0]["question"]
    assert "answer" not in payload["cards"][0]


def test_check_returns_reference_and_coverage(client):
    card_id = first_card_id(client)
    body = client.post(
        "/api/check",
        json={"card_id": card_id, "user_answer": "自测让提取路径被强化，重读只是眼熟"},
    ).json()

    assert "流畅度错觉" in body["answer"]
    assert body["coverage"]["percent"] > 0
    assert body["hint"]


def test_grade_applies_sm2_and_records_history(client, app_engine):
    card_id = first_card_id(client)
    session_id = client.get("/api/queue").json()["session_id"]

    result = client.post(
        "/api/grade",
        json={"card_id": card_id, "rating": 4, "user_answer": "x", "session_id": session_id},
    ).json()

    assert result["interval"] == 1
    assert result["mastery"] == "学习中"

    with db.session_scope(app_engine) as session:
        card = session.get(Card, card_id)
        assert card.repetitions == 1
        assert card.due_date > utcnow()
        reviews = list(session.scalars(select(Review)))
        assert len(reviews) == 1
        assert reviews[0].session_id == session_id


def test_grade_rejects_out_of_range_rating(client):
    card_id = first_card_id(client)
    assert client.post("/api/grade", json={"card_id": card_id, "rating": 9}).status_code == 422


def test_grade_and_check_return_404_for_unknown_card(client):
    assert client.post("/api/check", json={"card_id": 999, "user_answer": ""}).status_code == 404
    assert client.post("/api/grade", json={"card_id": 999, "rating": 3}).status_code == 404


def test_reflect_persists_only_non_blank_answers(client, app_engine):
    assert client.post("/api/reflect", json={"session_id": "s-1"}).json() == {"saved": False}
    assert client.post(
        "/api/reflect",
        json={
            "session_id": "s-1",
            "core_concept": "检索练习",
            "connection": "考前刷题",
            "next_improvement": "先遮住答案",
        },
    ).json() == {"saved": True}

    with db.session_scope(app_engine) as session:
        stored = list(session.scalars(select(Reflection)))
        assert len(stored) == 1
        assert stored[0].next_improvement == "先遮住答案"


def test_stats_endpoint_reports_health_line(client):
    payload = client.get("/api/stats").json()

    assert payload["total_cards"] == 1
    assert "卡片 1 张" in payload["health_line"]
    assert payload["recent_days"]


def test_topics_endpoint_lists_weakness(client):
    rows = client.get("/api/topics").json()
    assert rows[0]["name"] == "检索练习"
    assert rows[0]["cards"] == 1


def test_add_card_creates_topic_and_validates_input(client, app_engine):
    created = client.post(
        "/api/cards",
        json={
            "topic": "交错练习",
            "question": "交错为什么有效？",
            "answer": "逼你先辨别题型",
            "difficulty": 9,
        },
    ).json()

    assert created["topic"] == "交错练习"
    assert created["difficulty"] == 5, "难度必须被夹到 1-5"
    assert client.post("/api/cards", json={"topic": "t", "question": " ", "answer": "a"}).status_code == 422

    with db.session_scope(app_engine) as session:
        assert session.scalar(select(Topic).where(Topic.name == "交错练习")) is not None
        assert interleaver.new_cards(session)
