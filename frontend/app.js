const API_URL = "http://localhost:8000/events";

let allEvents = [];
let activeFilter = "Todos";

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

    buildFilters(allEvents);
    renderEvents(allEvents, activeFilter);
  } catch (err) {
    container.innerHTML = `<div class="error-msg">❌ Error al cargar datos.<br/><small>${err.message}</small></div>`;
  } finally {
    btn.classList.remove("spinning");
  }
}

function buildFilters(events) {
  const bar = document.getElementById("filter-bar");
  const sports = ["Todos", ...new Set(events.map(e => e.sport))];

  bar.innerHTML = sports.map(sport =>
    `<button class="filter-chip ${sport === activeFilter ? "active" : ""}" data-sport="${sport}">
      ${sport}
    </button>`
  ).join("");

  bar.querySelectorAll(".filter-chip").forEach(btn => {
    btn.addEventListener("click", () => {
      activeFilter = btn.dataset.sport;
      bar.querySelectorAll(".filter-chip").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      renderEvents(allEvents, activeFilter);
    });
  });
}

function renderEvents(events, filter) {
  const container = document.getElementById("events-container");
  const filtered = filter === "Todos" ? events : events.filter(e => e.sport === filter);

  if (filtered.length === 0) {
    container.innerHTML = '<div class="no-results">No hay eventos para este filtro.</div>';
    return;
  }

  // Group by sport
  const grouped = {};
  filtered.forEach(e => {
    if (!grouped[e.sport]) grouped[e.sport] = [];
    grouped[e.sport].push(e);
  });

  container.innerHTML = Object.entries(grouped).map(([sport, items]) => `
    <div class="sport-group">
      <div class="sport-group-header">
        ${items[0].emoji} ${sport}
      </div>
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
    </div>
  `).join("");
}

document.getElementById("refresh-btn").addEventListener("click", fetchEvents);

// Register service worker for PWA
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(console.error);
  });
}

fetchEvents();
