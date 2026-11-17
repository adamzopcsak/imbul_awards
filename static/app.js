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
  const done = categories.filter(isComplete).length;
  $("step").textContent = ballotUi.done ? "Összegzés" : `${ballotUi.index + 1} / ${categories.length}`;
  $("done-count").textContent = `${done} / ${categories.length} kategória kész`;
  $("progress-bar").style.width = `${(done / categories.length) * 100}%`;
  $("prev").disabled = !ballotUi.done && ballotUi.index === 0;
  $("next").disabled = ballotUi.done;
  $("dots").replaceChildren(...categories.map((category, index) => h("button", {
    type: "button",
    class: `dot${isComplete(category) ? " complete" : ""}${!ballotUi.done && index === ballotUi.index ? " current" : ""}`,
    title: `${index + 1}. ${category.name}`,
    "aria-label": `${index + 1}. ${category.name}${isComplete(category) ? " (kész)" : ""}`,
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
    return h("span", { class: /\.svg\//i.test(nominee.image) ? "art logo" : "art" }, img);
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
    onclick: () => vote(category.id, kind, chosen ? null : index),
  }, artEl(nominee, index), h("span", { class: "check", "aria-hidden": "true" }, "✓"),
  h("span", { class: "card-text" }, h("span", { class: "card-name" }, nominee.name),
    nominee.detail ? h("span", { class: "card-detail" }, nominee.detail) : null));
}

function categoryPage(index) {
  const category = state.ballot.categories[index];
  return h("article", { class: `page${isComplete(category) ? " complete" : ""}`, "data-category": category.id },
    h("header", { class: "page-head" },
      h("span", { class: "eyebrow" }, `${index + 1}. kategória · ${category.original}`),
      h("h2", {}, category.name)),
    ...["predict", "pick"].map((kind) => h("section", { class: `question ${kind}` },
      h("h3", {}, QUESTIONS[kind]),
      // As many columns as cards (the nominees and "Ötletem sincs"), so the row always fills the page's width
      h("div", { class: "cards", style: `--count: ${category.nominees.length + 1}` },
        ...category.nominees.map((nominee, i) => nomineeCard(category, kind, i, nominee)),
        nomineeCard(category, kind, NO_IDEA, { name: "Ötletem sincs, ember" })))),
    h("p", { class: "page-hint" }, isComplete(category)
      ? "✓ Mindkét szavazatod megvan. Bármikor módosíthatod."
      : "Válassz mindkét kérdésnél, és jön a következő kategória."));
}

function donePage() {
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
  const fromHash = state.ballot.categories.findIndex((c) => `#${c.id}` === location.hash);
  const open = firstOpenIndex(0);
  ballotUi.done = fromHash === -1 && (location.hash === "#kesz" || open === -1);
  ballotUi.index = fromHash !== -1 ? fromHash : Math.max(open, 0);
  showPage(null);
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

function renderAccess() {
  const { granted, refused, admins } = state.access;
  const button = (label, userId, allowed, cls) => h("button", {
    type: "button", class: cls, onclick: () => setAccess(userId, allowed),
  }, label);
  $("refused").replaceChildren(...(refused.length ? refused.map((p) => h("li", {},
    personEl(p, `${p.username ? "@" + p.username + " · " : ""}${p.id} · ${p.attempts}× · ${dateTime.format(new Date(p.lastAt))}`),
    button("Beengedem", p.id, true, "small"))) : [h("li", { class: "muted" }, "Senki.")]));
  $("granted").replaceChildren(
    ...admins.map((p) => h("li", {}, personEl(p), h("span", { class: "badge" }, "admin"))),
    ...granted.map((p) => h("li", {}, personEl(p, `${p.username ? "@" + p.username + " · " : ""}${p.id}`),
      button("Jog elvétele", p.id, false, "secondary small"))));
}

function renderAdmin() {
  renderTotals();
  renderVoters();
  renderScores();
}

async function loadAdmin() {
  $("admin-error").textContent = "";
  try {
    [state.summary, state.access] = await Promise.all([api("api/admin/summary"), api("api/admin/access")]);
    renderAdmin();
    renderAccess();
  } catch (error) {
    $("admin-error").textContent = error.message;
  }
}

async function setWinner(category, nominee) {
  try {
    state.summary = await api("api/admin/winners", { method: "PUT", body: { category, nominee } });
    renderAdmin();
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
  if (location.search) history.replaceState(null, "", location.pathname);
  $("me-person").replaceWith(Object.assign(personEl(state.me), { id: "me-person" }));
  $("me").hidden = false;
  try {
    const ballot = await api("api/ballot");
    state.votes = ballot.votes;
    state.ballot = ballot;
  } catch (error) {
    $("loading").textContent = `Hiba: ${error.message}`;
    return;
  }
  $("loading").hidden = true;
  $("tabs").hidden = !state.me.isAdmin;
  $("admin-tab").hidden = !state.me.isAdmin;
  renderBallot();
  showTab("ballot");
}

document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
document.querySelectorAll("[data-sub]").forEach((b) => b.addEventListener("click", () => showSub(b.dataset.sub)));
$("voter-select").addEventListener("change", renderVoterDetail);
$("logout").addEventListener("click", async () => {
  await api("logout", { method: "POST", body: {} }).catch(() => null);
  location.href = location.pathname;
});
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
