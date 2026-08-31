// Pi health, in two forms: the full panel in settings, and a dashboard
// badge that only appears when something is actually wrong.

function healthTile(label, value, hint) {
  return `<div class="stat-tile">
            <div class="label">${label}</div>
            <div class="value">${value}</div>
            ${hint ? `<div class="hint">${hint}</div>` : ""}
          </div>`;
}

function formatUptime(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

function renderHealth(rootId, data) {
  const root = document.getElementById(rootId);
  if (!root) return;
  const tiles = [];

  tiles.push(healthTile(
    "CPU temp",
    data.cpu_temp_c === null ? "-" : `${data.cpu_temp_c.toFixed(1)}<span class="unit"> &deg;C</span>`,
    data.load_average ? `load ${data.load_average.map((n) => n.toFixed(2)).join(" ")}` : ""
  ));

  if (data.disk) {
    tiles.push(healthTile(
      "Disk free",
      `${(data.disk.free_mb / 1024).toFixed(1)}<span class="unit"> GB</span>`,
      `${data.disk.used_percent.toFixed(0)}% of ${(data.disk.total_mb / 1024).toFixed(1)} GB used`
    ));
  }
  if (data.memory) {
    tiles.push(healthTile(
      "Memory free",
      `${(data.memory.available_mb / 1024).toFixed(2)}<span class="unit"> GB</span>`,
      `${data.memory.used_percent.toFixed(0)}% used`
    ));
  }
  tiles.push(healthTile("Uptime", formatUptime(data.uptime_s)));
  tiles.push(healthTile(
    "Feed",
    data.feed_active === "zcam" ? "Live" : "Simulated",
    data.feed_active === "zcam"
      ? (data.camera_connected ? "camera connected" : "camera NOT connected")
      : ""
  ));

  const warnings = data.warnings && data.warnings.length
    ? `<div class="banner ${data.status === "critical" ? "error" : "warn"}">
         ${data.warnings.map((w) => `<div>${w}</div>`).join("")}
       </div>`
    : `<p class="hint">No problems detected.</p>`;

  root.innerHTML = warnings + `<div class="stat-grid">${tiles.join("")}</div>`;
}

async function pollHealth(rootId, badgeId, intervalMs) {
  const badge = badgeId ? document.getElementById(badgeId) : null;
  for (;;) {
    try {
      const resp = await fetch("/api/health", { cache: "no-store" });
      const data = await resp.json();
      renderHealth(rootId, data);
      if (badge) {
        // Only surface on the dashboard when it needs attention, so the
        // badge row stays quiet during a normal session.
        if (data.status === "ok") {
          badge.style.display = "none";
        } else {
          badge.style.display = "";
          badge.className = "badge " + (data.status === "critical" ? "bad" : "warn");
          badge.innerHTML = `<span class="dot"></span>${data.warnings[0] || "System warning"}`;
          badge.title = data.warnings.join("\n");
        }
      }
    } catch (err) {
      /* health is diagnostic only -- never break the page over it */
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
