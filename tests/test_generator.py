"""Generative-answer coverage tests.

Scoring units: latin words + Chinese content characters, blended with a
bigram (word-order) view so copying the reference out of order cannot pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mist.generator import (
    HIGH_COVERAGE,
    LOW_COVERAGE,
    MEDIUM_COVERAGE,
    NO_SIGNAL,
    Coverage,
    content_bigrams,
    evaluate_with_llm,
    extract_keywords,
    grade_answer,
    label_for,
    missing_fragments,
    normalize,
    refinement_hint,
    score_answer,
    tokenize,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

REFERENCE_EN = "Spacing practice weakens associations but strengthens retrieval."
ANSWER_EN = "Practice spacing weakens associations and strengthens retrieval."

CARD_QA = "问答题要求从记忆里生成答案，检索路径被真正走一遍；选择题只需再认。"
CARD_QA_PARAPHRASE = "简答题必须自己把答案从记忆里造出来，等于真走了一遍检索通路；选择题只要认出选项就行。"
CARD_QA_OFF_TOPIC = "投壶研究中混合角度的组最后成绩比单一角度组高很多。"


def test_latin_stopwords_are_dropped():
    keywords = extract_keywords(REFERENCE_EN)
    for noise in ("the", "but", "and", "out"):
        assert noise not in keywords
    assert "practice" in keywords and "retrieval" in keywords


def test_latin_paraphrase_scores_full_coverage():
    coverage = score_answer(REFERENCE_EN, ANSWER_EN)
    assert coverage.percent == 100
    assert coverage.label == HIGH_COVERAGE


def test_latin_gaps_are_reported_verbatim():
    coverage = score_answer("Retrieval strengthens the association pathway.", "Retrieval helps.")
    assert "retrieval" in coverage.matched
    assert {"strengthens", "association", "pathway"} <= set(coverage.missing)


def test_chinese_units_are_content_characters():
    from mist.generator import CJK_CHAR_STOPWORDS

    keywords = extract_keywords("我们可以通过练习来记住它")
    assert "练" in keywords and "习" in keywords
    assert not set(keywords) & CJK_CHAR_STOPWORDS


def test_correct_paraphrase_beats_off_topic_answer():
    paraphrase = score_answer(CARD_QA, CARD_QA_PARAPHRASE)
    off_topic = score_answer(CARD_QA, CARD_QA_OFF_TOPIC)

    assert paraphrase.total > 0
    assert paraphrase.ratio > off_topic.ratio
    assert paraphrase.label in (MEDIUM_COVERAGE, HIGH_COVERAGE)
    assert off_topic.label == LOW_COVERAGE


def test_shuffled_copy_of_the_reference_cannot_pass():
    """Same characters, scrambled order: character coverage is high, order is zero."""
    shuffled = "".join(reversed(CARD_QA_PARAPHRASE))
    coverage = score_answer(CARD_QA, shuffled)

    assert coverage.char_ratio >= 0.3  # characters are all present somewhere
    assert coverage.order_ratio == 0.0
    assert coverage.label == LOW_COVERAGE


def test_missing_list_holds_readable_fragments_not_loose_characters():
    coverage = score_answer(CARD_QA, "简答题要自己生成")
    assert coverage.missing
    assert all(len(fragment) >= 2 for fragment in coverage.missing)
    assert "再认" in "".join(coverage.missing)


def test_missing_fragments_widen_around_the_gap():
    fragments = missing_fragments("记忆痕迹会被强化", {"痕", "迹", "会"})
    assert fragments == ("忆痕迹会被",)  # one character of context on each side


def test_missing_fragments_never_cross_punctuation():
    fragments = missing_fragments("缩短间隔，并记录失败次数", {"缩", "短", "间", "隔", "失", "败"})
    assert all("，" not in fragment for fragment in fragments)
    assert len(fragments) == 2


def test_bigram_view_ignores_function_characters():
    pairs = content_bigrams("强化记忆的痕迹")
    assert "强化" in pairs and "化记" in pairs
    assert not any("的" in pair for pair in pairs)


def test_blank_answer_scores_zero_and_flags_generation_failure():
    coverage = score_answer("参考答案的关键要点是检索练习", "   ")
    assert coverage.ratio == 0.0
    assert coverage.label == LOW_COVERAGE
    assert coverage.notes == ("没有生成任何答案。",)
    assert coverage.missing


def test_reference_without_keywords_is_not_scorable():
    coverage = score_answer("的了是在", "随便写点什么")
    assert coverage.total == 0
    assert coverage.label == NO_SIGNAL
    assert "关键词" in coverage.summary()


def test_label_thresholds():
    assert label_for(0.2) == LOW_COVERAGE
    assert label_for(0.34) == LOW_COVERAGE
    assert label_for(0.5) == MEDIUM_COVERAGE
    assert label_for(0.70) == HIGH_COVERAGE
    assert label_for(0.9) == HIGH_COVERAGE


def test_summary_reports_both_counts_and_gaps():
    coverage = score_answer(CARD_QA, CARD_QA_PARAPHRASE)
    text = coverage.summary()
    assert "关键词覆盖率" in text and "内容字" in text and "未覆盖" in text


def test_fullwidth_and_case_are_normalized():
    assert normalize("ＡＢＣ 练习") == "abc 练习"
    assert "abc" in tokenize("ABC 练习")


def test_refinement_hint_points_at_gaps():
    coverage = score_answer(CARD_QA, "简答题要自己生成")
    hint = refinement_hint(coverage)
    assert hint and any(fragment in hint for fragment in coverage.missing[:5])


def test_hint_for_unscorable_reference_asks_for_keywords():
    assert "关键词" in refinement_hint(Coverage(ratio=0.0, total=0, label=NO_SIGNAL))


def test_llm_hook_is_off_by_default():
    assert evaluate_with_llm("问题", "参考答案", "用户答案") is None
    coverage, comment = grade_answer("问题", "参考答案的关键要点", "参考答案的关键要点")
    assert comment is None
    assert coverage.percent == 100


@pytest.mark.parametrize("card", json.loads(
    (REPO_ROOT / "examples" / "seed.json").read_text(encoding="utf-8")
)["cards"], ids=lambda card: card["question"][:14])
def test_seed_references_yield_scorable_units(card):
    """Every shipped card must produce units and readable gap fragments."""
    coverage = score_answer(card["answer"], "完全无关的一句话内容")
    assert coverage.total >= 5, "参考答案太短，覆盖率没有意义"
    assert coverage.label == LOW_COVERAGE
    assert all(len(fragment) >= 2 for fragment in coverage.missing)

    self_copy = score_answer(card["answer"], card["answer"])
    assert self_copy.percent == 100
