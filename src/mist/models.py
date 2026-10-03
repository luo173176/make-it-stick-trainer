"""SQLAlchemy models for make-it-stick-trainer.

All timestamps are naive UTC (see ``utcnow``) so SQLite storage and interval
math stay predictable across platforms.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DEFAULT_EASE = 2.5
MIN_EASE = 1.3
MAX_DIFFICULTY = 5


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    cards: Mapped[List["Card"]] = relationship(
        back_populates="topic", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Topic id={self.id} name={self.name!r}>"


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), index=True)

    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(255), default="")
    tags: Mapped[str] = mapped_column(String(255), default="")
    difficulty: Mapped[int] = mapped_column(Integer, default=3)

    ease: Mapped[float] = mapped_column(Float, default=DEFAULT_EASE)
    interval: Mapped[int] = mapped_column(Integer, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    due_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    topic: Mapped["Topic"] = relationship(back_populates="cards")
    reviews: Mapped[List["Review"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    @property
    def is_new(self) -> bool:
        return self.last_reviewed_at is None and self.repetitions == 0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Card id={self.id} q={self.question[:20]!r} due={self.due_date}>"


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    rating: Mapped[int] = mapped_column(Integer)
    user_answer: Mapped[str] = mapped_column(Text, default="")
    feedback: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    elapsed_days: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_days: Mapped[int] = mapped_column(Integer, default=0)

    card: Mapped["Card"] = relationship(back_populates="reviews")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Review id={self.id} card={self.card_id} rating={self.rating}>"


class Reflection(Base):
    __tablename__ = "reflections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    core_concept: Mapped[str] = mapped_column(Text, default="")
    connection: Mapped[str] = mapped_column(Text, default="")
    next_improvement: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Reflection id={self.id} session={self.session_id!r}>"
