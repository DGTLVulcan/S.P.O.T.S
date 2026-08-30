(function () {
  const root = document.getElementById("camera-controls-root");

  async function applyControl(key, value) {
    const valueEl = document.getElementById(`v-${key}`);
    try {
      const resp = await fetch("/api/camera/controls", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, value }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || resp.statusText);
      if (valueEl) valueEl.textContent = data.value;
    } catch (err) {
      if (valueEl) valueEl.textContent = "error: " + err.message;
    }
  }

  function renderControl(c) {
    const row = document.createElement("div");
    row.className = "control-row";

    const head = document.createElement("div");
    head.className = "row-head";
    head.innerHTML = `<span class="k">${c.label}</span><span class="v" id="v-${c.key}">${c.value}</span>`;
    row.appendChild(head);

    if (c.type === 1) {
      // choice
      const select = document.createElement("select");
      select.disabled = c.ro;
      for (const opt of c.opts || []) {
        const option = document.createElement("option");
        option.value = opt;
        option.textContent = opt;
        if (opt === c.value) option.selected = true;
        select.appendChild(option);
      }
      select.addEventListener("change", () => applyControl(c.key, select.value));
      row.appendChild(select);
    } else if (c.type === 2) {
      // range
      const wrap = document.createElement("div");
      wrap.className = "field-row";
      const slider = document.createElement("input");
      slider.type = "range";
      slider.min = c.min;
      slider.max = c.max;
      slider.step = c.step || 1;
      slider.value = c.value;
      slider.disabled = c.ro;
      const output = document.createElement("output");
      output.textContent = c.value;
      slider.addEventListener("input", () => {
        output.textContent = slider.value;
      });
      slider.addEventListener("change", () => applyControl(c.key, Number(slider.value)));
      wrap.appendChild(slider);
      wrap.appendChild(output);
      row.appendChild(wrap);
    } else {
      const span = document.createElement("span");
      span.className = "hint";
      span.textContent = String(c.value);
      row.appendChild(span);
    }

    if (c.ro) {
      const note = document.createElement("span");
      note.className = "hint";
      note.textContent = "Read-only";
      row.appendChild(note);
    }
    return row;
  }

  async function load() {
    try {
      const resp = await fetch("/api/camera/controls");
      const data = await resp.json();
      root.innerHTML = "";
      if (!data.available) {
        root.innerHTML = `<p class="empty-state">${data.reason || "Camera controls unavailable."}</p>`;
        return;
      }
      if (!data.controls.length) {
        root.innerHTML = '<p class="empty-state">No camera controls could be read.</p>';
        return;
      }
      const list = document.createElement("div");
      list.className = "control-list";
      for (const c of data.controls) list.appendChild(renderControl(c));
      root.appendChild(list);
    } catch (err) {
      root.innerHTML = `<p class="empty-state">Failed to load camera controls: ${err.message}</p>`;
    }
  }

  load();
  // Diagnostics panel; refreshed on a slow timer since none of it moves fast.
  pollHealth("health-root", null, 5000);

  // Panel switching, matching the equipment page: one section on screen at a
  // time instead of every setting stacked into one long scroll.
  const FORM_PANELS = ["target", "detection", "camera"];
  const PANEL_KEY = "spots.settingsPanel";
  const navButtons = Array.from(document.querySelectorAll(".settings-nav"));
  const panels = Array.from(document.querySelectorAll(".settings-panel"));
  const actions = document.getElementById("settings-actions");

  function showPanel(name) {
    const known = panels.some((p) => p.dataset.panel === name);
    const active = known ? name : "target";
    panels.forEach((p) => {
      p.hidden = p.dataset.panel !== active;
    });
    navButtons.forEach((b) => b.classList.toggle("is-open", b.dataset.panel === active));
    // Save belongs to the form sections only; the device panels act on their
    // own and would make it look like they need saving too.
    if (actions) actions.hidden = !FORM_PANELS.includes(active);
    try {
      localStorage.setItem(PANEL_KEY, active);
    } catch (err) {
      /* private browsing -- the panel just won't be remembered */
    }
  }

  navButtons.forEach((button) => {
    button.addEventListener("click", () => showPanel(button.dataset.panel));
  });

  // Saving POSTs and redirects, which loses any in-page state, so the panel
  // you were on is remembered and restored rather than snapping back.
  let initial = "target";
  try {
    initial = localStorage.getItem(PANEL_KEY) || "target";
  } catch (err) {
    /* ignore */
  }
  showPanel(initial);
})();
