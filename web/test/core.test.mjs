/**
 * Parity suite: web/core.js must reproduce the behavior recorded from the Python
 * reference implementation (tests/vectors/behavior.json).
 *
 * Run with:  node --test web/test/
 * CI runs it on every push; regenerate the vectors with
 *   python tests/make_behavior_vectors.py
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as core from "../core.js";

const vectors = JSON.parse(
  readFileSync(new URL("../../tests/vectors/behavior.json", import.meta.url), "utf8")
);
const DAY = 86400000;
const NOW = vectors.nowMs;

const hydrateCards = (rows) =>
  rows.map((row) => ({
    id: row.id,
    topicId: row.topicId,
    ease: row.ease,
    interval: row.interval,
    repetitions: row.repetitions,
    lapses: row.lapses,
    dueAt: NOW + row.dueOffsetDays * DAY,
    lastReviewedAt: row.studied ? NOW - DAY : null,
  }));

const hydrateReviews = (rows) =>
  rows.map((row) => ({
    cardId: row.cardId,
    rating: row.rating,
    reviewedAt: NOW - row.daysAgo * DAY,
  }));

const round12 = (value) => core.roundHalfToEven(value, 12);

test("stopword sets match the Python implementation verbatim", () => {
  assert.deepEqual([...core.CJK_CHAR_STOPWORDS].sort(), vectors.meta.cjkCharStopwords);
  assert.deepEqual([...core.LATIN_STOPWORDS].sort(), vectors.meta.latinStopwords);
});

test("tuning constants match", () => {
  assert.equal(core.DEFAULT_EASE, vectors.meta.defaultEase);
  assert.equal(core.MIN_EASE, vectors.meta.minEase);
  assert.equal(core.PASS_THRESHOLD, vectors.meta.passThreshold);
  assert.equal(core.MAX_CONSECUTIVE_SAME_TOPIC, vectors.meta.maxConsecutiveSameTopic);
  assert.equal(core.WEAK_BOOST_THRESHOLD, vectors.meta.weakBoostThreshold);
  assert.equal(core.RECENT_REVIEW_WINDOW_DAYS, vectors.meta.recentReviewWindowDays);
  assert.equal(core.LOW_THRESHOLD, vectors.meta.thresholds.low);
  assert.equal(core.GOOD_THRESHOLD, vectors.meta.thresholds.good);
});

test("SM-2 sequences reproduce Python step for step", () => {
  for (const { start, steps } of vectors.scheduler) {
    let state = { ...start };
    steps.forEach((expected, index) => {
      const actual = core.nextSchedule({ ...state, rating: expected.rating, now: NOW });
      assert.deepEqual(
        {
          ease: actual.ease,
          interval: actual.interval,
          repetitions: actual.repetitions,
          lapses: actual.lapses,
          dueInDays: Math.round((actual.dueAt - NOW) / DAY),
          rating: expected.rating,
        },
        {
          ease: expected.ease,
          interval: expected.interval,
          repetitions: expected.repetitions,
          lapses: expected.lapses,
          dueInDays: expected.dueInDays,
          rating: expected.rating,
        },
        `sequence ${JSON.stringify(start)} step ${index}`
      );
      state = {
        ease: actual.ease,
        interval: actual.interval,
        repetitions: actual.repetitions,
        lapses: actual.lapses,
      };
    });
  }
});

test("ease is floored at 1.3 exactly like Python", () => {
  let ease = core.DEFAULT_EASE;
  for (let i = 0; i < 40; i += 1) ease = core.nextEase(ease, 0);
  assert.equal(ease, core.MIN_EASE);
});

test("invalid ratings are rejected", () => {
  for (const bad of [-1, 6, "x", null, undefined, 2.5]) {
    assert.throws(() => core.nextSchedule({ rating: bad, now: NOW }), core.InvalidRating);
  }
});

test("mastery bands match", () => {
  for (const { card, level } of vectors.mastery) {
    const hydrated = {
      ease: card.ease,
      interval: card.interval,
      repetitions: card.repetitions,
      lapses: card.lapses,
      lastReviewedAt: card.studied ? NOW : null,
    };
    assert.equal(core.masteryLevel(hydrated), level, JSON.stringify(card));
  }
});

test("coverage units match Python tokenization", () => {
  for (const vector of vectors.coverage) {
    assert.deepEqual(core.extractKeywords(vector.reference), vector.keywords, vector.reference.slice(0, 20));
    assert.deepEqual(core.contentBigrams(vector.reference), vector.bigrams, vector.reference.slice(0, 20));
  }
});

test("coverage scoring matches Python field for field", () => {
  for (const vector of vectors.coverage) {
    const actual = core.scoreAnswer(vector.reference, vector.userAnswer);
    assert.deepEqual(
      {
        total: actual.total,
        matched: actual.matched,
        missing: actual.missing,
        ratio: round12(actual.ratio),
        percent: actual.percent,
        label: actual.label,
        notes: actual.notes,
        summary: core.coverageSummary(actual),
        hint: core.refinementHint(actual),
      },
      {
        total: vector.total,
        matched: vector.matched,
        missing: vector.missing,
        ratio: vector.ratio,
        percent: vector.percent,
        label: vector.label,
        notes: vector.notes,
        summary: vector.summary,
        hint: vector.hint,
      },
      `reference=${vector.reference.slice(0, 18)} user=${vector.userAnswer.slice(0, 18)}`
    );
  }
});

test("a scrambled copy can never reach 覆盖良好", () => {
  const scrambled = vectors.coverage.find((vector) =>
    vector.userAnswer.length > 0 &&
    [...vector.userAnswer].sort().join("") === [...vector.reference].sort().join("") &&
    vector.userAnswer !== vector.reference
  );
  assert.ok(scrambled, "expected a reversed-reference vector");
  assert.ok(scrambled.charRatio > 0.9, "every character is present, so char view saturates");
  assert.equal(scrambled.orderRatio, 0, "order view must collapse for scrambled text");
  assert.ok(scrambled.percent <= 50, `scrambled copy scored ${scrambled.percent}%`);
  assert.notEqual(scrambled.label, core.HIGH_COVERAGE);
});

test("topic weakness scores match", () => {
  const cards = hydrateCards(vectors.weakness.cards);
  const reviews = hydrateReviews(vectors.weakness.reviews);
  const actual = new Map([...core.topicWeakness(cards, reviews, NOW)].map(([k, v]) => [String(k), v]));
  assert.deepEqual(Object.fromEntries([...actual].sort()), vectors.weakness.weakness);
});

test("queue order matches for the due pool and respects the new-card cap", () => {
  const cards = hydrateCards(vectors.queue.cards);
  const studiedIds = new Set(vectors.queue.cards.filter((row) => row.studied).map((row) => row.id));

  for (const plan of vectors.queue.plans) {
    const actual = core.buildReviewPlan(cards, [], {
      limit: plan.limit,
      newLimit: plan.newLimit,
      now: NOW,
      seed: 0,
    });
    const ids = actual.cards.map((card) => card.id);

    // Python shuffles new-card groups with Mersenne Twister, which JS cannot
    // reproduce; compare due order exactly and new-card membership as a set.
    assert.deepEqual(
      ids.filter((id) => studiedIds.has(id)),
      plan.ids.filter((id) => studiedIds.has(id)),
      `due order for limit=${plan.limit}`
    );
    assert.deepEqual(
      [...ids.filter((id) => !studiedIds.has(id))].sort((a, b) => a - b),
      [...plan.ids.filter((id) => !studiedIds.has(id))].sort((a, b) => a - b),
      `new membership for limit=${plan.limit}`
    );
    assert.equal(ids.length, plan.ids.length);
    assert.equal(actual.dueAvailable, plan.dueAvailable);
    assert.equal(actual.newAvailable, plan.newAvailable);
    assert.equal(actual.newTaken, plan.newTaken);
    assert.ok(actual.newTaken <= plan.newLimit);

    for (let i = 0; i + 2 < ids.length; i += 1) {
      const topic = actual.cards[i].topicId;
      if (actual.cards[i + 1].topicId !== topic) continue;
      assert.notEqual(actual.cards[i + 2].topicId, topic, `three in a row at ${i}`);
    }
  }
});

test("topic spread enforcement matches", () => {
  for (const { input, output } of vectors.spread) {
    const cards = input.map((topicId, index) => ({ topicId, id: index }));
    assert.deepEqual(
      core.enforceTopicSpread(cards, 2).map((card) => card.topicId),
      output,
      JSON.stringify(input)
    );
  }
});

test("review streak matches", () => {
  for (const vector of vectors.streak) {
    const reviews = vector.dayOffsets.map((day) => ({
      cardId: 1,
      rating: 3,
      reviewedAt: NOW - day * DAY,
    }));
    const { streakDays, studiedToday } = core.reviewStreak(reviews, NOW);
    assert.deepEqual(
      { dayOffsets: vector.dayOffsets, streakDays, studiedToday },
      { dayOffsets: vector.dayOffsets, streakDays: vector.streakDays, studiedToday: vector.studiedToday }
    );
  }
});

test("stats summary matches Python, including the health line", () => {
  const data = vectors.summary;
  const cards = hydrateCards(data.cards);
  const reviews = hydrateReviews(data.reviews);
  const topics = data.allTopics.map((topic) => ({ id: topic.id, name: topic.name }));

  const actual = core.summarize(cards, reviews, topics, { now: NOW, recentDays: 7 });

  assert.equal(actual.totalCards, data.totalCards);
  assert.equal(actual.totalTopics, data.totalTopics);
  assert.equal(actual.dueToday, data.dueToday);
  assert.equal(actual.dueBacklog, data.dueBacklog);
  assert.deepEqual(actual.mastery, data.mastery);
  assert.equal(actual.recentTotal, data.recentTotal);
  assert.equal(actual.streakDays, data.streakDays);
  assert.equal(actual.studiedToday, data.studiedToday);
  assert.equal(actual.reviewsTotal, data.reviewsTotal);
  assert.equal(actual.healthLine, data.healthLine);
  assert.deepEqual(actual.weakest, data.weakest);
  assert.deepEqual(
    actual.recentDaysSeries,
    data.recent_days ?? data.recentDays
  );
  assert.deepEqual(
    actual.topics.map((topic) => ({
      name: topic.name,
      cards: topic.cards,
      dueToday: topic.dueToday,
      avgEase: topic.avgEase,
      avgInterval: topic.avgInterval,
      lapses: topic.lapses,
      weakness: topic.weakness,
      studied: topic.studied,
    })),
    data.topics
  );
});
