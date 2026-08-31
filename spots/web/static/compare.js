// Compare sessions: stats side by side, groups overlaid, and the kit and
// conditions each recorded at the time (not the current equipment list).
(function () {
  const root = document.getElementById("compare-root");
  if (!root) return;
  const checks = Array.from(document.querySelectorAll(".compare-check"));

  // Distinct hues per session, assigned by position and never cycled -- the
  // same palette the shot markers use elsewhere.
  const SERIES = ["#2a78d6", "#e34948", "#0ca30c", "#fab219", "#8b5cf6", "#0891b2"];

  function escapeHtml(text) {
    return String(text === null || text === undefined ? "" : text).replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  const fmt = (n, digits) =>
    typeof n === "number" ? n.toFixed(digits === undefined ? 2 : digits) : "-";

  function kitLine(session, kind) {
    const kit = (session.equipment || {})[kind];
    if (!kit) return "-";
    const specs = kit.specs || {};
    const detail = Object.keys(specs).length
      ? Object.values(specs).slice(0, 3).join(" · ")
      : "";
    return `${escapeHtml(kit.name)}${detail ? `<div class="hint">${escapeHtml(detail)}</div>` : ""}`;
  }

  // The overlay needs one scale for every session, or the groups can't be
  // compared by eye -- the tightest would fill the same box as the widest.
  function renderOverlay(sessions) {
    const svg = document.getElementById("compare-diagram");
    if (!svg) return;
    const ns = "http://www.w3.org/2000/svg";
    svg.innerHTML = "";
    const size = 260, cx = size / 2, cy = size / 2, plotRadius = size / 2 - 24;

    let maxAbs = 0.5;
    sessions.forEach((s) =>
      s.shots.forEach((shot) => {
        maxAbs = Math.max(maxAbs, Math.abs(shot.x_units), Math.abs(shot.y_units));
      })
    );
    const scale = plotRadius / (maxAbs * 1.15);
    const toSvg = (x, y) => [cx + x * scale, cy - y * scale];

    [0.33, 0.66, 1.0].forEach((frac) => {
      const ring = document.createElementNS(ns, "circle");
      ring.setAttribute("cx", cx);
      ring.setAttribute("cy", cy);
      ring.setAttribute("r", plotRadius * frac);
      ring.setAttribute("class", "ring");
      svg.appendChild(ring);
    });
    [["x1", 12, "x2", size - 12, "y1", cy, "y2", cy], ["y1", 12, "y2", size - 12, "x1", cx, "x2", cx]]
      .forEach((attrs) => {
        const line = document.createElementNS(ns, "line");
        for (let i = 0; i < attrs.length; i += 2) line.setAttribute(attrs[i], attrs[i + 1]);
        line.setAttribute("class", "crosshair");
        svg.appendChild(line);
      });

    sessions.forEach((session, index) => {
      const colour = SERIES[index % SERIES.length];
      session.shots.forEach((shot) => {
        const [x, y] = toSvg(shot.x_units, shot.y_units);
        const dot = document.createElementNS(ns, "circle");
        dot.setAttribute("cx", x);
        dot.setAttribute("cy", y);
        dot.setAttribute("r", 4.5);
        dot.setAttribute("fill", shot.excluded ? "none" : colour);
        dot.setAttribute("stroke", colour);
        dot.setAttribute("stroke-width", shot.excluded ? 1.5 : 1);
        if (shot.excluded) dot.setAttribute("stroke-dasharray", "2 2");
        svg.appendChild(dot);
      });
      if (session.center) {
        const [x, y] = toSvg(session.center[0], session.center[1]);
        const marker = document.createElementNS(ns, "circle");
        marker.setAttribute("cx", x);
        marker.setAttribute("cy", y);
        marker.setAttribute("r", 7);
        marker.setAttribute("fill", "none");
        marker.setAttribute("stroke", colour);
        marker.setAttribute("stroke-width", 2);
        svg.appendChild(marker);
      }
    });
  }

  function render(sessions) {
    if (sessions.length < 2) {
      root.innerHTML =
        '<p class="empty-state">Tick two or more sessions on the left to compare them.</p>';
      return;
    }
    // Tightest group first: the reason you're comparing is to find it.
    const ranked = sessions
      .slice()
      .sort((a, b) => (a.extreme_spread ?? Infinity) - (b.extreme_spread ?? Infinity));
    const unit = ranked[0].unit_name;

    const row = (label, cell) =>
      `<tr><th>${label}</th>${ranked.map(cell).join("")}</tr>`;

    root.innerHTML = `
      <div class="compare-grid">
        <div class="table-scroll">
          <table class="compare-table">
            <thead>
              <tr>
                <th></th>
                ${ranked.map((s, i) => `
                  <th>
                    <span class="compare-swatch" style="background:${SERIES[i % SERIES.length]}"></span>
                    ${escapeHtml(s.name)}
                    ${i === 0 ? '<span class="compare-best">best</span>' : ""}
                    <div class="hint">${escapeHtml(s.created_at_str)}</div>
                  </th>`).join("")}
              </tr>
            </thead>
            <tbody>
              ${row("Shots", (s) => `<td>${s.shot_count}</td>`)}
              ${row("Extreme spread", (s) =>
                `<td><strong>${fmt(s.extreme_spread)}</strong> <span class="unit">${escapeHtml(unit)}</span>
                 ${s.extreme_spread_moa !== null ? `<div class="hint">${fmt(s.extreme_spread_moa)} MOA</div>` : ""}</td>`)}
              ${row("Mean radius", (s) => `<td>${fmt(s.mean_radius)} <span class="unit">${escapeHtml(unit)}</span></td>`)}
              ${row("Std dev", (s) => `<td>${fmt(s.std_dev)} <span class="unit">${escapeHtml(unit)}</span></td>`)}
              ${row("Distance", (s) => `<td>${s.distance_m ? fmt(s.distance_m, 0) + " m" : "-"}</td>`)}
              ${row("Rifle", (s) => `<td>${kitLine(s, "rifle")}</td>`)}
              ${row("Scope", (s) => `<td>${kitLine(s, "scope")}</td>`)}
              ${row("Ammo", (s) => `<td>${kitLine(s, "ammo")}</td>`)}
              ${row("Conditions", (s) =>
                `<td>${s.conditions_summary ? escapeHtml(s.conditions_summary) : "-"}
                 ${s.conditions && s.conditions.notes ? `<div class="hint">${escapeHtml(s.conditions.notes)}</div>` : ""}</td>`)}
            </tbody>
          </table>
        </div>

        <div class="compare-overlay">
          <h3>Groups overlaid</h3>
          <svg id="compare-diagram" class="target-diagram" viewBox="0 0 260 260" role="img"
               aria-label="Shot groups overlaid"></svg>
          <div class="legend-row">
            ${ranked.map((s, i) =>
              `<span><span class="swatch" style="background:${SERIES[i % SERIES.length]}"></span>${escapeHtml(s.name)}</span>`
            ).join("")}
          </div>
          <p class="hint">All groups share one scale, so their sizes are directly comparable.</p>
        </div>
      </div>`;
    renderOverlay(ranked);
  }

  async function refresh() {
    const ids = checks.filter((c) => c.checked).map((c) => c.value);
    history.replaceState(null, "", ids.length ? `?ids=${ids.join(",")}` : location.pathname);
    if (ids.length < 2) {
      render([]);
      return;
    }
    try {
      const data = await (await fetch(`/api/compare?ids=${ids.join(",")}`)).json();
      render(data.sessions || []);
    } catch (err) {
      root.innerHTML = '<p class="empty-state">Could not load those sessions.</p>';
    }
  }

  checks.forEach((c) => c.addEventListener("change", refresh));

  // Deep link: /compare?ids=3,5 arrives with them already ticked.
  const preselected = String(window.SPOTS_PRESELECTED || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (preselected.length) {
    checks.forEach((c) => {
      c.checked = preselected.includes(c.value);
    });
    refresh();
  }
})();
