"""Interleaving tests: topic spread, due priority, new-card cap, weakness weighting."""

from __future__ import annotations

from datetime import timedelta

from mist.interleaver import (
    MAX_CONSECUTIVE_SAME_TOPIC,
    build_review_plan,
    build_review_queue,
    due_cards,
    enforce_topic_spread,
    new_cards,
    queue_summary,
    topic_weakness,
    weakest_topics,
)
from mist.models import utcnow
from mist.scheduler import review_card


def _max_run(cards) -> int:
    best = run = 0
    for previous, current in zip(cards, cards[1:]):
        run = run + 1 if previous.topic_id == current.topic_id else 0
        best = max(best, run)
    return best + 1 if cards else 0


def test_queue_never_shows_three_of_the_same_topic_in_a_row(make_card, session):
    for topic in ("检索练习", "间隔练习", "交错练习"):
        for i in range(5):
            make_card(topic, f"{topic} 问题 {i}？", studied=True, due_offset_days=-1)

    queue = build_review_queue(session, limit=20, seed=7)

    assert len(queue) == 15
    assert _max_run(queue) <= MAX_CONSECUTIVE_SAME_TOPIC


def test_single_topic_queue_is_not_dropped(make_card, session):
    for i in range(6):
        make_card("只有一个主题", f"问题 {i}？", studied=True, due_offset_days=-1)

    queue = build_review_queue(session, limit=20, seed=1)
    assert len(queue) == 6  # spread constraint is relaxed when unavoidable


def test_enforce_topic_spread_preserves_membership():
    class Fake:
        def __init__(self, tid):
            self.topic_id = tid

    cards = [Fake(1) for _ in range(4)]
    assert len(enforce_topic_spread(cards, 2)) == 4


def test_due_reviews_come_before_new_cards(make_card, session):
    for i in range(4):
        make_card("到期主题", f"到期 {i}？", studied=True, due_offset_days=-1)
    for i in range(6):
        make_card("新卡主题", f"新卡 {i}？")

    queue = build_review_queue(session, limit=4, seed=3)

    assert len(queue) == 4
    assert all(card.last_reviewed_at is not None for card in queue)


def test_new_card_limit_caps_intake(make_card, session):
    for i in range(12):
        make_card("全是新卡", f"新卡 {i}？")

    plan = build_review_plan(session, limit=20, new_limit=3, seed=2)

    assert len(plan.cards) == 3
    assert plan.new_taken == 3
    assert plan.new_available == 12
    assert plan.due_available == 0


def test_due_and_new_pools_are_split_by_review_state(make_card, session):
    due = make_card("甲", "到期卡？", studied=True, due_offset_days=-1)
    fresh = make_card("乙", "新卡？")
    future = make_card("丙", "还没到期？", studied=True, due_offset_days=5)

    assert due_cards(session) == [due]
    assert new_cards(session) == [fresh]
    assert future not in due_cards(session) + new_cards(session)


def test_weak_topics_rank_a_lapsing_topic_higher(session, make_card):
    strong = make_card("已掌握主题", "简单？", studied=True, due_offset_days=30, interval=30, repetitions=4)
    weak = make_card("薄弱主题", "老忘？", studied=True, due_offset_days=-1, interval=1, repetitions=0, ease=1.6, lapses=3)

    review_card(session, strong, 5, now=utcnow())
    for offset in range(3):
        review_card(session, weak, 1, now=utcnow() + timedelta(days=offset))

    weakness = topic_weakness(session)
    ranked = [topic.name for topic, _ in weakest_topics(session)]

    assert weakness[weak.topic_id] > weakness[strong.topic_id]
    assert ranked[0] == "薄弱主题"


def test_weak_topics_get_extra_pulls(make_card, session):
    weak = make_card("薄弱", "弱？", studied=True, due_offset_days=-1, ease=1.5, lapses=4)
    make_card("薄弱", "弱2？", studied=True, due_offset_days=-1, ease=1.5, lapses=4)
    make_card("薄弱", "弱3？", studied=True, due_offset_days=-1, ease=1.5, lapses=4)
    strong = make_card("扎实", "稳？", studied=True, due_offset_days=-1, ease=2.5, lapses=0)
    make_card("扎实", "稳2？", studied=True, due_offset_days=-1, ease=2.5, lapses=0)
    make_card("扎实", "稳3？", studied=True, due_offset_days=-1, ease=2.5, lapses=0)
    review_card(session, weak, 1, now=utcnow())
    review_card(session, strong, 5, now=utcnow())

    queue = build_review_queue(session, limit=2, seed=5)

    assert {card.topic.name for card in queue} == {"薄弱"} or queue[0].topic.name == "薄弱"


def test_plan_note_and_summary_are_informative(make_card, session):
    make_card("甲", "a？", studied=True, due_offset_days=-1)
    make_card("乙", "b？")

    plan = build_review_plan(session, limit=10, seed=0)
    text = queue_summary(plan.cards)

    assert "到期 1 张" in plan.note
    assert "新卡 1 张" in plan.note
    assert "2 个主题" in text
