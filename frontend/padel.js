// Premier Padel section: live tournament schedule + collapsible season calendar.
// Renders inside #sec-padel on the single-page layout (no tab switching).

const PADEL_TOURNAMENTS_URL = "/api/padel/tournaments";
const PADEL_SCHEDULE_URL = "/api/padel/schedule";

const STATUS_LABEL = { live: "En juego", upcoming: "Próximos", finished: "Finalizados" };
const STATUS_ORDER = ["live", "upcoming", "finished"];

let padelTournaments = [];
let selectedSlug = null; // slug whose schedule is currently shown

// --- Helpers ---

function fmtDateRange(start, end) {
  const opts = { day: "numeric", month: "short" };
  try {
    const s = new Date(start), e = new Date(end);
    return `${s.toLocaleDateString("es-ES", opts)} – ${e.toLocaleDateString("es-ES", { ...opts, year: "numeric" })}`;
  } catch {
    return "";
  }
}

function fmtDayHeader(dateStr) {
  try {
    return new Date(dateStr + "T00:00:00").toLocaleDateString("es-ES", {
      weekday: "long", day: "numeric", month: "long",
    });
  } catch {
    return dateStr;
  }
}

function flagImg(url) {
  return url ? `<img class="padel-flag" src="${url}" alt="" loading="lazy" />` : "";
}

// --- Bootstrapping ---

async function fetchPadelTournaments() {
  const container = document.getElementById("padel-container");
  const bar = document.getElementById("padel-tournament-bar");
  container.innerHTML = '<div class="spinner"></div>';

  try {
    const res = await fetch(PADEL_TOURNAMENTS_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    padelTournaments = data.tournaments || [];

    document.getElementById("padel-updated").textContent =
      `Actualizado: ${new Date(data.fetched_at).toLocaleTimeString("es-ES")}`;

    // Section-level "en directo" tag in the heading.
    const liveTag = document.getElementById("padel-live-tag");
    const liveT = padelTournaments.find(t => t.slug === data.live_slug);
    if (liveT) {
      liveTag.textContent = `🔴 ${liveT.name || liveT.country}`;
      liveTag.hidden = false;
    } else {
      liveTag.hidden = true;
    }

    // Tournament bar: only meaningful when something is live (jump back to its draw).
    bar.innerHTML = data.live_slug
      ? `<button class="filter-chip active" data-slug="${data.live_slug}">🔴 En directo</button>`
      : "";
    bar.querySelectorAll(".filter-chip").forEach(chip => {
      chip.addEventListener("click", () => showPadelSchedule(chip.dataset.slug));
    });

    // Build the collapsible season calendar.
    buildPadelCalendar();

    // Land on the live tournament's draw; otherwise show the calendar inline.
    if (data.live_slug) {
      showPadelSchedule(data.live_slug);
    } else {
      container.innerHTML = '<div class="no-results">No hay ningún torneo en directo ahora mismo. Consulta el calendario abajo. 📅</div>';
    }
  } catch (err) {
    container.innerHTML = `<div class="error-msg">❌ Error al cargar pádel.<br/><small>${err.message}</small></div>`;
  }
}

// --- Season calendar (collapsible) ---

function buildPadelCalendar() {
  const block = document.getElementById("padel-calendar-block");
  const body = document.getElementById("padel-calendar-body");
  const toggle = document.getElementById("padel-calendar-toggle");
  if (!padelTournaments.length) { block.hidden = true; return; }
  block.hidden = false;

  const grouped = { live: [], upcoming: [], finished: [] };
  padelTournaments.forEach(t => (grouped[t.status] || grouped.upcoming).push(t));

  body.innerHTML = STATUS_ORDER.filter(s => grouped[s].length).map(status => {
    const rows = grouped[status].map(t => {
      const clickable = t.status !== "upcoming";
      return `
      <div class="padel-cal-row ${clickable ? "padel-cal-row--clickable" : ""}" ${clickable ? `data-slug="${t.slug}"` : ""}>
        <span class="padel-cal-cat">${t.emoji} ${t.category}</span>
        <span class="padel-cal-name">${flagImg(t.flag_url)}${t.name || t.city || t.country}</span>
        <span class="padel-cal-dates">${fmtDateRange(t.start_date, t.end_date)}</span>
        ${t.status === "live" ? '<span class="padel-live-badge">EN JUEGO</span>' : ""}
      </div>`;
    }).join("");
    return `<div class="padel-cal-group"><div class="padel-cal-group-head">${STATUS_LABEL[status]}</div>${rows}</div>`;
  }).join("");

  // Clicking a (live/finished) tournament loads its draw above and scrolls up to it.
  body.querySelectorAll(".padel-cal-row--clickable").forEach(row => {
    row.addEventListener("click", () => {
      showPadelSchedule(row.dataset.slug);
      document.getElementById("padel-container").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  // Toggle wiring (idempotent — only bind once).
  if (!toggle.dataset.bound) {
    toggle.dataset.bound = "1";
    toggle.addEventListener("click", () => {
      const open = body.hidden;
      body.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      toggle.classList.toggle("open", open);
    });
  }
}

// --- Tournament schedule (order of play) ---

async function showPadelSchedule(slug) {
  const container = document.getElementById("padel-container");
  if (selectedSlug !== slug) container.innerHTML = '<div class="spinner"></div>';
  selectedSlug = slug;

  const tournament = padelTournaments.find(t => t.slug === slug);

  try {
    const res = await fetch(`${PADEL_SCHEDULE_URL}?slug=${encodeURIComponent(slug)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (selectedSlug !== slug) return; // user navigated away while loading

    const title = tournament
      ? `${tournament.emoji} ${tournament.name || tournament.country}${tournament.city ? ` · ${tournament.city}` : ""}`
      : slug;

    if (!data.days.length) {
      container.innerHTML = `<div class="padel-schedule-title">${title}</div><div class="no-results">El cuadro de partidos aún no está disponible.</div>`;
      return;
    }

    const dayBlocks = data.days.map(day => {
      const byCourt = {};
      day.matches.forEach(m => { (byCourt[m.court] ||= []).push(m); });

      const courtBlocks = Object.entries(byCourt).map(([court, matches]) => `
        <div class="sport-group">
          <div class="sport-group-header" data-toggle>
            <span>🎾 ${court}</span><span class="chevron">›</span>
          </div>
          <div class="sport-group-body">
            ${matches.map(renderMatch).join("")}
          </div>
        </div>`).join("");

      return `
        <div class="day-section">
          <div class="day-header">${fmtDayHeader(day.date)}</div>
          ${courtBlocks}
        </div>`;
    }).join("");

    container.innerHTML = `<div class="padel-schedule-title">${title}</div>${dayBlocks}`;

    container.onclick = e => {
      const header = e.target.closest(".sport-group-header[data-toggle]");
      if (header) { header.closest(".sport-group").classList.toggle("collapsed"); return; }

      const card = e.target.closest(".padel-match[data-padel-event]");
      if (card) openPadelCalendar(JSON.parse(decodeURIComponent(card.dataset.padelEvent)));
    };

    container.onkeydown = e => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const card = e.target.closest(".padel-match[data-padel-event]");
      if (card) { e.preventDefault(); openPadelCalendar(JSON.parse(decodeURIComponent(card.dataset.padelEvent))); }
    };
  } catch (err) {
    container.innerHTML = `<div class="error-msg">❌ Error al cargar el cuadro.<br/><small>${err.message}</small></div>`;
  }
}

function renderMatch(m) {
  const live = m.status === "P";
  const done = m.status === "F" || m.winner;

  // Padel runs matches back-to-back, so show the order-of-play slot
  // ("Desde 11:00" / "No antes de 19:00" / "A continuación") rather than a fake
  // clock time. Live/finished override it with a clear status word.
  const slotLabel = live ? "En juego" : done ? "Finalizado" : (m.slot || "");
  const slotClass = live ? "padel-match-live" : done ? "padel-slot--done" : "padel-slot";

  // Only upcoming matches with a known court start can be added to a calendar
  // (a finished/live match makes no sense to schedule, and "A continuación"
  // has no clock time to anchor the entry).
  const addable = !live && !done && !!m.court_start && !!m.date;
  const payload = addable ? encodeURIComponent(JSON.stringify(m)) : "";

  const tags = [
    m.round ? `<span class="event-competition">${m.round}</span>` : "",
    m.draw_type ? `<span class="padel-draw">${m.draw_type}</span>` : "",
  ].join("");

  const teamRow = (team, score, side) => {
    const won = m.winner === side;
    return `<div class="padel-team ${won ? "padel-team--won" : ""}"><span class="padel-team-name">${team}</span><span class="padel-team-score">${score || ""}</span></div>`;
  };

  return `
    <div class="padel-match ${live ? "padel-match--live" : ""} ${done ? "padel-match--done" : ""} ${addable ? "padel-match--addable" : ""}"
      ${addable ? `data-padel-event="${payload}" role="button" tabindex="0" title="Añadir al calendario"` : ""}>
      <div class="padel-match-head">
        <span class="${slotClass}">${live ? "● " : ""}${slotLabel}</span>
        ${tags}
        ${addable ? '<span class="padel-cal-add">📅</span>' : ""}
      </div>
      <div class="padel-match-teams">
        ${teamRow(m.team_a, m.score_a, "a")}
        ${teamRow(m.team_b, m.score_b, "b")}
      </div>
    </div>`;
}

// Map a padel match onto the shape openCalendarModal() (in app.js) expects, then
// reuse that single modal so padel behaves exactly like the TV cards.
// parseEventDate() in app.js wants a Spanish text date ("jueves 13 de marzo"),
// not the ISO "2026-06-03" the padel API returns — so build that explicitly.
const MONTHS_ES_NAMES = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

function padelEventForCalendar(m) {
  const [y, mo, d] = (m.date || "").split("-").map(Number);
  if (!y || !mo || !d) return null;
  const dateText = `padel ${d} de ${MONTHS_ES_NAMES[mo - 1]}`; // parser reads parts[1]=day, parts[3]=month
  const tournament = padelTournaments.find(t => t.slug === selectedSlug);
  return {
    emoji: "🎾",
    match: `${m.team_a} vs ${m.team_b}`,
    sport: "Pádel",
    date: dateText,
    time: m.court_start,                       // court start-of-play (HH:MM)
    competition: [tournament?.name || tournament?.country, m.round].filter(Boolean).join(" · "),
    channel: m.court ? `Pista: ${m.court}` : "",
  };
}

function openPadelCalendar(m) {
  const event = padelEventForCalendar(m);
  if (event && typeof openCalendarModal === "function") openCalendarModal(event);
}

// Load padel immediately on page load (single-page layout, no tab gating).
fetchPadelTournaments();
