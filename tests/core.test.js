import test from "node:test";
import assert from "node:assert/strict";
import { createMatcher, answerError, normalizeAnswer, cardStats, createSchedule, errorWeight,
  csv, validateBackup } from "../web/core.js";

test("逐次判定は正解の途中を保持し、不可能な打鍵ですぐに失敗する", () => {
  const m = createMatcher("か");
  assert.equal(m("k"), "pending");
  assert.equal(m("s"), "wrong");
  assert.equal(m("ku"), "wrong");
  assert.equal(m("ka"), "correct");
  assert.equal(m("ka", true), "correct");
  assert.equal(m("k", true), "wrong");
});

test("複数ローマ字・小書き・促音・長音符に対応する", () => {
  for (const [answer, inputs] of [
    ["し", ["shi", "si", "ci"]], ["ち", ["chi", "ti"]], ["つ", ["tsu", "tu"]],
    ["きゃ", ["kya", "kixya", "kilya"]], ["がっこう", ["gakkou", "gaxtukou", "galtukou"]],
    ["っつ", ["ttu", "ttsu", "xtutu"]], ["っち", ["cchi", "tti", "tchi"]],
    ["こーひー", ["ko-hi-"]], ["ふぁ", ["fa", "huxa"]], ["ゔぁ", ["va", "vuxa"]],
  ]) {
    const m = createMatcher(answer);
    for (const input of inputs) {
      assert.equal(m(input), "correct", `${answer}: ${input}`);
      for (let i = 1; i < input.length; i++) assert.notEqual(m(input.slice(0, i)), "wrong", input.slice(0, i));
    }
  }
});

test("添付単語の『ん』の分岐を保持する", () => {
  for (const [answer, inputs] of [
    ["あんか", ["anka", "annka", "an'ka"]], ["かんさ", ["kansa", "kannsa"]],
    ["さんた", ["santa", "sannta"]], ["たんな", ["tanna", "tannna"]],
    ["なんあ", ["nanna", "nan'a"]], ["あんな", ["anna", "annna"]],
    ["さかな", ["sakana"]], ["かなた", ["kanata"]], ["たんか", ["tanka", "tannka"]],
  ]) for (const input of inputs) assert.equal(createMatcher(answer)(input), "correct", `${answer}: ${input}`);
  const m = createMatcher("あんな");
  assert.equal(m("ann"), "pending");
  assert.equal(m("anna"), "correct");
  assert.equal(m("annna"), "correct");
  assert.equal(createMatcher("なんあ")("nana"), "wrong");
  assert.equal(createMatcher("あんい")("ani"), "wrong");
  assert.equal(createMatcher("あんい")("anni"), "correct");
});

test("末尾『ん』は逐次判定で早期確定せずEnterならn単独も許可する", () => {
  const m = createMatcher("かん");
  assert.equal(m("kan"), "pending");
  assert.equal(m("kann"), "correct");
  assert.equal(m("kan'"), "correct");
  assert.equal(m("kan", true), "correct");
});

test("英字の大小設定と判定対象文字", () => {
  assert.equal(createMatcher("Apple")("apple"), "correct");
  assert.equal(createMatcher("Apple", true)("apple"), "wrong");
  assert.equal(createMatcher("Apple", true)("App"), "pending");
  assert.equal(createMatcher("Apple", true)("Apple"), "correct");
  assert.equal(createMatcher("か", true)("KA"), "correct");
  for (const bad of ["", "安価", "あ a", "あa", "hello-world", "a b"]) assert.ok(answerError(bad));
  assert.equal(answerError("こーひー"), "");
  assert.equal(normalizeAnswer(" か\u3099 "), "が");
});

const cards = ["a", "b", "c"].map(id => ({ id, historyId: "current" }));
const attempt = (cardId, correct, other = {}) => ({ cardId, historyId: "current", outcome: correct ? "correct" : "wrong", correct, firstMs: 100, totalMs: 200, ...other });
test("正答時の時間を集計し、中断とリセット以前の履歴を除く", () => {
  const stats = cardStats(cards, [attempt("a", true), attempt("a", false),
    attempt("a", true, { historyId: "old" }), attempt("b", false, { outcome: "interrupted", correct: null }),
    attempt("c", true, { firstMs: null })]);
  assert.equal(stats.get("a").count, 2);
  assert.equal(stats.get("a").correct, 1);
  assert.deepEqual(stats.get("a").first, [100]);
  assert.equal(stats.get("b").count, 0);
  assert.deepEqual(stats.get("c").first, []);
  assert.deepEqual(stats.get("c").total, [200]);
});

test("重複なしは周ごとに全カードを1回ずつ出す", () => {
  const schedule = createSchedule(cards, { order: "rounds", rounds: 2 }, cardStats(cards, []), () => 0.4);
  const first = [schedule.next(), schedule.next(), schedule.next()];
  const second = [schedule.next(), schedule.next(), schedule.next()];
  assert.equal(schedule.total, 6);
  assert.equal(new Set(first.map(c => c.id)).size, 3);
  assert.equal(new Set(second.map(c => c.id)).size, 3);
  assert.equal(schedule.next(), null);
});

test("苦手優先は未学習だけを先に1回ずつ、次のセッションでも続きから扱う", () => {
  const attempts = [attempt("a", true)];
  const stats = cardStats(cards, attempts);
  const schedule = createSchedule(cards, { order: "weak", count: 4 }, stats, () => 0);
  const first = schedule.next();
  assert.ok(["b", "c"].includes(first.id));
  attempts.push(attempt(first.id, false));
  const second = schedule.next(cardStats(cards, attempts));
  assert.notEqual(first.id, second.id);
  assert.notEqual(second.id, "a");
  const resumed = createSchedule(cards, { order: "weak", count: 1 }, cardStats(cards, attempts), () => 0);
  assert.equal(resumed.next().id, second.id);
  attempts.push(attempt(second.id, true));
  assert.equal(schedule.next(cardStats(cards, attempts)).id, "a");
  assert.ok(schedule.next());
  assert.equal(schedule.next(), null);
});

test("重みは正答率を平滑化し、得意カードも消さない", () => {
  assert.equal(errorWeight({ count: 0, correct: 0 }), 0.5);
  assert.ok(errorWeight({ count: 10, correct: 0 }) > errorWeight({ count: 10, correct: 10 }));
  assert.ok(errorWeight({ count: 1000, correct: 1000 }) >= 0.05);
});

test("CSVは改行・カンマ・引用符を保持し、数式を無害化する", () => {
  const result = csv([["a,b", 'a"b', "a\nb", "=1+1", 42, null]]);
  assert.equal(result, '\uFEFF"a,b","a""b","a\nb","\'=1+1","42",""');
});

function emptyBackup() {
  return { format: "recall-speed-trainer", version: 1, decks: [], cards: [], sessions: [], attempts: [], resets: [] };
}
test("不正なバックアップは既存データの置換前に拒否する", () => {
  assert.equal(validateBackup(emptyBackup()).version, 1);
  assert.throws(() => validateBackup({ ...emptyBackup(), version: 2 }));
  assert.throws(() => validateBackup({ ...emptyBackup(), cards: [{ id: "a", deckId: "missing" }] }));
  assert.throws(() => validateBackup({ ...emptyBackup(), decks: [{ id: "a", name: "A" }, { id: "a", name: "B" }] }));
  assert.throws(() => validateBackup({ ...emptyBackup(), decks: [{ id: "a", name: "A" }],
    cards: [{ id: "a", deckId: "a", answer: "a", extra: "", historyId: "h", question: [{ type: "image", name: "x", src: "https://evil.example/x" }] }] }));
});

test("カードの有効設定を復元でき、設定のない旧バックアップも受け付ける", () => {
  const backup = { ...emptyBackup(), decks: [{ id: "deck", name: "練習" }], cards: [{
    id: "card", deckId: "deck", answer: "あ", extra: "", historyId: "history",
    question: [{ type: "text", value: "問題" }],
  }] };
  assert.equal(validateBackup(backup).cards[0].enabled, undefined);
  for (const enabled of [true, false]) {
    backup.cards[0].enabled = enabled;
    assert.equal(validateBackup(backup).cards[0].enabled, enabled);
  }
  for (const enabled of ["false", 0, null, []]) {
    backup.cards[0].enabled = enabled;
    assert.throws(() => validateBackup(backup));
  }
});

test("履歴の表示対象は全カード・有効のみ、旧バックアップでは省略可能", () => {
  const backup = { ...emptyBackup(), settings: {
    order: "random", judge: "sequential", count: 1, rounds: 1, delay: 0,
    caseSensitive: false, selectedDecks: [],
  } };
  assert.equal(validateBackup(backup).settings.historyScope, undefined);
  for (const historyScope of ["all", "enabled"]) {
    backup.settings.historyScope = historyScope;
    assert.equal(validateBackup(backup).settings.historyScope, historyScope);
  }
  for (const historyScope of ["other", null, true, 0]) {
    backup.settings.historyScope = historyScope;
    assert.throws(() => validateBackup(backup));
  }
});
