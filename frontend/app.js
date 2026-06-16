const API_URL = "/api/events";

const DEFAULT_ORDER = ["Fútbol", "Tenis", "Fórmula 1", "Baloncesto"];
// Bump this when DEFAULT_ORDER changes to discard incompatible saved orders.
const ORDER_VERSION = "2";
const STORAGE_KEY = "sports-tv-filter-order";
const ORDER_VERSION_KEY = "sports-tv-filter-order-version";

let allEvents = [];
let activeFilter = null; // null = no sport selected → show all sports

// --- Shared day filter (coordinates TV + World Cup) ---
//
// One day bar at the top of the page drives every section. Days are keyed by
// ISO date (YYYY-MM-DD) so the Spanish TV dates and the ISO World Cup dates can
// share a single selection. Each section registers the ISO days it has and a
// re-render callback; selecting a day re-renders all of them.

const MONTHS_ES_NUM = {
  enero: 1, febrero: 2, marzo: 3, abril: 4, mayo: 5, junio: 6,
  julio: 7, agosto: 8, septiembre: 9, octubre: 10, noviembre: 11, diciembre: 12,
};

// "Lunes 8 de Junio de 2026" -> "2026-06-08"; returns "" if unparseable.
function tvDateToISO(dateStr) {
  const parts = (dateStr || "").toLowerCase().split(" ");
  const day = parseInt(parts[1], 10);
  const month = MONTHS_ES_NUM[parts[3]];
  const year = parseInt(parts[5], 10) || new Date().getFullYear();
  if (!day || !month) return "";
  const pad = n => String(n).padStart(2, "0");
  return `${year}-${pad(month)}-${pad(day)}`;
}

function todayISO() {
  const n = new Date();
  const pad = x => String(x).padStart(2, "0");
  return `${n.getFullYear()}-${pad(n.getMonth() + 1)}-${pad(n.getDate())}`;
}

let globalDateISO = null; // null = no day selected → every section shows all days
const dateSources = []; // [{ getDays: () => isoDate[], render: () => void }]

function registerDateSource(source) {
  dateSources.push(source);
}

let globalDateDefaulted = false; // only auto-pick the default day once

function buildGlobalDateBar() {
  const bar = document.getElementById("global-date-bar");
  if (!bar) return;

  // Union of every section's ISO days, sorted chronologically.
  const days = [...new Set(dateSources.flatMap(s => s.getDays()))].filter(Boolean).sort();
  if (!days.length) { bar.innerHTML = ""; return; }

  const today = todayISO();

  // On first populated build, land on today (or the next available day) so the
  // page opens focused, just like the old per-section default.
  if (!globalDateDefaulted) {
    globalDateDefaulted = true;
    globalDateISO = days.includes(today) ? today : (days.find(d => d > today) || null);
    dateSources.forEach(s => s.render());
  }
  bar.innerHTML = days.map(iso => {
    const label = iso === today
      ? "Hoy"
      : new Date(iso + "T00:00:00").toLocaleDateString("es-ES", { weekday: "short", day: "numeric" });
    return `<button class="filter-chip ${iso === globalDateISO ? "active" : ""}" data-date="${iso}">${label}</button>`;
  }).join("");

  bar.querySelectorAll(".filter-chip").forEach(btn => {
    btn.addEventListener("click", () => {
      // Tapping the active chip deselects it → all days.
      globalDateISO = btn.dataset.date === globalDateISO ? null : btn.dataset.date;
      bar.querySelectorAll(".filter-chip").forEach(b => b.classList.remove("active"));
      if (globalDateISO !== null) btn.classList.add("active");
      dateSources.forEach(s => s.render());
    });
  });
}

function getSavedOrder() {
  try {
    // Discard any saved order from a previous DEFAULT_ORDER version so users
    // pick up the new default instead of a stale dragged order.
    if (localStorage.getItem(ORDER_VERSION_KEY) !== ORDER_VERSION) {
      localStorage.removeItem(STORAGE_KEY);
      localStorage.setItem(ORDER_VERSION_KEY, ORDER_VERSION);
      return null;
    }
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? JSON.parse(saved) : null;
  } catch { return null; }
}

function saveOrder(order) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(order));
}

function sortSports(sportNames) {
  const saved = getSavedOrder();
  const priority = saved || DEFAULT_ORDER;

  const prioritized = [];
  const rest = [];

  for (const name of sportNames) {
    const idx = priority.findIndex(p => name.toLowerCase().includes(p.toLowerCase()));
    if (idx !== -1) {
      prioritized.push({ name, idx });
    } else {
      rest.push(name);
    }
  }

  prioritized.sort((a, b) => a.idx - b.idx);
  rest.sort((a, b) => a.localeCompare(b, "es"));

  return [...prioritized.map(p => p.name), ...rest];
}

async function fetchEvents() {
  const container = document.getElementById("events-container");
  const btn = document.getElementById("refresh-btn");

  btn.classList.add("spinning");
  container.innerHTML = '<div class="spinner"></div>';

  try {
    const res = await fetch(API_URL);
    const isOffline = res.status === 503;
    if (!res.ok && !isOffline) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (isOffline && (!data.events || data.events.length === 0)) {
      container.innerHTML = '<div class="error-msg">Sin conexión y sin datos guardados.</div>';
      return;
    }

    allEvents = data.events;
    // Cache each event's ISO date so the shared day filter can match it.
    allEvents.forEach(e => { e._iso = tvDateToISO(e.date); });
    document.getElementById("date-label").textContent = data.date;
    document.getElementById("last-updated").textContent = isOffline
      ? `Sin conexión — mostrando programación guardada (${new Date(data.scraped_at).toLocaleTimeString("es-ES")})`
      : `Actualizado: ${new Date(data.scraped_at).toLocaleTimeString("es-ES")}`;

    buildFilters(allEvents);
    buildGlobalDateBar();
    renderEvents(allEvents, activeFilter);
  } catch (err) {
    container.innerHTML = `<div class="error-msg">\u274C Error al cargar datos.<br/><small>${err.message}</small></div>`;
  } finally {
    btn.classList.remove("spinning");
  }
}

// --- Drag-to-reorder ---

let dragSrcEl = null;

function handleDragStart(e) {
  dragSrcEl = this;
  this.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", this.dataset.sport);
}

function handleDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  this.classList.add("drag-over");
}

function handleDragLeave() {
  this.classList.remove("drag-over");
}

function handleDrop(e) {
  e.preventDefault();
  this.classList.remove("drag-over");
  if (dragSrcEl === this) return;

  const bar = document.getElementById("filter-bar");
  const chips = [...bar.querySelectorAll(".filter-chip[draggable]")];
  const fromIdx = chips.indexOf(dragSrcEl);
  const toIdx = chips.indexOf(this);

  if (fromIdx < toIdx) {
    this.after(dragSrcEl);
  } else {
    this.before(dragSrcEl);
  }

  // Save new order
  const newOrder = [...bar.querySelectorAll(".filter-chip[draggable]")]
    .map(c => c.dataset.sport);
  saveOrder(newOrder);

  // Re-render events in new sport order
  renderEvents(allEvents, activeFilter);
}

function handleDragEnd() {
  this.classList.remove("dragging");
  document.querySelectorAll(".filter-chip").forEach(c => c.classList.remove("drag-over"));
}

// --- Touch drag support ---

let touchSrcEl = null;
let touchClone = null;
let touchStartX = 0;

function handleTouchStart(e) {
  const chip = e.currentTarget;
  touchSrcEl = chip;
  touchStartX = e.touches[0].clientX;

  // Create floating clone
  touchClone = chip.cloneNode(true);
  touchClone.classList.add("drag-clone");
  const rect = chip.getBoundingClientRect();
  touchClone.style.left = rect.left + "px";
  touchClone.style.top = rect.top + "px";
  document.body.appendChild(touchClone);

  chip.classList.add("dragging");
}

function handleTouchMove(e) {
  if (!touchSrcEl) return;
  e.preventDefault();

  const touch = e.touches[0];
  if (touchClone) {
    touchClone.style.left = (touch.clientX - touchClone.offsetWidth / 2) + "px";
    touchClone.style.top = (touch.clientY - touchClone.offsetHeight / 2) + "px";
  }

  // Find chip under finger
  const el = document.elementFromPoint(touch.clientX, touch.clientY);
  const target = el?.closest?.(".filter-chip[draggable]");

  document.querySelectorAll(".filter-chip").forEach(c => c.classList.remove("drag-over"));
  if (target && target !== touchSrcEl) {
    target.classList.add("drag-over");
  }
}

function handleTouchEnd(e) {
  if (!touchSrcEl) return;

  const touch = e.changedTouches[0];
  const el = document.elementFromPoint(touch.clientX, touch.clientY);
  const target = el?.closest?.(".filter-chip[draggable]");

  if (target && target !== touchSrcEl) {
    const bar = document.getElementById("filter-bar");
    const chips = [...bar.querySelectorAll(".filter-chip[draggable]")];
    const fromIdx = chips.indexOf(touchSrcEl);
    const toIdx = chips.indexOf(target);

    if (fromIdx < toIdx) {
      target.after(touchSrcEl);
    } else {
      target.before(touchSrcEl);
    }

    const newOrder = [...bar.querySelectorAll(".filter-chip[draggable]")]
      .map(c => c.dataset.sport);
    saveOrder(newOrder);
    renderEvents(allEvents, activeFilter);
  }

  touchSrcEl.classList.remove("dragging");
  document.querySelectorAll(".filter-chip").forEach(c => c.classList.remove("drag-over"));
  if (touchClone) { touchClone.remove(); touchClone = null; }
  touchSrcEl = null;
}


// --- Build filters ---

function buildFilters(events) {
  const bar = document.getElementById("filter-bar");
  const sportNames = [...new Set(events.map(e => e.sport))];
  const sorted = sortSports(sportNames);
  bar.innerHTML = sorted.map(sport => {
    return `<button class="filter-chip ${sport === activeFilter ? "active" : ""}" data-sport="${sport}" draggable="true">
      ${sport}
    </button>`;
  }).join("");

  bar.querySelectorAll(".filter-chip").forEach(btn => {
    btn.addEventListener("click", () => {
      // Tapping the active chip deselects it → back to showing all sports
      activeFilter = btn.dataset.sport === activeFilter ? null : btn.dataset.sport;
      bar.querySelectorAll(".filter-chip").forEach(b => b.classList.remove("active"));
      if (activeFilter !== null) btn.classList.add("active");
      renderEvents(allEvents, activeFilter);
    });
  });

  // Desktop drag events
  bar.querySelectorAll(".filter-chip[draggable]").forEach(chip => {
    chip.addEventListener("dragstart", handleDragStart);
    chip.addEventListener("dragover", handleDragOver);
    chip.addEventListener("dragleave", handleDragLeave);
    chip.addEventListener("drop", handleDrop);
    chip.addEventListener("dragend", handleDragEnd);
    // Touch events
    chip.addEventListener("touchstart", handleTouchStart, { passive: false });
    chip.addEventListener("touchmove", handleTouchMove, { passive: false });
    chip.addEventListener("touchend", handleTouchEnd);
  });
}

function sportId(sport) {
  return "sport-" + sport.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "");
}

function renderSportGroups(events) {
  const bar = document.getElementById("filter-bar");
  const chipOrder = [...bar.querySelectorAll(".filter-chip[draggable]")].map(c => c.dataset.sport);

  const grouped = {};
  events.forEach(e => {
    if (!grouped[e.sport]) grouped[e.sport] = [];
    grouped[e.sport].push(e);
  });

  const orderedSports = chipOrder.filter(s => grouped[s]);
  Object.keys(grouped).forEach(s => { if (!orderedSports.includes(s)) orderedSports.push(s); });

  return orderedSports.map(sport => {
    const items = grouped[sport];
    return `
    <div class="sport-group" id="${sportId(sport)}">
      <div class="sport-group-header" data-toggle>
        <span>${items[0].emoji} ${sport}</span>
        <span class="chevron">›</span>
      </div>
      <div class="sport-group-body">
        ${items.map(e => `
          <div class="event-card" data-event="${encodeURIComponent(JSON.stringify(e))}">
            <div class="event-time">${e.time}</div>
            <div class="event-body">
              <div class="event-match">${e.match}</div>
              <div class="event-meta">
                <span class="event-competition">${e.competition}</span>
                <span class="event-channel">${e.channel}</span>
              </div>
            </div>
          </div>
        `).join("")}
      </div>
    </div>`;
  }).join("");
}

function renderEvents(events, sportFilter) {
  const container = document.getElementById("events-container");

  const dateValue = globalDateISO; // null = show all days (shared filter)

  let filtered = !sportFilter ? events : events.filter(e => e.sport === sportFilter);
  if (dateValue) {
    filtered = filtered.filter(e => e._iso === dateValue);
  }

  if (filtered.length === 0) {
    container.innerHTML = '<div class="no-results">No hay nada en TV para este filtro.</div>';
    buildQuickNav();
    return;
  }

  if (!dateValue) {
    // Group by day, then by sport within each day
    const days = [...new Set(events.map(e => e.date))];
    container.innerHTML = days.map(date => {
      const dayEvents = filtered.filter(e => e.date === date);
      if (dayEvents.length === 0) return "";
      return `
        <div class="day-section">
          <div class="day-header">${date}</div>
          ${renderSportGroups(dayEvents)}
        </div>`;
    }).join("");
  } else {
    container.innerHTML = renderSportGroups(filtered);
  }

  // Collapse toggle
  container.onclick = e => {
    const header = e.target.closest(".sport-group-header[data-toggle]");
    if (!header) return;
    header.closest(".sport-group").classList.toggle("collapsed");
  };

  buildQuickNav();
}

function buildQuickNav() {
  const panel = document.getElementById("quick-nav-panel");

  const daySections = [...document.querySelectorAll(".day-section")];
  const sportGroups = [...document.querySelectorAll(".sport-group")];

  let items = [];

  if (daySections.length > 0) {
    // All-days mode: navigate by day
    daySections.forEach(section => {
      const header = section.querySelector(".day-header");
      if (header) items.push({ label: header.textContent.trim(), el: section });
    });
  } else {
    // Single day: navigate by sport
    sportGroups.forEach(g => {
      const span = g.querySelector(".sport-group-header span");
      if (span) items.push({ label: span.textContent.trim(), el: g });
    });
  }

  // Always offer jumps to the World Cup and Padel sections at the bottom.
  const wcSection = document.getElementById("sec-wc");
  if (wcSection) {
    items.push({ label: "🏆 Mundial", el: wcSection });
  }

  const padelSection = document.getElementById("sec-padel");
  if (padelSection) {
    items.push({ label: "🎾 Pádel", el: padelSection });
  }

  if (items.length <= 1) {
    panel.innerHTML = "";
    return;
  }

  panel.innerHTML = items.map((item, i) =>
    `<div class="quick-nav-item" data-idx="${i}">${item.label}</div>`
  ).join("");

  panel.querySelectorAll(".quick-nav-item").forEach(btn => {
    btn.addEventListener("click", () => {
      items[+btn.dataset.idx].el.scrollIntoView({ behavior: "smooth", block: "start" });
      panel.classList.remove("open");
    });
  });
}

// --- Calendar modal ---

const MONTHS_ES = {
  "enero": 0, "febrero": 1, "marzo": 2, "abril": 3, "mayo": 4, "junio": 5,
  "julio": 6, "agosto": 7, "septiembre": 8, "octubre": 9, "noviembre": 10, "diciembre": 11
};

function parseEventDate(dateStr, timeStr) {
  // dateStr: "Jueves 13 de Marzo" — extract day number and month name
  // timeStr: "20:30"
  const parts = dateStr.toLowerCase().split(" ");
  const day = parseInt(parts[1] || parts[0], 10);
  const monthName = parts[3] || parts[2] || "";
  const month = MONTHS_ES[monthName];
  if (isNaN(day) || month === undefined) return null;

  const [hours, minutes] = (timeStr || "00:00").split(":").map(Number);
  const year = new Date().getFullYear();
  const d = new Date(year, month, day, hours, minutes, 0);
  // If the date is in the past by more than a month, assume next year
  if (d < new Date() - 30 * 24 * 3600 * 1000) d.setFullYear(year + 1);
  return d;
}

function toICSDate(d) {
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}T${pad(d.getHours())}${pad(d.getMinutes())}00`;
}

function buildICS(event) {
  const start = parseEventDate(event.date, event.time);
  if (!start) return null;
  const end = new Date(start.getTime() + 2 * 60 * 60 * 1000);
  const uid = `${Date.now()}@sports-tv`;
  const summary = event.match || event.sport;
  const description = [event.competition, event.channel].filter(Boolean).join(" · ");
  return [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Sports TV//ES",
    "BEGIN:VEVENT",
    `UID:${uid}`,
    `DTSTART:${toICSDate(start)}`,
    `DTEND:${toICSDate(end)}`,
    `SUMMARY:${summary}`,
    `DESCRIPTION:${description}`,
    "END:VEVENT",
    "END:VCALENDAR"
  ].join("\r\n");
}

function calendarLinks(event, start) {
  const pad = n => String(n).padStart(2, "0");
  const fmt = d =>
    `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}00Z`;
  const end = new Date(start.getTime() + 2 * 60 * 60 * 1000);
  const title = encodeURIComponent(event.match || event.sport);
  const details = encodeURIComponent([event.competition, event.channel].filter(Boolean).join(" · "));
  const google = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&dates=${fmt(start)}/${fmt(end)}&details=${details}`;
  return { google };
}

function openCalendarModal(event) {
  const existing = document.getElementById("cal-modal");
  if (existing) existing.remove();

  const ics = buildICS(event);
  const start = parseEventDate(event.date, event.time);
  const timeLabel = start
    ? start.toLocaleString("es-ES", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })
    : `${event.date} ${event.time}`;
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  const links = start ? calendarLinks(event, start) : null;

  const icsUrl = ics ? (() => {
    const p = new URLSearchParams({
      summary: event.match || event.sport,
      date: event.date,
      time: event.time,
      description: [event.competition, event.channel].filter(Boolean).join(" · "),
    });
    return `/api/ics?${p.toString()}`;
  })() : null;

  const calButtons = ics ? (isIOS
    ? `<a class="cal-modal-btn" href="${icsUrl}">Añadir al calendario</a>`
    : `<div class="cal-modal-options">
        <a class="cal-modal-btn cal-modal-btn--outline" href="${links.google}" target="_blank" rel="noopener">Google Calendar</a>
        <button class="cal-modal-btn cal-modal-btn--ghost" id="cal-add-btn">⬇ Descargar .ics</button>
      </div>`)
    : `<div class="cal-modal-error">No se pudo determinar la fecha del evento.</div>`;

  const modal = document.createElement("div");
  modal.id = "cal-modal";
  modal.className = "cal-modal-overlay";
  modal.innerHTML = `
    <div class="cal-modal-sheet">
      <button class="cal-modal-close" aria-label="Cerrar">✕</button>
      <div class="cal-modal-emoji">${event.emoji}</div>
      <div class="cal-modal-match">${event.match}</div>
      <div class="cal-modal-meta">${event.competition ? `<span class="event-competition">${event.competition}</span>` : ""} ${event.channel || ""}</div>
      <div class="cal-modal-time">📅 ${timeLabel}</div>
      ${calButtons}
    </div>`;

  document.body.appendChild(modal);

  modal.querySelector(".cal-modal-close").addEventListener("click", () => modal.remove());
  modal.addEventListener("click", e => { if (e.target === modal) modal.remove(); });

  const addBtn = modal.querySelector("#cal-add-btn");
  if (addBtn) {
    addBtn.addEventListener("click", () => {
      const blob = new Blob([ics], { type: "text/calendar;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "evento.ics";
      a.click();
      URL.revokeObjectURL(url);
      modal.remove();
    });
  }
}

// Card click → calendar modal
document.getElementById("events-container").addEventListener("click", e => {
  const card = e.target.closest(".event-card[data-event]");
  if (!card) return;
  const event = JSON.parse(decodeURIComponent(card.dataset.event));
  openCalendarModal(event);
});

document.getElementById("refresh-btn").addEventListener("click", () => {
  fetchEvents();
  if (typeof fetchWorldCup === "function") fetchWorldCup();
  if (typeof fetchPadelTournaments === "function") fetchPadelTournaments();
});

// Quick-nav FAB
const quickNavBtn = document.getElementById("quick-nav-btn");
const quickNavPanel = document.getElementById("quick-nav-panel");
quickNavBtn.addEventListener("click", e => {
  e.stopPropagation();
  quickNavPanel.classList.toggle("open");
});
document.addEventListener("click", () => quickNavPanel.classList.remove("open"));

// Back to top
const backToTop = document.getElementById("back-to-top");
window.addEventListener("scroll", () => {
  backToTop.classList.toggle("visible", window.scrollY > 300);
}, { passive: true });
backToTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));

// Register service worker for PWA
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(console.error);
  });
}

// The TV section contributes its ISO days to the shared bar and re-renders on
// day selection. (World Cup registers itself from worldcup.js.)
registerDateSource({
  getDays: () => [...new Set(allEvents.map(e => e._iso))],
  render: () => renderEvents(allEvents, activeFilter),
});

// Initial load is orchestrated by the page-loader coordinator in index.html so
// all sections reveal together; fetchEvents() is not auto-invoked here.
