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
  const videoFrame = document.getElementById("video-frame");
  const fullscreenBtn = document.getElementById("fullscreen-btn");
  const resetSetupBtn = document.getElementById("reset-setup");

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

  // Delegated once on the table body rather than per-row, since rows are
  // fully rebuilt (innerHTML = "") every refreshShots() poll.
  document.querySelector("#shot-table tbody").addEventListener("click", async (ev) => {
    const excludeBtn = ev.target.closest(".row-exclude-btn");
    if (excludeBtn) {
      const seq = excludeBtn.dataset.seq;
      const excluded = excludeBtn.dataset.excluded !== "true";
      try {
        await postJson(`/api/shots/${seq}/exclude`, { excluded });
        setStatus(excluded ? `Shot #${seq} excluded as a flyer.` : `Shot #${seq} back in the group.`);
        refreshShots();
      } catch (err) {
        setStatus("Error updating shot: " + err.message);
      }
      return;
    }

    const btn = ev.target.closest(".row-delete-btn");
    if (!btn) return;
    const seq = btn.dataset.seq;
    try {
      await postJson(`/api/shots/${seq}/delete`);
      setStatus(`Removed shot #${seq}.`);
      refreshShots();
    } catch (err) {
      setStatus("Error removing shot: " + err.message);
    }
  });

  resetSetupBtn.addEventListener("click", async () => {
    // Not delegating to applyZoom() here: it swallows its own errors and
    // sets its own status message, which would either mask a failure here
    // or get overwritten by our own success message regardless of outcome.
    try {
      await postJson("/api/calibration/reset");
      const data = await postJson("/api/zoom", { level: 1.0, center_x: 0.5, center_y: 0.5 });
      zoomState = { level: data.level, center_x: data.center_x, center_y: data.center_y };
      zoomSlider.value = zoomState.level;
      zoomValue.textContent = zoomState.level.toFixed(1) + "x";
      setStatus("Calibration, target center, and zoom reset.");
      refreshShots();
    } catch (err) {
      setStatus("Reset error: " + err.message);
    }
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

  fullscreenBtn.addEventListener("click", () => {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      videoFrame.requestFullscreen().catch((err) => {
        setStatus("Fullscreen error: " + err.message);
      });
    }
  });

  document.addEventListener("fullscreenchange", () => {
    const isFull = document.fullscreenElement === videoFrame;
    fullscreenBtn.classList.toggle("active", isFull);
    fullscreenBtn.title = isFull ? "Exit fullscreen" : "Fullscreen";
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
    feedHint.textContent = isSynthetic
      ? "Simulated target active — click the feed to place a virtual bullet hole."
      : "Live feed active — click the feed to place a test shot.";
  }

  async function switchFeed(target) {
    if (target === currentFeed) return;
    try {
      await postJson("/api/feed", { target });
      updateFeedUI(target);
      // Nothing to do to the picture itself: frames are pulled one at a
      // time (see pumpFrames), so the next one already comes from the new
      // source.
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
    // Every coordinate below is derived from naturalWidth/Height; before the
    // first frame has decoded those are 0, which would silently send (0, 0)
    // for a calibration point or a shot.
    if (!feed.naturalWidth || !feed.naturalHeight) {
      setStatus("Waiting for the first video frame...");
      return;
    }
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

    // Default action: place a shot. On the simulated feed this draws a
    // real hole for the detector to find on its own next cycle; on the
    // live feed there's no fake canvas to draw on, so it's recorded
    // directly as a tagged test shot (see /api/test_shot).
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
    } else {
      try {
        await postJson("/api/test_shot", { x: viewX, y: viewY });
        setStatus("Test shot placed.");
        refreshShots();
      } catch (err) {
        setStatus("Error placing test shot: " + err.message);
      }
    }
  });

  function fmt(n) {
    return typeof n === "number" ? n.toFixed(2) : "-";
  }

  // renderTargetDiagram lives in diagram.js -- the session history detail
  // page draws the same diagram, so it is shared rather than duplicated.

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

    // Fullscreen HUD mirrors the same figures -- only visible via CSS
    // (.video-frame:fullscreen .hud) but kept in sync unconditionally so
    // there's nothing to wire up on entering/exiting fullscreen.
    document.getElementById("hud-stat-count").textContent = stats ? stats.shot_count : 0;
    document.getElementById("hud-stat-spread").textContent = stats
      ? `${fmt(stats.extreme_spread)} ${data.unit_name}` +
        (stats.extreme_spread_moa !== null ? ` (${fmt(stats.extreme_spread_moa)} MOA)` : "")
      : "-";
    document.getElementById("hud-stat-radius").textContent = stats
      ? `${fmt(stats.mean_radius)} ${data.unit_name}`
      : "-";
    document.getElementById("hud-stat-std").textContent = stats
      ? `${fmt(stats.std_dev)} ${data.unit_name}`
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

    const hudSubgroupsList = document.getElementById("hud-subgroups-list");
    hudSubgroupsList.innerHTML = sizes.length
      ? sizes
          .map((n) => {
            const bs = data.best_subgroups[n];
            return `<div><span class="hud-label">Best ${n}</span>${fmt(bs.extreme_spread)} ${data.unit_name}</div>`;
          })
          .join("")
      : '<div class="hud-empty">Not enough shots yet.</div>';

    const hudShotsRow = document.getElementById("hud-shots-row");
    const lastShots = data.shots.slice(-5);
    hudShotsRow.innerHTML = lastShots.length
      ? lastShots
          .map((shot) => {
            const x = fmt(shot.x_units);
            const y = fmt(shot.y_units);
            return `<span class="hud-shot-chip${shot.is_test ? " is-test" : ""}"><span class="hud-shot-seq">#${shot.seq}</span>${x}, ${y}</span>`;
          })
          .join("")
      : '<span class="hud-empty">No shots yet.</span>';

    const scopeEl = document.getElementById("scope-correction");
    const sc = data.scope_correction;
    if (!sc) {
      // Spell out which prerequisite is missing rather than just going blank
      // -- the usual one is having calibrated but not marked the centre.
      let why = "Fire a group to get a correction.";
      if (!data.calibrated) why = "Calibrate the scale first.";
      else if (!data.stats) why = "Fire a group to get a correction.";
      else if (!data.distance_m) why = "Set the distance to target.";
      else why = "Click Mark Center on the target's point of aim.";
      scopeEl.innerHTML = `<p class="empty-state">${why}</p>`;
    } else {
      const unit = sc.click_unit === "mrad" ? "mrad" : "MOA";
      const tile = (dir, clicks, angle) => `
        <div class="stat-tile">
          <div class="label">${dir.toUpperCase()}</div>
          <div class="value">${clicks}<span class="unit"> click${clicks === 1 ? "" : "s"}</span></div>
          <div class="hint">${fmt(angle)} ${unit}</div>
        </div>`;
      scopeEl.innerHTML =
        `<div class="stat-grid">
           ${tile(sc.vertical_dir, sc.vertical_clicks, sc.vertical_angle)}
           ${tile(sc.horizontal_dir, sc.horizontal_clicks, sc.horizontal_angle)}
         </div>
         <p class="hint">Turret: ${sc.click_value} ${unit}/click &mdash; change in Settings.</p>`;
    }

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
    let previousAt = null;
    for (const shot of data.shots) {
      const tr = document.createElement("tr");
      const classes = [];
      if (shot.is_test) classes.push("shot-row-test");
      if (shot.excluded) classes.push("shot-row-excluded");
      tr.className = classes.join(" ");
      const testBadge = shot.is_test ? ' <span class="badge warn" style="position:static;">Test</span>' : "";
      // Split from the previous shot, so a string's cadence is visible
      // without digging through the session history.
      let split = "-";
      if (shot.created_at) {
        if (previousAt) split = `+${(shot.created_at - previousAt).toFixed(1)}s`;
        else split = "start";
        previousAt = shot.created_at;
      }
      tr.innerHTML =
        `<td>${shot.seq}${testBadge}</td>` +
        `<td>${fmt(shot.x_units)}</td>` +
        `<td>${fmt(shot.y_units)}</td>` +
        `<td class="split-cell">${split}</td>` +
        `<td class="row-btns">` +
        `<button class="row-exclude-btn" data-seq="${shot.seq}" data-excluded="${shot.excluded}" ` +
        `title="${shot.excluded ? "Include in group stats" : "Exclude from group stats (flyer)"}">` +
        `${shot.excluded ? "&#8853;" : "&#8854;"}</button>` +
        `<button class="row-delete-btn" data-seq="${shot.seq}" title="Delete this shot">&times;</button>` +
        `</td>`;
      tbody.appendChild(tr);
    }

    const calibratedShots = data.shots.filter((s) => s.x_units !== null && s.x_units !== undefined);
    const diagramShots = calibratedShots.map((s) => ({
      x_units: s.x_units,
      y_units: s.y_units,
      is_test: s.is_test,
    }));
    const diagramCenter = stats ? stats.center : null;
    renderTargetDiagram("target-diagram", diagramShots, diagramCenter);
    renderTargetDiagram("hud-target-diagram", diagramShots, diagramCenter);
  }

  // Pull the video one frame at a time instead of consuming a continuous
  // MJPEG stream. A push stream has no backpressure -- the server keeps
  // emitting on a timer whether or not the link can carry it, and the
  // excess sits in the socket buffer, so over a slow link (the Pi hosting
  // its own 2.4GHz AP) you end up watching a picture from several seconds
  // ago, and only reloading the page clears the backlog. Requesting the
  // next frame only once the previous one has arrived keeps exactly one
  // frame in flight, so latency is a single round trip and the frame rate
  // settles at whatever the link can actually sustain.
  const FRAME_MIN_INTERVAL_MS = 100; // ceiling of ~10 fps on a fast link
  const FRAME_RETRY_MS = 1000;
  let feedObjectUrl = null;

  function showFrame(blob) {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(blob);
      let settled = false;
      // Release the previous blob only once the new one is actually
      // decoded and on screen, otherwise the image can flash empty.
      const done = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (feedObjectUrl) URL.revokeObjectURL(feedObjectUrl);
        feedObjectUrl = url;
        resolve();
      };
      // Never let a frame that fires neither load nor error wedge the loop
      // permanently -- this runs unattended at the range.
      const timer = setTimeout(done, 5000);
      feed.onload = done;
      feed.onerror = done;
      feed.src = url;
    });
  }

  async function pumpFrames() {
    for (;;) {
      const startedAt = Date.now();
      try {
        const resp = await fetch("/frame.jpg?t=" + startedAt, { cache: "no-store" });
        if (!resp.ok) throw new Error(String(resp.status));
        await showFrame(await resp.blob());
      } catch (err) {
        // No frame yet (503 before the camera warms up) or a dropped
        // connection -- back off a little and keep trying rather than
        // leaving a dead picture until the user reloads.
        await new Promise((r) => setTimeout(r, FRAME_RETRY_MS));
        continue;
      }
      const elapsed = Date.now() - startedAt;
      if (elapsed < FRAME_MIN_INTERVAL_MS) {
        await new Promise((r) => setTimeout(r, FRAME_MIN_INTERVAL_MS - elapsed));
      }
    }
  }

  setInterval(refreshShots, 1000);
  refreshShots();
  loadZoom();
  loadFeed();
  loadDistance();
  pumpFrames();
})();
