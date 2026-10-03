"""Generative-answer grading (生成性学习 + 细化).

The learner must produce an answer in their own words before seeing the
reference. To keep feedback cheap and offline, we measure how much of the
reference's content vocabulary the free-text answer touches.

Scoring units differ by script because the two languages fail differently:
latin text is split into words (real boundaries exist), Chinese into *content
characters*. Bigram windows were tried first and rejected — a correct
paraphrase that reorders characters scores ~8%, which is a fabricated signal
that misleads calibration, the exact failure mode the book warns about.
Unigram coverage is lenient about wording and strict about concepts, which is
the trade-off an advisory hint should make.

This is a proxy for recall completeness, not a judgement of understanding:
the self-rating afterwards is what drives the schedule.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

LLM_EVAL_ENV = "MIST_LLM_EVAL"

LATIN_STOPWORDS = frozenset(
    """a an the and or of to in for on with as at by is are was were be been being
    it its this that these those from not but then than too very can could should
    would will must do does did doing have has had having i you we they he she
    them him his her our your their which who whom whose what when where why how
    also such into over under out up down so if no yes one two three more most""".split()
)

# Function characters: they carry grammar, not concepts, so a learner cannot
# "miss" them meaningfully.
CJK_CHAR_STOPWORDS = frozenset(
    "的了是在和与及或着过把被为对从以就都也很更最这那有没人之等吗呢吧啊呀么并而于其该各"
    "只未免去即则虽若让又才且但所由被将使其兹兮焉哉矣耳"
)

LATIN_RE = re.compile(r"[a-z0-9_]+")
CJK_RE = re.compile(r"[一-鿿]+")

LOW_COVERAGE = "覆盖不足"
MEDIUM_COVERAGE = "基本覆盖"
HIGH_COVERAGE = "覆盖良好"
NO_SIGNAL = "无法评分"

LOW_THRESHOLD = 0.35
GOOD_THRESHOLD = 0.70
MIN_LATIN_TOKEN_LEN = 2
MIN_FRAGMENT_LEN = 2
MAX_MISSING_SHOWN = 8
MAX_GAP = 1


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def is_stopword(token: str) -> bool:
    if token in LATIN_STOPWORDS:
        return True
    return len(token) == 1 and token in CJK_CHAR_STOPWORDS


def tokenize(text: str) -> list[str]:
    """Order-preserving unique scorable units: latin words + Chinese content chars."""
    lowered = normalize(text)
    units: list[str] = []
    for match in LATIN_RE.finditer(lowered):
        word = match.group()
        if len(word) >= MIN_LATIN_TOKEN_LEN or any(ch.isdigit() for ch in word):
            units.append(word)
    for run in CJK_RE.findall(lowered):
        units.extend(char for char in run if char not in CJK_CHAR_STOPWORDS)
    seen: set[str] = set()
    return [u for u in units if not (u in seen or seen.add(u))]


def content_bigrams(text: str) -> list[str]:
    """Adjacent content-character pairs inside each Chinese segment.

    Character coverage alone would credit a shuffled copy of the reference,
    which is not recall, so pairs act as the ordering check. Segments are cut at
    function characters: bridging across them would invent adjacencies that
    never occur in the surface text (``服务于提取`` yielding ``服提``), which made
    even a verbatim copy score below 100%.
    """
    pairs: list[str] = []

    def flush(segment: list[str]) -> None:
        pairs.extend("".join(segment[i : i + 2]) for i in range(len(segment) - 1))

    for run in CJK_RE.findall(normalize(text)):
        segment: list[str] = []
        for char in run:
            if char in CJK_CHAR_STOPWORDS:
                flush(segment)
                segment = []
                continue
            segment.append(char)
        flush(segment)

    seen: set[str] = set()
    return [p for p in pairs if not (p in seen or seen.add(p))]


def extract_keywords(text: str, extra_stopwords: Iterable[str] = ()) -> list[str]:
    blocked = set(extra_stopwords)
    return [u for u in tokenize(text) if u not in blocked and not is_stopword(u)]


def covers(unit: str, user_words: set[str], user_text: str) -> bool:
    if LATIN_RE.fullmatch(unit):
        return unit in user_words
    return unit in user_text


def missing_fragments(reference: str, missing_units: Sequence[str]) -> tuple[str, ...]:
    """Turn missing Chinese characters back into readable slices of the reference.

    ``["记", "忆"]`` becomes ``记忆`` so the hint points at a concept instead of a
    pile of loose characters. Runs are merged across a single covered character
    and widened by one character of context, otherwise slices break mid-word
    (``提高可`` instead of ``提高可及性``). Latin words are reported verbatim.
    """
    chars = {u for u in missing_units if not LATIN_RE.fullmatch(u)}
    words = [u for u in missing_units if LATIN_RE.fullmatch(u)]
    lowered = normalize(reference)
    if not chars:
        return tuple(words)
    marks = [char in chars for char in lowered]

    def is_cjk(char: str) -> bool:
        return bool(CJK_RE.fullmatch(char))

    groups: list[tuple[int, int]] = []
    index = 0
    while index < len(lowered):
        if not marks[index]:
            index += 1
            continue
        start = last = index
        while True:
            follower = None
            for j in range(last + 1, min(last + 1 + MAX_GAP + 1, len(lowered))):
                if marks[j]:
                    follower = j
                    break
                if not is_cjk(lowered[j]):
                    break  # punctuation ends the fragment; never merge across it
            if follower is None:
                break
            last = follower
        groups.append((start, last))
        index = last + 1

    fragments: list[str] = []
    for start, end in groups:
        left = start - 1 if start > 0 and is_cjk(lowered[start - 1]) else start
        right = end + 1 if end + 1 < len(lowered) and is_cjk(lowered[end + 1]) else end
        fragment = lowered[left : right + 1]
        if len(fragment) >= MIN_FRAGMENT_LEN and fragment not in fragments:
            fragments.append(fragment)
    return tuple(words + fragments)


@dataclass(frozen=True)
class Coverage:
    ratio: float
    total: int
    matched: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    label: str = NO_SIGNAL
    notes: tuple[str, ...] = field(default_factory=tuple)
    char_ratio: float = 0.0
    order_ratio: float = 0.0

    @property
    def percent(self) -> int:
        return round(self.ratio * 100)

    def summary(self) -> str:
        if self.total == 0:
            return "参考答案里没有可提取的关键词，跳过覆盖率，直接自评。"
        head = f"关键词覆盖率 {self.percent}%（内容字 {len(self.matched)}/{self.total}）· {self.label}"
        if self.missing:
            return f"{head}｜未覆盖：" + "、".join(self.missing[:MAX_MISSING_SHOWN])
        return head


def label_for(ratio: float) -> str:
    if ratio < LOW_THRESHOLD:
        return LOW_COVERAGE
    if ratio < GOOD_THRESHOLD:
        return MEDIUM_COVERAGE
    return HIGH_COVERAGE


def score_answer(
    reference_answer: str, user_answer: str, *, extra_stopwords: Iterable[str] = ()
) -> Coverage:
    """Content coverage of the learner's free text against the reference answer."""
    units = extract_keywords(reference_answer, extra_stopwords)
    if not units:
        return Coverage(ratio=0.0, total=0, label=NO_SIGNAL)
    pairs = content_bigrams(reference_answer)
    if not normalize(user_answer).strip():
        return Coverage(
            ratio=0.0,
            total=len(units),
            missing=missing_fragments(reference_answer, units),
            label=LOW_COVERAGE,
            notes=("没有生成任何答案。",),
        )

    user_words = {u for u in tokenize(user_answer) if LATIN_RE.fullmatch(u)}
    user_text = normalize(user_answer)
    matched = tuple(u for u in units if covers(u, user_words, user_text))
    unmatched = tuple(u for u in units if u not in matched)
    char_ratio = len(matched) / len(units)
    order_ratio = (
        sum(1 for pair in pairs if pair in user_text) / len(pairs) if pairs else char_ratio
    )
    return Coverage(
        ratio=(char_ratio + order_ratio) / 2,
        total=len(units),
        matched=matched,
        missing=missing_fragments(reference_answer, unmatched),
        label=label_for((char_ratio + order_ratio) / 2),
        char_ratio=char_ratio,
        order_ratio=order_ratio,
    )


def refinement_hint(coverage: Coverage) -> str:
    """Turn a miss into a next-step prompt (细化)."""
    if coverage.total == 0:
        return "给参考答案补几个关键词，覆盖率评分才有效。"
    if coverage.label == LOW_COVERAGE:
        missing = "、".join(coverage.missing[:5])
        return f"先别重读资料，把漏掉的点（{missing}）自己再补一次，补不上的明天优先复习。"
    if coverage.label == MEDIUM_COVERAGE:
        missing = "、".join(coverage.missing[:5])
        return f"主干抓住了，还差：{missing}。试着把它们连成一句因果句。"
    return "覆盖良好。下一步：换个例子或换个场景把它讲一遍，防止只记住了字面。"


def evaluate_with_llm(question: str, answer: str, user_answer: str) -> Optional[str]:
    """Optional semantic grading hook; returns None unless a backend is wired in.

    Deliberately not called by default: coverage is a deterministic, offline
    proxy. Set ``MIST_LLM_EVAL=1`` and replace this body with an actual call if
    you want paraphrase-level judgement.
    """
    if os.environ.get(LLM_EVAL_ENV, "") not in {"1", "true", "yes"}:
        return None
    return None


def grade_answer(
    question: str, answer: str, user_answer: str, *, extra_stopwords: Sequence[str] = ()
) -> tuple[Coverage, Optional[str]]:
    """Coverage plus an optional LLM comment (None unless a hook is implemented)."""
    coverage = score_answer(answer, user_answer, extra_stopwords=extra_stopwords)
    return coverage, evaluate_with_llm(question, answer, user_answer)
