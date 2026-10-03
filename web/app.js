/**
 * Phone app: views and interaction. All scheduling/interleaving/coverage math
 * lives in core.js; persistence lives in store.js.
 */

import * as core from "./core.js";
import * as store from "./store.js";

const state = {
  topics: [],
  cards: [],
  reviews: [],
  reflections: [],
  queue: [],
  index: 0,
  sessionId: "",
  tab: "review",
};

const $ = (id) => document.getElementById(id);
const esc = (text) =>
  String(text ?? "").replace(/[&<>"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char]));

const REFLECTION_QUESTIONS = [
  ["coreConcept", "今天最核心的概念是什么？（一句话，自己的话）"],
  ["connection", "它和你已知的什么有联系？（旧知识 / 别的主题 / 亲历）"],
  ["nextImprovement", "下次练习你会怎么改进？（一个具体动作）"],
];

const RATING_HINTS = {
  0: "完全想不起来",
  1: "答错，看答案才认出",
  2: "大致对，漏了要点",
  3: "基本正确，回忆费力",
  4: "正确且较流畅",
  5: "正确、流畅、能举新例子",
};

async function reload() {
  const data = await store.loadAll();
  Object.assign(state, data);
}

function topicName(card) {
  return state.topics.find((topic) => topic.id === card.topicId)?.name ?? "未分类";
}

/* ------------------------------------------------------------------ review */

function buildQueue() {
  const now = Date.now();
  const plan = core.buildReviewPlan(state.cards, state.reviews, {
    limit: 20,
    newLimit: 10,
    now,
    seed: Math.floor(now / 60000) % 100000,
  });
  state.queue = plan.cards;
  state.index = 0;
  return plan;
}

/** Single place that recomputes the queue and repaints the review tab. */
function refreshQueue() {
  renderQueueHead(buildQueue());
  renderReview();
}

function renderQueueHead(plan) {
  const topics = new Set(plan.cards.map((card) => card.topicId));
  $("queue-head").innerHTML = plan.cards.length
    ? `<b>${plan.cards.length} 张 / ${topics.size} 个主题</b>，已按主题交错排列` +
      `<div class="meta">${plan.dueAvailable} 张到期复习 · ${plan.newTaken} 张新卡（共 ${plan.newAvailable} 张待学）</div>` +
      (plan.weakTopics.length
        ? `<div class="meta">薄弱主题加权：${plan.weakTopics.map((id) => esc(state.topics.find((t) => t.id === id)?.name ?? id)).join("、")}</div>`
        : "")
    : `<b>今天没有到期卡片</b><div class="meta">不要预习式重读资料 —— 等到期再做检索练习。</div>`;
  updateNavCount();
}

function updateNavCount() {
  const due = state.cards.filter((card) => card.dueAt < Date.now()).length;
  $("nav-count").innerHTML = due ? `<span class="badge">${due}</span>` : "";
}

function renderReview() {
  const stage = $("stage");
  if (!state.queue.length) {
    stage.innerHTML = `<div class="card"><p class="note">去<b>添加</b>页写几张卡片，或在<b>数据</b>页导入备份。</p></div>`;
    return;
  }
  if (state.index >= state.queue.length) {
    renderReflection();
    return;
  }

  const card = state.queue[state.index];
  stage.innerHTML = `<div class="card">
    <div class="meta">第 ${state.index + 1}/${state.queue.length} 题 · ${esc(topicName(card))} · 难度 ${card.difficulty}
      · ${core.masteryLevel(card)}</div>
    <p class="q">${esc(card.question)}</p>
    <textarea id="answer" placeholder="先合上资料，用自己的话写：关键词、因果链、例子。" autocomplete="off"></textarea>
    <p class="row" style="margin-top:10px">
      <button class="primary" id="reveal">写好了，看参考答案</button>
      <button id="skip">跳过</button>
    </p>
    <p class="note">跳过会让这张卡继续留在到期队列里 —— 它不会被排到明天。</p>
  </div>`;

  $("reveal").onclick = () => reveal(card);
  $("skip").onclick = () => {
    state.index += 1;
    renderReview();
  };
  $("answer").focus();
}

function reveal(card) {
  const userAnswer = $("answer").value;
  const coverage = core.scoreAnswer(card.answer, userAnswer);
  const cls = coverage.label === core.HIGH_COVERAGE ? "good" : coverage.label === core.MEDIUM_COVERAGE ? "mid" : "poor";
  const stage = $("stage");

  stage.innerHTML = `<div class="card">
    <div class="meta">第 ${state.index + 1}/${state.queue.length} 题 · ${esc(topicName(card))}</div>
    <p class="q">${esc(card.question)}</p>
    <p class="note">你的答案</p>
    <div class="ref">${esc(userAnswer.trim()) || "（空白 —— 按没生成处理）"}</div>
    <p class="note">参考答案</p>
    <div class="ref">${esc(card.answer)}</div>
    <p><span class="${cls}">${esc(core.coverageSummary(coverage))}</span></p>
    <p class="note">${esc(core.refinementHint(coverage))}</p>
    <p class="note" style="margin-top:14px">自评 0-5（诚实打分，分数只服务于你的记忆）</p>
    <div class="rates">${[0, 1, 2, 3, 4, 5]
      .map((n) => `<button data-r="${n}" title="${esc(RATING_HINTS[n])}">${n}</button>`)
      .join("")}</div>
    <p class="note" id="rating-hint">0 完全想不起来 · 3 回忆费力 · 5 流畅且能迁移</p>
  </div>`;

  stage.querySelectorAll(".rates button").forEach((button) => {
    button.onclick = () => grade(card, userAnswer, Number(button.dataset.r));
  });
}

async function grade(card, userAnswer, rating) {
  const now = Date.now();
  const coverage = core.scoreAnswer(card.answer, userAnswer);
  const { schedule, elapsedDays } = core.applyReview(card, rating, now);

  await store.put("cards", card);
  const record = await store.addReview({
    cardId: card.id,
    sessionId: state.sessionId,
    rating,
    userAnswer,
    feedback: core.coverageSummary(coverage),
    reviewedAt: now,
    elapsedDays,
    scheduledDays: schedule.interval,
  });
  // Keep the in-memory copy in sync: the session summary and the weakness
  // weighting both read state.reviews without going back to IndexedDB.
  state.reviews.push(record);

  const done = document.createElement("div");
  done.className = "card";
  done.innerHTML = `<p class="good">已记录：间隔 ${schedule.interval} 天，ease ${schedule.ease.toFixed(2)}，
      下次复习 ${esc(core.dueLabel(schedule.interval))}</p>
    <p class="note">${esc(core.masteryLevel(card))} · ${
      rating >= 5 ? "很轻松 —— 下次试着脱稿复述并举一个新例子。" :
      rating <= 2 ? "想不起来很正常，失败的检索同样在强化记忆痕迹。" :
      "努力回忆过的内容比重新阅读记得更牢。"
    }</p>
    <p class="row"><button class="primary" id="next">下一题</button></p>`;
  $("stage").innerHTML = "";
  $("stage").appendChild(done);
  $("next").onclick = () => {
    state.index += 1;
    renderReview();
  };
  updateNavCount();
}

function renderReflection() {
  const graded = state.reviews.filter((review) => review.sessionId === state.sessionId).length;
  $("stage").innerHTML = `<div class="card">
    <p class="q">这一轮练完了（${graded} 题）</p>
    <p class="note">合意困难的最后一步是把练习变成语言。三问都答收获最大，留空也可以跳过。</p>
    ${REFLECTION_QUESTIONS.map(
      ([name, label]) => `<label>${esc(label)}<textarea id="r-${name}"></textarea></label>`
    ).join("")}
    <p class="row">
      <button class="primary" id="save-reflect">保存反思</button>
      <button id="done">练完了</button>
    </p>
    <p class="note" id="reflect-msg"></p>
  </div>`;

  $("save-reflect").onclick = async () => {
    const saved = await store.addReflection({
      sessionId: state.sessionId,
      ...Object.fromEntries(REFLECTION_QUESTIONS.map(([name]) => [name, $(`r-${name}`).value.trim()])),
    });
    $("reflect-msg").textContent = saved ? "已保存。下次复习前先猜一遍自己上次写了什么。" : "三问都留空，没有保存。";
    if (saved) {
      state.reflections = [saved, ...state.reflections];
    }
    $("save-reflect").disabled = true;
  };
  $("done").onclick = () => {
    state.sessionId = newSessionId();
    refreshQueue();
  };
}

/* ------------------------------------------------------------------- stats */

async function renderStats() {
  const summary = core.summarize(state.cards, state.reviews, state.topics, { now: Date.now(), recentDays: 7 });
  $("s-health").textContent = summary.healthLine;
  $("s-overview").innerHTML =
    Object.entries(summary.mastery).map(([name, count]) => `<span class="pill">${esc(name)} ${count}</span>`).join("") +
    `<div class="meta">累计复习 ${summary.reviewsTotal} 次 · 反思 ${state.reflections.length} 篇` +
    (summary.dueBacklog ? ` · <span class="poor">逾期 ${summary.dueBacklog} 张</span>` : "") +
    (summary.studiedToday ? "" : " · 今天还没练习") +
    `</div>`;

  $("s-topics").innerHTML =
    "<tr><th>主题</th><th class=num>卡片</th><th class=num>到期</th><th class=num>ease</th><th class=num>间隔</th><th class=num>lapses</th><th class=num>薄弱</th></tr>" +
    (summary.topics.length
      ? summary.topics
          .map((topic) => {
            const cls = topic.weakness >= 0.45 ? "poor" : topic.weakness >= 0.3 ? "mid" : "good";
            return `<tr><td>${esc(topic.name)}</td><td class=num>${topic.cards}</td><td class=num>${topic.dueToday}</td>
              <td class=num>${topic.avgEase.toFixed(2)}</td><td class=num>${topic.avgInterval.toFixed(1)}</td>
              <td class=num>${topic.lapses}</td><td class=num><span class="${cls}">${topic.weakness.toFixed(2)}</span></td></tr>`;
          })
          .join("")
      : "<tr><td colspan=7 class=note>还没有卡片</td></tr>");

  const peak = Math.max(1, ...summary.recentDaysSeries.map(([, count]) => count));
  $("s-trend").innerHTML =
    summary.recentDaysSeries
      .map(
        ([day, count]) =>
          `<tr><td class=meta>${day.slice(5)}</td><td><span class="bar" style="width:${Math.max(4, Math.round((60 * count) / peak))}px"></span> ${count}</td></tr>`
      )
      .join("") + `<tr><td colspan=2 class=note>期间共 ${summary.recentTotal} 次</td></tr>`;

  $("s-weak").innerHTML = summary.weakest.length
    ? summary.weakest.map(([name, score]) => `<span class="pill">${esc(name)} ${score.toFixed(2)}</span>`).join("") +
      "<div class=meta>交错队列会给这些主题每轮多抽一张。</div>"
    : "还没有足够复习数据判定薄弱主题 —— 先完成一轮复习。";

  $("s-reflect").innerHTML = state.reflections.length
    ? state.reflections
        .slice(0, 3)
        .map(
          (item) =>
            `<div class=meta>${new Date(item.createdAt).toLocaleString("zh-CN", { hour12: false })}</div>` +
            `<p>核心：${esc(item.coreConcept) || "—"}<br>联系：${esc(item.connection) || "—"}<br>改进：${esc(item.nextImprovement) || "—"}</p>`
        )
        .join("<hr style='border-color:var(--line)'>")
    : "还没有反思记录。一轮复习结束后会被要求写。";
}

/* --------------------------------------------------------------------- add */

function renderTopicList() {
  $("topic-list").innerHTML = state.topics.map((topic) => `<option value="${esc(topic.name)}">`).join("");
}

async function submitAdd(event) {
  event.preventDefault();
  const form = event.target;
  const values = Object.fromEntries(new FormData(form).entries());
  if (!values.question.trim() || !values.answer.trim()) {
    $("add-msg").textContent = "问题和参考答案都不能为空。";
    return;
  }
  const card = await store.addCard({
    topicName: values.topic,
    question: values.question,
    answer: values.answer,
    tags: values.tags,
    source: values.source,
    difficulty: values.difficulty,
  });
  await reload();
  renderTopicList();
  form.reset();
  form.elements.difficulty.value = "3";
  $("add-msg").textContent = `已保存 #${card.id}，今天就会出现在复习队列里。`;
  updateNavCount();
}

/* -------------------------------------------------------------------- data */

function payload() {
  return {
    format: "make-it-stick-trainer",
    version: 1,
    exported_at: new Date().toISOString(),
    source: "phone",
    topics: state.topics,
    cards: state.cards,
    reviews: state.reviews,
    reflections: state.reflections,
  };
}

async function exportFile() {
  const text = JSON.stringify(payload(), null, 2);
  const name = `mist-backup-${new Date().toISOString().slice(0, 10)}.json`;
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  $("data-msg").textContent = `已导出 ${state.cards.length} 张卡片到 ${name}`;
}

async function copyJson() {
  const text = JSON.stringify(payload());
  try {
    await navigator.clipboard.writeText(text);
    $("data-msg").textContent = "已复制完整备份到剪贴板。";
  } catch {
    $("data-msg").textContent = "浏览器拒绝了剪贴板访问，请改用「导出 JSON」。";
  }
}

async function importFile(file) {
  const text = await file.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch (error) {
    $("data-msg").textContent = `不是合法的 JSON：${error.message}`;
    return;
  }
  const shape = {
    topics: Array.isArray(data.topics) ? data.topics : [],
    cards: Array.isArray(data.cards) ? data.cards : [],
    reviews: Array.isArray(data.reviews) ? data.reviews : [],
    reflections: Array.isArray(data.reflections) ? data.reflections : [],
  };
  if (!shape.cards.length) {
    $("data-msg").textContent = "文件里没有 cards 数组，未做任何改动。";
    return;
  }
  if (!window.confirm(`将用 ${shape.cards.length} 张卡片覆盖当前手机上的全部数据，继续？`)) return;
  const counts = await store.replaceAll(shape);
  await reload();
  afterDataChange(`已导入：${counts.cards} 张卡片 / ${counts.reviews} 条复习记录`);
}

async function loadSeed() {
  const response = await fetch("seed.json");
  if (!response.ok) {
    $("add-msg").textContent = "找不到 seed.json";
    return;
  }
  const data = await response.json();
  let added = 0;
  for (const entry of data.cards ?? []) {
    const exists = state.cards.some((card) => card.question === entry.question);
    if (exists) continue;
    await store.addCard({
      topicName: entry.topic,
      question: entry.question,
      answer: entry.answer,
      tags: Array.isArray(entry.tags) ? entry.tags.join(",") : entry.tags,
      source: entry.source,
      difficulty: entry.difficulty,
    });
    added += 1;
  }
  await reload();
  afterDataChange(`已导入 ${added} 张示例卡片`);
  switchTab("review");
}

async function wipe() {
  if (!window.confirm("删除本机全部卡片与复习记录？此操作不可撤销，建议先导出。")) return;
  await store.clearAll();
  await reload();
  afterDataChange("已清空");
}

function afterDataChange(message) {
  $("add-msg").textContent = message;
  $("data-msg").textContent = message;
  renderTopicList();
  refreshQueue();
  updateNavCount();
}

/* -------------------------------------------------------------------- tabs */

function switchTab(name) {
  state.tab = name;
  document.querySelectorAll("nav button").forEach((button) => {
    button.classList.toggle("on", button.dataset.tab === name);
  });
  for (const tab of ["review", "add", "stats", "data"]) {
    $(`tab-${tab}`).classList.toggle("on", tab === name);
  }
  if (name === "stats") renderStats();
  if (name === "review") refreshQueue();
}

/* ------------------------------------------------------------------- start */

function newSessionId() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  const random = Math.random().toString(16).slice(2, 8);
  return `${stamp}-${random}`;
}

async function start() {
  try {
    await store.openDB();
  } catch (error) {
    $("queue-head").innerHTML = `<b>无法打开本地数据库</b><div class="meta">${esc(error.message)}</div>
      <div class="meta">iOS 请通过「添加到主屏幕」后从图标打开；无痕模式下浏览器会拒绝持久存储。</div>`;
    return;
  }

  await reload();
  state.sessionId = newSessionId();
  renderTopicList();
  refreshQueue();
  updateNavCount();

  document.querySelectorAll("nav button").forEach((button) => {
    button.onclick = () => switchTab(button.dataset.tab);
  });
  $("add-form").addEventListener("submit", submitAdd);
  $("load-seed").onclick = loadSeed;
  $("export-json").onclick = exportFile;
  $("copy-json").onclick = copyJson;
  $("import-btn").onclick = () => $("import-file").click();
  $("import-file").onchange = (event) => {
    const [file] = event.target.files ?? [];
    if (file) importFile(file);
    event.target.value = "";
  };
  $("wipe-btn").onclick = wipe;

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
}

start();
