const API_URL = "/api/events";

const DEFAULT_ORDER = ["Futbol", "Tenis", "Formula 1", "NBA"];
const STORAGE_KEY = "sports-tv-filter-order";

let allEvents = [];
let activeFilter = "Todos";
let activeDateFilter = "Hoy";

function getSavedOrder() {
  try {
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
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    allEvents = data.events;
    document.getElementById("date-label").textContent = data.date;
    document.getElementById("last-updated").textContent =
      `Actualizado: ${new Date(data.scraped_at).toLocaleTimeString("es-ES")}`;

    buildDateFilters(allEvents, data.date);
    buildFilters(allEvents);
    renderEvents(allEvents, activeFilter, activeDateFilter);
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
  renderEvents(allEvents, activeFilter, activeDateFilter);
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
  if (chip.dataset.sport === "Todos") return;

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
    renderEvents(allEvents, activeFilter, activeDateFilter);
  }

  touchSrcEl.classList.remove("dragging");
  document.querySelectorAll(".filter-chip").forEach(c => c.classList.remove("drag-over"));
  if (touchClone) { touchClone.remove(); touchClone = null; }
  touchSrcEl = null;
}

// --- Date filter ---

function buildDateFilters(events, todayDate) {
  const bar = document.getElementById("date-bar");
  const dates = [...new Set(events.map(e => e.date))];

  const labels = dates.map(d => {
    if (d === todayDate) return { label: "Hoy", value: d };
    // Extract just "Viernes 13" style short label
    const parts = d.split(" ");
    const short = parts.slice(0, 2).join(" ");
    return { label: short, value: d };
  });

  const all = [{ label: "Todos", value: "Todos" }, ...labels];

  bar.innerHTML = all.map(({ label, value }) =>
    `<button class="filter-chip ${label === activeDateFilter ? "active" : ""}" data-date="${value}">${label}</button>`
  ).join("");

  bar.querySelectorAll(".filter-chip").forEach(btn => {
    btn.addEventListener("click", () => {
      activeDateFilter = btn.textContent.trim();
      bar.querySelectorAll(".filter-chip").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      renderEvents(allEvents, activeFilter, activeDateFilter);
    });
  });
}

// --- Build filters ---

function buildFilters(events) {
  const bar = document.getElementById("filter-bar");
  const sportNames = [...new Set(events.map(e => e.sport))];
  const sorted = sortSports(sportNames);
  const sports = ["Todos", ...sorted];

  bar.innerHTML = sports.map(sport => {
    const draggable = sport !== "Todos" ? 'draggable="true"' : "";
    return `<button class="filter-chip ${sport === activeFilter ? "active" : ""}" data-sport="${sport}" ${draggable}>
      ${sport}
    </button>`;
  }).join("");

  bar.querySelectorAll(".filter-chip").forEach(btn => {
    btn.addEventListener("click", () => {
      activeFilter = btn.dataset.sport;
      bar.querySelectorAll(".filter-chip").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      renderEvents(allEvents, activeFilter, activeDateFilter);
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
    <div class="sport-group">
      <div class="sport-group-header">${items[0].emoji} ${sport}</div>
      ${items.map(e => `
        <div class="event-card">
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
    </div>`;
  }).join("");
}

function renderEvents(events, sportFilter) {
  const container = document.getElementById("events-container");

  // Resolve date filter value from label back to actual date string
  const dateBar = document.getElementById("date-bar");
  const activeDateBtn = dateBar.querySelector(".filter-chip.active");
  const activeDateValue = activeDateBtn ? activeDateBtn.dataset.date : "Todos";

  let filtered = sportFilter === "Todos" ? events : events.filter(e => e.sport === sportFilter);
  if (activeDateValue !== "Todos") {
    filtered = filtered.filter(e => e.date === activeDateValue);
  }

  if (filtered.length === 0) {
    container.innerHTML = '<div class="no-results">No hay eventos para este filtro.</div>';
    return;
  }

  if (activeDateValue === "Todos") {
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
}

document.getElementById("refresh-btn").addEventListener("click", fetchEvents);

// Register service worker for PWA
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(console.error);
  });
}

fetchEvents();
