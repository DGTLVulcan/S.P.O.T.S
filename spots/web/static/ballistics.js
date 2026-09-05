// The ballistics page: a come-up solution, a DOPE card, and truing.
//
// Everything it can fill in for you, it does -- muzzle velocity and BC off
// the selected ammo, sight height off the rifle, zero and turret unit off
// the scope, the air off the running session's conditions. What it cannot
// find, it asks for by name rather than substituting a default, because a
// ballistic answer built on a guessed input still looks authoritative.
(function () {
  const $ = (id) => document.getElementById(id);

  // Field definitions. `simple` fields are the ones you cannot go without;
  // the rest only appear in the advanced view.
  const FIELDS = [
    { key: "muzzle_velocity_fps", label: "Muzzle velocity", unit: "fps", step: 1, simple: true,
      help: "Off the box, or a chronograph if you have one. This is the input truing corrects." },
    { key: "ballistic_coefficient", label: "Ballistic coefficient", step: 0.001, simple: true,
      help: "From the bullet maker. Make sure the drag model below matches the one they quoted." },
    { key: "drag_model", label: "Drag model", kind: "select", simple: true,
      options: [["g7", "G7 (boat tail / match)"], ["g1", "G1 (flat base / hunting)"]],
      help: "A G1 BC used as a G7 is a big error, so this travels with the number." },
    { key: "sight_height_mm", label: "Sight height over bore", unit: "mm", step: 0.5, simple: true,
      help: "Centre of the scope to centre of the bore. Gets the near end of the trajectory right." },
    { key: "zero_distance_m", label: "Zero distance", unit: "m", step: 5, simple: true,
      help: "Where the rifle is currently zeroed." },
    { key: "wind_speed_kph", label: "Wind speed", unit: "kph", step: 0.5, simple: true },
    { key: "wind_clock", label: "Wind from", kind: "select", simple: true,
      options: [[12, "12 o'clock (head)"], [1.5, "1:30"], [3, "3 o'clock (full, right)"],
                [4.5, "4:30"], [6, "6 o'clock (tail)"], [7.5, "7:30"],
                [9, "9 o'clock (full, left)"], [10.5, "10:30"]],
      help: "The direction it blows FROM, as a clock face with 12 downrange." },
    { key: "temperature_c", label: "Temperature", unit: "C", step: 0.5 },
    { key: "pressure_hpa", label: "Station pressure", unit: "hPa", step: 1,
      help: "What a barometer reads where you are standing, not the sea-level figure a forecast gives." },
    { key: "humidity_pct", label: "Humidity", unit: "%", step: 1 },
    { key: "bullet_grains", label: "Bullet weight", unit: "gr", step: 0.1,
      help: "Only used for the energy column." },
    { key: "bullet_diameter_mm", label: "Bullet diameter", unit: "mm", step: 0.01 },
    { key: "bullet_length_mm", label: "Bullet length", unit: "mm", step: 0.1,
      help: "Needed for spin drift. Left out of the answer when it is blank." },
    { key: "twist_rate_in", label: "Twist rate", unit: "in", step: 0.5 },
    { key: "look_angle_deg", label: "Look angle", unit: "deg", step: 1,
      help: "Uphill or downhill. Both need less come-up than a level shot." },
    { key: "max_distance_m", label: "Card out to", unit: "m", step: 50 },
    { key: "step_m", label: "Card steps of", unit: "m", step: 5 },
  ];

  const state = { unit: "mrad", advanced: false, values: {}, solved: null,
                dope: null, dopeLoaded: false, truing: [] };

  // ---- rendering the inputs -------------------------------------------

  function control(field) {
    const wrap = document.createElement("label");
    wrap.className = "ball-field";
    const name = document.createElement("span");
    name.className = "ball-field-label";
    name.textContent = field.unit ? `${field.label} (${field.unit})` : field.label;
    wrap.append(name);

    let input;
    if (field.kind === "select") {
      input = document.createElement("select");
      field.options.forEach(([value, text]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        input.append(option);
      });
    } else {
      input = document.createElement("input");
      input.type = "number";
      input.step = field.step || "any";
    }
    input.id = `f-${field.key}`;
    input.dataset.key = field.key;
    const current = state.values[field.key];
    if (current !== undefined && current !== null) input.value = current;
    input.addEventListener("input", () => { state.values[field.key] = input.value; });
    wrap.append(input);

    if (field.help) {
      const help = document.createElement("span");
      help.className = "hint";
      help.textContent = field.help;
      wrap.append(help);
    }
    return wrap;
  }

  function renderInputs() {
    const steps = $("ball-steps");
    const grid = $("ball-fields");
    steps.textContent = "";
    grid.textContent = "";
    FIELDS.forEach((field) => {
      if (field.simple) {
        const li = document.createElement("li");
        li.className = "ball-step";
        li.append(control(field));
        steps.append(li);
      }
      grid.append(control(field));
    });
    $("ball-simple").hidden = state.advanced;
    $("ball-advanced").hidden = !state.advanced;
  }

  function readValues() {
    const out = {};
    FIELDS.forEach((field) => {
      const el = document.querySelector(
        `${state.advanced ? "#ball-advanced" : "#ball-simple"} [data-key="${field.key}"]`)
        || document.querySelector(`[data-key="${field.key}"]`);
      if (el && el.value !== "") out[field.key] = el.value;
    });
    return out;
  }

  // ---- loading what we already know ------------------------------------

  async function loadInputs() {
    try {
      const res = await fetch("/api/ballistics/inputs");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.unit = data.unit;
      state.values = Object.assign({}, data.shot, data.defaults);
      state.clickValue = data.equipment.click_value;

      const kit = [data.equipment.rifle, data.equipment.scope, data.equipment.ammo]
        .filter(Boolean).join("  ·  ");
      $("ball-kit").textContent = kit
        ? `Using ${kit}. Anything below can be overridden without changing the equipment.`
        : "No equipment selected — fill the fields in by hand, or choose kit on the dashboard.";

      const missing = $("ball-missing");
      if (data.missing.length) {
        missing.hidden = false;
        missing.innerHTML = "Needed before this can be worked out: <strong>"
          + data.missing.map(escapeHtml).join("</strong>, <strong>") + "</strong>."
          + " Type it below, or record it on the equipment so it is remembered.";
      } else {
        missing.hidden = true;
      }
      setUnit(state.unit);
      renderInputs();
    } catch (err) {
      $("ball-status").textContent = `Could not read the equipment: ${err.message}`;
    }
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : text;
    return div.innerHTML;
  }

  // ---- solving ----------------------------------------------------------

  async function solve() {
    $("ball-status").textContent = "Working it out…";
    try {
      const values = readValues();
      const res = await fetch("/api/ballistics/solve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          shot: values,
          unit: state.unit,
          click_value: state.clickValue,
          max_distance_m: values.max_distance_m,
          step_m: values.step_m,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      state.solved = data;
      renderCard(data);
      if (window.SPOTS_SIM) window.SPOTS_SIM.cardChanged(data);
      $("ball-status").textContent = "";
    } catch (err) {
      $("ball-status").textContent = err.message;
      $("ball-result").hidden = true;
    }
  }

  function renderCard(data) {
    const unit = data.unit.toUpperCase();
    const body = $("ball-card").querySelector("tbody");
    body.textContent = "";
    data.rows.forEach((row) => {
      const tr = document.createElement("tr");
      if (row.transonic) tr.className = "is-transonic";
      const cells = [
        `${row.distance_m} m`,
        `${row.drop_cm} cm`,
        `${row.elevation.toFixed(2)} ${unit}`,
        row.elevation_clicks === null ? "—" : row.elevation_clicks,
        `${row.windage.toFixed(2)} ${unit}`,
        row.windage_clicks === null ? "—" : row.windage_clicks,
        `${row.velocity_fps} fps`,
        `${row.energy_j} J`,
        `${row.time_s.toFixed(2)} s`,
        row.mach.toFixed(2),
      ];
      cells.forEach((text) => {
        const td = document.createElement("td");
        td.textContent = text;
        tr.append(td);
      });
      body.append(tr);
    });

    const bits = [
      `Air ${data.air_density} kg/m³ (${(data.density_ratio * 100).toFixed(0)}% of standard)`,
      `speed of sound ${data.speed_of_sound_ms} m/s`,
    ];
    if (data.stability !== null) {
      bits.push(`stability Sg ${data.stability}${data.stability < 1.4 ? " — marginal" : ""}`);
    } else {
      bits.push("spin drift left out (no bullet length recorded)");
    }
    if (data.transonic_from_m !== null) {
      bits.push(`<strong>transonic from ${data.transonic_from_m} m — the solution stops being trustworthy there</strong>`);
    }
    $("ball-summary").innerHTML = bits.join(" · ");
    $("ball-result").hidden = false;
  }

  // ---- the DOPE card -----------------------------------------------------

  function dopeRow(row) {
    const tr = document.createElement("tr");
    const make = (value, key, step) => {
      const td = document.createElement("td");
      const input = document.createElement("input");
      input.type = "number";
      input.step = step;
      input.value = value === null || value === undefined ? "" : value;
      input.dataset.key = key;
      td.append(input);
      return td;
    };
    tr.append(make(row.distance_m, "distance_m", 5));
    tr.append(make(row.elevation, "elevation", 0.01));
    tr.append(make(row.windage, "windage", 0.01));

    const noteCell = document.createElement("td");
    const note = document.createElement("input");
    note.type = "text";
    note.value = row.note || "";
    note.dataset.key = "note";
    noteCell.append(note);
    tr.append(noteCell);

    const removeCell = document.createElement("td");
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "row-remove";
    remove.textContent = "×";
    remove.title = "Remove this row";
    remove.addEventListener("click", () => tr.remove());
    removeCell.append(remove);
    tr.append(removeCell);
    return tr;
  }

  function renderDope(card) {
    const body = $("dope-card").querySelector("tbody");
    body.textContent = "";
    (card && card.rows ? card.rows : []).forEach((row) => body.append(dopeRow(row)));
  }

  function readDope() {
    const rows = [];
    $("dope-card").querySelectorAll("tbody tr").forEach((tr) => {
      const row = {};
      tr.querySelectorAll("[data-key]").forEach((el) => {
        row[el.dataset.key] = el.dataset.key === "note" ? el.value
          : (el.value === "" ? null : Number(el.value));
      });
      if (row.distance_m) rows.push(row);
    });
    return { unit: state.unit, rows, source: "manual" };
  }

  async function loadDope() {
    try {
      const res = await fetch("/api/ballistics/dope");
      const data = await res.json();
      state.dope = data.card;
      state.dopeLoaded = true;
      renderDope(data.card);
      $("dope-status").textContent = data.card
        ? "" : "No card saved for this rifle and load yet.";
    } catch (err) {
      $("dope-status").textContent = err.message;
    }
  }

  // ---- truing ------------------------------------------------------------

  async function loadTruing() {
    $("true-status").textContent = "Reading your sessions…";
    try {
      const res = await fetch(`/api/ballistics/truing?unit=${state.unit}`);
      const data = await res.json();
      state.truing = data.rows;
      const body = $("true-table").querySelector("tbody");
      body.textContent = "";
      const unit = data.unit.toUpperCase();
      data.rows.forEach((row) => {
        const tr = document.createElement("tr");
        if (!row.usable) tr.className = "is-unusable";
        const tick = document.createElement("td");
        if (row.usable) {
          const box = document.createElement("input");
          box.type = "checkbox";
          box.checked = true;
          box.dataset.session = row.session_id;
          tick.append(box);
        }
        tr.append(tick);
        const cells = row.usable
          ? [row.name, `${row.distance_m} m`, `${row.dialled} ${unit}`,
             `${row.group_offset > 0 ? "+" : ""}${row.group_offset} ${unit}`,
             `${row.measured} ${unit}`]
          : [row.name, row.distance_m ? `${row.distance_m} m` : "—", "—", "—", row.why];
        cells.forEach((text) => {
          const td = document.createElement("td");
          td.textContent = text;
          tr.append(td);
        });
        body.append(tr);
      });
      const usable = data.rows.filter((r) => r.usable).length;
      $("true-status").textContent = usable
        ? `${usable} session${usable === 1 ? "" : "s"} can be trued against.`
        : "No session has everything truing needs yet.";
    } catch (err) {
      $("true-status").textContent = err.message;
    }
  }

  async function runTruing() {
    const chosen = new Set();
    $("true-table").querySelectorAll("[data-session]").forEach((box) => {
      if (box.checked) chosen.add(Number(box.dataset.session));
    });
    const observations = state.truing.filter((r) => r.usable && chosen.has(r.session_id));
    if (!observations.length) {
      $("true-status").textContent = "Tick at least one session first.";
      return;
    }
    $("true-status").textContent = "Fitting…";
    try {
      const res = await fetch("/api/ballistics/true", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shot: readValues(), unit: state.unit, observations }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      const direction = data.change_fps >= 0 ? "faster" : "slower";
      $("true-result").hidden = false;
      $("true-result").innerHTML =
        `<div class="ball-summary"><strong>${data.muzzle_velocity_fps} fps</strong> fits your
         ${data.observations} measured come-up${data.observations === 1 ? "" : "s"} best —
         ${Math.abs(data.change_fps)} fps ${direction} than the ${data.was_fps} fps you started
         with, leaving ${data.rms_error} ${data.unit.toUpperCase()} of error.</div>
         <p class="hint">Put that in the muzzle velocity field to use it, or record it on the
         ammo so it sticks. One range does not pin a velocity down &mdash; the further out you
         true, and the more distances you use, the better this gets.</p>`;
      $("true-status").textContent = "";
      state.values.muzzle_velocity_fps = data.muzzle_velocity_fps;
      const field = document.querySelector('[data-key="muzzle_velocity_fps"]');
      if (field) field.value = data.muzzle_velocity_fps;
    } catch (err) {
      $("true-status").textContent = err.message;
    }
  }

  // ---- chrome ------------------------------------------------------------

  function setUnit(unit) {
    state.unit = unit;
    $("unit-mrad").classList.toggle("primary", unit === "mrad");
    $("unit-moa").classList.toggle("primary", unit === "moa");
  }

  function showPanel(name) {
    document.querySelectorAll(".ball-panel").forEach((panel) => {
      panel.hidden = panel.dataset.panel !== name;
    });
    document.querySelectorAll(".ball-nav").forEach((button) => {
      button.classList.toggle("is-open", button.dataset.panel === name);
    });
    $("ball-title").textContent =
      { solve: "Come-up", dope: "DOPE card", true: "Truing", sim: "Simulation" }[name]
      || "Ballistics";
    if (name === "true") loadTruing();
    // Only the first visit reads the saved card. Re-fetching on every
    // switch raced whatever had just been put in the table -- filling from
    // the solution showed the rows, then the fetch landed and wiped them --
    // and threw away un-saved typing on the way past.
    if (name === "dope" && !state.dopeLoaded) loadDope();
    // The canvas has no measured size until its panel is on screen, so the
    // flight is worked out when the tab is opened rather than up front.
    if (name === "sim" && window.SPOTS_SIM) window.SPOTS_SIM.open();
    // Leaving the tab should not leave an animation running behind it.
    if (name !== "sim" && window.SPOTS_SIM) window.SPOTS_SIM.stop();
  }

  document.querySelectorAll(".ball-nav").forEach((button) => {
    button.addEventListener("click", () => showPanel(button.dataset.panel));
  });

  ["mrad", "moa"].forEach((unit) => {
    $(`unit-${unit}`).addEventListener("click", () => {
      setUnit(unit);
      if (state.solved) solve();
    });
  });

  $("view-simple").addEventListener("click", () => {
    state.advanced = false;
    $("view-simple").classList.add("primary");
    $("view-advanced").classList.remove("primary");
    renderInputs();
  });
  $("view-advanced").addEventListener("click", () => {
    state.advanced = true;
    $("view-advanced").classList.add("primary");
    $("view-simple").classList.remove("primary");
    renderInputs();
  });

  $("ball-solve").addEventListener("click", solve);

  $("ball-to-dope").addEventListener("click", () => {
    if (!state.solved) {
      $("ball-status").textContent = "Work out a solution first.";
      return;
    }
    // Claim the table before switching, so the first-visit load can't
    // fire and overwrite what is about to be put in it.
    state.dopeLoaded = true;
    showPanel("dope");
    renderDope({
      rows: state.solved.rows.map((row) => ({
        distance_m: row.distance_m,
        elevation: row.elevation,
        windage: row.windage,
        note: row.transonic ? "transonic" : "",
      })),
    });
    $("dope-status").textContent = "Filled from the solution — check it, then save.";
  });

  $("dope-fill").addEventListener("click", () => $("ball-to-dope").click());
  $("dope-add").addEventListener("click", () => {
    $("dope-card").querySelector("tbody").append(
      dopeRow({ distance_m: "", elevation: null, windage: null, note: "" }));
  });
  $("dope-save").addEventListener("click", async () => {
    try {
      const res = await fetch("/api/ballistics/dope", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ card: readDope() }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.dopeLoaded = true;
      $("dope-status").textContent = "Saved for this rifle and load.";
    } catch (err) {
      $("dope-status").textContent = err.message;
    }
  });
  $("dope-clear").addEventListener("click", async () => {
    await fetch("/api/ballistics/dope/delete", { method: "POST" });
    renderDope({ rows: [] });
    $("dope-status").textContent = "Card cleared.";
  });

  $("true-reload").addEventListener("click", loadTruing);
  $("true-run").addEventListener("click", runTruing);

  // The simulation is a separate file; it reads the inputs through this.
  window.SPOTS_BALLISTICS = {
    values: readValues,
    unit: () => state.unit,
    clickValue: () => state.clickValue,
    solved: () => state.solved,
    solve,
  };

  if (window.SPOTS_EQUIPMENT) {
    window.SPOTS_EQUIPMENT.onStatus((message) => { $("ball-status").textContent = message; });
    window.SPOTS_EQUIPMENT.onChange(async () => {
      // Different kit is a different solution entirely, so nothing already
      // worked out survives the change.
      state.solved = null;
      state.dopeLoaded = false;
      if (window.SPOTS_SIM) window.SPOTS_SIM.reset();
      await loadInputs();
      // Changing the load while watching the flight should re-fly it with
      // the new one rather than leaving an empty stage.
      const panel = document.querySelector('.ball-panel[data-panel="sim"]');
      if (panel && !panel.hidden && window.SPOTS_SIM) window.SPOTS_SIM.open();
    });
  }

  loadInputs();
})();
