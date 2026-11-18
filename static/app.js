// Imbul Awards: the ballot for everyone who may vote, and the admin views (totals, every vote, the guessing
// leaderboard, access). All requests go to the API next to this page (/api/...).
"use strict";

// The answer offered in every category next to the nominees, for those who would rather not guess
const NO_IDEA = -1;
const NO_IDEA_LABEL = "🤷 Ötletem sincs, ember";
const KIND_LABELS = { predict: "🔮 Szerinted ki nyeri?", pick: "❤️ Te kinek adnád?" };
const QUESTIONS = { predict: "🔮 Szerinted ki fogja megkapni a díjat?", pick: "❤️ Te kinek adnád a díjat?" };
const LOGIN_ERRORS = {
  "nincs-jog": "Beléptél, de még nincs jogod szavazni. Szólj yaginak, és beenged.",
  sikertelen: "A Discord belépés nem sikerült, próbáld újra.",
  beallitas: "A Discord belépés még nincs beállítva a szerveren.",
};

const $ = (id) => document.getElementById(id);

// Small DOM helper: h("div", { class: "x", onclick: fn }, "text", child)
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    // Through the CSSOM: the Content-Security-Policy blocks inline style attributes
    else if (key === "style") el.style.cssText = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) el.append(child);
  }
  return el;
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: {}, credentials: "same-origin" };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, init);
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error((data && data.error) || `Hiba (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return data;
}

const dateTime = new Intl.DateTimeFormat("hu-HU", {
  timeZone: "Europe/Budapest", year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit",
});

function personEl(person, sub) {
  const avatar = person.avatarUrl
    ? h("img", { src: person.avatarUrl, alt: "", loading: "lazy" })
    : h("span", { class: "no-avatar" }, (person.name || "?").slice(0, 1).toUpperCase());
  return h("span", { class: "person" }, avatar,
    h("span", { class: "names" }, h("span", { class: "name" }, person.name),
      h("span", { class: "handle" }, sub !== undefined ? sub : (person.username ? `@${person.username}` : person.id))));
}

// --- State

const state = { me: null, ballot: null, votes: {}, summary: null, access: null };

// --- Ballot: one category per page. Once both questions of a page are answered, the next unanswered category
// slides in by itself; the arrows, the dots, ← / → and the address (#goty) move between pages by hand.

const AUTO_ADVANCE_MS = 750;
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const ballotUi = { index: 0, done: false, advanceTimer: null, animating: false };

// After the deadline the ballot turns into "Díjazottak": the same pages, nobody can vote, every card is black and
// white; in a category the admin has shown, the most voted nominees (per question) are in colour.
const RESULTS_POLL_MS = 20_000;
const RESULT_QUESTIONS = { predict: "🔮 Szerintetek ki fogja megkapni a díjat?", pick: "❤️ Ti kinek adnátok a díjat?" };

function resultsMode() {
  return Boolean(state.ballot && state.ballot.closed);
}

function resultOf(category) {
  return (state.ballot.results || {})[category.id] || null;
}

// Who really won the category at The Game Awards (set by the admin), or undefined
function actualOf(category) {
  return (state.ballot.actual || {})[category.id];
}

function voteCount() {
  return Object.values(state.votes).reduce((sum, kinds) => sum + Object.keys(kinds).length, 0);
}

function isComplete(category) {
  const votes = state.votes[category.id] || {};
  return votes.predict !== undefined && votes.pick !== undefined;
}

function firstOpenIndex(from = 0) {
  const categories = state.ballot.categories;
  for (let step = 0; step < categories.length; step++) {
    const index = (from + step) % categories.length;
    if (!isComplete(categories[index])) return index;
  }
  return -1;
}

function renderDeadline() {
  const { closesAt, closed, ceremony } = state.ballot;
  const when = dateTime.format(new Date(closesAt));
  $("deadline").textContent = closed
    ? `A szavazás lezárult (${when}). Gála: ${ceremony}.`
    : `Szavazni ${when}-ig lehet · Gála: ${ceremony}`;
}

function renderNav() {
  const categories = state.ballot.categories;
  const results = resultsMode();
  // Gold dot: a category done (voting), or its result shown (Díjazottak)
  const isDone = (category) => (results ? Boolean(resultOf(category)) : isComplete(category));
  const done = categories.filter(isDone).length;
  $("step").textContent = ballotUi.done ? "Összegzés" : `${ballotUi.index + 1} / ${categories.length}`;
  $("done-count").textContent = results
    ? `${done} / ${categories.length} kategória díjazottja kint`
    : `${done} / ${categories.length} kategória kész`;
  $("progress-bar").style.width = `${(done / categories.length) * 100}%`;
  $("prev").disabled = !ballotUi.done && ballotUi.index === 0;
  $("next").disabled = ballotUi.done;
  $("dots").replaceChildren(...categories.map((category, index) => h("button", {
    type: "button",
    class: `dot${isDone(category) ? " complete" : ""}${!ballotUi.done && index === ballotUi.index ? " current" : ""}`,
    title: `${index + 1}. ${category.name}`,
    "aria-label": `${index + 1}. ${category.name}${isDone(category) ? (results ? " (díjazott kint)" : " (kész)") : ""}`,
    onclick: () => goTo(index),
  })));
}

function initials(name) {
  return name.replace(/[^\p{L}\p{N} ]/gu, "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}

function artEl(nominee, index) {
  if (index === NO_IDEA) return h("span", { class: "art placeholder shrug" }, "🤷");
  if (nominee.image) {
    const img = h("img", { src: nominee.image, alt: "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
    // A picture that does not load becomes the placeholder
    img.addEventListener("error", () => img.replaceWith(h("span", { class: "art placeholder" }, initials(nominee.name))));
    // Logos (rendered from an SVG) are often transparent with dark text: shown whole, on a light background
    const art = h("span", { class: /\.svg\//i.test(nominee.image) ? "art logo" : "art" }, img);
    // A landscape picture (a Steam header) would lose its sides in the portrait card: shown whole, over a blurred copy
    img.addEventListener("load", () => {
      if (art.classList.contains("logo") || img.naturalWidth < img.naturalHeight * 1.2) return;
      art.style.setProperty("--art", `url("${nominee.image}")`);
      art.classList.add("wide");
    });
    return art;
  }
  return h("span", { class: "art placeholder" }, initials(nominee.name));
}

function nomineeCard(category, kind, index, nominee) {
  const chosen = state.votes[category.id] && state.votes[category.id][kind] === index;
  return h("button", {
    type: "button",
    class: `card${index === NO_IDEA ? " no-idea" : ""}`,
    "aria-pressed": chosen ? "true" : "false",
    disabled: state.ballot.closed,
    "data-kind": kind,
    "data-index": String(index),
    // A vote can be changed, but not taken back: "Ötletem sincs, ember" is the way not to choose
    onclick: () => { if (!chosen) vote(category.id, kind, index); },
  }, artEl(nominee, index), h("span", { class: "check", "aria-hidden": "true" }, "✓"),
  h("span", { class: "card-text" }, h("span", { class: "card-name" }, nominee.name),
    nominee.detail ? h("span", { class: "card-detail" }, nominee.detail) : null));
}

function resultCard(category, kind, index, nominee) {
  const result = resultOf(category);
  const winner = Boolean(result && result[kind].winners.includes(index));
  const mine = state.votes[category.id] && state.votes[category.id][kind] === index;
  // The real winner is marked in the "who will win" row, where it settles the tips
  const real = kind === "predict" && actualOf(category) === index;
  const mineText = kind === "predict" ? (real ? "a te tipped · eltaláltad! 🎯" : "a te tipped") : "a te szavazatod";
  const art = artEl(nominee, index);
  if (real) art.append(h("span", { class: "real-badge" }, "⭐ A gálán nyert"));  // at the bottom of the picture
  return h("div", { class: `card result${winner ? " winner" : ""}${real ? " real" : ""}${mine ? " mine" : ""}`, "data-kind": kind },
    art,
    winner ? h("span", { class: "ribbon" }, kind === "predict" ? "🔮 Imbul tippje" : "🏆 Imbul-díj") : null,
    h("span", { class: "card-text" }, h("span", { class: "card-name" }, nominee.name),
      nominee.detail ? h("span", { class: "card-detail" }, nominee.detail) : null,
      mine ? h("span", { class: `mine-tag${real ? " hit" : ""}` }, mineText) : null));
}

function resultsPage(index) {
  const category = state.ballot.categories[index];
  const result = resultOf(category);
  const myNoIdea = (kind) => (state.votes[category.id] || {})[kind] === NO_IDEA;
  return h("article", { class: `page results${result ? " revealed" : ""}`, "data-category": category.id },
    h("header", { class: "page-head" },
      h("span", { class: "eyebrow" }, `${index + 1}. kategória · ${category.original}`),
      h("h2", {}, category.name)),
    ...["predict", "pick"].map((kind) => h("section", { class: `question ${kind}` },
      h("h3", {}, RESULT_QUESTIONS[kind],
        result && result[kind].votes
          ? h("span", { class: "result-votes" }, ` · ${result[kind].votes} szavazattal${result[kind].winners.length > 1 ? ", holtverseny" : ""}`)
          : null),
      h("div", { class: "cards", style: `--count: ${category.nominees.length}` },
        ...category.nominees.map((nominee, i) => resultCard(category, kind, i, nominee))),
      myNoIdea(kind) ? h("p", { class: "muted small-text" }, "Itt azt választottad: 🤷 Ötletem sincs, ember") : null,
      result && !result[kind].winners.length ? h("p", { class: "muted small-text" }, "Erre senki nem szavazott.") : null)),
    legend(),
    h("p", { class: "page-hint" }, result
      ? "Kint az Imbul eredménye: színes, arany keretben."
      : "Az Imbul eredménye a gála alatt derül ki: amint kint van, itt színesben látod."));
}

// The key under the cards: the same badges as on the pictures, each with what it means
function legend() {
  const item = (cls, badge, text) => h("li", {}, h("span", { class: `legend-badge ${cls}` }, badge), h("span", {}, text));
  return h("ul", { class: "legend" },
    item("real", "⭐ A gálán nyert", "a The Game Awards valódi díjazottja"),
    item("imbul", "🔮 Imbul tippje", "erre tippeltetek a legtöbben"),
    item("imbul", "🏆 Imbul-díj", "ti ennek adtátok a legtöbben"));
}

function categoryPage(index) {
  if (resultsMode()) return resultsPage(index);
  const category = state.ballot.categories[index];
  return h("article", { class: `page${isComplete(category) ? " complete" : ""}`, "data-category": category.id },
    h("header", { class: "page-head" },
      h("span", { class: "eyebrow" }, `${index + 1}. kategória · ${category.original}`),
      h("h2", {}, category.name)),
    ...["predict", "pick"].map((kind) => h("section", { class: `question ${kind}` },
      h("h3", {}, QUESTIONS[kind]),
      // As many columns as cards, so the row always fills the page's width. "Ötletem sincs" only where you give the
      // award: a tip can always be a bold guess
      h("div", { class: "cards", style: `--count: ${category.nominees.length + (kind === "pick" ? 1 : 0)}` },
        ...category.nominees.map((nominee, i) => nomineeCard(category, kind, i, nominee)),
        kind === "pick" ? nomineeCard(category, kind, NO_IDEA, { name: "Ötletem sincs, ember" }) : null))),
    h("p", { class: "page-hint" }, isComplete(category)
      ? "✓ Mindkét szavazatod megvan. Bármikor módosíthatod."
      : "Válassz mindkét kérdésnél, és jön a következő kategória."));
}

// --- The guessing contest, once every category's real winner is set: who guessed the most winners

const MEDALS = { 1: "🥇", 2: "🥈", 3: "🥉" };

function names(rows) {
  const list = rows.map((row) => row.name);
  return list.length > 1 ? `${list.slice(0, -1).join(", ")} és ${list[list.length - 1]}` : list[0];
}

function renderBanner() {
  const board = resultsMode() ? state.ballot.leaderboard : null;
  $("results-banner").hidden = !resultsMode() || Boolean(board);
  $("contest-banner").hidden = !board;
  if (!board) return;
  const first = board.rows.filter((row) => row.place === 1);
  const mine = board.rows.find((row) => row.id === state.me.id);
  $("contest-banner").replaceChildren(
    h("div", { class: "contest-trophy", "aria-hidden": "true" }, "🏅"),
    h("h2", {}, first.length ? "Megvan a tippverseny győztese!" : "Minden díj kiosztva!"),
    first.length
      ? h("p", {}, h("b", {}, names(first)), ` tippelt a legjobban: ${first[0].correct} / ${board.categories} talált.`)
      : h("p", {}, "Ebben az évben senki nem tippelt."),
    mine ? h("p", { class: "contest-mine" }, `Helyezésed: ${mine.place}. · ${mine.correct} talált`) : null,
    h("button", { type: "button", class: "contest-button", onclick: () => { goToDone(); scrollToBallot(); } },
      "A tippverseny teljes eredménye →"));
}

function scrollToBallot() {
  window.scrollTo({ top: $("ballot-view").offsetTop - 16, behavior: reducedMotion.matches ? "auto" : "smooth" });
}

function leaderboardEl(board) {
  const me = state.me.id;
  const person = (row) => personEl(row, `${row.correct} / ${board.categories} talált · ❤️ ${row.agreed}`);
  const podium = board.rows.slice(0, 3);
  // The winner in the middle, as on a real podium
  const order = podium.length === 3 ? [podium[1], podium[0], podium[2]] : podium;
  return h("section", { class: "contest" },
    h("h2", {}, "🏅 Tippverseny"),
    h("p", { class: "muted small-text" }, "Ki tippelte meg a legtöbb valódi nyertest (🔮). Holtversenynél az előrébb, akinek a "
      + "❤️ kedvence többször nyert; ha ez is egyezik, közös a helyezés."),
    board.rows.length ? h("div", { class: "podium" }, ...order.map((row) => h("div", {
      class: `podium-step place-${Math.min(row.place, 3)}${row.id === me ? " mine" : ""}`,
    }, h("span", { class: "medal" }, MEDALS[row.place] || `${row.place}.`), person(row),
    h("span", { class: "podium-block" }, `${row.place}.`)))) : h("p", { class: "muted" }, "Senki nem tippelt."),
    board.rows.length > 3 ? h("ol", { class: "board" }, ...board.rows.slice(3).map((row) => h("li", {
      class: row.id === me ? "mine" : "",
    }, h("span", { class: "board-place" }, `${row.place}.`), person(row),
    row.id === me ? h("span", { class: "you" }, "te") : null))) : null);
}

function resultsSummaryPage() {
  const categories = state.ballot.categories;
  const shown = categories.filter(resultOf).length;
  const names = (category, kind) => {
    const result = resultOf(category);
    if (!result) return h("td", { class: "muted" }, "még nincs kint");
    if (!result[kind].winners.length) return h("td", { class: "muted" }, "–");
    return h("td", { class: kind }, result[kind].winners.map((i) => nomineeName(category, i)).join(", "));
  };
  const real = (category) => (actualOf(category) === undefined
    ? h("td", { class: "muted" }, "még nincs kint")
    : h("td", { class: "real" }, `⭐ ${nomineeName(category, actualOf(category))}`));
  const rows = categories.map((category, index) => h("tr", {}, h("th", { scope: "row" },
    h("button", { type: "button", class: "link-button", onclick: () => goTo(index) }, category.name)),
  real(category), names(category, "predict"), names(category, "pick")));
  const board = state.ballot.leaderboard;
  return h("article", { class: "page done-page" },
    board ? leaderboardEl(board) : null,
    h("div", { class: "celebrate", "aria-hidden": "true" }, "🏆"),
    h("h2", {}, shown === categories.length ? "Minden díjazott kint van!" : `${shown} / ${categories.length} díjazott kint`),
    h("p", { class: "muted" }, "Kattints egy kategóriára a részletekért."),
    h("div", { class: "table-wrap" }, h("table", { class: "detail-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Kategória"), h("th", {}, "⭐ A gálán nyert"), h("th", {}, "🔮 Imbul tippje"), h("th", {}, "🏆 Imbul-díj"))),
      h("tbody", {}, ...rows))));
}

function donePage() {
  if (resultsMode()) return resultsSummaryPage();
  const categories = state.ballot.categories;
  const open = categories.filter((c) => !isComplete(c)).length;
  const rows = categories.map((category, index) => {
    const votes = state.votes[category.id] || {};
    const cell = (kind) => h("td", { class: votes[kind] === undefined ? "muted" : kind },
      votes[kind] === undefined ? "–" : nomineeName(category, votes[kind]));
    return h("tr", {}, h("th", { scope: "row" },
      h("button", { type: "button", class: "link-button", onclick: () => goTo(index) }, category.name)),
    cell("predict"), cell("pick"));
  });
  return h("article", { class: "page done-page" },
    h("div", { class: "celebrate", "aria-hidden": "true" }, "🏆"),
    h("h2", {}, open ? `Már csak ${open} kategória van hátra` : "Kész vagy, köszi a szavazást!"),
    h("p", { class: "muted" }, open
      ? "Kattints egy kategóriára, hogy befejezd. A határidőig mindent módosíthatsz."
      : "A határidőig bármit módosíthatsz: kattints egy kategóriára."),
    h("div", { class: "table-wrap" }, h("table", { class: "detail-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Kategória"), h("th", {}, "🔮 Szerinted nyer"), h("th", {}, "❤️ Te ennek adnád"))),
      h("tbody", {}, ...rows))));
}

function currentPage() {
  return ballotUi.done ? donePage() : categoryPage(ballotUi.index);
}

// Swaps the page; `direction` is "next", "prev" or null (no animation)
function showPage(direction) {
  const stage = $("stage");
  const old = stage.firstElementChild;
  const page = currentPage();
  renderNav();
  const id = ballotUi.done ? "kesz" : state.ballot.categories[ballotUi.index].id;
  if (location.hash !== `#${id}`) history.replaceState(null, "", `#${id}`);
  if (!old || !direction || reducedMotion.matches) {
    stage.replaceChildren(page);
    return;
  }
  ballotUi.animating = true;
  // The glow of a just completed page would override the slide-out (and its end would never come)
  old.classList.remove("just-completed");
  old.classList.add(`leave-${direction}`);
  afterAnimation(old, `leave-${direction}`, 400, () => {
    page.classList.add(`enter-${direction}`);
    stage.replaceChildren(page);
    window.scrollTo({ top: Math.min(window.scrollY, $("ballot-view").offsetTop - 16), behavior: "smooth" });
    afterAnimation(page, `enter-${direction}`, 600, () => {
      page.classList.remove(`enter-${direction}`);
      ballotUi.animating = false;
    });
  });
}

// Runs `then` once, when the element's `name` animation ends, or after `fallbackMs` at the latest (a hidden tab or
// an animation that never started would otherwise leave the ballot stuck between two pages)
function afterAnimation(element, name, fallbackMs, then) {
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    clearTimeout(timer);
    element.removeEventListener("animationend", onEnd);
    then();
  };
  const onEnd = (event) => { if (event.target === element && event.animationName === name) finish(); };
  const timer = setTimeout(finish, fallbackMs);
  element.addEventListener("animationend", onEnd);
}

function cancelAdvance() {
  clearTimeout(ballotUi.advanceTimer);
  ballotUi.advanceTimer = null;
}

function goTo(index) {
  cancelAdvance();
  if (ballotUi.animating) return;
  const direction = ballotUi.done || index < ballotUi.index ? "prev" : "next";
  if (!ballotUi.done && index === ballotUi.index) return;
  ballotUi.done = false;
  ballotUi.index = index;
  showPage(direction);
}

function goNext() {
  if (ballotUi.done) return;
  if (ballotUi.index < state.ballot.categories.length - 1) return goTo(ballotUi.index + 1);
  goToDone();
}

function goPrev() {
  if (ballotUi.done) return goTo(state.ballot.categories.length - 1);
  if (ballotUi.index > 0) goTo(ballotUi.index - 1);
}

function goToDone() {
  cancelAdvance();
  if (ballotUi.animating) return;
  ballotUi.done = true;
  showPage("next");
}

// After both questions are answered: a short pause to see the choice, then the next unanswered category
function scheduleAdvance() {
  cancelAdvance();
  const from = ballotUi.index;
  $("stage").firstElementChild.classList.add("just-completed");
  ballotUi.advanceTimer = setTimeout(() => {
    ballotUi.advanceTimer = null;
    if (ballotUi.done || ballotUi.index !== from) return;
    const next = firstOpenIndex(from + 1);
    if (next === -1) goToDone();
    else goTo(next);
  }, AUTO_ADVANCE_MS);
}

function refreshPage() {
  // Same page, new state: rebuilt in place, without animation
  const stage = $("stage");
  const old = stage.firstElementChild;
  const page = currentPage();
  if (old && old.classList.contains("just-completed")) page.classList.add("just-completed");
  stage.replaceChildren(page);
  renderNav();
}

function renderBallot() {
  renderDeadline();
  const results = resultsMode();
  renderBanner();
  $("ballot-tab").textContent = results ? "🏆 Díjazottak" : "🗳️ Szavazás";
  const fromHash = state.ballot.categories.findIndex((c) => `#${c.id}` === location.hash);
  const open = results ? 0 : firstOpenIndex(0);
  ballotUi.done = fromHash === -1 && (location.hash === "#kesz" || open === -1);
  ballotUi.index = fromHash !== -1 ? fromHash : Math.max(open, 0);
  showPage(null);
  if (results) watchResults();
}

// During the show the admin reveals the categories one by one: open pages check every RESULTS_POLL_MS (and when the
// tab comes back into view), and a newly shown category lights up
let resultsTimer = null;

function watchResults() {
  if (resultsTimer) return;
  const check = async () => {
    if (document.hidden || !resultsMode()) return;
    try {
      const ballot = await api(ballotPath(state.ballot.year === state.ballot.current ? null : state.ballot.year));
      const before = state.ballot.results || {};
      if (JSON.stringify(ballot.results) === JSON.stringify(before)
          && JSON.stringify(ballot.actual) === JSON.stringify(state.ballot.actual || {})) return;
      state.ballot.results = ballot.results;
      state.ballot.actual = ballot.actual;
      state.ballot.leaderboard = ballot.leaderboard;
      renderBanner();
      const current = !ballotUi.done && state.ballot.categories[ballotUi.index];
      const fresh = current && ballot.results[current.id] && !before[current.id];
      if (!ballotUi.animating) refreshPage();
      if (fresh) $("stage").firstElementChild.classList.add("just-revealed");
    } catch (error) {
      // Offline or logged out: try again next time
    }
  };
  resultsTimer = setInterval(check, RESULTS_POLL_MS);
  document.addEventListener("visibilitychange", check);
}

async function vote(categoryId, kind, nominee) {
  $("ballot-error").textContent = "";
  const category = state.ballot.categories.find((c) => c.id === categoryId);
  const wasComplete = isComplete(category);
  const previous = state.votes[categoryId] ? { ...state.votes[categoryId] } : undefined;
  // Show the choice at once; the server's answer confirms it
  state.votes[categoryId] = { ...(previous || {}) };
  if (nominee === null) delete state.votes[categoryId][kind];
  else state.votes[categoryId][kind] = nominee;
  if (!isComplete(category)) cancelAdvance();
  refreshPage();
  if (!wasComplete && isComplete(category)) scheduleAdvance();
  try {
    const { votes } = await api("api/votes", { method: "PUT", body: { category: categoryId, kind, nominee } });
    state.votes = votes;
  } catch (error) {
    cancelAdvance();
    if (previous) state.votes[categoryId] = previous;
    else delete state.votes[categoryId];
    $("ballot-error").textContent = `Nem sikerült menteni: ${error.message}`;
    if (error.status === 401 || error.status === 403) return start();
    refreshPage();
  }
  renderNav();
}

// A link or the back button changing only the #category part of the address
window.addEventListener("hashchange", () => {
  if (!state.ballot) return;
  const index = state.ballot.categories.findIndex((c) => `#${c.id}` === location.hash);
  if (index !== -1) goTo(index);
  else if (location.hash === "#kesz") goToDone();
});
$("prev").addEventListener("click", goPrev);
$("next").addEventListener("click", goNext);
document.addEventListener("keydown", (event) => {
  if ($("ballot-view").hidden || event.target.closest("input, select, textarea") || event.altKey || event.ctrlKey || event.metaKey) return;
  if (document.getElementById("new-version")) return;
  if (event.key === "ArrowRight") goNext();
  if (event.key === "ArrowLeft") goPrev();
});

// --- Admin

function nomineeName(category, index) {
  if (index === NO_IDEA) return NO_IDEA_LABEL;
  const nominee = category.nominees[index];
  if (!nominee) return "?";
  // Two nominees can share a name (Team Falcons): the detail tells them apart
  const twins = category.nominees.filter((n) => n.name === nominee.name).length > 1;
  return twins ? `${nominee.name} (${nominee.detail})` : nominee.name;
}

function totalsCard(category) {
  const totals = state.summary.totals[category.id];
  const winner = state.summary.winners[category.id];
  const max = Math.max(1, ...totals.predict, ...totals.pick, totals.noIdea.predict, totals.noIdea.pick);
  const bar = (kind, count) => h("td", { class: `count ${kind}` },
    h("span", { class: "mini-bar", style: `width:${(count / max) * 100}%` }), h("b", {}, String(count)));
  const rows = category.nominees.map((nominee, index) => h("tr", { class: winner === index ? "winner" : "" },
    h("th", { scope: "row" }, nomineeName(category, index)),
    ...["predict", "pick"].map((kind) => bar(kind, totals[kind][index])),
    h("td", {}, h("button", {
      type: "button",
      class: "trophy",
      "aria-pressed": winner === index ? "true" : "false",
      title: winner === index ? "Nyertes (kattints a visszavonáshoz)" : "Megjelölöm nyertesként",
      onclick: () => setWinner(category.id, winner === index ? null : index),
    }, "🏆"))));
  rows.push(h("tr", { class: "no-idea" }, h("th", { scope: "row" }, NO_IDEA_LABEL),
    bar("predict", totals.noIdea.predict), bar("pick", totals.noIdea.pick), h("td", {})));
  return h("article", { class: "panel category" },
    h("header", { class: "category-head" }, h("h2", {}, category.name), h("span", { class: "original" }, category.original)),
    h("div", { class: "table-wrap" }, h("table", { class: "totals-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Jelölt"), h("th", {}, "🔮 Tipp"), h("th", {}, "❤️ Kedvenc"), h("th", {}, ""))),
      h("tbody", {}, ...rows))));
}

function renderTotals() {
  $("totals").replaceChildren(...state.ballot.categories.map(totalsCard));
}

function renderVoterDetail() {
  const select = $("voter-select");
  const voter = state.summary.voters.find((v) => v.id === select.value);
  if (!voter) {
    $("voter-detail").replaceChildren(h("p", { class: "muted" }, "Még senki nem szavazott."));
    return;
  }
  const rows = state.ballot.categories.map((category) => {
    const votes = voter.votes[category.id] || {};
    const winner = state.summary.winners[category.id];
    const cell = (kind) => {
      if (votes[kind] === undefined) return h("td", { class: "muted" }, "–");
      const hit = winner !== undefined && votes[kind] === winner;
      return h("td", { class: hit ? "hit" : "" }, nomineeName(category, votes[kind]), hit ? " 🏆" : "");
    };
    return h("tr", {}, h("th", { scope: "row" }, category.name), cell("predict"), cell("pick"));
  });
  $("voter-detail").replaceChildren(
    h("div", { class: "voter-head" }, personEl(voter, `${voter.count} / ${state.ballot.categories.length * 2} szavazat`)),
    h("div", { class: "table-wrap" }, h("table", { class: "detail-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Kategória"), h("th", {}, "🔮 Szerinte nyer"), h("th", {}, "❤️ Ő ennek adná"))),
      h("tbody", {}, ...rows))));
}

function renderVoterTable() {
  const voters = state.summary.voters;
  if (!voters.length) {
    $("voter-table").replaceChildren();
    return;
  }
  const head = h("tr", {}, h("th", {}, "Kategória"), ...voters.map((v) => h("th", { colspan: 2 }, v.name)));
  const rows = state.ballot.categories.map((category) => h("tr", {}, h("th", { scope: "row" }, category.name),
    ...voters.flatMap((voter) => ["predict", "pick"].map((kind) => {
      const value = (voter.votes[category.id] || {})[kind];
      const hit = value !== undefined && state.summary.winners[category.id] === value;
      return h("td", { class: `${kind}${hit ? " hit" : ""}`, title: KIND_LABELS[kind] },
        value === undefined ? "–" : `${kind === "predict" ? "🔮" : "❤️"} ${nomineeName(category, value)}`);
    }))));
  $("voter-table").replaceChildren(h("thead", {}, head), h("tbody", {}, ...rows));
}

function renderVoters() {
  const select = $("voter-select");
  const current = select.value;
  select.replaceChildren(...state.summary.voters.map((v) => h("option", { value: v.id }, `${v.name} (${v.count})`)));
  if (state.summary.voters.some((v) => v.id === current)) select.value = current;
  renderVoterDetail();
  renderVoterTable();
}

function renderScores() {
  const { scores, decided } = state.summary;
  const total = state.ballot.categories.length;
  $("scores-note").textContent = decided
    ? `${decided} / ${total} kategória nyertese van megjelölve. A 🔮 tippek számítanak; holtversenynél az dönt, kinek a ❤️ kedvence nyert többször.`
    : "Még egyik kategória nyertese sincs megjelölve (Összesítés → 🏆).";
  $("scores").replaceChildren(...scores.map((s) => h("li", {},
    personEl(s, `${s.predicted} tipp a lezárt kategóriákban`),
    h("span", { class: "score" }, h("b", {}, String(s.correct)), " talált",
      h("small", {}, ` · ❤️ ${s.agreed}`)))));
}

// Access, as on Imbul Stats: everyone or nobody at once, one by one by Discord id, the refused logins, and the list
// of everyone with access (taken away ones stay on it, to be given back)
function renderAccess() {
  const { everyone, everyoneAuto, opensAt, people, refused, allowedNow } = state.access;
  const handle = (p) => `${p.username ? "@" + p.username + " · " : ""}${p.id}`;
  const when = (iso) => dateTime.format(new Date(iso));
  if (everyone) {
    $("everyone-state").textContent = everyoneAuto
      ? `✅ Most a szerver minden tagja beléphet (magától kapcsolt be: ${when(opensAt)}).`
      : "✅ Most a szerver minden tagja beléphet.";
  } else if (everyoneAuto && opensAt) {
    $("everyone-state").textContent = `🕙 ${when(opensAt)}-kor magától bekapcsol: onnantól a szerver minden tagja `
      + "beléphet (a yagi.hu csempéje is ekkor fedi fel magát). Addig csak az adminok és a listán szereplők.";
  } else {
    $("everyone-state").textContent = "🔒 Most csak az adminok és a listán szereplők léphetnek be.";
  }
  $("everyone-state").className = `everyone-state${everyone ? " on" : ""}`;
  $("grant-everyone").disabled = everyone;

  $("refused").replaceChildren(...(refused.length ? refused.map((p) => h("li", {},
    personEl(p, `${handle(p)} · ${p.attempts}× · ${when(p.lastAt)}`),
    h("button", { type: "button", class: "small", onclick: () => setAccess(p.id, true) }, "Beengedem")))
    : [h("li", { class: "muted" }, "Senki.")]));

  $("access-summary").textContent = `Akik szavazhatnak, az utoljára itt járt elöl. Összesen ${people.length}, ebből ${allowedNow} most is beléphet.`;
  const rows = people.map((p) => {
    const notes = [];
    if (p.granted) notes.push(`Felvéve: ${when(p.granted.at)} · ${p.granted.by}`);
    if (p.revoked) notes.push(`Elvéve: ${when(p.revoked.at)} · ${p.revoked.by}`);
    let action = null;
    if (p.isAdmin) action = null;
    else if (p.revoked) action = h("button", { type: "button", class: "small", onclick: () => setAccess(p.id, true) }, "Visszaadás");
    else action = h("button", {
      type: "button",
      class: "small secondary revoke",
      onclick: () => { if (confirm(`Biztosan elveszed ${p.name} hozzáférését?`)) setAccess(p.id, false); },
    }, "Elvétel");
    return h("tr", { class: p.revoked ? "access-revoked" : "" },
      h("td", {}, personEl(p, handle(p)),
        p.isAdmin ? h("span", { class: "badge" }, "admin") : null,
        ...notes.map((note) => h("div", { class: "access-note muted" }, note))),
      h("td", { class: "muted" }, p.lastSeen ? when(p.lastSeen) : "Még nem járt itt"),
      h("td", { class: "access-action" }, action));
  });
  $("access-table").replaceChildren(
    h("thead", {}, h("tr", {}, h("th", {}, "Ki"), h("th", {}, "Utoljára"), h("th", {}, ""))),
    h("tbody", {}, ...rows));
}

async function setEveryone(allowed) {
  const question = allowed
    ? "Biztosan jogosultságot adsz a szerver minden tagjának?"
    : "Biztosan mindenkitől elveszed a jogosultságot? Csak az adminok tudnak majd belépni.";
  if (!confirm(question)) return;
  $("admin-error").textContent = "";
  try {
    const result = await api("api/admin/access/everyone", { method: "POST", body: { allowed } });
    state.access = result;
    renderAccess();
    const message = allowed
      ? `Mostantól a szerver minden tagja be tud lépni.${result.changed ? ` ${result.changed} ember visszakapta a jogát.` : ""}`
      : `${result.changed} embertől vetted el a jogosultságot, most csak az adminok tudnak belépni.`;
    $("access-result").textContent = message;
    $("access-result").className = `access-result${allowed ? " ok" : ""}`;
    $("access-result").hidden = false;
  } catch (error) {
    $("admin-error").textContent = error.message;
  }
}

// Díjazottak: each category's most voted nominees (per question), shown to everyone or hidden again
function topNames(category, counts) {
  const top = Math.max(0, ...counts);
  if (!top) return { text: "nincs szavazat", votes: 0 };
  const names = counts.map((c, i) => (c === top ? nomineeName(category, i) : null)).filter(Boolean);
  return { text: names.join(", "), votes: top };
}

function renderReveal() {
  const { totals, revealed, closed } = state.summary;
  const shown = new Set(revealed);
  const when = dateTime.format(new Date(state.ballot.closesAt));
  $("reveal-note").textContent = closed
    ? `${shown.size} / ${state.ballot.categories.length} kategória díjazottja látszik mindenkinek. A szavazók oldala 20 másodpercen belül magától frissül.`
    : `A szavazás még tart (${when}-ig): addig a szavazók semmit nem látnak ebből, a megjelölt kategóriák a határidő után jelennek meg.`;
  const cell = (category, kind) => {
    const top = topNames(category, totals[category.id][kind]);
    return h("td", { class: top.votes ? kind : "muted" }, top.text, top.votes ? h("small", { class: "muted" }, ` (${top.votes})`) : null);
  };
  // The real winner at The Game Awards: chosen from the category's nominees, shown to everyone after the deadline
  const realSelect = (category) => {
    const current = state.summary.winners[category.id];
    const select = h("select", { class: "real-select", "aria-label": `${category.name}: ki nyert a gálán?` },
      h("option", { value: "" }, "– még nincs –"),
      ...category.nominees.map((nominee, i) => h("option", { value: String(i) }, nomineeName(category, i))));
    select.value = current === undefined ? "" : String(current);
    select.addEventListener("change", () => setWinner(category.id, select.value === "" ? null : Number(select.value)));
    return h("td", { class: current === undefined ? "" : "has-real" }, select);
  };
  const rows = state.ballot.categories.map((category) => {
    const isShown = shown.has(category.id);
    return h("tr", { class: isShown ? "shown" : "" },
      h("th", { scope: "row" }, category.name),
      realSelect(category),
      cell(category, "predict"), cell(category, "pick"),
      h("td", { class: "reveal-action" }, h("button", {
        type: "button",
        class: isShown ? "small secondary" : "small",
        onclick: () => setRevealed(category.id, !isShown),
      }, isShown ? "Elrejtés" : "Mutatás mindenkinek")));
  });
  $("reveal-table").replaceChildren(
    h("thead", {}, h("tr", {}, h("th", {}, "Kategória"), h("th", {}, "⭐ A gálán nyert"), h("th", {}, "🔮 Imbul tippje"), h("th", {}, "🏆 Imbul-díj"), h("th", {}, "Imbul eredmény"))),
    h("tbody", {}, ...rows));
}

async function setRevealed(category, shown) {
  $("admin-error").textContent = "";
  try {
    state.summary = await api("api/admin/reveal", { method: "PUT", body: { category, shown, year: state.ballot.year } });
    renderAdmin();
    // The admin's own Díjazottak pages follow at once
    if (resultsMode()) {
      state.ballot.results = state.summary.results;
      if (!ballotUi.animating) refreshPage();
    }
  } catch (error) {
    $("admin-error").textContent = error.message;
  }
}

function renderAdmin() {
  renderTotals();
  renderVoters();
  renderScores();
  renderReveal();
}

async function loadAdmin() {
  $("admin-error").textContent = "";
  try {
    [state.summary, state.access] = await Promise.all([api(`api/admin/summary?ev=${state.ballot.year}`), api("api/admin/access")]);
    renderAdmin();
    renderAccess();
  } catch (error) {
    $("admin-error").textContent = error.message;
  }
}

async function setWinner(category, nominee) {
  try {
    state.summary = await api("api/admin/winners", { method: "PUT", body: { category, nominee, year: state.ballot.year } });
    renderAdmin();
    // The admin's own Díjazottak pages follow at once
    if (resultsMode()) {
      state.ballot.actual = state.summary.winners;
      if (!ballotUi.animating) refreshPage();
    }
  } catch (error) {
    $("admin-error").textContent = error.message;
  }
}

async function setAccess(userId, allowed) {
  $("admin-error").textContent = "";
  try {
    state.access = await api("api/admin/access", { method: "POST", body: { userId, allowed } });
    renderAccess();
    return true;
  } catch (error) {
    $("admin-error").textContent = error.message;
    return false;
  }
}

// --- Tabs

function showTab(tab) {
  for (const button of document.querySelectorAll("[data-tab]")) {
    button.setAttribute("aria-pressed", String(button.dataset.tab === tab));
  }
  $("ballot-view").hidden = tab !== "ballot";
  $("admin-view").hidden = tab !== "admin";
  if (tab === "admin") loadAdmin();
}

function showSub(sub) {
  for (const button of document.querySelectorAll("[data-sub]")) {
    button.setAttribute("aria-pressed", String(button.dataset.sub === sub));
  }
  for (const panel of document.querySelectorAll("[data-panel]")) panel.hidden = panel.dataset.panel !== sub;
}

// --- Start

function showLogin() {
  const error = new URLSearchParams(location.search).get("hiba");
  if (error && LOGIN_ERRORS[error]) {
    $("login-error").textContent = LOGIN_ERRORS[error];
    $("login-error").hidden = false;
  }
  $("login-view").hidden = false;
  // After the deadline the welcome page leads to the Díjazottak instead
  api("api/status").then(({ closed }) => {
    if (!closed) return;
    $("deadline-note").textContent = "A szavazás lezárult. Lépj be, és nézd meg a díjazottakat: a gála alatt folyamatosan kerülnek fel.";
  }).catch(() => null);
}

// --- Years: the latest year is the current vote and the default; earlier years show their results the same way.
// The choice is kept in the address (?ev=2026), only while it is not the current year.

function chosenYear() {
  const year = Number(new URLSearchParams(location.search).get("ev"));
  return Number.isInteger(year) && year > 0 ? year : null;
}

function ballotPath(year = chosenYear()) {
  return year ? `api/ballot?ev=${year}` : "api/ballot";
}

async function loadBallot(year = chosenYear()) {
  const ballot = await api(ballotPath(year));
  state.votes = ballot.votes;
  state.ballot = ballot;
}

function renderYears() {
  const { year, years, current } = state.ballot;
  document.querySelector(".page-header .year").textContent = String(year);
  document.title = year === current ? "Imbul Awards" : `Imbul Awards ${year}`;
  $("year-switch").hidden = years.length < 2;
  $("year-select").replaceChildren(...[...years].reverse().map((y) => h("option", { value: String(y) },
    y === current ? `${y} (idei)` : String(y))));
  $("year-select").value = String(year);
}

async function switchYear(year) {
  const params = new URLSearchParams(location.search);
  if (year === state.ballot.current) params.delete("ev");
  else params.set("ev", String(year));
  const search = params.toString();
  history.replaceState(null, "", `${location.pathname}${search ? `?${search}` : ""}`);
  try {
    await loadBallot(year === state.ballot.current ? null : year);
  } catch (error) {
    $("ballot-error").textContent = error.message;
    return;
  }
  renderYears();
  renderBallot();
  if (!$("admin-view").hidden) loadAdmin();
}

async function start() {
  for (const id of ["login-view", "tabs", "ballot-view", "admin-view", "me"]) $(id).hidden = true;
  $("loading").hidden = false;
  try {
    state.me = await api("api/me");
  } catch (error) {
    $("loading").hidden = true;
    if (error.status === 401 || error.status === 403) return showLogin();
    $("loading").textContent = `Hiba: ${error.message}`;
    $("loading").hidden = false;
    return;
  }
  // Tidy the login's ?hiba=… away, keep the chosen year
  const year = chosenYear();
  history.replaceState(null, "", `${location.pathname}${year ? `?ev=${year}` : ""}${location.hash}`);
  $("me-person").replaceWith(Object.assign(personEl(state.me), { id: "me-person" }));
  $("me").hidden = false;
  try {
    await loadBallot();
  } catch (error) {
    if (chosenYear()) {  // a year there is none of: the current one instead
      history.replaceState(null, "", location.pathname);
      return start();
    }
    $("loading").textContent = `Hiba: ${error.message}`;
    return;
  }
  renderYears();
  $("loading").hidden = true;
  $("tabs").hidden = !state.me.isAdmin;
  $("admin-tab").hidden = !state.me.isAdmin;
  renderBallot();
  showTab("ballot");
}

document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
document.querySelectorAll("[data-sub]").forEach((b) => b.addEventListener("click", () => showSub(b.dataset.sub)));
$("voter-select").addEventListener("change", renderVoterDetail);
$("year-select").addEventListener("change", (event) => switchYear(Number(event.target.value)));
$("logout").addEventListener("click", async () => {
  await api("logout", { method: "POST", body: {} }).catch(() => null);
  location.href = location.pathname;
});
$("grant-everyone").addEventListener("click", () => setEveryone(true));
$("revoke-everyone").addEventListener("click", () => setEveryone(false));
$("grant-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = event.target.elements.userId;
  if (await setAccess(input.value.trim(), true)) input.value = "";
});

start();

// --- Footer and new-version dialog (as on Imbul Forever)
// version.json is written by deploy.sh, so locally there is no footer and no check. Every minute (and when the tab
// comes back into view) the page checks it; once a newer version is live, a dialog asks for a reload.
// "Később" hides it for 15 minutes.

const VERSION_CHECK_INTERVAL = 60_000;
const VERSION_SNOOZE = 15 * 60_000;
let newVersionSnoozedUntil = 0;

async function fetchVersion() {
  const response = await fetch("version.json", { cache: "no-store" });
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

async function renderFooter() {
  try {
    const { version, released } = await fetchVersion();
    $("site-footer").textContent = `v${version} · kiadva: ${dateTime.format(new Date(released))}`;
    $("site-footer").hidden = false;
    return version;
  } catch (error) {
    return null;
  }
}

function showNewVersionDialog(version) {
  if ($("new-version")) return;
  const reload = h("button", { type: "button", class: "modal-primary", onclick: () => location.reload() }, "Újratöltés");
  const later = h("button", { type: "button", class: "modal-secondary" }, "Később");
  const backdrop = h("div", { id: "new-version", class: "modal-backdrop" },
    h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "new-version-title" },
      h("h2", { id: "new-version-title" }, "✨ Frissült az oldal"),
      h("p", {}, `Megjelent az Imbul Awards új verziója (v${version}). `
        + "Töltsd újra az oldalt, hogy a legfrissebb változatot lásd, és minden rendben működjön."),
      h("div", { class: "modal-actions" }, later, reload)));
  document.body.append(backdrop);
  const close = () => {
    backdrop.remove();
    document.removeEventListener("keydown", onKey);
    newVersionSnoozedUntil = Date.now() + VERSION_SNOOZE;
  };
  const onKey = (event) => { if (event.key === "Escape") close(); };
  later.addEventListener("click", close);
  document.addEventListener("keydown", onKey);
  reload.focus();
}

function watchForNewVersion(loadedVersion) {
  if (!loadedVersion) return;
  const check = async () => {
    if (document.hidden || Date.now() < newVersionSnoozedUntil) return;
    try {
      const { version } = await fetchVersion();
      if (version && version !== loadedVersion) showNewVersionDialog(version);
    } catch (error) {
      // Offline or mid-deploy: try again next time.
    }
  };
  setInterval(check, VERSION_CHECK_INTERVAL);
  document.addEventListener("visibilitychange", check);
}

renderFooter().then(watchForNewVersion);
