// A side-on view of the shot, drawn in perspective and flown in time.
//
// The projection is hand-rolled onto a 2D canvas, so nothing has to be
// fetched at the range. The camera and the vertical exaggeration are both
// fitted to the flight. Only the height is stretched, by the factor shown
// on the slider; every figure in the readout is the real one.
(function () {
  const canvas = document.getElementById("sim-canvas");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  const $ = (id) => document.getElementById(id);

  const sim = {
    data: null,          // the trajectory from the server
    card: null,          // the come-up rows shown above the stage
    range: null,         // the row currently picked
    playing: false,
    t: 0,                // seconds of flight elapsed
    speed: 0.25,         // playback rate; 1 is real time
    scale: null,         // vertical exaggeration; null follows the fitted one
    lastFrame: 0,
    raf: null,
  };

  // ---- framing and projection -------------------------------------------

  // Edge padding. The left gutter is wider because it carries the drop
  // scale.
  const PAD = { left: 62, right: 34, top: 34, bottom: 44 };
  const FILL = 0.78;   // share of the plot the flight fills at the fitted scale
  const AIR = 0.07;    // a little room above the sight line
  const MAX_SCALE = 3162;   // the top of the slider, 10^3.5

  // Gridline spacing: about ten lines, on a round number.
  function gridStep(range) {
    const steps = [1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000];
    return steps.find((s) => s >= range / 10) || steps[steps.length - 1];
  }

  // Solves the camera from the flight. It sits off to the side and a
  // little above, looking at the middle: downrange runs across the screen
  // and lateral drift into it.
  function fit() {
    const range = sim.data.max_distance_m;
    const width = canvas.clientWidth || 900;
    const height = canvas.clientHeight || 400;

    // The sight line is at y = 0, so it counts towards the extent.
    let lo = 0;
    let hi = 0;
    for (const point of sim.data.points) {
      if (point.y < lo) lo = point.y;
      if (point.y > hi) hi = point.y;
    }
    const span = Math.max(hi - lo, 1e-4);   // a dead flat shot still needs one

    const lateral = Math.max(0.5, range * 0.012);
    // Far enough back to see the whole flight, never so close that the
    // near edge ends up behind the lens.
    const dolly = Math.max(range * 1.15, lateral + 8);
    const near = dolly - lateral;           // nearest corner of the plane

    // The length across pins the focal length on its own, independently of
    // the height scale. The floor covers a canvas too small for the padding.
    const across = Math.max(40, (width - PAD.left - PAD.right) / 2);
    const focal = (across * near) / (range / 2);

    // The plot is a fixed box; the height scale changes how much drop it
    // covers, so the axis numbers move and the box does not.
    const usable = Math.max(60, height - PAD.top - PAD.bottom);
    const fitted = clampScale((FILL * usable * near) / (focal * span));
    const exaggeration = sim.scale === null ? fitted : clampScale(sim.scale);

    // The drop the box spans, top to bottom, at this scale.
    const visible = (usable * near) / (focal * exaggeration);
    const top = hi + visible * AIR;
    const floor = top - visible;             // the distance axis sits here

    return {
      range, width, height, lateral, focal, floor, hi, top, visible,
      fitted, exaggeration,
      step: gridStep(range),
      x: range / 2,
      y: ((top + floor) / 2) * exaggeration,
      z: -dolly,
      cx: PAD.left + across,                 // x = 0 lands on the left gutter
      horizon: PAD.top + usable / 2,
    };
  }

  // Clamps the exaggeration to the slider's range. 1x is true scale:
  // vertical metres drawn the same size as downrange ones.
  function clampScale(value) {
    if (!Number.isFinite(value) || value < 1) return 1;
    return Math.min(value, MAX_SCALE);
  }

  function project(x, y, z, cam) {
    const ex = x - cam.x;
    const ey = y * cam.exaggeration - cam.y;
    const ez = z - cam.z;
    if (ez <= 1) return null;                    // behind the camera
    return {
      sx: cam.cx + (cam.focal * ex) / ez,
      sy: cam.horizon - (cam.focal * ey) / ez,
      scale: cam.focal / ez,
    };
  }

  // ---- drawing ---------------------------------------------------------

  function css(name, fallback) {
    return getComputedStyle(document.body).getPropertyValue(name).trim() || fallback;
  }

  function clear() {
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
      canvas.width = width * ratio;
      canvas.height = height * ratio;
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
  }

  // The distance scale under the flight: a ruled axis with a tick per
  // gridline, plus faint verticals rising from the labelled ranges.
  function drawGrid(cam) {
    const lines = Math.floor(cam.range / cam.step + 1e-6);
    // Label every second tick once they crowd each other.
    const every = lines > 8 ? 2 : 1;
    // One depth and one height, so the axis is exactly level: every foot
    // shares this y.
    const foot = (i) => project(i * cam.step, cam.floor, -cam.lateral, cam);
    const start = foot(0);
    if (!start) return;

    // Verticals at the labelled ranges, for reading the drop at a given
    // distance.
    ctx.lineWidth = 1;
    ctx.strokeStyle = css("--gridline", "#e1e0d9");
    for (let i = 0; i <= lines; i += every) {
      const p = foot(i);
      if (!p) continue;
      ctx.beginPath();
      ctx.moveTo(p.sx, p.sy);
      ctx.lineTo(p.sx, PAD.top);
      ctx.stroke();
    }

    // Full width, since the range is not always a whole number of
    // gridlines.
    ctx.strokeStyle = css("--ink-muted", "#898781");
    ctx.beginPath();
    ctx.moveTo(PAD.left, start.sy);
    ctx.lineTo(cam.width - PAD.right, start.sy);
    ctx.stroke();
    for (let i = 0; i <= lines; i += 1) {
      const p = foot(i);
      if (!p) continue;
      ctx.beginPath();
      ctx.moveTo(p.sx, p.sy);
      ctx.lineTo(p.sx, p.sy + (i % every ? 3 : 6));
      ctx.stroke();
    }

    ctx.fillStyle = css("--ink-muted", "#898781");
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "center";
    for (let i = 0; i <= lines; i += every) {
      const p = foot(i);
      if (p) ctx.fillText(`${i * cam.step} m`, p.sx, p.sy + 19);
    }
  }

  // Round drop values, about six of them, on a 1/2/5 decade.
  function dropStep(span) {
    const decade = Math.pow(10, Math.floor(Math.log10(span / 6)));
    return [1, 2, 5].map((m) => decade * m).find((s) => s >= span / 6)
      || decade * 10;
  }

  // The scale up the left, reading drop from the line of sight.
  //
  // Depth sets the vertical scale in this projection, and depth changes
  // across the flight rather than along it, so a scale read at the near
  // edge holds at every range on the plot.
  function drawDropScale(cam) {
    const step = dropStep(cam.visible);
    const cm = step < 1;          // centimetres until the drop is metres deep
    const at = (value) => project(0, value, -cam.lateral, cam);
    const right = cam.width - PAD.right;
    const first = Math.ceil(cam.floor / step - 1e-9);
    const last = Math.floor(cam.top / step + 1e-9);

    ctx.font = "11px system-ui, sans-serif";
    ctx.lineWidth = 1;
    for (let i = first; i <= last; i += 1) {
      const row = at(i * step);
      if (!row) continue;

      // No gridline at zero: the dashed sight line is already there.
      if (i !== 0) {
        ctx.strokeStyle = css("--gridline", "#e1e0d9");
        ctx.beginPath();
        ctx.moveTo(PAD.left, row.sy);
        ctx.lineTo(right, row.sy);
        ctx.stroke();
      }

      ctx.strokeStyle = css("--ink-muted", "#898781");
      ctx.beginPath();
      ctx.moveTo(PAD.left - 5, row.sy);
      ctx.lineTo(PAD.left, row.sy);
      ctx.stroke();

      ctx.fillStyle = css("--ink-muted", "#898781");
      ctx.textAlign = "right";
      const shown = cm ? Math.round(i * step * 100)
                       : Math.round(i * step * 10) / 10;
      ctx.fillText(`${shown}`, PAD.left - 8, row.sy + 4);
    }

    const top = at(cam.top);
    const bottom = at(cam.floor);
    if (top && bottom) {
      ctx.strokeStyle = css("--ink-muted", "#898781");
      ctx.beginPath();
      ctx.moveTo(PAD.left, top.sy);
      ctx.lineTo(PAD.left, bottom.sy);
      ctx.stroke();
    }

    ctx.fillStyle = css("--ink-muted", "#898781");
    ctx.textAlign = "left";
    ctx.fillText(cm ? "drop (cm)" : "drop (m)", 4, PAD.top - 14);
  }

  function drawSightLine(cam) {
    // Drop is measured from the line of sight, so here it is just y = 0 --
    // the line the bullet crosses at the zero and falls away from.
    const range = cam.range;
    ctx.save();
    ctx.setLineDash([6, 5]);
    ctx.strokeStyle = css("--ink-muted", "#898781");
    ctx.lineWidth = 1;
    ctx.beginPath();
    let started = false;
    for (let d = 0; d <= range; d += range / 80) {
      const p = project(d, 0, 0, cam);
      if (!p) continue;
      started ? ctx.lineTo(p.sx, p.sy) : ctx.moveTo(p.sx, p.sy);
      started = true;
    }
    ctx.stroke();
    ctx.restore();

    const label = project(range * 0.12, 0, 0, cam);
    if (label) {
      ctx.fillStyle = css("--ink-muted", "#898781");
      ctx.font = "11px system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText("line of sight", label.sx, label.sy - 6);
    }
  }

  function drawPath(cam, upto) {
    const points = sim.data.points;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";

    // Only the part already flown is drawn, so playback is a flight rather
    // than a bullet chasing a drawn line.
    ctx.strokeStyle = css("--accent", "#2a78d6");
    ctx.beginPath();
    let started = false;
    for (const point of points) {
      if (point.t > upto) break;
      const p = project(point.x, point.y, point.z, cam);
      if (!p) continue;
      started ? ctx.lineTo(p.sx, p.sy) : ctx.moveTo(p.sx, p.sy);
      started = true;
    }
    ctx.stroke();

    // Where it dropped through transonic, if it did.
    const transonic = points.find((p) => p.mach < sim.data.transonic_mach && p.t <= upto);
    if (transonic) {
      const p = project(transonic.x, transonic.y, transonic.z, cam);
      if (p) {
        ctx.fillStyle = css("--serious", "#ec835a");
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.font = "10px system-ui, sans-serif";
        ctx.textAlign = "center";
        // Below the dot, clear of the sight line's own label.
        ctx.fillText("transonic", p.sx, p.sy + 16);
      }
    }
  }

  function drawMuzzle(cam) {
    const start = sim.data.points[0];
    const p = project(0, start ? start.y : 0, 0, cam);
    if (!p) return;
    // Downrange of the axis, clear of the drop scale's gutter.
    ctx.fillStyle = css("--ink-secondary", "#52514e");
    ctx.fillRect(p.sx + 1, p.sy - 2.5, 16, 5);
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("muzzle", p.sx + 14, p.sy - 9);
  }

  function drawTarget(cam) {
    const last = sim.data.points[sim.data.points.length - 1];
    if (!last) return;
    const aim = project(last.x, 0, 0, cam);              // on the line of sight
    const hit = project(last.x, last.y, last.z, cam);
    if (!aim || !hit) return;

    // A post in the target's plane rather than a target face, since the
    // vertical is stretched. The span from aim to impact is the drop.
    const top = Math.min(aim.sy, hit.sy) - 16;
    const bottom = Math.max(aim.sy, hit.sy) + 16;
    ctx.strokeStyle = css("--ink-secondary", "#52514e");
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(aim.sx, top);
    ctx.lineTo(aim.sx, bottom);
    ctx.stroke();
    ctx.fillStyle = css("--ink-muted", "#898781");
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "center";
    // No distance label: the axis already labels the far end.
    ctx.fillText("target", aim.sx, top - 6);
  }

  function drawBullet(cam, upto) {
    const at = sampleAt(upto);
    if (!at) return;
    const p = project(at.x, at.y, at.z, cam);
    if (!p) return;
    const radius = Math.max(3.5, p.scale * 0.02);
    ctx.fillStyle = css("--center-marker", "#e34948");
    ctx.beginPath();
    ctx.arc(p.sx, p.sy, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = css("--surface", "#fff");
    ctx.lineWidth = 1.5;
    ctx.stroke();
    return at;
  }

  function drawImpact(cam) {
    const last = sim.data.points[sim.data.points.length - 1];
    const p = project(last.x, last.y, last.z, cam);
    if (!p) return;
    ctx.strokeStyle = css("--center-marker", "#e34948");
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(p.sx, p.sy, 9, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(p.sx - 13, p.sy);
    ctx.lineTo(p.sx + 13, p.sy);
    ctx.moveTo(p.sx, p.sy - 13);
    ctx.lineTo(p.sx, p.sy + 13);
    ctx.stroke();
  }

  function sampleAt(time) {
    const points = sim.data.points;
    if (!points.length) return null;
    if (time >= points[points.length - 1].t) return points[points.length - 1];
    for (let i = 1; i < points.length; i += 1) {
      if (points[i].t >= time) {
        const a = points[i - 1];
        const b = points[i];
        const span = b.t - a.t;
        const f = span <= 0 ? 0 : (time - a.t) / span;
        return {
          x: a.x + (b.x - a.x) * f,
          y: a.y + (b.y - a.y) * f,
          z: a.z + (b.z - a.z) * f,
          v: a.v + (b.v - a.v) * f,
          t: time,
          mach: a.mach + (b.mach - a.mach) * f,
        };
      }
    }
    return points[0];
  }

  function render() {
    if (!sim.data) return;
    clear();
    const cam = fit();
    const finished = sim.t >= sim.data.flight_time_s;
    drawGrid(cam);
    drawDropScale(cam);

    // Clipped to the plot, so a flight scaled past the box does not run
    // over the axis labels.
    ctx.save();
    ctx.beginPath();
    ctx.rect(PAD.left - 8, PAD.top,
             cam.width - PAD.left - PAD.right + 16,
             cam.height - PAD.top - PAD.bottom);
    ctx.clip();
    drawSightLine(cam);
    drawPath(cam, sim.t);
    drawMuzzle(cam);
    drawTarget(cam);
    if (finished) drawImpact(cam);
    const at = drawBullet(cam, sim.t);
    ctx.restore();

    showScale(cam);
    readout(at, finished);
  }

  function readout(at, finished) {
    if (!at) return;
    const drop = at.y * 100;
    const drift = at.z * 100;
    $("sim-readout").innerHTML = [
      ["Range", `${at.x.toFixed(0)} m`],
      ["Time", `${at.t.toFixed(3)} s`],
      ["Velocity", `${(at.v / 0.3048).toFixed(0)} fps`],
      ["Mach", at.mach.toFixed(2)],
      ["Drop", `${drop.toFixed(1)} cm`],
      ["Drift", `${drift.toFixed(1)} cm`],
    ].map(([label, value]) =>
      `<span class="sim-stat"><span class="sim-stat-label">${label}</span>${value}</span>`
    ).join("");
    $("sim-state").textContent = finished
      ? `Impact at ${sim.data.max_distance_m} m after `
        + `${sim.data.flight_time_s.toFixed(3)} s of flight.`
      : (sim.playing ? "In flight…" : "Paused mid-flight.");
  }

  // ---- playback --------------------------------------------------------

  function tick(now) {
    if (!sim.playing) return;
    const delta = (now - sim.lastFrame) / 1000;
    sim.lastFrame = now;
    sim.t += delta * sim.speed;
    if (sim.t >= sim.data.flight_time_s) {
      sim.t = sim.data.flight_time_s;
      stop(true);
      return;
    }
    render();
    sim.raf = requestAnimationFrame(tick);
  }

  function play() {
    if (!sim.data) return;
    // Play on a finished flight restarts it.
    if (sim.t >= sim.data.flight_time_s) sim.t = 0;
    sim.playing = true;
    sim.lastFrame = performance.now();
    $("sim-play").textContent = "Pause";
    sim.raf = requestAnimationFrame(tick);
  }

  function stop(finished) {
    sim.playing = false;
    if (sim.raf) cancelAnimationFrame(sim.raf);
    // Stopping shows the completed flight rather than freezing mid-air.
    if (!finished && sim.data) sim.t = sim.data.flight_time_s;
    $("sim-play").textContent = "Play";
    render();
  }

  // ---- the come-up table, and choosing a range off it -------------------

  // The same rows the Come-up tab shows. Clicking one flies that range.
  function renderTable(card) {
    const drawn = window.SPOTS_PICKER.render($("sim-card"), card, choose);
    $("sim-pick-hint").textContent = drawn
      ? "Pick a range to fly it."
      : "No solution yet — work one out on the Come-up tab.";
  }

  function markChosen(distance) {
    window.SPOTS_PICKER.mark($("sim-card"), distance);
  }

  async function choose(distance) {
    // A different range drops differently, so go back to the fitted scale.
    if (distance !== sim.range) sim.scale = null;
    sim.range = distance;
    markChosen(distance);
    await load();
  }

  // Called by the page when a fresh solution lands.
  function cardChanged(card) {
    sim.card = card;
    renderTable(card);
    const wanted = window.SPOTS_PICKER.keep(card, sim.range);
    if (wanted !== null) choose(wanted);
  }

  // Opening the tab: show the existing solution, or ask for one.
  async function open() {
    const api = window.SPOTS_BALLISTICS;
    const existing = api && api.solved && api.solved();
    if (existing) {
      if (sim.card !== existing) cardChanged(existing);
      else if (!sim.data) load();
      return;
    }
    $("sim-state").textContent = "Working out the solution…";
    if (api && api.solve) {
      await api.solve();          // this calls cardChanged when it lands
      if (!(api.solved && api.solved())) {
        $("sim-state").textContent =
          "Nothing to fly yet — the Come-up tab says what is missing.";
      }
    }
  }

  function reset() {
    stop(false);
    sim.data = null;
    sim.card = null;
    sim.range = null;
    renderTable(null);
    $("sim-load").textContent = "";
    clear();
  }

  // ---- loading ---------------------------------------------------------

  async function load() {
    const api = window.SPOTS_BALLISTICS;
    $("sim-state").textContent = "Working out the flight…";
    try {
      const values = api ? api.values() : {};
      const res = await fetch("/api/ballistics/trajectory", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          shot: values,
          max_distance_m: sim.range || values.max_distance_m,
          samples: 240,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      sim.data = data;
      sim.t = data.flight_time_s;        // show the finished flight first
      // Name the load the flight was solved from.
      const load = data.load || {};
      const kit = data.equipment || {};
      const bullet = load.bullet_grains ? `${load.bullet_grains} gr, ` : "";
      $("sim-load").textContent = kit.ammo
        ? `${kit.ammo} — ${bullet}${Math.round(load.muzzle_velocity_fps)} fps, `
          + `BC ${load.ballistic_coefficient} ${String(load.drag_model || "").toUpperCase()}`
          + (kit.rifle ? `, from ${kit.rifle}` : "")
        : "No ammo selected — the fields on the Come-up tab are being used instead.";
      $("sim-summary").textContent =
        `Launched ${data.launch_angle_deg}° above the line of sight, `
        + `${data.flight_time_s.toFixed(3)} s to ${data.max_distance_m} m, `
        + `arriving at ${Math.round(data.impact_velocity_ms / 0.3048)} fps.`;
      render();
      $("sim-state").textContent = "Ready — press Play.";
    } catch (err) {
      $("sim-state").textContent = err.message;
      sim.data = null;
      clear();
    }
  }

  // ---- controls --------------------------------------------------------

  $("sim-play").addEventListener("click", () => (sim.playing ? stop(false) : play()));
  $("sim-restart").addEventListener("click", () => {
    if (!sim.data) return;
    sim.t = 0;
    render();
    play();
  });
  $("sim-reload").addEventListener("click", load);

  $("sim-speed").addEventListener("change", () => {
    sim.speed = Number($("sim-speed").value);
  });
  // The slider is the exaggeration itself, on a log scale: the useful
  // value runs from 1x to several hundred depending on the range.
  $("sim-exaggeration").addEventListener("input", () => {
    sim.scale = clampScale(Math.pow(10, Number($("sim-exaggeration").value)));
    render();
  });

  $("sim-fit").addEventListener("click", () => {
    sim.scale = null;
    render();
  });

  function showScale(cam) {
    const slider = $("sim-exaggeration");
    // While following the fit, the slider tracks wherever it landed.
    if (slider && sim.scale === null) {
      slider.value = Math.log10(cam.exaggeration);
    }
    $("sim-exaggeration-value").textContent = cam.exaggeration < 1.02
      ? "1x — true scale, no exaggeration"
      : `${Math.round(cam.exaggeration)}x vertical`
        + (sim.scale === null ? " (fitted)" : "");
  }

  window.addEventListener("resize", () => { if (sim.data) render(); });

  // The page calls these when the tab opens, once the canvas has a size.
  window.SPOTS_SIM = {
    load, open, reset, cardChanged,
    stop: () => stop(false),
    hasData: () => !!sim.data,
  };
})();
