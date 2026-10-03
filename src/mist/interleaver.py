"""Interleaved queue building (穿插/交错练习 + 多样化练习).

A session pulls from three pools instead of one topic stack:
due review cards, a capped number of new cards, and extra weight for topics the
learner is objectively weak in. Cards are grouped by topic, taken round-robin,
and then reordered so the same topic never shows up more than twice in a row.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Sequence

from sqlalchemy import func, select

from .models import DEFAULT_EASE, MIN_EASE, Card, Review, Topic, utcnow
from .scheduler import mastery_level

DEFAULT_LIMIT = 20
DEFAULT_NEW_LIMIT = 10
MAX_CONSECUTIVE_SAME_TOPIC = 2
WEAK_BOOST_THRESHOLD = 0.35
RECENT_REVIEW_WINDOW_DAYS = 30


def due_cards(session, now: Optional[datetime] = None) -> list[Card]:
    """Cards already studied at least once whose due date has passed."""
    moment = now or utcnow()
    stmt = (
        select(Card)
        .where(Card.due_date <= moment, Card.last_reviewed_at.is_not(None))
        .order_by(Card.due_date.asc(), Card.id.asc())
    )
    return list(session.scalars(stmt))


def new_cards(session, now: Optional[datetime] = None) -> list[Card]:
    """Never-reviewed cards whose due date has passed (generation starts here)."""
    moment = now or utcnow()
    stmt = (
        select(Card)
        .where(Card.due_date <= moment, Card.last_reviewed_at.is_(None))
        .order_by(Card.id.asc())
    )
    return list(session.scalars(stmt))


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def topic_weakness(
    session,
    *,
    now: Optional[datetime] = None,
    window_days: int = RECENT_REVIEW_WINDOW_DAYS,
) -> dict[int, float]:
    """0..1 weakness per topic: low ease + many lapses + low recent ratings.

    Topics with no review history score 0.0 (unknown, not weak).
    """
    moment = now or utcnow()
    weakness: dict[int, float] = {}

    rows = session.execute(
        select(
            Card.topic_id,
            func.count(Card.id),
            func.avg(Card.ease),
            func.coalesce(func.sum(Card.lapses), 0),
        ).group_by(Card.topic_id)
    ).all()

    recent_rows = session.execute(
        select(Card.topic_id, Review.rating)
        .join(Card, Review.card_id == Card.id)
        .where(Review.reviewed_at >= moment - timedelta(days=window_days))
    ).all()
    recent: dict[int, list[int]] = {}
    for topic_id, rating in recent_rows:
        recent.setdefault(topic_id, []).append(rating)

    for topic_id, card_count, avg_ease, total_lapses in rows:
        card_count = max(1, int(card_count or 0))
        ease_term = _clamp((DEFAULT_EASE - float(avg_ease or DEFAULT_EASE)) / (DEFAULT_EASE - MIN_EASE))
        lapse_term = _clamp(int(total_lapses or 0) / card_count / 2)
        ratings = recent.get(topic_id)
        rating_term = _clamp((3.5 - (sum(ratings) / len(ratings))) / 3.5) if ratings else 0.0
        weakness[topic_id] = round(0.45 * ease_term + 0.25 * lapse_term + 0.30 * rating_term, 4)
    return weakness


def weakest_topics(session, *, top: int = 5, now: Optional[datetime] = None) -> list[tuple[Topic, float]]:
    """Weakness-ranked topics that have actually been studied."""
    moment = now or utcnow()
    weakness = topic_weakness(session, now=moment)
    studied = set(
        session.execute(
            select(Card.topic_id).where(Card.last_reviewed_at.is_not(None)).distinct()
        ).scalars()
    )
    ranked = sorted(
        ((tid, score) for tid, score in weakness.items() if tid in studied and score > 0),
        key=lambda item: (-item[1], item[0]),
    )[:top]
    if not ranked:
        return []
    topics = {
        t.id: t for t in session.scalars(select(Topic).where(Topic.id.in_([tid for tid, _ in ranked])))
    }
    return [(topics[tid], score) for tid, score in ranked if tid in topics]


def _group_by_topic(cards: Sequence[Card]) -> dict[int, list[Card]]:
    groups: dict[int, list[Card]] = {}
    for card in cards:
        groups.setdefault(card.topic_id, []).append(card)
    return groups


def _shuffle_within_groups(groups: dict[int, list[Card]], rng: random.Random) -> None:
    for cards in groups.values():
        rng.shuffle(cards)


def _streak_violated(queue: Sequence[Card], topic_id: int, limit: int) -> bool:
    if limit < 1 or len(queue) < limit:
        return False
    return all(card.topic_id == topic_id for card in queue[-limit:])


def enforce_topic_spread(
    cards: Sequence[Card], max_consecutive: int = MAX_CONSECUTIVE_SAME_TOPIC
) -> list[Card]:
    """Reorder greedily so a topic rarely repeats more than ``max_consecutive``.

    When only one topic is left its cards unavoidably run consecutively; the
    original order (due-first) is otherwise preserved.
    """
    pending = list(cards)
    result: list[Card] = []
    while pending:
        for index, card in enumerate(pending):
            if not _streak_violated(result, card.topic_id, max_consecutive):
                result.append(pending.pop(index))
                break
        else:
            result.append(pending.pop(0))
    return result


def _round_robin(
    groups: dict[int, list[Card]],
    weakness: dict[int, float],
    max_consecutive: int,
) -> list[Card]:
    remaining = {tid: list(cards) for tid, cards in groups.items()}
    order = sorted(remaining, key=lambda tid: (-weakness.get(tid, 0.0), tid))
    interleaved: list[Card] = []
    while any(remaining.values()):
        for tid in order:
            pool = remaining.get(tid)
            if not pool:
                continue
            budget = 2 if weakness.get(tid, 0.0) >= WEAK_BOOST_THRESHOLD else 1
            for _ in range(budget):
                if not pool:
                    break
                interleaved.append(pool.pop(0))
    return enforce_topic_spread(interleaved, max_consecutive)


@dataclass
class QueuePlan:
    cards: list[Card] = field(default_factory=list)
    due_available: int = 0
    new_available: int = 0
    new_taken: int = 0
    weak_topics: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def topics(self) -> list[int]:
        return sorted({card.topic_id for card in self.cards})


def build_review_queue(
    session,
    limit: int = DEFAULT_LIMIT,
    *,
    new_limit: int = DEFAULT_NEW_LIMIT,
    now: Optional[datetime] = None,
    seed: Optional[int] = None,
    max_consecutive: int = MAX_CONSECUTIVE_SAME_TOPIC,
) -> list[Card]:
    """Return the interleaved cards to study now: due reviews first, then new."""
    plan = build_review_plan(
        session,
        limit,
        new_limit=new_limit,
        now=now,
        seed=seed,
        max_consecutive=max_consecutive,
    )
    return plan.cards


def build_review_plan(
    session,
    limit: int = DEFAULT_LIMIT,
    *,
    new_limit: int = DEFAULT_NEW_LIMIT,
    now: Optional[datetime] = None,
    seed: Optional[int] = None,
    max_consecutive: int = MAX_CONSECUTIVE_SAME_TOPIC,
) -> QueuePlan:
    moment = now or utcnow()
    rng = random.Random(seed)
    weakness = topic_weakness(session, now=moment)

    due_pool = due_cards(session, moment)
    new_pool = new_cards(session, moment)

    due_order = _round_robin(_group_by_topic(due_pool), weakness, max_consecutive)
    new_groups = _group_by_topic(new_pool)
    _shuffle_within_groups(new_groups, rng)
    new_order = _round_robin(new_groups, weakness, max_consecutive)[: max(0, new_limit)]

    cards = (due_order + new_order)[: max(0, limit)]
    taken_new = {c.id for c in cards if c.id in {n.id for n in new_order}}
    weak_names = [
        topic.name
        for topic, _ in sorted(
            [(t, weakness.get(t.id, 0.0)) for t in session.scalars(select(Topic))],
            key=lambda item: -item[1],
        )
        if weakness.get(topic.id, 0.0) >= WEAK_BOOST_THRESHOLD
    ][:5]

    return QueuePlan(
        cards=cards,
        due_available=len(due_pool),
        new_available=len(new_pool),
        new_taken=len(taken_new),
        weak_topics=weak_names,
        note=f"到期 {len(due_pool)} 张 / 新卡 {len(new_pool)} 张（本次取 {len(taken_new)} 张）",
    )


def queue_summary(cards: Sequence[Card]) -> str:
    counts: dict[int, int] = {}
    for card in cards:
        counts[card.topic_id] = counts.get(card.topic_id, 0) + 1
    bands: dict[str, int] = {}
    for card in cards:
        bands[mastery_level(card)] = bands.get(mastery_level(card), 0) + 1
    parts = [f"{len(cards)} 张 / {len(counts)} 个主题"]
    parts.append("、".join(f"{k}:{v}" for k, v in sorted(bands.items())))
    return "；".join(parts)
