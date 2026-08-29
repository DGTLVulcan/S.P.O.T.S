(function () {
  const feed = document.getElementById("feed");
  const statusEl = document.getElementById("status");
  const newTargetBtn = document.getElementById("new-target");
  const calibrateBtn = document.getElementById("calibrate");
  const undoBtn = document.getElementById("undo");

  let calibrating = false;
  let calibrationPoints = [];

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
    calibrating = !calibrating;
    calibrationPoints = [];
    calibrateBtn.classList.toggle("active", calibrating);
    setStatus(
      calibrating
        ? "Calibration mode: click two points on the target a known distance apart."
        : "Calibration cancelled."
    );
  });

  feed.addEventListener("click", async (ev) => {
    if (!calibrating) return;
    const rect = feed.getBoundingClientRect();
    const scaleX = feed.naturalWidth / rect.width;
    const scaleY = feed.naturalHeight / rect.height;
    const x = (ev.clientX - rect.left) * scaleX;
    const y = (ev.clientY - rect.top) * scaleY;
    calibrationPoints.push([x, y]);

    if (calibrationPoints.length === 1) {
      setStatus("First point set. Click the second point.");
      return;
    }

    calibrating = false;
    calibrateBtn.classList.remove("active");
    const distance = window.prompt(
      `Real-world distance between the two points, in ${window.SPOTS_UNIT_NAME}:`
    );
    if (!distance || isNaN(parseFloat(distance))) {
      setStatus("Calibration cancelled: no valid distance entered.");
      return;
    }
    try {
      await postJson("/api/calibration", {
        p1: calibrationPoints[0],
        p2: calibrationPoints[1],
        distance: parseFloat(distance),
      });
      setStatus("Calibration set.");
    } catch (err) {
      setStatus("Calibration error: " + err.message);
    }
  });

  function fmt(n) {
    return typeof n === "number" ? n.toFixed(2) : "-";
  }

  async function refreshShots() {
    const resp = await fetch("/api/shots");
    const data = await resp.json();
    const stats = data.stats;

    document.getElementById("stat-count").textContent = stats ? stats.shot_count : 0;
    document.getElementById("stat-spread").textContent = stats
      ? `${fmt(stats.extreme_spread)} ${data.unit_name}`
      : "-";
    document.getElementById("stat-radius").textContent = stats
      ? `${fmt(stats.mean_radius)} ${data.unit_name}`
      : "-";
    document.getElementById("stat-std").textContent = stats
      ? `${fmt(stats.std_dev)} ${data.unit_name}`
      : "-";

    const subgroupsEl = document.getElementById("best-subgroups");
    const sizes = Object.keys(data.best_subgroups || {}).sort((a, b) => a - b);
    subgroupsEl.innerHTML = sizes.length
      ? sizes
          .map(
            (n) =>
              `<dt>Best ${n}-shot</dt><dd>${fmt(data.best_subgroups[n].extreme_spread)} ${data.unit_name}</dd>`
          )
          .join("")
      : "<dt>-</dt><dd>Not enough shots yet</dd>";

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
  }

  setInterval(refreshShots, 1000);
  refreshShots();
})();
