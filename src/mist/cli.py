"""Command line interface for make-it-stick-trainer.

Uses stdlib ``argparse`` so ``git clone && pip install -e .`` works with the
smallest possible dependency surface.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import select

from . import __version__, db, generator, interleaver, reflection, stats as stats_mod
from .models import Card, DEFAULT_EASE, Reflection, Review, Topic, utcnow
from .scheduler import Schedule, difficulty_hint, mastery_level, review_card

console = Console()

RATING_LEGEND = """[bold]自评 0-5[/bold]（诚实打分，分数只服务于你的记忆）
  0 完全想不起来　1 答错，看答案才认出　2 大致对，漏了要点
  3 基本正确，回忆费力　4 正确且较流畅　5 正确、流畅、能举新例子"""

SKIP_WORDS = {"skip", "s", "跳过", "略"}


class AbortInteraction(Exception):
    """Raised when stdin stops being interactive (piped input, Ctrl-D)."""


def configure_stdio() -> None:
    """Piped UTF-8 text must survive stdin, and unencodable glyphs must not crash stdout.

    Windows consoles default to a legacy code page; a redirected script file is
    usually UTF-8, so decoding is pinned to UTF-8 while encoding keeps the
    console's own code page with lossy replacement.
    """
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _engine(args: argparse.Namespace):
    engine = db.build_engine(args.db)
    db.init_engine(engine)
    return engine


def _ask(console: Console, label: str) -> str:
    try:
        return console.input(label).strip()
    except (EOFError, KeyboardInterrupt):
        raise AbortInteraction from None


def _ask_multiline(console: Console, label: str) -> str:
    console.print(f"[dim]{label}（多行输入，空回车结束；输入 skip 跳过本题）[/dim]")
    lines: list[str] = []
    while True:
        try:
            line = console.input("> " if not lines else "  ")
        except (EOFError, KeyboardInterrupt):
            raise AbortInteraction from None
        if not line.strip():
            if lines:
                break
            return ""
        if not lines and line.strip().lower() in SKIP_WORDS:
            return line.strip()
        lines.append(line.strip())
    return "\n".join(lines)


def _ask_rating(console: Console) -> int:
    while True:
        raw = _ask(console, "自评 [0-5] > ")
        if raw == "":
            console.print("[yellow]必须给出 0-5 的自评，这是调度复习的唯一依据。[/yellow]")
            continue
        try:
            return int(raw)
        except ValueError:
            console.print(f"[red]无法识别 {raw!r}，请输入 0-5 的整数。[/red]")


def _get_or_create_topic(session, name: str, description: str = "") -> Topic:
    topic = session.scalar(select(Topic).where(Topic.name == name))
    if topic is None:
        topic = Topic(name=name, description=description)
        session.add(topic)
        session.flush()
    return topic


def _find_card(session, topic_id: int, question: str) -> Optional[Card]:
    return session.scalar(select(Card).where(Card.topic_id == topic_id, Card.question == question))


def _next_due_hint(session, now: datetime) -> Optional[datetime]:
    return session.scalar(select(Card.due_date).where(Card.due_date > now).order_by(Card.due_date).limit(1))


# --------------------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace) -> int:
    engine = _engine(args)
    raw = engine.url.database
    resolved = str(Path(raw).expanduser().resolve()) if raw and raw != ":memory:" else (raw or str(engine.url))
    console.print(f"[green]数据库已就绪[/green]：{resolved}")
    console.print("[dim]接下来：mist add --topic \"主题\" --question \"问题\" --answer \"参考答案\"[/dim]")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    engine = _engine(args)
    difficulty = args.difficulty
    if not 1 <= difficulty <= 5:
        console.print("[red]--difficulty 必须在 1-5 之间。[/red]")
        return 2
    with db.session_scope(engine) as session:
        topic = _get_or_create_topic(session, args.topic, args.topic_description or "")
        existing = _find_card(session, topic.id, args.question)
        if existing is not None and not args.force:
            console.print(
                f"[yellow]同一主题下已有完全相同的问题（id={existing.id}），未重复添加。用 --force 可再加一张。[/yellow]"
            )
            return 0
        card = Card(
            topic_id=topic.id,
            question=args.question,
            answer=args.answer,
            source=args.source or "",
            tags=",".join(t.strip() for t in (args.tags or "").split(",") if t.strip()),
            difficulty=difficulty,
            ease=DEFAULT_EASE,
            interval=0,
            repetitions=0,
            due_date=utcnow(),
        )
        session.add(card)
        session.flush()
        keywords = generator.extract_keywords(card.answer)
        console.print(
            Panel(
                f"[bold]{card.question}[/bold]\n\n主题：{topic.name}　难度：{card.difficulty}　"
                f"标签：{card.tags or '-'}　来源：{card.source or '-'}\n"
                f"可提取关键词 {len(keywords)} 个，覆盖率评分将从这里来。",
                title=f"已添加卡片 #{card.id}（今天即可复习）",
                border_style="green",
            )
        )
    return 0


def _show_question(card: Card, index: int, total: int) -> None:
    topic_name = card.topic.name if card.topic else "?"
    meta = f"主题 {topic_name}｜难度 {card.difficulty}｜已复习 {card.repetitions} 次｜lapses {card.lapses}"
    if card.tags:
        meta += f"｜标签 {card.tags}"
    console.print(
        Panel(
            f"[bold]{card.question}[/bold]\n\n[dim]{meta}[/dim]\n[dim]状态：{mastery_level(card)}[/dim]",
            title=f"第 {index}/{total} 题 · 先回忆，不要先看答案",
            border_style="cyan",
        )
    )


def _review_one(
    session, card: Card, index: int, total: int, session_id: str
) -> Optional[dict]:
    _show_question(card, index, total)
    user_answer = _ask_multiline(
        console, "用你自己的话重述答案（写关键词、因果链、例子都行，别抄原文）"
    )
    if user_answer.strip().lower() in SKIP_WORDS:
        console.print("[dim]已跳过：这张卡仍留在到期队列里。[/dim]")
        return None
    if not user_answer.strip():
        console.print("[yellow]空白答案按“没生成”处理，覆盖率 0%，仍然要自评。[/yellow]")

    console.print(Panel(card.answer, title="参考答案（对照你自己的版本）", border_style="bright_black"))
    coverage, llm_comment = generator.grade_answer(card.question, card.answer, user_answer)
    console.print(coverage.summary())
    console.print(f"[dim]细化建议：{generator.refinement_hint(coverage)}[/dim]")
    if llm_comment:
        console.print(f"[dim]语义点评：{llm_comment}[/dim]")
    console.print(RATING_LEGEND)
    rating = _ask_rating(console)
    review = review_card(
        session,
        card,
        rating,
        user_answer=user_answer,
        feedback=coverage.summary(),
        session_id=session_id,
    )
    schedule = Schedule(
        ease=card.ease,
        interval=card.interval,
        repetitions=card.repetitions,
        lapses=card.lapses,
        due_date=card.due_date,
    )
    console.print(
        f"[green]已记录：rating={review.rating} → 间隔 {schedule.interval} 天，"
        f"ease {schedule.ease:.2f}，下次复习 {schedule.due_label}（{schedule.due_date:%m-%d %H:%M} UTC）[/green]"
    )
    console.print(f"[dim]{difficulty_hint(rating)}[/dim]")
    return {"card": card, "rating": rating, "coverage": coverage}


def cmd_review(args: argparse.Namespace) -> int:
    engine = _engine(args)
    with db.session_scope(engine) as session:
        plan = interleaver.build_review_plan(
            session, args.limit, new_limit=args.new_limit, seed=args.seed
        )
        if not plan.cards:
            nxt = _next_due_hint(session, utcnow())
            console.print(
                "[green]今天没有到期的卡片。[/green]"
                + (f"下一次到期：{nxt:%Y-%m-%d %H:%M} UTC" if nxt else "先用 mist add 添加卡片。")
            )
            console.print("[dim]没有到期项时不要预习式重读资料 —— 那是“熟练度错觉”的来源。[/dim]")
            return 0

        console.print(
            Panel(
                f"{interleaver.queue_summary(plan.cards)}\n{plan.note}"
                + (f"\n薄弱主题加权：{', '.join(plan.weak_topics)}" if plan.weak_topics else ""),
                title="本次复习队列（交错编排，主题被打乱）",
                border_style="magenta",
            )
        )

        session_id = reflection.make_session_id()
        graded: list[dict] = []
        interrupted = False
        for index, card in enumerate(plan.cards, start=1):
            try:
                outcome = _review_one(session, card, index, len(plan.cards), session_id)
            except AbortInteraction:
                console.print("\n[yellow]输入已结束，本次复习提前停止；已完成的结果已保存。[/yellow]")
                interrupted = True
                break
            if outcome:
                graded.append(outcome)
            console.rule(style="dim")

        if graded:
            table = Table(title=f"本次小结（session {session_id}）")
            table.add_column("主题")
            table.add_column("自评", justify="right")
            table.add_column("覆盖率", justify="right")
            table.add_column("新间隔", justify="right")
            table.add_column("状态")
            for item in graded:
                card = item["card"]
                table.add_row(
                    card.topic.name,
                    str(item["rating"]),
                    f"{item['coverage'].percent}%",
                    f"{card.interval}d",
                    mastery_level(card),
                )
            console.print(table)

        if not interrupted and not args.no_reflect:
            _run_reflection(session, session_id)
        elif args.no_reflect:
            console.print("[dim]已跳过反思环节（--no-reflect）。反思是细化与校准的关键步骤。[/dim]")
    return 0


def _run_reflection(session, session_id: str) -> None:
    console.print(
        Panel(
            "合意困难的最后一步：把今天的练习变成语言。空着跳过也可以，但三问都答的收获最大。",
            title="反思 / 细化",
            border_style="blue",
        )
    )
    answers: list[str] = []
    try:
        for question in reflection.REFLECTION_QUESTIONS:
            answers.append(_ask_multiline(console, question))
    except AbortInteraction:
        console.print("[yellow]反思未完成，未保存。[/yellow]")
        return
    saved = reflection.record_reflection(session, session_id, *answers)
    if saved is None:
        console.print("[dim]三问都留空，本次没有写入反思。[/dim]")
        return
    console.print("[green]反思已保存。[/green] 下次复习开始时可以先猜一遍自己上次写了什么。")


def cmd_stats(args: argparse.Namespace) -> int:
    engine = _engine(args)
    with db.session_scope(engine) as session:
        data = stats_mod.compute_stats(session, recent_days=args.days)
        stats_mod.render_stats(data, console)
    return 0


def cmd_topics(args: argparse.Namespace) -> int:
    engine = _engine(args)
    with db.session_scope(engine) as session:
        weakness = interleaver.topic_weakness(session)
        table = Table(title="学习主题")
        table.add_column("ID", justify="right")
        table.add_column("主题", overflow="fold")
        table.add_column("卡片", justify="right")
        table.add_column("到期", justify="right")
        table.add_column("平均 ease", justify="right")
        table.add_column("lapses", justify="right")
        table.add_column("薄弱指数", justify="right")
        table.add_column("说明", overflow="fold")
        now = utcnow()
        for topic in session.scalars(select(Topic).order_by(Topic.name)):
            cards = list(topic.cards)
            if not cards:
                table.add_row(str(topic.id), topic.name, "0", "-", "-", "-", "-", topic.description)
                continue
            due = sum(1 for c in cards if c.due_date <= now)
            avg_ease = sum(c.ease for c in cards) / len(cards)
            score = weakness.get(topic.id, 0.0)
            color = "red" if score >= 0.45 else "yellow" if score >= 0.3 else "green"
            table.add_row(
                str(topic.id),
                topic.name,
                str(len(cards)),
                str(due),
                f"{avg_ease:.2f}",
                str(sum(c.lapses for c in cards)),
                f"[{color}]{score:.2f}[/{color}]",
                topic.description or "",
            )
        console.print(table)
    return 0


def _card_payload(card: Card) -> dict:
    return {
        "id": card.id,
        "topic": card.topic.name if card.topic else "",
        "question": card.question,
        "answer": card.answer,
        "source": card.source,
        "tags": card.tags,
        "difficulty": card.difficulty,
        "ease": card.ease,
        "interval": card.interval,
        "repetitions": card.repetitions,
        "lapses": card.lapses,
        "due_date": card.due_date.isoformat() if card.due_date else None,
        "last_reviewed_at": card.last_reviewed_at.isoformat() if card.last_reviewed_at else None,
        "created_at": card.created_at.isoformat() if card.created_at else None,
    }


def cmd_export(args: argparse.Namespace) -> int:
    engine = _engine(args)
    target = Path(args.file).expanduser()
    with db.session_scope(engine) as session:
        payload = {
            "format": "make-it-stick-trainer",
            "version": 1,
            "exported_at": utcnow().isoformat(),
            "topics": [
                {
                    "name": t.name,
                    "description": t.description,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in session.scalars(select(Topic).order_by(Topic.name))
            ],
            "cards": [_card_payload(c) for c in session.scalars(select(Card).order_by(Card.id))],
            "reviews": [
                {
                    "card": r.card.question if r.card else None,
                    "topic": r.card.topic.name if r.card and r.card.topic else None,
                    "session_id": r.session_id,
                    "rating": r.rating,
                    "user_answer": r.user_answer,
                    "feedback": r.feedback,
                    "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                    "elapsed_days": r.elapsed_days,
                    "scheduled_days": r.scheduled_days,
                }
                for r in session.scalars(select(Review).order_by(Review.id))
            ],
            "reflections": [
                {
                    "session_id": f.session_id,
                    "core_concept": f.core_concept,
                    "connection": f.connection,
                    "next_improvement": f.next_improvement,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                }
                for f in session.scalars(select(Reflection).order_by(Reflection.id))
            ],
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(
        f"[green]已导出[/green] {len(payload['cards'])} 张卡片 / {len(payload['reviews'])} 条复习记录 → {target}"
    )
    return 0


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def cmd_import(args: argparse.Namespace) -> int:
    engine = _engine(args)
    source = Path(args.file).expanduser()
    if not source.exists():
        console.print(f"[red]找不到文件：{source}[/red]")
        return 2
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        console.print(f"[red]JSON 解析失败：{exc}[/red]")
        return 2

    if isinstance(payload, list):
        payload = {"cards": payload}
    topic_entries = payload.get("topics") or []
    card_entries = payload.get("cards") or []
    if not isinstance(card_entries, list):
        console.print("[red]cards 字段必须是列表。[/red]")
        return 2

    created = updated = skipped = 0
    with db.session_scope(engine) as session:
        for entry in topic_entries:
            name = (entry.get("name") or "").strip() if isinstance(entry, dict) else str(entry).strip()
            if name:
                _get_or_create_topic(session, name, (entry.get("description") or "") if isinstance(entry, dict) else "")

        index_by_question: dict[str, Card] = {}
        for card in session.scalars(select(Card)):
            index_by_question[card.question.strip()] = card

        for entry in card_entries:
            if not isinstance(entry, dict):
                skipped += 1
                continue
            question = (entry.get("question") or "").strip()
            answer = (entry.get("answer") or "").strip()
            if not question or not answer:
                console.print(f"[yellow]跳过缺少 question/answer 的条目：{entry!r:.80}[/yellow]")
                skipped += 1
                continue
            topic_name = (entry.get("topic") or entry.get("topic_name") or "未分类").strip()
            topic = _get_or_create_topic(session, topic_name)
            tags = entry.get("tags") or ""
            if isinstance(tags, list):
                tags = ",".join(str(t).strip() for t in tags if str(t).strip())
            existing = index_by_question.get(question)
            if existing is not None:
                has_schedule = entry.get("interval") is not None or entry.get("last_reviewed_at")
                if not has_schedule:
                    skipped += 1
                    continue
                _apply_schedule(existing, entry)
                updated += 1
                continue
            card = Card(
                topic_id=topic.id,
                question=question,
                answer=answer,
                source=entry.get("source") or "",
                tags=tags,
                difficulty=int(entry.get("difficulty") or 3),
                ease=DEFAULT_EASE,
                interval=0,
                repetitions=0,
                due_date=_parse_dt(entry.get("due_date")) or utcnow(),
            )
            _apply_schedule(card, entry)
            session.add(card)
            session.flush()
            index_by_question[question] = card
            created += 1

        restored_reviews = _import_reviews(session, payload.get("reviews") or [], index_by_question)
        restored_reflections = _import_reflections(session, payload.get("reflections") or [])

    console.print(
        f"[green]导入完成[/green]：新建 {created} 张，更新 {updated} 张，跳过 {skipped} 张；"
        f"复习记录 {restored_reviews} 条，反思 {restored_reflections} 篇。"
    )
    return 0


def _apply_schedule(card: Card, entry: dict) -> None:
    """Restore scheduler state from a full export; ignore unknown/absent fields."""
    if "ease" in entry and entry["ease"] is not None:
        card.ease = float(entry["ease"])
    if "interval" in entry and entry["interval"] is not None:
        card.interval = int(entry["interval"])
    if "repetitions" in entry and entry["repetitions"] is not None:
        card.repetitions = int(entry["repetitions"])
    if "lapses" in entry and entry["lapses"] is not None:
        card.lapses = int(entry["lapses"])
    if entry.get("source"):
        card.source = str(entry["source"])
    if entry.get("tags"):
        card.tags = str(entry["tags"])
    if entry.get("difficulty") is not None:
        card.difficulty = int(entry["difficulty"])
    due = _parse_dt(entry.get("due_date"))
    if due is not None:
        card.due_date = due
    last = _parse_dt(entry.get("last_reviewed_at"))
    if last is not None:
        card.last_reviewed_at = last


def _import_reviews(session, entries: list, index_by_question: dict[str, Card]) -> int:
    count = 0
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("card") is None:
            continue
        card = index_by_question.get(str(entry["card"]).strip())
        if card is None:
            continue
        if session.scalar(
            select(Review).where(
                Review.card_id == card.id,
                Review.reviewed_at == _parse_dt(entry.get("reviewed_at")),
            )
        ):
            continue
        session.add(
            Review(
                card_id=card.id,
                session_id=entry.get("session_id"),
                rating=int(entry.get("rating") or 0),
                user_answer=entry.get("user_answer") or "",
                feedback=entry.get("feedback") or "",
                reviewed_at=_parse_dt(entry.get("reviewed_at")) or utcnow(),
                elapsed_days=int(entry.get("elapsed_days") or 0),
                scheduled_days=int(entry.get("scheduled_days") or 0),
            )
        )
        count += 1
    session.flush()
    return count


def _import_reflections(session, entries: list) -> int:
    count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        saved = reflection.record_reflection(
            session,
            str(entry.get("session_id") or "imported"),
            entry.get("core_concept") or "",
            entry.get("connection") or "",
            entry.get("next_improvement") or "",
            now=_parse_dt(entry.get("created_at")),
        )
        count += 1 if saved else 0
    return count


def cmd_reflect(args: argparse.Namespace) -> int:
    engine = _engine(args)
    with db.session_scope(engine) as session:
        if args.show:
            items = reflection.recent_reflections(session, limit=args.show)
            if not items:
                console.print("[dim]还没有反思记录。完成一次 mist review 后会被要求写。[/dim]")
                return 0
            table = Table(title="最近的反思")
            table.add_column("时间", justify="left")
            table.add_column("核心概念", overflow="fold")
            table.add_column("联系", overflow="fold")
            table.add_column("下次改进", overflow="fold")
            for item in items:
                table.add_row(
                    f"{item.created_at:%m-%d %H:%M}",
                    item.core_concept,
                    item.connection,
                    item.next_improvement,
                )
            console.print(table)
            return 0
        session_id = args.session_id or reflection.make_session_id()
        try:
            answers = [_ask_multiline(console, question) for question in reflection.REFLECTION_QUESTIONS]
        except AbortInteraction:
            console.print("[yellow]反思未完成，未保存。[/yellow]")
            return 1
        saved = reflection.record_reflection(session, session_id, *answers)
        if saved is None:
            console.print("[dim]三问都留空，未写入。[/dim]")
            return 0
        console.print(f"[green]反思已保存[/green]（session {session_id}）")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from .web import serve

    engine = _engine(args)
    return serve(engine, host=args.host, port=args.port)


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db",
        default=None,
        help=f"SQLite 数据库路径（默认 {db.ENV_DB} 环境变量，或 ~/.mist/mist.db）",
    )

    parser = argparse.ArgumentParser(
        prog="mist",
        description="认知天性学习策略训练器：检索练习 + 间隔重复 + 交错练习 + 生成 + 反思。",
        epilog="先跑 mist init，再用 examples/seed.json 体验一轮 mist review。",
    )
    parser.add_argument("--version", action="version", version=f"make-it-stick-trainer {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", parents=[common], help="初始化 SQLite 数据库")
    p_init.set_defaults(func=cmd_init)

    p_add = sub.add_parser("add", parents=[common], help="添加学习卡片")
    p_add.add_argument("--topic", required=True, help="主题名（不存在则创建）")
    p_add.add_argument("--topic-description", default="", help="主题说明")
    p_add.add_argument("--question", required=True, help="问题（复习时先看到的部分）")
    p_add.add_argument("--answer", required=True, help="参考答案，用于生成性自评的关键词提取")
    p_add.add_argument("--tags", default="", help="标签，逗号分隔（多样化练习用）")
    p_add.add_argument("--source", default="", help="来源：章节、讲义、视频链接等")
    p_add.add_argument("--difficulty", type=int, default=3, help="1-5，默认 3")
    p_add.add_argument("--force", action="store_true", help="允许重复问题")
    p_add.set_defaults(func=cmd_add)

    p_review = sub.add_parser("review", parents=[common], help="开始一次交错复习 session")
    p_review.add_argument("--limit", type=int, default=interleaver.DEFAULT_LIMIT, help="本次最多几张")
    p_review.add_argument(
        "--new-limit", type=int, default=interleaver.DEFAULT_NEW_LIMIT, help="其中新卡最多几张"
    )
    p_review.add_argument("--seed", type=int, default=None, help="固定队列随机种子，便于复现")
    p_review.add_argument(
        "--no-reflect", action="store_true", help="跳过结尾反思（不推荐，反思属于细化）"
    )
    p_review.set_defaults(func=cmd_review)

    p_stats = sub.add_parser("stats", parents=[common], help="显示到期、掌握度、薄弱主题")
    p_stats.add_argument("--days", type=int, default=7, help="复习趋势统计天数，默认 7")
    p_stats.set_defaults(func=cmd_stats)

    p_topics = sub.add_parser("topics", parents=[common], help="列出主题及其状态")
    p_topics.set_defaults(func=cmd_topics)

    p_export = sub.add_parser("export", parents=[common], help="导出为 JSON 备份")
    p_export.add_argument("--file", required=True, help="输出文件路径")
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser("import", parents=[common], help="从 JSON 导入卡片或完整备份")
    p_import.add_argument("--file", required=True, help="输入文件路径")
    p_import.set_defaults(func=cmd_import)

    p_reflect = sub.add_parser("reflect", parents=[common], help="单独写/看反思小结")
    p_reflect.add_argument("--session-id", default=None, help="挂到已有复习 session")
    p_reflect.add_argument("--show", type=int, default=0, const=5, nargs="?", help="查看最近 N 篇反思")
    p_reflect.set_defaults(func=cmd_reflect)

    p_web = sub.add_parser("web", parents=[common], help="启动极简 Web 界面（需要 pip install -e '.[web]'）")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=8000)
    p_web.set_defaults(func=cmd_web)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        console.print("\n[yellow]已中断，之前的进度都已保存。[/yellow]")
        return 130
    except Exception as exc:
        console.print(f"[bold red]出错了：[/bold red]{exc.__class__.__name__}: {exc}")
        if "--debug" in (argv or sys.argv):
            raise
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
