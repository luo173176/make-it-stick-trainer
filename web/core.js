/**
 * make-it-stick-trainer — browser core (spaced repetition, interleaving, coverage).
 *
 * This file is a second implementation of the algorithms in src/mist/{scheduler,
 * interleaver,generator,stats}.py. It exists because the phone build must run
 * without a Python backend; the CLI stays the reference implementation.
 *
 * The two implementations are kept honest by tests/vectors/behavior.json, which
 * is generated from Python and asserted against here (see web/test/core.test.mjs)
 * and against the Python modules themselves (see tests/test_behavior_vectors.py).
 *
 * Deliberately excluded from parity: the seeded shuffle of new-card groups.
 * Python uses Mersenne Twister via random.Random, which cannot be reproduced in
 * JS; the shuffle only reorders cards *within* one topic, so it cannot change
 * the interleaving invariants, which are asserted instead.
 *
 * Timestamps are epoch milliseconds so ordering never depends on locale parsing.
 */

export const DEFAULT_EASE = 2.5;
export const MIN_EASE = 1.3;
export const PASS_THRESHOLD = 3;
export const FIRST_INTERVAL = 1;
export const SECOND_INTERVAL = 6;

export const MAX_CONSECUTIVE_SAME_TOPIC = 2;
export const WEAK_BOOST_THRESHOLD = 0.35;
export const RECENT_REVIEW_WINDOW_DAYS = 30;

export const LOW_COVERAGE = "覆盖不足";
export const MEDIUM_COVERAGE = "基本覆盖";
export const HIGH_COVERAGE = "覆盖良好";
export const NO_SIGNAL = "无法评分";
export const LOW_THRESHOLD = 0.35;
export const GOOD_THRESHOLD = 0.7;

export const MASTERY_NEW = "新卡片";
export const MASTERY_LEARNING = "学习中";
export const MASTERY_CONSOLIDATING = "巩固中";
export const MASTERY_MASTERED = "已掌握";
export const MASTERY_ORDER = [
  MASTERY_MASTERED,
  MASTERY_CONSOLIDATING,
  MASTERY_LEARNING,
  MASTERY_NEW,
];

/** Must stay character-identical to CJK_CHAR_STOPWORDS in src/mist/generator.py. */
export const CJK_CHAR_STOPWORDS = new Set(
  ("的了是在和与及或着过把被为对从以就都也很更最这那有没人之等吗呢吧啊呀么并而于其该各" +
    "只未免去即则虽若让又才且但所由被将使其兹兮焉哉矣耳").split("")
);

/** Must stay identical to LATIN_STOPWORDS in src/mist/generator.py. */
export const LATIN_STOPWORDS = new Set(
  `a an the and or of to in for on with as at by is are was were be been being
   it its this that these those from not but then than too very can could should
   would will must do does did doing have has had having i you we they he she
   them him his her our your their which who whom whose what when where why how
   also such into over under out up down so if no yes one two three more most`
    .split(/\s+/)
    .filter(Boolean)
);

const LATIN_RE = /[a-z0-9_]+/g;
const LATIN_ONE = /^[a-z0-9_]+$/;
const CJK_RE = /[一-鿿]+/g;
const DAY_MS = 86400000;

/* ------------------------------------------------------------------ numbers */

/**
 * Python's round(): banker's rounding (half to even). Plain Math.round always
 * rounds halves up, which would drift from the CLI on exact .5 values.
 */
export function roundHalfToEven(value, digits = 0) {
  if (!Number.isFinite(value)) return value;
  const factor = 10 ** digits;
  const scaled = value * factor;
  const floor = Math.floor(scaled);
  const diff = scaled - floor;
  let rounded;
  if (Math.abs(diff - 0.5) < 1e-9) {
    rounded = floor % 2 === 0 ? floor : floor + 1;
  } else {
    rounded = Math.round(scaled);
  }
  // Kill float dust (0.1 + 0.2 style) so equality checks against Python hold.
  return Number((rounded / factor).toFixed(12));
}

const clamp01 = (value) => Math.max(0, Math.min(1, value));

/** SQLite's default collation orders text by code point, not by locale. */
export function compareNames(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

/* --------------------------------------------------------------- scheduler */

export class InvalidRating extends Error {}

export function validateRating(rating) {
  // Numbers only, integers only: matches scheduler.validate_rating, which
  // rejects both strings and floats rather than coercing them.
  if (typeof rating !== "number" || !Number.isInteger(rating) || rating < 0 || rating > 5) {
    throw new InvalidRating(`评分必须是 0-5 的整数，收到 ${JSON.stringify(rating)}`);
  }
  return rating;
}

export function nextEase(ease, rating) {
  const delta = 5 - rating;
  return roundHalfToEven(Math.max(MIN_EASE, ease + (0.1 - delta * (0.08 + delta * 0.02))), 4);
}

/** Pure SM-2 step; mirrors scheduler.next_schedule. */
export function nextSchedule({
  ease = DEFAULT_EASE,
  interval = 0,
  repetitions = 0,
  lapses = 0,
  rating,
  now,
}) {
  const scored = validateRating(rating);
  const moment = now ?? Date.now();
  let nextInterval;
  let nextReps;
  let nextLapses;

  if (scored < PASS_THRESHOLD) {
    nextInterval = FIRST_INTERVAL;
    nextReps = 0;
    nextLapses = lapses + 1;
  } else {
    nextReps = repetitions + 1;
    if (repetitions === 0) nextInterval = FIRST_INTERVAL;
    else if (repetitions === 1) nextInterval = SECOND_INTERVAL;
    else nextInterval = Math.max(1, roundHalfToEven(interval * ease, 0));
    nextLapses = lapses;
  }

  return {
    ease: nextEase(ease, scored),
    interval: nextInterval,
    repetitions: nextReps,
    lapses: nextLapses,
    dueAt: moment + nextInterval * DAY_MS,
  };
}

export function applyReview(card, rating, now) {
  const schedule = nextSchedule({
    ease: card.ease,
    interval: card.interval,
    repetitions: card.repetitions,
    lapses: card.lapses,
    rating,
    now,
  });
  const elapsedDays = card.lastReviewedAt
    ? Math.floor((now - card.lastReviewedAt) / DAY_MS)
    : 0;
  Object.assign(card, schedule, { lastReviewedAt: now });
  return { schedule, elapsedDays };
}

export function isNewCard(card) {
  return !card.lastReviewedAt && (card.repetitions ?? 0) === 0;
}

export function masteryLevel(card) {
  if (isNewCard(card)) return MASTERY_NEW;
  if (card.interval >= 21 && card.repetitions >= 3 && card.ease >= 2.2) return MASTERY_MASTERED;
  if (card.interval >= 7) return MASTERY_CONSOLIDATING;
  return MASTERY_LEARNING;
}

export function dueLabel(interval) {
  if (interval <= 0) return "现在";
  if (interval === 1) return "明天";
  if (interval < 30) return `${interval} 天后`;
  if (interval < 365) return `约 ${roundHalfToEven(interval / 30, 1)} 个月后`;
  return `约 ${roundHalfToEven(interval / 365, 1)} 年后`;
}

/* --------------------------------------------------------------- coverage */

export function normalizeText(text) {
  return (text ?? "").normalize("NFKC").toLowerCase();
}

export function isStopword(token) {
  if (LATIN_STOPWORDS.has(token)) return true;
  return token.length === 1 && CJK_CHAR_STOPWORDS.has(token);
}

function latinWords(lowered) {
  return (lowered.match(LATIN_RE) ?? []).filter(
    (word) => word.length >= 2 || /[0-9]/.test(word)
  );
}

function cjkRuns(lowered) {
  return lowered.match(CJK_RE) ?? [];
}

/** Scorable units: latin words + Chinese content characters, deduped in order. */
export function tokenize(text) {
  const lowered = normalizeText(text);
  const units = [...latinWords(lowered)];
  for (const run of cjkRuns(lowered)) {
    for (const char of run) if (!CJK_CHAR_STOPWORDS.has(char)) units.push(char);
  }
  return [...new Set(units)];
}

/** Adjacent content-character pairs, cut at function characters. */
export function contentBigrams(text) {
  const pairs = [];
  for (const run of cjkRuns(normalizeText(text))) {
    let segment = [];
    const flush = () => {
      for (let i = 0; i + 1 < segment.length; i += 1) pairs.push(segment.slice(i, i + 2).join(""));
      segment = [];
    };
    for (const char of run) {
      if (CJK_CHAR_STOPWORDS.has(char)) flush();
      else segment.push(char);
    }
    flush();
  }
  return [...new Set(pairs)];
}

export function extractKeywords(text, extraStopwords = []) {
  const blocked = new Set(extraStopwords);
  return tokenize(text).filter((unit) => !blocked.has(unit) && !isStopword(unit));
}

/** Missing characters become readable slices of the reference, never loose chars. */
export function missingFragments(reference, missingUnits) {
  const chars = new Set(missingUnits.filter((unit) => !LATIN_ONE.test(unit)));
  const words = missingUnits.filter((unit) => LATIN_ONE.test(unit));
  const lowered = normalizeText(reference);
  if (chars.size === 0) return words;

  const marks = [...lowered].map((char) => chars.has(char));
  const groups = [];
  for (let i = 0; i < marks.length; i += 1) {
    if (!marks[i]) continue;
    let last = i;
    for (;;) {
      let follower = null;
      for (let j = last + 1; j < Math.min(last + 2 + 1, marks.length); j += 1) {
        if (marks[j]) {
          follower = j;
          break;
        }
        if (!/[一-鿿]/.test(lowered[j])) break;
      }
      if (follower === null) break;
      last = follower;
    }
    groups.push([i, last]);
    i = last;
  }

  const source = [...lowered];
  const isCjk = (char) => char !== undefined && /[一-鿿]/.test(char);
  const fragments = [];
  for (const [start, end] of groups) {
    const left = isCjk(source[start - 1]) ? start - 1 : start;
    const right = isCjk(source[end + 1]) ? end + 1 : end;
    const fragment = source.slice(left, right + 1).join("");
    if (fragment.length >= 2 && !fragments.includes(fragment)) fragments.push(fragment);
  }
  return [...words, ...fragments];
}

export function labelFor(ratio) {
  if (ratio < LOW_THRESHOLD) return LOW_COVERAGE;
  if (ratio < GOOD_THRESHOLD) return MEDIUM_COVERAGE;
  return HIGH_COVERAGE;
}

/** Blended content-character + word-order coverage; mirrors generator.score_answer. */
export function scoreAnswer(referenceAnswer, userAnswer, extraStopwords = []) {
  const units = extractKeywords(referenceAnswer, extraStopwords);
  if (units.length === 0) {
    return { ratio: 0, total: 0, matched: [], missing: [], label: NO_SIGNAL, notes: [], charRatio: 0, orderRatio: 0, percent: 0 };
  }
  const pairs = contentBigrams(referenceAnswer);
  const loweredUser = normalizeText(userAnswer ?? "");

  if (loweredUser.trim() === "") {
    const ratio = 0;
    return {
      ratio,
      total: units.length,
      matched: [],
      missing: missingFragments(referenceAnswer, units),
      label: LOW_COVERAGE,
      notes: ["没有生成任何答案。"],
      charRatio: 0,
      orderRatio: pairs.length ? 0 : 0,
      percent: 0,
    };
  }

  const userWords = new Set(tokenize(userAnswer).filter((unit) => LATIN_ONE.test(unit)));
  const matched = units.filter((unit) =>
    LATIN_ONE.test(unit) ? userWords.has(unit) : loweredUser.includes(unit)
  );
  const unmatched = units.filter((unit) => !matched.includes(unit));
  const charRatio = matched.length / units.length;
  const orderRatio = pairs.length
    ? pairs.filter((pair) => loweredUser.includes(pair)).length / pairs.length
    : charRatio;
  const ratio = (charRatio + orderRatio) / 2;

  return {
    ratio,
    total: units.length,
    matched,
    missing: missingFragments(referenceAnswer, unmatched),
    label: labelFor(ratio),
    notes: [],
    charRatio,
    orderRatio,
    percent: roundHalfToEven(ratio * 100, 0),
  };
}

export function coverageSummary(coverage) {
  if (coverage.total === 0) {
    return "参考答案里没有可提取的关键词，跳过覆盖率，直接自评。";
  }
  const head = `关键词覆盖率 ${coverage.percent}%（内容字 ${coverage.matched.length}/${coverage.total}）· ${coverage.label}`;
  return coverage.missing.length
    ? `${head}｜未覆盖：${coverage.missing.slice(0, 8).join("、")}`
    : head;
}

export function refinementHint(coverage) {
  if (coverage.total === 0) return "给参考答案补几个关键词，覆盖率评分才有效。";
  const missing = coverage.missing.slice(0, 5).join("、");
  if (coverage.label === LOW_COVERAGE) {
    return `先别重读资料，把漏掉的点（${missing}）自己再补一次，补不上的明天优先复习。`;
  }
  if (coverage.label === MEDIUM_COVERAGE) {
    return `主干抓住了，还差：${missing}。试着把它们连成一句因果句。`;
  }
  return "覆盖良好。下一步：换个例子或换个场景把它讲一遍，防止只记住了字面。";
}

/* ------------------------------------------------------------ interleaver */

export function dueCards(cards, now) {
  return cards
    .filter((card) => card.dueAt <= now && card.lastReviewedAt)
    .sort((a, b) => a.dueAt - b.dueAt || a.id - b.id);
}

export function newCards(cards, now) {
  return cards.filter((card) => card.dueAt <= now && !card.lastReviewedAt).sort((a, b) => a.id - b.id);
}

/** 0..1 weakness per topic id; mirrors interleaver.topic_weakness. */
export function topicWeakness(cards, reviews, now = Date.now()) {
  const byTopic = new Map();
  for (const card of cards) {
    if (!byTopic.has(card.topicId)) byTopic.set(card.topicId, []);
    byTopic.get(card.topicId).push(card);
  }
  const windowStart = now - RECENT_REVIEW_WINDOW_DAYS * DAY_MS;
  const cardTopic = new Map(cards.map((card) => [card.id, card.topicId]));
  const recent = new Map();
  for (const review of reviews) {
    if (review.reviewedAt < windowStart) continue;
    const topicId = cardTopic.get(review.cardId);
    if (topicId === undefined) continue;
    if (!recent.has(topicId)) recent.set(topicId, []);
    recent.get(topicId).push(review.rating);
  }

  const weakness = new Map();
  for (const [topicId, topicCards] of byTopic) {
    const count = Math.max(1, topicCards.length);
    const avgEase = topicCards.reduce((sum, card) => sum + card.ease, 0) / count;
    const totalLapses = topicCards.reduce((sum, card) => sum + (card.lapses || 0), 0);
    const ratings = recent.get(topicId);
    const easeTerm = clamp01((DEFAULT_EASE - avgEase) / (DEFAULT_EASE - MIN_EASE));
    const lapseTerm = clamp01(totalLapses / count / 2);
    const ratingTerm = ratings ? clamp01((3.5 - ratings.reduce((a, b) => a + b, 0) / ratings.length) / 3.5) : 0;
    weakness.set(topicId, roundHalfToEven(0.45 * easeTerm + 0.25 * lapseTerm + 0.3 * ratingTerm, 4));
  }
  return weakness;
}

/** Greedy reorder so a topic never exceeds maxConsecutive in a row. */
export function enforceTopicSpread(cards, maxConsecutive = MAX_CONSECUTIVE_SAME_TOPIC) {
  const pending = [...cards];
  const result = [];
  while (pending.length) {
    const index = pending.findIndex(
      (card) => !streakViolated(result, card.topicId, maxConsecutive)
    );
    result.push(pending.splice(index === -1 ? 0 : index, 1)[0]);
  }
  return result;
}

function streakViolated(queue, topicId, limit) {
  if (limit < 1 || queue.length < limit) return false;
  for (let i = queue.length - limit; i < queue.length; i += 1) {
    if (queue[i].topicId !== topicId) return false;
  }
  return true;
}

function groupByTopic(cards) {
  const groups = new Map();
  for (const card of cards) {
    if (!groups.has(card.topicId)) groups.set(card.topicId, []);
    groups.get(card.topicId).push(card);
  }
  return groups;
}

function roundRobin(groups, weakness) {
  const remaining = new Map([...groups].map(([id, list]) => [id, [...list]]));
  const order = [...remaining.keys()].sort(
    (a, b) => (weakness.get(b) ?? 0) - (weakness.get(a) ?? 0) || a - b
  );
  const out = [];
  while ([...remaining.values()].some((list) => list.length)) {
    for (const topicId of order) {
      const pool = remaining.get(topicId);
      if (!pool || !pool.length) continue;
      const budget = (weakness.get(topicId) ?? 0) >= WEAK_BOOST_THRESHOLD ? 2 : 1;
      for (let taken = 0; taken < budget && pool.length; taken += 1) out.push(pool.shift());
    }
  }
  return enforceTopicSpread(out);
}

/** mulberry32: deterministic PRNG so a seed reproduces a queue in the browser. */
export function makeRandom(seed) {
  let state = (seed >>> 0) || 1;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function shuffleInPlace(list, random) {
  for (let i = list.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [list[i], list[j]] = [list[j], list[i]];
  }
}

export function buildReviewPlan(cards, reviews, {
  limit = 20,
  newLimit = 10,
  now = Date.now(),
  seed = null,
} = {}) {
  const weakness = topicWeakness(cards, reviews, now);
  const due = roundRobin(groupByTopic(dueCards(cards, now)), weakness);
  const newGroups = groupByTopic(newCards(cards, now));
  const random = makeRandom(seed ?? 20260101);
  for (const list of newGroups.values()) shuffleInPlace(list, random);
  const fresh = roundRobin(newGroups, weakness).slice(0, Math.max(0, newLimit));
  const dueIds = new Set(dueCards(cards, now).map((card) => card.id));
  const queue = [...due, ...fresh].slice(0, Math.max(0, limit));

  return {
    cards: queue,
    dueAvailable: dueIds.size,
    newAvailable: newCards(cards, now).length,
    newTaken: queue.filter((card) => !card.lastReviewedAt).length,
    weakTopics: [...weakness.entries()]
      .filter(([, score]) => score >= WEAK_BOOST_THRESHOLD)
      .sort((a, b) => b[1] - a[1] || a[0] - b[0])
      .map(([topicId]) => topicId),
  };
}

/* ------------------------------------------------------------------ stats */

export function reviewStreak(reviews, now = Date.now()) {
  const days = new Set(reviews.map((review) => dayKey(review.reviewedAt)));
  if (!days.size) return { streakDays: 0, studiedToday: false };
  const today = dayKey(now);
  const studiedToday = days.has(today);
  let cursor = studiedToday ? now : now - DAY_MS;
  let streak = 0;
  while (days.has(dayKey(cursor))) {
    streak += 1;
    cursor -= DAY_MS;
  }
  return { streakDays: streak, studiedToday };
}

/** Local calendar day, matching Python's naive-UTC date buckets closely enough. */
export function dayKey(millis) {
  return new Date(millis).toISOString().slice(0, 10);
}

export function summarize(cards, reviews, topics, { now = Date.now(), recentDays = 7 } = {}) {
  const startOfDay = new Date(now);
  startOfDay.setUTCHours(0, 0, 0, 0);
  const horizon = startOfDay.getTime() + DAY_MS;

  const mastery = {};
  for (const name of MASTERY_ORDER) mastery[name] = 0;
  for (const card of cards) mastery[masteryLevel(card)] += 1;

  const weakness = topicWeakness(cards, reviews, now);
  const studiedTopics = new Set(cards.filter((card) => card.lastReviewedAt).map((card) => card.topicId));

  const perTopic = topics
    .map((topic) => {
      const topicCards = cards.filter((card) => card.topicId === topic.id);
      const count = topicCards.length || 0;
      const avgEase = count ? topicCards.reduce((s, c) => s + c.ease, 0) / count : 0;
      const avgInterval = count ? topicCards.reduce((s, c) => s + c.interval, 0) / count : 0;
      const lapses = topicCards.reduce((s, c) => s + (c.lapses || 0), 0);
      return {
        id: topic.id,
        name: topic.name,
        cards: count,
        dueToday: topicCards.filter((card) => card.dueAt < horizon).length,
        avgEase: roundHalfToEven(avgEase, 2),
        avgInterval: roundHalfToEven(avgInterval, 1),
        lapses,
        weakness: roundHalfToEven(weakness.get(topic.id) ?? 0, 3),
        studied: studiedTopics.has(topic.id),
      };
    })
    .sort((a, b) => compareNames(a.name, b.name));

  const windowStart = now - recentDays * DAY_MS;
  const buckets = new Map();
  for (const review of reviews) {
    if (review.reviewedAt >= windowStart) {
      const key = dayKey(review.reviewedAt);
      buckets.set(key, (buckets.get(key) ?? 0) + 1);
    }
  }
  const series = [];
  for (let offset = recentDays - 1; offset >= 0; offset -= 1) {
    const key = dayKey(now - offset * DAY_MS);
    series.push([key, buckets.get(key) ?? 0]);
  }

  const { streakDays, studiedToday } = reviewStreak(reviews, now);
  const dueToday = cards.filter((card) => card.dueAt < horizon).length;
  const dueBacklog = cards.filter((card) => card.dueAt < startOfDay.getTime()).length;
  const mastered = mastery[MASTERY_MASTERED] ?? 0;

  return {
    totalCards: cards.length,
    totalTopics: topics.length,
    dueToday,
    dueBacklog,
    mastery,
    topics: perTopic,
    recentDaysSeries: series,
    recentTotal: series.reduce((sum, [, count]) => sum + count, 0),
    weakest: perTopic
      .filter((topic) => topic.studied && topic.weakness > 0)
      .sort((a, b) => b.weakness - a.weakness || compareNames(a.name, b.name))
      .slice(0, 5)
      .map((topic) => [topic.name, topic.weakness]),
    streakDays,
    studiedToday,
    reviewsTotal: reviews.length,
    healthLine: `卡片 ${cards.length} 张 / 主题 ${topics.length} 个 · 今日到期 ${dueToday} · ` +
      `已掌握 ${mastered}（${cards.length ? roundHalfToEven((100 * mastered) / cards.length, 0) : 0}%）· 连续复习 ${streakDays} 天`,
  };
}
