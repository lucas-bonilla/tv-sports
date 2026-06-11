// FIFA World Cup section: fixtures + results by day, with a collapsible
// group-standings block. Renders inside #sec-wc on the single-page layout.
//
// The day bar carries both past and future days. Selecting a past day shows
// that day's matches *with their final scores* (the season payload already
// includes played results), which is the "recover earlier-day results" need.

const WC_MATCHES_URL = "/api/wc/matches";
const WC_STANDINGS_URL = "/api/wc/standings";

let wcDays = [];                 // [{date, matches}]

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

async function fetchWorldCup() {
  const container = document.getElementById("wc-container");
  container.innerHTML = '<div class="spinner"></div>';

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

    // Standings load independently so a failure there doesn't break fixtures.
    fetchWcStandings();
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

  // Future matches are addable to a calendar; reuse the shared modal.
  container.onclick = e => {
    const card = e.target.closest("[data-wc-event]");
    if (card) openWcCalendar(JSON.parse(decodeURIComponent(card.dataset.wcEvent)));
  };
  container.onkeydown = e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const card = e.target.closest("[data-wc-event]");
    if (card) { e.preventDefault(); openWcCalendar(JSON.parse(decodeURIComponent(card.dataset.wcEvent))); }
  };

  if (typeof buildQuickNav === "function") buildQuickNav();
}

function renderWcMatch(m) {
  const live = m.status === "live";
  const done = m.status === "finished";
  const hasScore = m.home_score !== null && m.away_score !== null;

  // Left column: kickoff time for upcoming, final/live score for played matches —
  // mirrors the TV-schedule card where the time sits to the left of the title.
  const lead = (live || done) && hasScore
    ? `<span class="wc-time-score ${live ? "wc-time-score--live" : ""}">${m.home_score}-${m.away_score}</span>`
    : (m.time || "—");

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

  const winA = done && hasScore && m.home_score > m.away_score;
  const winB = done && hasScore && m.away_score > m.home_score;

  // Single-line title "Home 🏳 - 🏴 Away", with the winner emphasised.
  const title = `<span class="${winA ? "wc-won" : ""}">${m.home}</span>${wcFlag(m.home_flag)}`
    + ` - ${wcFlag(m.away_flag)}<span class="${winB ? "wc-won" : ""}">${m.away}</span>`;

  const competition = Number.isFinite(m.round) ? `Jornada ${m.round}` : "Mundial";

  return `
    <div class="event-card wc-card ${live ? "wc-card--live" : ""} ${done ? "wc-card--done" : ""}"
      ${addable ? `data-wc-event="${payload}" role="button" tabindex="0" title="Añadir al calendario"` : ""}>
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

// Contribute the World Cup's days to the shared day bar and re-render on
// selection. (registerDateSource lives in app.js, which loads first.)
if (typeof registerDateSource === "function") {
  registerDateSource({
    getDays: () => wcDays.map(d => d.date),
    render: () => renderWcMatches(),
  });
}

// Load on page load (single-page layout, no tab gating).
fetchWorldCup();
