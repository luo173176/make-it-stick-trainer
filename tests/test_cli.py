"""End-to-end CLI tests: init/add/import/stats/export plus the review interaction."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from rich.console import Console
from sqlalchemy import select

from mist import cli, db
from mist.models import Card, Reflection, Review, Topic, utcnow
from mist.scheduler import review_card

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED = REPO_ROOT / "examples" / "seed.json"


@pytest.fixture
def dbpath(tmp_path) -> Path:
    return tmp_path / "mist.db"


@pytest.fixture
def feed(monkeypatch):
    """Drive the interactive prompts by replacing rich's input call."""

    def _feed(lines: list[str]):
        iterator = iter(lines)

        def fake_input(self, prompt="", *args, **kwargs):
            try:
                return next(iterator)
            except StopIteration:
                raise EOFError from None

        monkeypatch.setattr(Console, "input", fake_input)

    return _feed


def test_init_creates_database_and_is_idempotent(dbpath, capsys):
    assert cli.main(["init", "--db", str(dbpath)]) == 0
    assert cli.main(["init", "--db", str(dbpath)]) == 0
    assert dbpath.exists()
    assert "数据库已就绪" in capsys.readouterr().out

    engine = db.build_engine(dbpath)
    with db.session_scope(engine) as session:
        assert session.scalar(select(Topic)) is None


def test_add_creates_topic_and_card(dbpath, capsys):
    args = [
        "add",
        "--db",
        str(dbpath),
        "--topic",
        "检索练习",
        "--question",
        "为什么自测比重读有效？",
        "--answer",
        "自测强化提取路径，重读只产生流畅度错觉",
        "--tags",
        "错觉, 自测",
        "--source",
        "第2章",
    ]
    assert cli.main(args) == 0
    assert "已添加卡片" in capsys.readouterr().out

    engine = db.build_engine(dbpath)
    with db.session_scope(engine) as session:
        cards = list(session.scalars(select(Card)))
        assert len(cards) == 1
        assert cards[0].topic.name == "检索练习"
        assert cards[0].tags == "错觉,自测"
        assert cards[0].last_reviewed_at is None
        assert cards[0].due_date <= utcnow()

    assert cli.main(args) == 0
    with db.session_scope(engine) as session:
        assert len(list(session.scalars(select(Card)))) == 1


def test_add_rejects_bad_difficulty(dbpath):
    rc = cli.main(
        [
            "add",
            "--db",
            str(dbpath),
            "--topic",
            "t",
            "--question",
            "q",
            "--answer",
            "a",
            "--difficulty",
            "9",
        ]
    )
    assert rc == 2


def test_import_seed_then_stats_and_topics(dbpath, capsys):
    assert cli.main(["import", "--db", str(dbpath), "--file", str(SEED)]) == 0
    out = capsys.readouterr().out
    assert "新建 12 张" in out

    assert cli.main(["stats", "--db", str(dbpath)]) == 0
    assert cli.main(["topics", "--db", str(dbpath)]) == 0
    printed = capsys.readouterr().out
    assert "认知天性 · 学习统计" in printed
    assert "检索练习" in printed


def test_import_is_idempotent_for_plain_cards(dbpath):
    cli.main(["import", "--db", str(dbpath), "--file", str(SEED)])
    engine = db.build_engine(dbpath)

    assert cli.main(["import", "--db", str(dbpath), "--file", str(SEED)]) == 0
    with db.session_scope(engine) as session:
        assert len(list(session.scalars(select(Card)))) == 12
        assert len(list(session.scalars(select(Topic)))) == 4


def test_import_reports_broken_files(tmp_path, dbpath, capsys):
    missing = tmp_path / "nope.json"
    assert cli.main(["import", "--db", str(dbpath), "--file", str(missing)]) == 2

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cli.main(["import", "--db", str(dbpath), "--file", str(bad)]) == 2

    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({"cards": [{"question": "只有问题"}]}), encoding="utf-8")
    assert cli.main(["import", "--db", str(dbpath), "--file", str(str(partial))]) == 0
    assert "跳过 1 张" in capsys.readouterr().out


def test_export_import_roundtrip_preserves_schedule(dbpath, tmp_path, capsys):
    cli.main(["import", "--db", str(dbpath), "--file", str(SEED)])
    engine = db.build_engine(dbpath)
    with db.session_scope(engine) as session:
        card = session.scalars(select(Card)).first()
        review_card(session, card, 4, user_answer="自己的话", session_id="s-1", now=utcnow())
        expected_interval = card.interval
        expected_ease = card.ease

    backup = tmp_path / "backup.json"
    assert cli.main(["export", "--db", str(dbpath), "--file", str(backup)]) == 0
    payload = json.loads(backup.read_text(encoding="utf-8"))
    assert payload["format"] == "make-it-stick-trainer"
    assert len(payload["cards"]) == 12
    assert len(payload["reviews"]) == 1
    assert payload["reviews"][0]["topic"]

    other = tmp_path / "other.db"
    assert cli.main(["import", "--db", str(other), "--file", str(backup)]) == 0
    out = capsys.readouterr().out
    assert "新建 12 张" in out and "复习记录 1 条" in out

    second = db.build_engine(other)
    with db.session_scope(second) as session:
        imported = session.scalar(select(Card).where(Card.interval == expected_interval))
        assert imported is not None
        assert imported.ease == pytest.approx(expected_ease)
        assert session.scalar(select(Review).limit(1)) is not None


def test_review_records_answer_rating_and_reflection(dbpath, feed, capsys):
    cli.main(["import", "--db", str(dbpath), "--file", str(SEED)])
    feed(
        [
            "间隔练习让提取发生在半遗忘状态下，所以记得更牢",
            "",
            "4",
            "交错练习逼我先辨别题型",
            "",
            "和考试前刷题的经历有关",
            "",
            "下次先遮住答案自己写",
            "",
        ]
    )

    assert cli.main(["review", "--db", str(dbpath), "--limit", "1", "--seed", "3"]) == 0

    engine = db.build_engine(dbpath)
    with db.session_scope(engine) as session:
        reviews = list(session.scalars(select(Review)))
        assert len(reviews) == 1
        assert reviews[0].rating == 4
        assert "半遗忘" in reviews[0].user_answer
        assert "覆盖率" in reviews[0].feedback
        assert reviews[0].session_id

        reflections = list(session.scalars(select(Reflection)))
        assert len(reflections) == 1
        assert reflections[0].session_id == reviews[0].session_id
        assert reflections[0].core_concept == "交错练习逼我先辨别题型"
        assert reflections[0].next_improvement == "下次先遮住答案自己写"

        card = session.get(Card, reviews[0].card_id)
        assert card.last_reviewed_at is not None
        assert card.due_date > utcnow()
        assert card.interval == 1

    printed = capsys.readouterr().out
    assert "关键词覆盖率" in printed
    assert "反思已保存" in printed


def test_review_skips_and_blank_answer_still_grades(dbpath, feed, capsys):
    cli.main(["import", "--db", str(dbpath), "--file", str(SEED)])
    feed(["skip", "", "3"])

    assert cli.main(["review", "--db", str(dbpath), "--limit", "2", "--no-reflect", "--seed", "5"]) == 0
    engine = db.build_engine(dbpath)
    with db.session_scope(engine) as session:
        reviews = list(session.scalars(select(Review)))
        assert len(reviews) == 1  # one skipped, one graded
        assert reviews[0].rating == 3

    feed(["", "0"])
    assert cli.main(["review", "--db", str(dbpath), "--limit", "1", "--no-reflect", "--seed", "5"]) == 0
    assert "空白答案" in capsys.readouterr().out


def test_review_without_due_cards_is_a_no_op(dbpath, capsys):
    cli.main(["import", "--db", str(dbpath), "--file", str(SEED)])
    engine = db.build_engine(dbpath)
    with db.session_scope(engine) as session:
        for card in session.scalars(select(Card)):
            card.due_date = utcnow() + timedelta(days=3)

    assert cli.main(["review", "--db", str(dbpath)]) == 0
    assert "今天没有到期的卡片" in capsys.readouterr().out


def test_reflect_standalone_and_show(dbpath, feed, capsys):
    feed(["核心概念A", "", "联系B", "", "改进C", ""])
    assert cli.main(["reflect", "--db", str(dbpath), "--session-id", "manual-1"]) == 0

    feed(["", "", "", "", "", ""])
    assert cli.main(["reflect", "--db", str(dbpath)]) == 0
    assert "都留空" in capsys.readouterr().out

    assert cli.main(["reflect", "--db", str(dbpath), "--show"]) == 0
    printed = capsys.readouterr().out
    assert "核心概念A" in printed and "manual-1" not in printed  # table shows content, not ids


def test_unknown_command_exits_with_usage(dbpath):
    with pytest.raises(SystemExit):
        cli.main(["nope", "--db", str(dbpath)])
