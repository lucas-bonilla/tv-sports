// FIFA World Cup section: fixtures + results by day, with a collapsible
// group-standings block. Renders inside #sec-wc on the single-page layout.
//
// The day bar carries both past and future days. Selecting a past day shows
// that day's matches *with their final scores* (the season payload already
// includes played results), which is the "recover earlier-day results" need.

const WC_MATCHES_URL = "/api/wc/matches";
const WC_STANDINGS_URL = "/api/wc/standings";
const WC_BRACKET_URL = "/api/wc/bracket";

let wcDays = [];                 // [{date, matches}]

// The cuadro columns read left→right as the tournament narrows (16→8→4→2→1);
// the third-place match is a trailing epilogue so it doesn't break the ladder.
const WC_BRACKET_ORDER = ["r32", "r16", "qf", "sf", "final", "third"];

const WC_MONTHS_ABBR = [
  "ene", "feb", "mar", "abr", "may", "jun",
  "jul", "ago", "sep", "oct", "nov", "dic",
];

// --- Helpers ---

function wcFmtDayHeader(dateStr) {
  try {
    return new Date(dateStr + "T00:00:00").toLocaleDateString("es-ES", {
      weekday: "long", day: "numeric", month: "long",
    });
  } catch {
    return dateStr;
  }
}

function wcFlag(emoji) {
  return emoji ? `<span class="wc-flag" aria-hidden="true">${emoji}</span>` : "";
}

// --- Bootstrapping ---

async function fetchWorldCup(quiet = false) {
  const container = document.getElementById("wc-container");
  // On a live auto-refresh we keep the current fixtures on screen (no spinner
  // flash) and just swap in the updated data when it arrives.
  if (!quiet) container.innerHTML = '<div class="spinner"></div>';

  try {
    const res = await fetch(WC_MATCHES_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    wcDays = data.days || [];

    document.getElementById("wc-updated").textContent =
      `Actualizado: ${new Date(data.fetched_at).toLocaleTimeString("es-ES")}`;

    // Section-level live tag: any match currently in play.
    const liveMatch = wcDays.flatMap(d => d.matches).find(m => m.status === "live");
    const liveTag = document.getElementById("wc-live-tag");
    if (liveMatch) {
      liveTag.textContent = `🔴 ${liveMatch.home} ${liveMatch.home_score ?? ""}-${liveMatch.away_score ?? ""} ${liveMatch.away}`;
      liveTag.hidden = false;
    } else {
      liveTag.hidden = true;
    }

    if (!wcDays.length) {
      container.innerHTML = '<div class="no-results">El calendario del Mundial aún no está disponible. 📅</div>';
    } else {
      // Feed the World Cup days into the shared day bar and render.
      if (typeof buildGlobalDateBar === "function") buildGlobalDateBar();
      renderWcMatches();
    }

    // Standings and the knockout bracket load independently so a failure in
    // either doesn't break the fixtures.
    fetchWcStandings();
    fetchWcBracket();
  } catch (err) {
    container.innerHTML = `<div class="error-msg">❌ Error al cargar el Mundial.<br/><small>${err.message}</small></div>`;
  }
}

// --- Matches ---

function renderWcMatches() {
  const container = document.getElementById("wc-container");
  // Shared day filter: globalDateISO (from app.js) is an ISO date or null.
  const sel = typeof globalDateISO !== "undefined" ? globalDateISO : null;
  const days = sel ? wcDays.filter(d => d.date === sel) : wcDays;

  if (!days.length || days.every(d => !d.matches.length)) {
    container.innerHTML = '<div class="no-results">No hay partidos del Mundial este día.</div>';
    return;
  }

  container.innerHTML = days.map(day => `
    <div class="day-section">
      <div class="day-header">${wcFmtDayHeader(day.date)}</div>
      <div class="wc-match-list">
        ${day.matches.map(renderWcMatch).join("")}
      </div>
    </div>`).join("");

  // Two interactions share the card grid: upcoming matches open the calendar
  // modal; finished matches open a result summary (scorers, cards, venue).
  const activate = card => {
    if (card.dataset.wcDetail) openWcDetail(card.dataset.wcDetail, card.dataset.wcTime);
    else if (card.dataset.wcEvent) openWcCalendar(JSON.parse(decodeURIComponent(card.dataset.wcEvent)));
  };
  container.onclick = e => {
    const card = e.target.closest("[data-wc-event],[data-wc-detail]");
    if (card) activate(card);
  };
  container.onkeydown = e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const card = e.target.closest("[data-wc-event],[data-wc-detail]");
    if (card) { e.preventDefault(); activate(card); }
  };

  if (typeof buildQuickNav === "function") buildQuickNav();
}

function renderWcMatch(m) {
  const live = m.status === "live";
  const done = m.status === "finished";
  const hasScore = Number.isFinite(m.home_score) && Number.isFinite(m.away_score);

  // Left column always shows the kickoff time; the score for played matches now
  // sits in the middle of the title, between the two team names.
  const lead = m.time || "—";

  const statusBadge = live
    ? '<span class="wc-badge wc-badge--live">● EN JUEGO</span>'
    : done
      ? '<span class="wc-badge wc-badge--done">FINAL</span>'
      : m.postponed
        ? '<span class="wc-badge wc-badge--warn">APLAZADO</span>'
        : "";

  // Only upcoming, non-postponed matches with a kickoff time can be scheduled.
  const addable = !live && !done && !m.postponed && !!m.time && !!m.date;
  const payload = addable ? encodeURIComponent(JSON.stringify(m)) : "";
  // Finished matches we have an id for open a result summary (scorers, cards).
  const detailable = (done || live) && !!m.match_id;

  const winA = done && hasScore && m.home_score > m.away_score;
  const winB = done && hasScore && m.away_score > m.home_score;

  // Single-line title "Home 🏳 <score> 🏴 Away": for live/finished matches the
  // result sits in the middle between the team names; upcoming matches keep "-".
  const middle = (live || done) && hasScore
    ? `<span class="wc-time-score ${live ? "wc-time-score--live" : ""}">${m.home_score}-${m.away_score}</span>`
    : "-";
  const title = `<span class="${winA ? "wc-won" : ""}">${m.home}</span>${wcFlag(m.home_flag)}`
    + ` ${middle} ${wcFlag(m.away_flag)}<span class="${winB ? "wc-won" : ""}">${m.away}</span>`;

  const competition = Number.isFinite(m.round) ? `Jornada ${m.round}` : "Mundial";

  const interaction = detailable
    ? `data-wc-detail="${m.match_id}" data-wc-time="${m.time || ""}" role="button" tabindex="0" title="Ver resumen del partido"`
    : addable
      ? `data-wc-event="${payload}" role="button" tabindex="0" title="Añadir al calendario"`
      : "";

  return `
    <div class="event-card wc-card ${live ? "wc-card--live" : ""} ${done ? "wc-card--done" : ""}"
      ${interaction}>
      <div class="event-time">${lead}</div>
      <div class="event-body">
        <div class="event-match">${title}</div>
        <div class="event-meta">
          ${statusBadge}
          <span class="event-competition">${competition}</span>
          ${m.channel ? `<span class="event-channel">${m.channel}</span>` : ""}
        </div>
      </div>
    </div>`;
}

// --- Standings (collapsible) ---

async function fetchWcStandings() {
  const block = document.getElementById("wc-standings-block");
  const body = document.getElementById("wc-standings-body");
  const toggle = document.getElementById("wc-standings-toggle");

  try {
    const res = await fetch(WC_STANDINGS_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const groups = data.groups || [];
    if (!groups.length || groups.every(g => !g.table.length)) {
      block.hidden = true;
      return;
    }
    block.hidden = false;

    body.innerHTML = groups.map(g => `
      <div class="wc-standings-group">
        <div class="wc-standings-group-head">${g.name}</div>
        <table class="wc-table">
          <colgroup>
            <col class="wc-col-pos" /><col class="wc-col-team" />
            <col class="wc-col-stat" /><col class="wc-col-stat" /><col class="wc-col-stat" />
            <col class="wc-col-stat" /><col class="wc-col-stat" /><col class="wc-col-stat" />
          </colgroup>
          <thead>
            <tr><th class="wc-th-pos">#</th><th class="wc-th-team">Equipo</th>
              <th>PJ</th><th>G</th><th>E</th><th>P</th><th>DG</th><th class="wc-th-pts">Pts</th></tr>
          </thead>
          <tbody>
            ${g.table.map(r => `
              <tr>
                <td class="wc-td-pos">${r.rank ?? ""}</td>
                <td class="wc-td-team">${wcFlag(r.flag)}<span>${r.team}</span></td>
                <td>${r.played}</td><td>${r.win}</td><td>${r.draw}</td><td>${r.loss}</td>
                <td>${r.gd > 0 ? "+" + r.gd : r.gd}</td>
                <td class="wc-td-pts">${r.points}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>`).join("");

    if (!toggle.dataset.bound) {
      toggle.dataset.bound = "1";
      toggle.addEventListener("click", () => {
        const open = body.hidden;
        body.hidden = !open;
        toggle.setAttribute("aria-expanded", String(open));
        toggle.classList.toggle("open", open);
      });
    }
  } catch (err) {
    // Standings are secondary — hide the block silently on failure.
    block.hidden = true;
  }
}

// --- Knockout bracket (cuadro de la fase eliminatoria) ---

// A short "3 jul · 21:00" label for a bracket card. Either part can be missing.
function wcBracketWhen(m) {
  let day = "";
  if (m.date) {
    const [y, mo, d] = m.date.split("-").map(Number);
    if (d && mo) day = `${d} ${WC_MONTHS_ABBR[mo - 1]}`;
  }
  return [day, m.time].filter(Boolean).join(" · ");
}

// One team row inside a matchup card: flag, name, score. `side` is "home"/"away"
// so the winner of a finished tie can be bolded.
function wcBracketTeam(m, side) {
  const name = m[side];
  const flag = m[`${side}_flag`];
  const score = m[`${side}_score`];
  const won = m.winner === side;
  const hasScore = Number.isFinite(score);
  return `
    <div class="wc-br-team ${won ? "wc-br-team--won" : ""}">
      ${wcFlag(flag)}<span class="wc-br-name">${name || "—"}</span>
      <span class="wc-br-score">${hasScore ? score : ""}</span>
    </div>`;
}

// An empty slot shown before the draw for a round resolves.
function wcBracketPlaceholder() {
  return `
    <div class="wc-br-match wc-br-match--tbd" aria-hidden="true">
      <div class="wc-br-team"><span class="wc-br-name">Por definir</span></div>
      <div class="wc-br-team"><span class="wc-br-name">Por definir</span></div>
    </div>`;
}

function wcBracketMatch(m) {
  const live = m.status === "live";
  const done = m.status === "finished";
  const when = wcBracketWhen(m);
  const badge = live
    ? '<span class="wc-badge wc-badge--live">● EN JUEGO</span>'
    : done
      ? '<span class="wc-badge wc-badge--done">FINAL</span>'
      : "";
  // Live/finished matches with an id open the same result sheet as the fixtures.
  const detailable = (done || live) && !!m.match_id;
  const interaction = detailable
    ? `data-wc-detail="${m.match_id}" data-wc-time="${m.time || ""}" role="button" tabindex="0" title="Ver resumen del partido"`
    : "";

  return `
    <div class="wc-br-match ${live ? "wc-br-match--live" : ""} ${done ? "wc-br-match--done" : ""}" ${interaction}>
      ${wcBracketTeam(m, "home")}
      ${wcBracketTeam(m, "away")}
      <div class="wc-br-foot">
        ${when ? `<span class="wc-br-when">${when}</span>` : "<span></span>"}
        ${badge}
      </div>
    </div>`;
}

function wcBracketColumn(round) {
  const matches = round.matches || [];
  // Pad the round out to its full slot count so the ladder keeps its shape
  // before the draw resolves.
  const pad = Math.max(0, (round.slots || matches.length) - matches.length);
  const cards = matches.map(wcBracketMatch).join("")
    + Array.from({ length: pad }, wcBracketPlaceholder).join("");
  const count = round.slots ? `${matches.length}/${round.slots}` : `${matches.length}`;
  return `
    <div class="wc-br-round wc-br-round--${round.key}">
      <div class="wc-br-round-head">
        <span class="wc-br-round-name">${round.name}</span>
        <span class="wc-br-round-count">${count}</span>
      </div>
      <div class="wc-br-round-body">${cards}</div>
    </div>`;
}

async function fetchWcBracket() {
  const block = document.getElementById("wc-bracket-block");
  const body = document.getElementById("wc-bracket-body");
  const toggle = document.getElementById("wc-bracket-toggle");
  const scroll = document.getElementById("wc-bracket-scroll");
  if (!block || !scroll) return;

  try {
    const res = await fetch(WC_BRACKET_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (!data.has_matches) {
      // Group stage still running — nothing to show yet.
      block.hidden = true;
      return;
    }
    block.hidden = false;

    const byKey = Object.fromEntries((data.rounds || []).map(r => [r.key, r]));
    const ordered = WC_BRACKET_ORDER.map(k => byKey[k]).filter(Boolean);
    scroll.innerHTML = ordered.map(wcBracketColumn).join("");

    // The live pill sits in the toggle label so it's visible while collapsed.
    const liveTag = document.getElementById("wc-bracket-live-tag");
    if (liveTag) {
      liveTag.textContent = data.live ? "🔴 EN JUEGO" : "";
      liveTag.hidden = !data.live;
    }

    // Clicking a played/live match opens the shared result sheet.
    scroll.onclick = e => {
      const card = e.target.closest("[data-wc-detail]");
      if (card) openWcDetail(card.dataset.wcDetail, card.dataset.wcTime);
    };
    scroll.onkeydown = e => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const card = e.target.closest("[data-wc-detail]");
      if (card) { e.preventDefault(); openWcDetail(card.dataset.wcDetail, card.dataset.wcTime); }
    };

    // Collapse/expand, bound once (mirrors the group-standings block).
    if (toggle && !toggle.dataset.bound) {
      toggle.dataset.bound = "1";
      toggle.addEventListener("click", () => {
        const open = body.hidden;
        body.hidden = !open;
        toggle.setAttribute("aria-expanded", String(open));
        toggle.classList.toggle("open", open);
      });
    }
  } catch (err) {
    // The bracket is secondary to the fixtures — hide it silently on failure.
    block.hidden = true;
  }
}

// --- Live auto-refresh ---
//
// Keep the fixtures and cuadro in sync as matches finish, without a manual
// refresh. Polls only while the tab is visible (and refreshes on becoming
// visible again) so a backgrounded tab doesn't hammer the API.
const WC_AUTO_REFRESH_MS = 60000;
let wcAutoRefreshTimer = null;

function wcRefreshLive() {
  // fetchWorldCup(quiet) also refreshes the bracket and standings.
  fetchWorldCup(true);
}

function startWcAutoRefresh() {
  if (wcAutoRefreshTimer) return;
  wcAutoRefreshTimer = setInterval(() => {
    if (document.visibilityState === "visible") wcRefreshLive();
  }, WC_AUTO_REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") wcRefreshLive();
  });
}

// --- Calendar integration ---

// Reuse openCalendarModal() from app.js. parseEventDate() there wants a Spanish
// text date ("sábado 11 de junio"), not the ISO date the API returns.
const WC_MONTHS_ES = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

function wcEventForCalendar(m) {
  const [y, mo, d] = (m.date || "").split("-").map(Number);
  if (!y || !mo || !d) return null;
  return {
    emoji: "⚽",
    match: `${m.home} vs ${m.away}`,
    sport: "Mundial",
    date: `mundial ${d} de ${WC_MONTHS_ES[mo - 1]}`, // parser reads parts[1]=day, parts[3]=month
    time: m.time,
    competition: ["Mundial 2026", Number.isFinite(m.round) ? `Jornada ${m.round}` : ""].filter(Boolean).join(" · "),
    channel: m.channel || m.venue || "",
  };
}

function openWcCalendar(m) {
  const event = wcEventForCalendar(m);
  if (event && typeof openCalendarModal === "function") openCalendarModal(event);
}

// --- Match summary slider (resultado, goleadores, info) ---

const WC_EVENT_ICON = {
  goal: "⚽", penalty: "⚽", own_goal: "🥅", yellow: "🟨", red: "🟥", sub: "🔁",
};

function wcEventRow(e) {
  const icon = WC_EVENT_ICON[e.kind] || "•";
  const min = e.minute != null ? `${e.minute}'` : "";
  const tag = e.kind === "penalty" ? " (pen.)" : e.kind === "own_goal" ? " (p.p.)" : "";
  const assist = e.assist && (e.kind === "goal" || e.kind === "penalty")
    ? `<span class="wc-detail-assist">asist. ${e.assist}</span>` : "";
  // Home events sit on the left rail, away events on the right.
  return `
    <li class="wc-detail-ev ${e.home ? "wc-detail-ev--home" : "wc-detail-ev--away"}">
      <span class="wc-detail-min">${min}</span>
      <span class="wc-detail-icon">${icon}</span>
      <span class="wc-detail-player">${e.player}${tag}${assist}</span>
    </li>`;
}

function wcDetailBody(d) {
  const events = d.events || [];
  const goals = events.filter(e => e.kind === "goal" || e.kind === "penalty" || e.kind === "own_goal");
  const cards = events.filter(e => e.kind === "yellow" || e.kind === "red");
  const score = (d.home_score != null && d.away_score != null) ? `${d.home_score} - ${d.away_score}` : "vs";
  const info = [
    Number.isFinite(d.round) ? `Jornada ${d.round}` : null,
    [d.venue, d.city].filter(Boolean).join(", ") || null,
    d.time ? `${d.time} h` : null,
    d.spectators ? `${d.spectators.toLocaleString("es-ES")} espectadores` : null,
  ].filter(Boolean);

  // The free TheSportsDB tier caps its timeline at ~5 events, so flag when the
  // goals shown don't add up to the final score (incomplete fallback data).
  const totalScore = (d.home_score || 0) + (d.away_score || 0);
  const partial = d.events_source !== "apifootball" && totalScore > goals.length;

  const live = d.status === "live";

  return `
    ${live ? '<div class="wc-detail-live">● EN JUEGO</div>' : ""}
    <div class="wc-detail-score">
      <span class="wc-detail-team">${d.home}${wcFlag(d.home_flag)}</span>
      <span class="wc-detail-result ${live ? "wc-detail-result--live" : ""}">${score}</span>
      <span class="wc-detail-team">${wcFlag(d.away_flag)}${d.away}</span>
    </div>
    ${goals.length ? `<ul class="wc-detail-list">${goals.map(wcEventRow).join("")}</ul>`
      : '<div class="wc-detail-empty">Sin goles.</div>'}
    ${cards.length ? `<div class="wc-detail-sub">Tarjetas</div>
      <ul class="wc-detail-list wc-detail-list--cards">${cards.map(wcEventRow).join("")}</ul>` : ""}
    ${partial ? '<div class="wc-detail-partial">⚠ Datos parciales — algunos goles o tarjetas no están disponibles.</div>' : ""}
    ${info.length ? `<div class="wc-detail-info">${info.join(" · ")}</div>` : ""}`;
}

// Live summaries keep updating while the sheet is open.
const WC_DETAIL_LIVE_REFRESH_MS = 30000;

async function openWcDetail(matchId, time) {
  const existing = document.getElementById("wc-detail-modal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.id = "wc-detail-modal";
  modal.className = "cal-modal-overlay";
  modal.innerHTML = `
    <div class="cal-modal-sheet wc-detail-sheet">
      <button class="cal-modal-close" aria-label="Cerrar">✕</button>
      <div class="wc-detail-content"><div class="spinner"></div></div>
    </div>`;
  document.body.appendChild(modal);

  let timer = null;
  const close = () => { if (timer) clearInterval(timer); modal.remove(); };
  modal.querySelector(".cal-modal-close").addEventListener("click", close);
  modal.addEventListener("click", e => { if (e.target === modal) close(); });

  const content = modal.querySelector(".wc-detail-content");
  const load = async () => {
    try {
      const res = await fetch(`/api/wc/match/${matchId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      // The detail endpoint doesn't return kickoff time; reuse the value the
      // list card already has so the info line can show it.
      if (time && !data.time) data.time = time;
      content.innerHTML = wcDetailBody(data);
      // Poll only while the match is live; stop once it's finished.
      if (data.status === "live" && !timer) {
        timer = setInterval(load, WC_DETAIL_LIVE_REFRESH_MS);
      } else if (data.status !== "live" && timer) {
        clearInterval(timer);
        timer = null;
      }
    } catch (err) {
      if (!timer) content.innerHTML = `<div class="wc-detail-empty">No se pudo cargar el resumen.<br/><small>${err.message}</small></div>`;
    }
  };
  await load();
}

// Contribute the World Cup's days to the shared day bar and re-render on
// selection. (registerDateSource lives in app.js, which loads first.)
if (typeof registerDateSource === "function") {
  registerDateSource({
    getDays: () => wcDays.map(d => d.date),
    render: () => renderWcMatches(),
  });
}

// Keep fixtures + cuadro live as matches finish (visibility-aware polling).
startWcAutoRefresh();

// Initial load is orchestrated by the page-loader coordinator in index.html so
// all sections reveal together; fetchWorldCup() is not auto-invoked here.
