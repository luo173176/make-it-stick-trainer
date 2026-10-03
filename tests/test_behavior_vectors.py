"""Guard against Python-side drift in the shared behavior vectors.

web/core.js is written against tests/vectors/behavior.json. If a rule in
src/mist changes without regenerating that file, the JS would silently keep
implementing the old rule — this test makes the stale contract fail loudly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from make_behavior_vectors import build_payload

VECTORS = json.loads(
    (Path(__file__).parent / "vectors" / "behavior.json").read_text(encoding="utf-8")
)


def test_checked_in_vectors_match_the_current_implementation():
    fresh = build_payload()
    stale = [
        section
        for section in VECTORS
        if section != "_comment" and json.dumps(fresh.get(section), sort_keys=True)
        != json.dumps(VECTORS[section], sort_keys=True)
    ]
    assert not stale, (
        f"这些行为向量已过期：{stale}。运行 python tests/make_behavior_vectors.py 重新生成，"
        "并同步检查 web/core.js 是否仍然一致。"
    )


def test_vector_sections_are_all_present():
    expected = {
        "now",
        "nowMs",
        "meta",
        "scheduler",
        "mastery",
        "coverage",
        "weakness",
        "queue",
        "spread",
        "streak",
        "summary",
    }
    assert set(VECTORS) - {"_comment"} == expected


@pytest.mark.parametrize("index", range(len(VECTORS["coverage"])))
def test_coverage_vectors_are_self_consistent(index):
    vector = VECTORS["coverage"][index]
    if vector["total"] == 0:
        assert vector["label"] == "无法评分"
        return
    assert 0 <= vector["percent"] <= 100
    assert len(vector["matched"]) <= vector["total"]
    for fragment in vector["missing"]:
        # Chinese slices are always >=2 chars; latin units may be a single digit.
        assert len(fragment) >= 2 or fragment.isascii(), fragment
