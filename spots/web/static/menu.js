// The site menu behind the hamburger button.
//
// The panel is in the markup and merely hidden, so the links work with
// JavaScript off. This handles opening, closing and keyboard use.
(function () {
  const button = document.getElementById("menu-button");
  const panel = document.getElementById("menu-panel");
  if (!button || !panel) return;

  function items() {
    return Array.from(panel.querySelectorAll(".menu-item"));
  }

  function open(focusFirst) {
    panel.hidden = false;
    button.setAttribute("aria-expanded", "true");
    startStats();
    if (focusFirst) {
      const first = panel.querySelector(".is-current") || items()[0];
      if (first) first.focus();
    }
  }

  function close(returnFocus) {
    panel.hidden = true;
    button.setAttribute("aria-expanded", "false");
    stopStats();
    if (returnFocus) button.focus();
  }

  button.addEventListener("click", (ev) => {
    ev.stopPropagation();
    if (panel.hidden) open(false);
    else close(false);
  });

  // Anywhere else on the page dismisses it, which is what a stray tap on a
  // phone means.
  document.addEventListener("click", (ev) => {
    if (!panel.hidden && !panel.contains(ev.target) && ev.target !== button) close(false);
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !panel.hidden) close(true);
  });

  button.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      open(true);
    }
  });

  panel.addEventListener("keydown", (ev) => {
    if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp") return;
    ev.preventDefault();
    const all = items();
    const at = all.indexOf(document.activeElement);
    const next = ev.key === "ArrowDown" ? at + 1 : at - 1;
    const target = all[(next + all.length) % all.length];
    if (target) target.focus();
  });

  // ---- the system box at the foot of the panel --------------------------
  //
  // Polled only while the menu is open, since this runs on the same Pi that
  // is doing the detection.
  const rows = document.getElementById("menu-stats-rows");
  let timer = null;

  function startStats() {
    if (!rows || timer !== null) return;
    readStats();
    timer = setInterval(readStats, 5000);
  }

  function stopStats() {
    if (timer === null) return;
    clearInterval(timer);
    timer = null;
  }

  async function readStats() {
    try {
      const resp = await fetch("/api/health", { cache: "no-store" });
      render(await resp.json());
    } catch (err) {
      // Diagnostics must never take the menu down with them: the links
      // below are the only way off this page.
      rows.innerHTML = row("System", "unavailable", "", "warn");
    }
  }

  function row(label, value, note, level) {
    return `<div class="menu-stat is-${level || "ok"}">
              <span class="menu-stat-label">${label}</span>
              <span class="menu-stat-value">${value}</span>
              <span class="menu-stat-note">${note || ""}</span>
            </div>`;
  }

  function uptime(seconds) {
    if (seconds === null || seconds === undefined) return "&ndash;";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    return `${m}m`;
  }

  // What the power row says. A supply sagging right now outranks one that
  // dipped at boot: they are different problems.
  function power(flags) {
    if (!flags) return { text: "&ndash;", note: "not reported", level: "ok" };
    if (flags.under_voltage_now) {
      return { text: "Low volts", note: "check the supply", level: "critical" };
    }
    if (flags.throttled_now) {
      return { text: "Throttled", note: "running slow", level: "critical" };
    }
    if (flags.under_voltage_since_boot) {
      return { text: "Dipped", note: "under-volt since boot", level: "warn" };
    }
    if (flags.throttled_since_boot) {
      return { text: "Throttled", note: "since boot", level: "warn" };
    }
    return { text: "OK", note: "", level: "ok" };
  }

  function render(data) {
    const level = data.levels || {};
    const out = [];

    out.push(row("CPU",
      data.cpu_temp_c === null ? "&ndash;" : `${data.cpu_temp_c.toFixed(0)}&deg;C`,
      data.load_average ? `load ${data.load_average[0].toFixed(2)}` : "",
      level.cpu));

    out.push(row("Uptime", uptime(data.uptime_s), "", "ok"));

    out.push(row("Disk",
      data.disk ? `${(data.disk.free_mb / 1024).toFixed(1)} GB` : "&ndash;",
      data.disk ? `${data.disk.used_percent.toFixed(0)}% used` : "",
      level.disk));

    const supply = power(data.throttled);
    out.push(row("Power", supply.text, supply.note,
                 level.power === "ok" ? supply.level : level.power));

    rows.innerHTML = out.join("");
  }

  // The panel starts hidden, but a page could render it open.
  if (!panel.hidden) startStats();
})();
