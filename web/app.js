import { RULE_VERSION, normalizeAnswer, answerError, createMatcher, createSchedule,
  cardStats, mean, csv, validateBackup } from "/core.js";

const $ = id => document.getElementById(id);
const stores = ["decks", "cards", "sessions", "attempts", "resets", "meta"];
const defaults = { order: "random", judge: "sequential", count: 20, rounds: 1, delay: 500, caseSensitive: false, selectedDecks: [] };
const state = { decks: [], cards: [], sessions: [], attempts: [], resets: [], settings: { ...defaults } };
let db, run = null, pendingImport = null, importBusy = false;
const id = () => crypto.randomUUID();
const now = () => new Date().toISOString();
const ms = n => n === null ? "—" : `${Math.round(n)} ms`;
const rate = (correct, count) => count ? `${(100 * correct / count).toFixed(1)}%` : "—";
const radio = name => document.querySelector(`input[name="${name}"]:checked`).value;

function notice(text, error = false) {
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
  $("message").hidden = !text;
}

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("recall-speed-trainer", 1);
    request.onupgradeneeded = () => {
      for (const name of stores) request.result.createObjectStore(name, { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error("別のタブを閉じてから再読み込みしてください。"));
  });
}

function write(changes, replace = false) {
  return new Promise((resolve, reject) => {
    const names = replace ? stores : Object.keys(changes);
    const tx = db.transaction(names, "readwrite");
    try {
      for (const name of names) {
        const store = tx.objectStore(name);
        if (replace) store.clear();
        for (const value of changes[name] || []) store.put(value);
      }
    } catch (error) {
      tx.abort(); reject(error); return;
    }
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("保存が中断されました。"));
  });
}

function readAll(name) {
  return new Promise((resolve, reject) => {
    const request = db.transaction(name).objectStore(name).getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function selectedCards() {
  const selected = new Set(state.settings.selectedDecks);
  return state.cards.filter(c => selected.has(c.deckId));
}

function readSettings() {
  const numeric = key => $(key).validity.valid ? Number($(key).value) : defaults[key];
  return { order: radio("order"), judge: radio("judge"), count: numeric("count"),
    rounds: numeric("rounds"), delay: numeric("delay"), caseSensitive: $("case-sensitive").checked,
    selectedDecks: [...$("decks").querySelectorAll("input:checked")].map(e => e.value) };
}

function applySettings() {
  for (const name of ["order", "judge"]) {
    const input = [...document.querySelectorAll(`input[name="${name}"]`)].find(e => e.value === state.settings[name]);
    if (input) input.checked = true;
  }
  for (const key of ["count", "rounds", "delay"]) $(key).value = state.settings[key];
  $("case-sensitive").checked = state.settings.caseSensitive;
  orderHelp();
}

function orderHelp() {
  const order = radio("order");
  $("count-label").hidden = order === "rounds";
  $("rounds-label").hidden = order !== "rounds";
  $("order-help").textContent = {
    random: "カードを毎回ランダムに選びます。",
    rounds: "全カードを1回ずつ出題し、周ごとに並べ替えます。",
    weak: "未学習を1回ずつ出題し、その後は低正答率のカードを優先します。",
  }[order];
}

function controls() {
  const active = !!run;
  $("stats-body").closest(".table-scroll").hidden = active;
  $("settings-fields").disabled = active || importBusy || !db;
  $("start").disabled = active || importBusy || !db || !selectedCards().length;
  $("pause").disabled = !active || ["saving", "stopping"].includes(run?.phase);
  $("pause").textContent = run?.phase === "save-error" ? "保存を再試行" : run?.phase === "paused" ? "再開" : "中断";
  $("stop").disabled = !active || ["saving", "stopping", "save-error"].includes(run?.phase);
  for (const key of ["restore-backup", "open-reset"]) $(key).disabled = active || importBusy || !db;
  for (const key of ["export-backup", "export-raw", "export-csv", "export-stats"]) $(key).disabled = !db;
}

function renderDecks() {
  const box = $("decks");
  box.replaceChildren();
  if (!state.decks.length) box.append(node("p", "デックを読み込んでください。", "muted"));
  for (const deck of state.decks) {
    const label = node("label", undefined, "deck-option");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.value = deck.id;
    check.checked = state.settings.selectedDecks.includes(deck.id);
    label.append(check, node("span", deck.name), node("small", `${state.cards.filter(c => c.deckId === deck.id).length}枚`));
    box.append(label);
  }
  renderSelected();
}

function renderSelected() {
  $("selected-count").textContent = `選択中：${selectedCards().length}枚`;
  controls();
  renderStats();
}

function renderParts(container, parts) {
  container.replaceChildren();
  for (const part of parts) {
    if (part.type === "text") container.append(document.createTextNode(part.value));
    else {
      const image = document.createElement("img");
      image.src = part.src;
      image.alt = "問題画像";
      container.append(image);
    }
  }
}

function renderStats() {
  const cards = selectedCards();
  const stats = cardStats(cards, state.attempts);
  const totals = [...stats.values()];
  const count = totals.reduce((n, s) => n + s.count, 0);
  const correct = totals.reduce((n, s) => n + s.correct, 0);
  $("metric-count").textContent = count.toLocaleString();
  $("metric-accuracy").textContent = rate(correct, count);
  $("metric-first").textContent = ms(mean(totals.flatMap(s => s.first)));
  $("metric-total").textContent = ms(mean(totals.flatMap(s => s.total)));
  const body = $("stats-body");
  body.replaceChildren();
  const decks = new Map(state.decks.map(d => [d.id, d.name]));
  for (const card of cards) {
    const s = stats.get(card.id);
    const row = node("tr");
    const question = node("td");
    renderParts(question, card.question);
    question.append(node("small", decks.get(card.deckId)));
    const answer = node("td", card.answer);
    if (card.extra) answer.append(node("small", card.extra));
    row.append(question, answer, node("td", String(s.count)), node("td", rate(s.correct, s.count)),
      node("td", ms(mean(s.first))), node("td", ms(mean(s.total))));
    body.append(row);
  }
  if (!cards.length) {
    const cell = node("td", "デックを選ぶと、カードごとの成績を表示します。", "muted");
    cell.colSpan = 6;
    const row = node("tr"); row.append(cell); body.append(row);
  }
}

async function settingsChanged() {
  if (run) return;
  state.settings = readSettings();
  orderHelp();
  renderSelected();
  try { await write({ meta: [{ id: "settings", value: state.settings }] }); }
  catch { notice("設定を保存できません。ブラウザの保存領域を確認してください。", true); }
}

// Import only inert, parsed fields. No imported HTML is assigned to innerHTML.
function buildImport() {
  const mappings = new Map();
  for (const group of $("field-mappings").children) {
    mappings.set(group.dataset.model, Object.fromEntries([...group.querySelectorAll("select")].map(e => [e.name, Number(e.value)])));
  }
  const accepted = [], rejected = [], seen = new Set();
  for (const source of pendingImport.cards) {
    const mapping = mappings.get(source.noteTypeId);
    const model = pendingImport.noteTypes.find(n => n.id === source.noteTypeId);
    const q = source.fields[mapping.question], a = source.fields[mapping.answer];
    const extra = source.fields[mapping.extra];
    const answer = normalizeAnswer(a.text);
    const warnings = [...new Set([...q.warnings, ...a.warnings, ...(extra?.warnings || [])])];
    let error = answerError(answer);
    const parts = q.parts.map(p => p.type === "image" ? { ...p, src: pendingImport.media[p.name] } : { ...p });
    if (a.parts.some(p => p.type === "image")) error = "正解に画像が含まれています。";
    if (!q.text && !parts.some(p => p.type === "image")) error = "問題が空です。";
    if (parts.some(p => p.type === "image" && !p.src)) error = "問題画像が欠落しているか、未対応の画像形式です。";
    const cardId = source.id + ":" + encodeURIComponent(model.fields[mapping.question] + "|" + model.fields[mapping.answer]);
    if (seen.has(cardId)) error = "重複するカードIDです。";
    seen.add(cardId);
    const card = { id: cardId, deckId: source.deckId, question: parts, answer, extra: extra?.text || "",
      mapping: { question: model.fields[mapping.question], answer: model.fields[mapping.answer], extra: model.fields[mapping.extra] || null },
      sourceId: source.sourceId, sourceFile: pendingImport.filename, ruleVersion: RULE_VERSION };
    (error ? rejected : accepted).push({ card, error, warnings });
  }
  return { accepted, rejected };
}

function previewImport() {
  const { accepted, rejected } = buildImport();
  $("import-count").textContent = `${accepted.length}枚を取り込み可能 ／ ${rejected.length}枚を除外`;
  $("confirm-import").disabled = !accepted.length;
  const body = $("import-preview"); body.replaceChildren();
  for (const { card, error, warnings } of [...accepted, ...rejected].slice(0, 30)) {
    const row = node("tr");
    const question = node("td");
    renderParts(question, card.question.filter(p => p.type !== "image" || p.src));
    row.append(question, node("td", card.answer), node("td", card.extra), node("td", error || warnings.join(" ") || "OK"));
    body.append(row);
  }
  $("import-errors").hidden = !rejected.length;
  const list = $("import-errors").querySelector("ul"); list.replaceChildren();
  for (const { card, error } of rejected) list.append(node("li", `${card.sourceId} / ${card.answer || "（空欄）"}：${error}`));
}

async function loadDeck(file) {
  if (!file || run) return;
  importBusy = true; controls(); notice("デックを読み込んでいます…");
  try {
    if (file.size > 50 * 1024 * 1024) throw new Error("ファイルは50MiB以下にしてください。");
    const response = await fetch("/api/import?name=" + encodeURIComponent(file.name), {
      method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    pendingImport = { ...data, filename: file.name };
    $("import-source").textContent = `${file.name} ／ ${data.cards.length}カード`;
    const box = $("field-mappings"); box.replaceChildren();
    for (const model of data.noteTypes) {
      const group = node("div", undefined, "mapping"); group.dataset.model = model.id;
      group.append(node("h3", `ノートタイプ：${model.name}`));
      const grid = node("div", undefined, "mapping-grid");
      for (const [key, labelText, preferred, fallback] of [
        ["question", "問題", ["表面", "Front", "question"], 0],
        ["answer", "正解", ["裏面", "Back", "answer"], Math.min(1, model.fields.length - 1)],
        ["extra", "補足（任意）", ["単語", "表記", "extra"], -1],
      ]) {
        const label = node("label", labelText), select = document.createElement("select"); select.name = key;
        if (key === "extra") select.add(new Option("使用しない", "-1"));
        model.fields.forEach((field, i) => select.add(new Option(field, String(i))));
        const match = model.fields.findIndex(f => preferred.includes(f));
        select.value = String(match >= 0 ? match : fallback);
        select.addEventListener("change", previewImport);
        label.append(select); grid.append(label);
      }
      group.append(grid); box.append(group);
    }
    previewImport(); notice(""); $("import-dialog").showModal();
  } catch (error) { notice(error.message, true); }
  finally { importBusy = false; controls(); $("deck-file").value = ""; }
}

async function confirmImport() {
  const { accepted, rejected } = buildImport();
  if (!accepted.length) return;
  $("confirm-import").disabled = true;
  try {
    const at = now(), changedIds = [];
    const existing = new Map(state.cards.map(c => [c.id, c]));
    const cards = accepted.map(({ card }) => {
      const old = existing.get(card.id);
      const changed = old && (old.answer !== card.answer || JSON.stringify(old.question) !== JSON.stringify(card.question));
      if (changed) changedIds.push(card.id);
      return { ...card, historyId: old && !changed ? old.historyId : id(), importedAt: at };
    });
    const deckIds = new Set(cards.map(c => c.deckId));
    const decks = pendingImport.decks.filter(d => deckIds.has(d.id));
    const resets = changedIds.length ? [{ id: id(), at, cardIds: changedIds, reason: "card-changed" }] : [];
    const settings = { ...state.settings, selectedDecks: [...new Set([...state.settings.selectedDecks, ...deckIds])] };
    await write({ cards, decks, resets, meta: [{ id: "settings", value: settings }] });
    for (const c of cards) existing.set(c.id, c);
    state.cards = [...existing.values()];
    const deckMap = new Map(state.decks.map(d => [d.id, d])); decks.forEach(d => deckMap.set(d.id, d));
    state.decks = [...deckMap.values()]; state.settings = settings; state.resets.push(...resets);
    $("import-dialog").close(); pendingImport = null;
    renderDecks();
    notice(`${cards.length}枚を取り込みました。除外：${rejected.length}枚。${changedIds.length ? `内容が変わった${changedIds.length}枚の成績をリセットしました。` : ""}`);
  } catch (error) { notice("取り込みを保存できませんでした：" + error.message, true); }
  finally { $("confirm-import").disabled = false; }
}

function resultContents(a) {
  const fragment = document.createDocumentFragment();
  fragment.append(node("strong", a.correct ? "○ 正解" : "× 不正解", a.correct ? "result-correct" : "result-wrong"));
  fragment.append(node("span", `正解：${a.answer}${a.extra ? `（${a.extra}）` : ""}`));
  fragment.append(node("span", `初動 ${ms(a.firstMs)} ／ 合計 ${ms(a.totalMs)}`, "result-times"));
  return fragment;
}

function showPrevious(a) {
  const box = $("previous"), question = node("div", undefined, "previous-question");
  renderParts(question, a.question);
  box.replaceChildren(question, resultContents(a), node("span", `入力：${a.input || "（空欄）"}`));
}

function resetTrainingDisplay() {
  $("training-title").textContent = "準備できたら、始めましょう";
  $("progress").textContent = "— / —"; $("progress-bar").value = 0;
  $("question").style.visibility = "visible";
  $("question").replaceChildren(node("p", "出題対象を選び、学習を開始してください。", "muted"));
  $("answer").value = ""; $("answer").disabled = true;
  $("feedback").className = "feedback";
  $("feedback").replaceChildren(node("span", "判定結果はここに表示されます", "muted"));
  $("session-summary").hidden = true;
  const last = state.attempts.filter(a => a.outcome !== "interrupted").sort((a, b) => a.endedAt.localeCompare(b.endedAt)).at(-1);
  if (last) showPrevious(last);
  else $("previous").replaceChildren(node("p", "まだ回答がありません。", "muted"));
}

function makeAttempt(r, outcome) {
  const elapsed = performance.now() - r.t0;
  return { id: id(), sessionId: r.session.id, cardId: r.current.id, historyId: r.current.historyId,
    ordinal: r.index, trial: r.trial, deckName: state.decks.find(d => d.id === r.current.deckId)?.name || "",
    question: r.current.question, answer: r.current.answer, extra: r.current.extra, input: $("answer").value,
    correct: outcome === "interrupted" ? null : outcome === "correct", outcome,
    startedAt: r.startedAt, endedAt: now(), firstMs: r.firstMs, totalMs: Math.max(0, elapsed),
    keys: [...r.keys], ruleVersion: RULE_VERSION };
}

async function presentCard(r, card) {
  r.phase = "loading"; r.current = card; r.trial++; r.pauseRequested = false;
  const token = ++r.renderToken;
  controls();
  $("training-title").textContent = state.decks.find(d => d.id === card.deckId)?.name || "学習中";
  $("progress").textContent = `${r.index} / ${r.schedule.total}`;
  $("progress-bar").max = r.schedule.total; $("progress-bar").value = r.session.answered;
  $("answer").value = ""; $("answer").disabled = true;
  $("input-hint").textContent = r.session.settings.judge === "enter" ? "入力後にEnterで判定" : "入力するたびに判定";
  $("feedback").className = "feedback";
  $("feedback").replaceChildren(node("span", "回答を入力してください", "muted"));
  $("question").style.visibility = "hidden";
  renderParts($("question"), card.question);
  try {
    await Promise.all([...$("question").querySelectorAll("img")].map(image => image.decode()));
    await new Promise(resolve => requestAnimationFrame(resolve));
    if (run !== r || r.phase !== "loading" || token !== r.renderToken) return;
    $("question").style.visibility = "visible";
    r.matcher = createMatcher(card.answer, r.session.settings.caseSensitive);
    r.keys = []; r.firstMs = null; r.t0 = performance.now(); r.startedAt = now();
    r.phase = "answering"; $("answer").disabled = false; $("answer").focus(); controls();
  } catch {
    if (run !== r || token !== r.renderToken) return;
    r.phase = "paused"; r.resumeAction = "current";
    notice("問題画像を表示できません。デックを確認してください。再開で再試行できます。", true); controls();
  }
}

async function nextQuestion(r) {
  if (run !== r) return;
  const card = r.schedule.next(cardStats(r.cards, state.attempts));
  if (!card) return endSession("completed");
  r.index++;
  await presentCard(r, card);
}

async function startSession() {
  if (run || !db) return;
  const settings = readSettings();
  const fields = [$("delay"), settings.order === "rounds" ? $("rounds") : $("count")];
  if (fields.some(input => !input.reportValidity())) return;
  state.settings = settings;
  const cards = selectedCards();
  if (!cards.length) return notice("出題対象を選択してください。", true);
  const schedule = createSchedule(cards, settings, cardStats(cards, state.attempts));
  if (schedule.total > 100000) return notice("1セッションは100,000問以下にしてください。", true);
  const session = { id: id(), startedAt: now(), endedAt: null, status: "running", settings: { ...settings },
    cardIds: cards.map(c => c.id), total: schedule.total, answered: 0, ruleVersion: RULE_VERSION };
  const r = { session, cards, schedule, phase: "saving", index: 0, trial: 0, renderToken: 0, timer: null };
  run = r; controls(); notice(""); $("session-summary").hidden = true;
  try {
    await write({ sessions: [session], meta: [{ id: "settings", value: settings }] });
    state.sessions.push(session);
    if (r.pauseRequested) { r.phase = "paused"; r.resumeAction = "next"; controls(); }
    else await nextQuestion(r);
  } catch (error) { run = null; controls(); notice("学習を開始できません：" + error.message, true); }
}

function hold(r, action) {
  r.phase = "paused"; r.resumeAction = action; r.renderToken++;
  $("answer").disabled = true;
  $("training-title").textContent = "中断中 — 再開すると出題を再開します";
  $("question").style.visibility = "hidden";
  controls();
}

async function saveAttempt(r, attempt, action) {
  r.phase = "saving"; controls();
  try {
    await write({ attempts: [attempt], sessions: [r.session] });
    $("saved-status").textContent = "ブラウザに保存済み";
    renderStats();
    if (action === "pause") { hold(r, "current"); return; }
    $("progress-bar").value = r.session.answered;
    if (r.pauseRequested) { hold(r, "next"); return; }
    const delay = Math.max(0, r.feedbackDue - performance.now());
    if (!delay) return nextQuestion(r);
    r.phase = "feedback";
    r.timer = setTimeout(() => { nextQuestion(r).catch(reportError); }, delay);
    controls();
  } catch (error) {
    r.phase = "save-error"; r.pendingSave = { attempt, action };
    $("saved-status").textContent = "保存失敗";
    notice("回答を保存できません。「保存を再試行」か、バックアップJSONの書き出しを行ってください：" + error.message, true);
    controls();
  }
}

async function judge(outcome) {
  const r = run;
  if (!r || r.phase !== "answering") return;
  const attempt = makeAttempt(r, outcome);
  r.phase = "saving"; $("answer").disabled = true;
  r.feedbackDue = performance.now() + r.session.settings.delay;
  showPrevious(attempt);
  if (r.session.settings.delay) {
    $("feedback").className = `feedback ${attempt.correct ? "correct" : "wrong"}`;
    $("feedback").replaceChildren(resultContents(attempt));
  }
  r.session.answered++; state.attempts.push(attempt);
  await saveAttempt(r, attempt, "next");
}

async function pauseSession() {
  const r = run;
  if (!r || ["paused", "stopping", "save-error"].includes(r.phase)) return;
  if (r.phase === "saving") { r.pauseRequested = true; return; }
  clearTimeout(r.timer);
  if (r.phase === "answering") {
    const attempt = makeAttempt(r, "interrupted");
    $("answer").disabled = true; state.attempts.push(attempt);
    await saveAttempt(r, attempt, "pause");
  } else hold(r, r.phase === "feedback" ? "next" : "current");
}

async function togglePause() {
  const r = run;
  if (!r) return;
  if (r.phase === "save-error") {
    notice(""); return saveAttempt(r, r.pendingSave.attempt, r.pendingSave.action);
  }
  if (r.phase !== "paused") return pauseSession();
  if (r.resumeAction === "end") return endSession(r.session.status);
  if (r.resumeAction === "next") return nextQuestion(r);
  return presentCard(r, r.current);
}

async function endSession(status = "stopped") {
  const r = run;
  if (!r || ["stopping", "save-error"].includes(r.phase)) return;
  clearTimeout(r.timer); r.renderToken++;
  const interrupted = r.phase === "answering" ? makeAttempt(r, "interrupted") : null;
  r.phase = "stopping"; $("answer").disabled = true; controls();
  r.session.status = status; r.session.endedAt = now();
  if (interrupted) state.attempts.push(interrupted);
  try {
    await write({ sessions: [r.session], attempts: interrupted ? [interrupted] : [] });
  } catch (error) {
    r.phase = "paused"; r.resumeAction = "end";
    notice("終了状態を保存できません。バックアップを書き出してください：" + error.message, true);
    controls(); return;
  }
  const answers = state.attempts.filter(a => a.sessionId === r.session.id && a.outcome !== "interrupted");
  const correct = answers.filter(a => a.correct);
  $("session-summary").textContent = `${status === "completed" ? "学習完了" : "途中終了"}：${answers.length}問 ／ 正答率 ${rate(correct.length, answers.length)} ／ 正答時の平均初動 ${ms(mean(correct.map(a => a.firstMs).filter(n => n !== null)))} ／ 平均合計 ${ms(mean(correct.map(a => a.totalMs)))}`;
  $("session-summary").hidden = false;
  $("training-title").textContent = status === "completed" ? "学習が完了しました" : "学習を終了しました";
  $("question").style.visibility = "visible";
  $("question").replaceChildren(node("p", "おつかれさまでした", "muted"));
  $("feedback").className = "feedback";
  $("feedback").replaceChildren(node("span", "結果は学習履歴に保存されています", "muted"));
  run = null; renderStats(); controls();
}

function onKey(event) {
  const r = run;
  if (!r || r.phase !== "answering") return;
  const elapsed = Math.max(0, performance.now() - r.t0);
  r.keys.push({ key: event.key, code: event.code, atMs: elapsed, repeat: event.repeat,
    ctrl: event.ctrlKey, alt: event.altKey, shift: event.shiftKey, meta: event.metaKey });
  if (event.repeat) { event.preventDefault(); return; }
  const editing = (event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey) || ["Backspace", "Delete"].includes(event.key);
  if (editing && r.firstMs === null) r.firstMs = elapsed;
  if (event.key === "Enter") {
    event.preventDefault();
    if (r.session.settings.judge === "enter") judge(r.matcher($("answer").value, true) === "correct" ? "correct" : "wrong").catch(reportError);
  }
}

function onInput() {
  const r = run;
  if (!r || r.phase !== "answering" || r.session.settings.judge !== "sequential") return;
  const outcome = r.matcher($("answer").value);
  if (outcome !== "pending") judge(outcome).catch(reportError);
}

function download(filename, contents, type) {
  const url = URL.createObjectURL(new Blob([contents], { type }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename;
  anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function backup() {
  return { format: "recall-speed-trainer", version: 1, exportedAt: now(), settings: state.settings,
    ...Object.fromEntries(stores.filter(s => s !== "meta").map(s => [s, state[s]])) };
}

const fileStamp = () => now().replaceAll(":", "-").replaceAll(".", "-");
function exportBackup() { download(`recall-backup-${fileStamp()}.json`, JSON.stringify(backup()), "application/json"); }
function logQuestion(parts) { return parts.map(p => p.type === "text" ? p.value : `[画像:${p.name}]`).join(""); }

function exportRaw() {
  const attempts = state.attempts.map(a => ({ ...a, question: a.question.map(p => p.type === "image" ? { type: "image", mediaId: p.name } : p) }));
  download(`recall-raw-${fileStamp()}.json`, JSON.stringify({ format: "recall-speed-trainer-raw", version: 1,
    exportedAt: now(), sessions: state.sessions, attempts, resets: state.resets }), "application/json");
}

function exportAnswers() {
  const rows = [["timestamp", "session_id", "ordinal", "trial", "card_id", "deck", "question", "answer", "extra", "input", "outcome", "correct", "first_ms", "total_ms"]];
  for (const a of state.attempts) rows.push([a.endedAt, a.sessionId, a.ordinal, a.trial, a.cardId, a.deckName,
    logQuestion(a.question), a.answer, a.extra, a.input, a.outcome, a.correct, a.firstMs, a.totalMs]);
  download(`recall-answers-${fileStamp()}.csv`, csv(rows), "text/csv");
}

function exportStats() {
  const at = now(), stats = cardStats(state.cards, state.attempts);
  const rows = [["timestamp", "card_id", "deck", "question", "answer", "extra", "count", "correct", "accuracy", "correct_first_mean_ms", "correct_total_mean_ms", "history_id"]];
  for (const c of state.cards) {
    const s = stats.get(c.id);
    rows.push([at, c.id, state.decks.find(d => d.id === c.deckId)?.name, logQuestion(c.question), c.answer,
      c.extra, s.count, s.correct, s.count ? s.correct / s.count : null, mean(s.first), mean(s.total), c.historyId]);
  }
  download(`recall-stats-${fileStamp()}.csv`, csv(rows), "text/csv");
}

async function restoreBackup(file) {
  if (!file || run) return;
  try {
    if (file.size > 200 * 1024 * 1024) throw new Error("バックアップは200MiB以下にしてください。");
    const data = validateBackup(JSON.parse(await file.text()));
    if (!confirm(`現在の保存データを、このバックアップ（${data.cards.length}枚・${data.attempts.length}試行）の内容で置き換えます。現在のデータは書き出しましたか？`)) return;
    const settings = { ...defaults, ...data.settings };
    settings.selectedDecks = Array.isArray(settings.selectedDecks) ? settings.selectedDecks.filter(d => data.decks.some(x => x.id === d)) : [];
    const recovered = data.sessions.map(s => s.status === "running" ? { ...s, status: "recovered", endedAt: now() } : s);
    await write({ ...data, sessions: recovered, meta: [{ id: "settings", value: settings }] }, true);
    for (const key of stores.filter(k => k !== "meta")) state[key] = key === "sessions" ? recovered : data[key];
    state.settings = settings; applySettings(); renderDecks(); resetTrainingDisplay();
    notice("バックアップを読み込みました。");
  } catch (error) { notice("バックアップを読み込めませんでした：" + error.message, true); }
  finally { $("backup-file").value = ""; }
}

async function resetHistory() {
  if (run) return;
  const cards = radio("reset-scope") === "all" ? state.cards : selectedCards();
  if (!cards.length) return notice("リセット対象のカードがありません。", true);
  const changed = cards.map(c => ({ ...c, historyId: id() }));
  const reset = { id: id(), at: now(), cardIds: cards.map(c => c.id), reason: "manual" };
  try {
    await write({ cards: changed, resets: [reset] });
    const updates = new Map(changed.map(c => [c.id, c]));
    state.cards = state.cards.map(c => updates.get(c.id) || c); state.resets.push(reset);
    $("reset-dialog").close(); renderStats();
    notice(`${cards.length}枚の成績をリセットしました。過去の生ログは残っています。`);
  } catch (error) { notice("リセットを保存できません：" + error.message, true); }
}

function reportError(error) { notice(error.message || String(error), true); }

$("settings-fields").addEventListener("change", () => settingsChanged().catch(reportError));
$("open-import").addEventListener("click", () => $("deck-file").click());
$("deck-file").addEventListener("change", event => loadDeck(event.target.files[0]));
for (const key of ["close-import", "cancel-import"]) $(key).addEventListener("click", () => $("import-dialog").close());
$("confirm-import").addEventListener("click", confirmImport);
$("start").addEventListener("click", () => startSession().catch(reportError));
$("pause").addEventListener("click", () => togglePause().catch(reportError));
$("stop").addEventListener("click", () => endSession().catch(reportError));
$("answer").addEventListener("keydown", onKey);
$("answer").addEventListener("input", onInput);
for (const type of ["paste", "drop"]) $("answer").addEventListener(type, event => {
  event.preventDefault(); notice("計測のため、貼り付けではなくキーボードで入力してください。");
});
$("answer").addEventListener("compositionstart", () => {
  notice("IMEをオフにしてから再開してください。"); pauseSession().catch(reportError);
});
window.addEventListener("blur", () => pauseSession().catch(reportError));
document.addEventListener("visibilitychange", () => { if (document.hidden) pauseSession().catch(reportError); });
window.addEventListener("beforeunload", event => { if (run) { event.preventDefault(); event.returnValue = ""; } });
$("export-backup").addEventListener("click", exportBackup);
$("export-raw").addEventListener("click", exportRaw);
$("export-csv").addEventListener("click", exportAnswers);
$("export-stats").addEventListener("click", exportStats);
$("restore-backup").addEventListener("click", () => $("backup-file").click());
$("backup-file").addEventListener("change", event => restoreBackup(event.target.files[0]));
$("open-reset").addEventListener("click", () => $("reset-dialog").showModal());
$("cancel-reset").addEventListener("click", () => $("reset-dialog").close());
$("reset-backup").addEventListener("click", exportBackup);
$("confirm-reset").addEventListener("click", resetHistory);

async function init() {
  try {
    db = await openDatabase();
    const values = await Promise.all(stores.map(readAll));
    stores.forEach((name, i) => { if (name !== "meta") state[name] = values[i]; });
    state.settings = { ...defaults, ...(values.at(-1).find(v => v.id === "settings")?.value || {}) };
    const recovered = state.sessions.filter(s => s.status === "running");
    recovered.forEach(s => { s.status = "recovered"; s.endedAt = now(); });
    if (recovered.length) await write({ sessions: recovered });
    db.onversionchange = () => { db.close(); notice("保存領域が更新されました。このタブを再読み込みしてください。", true); };
    applySettings(); renderDecks();
    if (state.attempts.length) resetTrainingDisplay();
    $("saved-status").textContent = "ブラウザに保存済み";
    if (recovered.length) notice("前回の学習は途中で終了しました。保存済みの回答から再開できます。");
  } catch (error) {
    db = null; $("saved-status").textContent = "保存領域を利用できません";
    notice("ローカル保存を開始できません。ブラウザの保存許可を確認してください：" + error.message, true);
  }
  controls();
}
init();
