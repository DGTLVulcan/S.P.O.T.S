(function () {
  const feed = document.getElementById("feed");
  const statusEl = document.getElementById("status");
  const newTargetBtn = document.getElementById("new-target");
  const calibrateBtn = document.getElementById("calibrate");
  const undoBtn = document.getElementById("undo");
  const badgeCalibration = document.getElementById("badge-calibration");
  const badgeSession = document.getElementById("badge-session");
  const zoomSlider = document.getElementById("zoom-slider");
  const zoomValue = document.getElementById("zoom-value");
  const zoomCenterBtn = document.getElementById("zoom-center-btn");
  const feedSyntheticBtn = document.getElementById("feed-synthetic");
  const feedZcamBtn = document.getElementById("feed-zcam");
  const badgeFeed = document.getElementById("badge-feed");
  const feedHint = document.getElementById("feed-hint");
  const markCenterBtn = document.getElementById("mark-center");
  const distanceInput = document.getElementById("distance-input");

  // Exactly one of these at a time; the feed click handler branches on it.
  let mode = "none"; // "none" | "calibrate" | "zoom-center" | "mark-center"
  let calibrationPoints = [];
  let zoomState = { level: 1.0, center_x: 0.5, center_y: 0.5 };
  let currentFeed = "synthetic";

  function setMode(next) {
    mode = next;
    if (mode !== "calibrate") calibrationPoints = [];
    calibrateBtn.classList.toggle("active", mode === "calibrate");
    zoomCenterBtn.classList.toggle("active", mode === "zoom-center");
    markCenterBtn.classList.toggle("active", mode === "mark-center");
  }

  function setStatus(msg) {
    statusEl.textContent = msg;
  }

  async function postJson(url, body) {
    const resp = await fetch(url, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    return data;
  }

  newTargetBtn.addEventListener("click", async () => {
    try {
      await postJson("/api/session/new");
      setStatus("New target set. Reference frame captured.");
    } catch (err) {
      setStatus("Error: " + err.message);
    }
  });

  undoBtn.addEventListener("click", async () => {
    await postJson("/api/session/undo");
    setStatus("Removed last shot.");
    refreshShots();
  });

  calibrateBtn.addEventListener("click", () => {
    const next = mode === "calibrate" ? "none" : "calibrate";
    setMode(next);
    setStatus(
      next === "calibrate"
        ? "Calibration mode: click two points on the target a known distance apart."
        : "Calibration cancelled."
    );
  });

  zoomCenterBtn.addEventListener("click", () => {
    const next = mode === "zoom-center" ? "none" : "zoom-center";
    setMode(next);
    setStatus(next === "zoom-center" ? "Click on the feed to re-center the zoom." : "Zoom centering cancelled.");
  });

  markCenterBtn.addEventListener("click", () => {
    const next = mode === "mark-center" ? "none" : "mark-center";
    setMode(next);
    setStatus(
      next === "mark-center"
        ? "Click the target's true center on the feed."
        : "Center marking cancelled."
    );
  });

  async function applyZoom(level, centerX, centerY) {
    try {
      const data = await postJson("/api/zoom", { level, center_x: centerX, center_y: centerY });
      zoomState = { level: data.level, center_x: data.center_x, center_y: data.center_y };
      zoomSlider.value = zoomState.level;
      zoomValue.textContent = zoomState.level.toFixed(1) + "x";
      setStatus("Zoom updated -- click New Target, then re-calibrate.");
    } catch (err) {
      setStatus("Zoom error: " + err.message);
    }
  }

  zoomSlider.addEventListener("input", () => {
    zoomValue.textContent = parseFloat(zoomSlider.value).toFixed(1) + "x";
  });
  zoomSlider.addEventListener("change", () => {
    applyZoom(parseFloat(zoomSlider.value), zoomState.center_x, zoomState.center_y);
  });

  async function loadZoom() {
    try {
      const resp = await fetch("/api/zoom");
      zoomState = await resp.json();
      zoomSlider.value = zoomState.level;
      zoomValue.textContent = zoomState.level.toFixed(1) + "x";
    } catch (err) {
      /* zoom endpoint unreachable -- keep the 1.0x default, non-fatal */
    }
  }

  const MIN_DISTANCE_M = 10;

  function updateNewTargetGate() {
    const distance_m = parseFloat(distanceInput.value);
    const ok = !isNaN(distance_m) && distance_m > MIN_DISTANCE_M;
    newTargetBtn.disabled = !ok;
    newTargetBtn.title = ok ? "" : `Set distance to target above ${MIN_DISTANCE_M} m first`;
  }

  distanceInput.addEventListener("input", updateNewTargetGate);

  distanceInput.addEventListener("change", async () => {
    const distance_m = parseFloat(distanceInput.value);
    if (isNaN(distance_m) || distance_m < 0) {
      setStatus("Distance must be a non-negative number.");
      return;
    }
    try {
      await postJson("/api/distance", { distance_m });
      setStatus("Distance to target set.");
      refreshShots();
    } catch (err) {
      setStatus("Distance error: " + err.message);
    }
  });

  async function loadDistance() {
    try {
      const resp = await fetch("/api/distance");
      const data = await resp.json();
      distanceInput.value = data.distance_m || "";
    } catch (err) {
      /* distance endpoint unreachable -- leave the field blank, non-fatal */
    } finally {
      updateNewTargetGate();
    }
  }

  function updateFeedUI(active) {
    currentFeed = active;
    const isSynthetic = active === "synthetic";
    feedSyntheticBtn.classList.toggle("primary", isSynthetic);
    feedZcamBtn.classList.toggle("primary", !isSynthetic);
    badgeFeed.className = "badge " + (isSynthetic ? "good" : "");
    badgeFeed.innerHTML = `<span class="dot"></span>${isSynthetic ? "Simulated" : "Live"}`;
    feedHint.style.display = isSynthetic ? "" : "none";
  }

  async function switchFeed(target) {
    if (target === currentFeed) return;
    try {
      await postJson("/api/feed", { target });
      updateFeedUI(target);
      setStatus("Feed switched -- click New Target, then re-calibrate.");
    } catch (err) {
      setStatus("Feed switch error: " + err.message);
    }
  }

  feedSyntheticBtn.addEventListener("click", () => switchFeed("synthetic"));
  feedZcamBtn.addEventListener("click", () => switchFeed("zcam"));

  async function loadFeed() {
    try {
      const resp = await fetch("/api/feed");
      const data = await resp.json();
      updateFeedUI(data.active);
    } catch (err) {
      /* feed endpoint unreachable -- keep the synthetic default, non-fatal */
    }
  }

  // Single click handler for the feed, branching on the current mode.
  // Two different coordinate conventions are in play:
  //  - "view space" (direct naturalWidth/rect.width scaling): matches
  //    whatever the detector/calibration currently operate on -- the
  //    CURRENT effective (already zoomed/cropped) view. Calibrate and Mark
  //    Center both use this, since origin_px lives in that same space.
  //  - "native frame fraction" (undoing the current zoom crop): needed
  //    when the target itself is the synthetic source's own native canvas
  //    (zoom pan center, and hole placement, which draws into that canvas
  //    before any cropping happens).
  feed.addEventListener("click", async (ev) => {
    const rect = feed.getBoundingClientRect();
    const viewX = (ev.clientX - rect.left) * (feed.naturalWidth / rect.width);
    const viewY = (ev.clientY - rect.top) * (feed.naturalHeight / rect.height);

    if (mode === "zoom-center") {
      const fx = (ev.clientX - rect.left) / rect.width;
      const fy = (ev.clientY - rect.top) / rect.height;
      const newCenterX = zoomState.center_x + (fx - 0.5) / zoomState.level;
      const newCenterY = zoomState.center_y + (fy - 0.5) / zoomState.level;
      setMode("none");
      await applyZoom(zoomState.level, newCenterX, newCenterY);
      return;
    }

    if (mode === "mark-center") {
      setMode("none");
      try {
        await postJson("/api/calibration/center", { x: viewX, y: viewY });
        setStatus("Target center marked.");
        refreshShots();
      } catch (err) {
        setStatus("Error marking center: " + err.message);
      }
      return;
    }

    if (mode === "calibrate") {
      calibrationPoints.push([viewX, viewY]);
      if (calibrationPoints.length === 1) {
        setStatus("First point set. Click the second point.");
        return;
      }
      // Capture before setMode("none"), which clears calibrationPoints as
      // part of leaving calibrate mode.
      const [p1, p2] = calibrationPoints;
      setMode("none");
      const distance = window.prompt(
        `Real-world distance between the two points, in ${window.SPOTS_UNIT_NAME}:`
      );
      if (!distance || isNaN(parseFloat(distance))) {
        setStatus("Calibration cancelled: no valid distance entered.");
        return;
      }
      try {
        await postJson("/api/calibration", { p1, p2, distance: parseFloat(distance) });
        setStatus("Calibration set.");
      } catch (err) {
        setStatus("Calibration error: " + err.message);
      }
      return;
    }

    // Default action on the simulated feed: place a virtual bullet hole.
    if (currentFeed === "synthetic") {
      const fx = (ev.clientX - rect.left) / rect.width;
      const fy = (ev.clientY - rect.top) / rect.height;
      const nativeFx = zoomState.center_x + (fx - 0.5) / zoomState.level;
      const nativeFy = zoomState.center_y + (fy - 0.5) / zoomState.level;
      const x = nativeFx * feed.naturalWidth;
      const y = nativeFy * feed.naturalHeight;
      try {
        await postJson("/api/simulate/hole", { x, y });
        setStatus("Virtual bullet hole placed.");
      } catch (err) {
        setStatus("Error placing hole: " + err.message);
      }
    }
  });

  function fmt(n) {
    return typeof n === "number" ? n.toFixed(2) : "-";
  }

  function renderTargetDiagram(shots, center) {
    const svg = document.getElementById("target-diagram");
    const ns = "http://www.w3.org/2000/svg";
    svg.innerHTML = "";
    const size = 200,
      cx = size / 2,
      cy = size / 2,
      plotRadius = size / 2 - 20;

    if (!shots.length) {
      const text = document.createElementNS(ns, "text");
      text.setAttribute("x", cx);
      text.setAttribute("y", cy);
      text.setAttribute("class", "empty-label");
      text.textContent = "No calibrated shots yet";
      svg.appendChild(text);
      return;
    }

    let maxAbs = 0.5; // floor so a single near-center shot doesn't blow up the scale
    for (const s of shots) {
      maxAbs = Math.max(maxAbs, Math.abs(s.x_units), Math.abs(s.y_units));
    }
    if (center) maxAbs = Math.max(maxAbs, Math.abs(center[0]), Math.abs(center[1]));
    const scale = plotRadius / (maxAbs * 1.15);
    const toSvg = (x, y) => [cx + x * scale, cy - y * scale];

    for (const frac of [0.33, 0.66, 1.0]) {
      const ring = document.createElementNS(ns, "circle");
      ring.setAttribute("cx", cx);
      ring.setAttribute("cy", cy);
      ring.setAttribute("r", plotRadius * frac);
      ring.setAttribute("class", "ring");
      svg.appendChild(ring);
    }
    const hline = document.createElementNS(ns, "line");
    hline.setAttribute("x1", 10);
    hline.setAttribute("x2", size - 10);
    hline.setAttribute("y1", cy);
    hline.setAttribute("y2", cy);
    hline.setAttribute("class", "crosshair");
    svg.appendChild(hline);
    const vline = document.createElementNS(ns, "line");
    vline.setAttribute("y1", 10);
    vline.setAttribute("y2", size - 10);
    vline.setAttribute("x1", cx);
    vline.setAttribute("x2", cx);
    vline.setAttribute("class", "crosshair");
    svg.appendChild(vline);

    for (const s of shots) {
      const [x, y] = toSvg(s.x_units, s.y_units);
      const dot = document.createElementNS(ns, "circle");
      dot.setAttribute("cx", x);
      dot.setAttribute("cy", y);
      dot.setAttribute("r", 5);
      dot.setAttribute("class", "shot");
      svg.appendChild(dot);
    }

    if (center) {
      const [x, y] = toSvg(center[0], center[1]);
      const c = document.createElementNS(ns, "circle");
      c.setAttribute("cx", x);
      c.setAttribute("cy", y);
      c.setAttribute("r", 7);
      c.setAttribute("class", "center");
      svg.appendChild(c);
    }
  }

  async function refreshShots() {
    const resp = await fetch("/api/shots");
    const data = await resp.json();
    const stats = data.stats;

    if (data.calibrated) {
      badgeCalibration.className = "badge good";
      badgeCalibration.innerHTML = '<span class="dot"></span>Calibrated';
    } else {
      badgeCalibration.className = "badge warn";
      badgeCalibration.innerHTML = '<span class="dot"></span>Not calibrated';
    }
    if (data.session_id) {
      badgeSession.style.display = "";
      badgeSession.textContent = `Session #${data.session_id}`;
    } else {
      badgeSession.style.display = "none";
    }

    document.getElementById("stat-count").textContent = stats ? stats.shot_count : 0;
    document.getElementById("stat-spread").innerHTML = stats
      ? `${fmt(stats.extreme_spread)}<span class="unit"> ${data.unit_name}</span>`
      : "-";
    document.getElementById("stat-spread-moa").textContent =
      stats && stats.extreme_spread_moa !== null ? `${fmt(stats.extreme_spread_moa)} MOA` : "";
    document.getElementById("stat-radius").innerHTML = stats
      ? `${fmt(stats.mean_radius)}<span class="unit"> ${data.unit_name}</span>`
      : "-";
    document.getElementById("stat-std").innerHTML = stats
      ? `${fmt(stats.std_dev)}<span class="unit"> ${data.unit_name}</span>`
      : "-";

    const subgroupsGrid = document.getElementById("best-subgroups");
    const sizes = Object.keys(data.best_subgroups || {}).sort((a, b) => a - b);
    subgroupsGrid.innerHTML = sizes.length
      ? sizes
          .map((n) => {
            const bs = data.best_subgroups[n];
            const moa = bs.extreme_spread_moa !== null ? `<div class="hint">${fmt(bs.extreme_spread_moa)} MOA</div>` : "";
            return `
          <div class="stat-tile">
            <div class="label">Best ${n}-shot</div>
            <div class="value">${fmt(bs.extreme_spread)}<span class="unit"> ${data.unit_name}</span></div>
            ${moa}
          </div>`;
          })
          .join("")
      : '<p class="empty-state">Not enough shots yet.</p>';

    const exportLink = document.getElementById("export-csv");
    if (data.session_id) {
      exportLink.href = `/api/session/${data.session_id}/export.csv`;
      exportLink.classList.remove("disabled");
    } else {
      exportLink.removeAttribute("href");
      exportLink.classList.add("disabled");
    }

    const tbody = document.querySelector("#shot-table tbody");
    tbody.innerHTML = "";
    for (const shot of data.shots) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${shot.seq}</td><td>${fmt(shot.x_units)}</td><td>${fmt(shot.y_units)}</td>`;
      tbody.appendChild(tr);
    }

    const calibratedShots = data.shots.filter((s) => s.x_units !== null && s.x_units !== undefined);
    renderTargetDiagram(
      calibratedShots.map((s) => ({ x_units: s.x_units, y_units: s.y_units })),
      stats ? stats.center : null
    );
  }

  setInterval(refreshShots, 1000);
  refreshShots();
  loadZoom();
  loadFeed();
  loadDistance();
})();
