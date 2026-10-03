"""Simplified SM-2 spaced repetition scheduler (interval practice / 间隔练习).

Formulas follow the classic SM-2 update, reduced to what a learner can judge in
one keystroke:

    rating < 3  -> repetitions = 0, interval = 1, lapses += 1
    rating >= 3 -> interval = 1 (rep 0) | 6 (rep 1) | round(interval * ease)
    ease = max(1.3, ease + 0.1 - (5 - rating) * (0.08 + (5 - rating) * 0.02))

The interval is grown with the *current* ease; ease is updated afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .models import DEFAULT_EASE, MAX_DIFFICULTY, MIN_EASE, Card, Review, utcnow

PASS_THRESHOLD = 3
FIRST_INTERVAL = 1
SECOND_INTERVAL = 6

MASTERY_NEW = "新卡片"
MASTERY_LEARNING = "学习中"
MASTERY_CONSOLIDATING = "巩固中"
MASTERY_MASTERED = "已掌握"


class InvalidRating(ValueError):
    pass


@dataclass(frozen=True)
class Schedule:
    ease: float
    interval: int
    repetitions: int
    lapses: int
    due_date: datetime

    @property
    def due_label(self) -> str:
        if self.interval <= 0:
            return "现在"
        if self.interval == 1:
            return "明天"
        if self.interval < 30:
            return f"{self.interval} 天后"
        if self.interval < 365:
            return f"约 {self.interval / 30:.1f} 个月后"
        return f"约 {self.interval / 365:.1f} 年后"


def validate_rating(rating: int) -> int:
    """Accept whole numbers only: a silently truncated rating would rewrite the schedule.

    ``int(2.5) == 2`` is exactly the fuzz this scheduler must not absorb, and
    ``bool`` is an ``int`` subclass in Python, so both are rejected explicitly.
    """
    if isinstance(rating, bool) or not isinstance(rating, int):
        raise InvalidRating(f"评分必须是 0-5 的整数，收到 {rating!r}")
    if not 0 <= rating <= 5:
        raise InvalidRating(f"评分必须在 0-5 之间，收到 {rating}")
    return rating


def next_ease(ease: float, rating: int) -> float:
    delta = 5 - rating
    updated = ease + (0.1 - delta * (0.08 + delta * 0.02))
    return round(max(MIN_EASE, updated), 4)


def next_schedule(
    ease: float = DEFAULT_EASE,
    interval: int = 0,
    repetitions: int = 0,
    lapses: int = 0,
    rating: int = 5,
    now: Optional[datetime] = None,
) -> Schedule:
    """Pure SM-2 step: returns the schedule a card should move to after ``rating``."""
    rating = validate_rating(rating)
    moment = now or utcnow()

    if rating < PASS_THRESHOLD:
        new_interval, new_reps, new_lapses = FIRST_INTERVAL, 0, lapses + 1
    else:
        new_reps = repetitions + 1
        if repetitions == 0:
            new_interval = FIRST_INTERVAL
        elif repetitions == 1:
            new_interval = SECOND_INTERVAL
        else:
            new_interval = max(1, round(interval * ease))
        new_lapses = lapses

    return Schedule(
        ease=next_ease(ease, rating),
        interval=new_interval,
        repetitions=new_reps,
        lapses=new_lapses,
        due_date=moment + timedelta(days=new_interval),
    )


def review_card(
    session,
    card: Card,
    rating: int,
    *,
    user_answer: str = "",
    feedback: str = "",
    session_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Review:
    """Apply SM-2 to ``card``, persist the new state and append a review record."""
    moment = now or utcnow()
    rating = validate_rating(rating)

    schedule = next_schedule(
        ease=card.ease,
        interval=card.interval,
        repetitions=card.repetitions,
        lapses=card.lapses,
        rating=rating,
        now=moment,
    )
    elapsed_days = (moment - card.last_reviewed_at).days if card.last_reviewed_at else 0

    card.ease = schedule.ease
    card.interval = schedule.interval
    card.repetitions = schedule.repetitions
    card.lapses = schedule.lapses
    card.due_date = schedule.due_date
    card.last_reviewed_at = moment

    review = Review(
        card_id=card.id,
        session_id=session_id,
        rating=rating,
        user_answer=user_answer,
        feedback=feedback,
        reviewed_at=moment,
        elapsed_days=max(0, elapsed_days),
        scheduled_days=schedule.interval,
    )
    session.add(review)
    session.flush()
    return review


def mastery_level(card: Card) -> str:
    """Coarse mastery band used by stats and the interleaver."""
    if card.is_new:
        return MASTERY_NEW
    if card.interval >= 21 and card.repetitions >= 3 and card.ease >= 2.2:
        return MASTERY_MASTERED
    if card.interval >= 7:
        return MASTERY_CONSOLIDATING
    return MASTERY_LEARNING


def difficulty_hint(rating: int) -> str:
    """Remind the learner that easy retrieval is not the goal (合意困难)."""
    if rating >= 5:
        return "很轻松 —— 下次试着脱稿复述并举一个新例子。"
    if rating <= 2:
        return "想不起来很正常，失败的检索同样在强化记忆痕迹，别放弃。"
    return "努力回忆过的内容比重新阅读记得更牢。"


__all__ = [
    "DEFAULT_EASE",
    "MAX_DIFFICULTY",
    "MIN_EASE",
    "MASTERY_CONSOLIDATING",
    "MASTERY_LEARNING",
    "MASTERY_MASTERED",
    "MASTERY_NEW",
    "PASS_THRESHOLD",
    "InvalidRating",
    "Schedule",
    "difficulty_hint",
    "mastery_level",
    "next_ease",
    "next_schedule",
    "review_card",
    "utcnow",
]
