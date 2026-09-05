// The scope picture: where the target centre has to sit if you dial nothing.
//
// This is the come-up read backwards. If the shot needs 4.3 mrad of UP on
// the turret, then leaving the turret alone means putting the reticle
// centre 4.3 mrad above the target -- which is the same thing as saying
// the target sits 4.3 mrad BELOW the centre. Wind works the same way: a
// correction of "dial right" becomes "hold right", and the target sits to
// the left of centre by that much.
//
// So the target lands in the picture exactly where the bullet would have
// landed had you aimed dead centre, which is worth knowing because it is
// the one part of this that people get backwards.
(function () {
  const canvas = document.getElementById("reticle-canvas");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  const $ = (id) => document.getElementById(id);

  // 1 mrad in MOA. The card comes in whichever unit the scope's turrets
  // use; the reticle has a unit of its own, and they are often different.
  const MOA_PER_MRAD = 3.437746;

  const view = { row: null, unit: "mrad", choice: "mil-dot",
                 matched: false, touched: false };

  // ---- the reticles ----------------------------------------------------
  //
  // `extent` is how far the marks reach from centre, in the reticle's own
  // unit. Past that there is nothing to hold against, which is a real
  // limit worth drawing rather than hiding.
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

  // What a scope's recorded reticle name most likely is. Only obvious
  // matches -- guessing wrong here would put the wrong marks on screen.
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

  function cross(v, reach, width) {
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(v.cx - reach * v.scale, v.cy);
    ctx.lineTo(v.cx + reach * v.scale, v.cy);
    ctx.moveTo(v.cx, v.cy - reach * v.scale);
    ctx.lineTo(v.cx, v.cy + reach * v.scale);
    ctx.stroke();
  }

  // The heavy bars that run in from the edge of the glass on most hunting
  // and tactical reticles.
  function posts(v, from) {
    const edge = v.half;
    ctx.lineWidth = 4;
    ctx.beginPath();
    [[-1, 0], [1, 0], [0, -1], [0, 1]].forEach(([dx, dy]) => {
      ctx.moveTo(v.cx + dx * from * v.scale, v.cy + dy * from * v.scale);
      ctx.lineTo(v.cx + dx * edge, v.cy + dy * edge);
    });
    ctx.stroke();
  }

  function tick(v, along, across, vertical) {
    const a = along * v.scale;
    const b = across * v.scale;
    // Set every time: the posts run at 4px and hash marks drawn after them
    // inherit it, which turns a fine reticle into a row of blobs.
    ctx.lineWidth = 1.2;
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

  function number(v, text, x, y) {
    ctx.font = "10px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(text, v.cx + x * v.scale, v.cy - y * v.scale + 3);
  }

  function drawMilDot(v) {
    cross(v, 5, 1);
    posts(v, 5);
    const r = Math.max(1.6, 0.1 * v.scale);
    for (let i = 1; i <= 4; i += 1) {
      [[i, 0], [-i, 0], [0, i], [0, -i]].forEach(([x, y]) => dot(v, x, y, r));
    }
  }

  function drawMradHash(v) {
    cross(v, 6, 1);
    posts(v, 6);
    for (let i = 1; i <= 12; i += 1) {
      const at = i * 0.5;
      const long = i % 2 === 0;
      const across = long ? 0.3 : 0.15;
      tick(v, at, across, true);            // above centre
      tick(v, -at, across, true);           // below centre
      tick(v, at, across, false);           // right
      tick(v, -at, across, false);          // left
    }
    for (let mrad = 2; mrad <= 6; mrad += 2) {
      number(v, String(mrad), 0.55, -mrad);
      number(v, String(mrad), mrad, -0.55);
      number(v, String(mrad), -mrad, -0.55);
    }
  }

  function drawMradTree(v) {
    cross(v, 10, 1);
    posts(v, 10);
    const r = Math.max(1.3, 0.07 * v.scale);
    for (let i = 1; i <= 20; i += 1) {
      const at = i * 0.5;
      tick(v, at, i % 2 === 0 ? 0.3 : 0.15, true);     // straight down
      if (at <= 6) {
        tick(v, at, i % 2 === 0 ? 0.3 : 0.15, false);
        tick(v, -at, i % 2 === 0 ? 0.3 : 0.15, false);
      }
    }
    // The tree itself: a row of wind dots under each whole mrad, widening
    // with depth because the wind hold grows faster than the drop does.
    for (let drop = 1; drop <= 10; drop += 1) {
      const reach = Math.min(4, Math.max(1, Math.round(drop / 2)));
      for (let i = 1; i <= reach; i += 1) {
        dot(v, i * 0.5, -drop, r);
        dot(v, -i * 0.5, -drop, r);
      }
    }
    for (let mrad = 2; mrad <= 10; mrad += 2) number(v, String(mrad), 0.55, -mrad);
  }

  function drawMoaHash(v) {
    cross(v, 20, 1);
    posts(v, 20);
    for (let i = 1; i <= 20; i += 1) {
      const long = i % 5 === 0;
      const across = long ? 1.0 : 0.5;
      tick(v, i, across, true);
      tick(v, -i, across, true);
      tick(v, i, across, false);
      tick(v, -i, across, false);
    }
    for (let moa = 5; moa <= 20; moa += 5) {
      number(v, String(moa), 1.9, -moa);
      number(v, String(moa), moa, -1.9);
      number(v, String(moa), -moa, -1.9);
    }
  }

  function drawDuplex(v) {
    cross(v, 2.5, 1);
    posts(v, 2.5);
  }

  function drawFine(v) {
    cross(v, v.halfUnits, 1);
  }

  // ---- the picture -----------------------------------------------------

  function clear() {
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || 420;
    const height = canvas.clientHeight || 420;
    if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
      canvas.width = width * ratio;
      canvas.height = height * ratio;
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { width, height };
  }

  // The hold, in the reticle's own unit and signed the way the picture is
  // drawn: x right of centre, y above it. Both come out opposite to the
  // correction you would otherwise have dialled.
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
    return { x: -right, y: -up, unit: reticle.unit };
  }

  function render() {
    const size = clear();
    const reticle = RETICLES[view.choice] || RETICLES["mil-dot"];
    const target = hold(reticle);
    const side = Math.min(size.width, size.height);
    const radius = side / 2 - 6;
    const cx = size.width / 2;
    const cy = size.height / 2;

    // The glass, and the black beyond it.
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fillStyle = css("--surface", "#ffffff");
    ctx.fill();
    ctx.clip();

    // Frame far enough out to hold the marks, and further still when the
    // hold runs past the end of them.
    const reach = Math.max(reticle.extent, 1);
    const need = target ? Math.max(Math.abs(target.x), Math.abs(target.y)) : 0;
    const halfUnits = Math.max(reach * 1.15, need * 1.25);
    const v = { cx, cy, half: radius, halfUnits, scale: radius / halfUnits };

    ctx.strokeStyle = css("--ink-primary", "#26251f");
    ctx.fillStyle = css("--ink-primary", "#26251f");
    reticle.draw(v);

    if (target) drawTarget(v, target, reticle);
    ctx.restore();

    ctx.strokeStyle = css("--border", "#d9d7cf");
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.stroke();

    readout(target, reticle);
  }

  function drawTarget(v, target, reticle) {
    const x = v.cx + target.x * v.scale;
    const y = v.cy - target.y * v.scale;

    // A line from centre to the hold, so the offset reads as a direction
    // and not just two marks that happen to be apart.
    ctx.save();
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = css("--center-marker", "#e34948");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(v.cx, v.cy);
    ctx.lineTo(x, y);
    ctx.stroke();
    ctx.restore();

    // Fixed size on screen on purpose: this marks a point, and drawing it
    // at some angular size would be claiming a target size nobody gave us.
    ctx.strokeStyle = css("--center-marker", "#e34948");
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
    ctx.fillStyle = css("--center-marker", "#e34948");
    ctx.beginPath();
    ctx.arc(x, y, 2.5, 0, Math.PI * 2);
    ctx.fill();

    const past = reticle.marked
      && Math.max(Math.abs(target.x), Math.abs(target.y)) > reticle.extent;
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(past ? "past the marks" : "target centre", x, y - 21);
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
    // Named as a hold, which is the instruction: the sign convention only
    // confuses things once it is on screen.
    const up = -target.y;
    const right = -target.x;
    const parts = [
      ["Elevation", `${say(up, target.unit)} ${up >= 0 ? "up" : "down"}`],
      ["Windage", Math.abs(right) < 0.005 ? "none"
        : `${say(right, target.unit)} ${right >= 0 ? "right" : "left"}`],
      ["Target sits", `${say(target.y, target.unit)} `
        + `${target.y >= 0 ? "above" : "below"} centre, `
        + `${say(target.x, target.unit)} ${target.x >= 0 ? "right" : "left"}`],
    ];
    $("reticle-readout").innerHTML = parts.map(([label, text]) =>
      `<span class="sim-stat"><span class="sim-stat-label">${label}</span>${text}</span>`
    ).join("");

    if (!note) return;
    const lines = [reticle.note];
    if (reticle.marked
        && Math.max(Math.abs(target.x), Math.abs(target.y)) > reticle.extent) {
      lines.push("This hold runs off the end of the marks — at this range you "
                 + "have to dial, or zero further out.");
    }
    if (view.matched) lines.push("Matched to the selected scope.");
    note.textContent = lines.join(" ");
  }

  // ---- wiring ----------------------------------------------------------

  const picker = $("reticle-type");
  if (picker) {
    picker.innerHTML = Object.entries(RETICLES)
      .map(([key, r]) => `<option value="${key}">${r.label}</option>`).join("");
    picker.value = view.choice;
    picker.addEventListener("change", () => {
      // Once it has been chosen by hand, stop overriding it from the scope.
      view.touched = true;
      view.choice = picker.value;
      view.matched = false;
      render();
    });
  }

  // The scope's own reticle, when it is named clearly enough to be sure.
  function adopt() {
    const api = window.SPOTS_BALLISTICS;
    const scope = api && api.scope ? api.scope() : null;
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
    // On a second focal plane scope the marks only subtend what they claim
    // at one magnification, which is the difference between a hold that
    // lands and one that misses by half the correction.
    if (scope.focal_plane === "sfp" && label && named) {
      label.textContent += " — second focal plane, so the spacing only holds "
        + "at the scope's calibrated magnification";
    }
  }

  window.addEventListener("resize", () => { if (view.row) render(); });

  window.SPOTS_RETICLE = {
    // Called by the simulation whenever a different come-up row is chosen.
    show(row, unit) {
      view.row = row || null;
      view.unit = unit === "moa" ? "moa" : "mrad";
      adopt();
      render();
    },
    clear() {
      view.row = null;
      render();
    },
    reticles: () => Object.keys(RETICLES),
  };
})();
