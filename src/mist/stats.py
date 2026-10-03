"""Progress reporting: what's due, what's mastered, what's weak (校准)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func, select

from .interleaver import topic_weakness
from .models import Card, Reflection, Review, Topic, utcnow
from .scheduler import (
    MASTERY_CONSOLIDATING,
    MASTERY_LEARNING,
    MASTERY_MASTERED,
    MASTERY_NEW,
    mastery_level,
)

MASTERY_ORDER = (MASTERY_MASTERED, MASTERY_CONSOLIDATING, MASTERY_LEARNING, MASTERY_NEW)


@dataclass
class TopicStat:
    topic_id: int
    name: str
    cards: int
    due_today: int
    avg_ease: float
    avg_interval: float
    lapses: int
    weakness: float
    studied: bool
    mastery: dict[str, int] = field(default_factory=dict)

    @property
    def mastery_text(self) -> str:
        return " ".join(
            f"{name}={self.mastery.get(name, 0)}"
            for name in MASTERY_ORDER
            if self.mastery.get(name)
        )


@dataclass
class Stats:
    generated_at: datetime
    total_cards: int
    total_topics: int
    due_today: int
    due_backlog: int
    mastery: dict[str, int]
    topics: list[TopicStat]
    recent_days: list[tuple[date, int]]
    recent_total: int
    weakest: list[tuple[str, float]]
    streak_days: int
    studied_today: bool
    reviews_total: int
    reflections_total: int
    avg_rating_recent: Optional[float]

    @property
    def health_line(self) -> str:
        mastered = self.mastery.get(MASTERY_MASTERED, 0)
        share = round(100 * mastered / self.total_cards) if self.total_cards else 0
        return (
            f"卡片 {self.total_cards} 张 / 主题 {self.total_topics} 个 · 今日到期 {self.due_today} · "
            f"已掌握 {mastered}（{share}%）· 连续复习 {self.streak_days} 天"
        )


def _review_dates(session) -> set[date]:
    rows = session.execute(select(Review.reviewed_at)).all()
    return {value.date() for (value,) in rows if value is not None}


def review_streak(session, now: Optional[datetime] = None) -> tuple[int, bool]:
    """Consecutive days with at least one review, counted back from today/yesterday."""
    moment = now or utcnow()
    days = _review_dates(session)
    if not days:
        return 0, False
    today = moment.date()
    studied_today = today in days
    cursor = today if studied_today else today - timedelta(days=1)
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak, studied_today


def compute_stats(
    session, *, now: Optional[datetime] = None, recent_days: int = 7
) -> Stats:
    moment = now or utcnow()
    start_of_day = datetime(moment.year, moment.month, moment.day)
    horizon = start_of_day + timedelta(days=1)

    total_cards = session.scalar(select(func.count(Card.id))) or 0
    total_topics = session.scalar(select(func.count(Topic.id))) or 0
    # "今日到期" includes overdue cards, matching what `mist review` will serve.
    due_today = session.scalar(select(func.count(Card.id)).where(Card.due_date < horizon)) or 0
    due_backlog = session.scalar(
        select(func.count(Card.id)).where(Card.due_date < start_of_day)
    )

    mastery: Counter = Counter()
    per_topic_mastery: dict[int, Counter] = {}
    for card in session.scalars(select(Card)):
        level = mastery_level(card)
        mastery[level] += 1
        per_topic_mastery.setdefault(card.topic_id, Counter())[level] += 1
    weakness = topic_weakness(session, now=moment)

    topic_stats: list[TopicStat] = []
    rows = session.execute(
        select(
            Topic.id,
            Topic.name,
            func.count(Card.id),
            func.avg(Card.ease),
            func.avg(Card.interval),
            func.coalesce(func.sum(Card.lapses), 0),
        )
        .outerjoin(Card, Card.topic_id == Topic.id)
        .group_by(Topic.id, Topic.name)
        .order_by(Topic.name)
    ).all()
    studied_ids = set(
        session.execute(
            select(Card.topic_id).where(Card.last_reviewed_at.is_not(None)).distinct()
        ).scalars()
    )
    due_by_topic = dict(
        session.execute(
            select(Card.topic_id, func.count(Card.id))
            .where(Card.due_date < horizon)
            .group_by(Card.topic_id)
        ).all()
    )

    for topic_id, name, cards, avg_ease, avg_interval, lapses in rows:
        topic_stats.append(
            TopicStat(
                topic_id=topic_id,
                name=name,
                cards=int(cards or 0),
                due_today=int(due_by_topic.get(topic_id, 0)),
                avg_ease=round(float(avg_ease or 0.0), 2),
                avg_interval=round(float(avg_interval or 0.0), 1),
                lapses=int(lapses or 0),
                weakness=round(weakness.get(topic_id, 0.0), 3),
                studied=topic_id in studied_ids,
                mastery=dict(per_topic_mastery.get(topic_id, {})),
            )
        )

    window_start = moment - timedelta(days=recent_days)
    window_ratings = list(
        session.scalars(select(Review.rating).where(Review.reviewed_at >= window_start))
    )
    reviewed_at_values = list(session.scalars(select(Review.reviewed_at)))
    buckets: Counter = Counter()
    for value in reviewed_at_values:
        if value is not None and value >= window_start:
            buckets[value.date()] += 1
    recent_series = [
        (day, buckets.get(day, 0))
        for day in (
            (moment - timedelta(days=offset)).date()
            for offset in range(recent_days - 1, -1, -1)
        )
    ]

    weakest = sorted(
        [
            (stat.name, stat.weakness)
            for stat in topic_stats
            if stat.studied and stat.weakness > 0
        ],
        key=lambda item: (-item[1], item[0]),
    )[:5]

    streak_days, studied_today = review_streak(session, moment)

    return Stats(
        generated_at=moment,
        total_cards=int(total_cards),
        total_topics=int(total_topics),
        due_today=int(due_today),
        due_backlog=int(due_backlog or 0),
        mastery={name: int(mastery.get(name, 0)) for name in MASTERY_ORDER},
        topics=topic_stats,
        recent_days=recent_series,
        recent_total=sum(count for _, count in recent_series),
        weakest=weakest,
        streak_days=streak_days,
        studied_today=studied_today,
        reviews_total=session.scalar(select(func.count(Review.id))) or 0,
        reflections_total=session.scalar(select(func.count(Reflection.id))) or 0,
        avg_rating_recent=round(sum(window_ratings) / len(window_ratings), 2)
        if window_ratings
        else None,
    )


def render_stats(stats: Stats, console) -> None:
    """Rich rendering of :func:`compute_stats`."""
    from rich.panel import Panel
    from rich.table import Table

    console.print(Panel(stats.health_line, title="认知天性 · 学习统计", border_style="cyan"))

    mastery_table = Table(title="卡片状态分布", show_lines=False)
    for name in MASTERY_ORDER:
        mastery_table.add_column(name, justify="center")
    mastery_table.add_row(*(str(stats.mastery.get(name, 0)) for name in MASTERY_ORDER))
    console.print(mastery_table)

    if stats.topics:
        topic_table = Table(title="各主题掌握情况")
        topic_table.add_column("主题", overflow="fold")
        topic_table.add_column("卡片", justify="right")
        topic_table.add_column("今日到期", justify="right")
        topic_table.add_column("平均 ease", justify="right")
        topic_table.add_column("平均间隔(天)", justify="right")
        topic_table.add_column("lapses", justify="right")
        topic_table.add_column("薄弱指数", justify="right")
        topic_table.add_column("分布", overflow="fold")
        for stat in stats.topics:
            color = "red" if stat.weakness >= 0.45 else "yellow" if stat.weakness >= 0.3 else "green"
            topic_table.add_row(
                stat.name,
                str(stat.cards),
                str(stat.due_today),
                f"{stat.avg_ease:.2f}",
                f"{stat.avg_interval:.1f}",
                str(stat.lapses),
                f"[{color}]{stat.weakness:.2f}[/{color}]",
                stat.mastery_text or "-",
            )
        console.print(topic_table)

    bar_max = max((count for _, count in stats.recent_days), default=0) or 1
    trend = Table(title=f"最近 {len(stats.recent_days)} 天复习次数")
    trend.add_column("日期", justify="left")
    trend.add_column("复习", justify="left")
    for day, count in stats.recent_days:
        trend.add_row(f"{day:%m-%d}", "█" * max(1, round(30 * count / bar_max)) + f" {count}" if count else "· 0")
    console.print(trend)
    console.print(
        f"期间共复习 {stats.recent_total} 次"
        + (f"，平均自评 {stats.avg_rating_recent}" if stats.avg_rating_recent else "")
        + f"；累计复习 {stats.reviews_total} 次，反思 {stats.reflections_total} 篇。"
    )

    if stats.weakest:
        weak = Table(title="薄弱主题 Top 5（优先交错抽取）")
        weak.add_column("主题")
        weak.add_column("薄弱指数", justify="right")
        for name, score in stats.weakest:
            weak.add_row(name, f"{score:.3f}")
        console.print(weak)
    else:
        console.print("[dim]还没有足够复习数据判定薄弱主题，先完成一次复习。[/dim]")

    if stats.due_backlog:
        console.print(f"[bold red]逾期未复习 {stats.due_backlog} 张 —— 间隔被拉长时先补到期卡。[/bold red]")
    if not stats.studied_today:
        console.print("[yellow]今天还没练习。检索练习的价值随间隔衰减，别攒到明天。[/yellow]")
