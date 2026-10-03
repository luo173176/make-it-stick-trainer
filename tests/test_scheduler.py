"""SM-2 simplified scheduling tests."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from mist.models import Card, Review
from mist.scheduler import (
    DEFAULT_EASE,
    MIN_EASE,
    InvalidRating,
    mastery_level,
    next_ease,
    next_schedule,
    review_card,
)

NOW = datetime(2026, 1, 1, 9, 0, 0)


def test_failing_rating_resets_repetitions_and_counts_lapse():
    schedule = next_schedule(
        ease=2.5, interval=30, repetitions=4, lapses=1, rating=2, now=NOW
    )
    assert schedule.repetitions == 0
    assert schedule.interval == 1
    assert schedule.lapses == 2
    assert schedule.due_date == NOW + timedelta(days=1)


def test_zero_rating_also_resets_and_lowers_ease():
    schedule = next_schedule(ease=2.5, interval=6, repetitions=2, lapses=0, rating=0, now=NOW)
    assert (schedule.interval, schedule.repetitions, schedule.lapses) == (1, 0, 1)
    assert schedule.ease < 2.5


def test_rating_three_is_a_pass_and_grows_interval():
    schedule = next_schedule(ease=2.5, interval=0, repetitions=0, lapses=0, rating=3, now=NOW)
    assert schedule.interval == 1
    assert schedule.repetitions == 1
    assert schedule.lapses == 0


def test_successful_reviews_follow_one_six_then_ease_growth():
    first = next_schedule(ease=2.5, interval=0, repetitions=0, lapses=0, rating=5, now=NOW)
    assert first.interval == 1 and first.repetitions == 1
    assert first.ease == pytest.approx(2.6)

    second = next_schedule(
        ease=first.ease, interval=first.interval, repetitions=first.repetitions, lapses=0, rating=5, now=NOW
    )
    assert second.interval == 6 and second.repetitions == 2
    assert second.ease == pytest.approx(2.7)

    third = next_schedule(
        ease=second.ease, interval=second.interval, repetitions=second.repetitions, lapses=0, rating=4, now=NOW
    )
    assert third.interval == round(6 * second.ease)
    assert third.repetitions == 3
    assert third.due_date == NOW + timedelta(days=third.interval)


def test_ease_never_drops_below_minimum():
    ease = DEFAULT_EASE
    for _ in range(30):
        ease = next_ease(ease, 0)
        assert ease >= MIN_EASE
    assert ease == pytest.approx(MIN_EASE)


def test_ease_formula_matches_sm2_delta():
    assert next_ease(2.5, 5) == pytest.approx(2.6)
    assert next_ease(2.5, 4) == pytest.approx(2.5)
    assert next_ease(2.5, 3) == pytest.approx(2.36)
    assert next_ease(2.5, 2) == pytest.approx(2.18)


@pytest.mark.parametrize("bad", [-1, 6, "x", None])
def test_invalid_rating_rejected(bad):
    with pytest.raises(InvalidRating):
        next_schedule(rating=bad, now=NOW)


def test_review_card_mutates_card_and_writes_history(session, make_card):
    card = make_card("间隔练习", "答错的卡片要怎么调度？", answer="缩短间隔并记录 lapse", studied=True)
    card.due_date = NOW
    card.last_reviewed_at = NOW - timedelta(days=5)

    review = review_card(
        session, card, 1, user_answer="缩短间隔", feedback="覆盖率 20%", session_id="s-1", now=NOW
    )
    session.flush()

    assert isinstance(review, Review)
    assert card.repetitions == 0
    assert card.interval == 1
    assert card.lapses == 1
    assert card.due_date == NOW + timedelta(days=1)
    assert card.last_reviewed_at == NOW
    assert review.elapsed_days == 5
    assert review.scheduled_days == 1
    assert review.user_answer == "缩短间隔"
    assert review.session_id == "s-1"


def test_mastery_bands_move_as_intervals_grow(session, make_card):
    fresh = make_card("校准", "新卡是什么状态？")
    assert mastery_level(fresh) == "新卡片"

    learning = make_card("校准", "刚学过？", studied=True, interval=1, repetitions=1)
    assert mastery_level(learning) == "学习中"

    consolidating = make_card("校准", "巩固中？", studied=True, interval=10, repetitions=2)
    assert mastery_level(consolidating) == "巩固中"

    mastered = make_card("校准", "掌握了？", studied=True, interval=30, repetitions=4, ease=2.5)
    assert mastery_level(mastered) == "已掌握"


def test_lapse_restarts_the_ladder_instead_of_jumping(session, make_card):
    card = make_card("交错", "答错后间隔会不会暴涨？", studied=True, interval=30, repetitions=4)
    review_card(session, card, 1, now=NOW)
    assert (card.interval, card.repetitions) == (1, 0)

    first_pass = review_card(session, card, 5, now=NOW + timedelta(days=1))
    assert first_pass.scheduled_days == 1
    assert card.repetitions == 1

    second_pass = review_card(session, card, 5, now=NOW + timedelta(days=2))
    assert second_pass.scheduled_days == 6
