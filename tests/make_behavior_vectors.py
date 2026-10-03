"""Regenerate tests/vectors/behavior.json from the Python reference implementation.

The browser build (web/core.js) is a second implementation of the same rules.
This file is the contract between them: run ``python tests/make_behavior_vectors.py``
whenever src/mist/{scheduler,interleaver,generator,stats}.py changes, review the
diff, and the JS suite (web/test/core.test.mjs) fails until web/core.js agrees.

Vectors are produced through a real SQLite session rather than a stub, so the
recorded expectations come from the same code path the CLI uses.

Times are fixed and emitted both as naive-UTC ISO strings and epoch millis so
neither side can drift on parsing or timezones.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select
from sqlalchemy.orm import Session

from mist import db, generator, interleaver
from mist.models import DEFAULT_EASE, Card, Review, Topic
from mist.scheduler import PASS_THRESHOLD, mastery_level, next_schedule
from mist.stats import compute_stats, review_streak

HERE = Path(__file__).resolve().parent
OUT = HERE / "vectors" / "behavior.json"

NOW = datetime(2026, 1, 15, 12, 0, 0)
NOW_MS = int(NOW.replace(tzinfo=timezone.utc).timestamp() * 1000)
DAY_MS = 86_400_000

TOPIC_NAMES = {1: "检索练习", 2: "间隔与交错", 3: "校准与元认知", 4: "生成与细化", 5: "合意困难"}


def ms(moment: datetime) -> int:
    return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000)


def at(day_offset: float) -> datetime:
    return NOW + timedelta(days=day_offset)


# --------------------------------------------------------------- fixtures

SCHEDULER_SEQUENCES = [
    (DEFAULT_EASE, 0, 0, 0, [5]),
    (DEFAULT_EASE, 0, 0, 0, [5, 5]),
    (DEFAULT_EASE, 0, 0, 0, [5, 5, 4]),
    (DEFAULT_EASE, 0, 0, 0, [5, 5, 5, 5, 5]),
    (DEFAULT_EASE, 0, 0, 0, [1]),
    (2.6, 30, 4, 1, [2]),
    (2.6, 30, 4, 1, [0]),
    (2.6, 30, 4, 1, [3]),
    (1.3, 1, 0, 9, [0]),
    (1.4, 6, 1, 2, [5]),
    (2.5, 0, 0, 0, [4, 4, 4, 4, 1, 5, 5]),
]

MASTERY_CARDS = [
    {"ease": DEFAULT_EASE, "interval": 0, "repetitions": 0, "lapses": 0, "studied": False},
    {"ease": DEFAULT_EASE, "interval": 1, "repetitions": 1, "lapses": 0, "studied": True},
    {"ease": DEFAULT_EASE, "interval": 7, "repetitions": 2, "lapses": 0, "studied": True},
    {"ease": 2.2, "interval": 21, "repetitions": 3, "lapses": 0, "studied": True},
    {"ease": 2.1, "interval": 40, "repetitions": 5, "lapses": 0, "studied": True},
    {"ease": 2.5, "interval": 21, "repetitions": 2, "lapses": 1, "studied": True},
]

SEED_ANSWER = (
    "重复阅读只产生流畅度错觉：材料看着眼熟，但提取路径没有被强化。"
    "记忆的可用度取决于检索练习的次数，把相同时间用来自测、回忆、做练习，比重新阅读记得更牢。"
)
QA_ANSWER = "问答题要求从记忆里生成答案，检索路径被真正走一遍；选择题只需再认。"

COVERAGE_CASES = [
    (SEED_ANSWER, "重读只是眼熟，提取路径没有被走过；自测和回忆才让记忆可用，把时间花在检索练习上。"),
    (SEED_ANSWER, SEED_ANSWER),
    (SEED_ANSWER, "".join(reversed(SEED_ANSWER))),
    (SEED_ANSWER, ""),
    (SEED_ANSWER, "   \n  "),
    (SEED_ANSWER, "投壶研究中混合角度的组最后成绩比单一角度组高约两倍。"),
    (QA_ANSWER, "简答题必须自己把答案从记忆里造出来，等于真走了一遍检索通路；选择题只要认出选项就行。"),
    (QA_ANSWER, "简答题要自己生成"),
    ("Spacing practice weakens associations but strengthens retrieval.",
     "Practice spacing weakens associations and strengthens retrieval."),
    ("Spacing practice weakens associations but strengthens retrieval.", "Retrieval helps."),
    ("的了是在", "随便写点什么"),
    ("SM-2 里 rating 小于 3 会重置 repetitions，并把 lapses 加 1。", "rating 小于 3 就重置，lapses 加一"),
    ("间隔练习让每次提取都在半遗忘状态下重建线索，长期保持更好。", "间隔练习让提取重建线索，长期保持更好。"),
    ("强化记忆痕迹并让知识更容易被后续学习吸收", "记忆痕迹被强化，知识也吸收了"),
]

# (id, topic, ease, interval, repetitions, lapses, due_offset_days, studied)
WEAKNESS_CARDS = [
    (1, 1, 1.5, 1, 0, 4, -1, True),
    (2, 1, 1.5, 1, 0, 4, -1, True),
    (3, 1, 1.6, 2, 1, 5, -2, True),
    (4, 2, 2.6, 30, 4, 0, 20, True),
    (5, 2, 2.6, 40, 5, 0, 30, True),
    (6, 3, DEFAULT_EASE, 0, 0, 0, 0, False),
    (7, 3, DEFAULT_EASE, 0, 0, 0, 0, False),
]
WEAKNESS_REVIEWS = [(1, 1, 1), (1, 2, 5), (2, 1, 2), (4, 5, 3), (5, 5, 1), (3, 5, 100)]

QUEUE_CARDS = [
    (1, 1, -3, True),
    (2, 1, -2, True),
    (3, 1, -1, True),
    (4, 1, -1, True),
    (5, 2, -4, True),
    (6, 2, -2, True),
    (7, 2, -1, True),
    (8, 3, -5, True),
    (9, 3, -3, True),
    (10, 3, -1, True),
    (11, 4, 0, False),
    (12, 4, 0, False),
    (13, 5, 0, False),
    (14, 5, 0, False),
    (15, 5, 0, False),
]

SPREAD_INPUTS = [
    [1, 1, 1, 1, 1, 1],
    [1, 1, 2],
    [1, 2, 1, 2, 1],
    [1, 1, 1],
    [1, 1, 1, 2],
    [1, 1, 1, 2, 3, 1, 1, 1],
    [],
]

STREAK_CASES = [[0], [0, 0, 0], [1, 2, 3], [0, 1, 2, 4], [2, 3, 4], [], [0, 1, 2, 3, 5, 6]]

SUMMARY_CARDS = [
    (1, 1, 2.6, 21, 3, 0, -1, True),
    (2, 1, 1.6, 1, 0, 3, -2, True),
    (3, 2, DEFAULT_EASE, 0, 0, 0, 0, False),
    (4, 2, 2.5, 7, 2, 1, 5, True),
    (5, 3, 2.4, 12, 3, 0, -10, True),
]
SUMMARY_REVIEWS = [(1, 5, 0), (2, 1, 0), (5, 3, 1), (4, 4, 2), (1, 4, 3)]


# ------------------------------------------------------------------ helpers


def normalize_card_row(row):
    """Accept the short (id, topic, due_offset_days, studied) form."""
    if len(row) == 4:
        card_id, topic_id, due_offset, studied = row
        return (card_id, topic_id, DEFAULT_EASE, 0, 0, 0, due_offset, studied)
    return row


def card_rows(rows):
    return [
        {
            "id": card_id,
            "topicId": topic_id,
            "topicName": TOPIC_NAMES[topic_id],
            "ease": ease,
            "interval": interval,
            "repetitions": repetitions,
            "lapses": lapses,
            "dueOffsetDays": due_offset,
            "studied": studied,
        }
        for card_id, topic_id, ease, interval, repetitions, lapses, due_offset, studied in (
            normalize_card_row(row) for row in rows
        )
    ]


def review_rows(rows):
    return [{"cardId": card_id, "rating": rating, "daysAgo": days} for card_id, rating, days in rows]


def open_session(cards, reviews):
    """A real SQLite session holding the fixture, so vectors use production paths."""
    engine = db.build_engine("sqlite:///:memory:")
    db.init_engine(engine)
    session = Session(bind=engine)
    for topic_id, name in TOPIC_NAMES.items():
        session.add(Topic(id=topic_id, name=name))
    for card_id, topic_id, ease, interval, repetitions, lapses, due_offset, studied in (
        normalize_card_row(row) for row in cards
    ):
        session.add(
            Card(
                id=card_id,
                topic_id=topic_id,
                question=f"q{card_id}",
                answer=f"a{card_id}",
                ease=ease,
                interval=interval,
                repetitions=repetitions,
                lapses=lapses,
                due_date=at(due_offset),
                last_reviewed_at=at(-1) if studied else None,
            )
        )
    for card_id, rating, days in reviews:
        session.add(Review(card_id=card_id, rating=rating, reviewed_at=at(-days)))
    session.flush()
    return engine, session


# ------------------------------------------------------------------ vectors


def scheduler_vectors():
    out = []
    for ease, interval, reps, lapses, ratings in SCHEDULER_SEQUENCES:
        state = {"ease": ease, "interval": interval, "repetitions": reps, "lapses": lapses}
        steps = []
        for rating in ratings:
            schedule = next_schedule(now=NOW, rating=rating, **state)
            steps.append(
                {
                    "rating": rating,
                    "ease": schedule.ease,
                    "interval": schedule.interval,
                    "repetitions": schedule.repetitions,
                    "lapses": schedule.lapses,
                    "dueInDays": (schedule.due_date - NOW).days,
                }
            )
            state = {
                "ease": schedule.ease,
                "interval": schedule.interval,
                "repetitions": schedule.repetitions,
                "lapses": schedule.lapses,
            }
        out.append(
            {
                "start": {"ease": ease, "interval": interval, "repetitions": reps, "lapses": lapses},
                "steps": steps,
            }
        )
    return out


def mastery_vectors():
    out = []
    for spec in MASTERY_CARDS:
        card = Card(
            id=1,
            topic_id=1,
            question="q",
            answer="a",
            ease=spec["ease"],
            interval=spec["interval"],
            repetitions=spec["repetitions"],
            lapses=spec["lapses"],
            due_date=NOW,
            last_reviewed_at=NOW if spec["studied"] else None,
        )
        out.append({"card": spec, "level": mastery_level(card)})
    return out


def coverage_vectors():
    out = []
    for reference, user in COVERAGE_CASES:
        coverage = generator.score_answer(reference, user)
        out.append(
            {
                "reference": reference,
                "userAnswer": user,
                "total": coverage.total,
                "matched": list(coverage.matched),
                "missing": list(coverage.missing),
                "ratio": round(coverage.ratio, 12),
                "percent": coverage.percent,
                "charRatio": round(coverage.char_ratio, 12),
                "orderRatio": round(coverage.order_ratio, 12),
                "label": coverage.label,
                "notes": list(coverage.notes),
                "summary": coverage.summary(),
                "hint": generator.refinement_hint(coverage),
                "keywords": generator.extract_keywords(reference),
                "bigrams": generator.content_bigrams(reference),
            }
        )
    return out


def weakness_vectors():
    engine, session = open_session(WEAKNESS_CARDS, WEAKNESS_REVIEWS)
    try:
        weakness = interleaver.topic_weakness(session, now=NOW)
        return {
            "cards": card_rows(WEAKNESS_CARDS),
            "reviews": review_rows(WEAKNESS_REVIEWS),
            "weakness": {str(topic_id): score for topic_id, score in sorted(weakness.items())},
        }
    finally:
        session.close()
        engine.dispose()


def queue_vectors():
    engine, session = open_session(QUEUE_CARDS, [])
    try:
        weakness = interleaver.topic_weakness(session, now=NOW)
        plans = []
        for limit, new_limit in [(20, 10), (5, 0), (20, 2), (3, 3), (8, 1)]:
            plan = interleaver.build_review_plan(session, limit, new_limit=new_limit, now=NOW, seed=0)
            plans.append(
                {
                    "limit": limit,
                    "newLimit": new_limit,
                    "ids": [card.id for card in plan.cards],
                    "dueAvailable": plan.due_available,
                    "newAvailable": plan.new_available,
                    "newTaken": plan.new_taken,
                }
            )
        return {
            "cards": card_rows(QUEUE_CARDS),
            "weakness": {str(topic_id): score for topic_id, score in sorted(weakness.items())},
            "plans": plans,
        }
    finally:
        session.close()
        engine.dispose()


def spread_vectors():
    class Fake:
        def __init__(self, topic_id):
            self.topic_id = topic_id

    out = []
    for topics in SPREAD_INPUTS:
        result = interleaver.enforce_topic_spread([Fake(topic) for topic in topics], 2)
        out.append({"input": topics, "output": [card.topic_id for card in result]})
    return out


def streak_vectors():
    out = []
    for day_offsets in STREAK_CASES:
        reviews = [(1, 3, day) for day in day_offsets]
        engine, session = open_session([(1, 1, DEFAULT_EASE, 1, 1, 0, -1, True)], reviews)
        try:
            streak, studied_today = review_streak(session, NOW)
        finally:
            session.close()
            engine.dispose()
        out.append(
            {
                "dayOffsets": list(day_offsets),
                "streakDays": streak,
                "studiedToday": studied_today,
            }
        )
    return out


def summary_vectors():
    engine, session = open_session(SUMMARY_CARDS, SUMMARY_REVIEWS)
    try:
        data = compute_stats(session, now=NOW, recent_days=7)
        return {
            # Every Topic row, including the two with no cards: total_topics counts them.
            "allTopics": [
                {"id": topic.id, "name": topic.name}
                for topic in session.scalars(select(Topic).order_by(Topic.id))
            ],
            "cards": card_rows(SUMMARY_CARDS),
            "reviews": review_rows(SUMMARY_REVIEWS),
            "totalCards": data.total_cards,
            "totalTopics": data.total_topics,
            "dueToday": data.due_today,
            "dueBacklog": data.due_backlog,
            "mastery": data.mastery,
            "recentTotal": data.recent_total,
            "recentDays": [[day.isoformat(), count] for day, count in data.recent_days],
            "streakDays": data.streak_days,
            "studiedToday": data.studied_today,
            "reviewsTotal": data.reviews_total,
            "weakest": [[name, score] for name, score in data.weakest],
            "healthLine": data.health_line,
            "topics": [
                {
                    "name": topic.name,
                    "cards": topic.cards,
                    "dueToday": topic.due_today,
                    "avgEase": topic.avg_ease,
                    "avgInterval": topic.avg_interval,
                    "lapses": topic.lapses,
                    "weakness": topic.weakness,
                    "studied": topic.studied,
                }
                for topic in data.topics
            ],
        }
    finally:
        session.close()
        engine.dispose()


def build_payload() -> dict:
    return {
        "_comment": "Generated by tests/make_behavior_vectors.py — do not hand-edit.",
        "now": NOW.isoformat(timespec="seconds"),
        "nowMs": NOW_MS,
        "meta": {
            "defaultEase": DEFAULT_EASE,
            "minEase": 1.3,
            "passThreshold": PASS_THRESHOLD,
            "maxConsecutiveSameTopic": interleaver.MAX_CONSECUTIVE_SAME_TOPIC,
            "weakBoostThreshold": interleaver.WEAK_BOOST_THRESHOLD,
            "recentReviewWindowDays": interleaver.RECENT_REVIEW_WINDOW_DAYS,
            "cjkCharStopwords": sorted(generator.CJK_CHAR_STOPWORDS),
            "latinStopwords": sorted(generator.LATIN_STOPWORDS),
            "thresholds": {"low": generator.LOW_THRESHOLD, "good": generator.GOOD_THRESHOLD},
        },
        "scheduler": scheduler_vectors(),
        "mastery": mastery_vectors(),
        "coverage": coverage_vectors(),
        "weakness": weakness_vectors(),
        "queue": queue_vectors(),
        "spread": spread_vectors(),
        "streak": streak_vectors(),
        "summary": summary_vectors(),
    }


def main() -> int:
    payload = build_payload()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
