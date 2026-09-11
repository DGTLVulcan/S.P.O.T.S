// The come-up rows, as a table you pick a range off.
//
// Built in one place and used by two panels, the flight simulation and the
// scope picture, so both show the same rows.
(function () {
  const CELLS = [
    (row) => `${row.distance_m} m`,
    (row) => `${row.drop_cm} cm`,
    (row, unit) => `${row.elevation.toFixed(2)} ${unit}`,
    (row) => (row.elevation_clicks === null ? "—" : row.elevation_clicks),
    (row, unit) => `${row.windage.toFixed(2)} ${unit}`,
    (row) => (row.windage_clicks === null ? "—" : row.windage_clicks),
    (row) => `${row.velocity_fps} fps`,
    (row) => `${row.energy_j} J`,
    (row) => `${row.time_s.toFixed(2)} s`,
    (row) => row.mach.toFixed(2),
  ];

  // Fills the table's tbody. Returns whether there was anything to draw,
  // so the caller can word its own empty state.
  function render(table, card, onPick) {
    if (!table) return false;
    const body = table.querySelector("tbody");
    if (!body) return false;
    body.textContent = "";
    const rows = (card && card.rows) || [];
    if (!rows.length) return false;
    const unit = String(card.unit || "mrad").toUpperCase();
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      tr.className = "sim-row" + (row.transonic ? " is-transonic" : "");
      tr.dataset.distance = row.distance_m;
      CELLS.forEach((cell) => {
        const td = document.createElement("td");
        td.textContent = cell(row, unit);
        tr.append(td);
      });
      tr.addEventListener("click", () => onPick(Number(tr.dataset.distance)));
      body.append(tr);
    });
    return true;
  }

  function mark(table, distance) {
    if (!table) return;
    table.querySelectorAll("tbody tr").forEach((tr) => {
      tr.classList.toggle("is-chosen", Number(tr.dataset.distance) === distance);
    });
  }

  // Which row a panel lands on after a fresh solution: the one it was
  // showing if that range survived, otherwise the longest shot on the card.
  function keep(card, wanted) {
    const rows = (card && card.rows) || [];
    if (rows.some((row) => row.distance_m === wanted)) return wanted;
    return rows.length ? rows[rows.length - 1].distance_m : null;
  }

  function rowFor(card, distance) {
    return ((card && card.rows) || [])
      .find((row) => row.distance_m === distance) || null;
  }

  window.SPOTS_PICKER = { render, mark, keep, rowFor };
})();
