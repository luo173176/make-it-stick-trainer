/**
 * IndexedDB persistence for the phone build.
 *
 * The whole library is loaded into memory on start (a few thousand cards is
 * trivial for a phone) and individual records are written back after each
 * review, so all scheduling math happens on plain arrays in core.js.
 *
 * Ids are assigned by the app (max + 1) rather than autoIncrement so an
 * exported JSON can be re-imported on another device without id collisions.
 */

export const DB_NAME = "make-it-stick";
export const DB_VERSION = 1;
export const STORES = ["topics", "cards", "reviews", "reflections"];

let database = null;

export function openDB() {
  if (database) return Promise.resolve(database);
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      for (const name of STORES) {
        if (!db.objectStoreNames.contains(name)) {
          db.createObjectStore(name, { keyPath: "id" });
        }
      }
    };
    request.onsuccess = () => {
      database = request.result;
      database.onversionchange = () => {
        database.close();
        database = null;
      };
      resolve(database);
    };
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error("IndexedDB 被其他标签页占用，请关闭后重试"));
  });
}

function tx(storeName, mode, fn) {
  return openDB().then(
    (db) =>
      new Promise((resolve, reject) => {
        const transaction = db.transaction(storeName, mode);
        const store = transaction.objectStore(storeName);
        const result = fn(store);
        transaction.oncomplete = () => resolve(result?.__request ? result.value : result);
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      })
  );
}

function getAll(storeName) {
  return new Promise((resolve, reject) => {
    openDB().then((db) => {
      const request = db.transaction(storeName, "readonly").objectStore(storeName).getAll();
      request.onsuccess = () => resolve(request.result ?? []);
      request.onerror = () => reject(request.error);
    });
  });
}

export async function loadAll() {
  const [topics, cards, reviews, reflections] = await Promise.all(STORES.map(getAll));
  return {
    topics: topics.sort((a, b) => a.name.localeCompare(b.name)),
    cards: cards.sort((a, b) => a.id - b.id),
    reviews: reviews.sort((a, b) => a.reviewedAt - b.reviewedAt),
    reflections: reflections.sort((a, b) => b.createdAt - a.createdAt),
  };
}

export function put(storeName, record) {
  return tx(storeName, "readwrite", (store) => {
    store.put(record);
  });
}

export async function nextId(storeName) {
  const rows = await getAll(storeName);
  return rows.reduce((max, row) => Math.max(max, row.id ?? 0), 0) + 1;
}

export async function findOrCreateTopic(name) {
  const trimmed = (name || "").trim() || "未分类";
  const topics = await getAll("topics");
  const existing = topics.find((topic) => topic.name === trimmed);
  if (existing) return existing;
  const topic = { id: await nextId("topics"), name: trimmed, description: "", createdAt: Date.now() };
  await put("topics", topic);
  return topic;
}

export async function addCard({ topicName, question, answer, tags = "", source = "", difficulty = 3 }) {
  const topic = await findOrCreateTopic(topicName);
  const now = Date.now();
  const card = {
    id: await nextId("cards"),
    topicId: topic.id,
    question: question.trim(),
    answer: answer.trim(),
    source: (source || "").trim(),
    tags: (tags || "").trim(),
    difficulty: Math.min(5, Math.max(1, Number(difficulty) || 3)),
    ease: 2.5,
    interval: 0,
    repetitions: 0,
    lapses: 0,
    dueAt: now,
    lastReviewedAt: null,
    createdAt: now,
  };
  await put("cards", card);
  return card;
}

export async function addReview(review) {
  const record = { id: await nextId("reviews"), ...review };
  await put("reviews", record);
  return record;
}

export async function addReflection(reflection) {
  const filled = ["coreConcept", "connection", "nextImprovement"].some(
    (key) => (reflection[key] || "").trim()
  );
  if (!filled) return null;
  const record = { id: await nextId("reflections"), createdAt: Date.now(), ...reflection };
  await put("reflections", record);
  return record;
}

export async function replaceAll(payload) {
  const counts = { topics: 0, cards: 0, reviews: 0, reflections: 0 };
  for (const name of STORES) {
    const rows = Array.isArray(payload[name]) ? payload[name] : [];
    await tx(name, "readwrite", (store) => store.clear());
    for (const row of rows) {
      await put(name, row);
      counts[name] += 1;
    }
  }
  return counts;
}

export async function clearAll() {
  for (const name of STORES) {
    await tx(name, "readwrite", (store) => store.clear());
  }
}
