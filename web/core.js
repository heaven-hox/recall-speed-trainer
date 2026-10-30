// Pure judging, scheduling and log functions; no DOM or storage dependency.
export const RULE_VERSION = "romaji-1";

const kana = {
  あ: "a", い: "i,yi", う: "u,wu,whu", え: "e", お: "o",
  か: "ka,ca", き: "ki", く: "ku,cu,qu", け: "ke", こ: "ko,co",
  さ: "sa", し: "shi,si,ci", す: "su", せ: "se,ce", そ: "so",
  た: "ta", ち: "chi,ti", つ: "tsu,tu", て: "te", と: "to",
  な: "na", に: "ni", ぬ: "nu", ね: "ne", の: "no",
  は: "ha", ひ: "hi", ふ: "fu,hu", へ: "he", ほ: "ho",
  ま: "ma", み: "mi", む: "mu", め: "me", も: "mo",
  や: "ya", ゆ: "yu", よ: "yo", ら: "ra", り: "ri", る: "ru", れ: "re", ろ: "ro",
  わ: "wa", ゐ: "wi", ゑ: "we", を: "wo", ゔ: "vu",
  が: "ga", ぎ: "gi", ぐ: "gu", げ: "ge", ご: "go",
  ざ: "za", じ: "ji,zi", ず: "zu", ぜ: "ze", ぞ: "zo",
  だ: "da", ぢ: "di", づ: "du", で: "de", ど: "do",
  ば: "ba", び: "bi", ぶ: "bu", べ: "be", ぼ: "bo",
  ぱ: "pa", ぴ: "pi", ぷ: "pu", ぺ: "pe", ぽ: "po",
  ぁ: "xa,la", ぃ: "xi,li,xyi,lyi", ぅ: "xu,lu", ぇ: "xe,le,xye,lye", ぉ: "xo,lo",
  ゃ: "xya,lya", ゅ: "xyu,lyu", ょ: "xyo,lyo", ゎ: "xwa,lwa", ゕ: "xka,lka", ゖ: "xke,lke",
  っ: "xtu,ltu,xtsu,ltsu", ー: "-",
};
const pairs = {
  しゃ: "sha,sya", しゅ: "shu,syu", しょ: "sho,syo", しぇ: "she,sye",
  ちゃ: "cha,tya,cya", ちゅ: "chu,tyu,cyu", ちょ: "cho,tyo,cyo", ちぇ: "che,tye,cye",
  じゃ: "ja,jya,zya", じゅ: "ju,jyu,zyu", じょ: "jo,jyo,zyo", じぇ: "je,jye,zye",
  ふぁ: "fa,fwa", ふぃ: "fi,fyi,fwi", ふぇ: "fe,fye,fwe", ふぉ: "fo,fwo", ふゅ: "fyu",
  うぁ: "wha", うぃ: "wi,whi", うぇ: "we,whe", うぉ: "who",
  てぃ: "thi", てゅ: "thu", でぃ: "dhi", でゅ: "dhu", とぅ: "twu", どぅ: "dwu",
  つぁ: "tsa", つぃ: "tsi", つぇ: "tse", つぉ: "tso",
  ゔぁ: "va", ゔぃ: "vi,vyi", ゔぇ: "ve,vye", ゔぉ: "vo", ゔゅ: "vyu",
  くぁ: "qa,kwa", くぃ: "qi,qyi,kwi", くぇ: "qe,qye,kwe", くぉ: "qo,kwo", くゎ: "qwa,kwa",
  ぐぁ: "gwa", ぐぃ: "gwi", ぐぇ: "gwe", ぐぉ: "gwo", ぐゎ: "gwa",
};
for (const [base, prefixes] of Object.entries({ き: "ky", ぎ: "gy", に: "ny", ひ: "hy", び: "by", ぴ: "py", み: "my", り: "ry", ぢ: "dy" })) {
  for (const [small, vowel] of Object.entries({ ゃ: "a", ゅ: "u", ょ: "o", ぇ: "e" })) {
    pairs[base + small] = prefixes + vowel;
  }
}

export function normalizeAnswer(text) {
  return text.normalize("NFC").trim();
}

export function answerError(text) {
  if (!text) return "正解が空です。";
  if (!/^[ぁ-ゖー]+$/.test(text) && !/^[A-Za-z]+$/.test(text)) {
    return "正解はひらがな（ーを含む）または英字だけにしてください。空白・ハイフン・混在は対象外です。";
  }
  return "";
}

// A DAG keeps all possible romanizations alive without enumerating whole words.
// In particular, ann can be either あん + … or あん + the n of な.
export function createMatcher(answer, caseSensitive = false) {
  const latin = /^[A-Za-z]+$/.test(answer);
  if (answerError(answer)) throw new Error(answerError(answer));
  const graphs = new Map();
  function graph(final) {
    if (graphs.has(final)) return graphs.get(final);
    const nodes = Array.from({ length: answer.length + 1 }, () => new Map());
    const end = answer.length;
    const edge = (from, pattern, to) => {
      let current = from;
      for (let j = 0; j < pattern.length; j++) {
        const next = j === pattern.length - 1 ? to : nodes.push(new Map()) - 1;
        if (!nodes[current].has(pattern[j])) nodes[current].set(pattern[j], new Set());
        nodes[current].get(pattern[j]).add(next);
        current = next;
      }
    };
    const optionsAt = (i) => {
      const options = (kana[answer[i]] || "").split(",").filter(Boolean).map(s => [s, 1]);
      const combined = pairs[answer.slice(i, i + 2)];
      if (combined) options.push(...combined.split(",").map(s => [s, 2]));
      return options;
    };
    for (let i = 0; i < answer.length; i++) {
      if (latin) {
        edge(i, caseSensitive ? answer[i] : answer[i].toLowerCase(), i + 1);
        continue;
      }
      if (answer[i] === "ん") {
        edge(i, "nn", i + 1);
        edge(i, "n'", i + 1);
        if ((i === end - 1 && final) ||
            (i < end - 1 && !"あいうえおやゆよん".includes(answer[i + 1]))) edge(i, "n", i + 1);
      } else {
        for (const [pattern, length] of optionsAt(i)) edge(i, pattern, i + length);
        if (answer[i] === "っ" && i < end - 1) {
          for (const [pattern, length] of optionsAt(i + 1)) {
            if (/^[bcdfghjkmprstvwxyz]/.test(pattern) && !/^[xl]/.test(pattern)) {
              edge(i, pattern[0] + pattern, i + length + 1);
              if (pattern.startsWith("ch")) edge(i, "t" + pattern, i + length + 1);
            }
          }
        }
      }
    }
    const result = { nodes, end };
    graphs.set(final, result);
    return result;
  }
  return (input, final = false) => {
    const { nodes, end } = graph(final);
    let states = new Set([0]);
    const normalized = !latin || !caseSensitive ? input.toLowerCase() : input;
    for (const char of normalized) {
      const next = new Set();
      for (const state of states) for (const target of nodes[state].get(char) || []) next.add(target);
      states = next;
      if (!states.size) return "wrong";
    }
    return states.has(end) ? "correct" : final ? "wrong" : "pending";
  };
}

export function shuffled(values, random = Math.random) {
  const copy = [...values];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

export function cardStats(cards, attempts) {
  const result = new Map(cards.map(c => [c.id, { count: 0, correct: 0, first: [], total: [] }]));
  const versions = new Map(cards.map(c => [c.id, c.historyId]));
  for (const a of attempts) {
    if (a.outcome === "interrupted" || a.historyId !== versions.get(a.cardId)) continue;
    const stats = result.get(a.cardId);
    if (!stats) continue;
    stats.count++;
    if (a.correct) {
      stats.correct++;
      if (a.firstMs !== null) stats.first.push(a.firstMs);
      stats.total.push(a.totalMs);
    }
  }
  return result;
}

export const mean = numbers => numbers.length ? numbers.reduce((a, b) => a + b, 0) / numbers.length : null;
export const errorWeight = stats => Math.max(0.05, 1 - (stats.correct + 1) / (stats.count + 2));

export function weightedPick(cards, weights, random = Math.random) {
  let value = random() * weights.reduce((a, b) => a + b, 0);
  for (let i = 0; i < cards.length; i++) {
    value -= weights[i];
    if (value < 0) return cards[i];
  }
  return cards.at(-1);
}

export function createSchedule(cards, settings, stats, random = Math.random) {
  let queue = settings.order === "weak" ? shuffled(cards.filter(c => !stats.get(c.id)?.count), random) : [];
  let weights = null;
  const total = settings.order === "rounds" ? cards.length * settings.rounds : settings.count;
  let issued = 0;
  return {
    total,
    next(currentStats = stats) {
      if (issued >= total) return null;
      issued++;
      if (settings.order === "random") return cards[Math.floor(random() * cards.length)];
      if (settings.order === "rounds") {
        if (!queue.length) queue = shuffled(cards, random);
        return queue.shift();
      }
      if (queue.length) return queue.shift();
      // Freeze weights when the weighted phase starts, including the just-completed first pass.
      if (!weights) weights = cards.map(c => errorWeight(currentStats.get(c.id)));
      return weightedPick(cards, weights, random);
    },
  };
}

export function csv(rows) {
  const cell = v => '"' + String(v ?? "").replaceAll('"', '""') + '"';
  // Prefix spreadsheet formulas in imported text to avoid execution when opening a log.
  const safe = v => typeof v === "string" && /^(\s*[=+@\-]|[\t\r])/.test(v) ? "'" + v : v;
  return "\uFEFF" + rows.map(row => row.map(v => cell(safe(v))).join(",")).join("\r\n");
}

const imagePattern = /^data:image\/(jpeg|png|gif|webp);base64,[A-Za-z0-9+/]+=*$/;
export function validParts(parts) {
  return Array.isArray(parts) && parts.length > 0 && parts.every(p => p &&
    (p.type === "text" ? typeof p.value === "string" : p.type === "image" &&
      typeof p.name === "string" && typeof p.src === "string" && imagePattern.test(p.src)));
}

export function validateBackup(data) {
  const fail = () => { throw new Error("対応するバックアップJSONではないか、データに不整合があります。"); };
  const string = v => typeof v === "string";
  const number = v => typeof v === "number" && Number.isFinite(v) && v >= 0;
  if (!data || data.format !== "recall-speed-trainer" || data.version !== 1) fail();
  if (data.settings !== undefined && (!data.settings ||
    !["random", "rounds", "weak"].includes(data.settings.order) ||
    !["sequential", "enter"].includes(data.settings.judge) ||
    !Number.isInteger(data.settings.count) || data.settings.count < 1 || data.settings.count > 10000 ||
    !Number.isInteger(data.settings.rounds) || data.settings.rounds < 1 || data.settings.rounds > 1000 ||
    !Number.isInteger(data.settings.delay) || data.settings.delay < 0 || data.settings.delay > 10000 || data.settings.delay % 10 ||
    typeof data.settings.caseSensitive !== "boolean" || !Array.isArray(data.settings.selectedDecks) ||
    data.settings.selectedDecks.some(d => !string(d)) ||
    (data.settings.historyScope !== undefined && !["all", "enabled"].includes(data.settings.historyScope)))) fail();
  for (const key of ["decks", "cards", "sessions", "attempts", "resets"]) {
    if (!Array.isArray(data[key]) || data[key].some(x => !x || !string(x.id))) fail();
    if (new Set(data[key].map(x => x.id)).size !== data[key].length) fail();
  }
  const decks = new Set(data.decks.map(d => d.id));
  const cards = new Set(data.cards.map(c => c.id));
  const sessions = new Set(data.sessions.map(s => s.id));
  if (data.decks.some(d => !string(d.name))) fail();
  if (data.cards.some(c => !decks.has(c.deckId) || !validParts(c.question) ||
    !string(c.answer) || answerError(c.answer) || !string(c.extra) || !string(c.historyId) ||
    (c.enabled !== undefined && typeof c.enabled !== "boolean"))) fail();
  if (data.sessions.some(s => !string(s.startedAt) || !s.settings || !Array.isArray(s.cardIds) ||
    !["running", "completed", "stopped", "recovered"].includes(s.status))) fail();
  if (data.attempts.some(a => !cards.has(a.cardId) || !sessions.has(a.sessionId) || !string(a.historyId) ||
    !string(a.endedAt) || !string(a.answer) || !string(a.input) || !validParts(a.question) ||
    !["correct", "wrong", "interrupted"].includes(a.outcome) ||
    (a.outcome === "interrupted" ? a.correct !== null : a.correct !== (a.outcome === "correct")) ||
    !(a.firstMs === null || number(a.firstMs)) || !number(a.totalMs) ||
    !Array.isArray(a.keys) || a.keys.some(k => !string(k.key) || !number(k.atMs)))) fail();
  if (data.resets.some(r => !string(r.at) || !Array.isArray(r.cardIds) || r.cardIds.some(id => !cards.has(id)))) fail();
  return data;
}
