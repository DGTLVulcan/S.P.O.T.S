// The scope picture: where the target centre has to sit if you dial nothing.
//
// The hold is the come-up reversed. A shot needing 4.3 mrad of UP on the
// turret is held by putting the target 4.3 mrad BELOW the centre, and a
// "dial right" correction puts it to the right. The target therefore lands
// where the bullet would have, had you aimed dead centre.
//
// Magnification only moves where that angle falls on the glass, and the
// two kinds of scope do opposite things. A first focal plane reticle is
// magnified with the target, so a mark is worth the same angle at every
// power and the hold never leaves its mark; zoom only changes how much is
// in view. A second focal plane reticle keeps its size on the glass while
// the target grows, so each mark is worth less angle as you zoom in and
// the hold moves -- 4.4 mrad is 4.4 dots at the calibrated power and 2.2
// at half of it.
(function () {
  const canvas = document.getElementById("reticle-canvas");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  const $ = (id) => document.getElementById(id);

  // 1 mrad in MOA. The card uses the turret's unit, the reticle its own,
  // and the two are often different.
  const MOA_PER_MRAD = 3.437746;

  // Apparent field of the eyepiece, in mrad of true field times power --
  // about 19 degrees, typical of a variable scope. It sets how much glass
  // the picture shows and nothing else; the hold figures do not use it.
  const APPARENT_MRAD = 330;

  const view = {
    row: null, unit: "mrad", choice: "mil-dot",
    matched: false, touched: false,
    mag: null,          // where the zoom ring is
    scope: null,        // what we know about the glass
    card: null,         // the solution the table above is showing
    range: null,        // the row picked off it
  };

  // ---- the reticles ----------------------------------------------------
  //
  // `extent` is how far the marks reach from centre, in the reticle's own
  // unit. Past it there is nothing to hold against.
  const RETICLES = {
    "mil-dot": {
      label: "Mil-Dot", unit: "mrad", extent: 5, marked: true,
      note: "Dots at 1 mrad. The gap between two dots is one mil.",
      draw: drawMilDot,
    },
    "mrad-hash": {
      label: "mrad hash (0.5 mrad)", unit: "mrad", extent: 6, marked: true,
      note: "Hashes every 0.5 mrad, long ones on the whole mrad.",
      draw: drawMradHash,
    },
    "mrad-tree": {
      label: "mrad tree (holdover grid)", unit: "mrad", extent: 10, marked: true,
      note: "A wind row under every whole mrad, so both holds are one point.",
      draw: drawMradTree,
    },
    "moa-hash": {
      label: "MOA hash (1 MOA)", unit: "moa", extent: 20, marked: true,
      note: "Hashes every 1 MOA, long ones every 5.",
      draw: drawMoaHash,
    },
    duplex: {
      label: "Duplex (no marks)", unit: "mrad", extent: 2.5, marked: false,
      note: "No reference marks: a hold on this is an estimate by eye.",
      draw: drawDuplex,
    },
    fine: {
      label: "Fine crosshair", unit: "mrad", extent: 0, marked: false,
      note: "No reference marks: a hold on this is an estimate by eye.",
      draw: drawFine,
    },
  };

  // Maps a scope's recorded reticle name onto one of the drawings above.
  // Only unambiguous matches; anything else returns null.
  function guess(name) {
    const text = (name || "").toLowerCase();
    if (!text) return null;
    if (text.includes("mil-dot") || text.includes("mil dot")
        || text.includes("mildot")) return "mil-dot";
    if (text.includes("tree") || text.includes("ebr") || text.includes("tremor")
        || text.includes("christmas")) return "mrad-tree";
    if (text.includes("moa")) return "moa-hash";
    if (text.includes("duplex")) return "duplex";
    if (text.includes("mrad") || text.includes("mil-r") || text.includes("p4f")
        || text.includes("mrd")) return "mrad-hash";
    return null;
  }

  // ---- drawing ---------------------------------------------------------

  function css(name, fallback) {
    return getComputedStyle(document.body).getPropertyValue(name).trim() || fallback;
  }

  // The sight picture has its own palette rather than following the page
  // theme: a reticle is black against a daylit target in either mode.
  const FIELD = () => css("--scope-field", "#edebe4");
  const INK = () => css("--scope-ink", "#14130e");
  const RIM = () => css("--scope-rim", "#a9a69e");
  const MARK = () => css("--scope-mark", "#d2352f");

  // Marks closer together than this cannot be told apart on screen.
  const MIN_TICK_PX = 7;

  // How many marks to skip so the ones drawn stay legible. The multipliers
  // are per reticle, so the surviving ticks land on round numbers.
  function stride(v, step, steps) {
    const usable = steps || [1, 2, 5, 10, 20];
    return usable.find((n) => step * n * v.scale >= MIN_TICK_PX)
      || usable[usable.length - 1];
  }

  function cross(v, reach, width) {
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(v.cx - reach * v.scale, v.cy);
    ctx.lineTo(v.cx + reach * v.scale, v.cy);
    ctx.moveTo(v.cx, v.cy - reach * v.scale);
    ctx.lineTo(v.cx, v.cy + reach * v.scale);
    ctx.stroke();
  }

  // The heavy bars running in from the edge of the glass.
  function posts(v, from) {
    ctx.lineWidth = 4;
    ctx.beginPath();
    [[-1, 0], [1, 0], [0, -1], [0, 1]].forEach(([dx, dy]) => {
      ctx.moveTo(v.cx + dx * from * v.scale, v.cy + dy * from * v.scale);
      ctx.lineTo(v.cx + dx * v.half, v.cy + dy * v.half);
    });
    ctx.stroke();
  }

  function tick(v, along, across, vertical) {
    const a = along * v.scale;
    const b = across * v.scale;
    // Set every time, since the posts leave the line width at 4px.
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    if (vertical) {
      ctx.moveTo(v.cx - b, v.cy - a);
      ctx.lineTo(v.cx + b, v.cy - a);
    } else {
      ctx.moveTo(v.cx + a, v.cy - b);
      ctx.lineTo(v.cx + a, v.cy + b);
    }
    ctx.stroke();
  }

  function dot(v, x, y, radius) {
    ctx.beginPath();
    ctx.arc(v.cx + x * v.scale, v.cy - y * v.scale, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  function number(v, text, x, y, dx, dy) {
    ctx.font = "600 11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(text, v.cx + x * v.scale + (dx || 0),
                 v.cy - y * v.scale + (dy || 4));
  }

  function drawMilDot(v) {
    cross(v, 5, 1.5);
    posts(v, 5);
    // A mil-dot is about 0.2 mrad across -- two or three pixels. Floored a
    // little above life size so the dots stay countable.
    const r = Math.max(3, 0.1 * v.scale);
    for (let i = 1; i <= 4; i += 1) {
      [[i, 0], [-i, 0], [0, i], [0, -i]].forEach(([x, y]) => dot(v, x, y, r));
    }
  }

  function drawMradHash(v) {
    cross(v, 6, 1.5);
    posts(v, 6);
    const step = stride(v, 0.5, [1, 2, 4, 12]);
    for (let i = step; i <= 12; i += step) {
      const at = i * 0.5;
      const across = i % 2 === 0 ? 0.3 : 0.15;
      tick(v, at, across, true);
      tick(v, -at, across, true);
      tick(v, at, across, false);
      tick(v, -at, across, false);
    }
    for (let mrad = 2; mrad <= 6; mrad += 2) {
      number(v, String(mrad), 0, -mrad, 12);
      number(v, String(mrad), mrad, 0, 0, 18);
      number(v, String(mrad), -mrad, 0, 0, 18);
    }
  }

  function drawMradTree(v) {
    cross(v, 10, 1.5);
    posts(v, 10);
    const r = Math.max(1.8, 0.07 * v.scale);
    const step = stride(v, 0.5, [1, 2, 4, 20]);
    for (let i = step; i <= 20; i += step) {
      const at = i * 0.5;
      const across = i % 2 === 0 ? 0.3 : 0.15;
      tick(v, at, across, true);
      if (at <= 6) {
        tick(v, at, across, false);
        tick(v, -at, across, false);
      }
    }
    // The tree: a row of wind dots under each whole mrad, widening with
    // depth as the wind hold grows faster than the drop.
    for (let drop = 1; drop <= 10; drop += 1) {
      const reach = Math.min(4, Math.max(1, Math.round(drop / 2)));
      for (let i = 1; i <= reach; i += 1) {
        dot(v, i * 0.5, -drop, r);
        dot(v, -i * 0.5, -drop, r);
      }
    }
    // Left of the tree, clear of the widest wind row.
    for (let mrad = 2; mrad <= 10; mrad += 2) number(v, String(mrad), -2.5, -mrad);
  }

  function drawMoaHash(v) {
    cross(v, 20, 1.5);
    posts(v, 20);
    const step = stride(v, 1);
    for (let i = step; i <= 20; i += step) {
      const across = i % 5 === 0 ? 1.0 : 0.5;
      tick(v, i, across, true);
      tick(v, -i, across, true);
      tick(v, i, across, false);
      tick(v, -i, across, false);
    }
    for (let moa = 5; moa <= 20; moa += 5) {
      number(v, String(moa), 0, -moa, 13);
      number(v, String(moa), moa, 0, 0, 20);
      number(v, String(moa), -moa, 0, 0, 20);
    }
  }

  function drawDuplex(v) {
    cross(v, 2.5, 1.5);
    posts(v, 2.5);
  }

  function drawFine(v) {
    cross(v, v.halfUnits, 1.2);
  }

  // ---- what the zoom ring does -----------------------------------------

  // Half the true field of view at a given power, in the reticle's unit.
  function halfField(mag, unit) {
    const mrad = APPARENT_MRAD / (2 * Math.max(mag, 0.1));
    return unit === "moa" ? mrad * MOA_PER_MRAD : mrad;
  }

  function ffp() {
    return !!(view.scope && view.scope.focal_plane === "ffp");
  }

  // The power at which a second focal plane reticle subtends what it
  // claims. Top power unless the scope records otherwise.
  function calibration() {
    const scope = view.scope || {};
    const stated = Number(scope.reticle_calibration_x);
    if (Number.isFinite(stated) && stated > 0) return { mag: stated, stated: true };
    const top = Number(scope.magnification_max);
    if (Number.isFinite(top) && top > 0) return { mag: top, stated: false };
    return { mag: null, stated: false };
  }

  // ---- the picture -----------------------------------------------------

  function clear() {
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || 440;
    const height = canvas.clientHeight || 440;
    if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
      canvas.width = width * ratio;
      canvas.height = height * ratio;
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { width, height };
  }

  // The hold in the reticle's unit, signed the way the picture is drawn:
  // x right of centre, y above it, both opposite to the dialled
  // correction. `angle` is the real angle; `marks` is what to read off the
  // glass, which on a second focal plane scope depends on the power.
  function hold(reticle) {
    if (!view.row) return null;
    let up = Number(view.row.elevation);
    let right = Number(view.row.windage);
    if (!Number.isFinite(up) || !Number.isFinite(right)) return null;
    if (view.unit !== reticle.unit) {
      const factor = reticle.unit === "moa" ? MOA_PER_MRAD : 1 / MOA_PER_MRAD;
      up *= factor;
      right *= factor;
    }

    // First focal plane marks are worth the same angle at every power, so
    // the reading is the angle. Second focal plane marks scale from the
    // calibration power.
    const cal = calibration();
    let ratio = 1;
    if (!ffp() && cal.mag && view.mag) ratio = view.mag / cal.mag;

    return {
      unit: reticle.unit,
      angle: { x: -right, y: -up },
      marks: { x: -right * ratio, y: -up * ratio },
      ratio,
      calibration: cal,
    };
  }

  function render() {
    const size = clear();
    const reticle = RETICLES[view.choice] || RETICLES["mil-dot"];
    const target = hold(reticle);
    const side = Math.min(size.width, size.height);
    const radius = side / 2 - 6;
    const cx = size.width / 2;
    const cy = size.height / 2;

    // How much of the reticle the eyepiece shows: shrinks with zoom on a
    // first focal plane scope, fixed on a second.
    const cal = calibration();
    let visible = Math.max(reticle.extent, 1) * 1.15;
    if (view.mag) {
      visible = ffp() ? halfField(view.mag, reticle.unit)
        : (cal.mag ? halfField(cal.mag, reticle.unit) : visible);
    }

    // Zooms the picture out if the hold falls outside the glass, so it is
    // still visible sitting beyond the edge.
    const need = target
      ? Math.max(Math.abs(target.marks.x), Math.abs(target.marks.y)) : 0;
    const halfUnits = Math.max(visible, need * 1.2, reticle.extent * 1.05, 0.5);
    const v = { cx, cy, halfUnits, scale: radius / halfUnits,
                half: radius * (visible / halfUnits) };

    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, v.half, 0, Math.PI * 2);
    ctx.fillStyle = FIELD();
    ctx.fill();
    ctx.clip();
    ctx.strokeStyle = INK();
    ctx.fillStyle = INK();
    reticle.draw(v);
    ctx.restore();

    // The edge of the glass; anything past it is out of view in a real
    // scope, and drawn outside the ring here.
    ctx.strokeStyle = RIM();
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(cx, cy, v.half, 0, Math.PI * 2);
    ctx.stroke();

    if (target) drawTarget(v, target, reticle);
    readout(target, reticle);
  }

  function drawTarget(v, target, reticle) {
    const x = v.cx + target.marks.x * v.scale;
    const y = v.cy - target.marks.y * v.scale;

    // A line from centre to the hold, so the offset reads as a direction.
    ctx.save();
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = MARK();
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(v.cx, v.cy);
    ctx.lineTo(x, y);
    ctx.stroke();
    ctx.restore();

    // Fixed size on screen: it marks a point, and no target size is known.
    ctx.strokeStyle = MARK();
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 11, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x - 16, y);
    ctx.lineTo(x + 16, y);
    ctx.moveTo(x, y - 16);
    ctx.lineTo(x, y + 16);
    ctx.stroke();
    ctx.fillStyle = MARK();
    ctx.beginPath();
    ctx.arc(x, y, 2.5, 0, Math.PI * 2);
    ctx.fill();

    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "center";
    // Under the marker when the hold is small, clear of the crosshair.
    const above = Math.abs(y - v.cy) > 34;
    ctx.fillText(offGlass(target, v) ? "outside the field of view"
      : (pastMarks(target, reticle) ? "past the marks" : "target centre"),
      x, above ? y - 21 : y + 30);
  }

  function pastMarks(target, reticle) {
    return reticle.marked
      && Math.max(Math.abs(target.marks.x), Math.abs(target.marks.y)) > reticle.extent;
  }

  function offGlass(target, v) {
    const r = Math.hypot(target.marks.x, target.marks.y) * v.scale;
    return r > v.half;
  }

  function say(value, unit) {
    return `${Math.abs(value).toFixed(2)} ${unit === "moa" ? "MOA" : "mrad"}`;
  }

  function readout(target, reticle) {
    const note = $("reticle-note");
    if (!target) {
      $("reticle-readout").innerHTML = "";
      if (note) note.textContent = "Pick a range above to work out the hold.";
      return;
    }
    // Phrased as a hold, which is the instruction, rather than as a sign.
    const up = -target.angle.y;
    const right = -target.angle.x;
    const unitName = target.unit === "moa" ? "MOA" : "mrad";
    const parts = [
      ["Elevation", `${say(up, target.unit)} ${up >= 0 ? "up" : "down"}`],
      ["Windage", Math.abs(right) < 0.005 ? "none"
        : `${say(right, target.unit)} ${right >= 0 ? "right" : "left"}`],
    ];
    // What to read off the glass. The same number on a first focal plane
    // scope; on a second it is the one that matters.
    parts.push(["Read off the reticle",
      `${Math.abs(target.marks.y).toFixed(2)} ${unitName} `
      + `${target.marks.y >= 0 ? "up" : "down"}`
      + (Math.abs(target.marks.x) < 0.005 ? ""
        : `, ${Math.abs(target.marks.x).toFixed(2)} `
          + `${target.marks.x >= 0 ? "right" : "left"}`)]);
    $("reticle-readout").innerHTML = parts.map(([label, text]) =>
      `<span class="sim-stat"><span class="sim-stat-label">${label}</span>${text}</span>`
    ).join("");

    if (!note) return;
    const lines = [reticle.note];
    if (view.mag) {
      if (ffp()) {
        lines.push(`First focal plane, so the marks are worth the same at `
          + `every power — the hold is the same at ${view.mag.toFixed(1)}x as `
          + `anywhere else.`);
      } else if (target.calibration.mag) {
        const worth = target.calibration.mag / view.mag;
        lines.push(`Second focal plane: one mark is worth `
          + `${worth.toFixed(2)} ${unitName} at ${view.mag.toFixed(1)}x, against `
          + `1 ${unitName} at ${target.calibration.mag.toFixed(1)}x`
          + (target.calibration.stated ? "" : " (assumed — top power)") + ".");
      }
    }
    if (pastMarks(target, reticle)) {
      lines.push("This hold runs off the end of the marks — at this range you "
                 + "have to dial, or zero further out.");
    }
    if (view.matched) lines.push("Reticle matched to the selected scope.");
    note.textContent = lines.join(" ");
  }

  // ---- wiring ----------------------------------------------------------

  const picker = $("reticle-type");
  if (picker) {
    picker.innerHTML = Object.entries(RETICLES)
      .map(([key, r]) => `<option value="${key}">${r.label}</option>`).join("");
    picker.value = view.choice;
    picker.addEventListener("change", () => {
      // Chosen by hand, so stop overriding it from the scope.
      view.touched = true;
      view.choice = picker.value;
      view.matched = false;
      render();
    });
  }

  const zoom = $("reticle-zoom");
  if (zoom) {
    zoom.addEventListener("input", () => {
      view.mag = Number(zoom.value);
      showZoom();
      render();
    });
  }

  function showZoom() {
    const out = $("reticle-zoom-value");
    if (!out) return;
    if (!view.mag) {
      out.textContent = "no magnification recorded";
      return;
    }
    const field = halfField(view.mag, "mrad") * 2;
    out.textContent = `${view.mag.toFixed(1)}x — about ${field.toFixed(0)} mrad `
      + `(${(field * 0.1).toFixed(1)} m at 100 m) across the glass`;
  }

  // Adopts the selected scope's reticle and zoom range, where recorded.
  function adopt() {
    const api = window.SPOTS_BALLISTICS;
    const scope = api && api.scope ? api.scope() : null;
    view.scope = scope;
    const label = $("reticle-scope");
    if (!scope || !scope.scope) {
      if (label) label.textContent = "";
      return;
    }
    const named = [scope.reticle, scope.magnification].filter(Boolean).join(", ");
    if (label) {
      label.textContent = named ? `${scope.scope} — ${named}` : scope.scope;
    }
    const key = guess(scope.reticle);
    if (key && picker && !view.touched) {
      view.choice = key;
      view.matched = true;
      picker.value = key;
    }

    const low = Number(scope.magnification_min);
    const high = Number(scope.magnification_max);
    const usable = Number.isFinite(low) && Number.isFinite(high) && low > 0;
    if (zoom) {
      zoom.disabled = !usable;
      if (usable) {
        zoom.min = low;
        zoom.max = high;
        // Fine enough to feel continuous, coarse enough to land on the
        // numbers the ring is marked with.
        zoom.step = high - low > 12 ? 0.5 : 0.1;
        if (!view.mag || view.mag < low || view.mag > high) {
          // Top power, where a second focal plane reticle is usually true.
          view.mag = high;
        }
        zoom.value = view.mag;
      } else {
        view.mag = null;
      }
    }
    showZoom();

    if (scope.focal_plane === "sfp" && label && named) {
      label.textContent += " — second focal plane, so the spacing only holds "
        + "at the scope's calibrated magnification";
    } else if (scope.focal_plane === "ffp" && label && named) {
      label.textContent += " — first focal plane, so the spacing holds at any power";
    }
  }

  // ---- the come-up rows above the picture -------------------------------
  //
  // Its own table, independent of the simulation's: the range you want a
  // hold for is rarely the one you last watched fly.
  function renderTable(card) {
    const drawn = window.SPOTS_PICKER
      && window.SPOTS_PICKER.render($("scope-card"), card, pick);
    const hint = $("scope-pick-hint");
    if (hint) {
      hint.textContent = drawn
        ? "Pick a range to see the hold for it."
        : "No solution yet — work one out on the Come-up tab.";
    }
  }

  function pick(distance) {
    view.range = distance;
    window.SPOTS_PICKER.mark($("scope-card"), distance);
    show(window.SPOTS_PICKER.rowFor(view.card, distance),
         view.card && view.card.unit);
  }

  function show(row, unit) {
    view.row = row || null;
    view.unit = unit === "moa" ? "moa" : "mrad";
    adopt();
    render();
  }

  // Called by the page when a fresh solution lands.
  function cardChanged(card) {
    view.card = card;
    renderTable(card);
    const wanted = window.SPOTS_PICKER.keep(card, view.range);
    if (wanted !== null) pick(wanted);
    else show(null, view.unit);
  }

  // Opening the tab. The canvas has no measured size until its panel is
  // shown, so nothing is drawn before this runs.
  async function open() {
    const api = window.SPOTS_BALLISTICS;
    const existing = api && api.solved && api.solved();
    if (existing) {
      if (view.card !== existing) cardChanged(existing);
      else show(view.row, view.unit);
      return;
    }
    renderTable(null);
    show(null, view.unit);
    if (api && api.solve) await api.solve();   // calls cardChanged when it lands
  }

  function reset() {
    view.card = null;
    view.range = null;
    renderTable(null);
    show(null, view.unit);
  }

  window.addEventListener("resize", () => { if (view.row) render(); });

  window.SPOTS_RETICLE = {
    show, open, reset, cardChanged,
    clear() { show(null, view.unit); },
    reticles: () => Object.keys(RETICLES),
  };
})();
