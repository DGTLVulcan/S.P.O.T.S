// A side-on view of the shot, drawn in perspective and flown in time.
//
// Hand-rolled projection onto a 2D canvas rather than a 3D library: this
// has to work at the range, on the Pi's own network, with no internet to
// fetch anything from.
//
// The one thing to understand before reading the numbers off it: the
// vertical scale is exaggerated, and has to be. A .308 drops about two
// metres over five hundred, which is a slope of 0.4% -- at true scale the
// trajectory is a straight line and the picture says nothing. Every figure
// in the readout is real; only the height of the curve is stretched, and
// the factor is on screen so it can't be mistaken for the real shape.
//
// The camera and that factor are both fitted to the flight rather than
// fixed, because the shape changes enormously with distance: a .223 drops
// 3.7 cm over 50 m and 279 m over 2000. One hard-coded camera frames one
// of those and loses the other off the edge of the canvas entirely.
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
    stretch: 1,          // height scale, as a multiple of the fitted one
    lastFrame: 0,
    raf: null,
  };

  // ---- framing and projection -------------------------------------------

  const PAD = { x: 58, top: 30, bottom: 44 };  // room for the edge labels
  const FILL = 0.62;      // share of the usable height the flight fills
  const FLOOR_GAP = 0.28; // how far under the flight the scale plane sits

  // About ten gridlines, spaced on a number a shooter reads without
  // thinking: 25s and 50s and 100s, never 37s.
  function gridStep(range) {
    const steps = [1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000];
    return steps.find((s) => s >= range / 10) || steps[steps.length - 1];
  }

  // Camera sits off to the side and a little above, looking at the middle
  // of the flight. Downrange runs across the screen, lateral drift into
  // it, which is what makes a side-on view read as three dimensional.
  //
  // Every part of it is solved from the flight itself, so the muzzle, the
  // target and the distance scale are all in frame at any range.
  function fit() {
    const range = sim.data.max_distance_m;
    const width = canvas.clientWidth || 900;
    const height = canvas.clientHeight || 400;

    // The sight line is drawn at y = 0, so it counts towards the extent.
    let lo = 0;
    let hi = 0;
    for (const point of sim.data.points) {
      if (point.y < lo) lo = point.y;
      if (point.y > hi) hi = point.y;
    }
    const span = Math.max(hi - lo, 1e-4);   // a dead flat shot still needs one
    const floor = lo - span * FLOOR_GAP;

    const lateral = Math.max(0.5, range * 0.012);
    // Far enough back to see the whole flight, and never so close that the
    // near edge of the plane ends up behind the lens.
    const dolly = Math.max(range * 1.15, lateral + 8);
    const near = dolly - lateral;           // nearest corner of the plane

    // Fit the length across first. That constraint does not depend on the
    // height scale, so it pins the focal length on its own. The floors are
    // for a canvas too small to hold the padding, on a phone in portrait.
    const across = Math.max(40, width / 2 - PAD.x);
    const focal = (across * near) / (range / 2);

    // Then scale the height so the flight fills the frame it is given.
    const usable = Math.max(60, height - PAD.top - PAD.bottom);
    const fitted = (FILL * usable * near) / (focal * (hi - floor));

    const exaggeration = fitted * sim.stretch;
    return {
      range, width, height, lateral, focal, floor, fitted, exaggeration,
      step: gridStep(range),
      x: range / 2,
      y: ((hi + floor) / 2) * exaggeration,  // centre the flight vertically
      z: -dolly,
      horizon: PAD.top + usable / 2,
    };
  }

  function project(x, y, z, cam) {
    const ex = x - cam.x;
    const ey = y * cam.exaggeration - cam.y;
    const ez = z - cam.z;
    if (ez <= 1) return null;                    // behind the camera
    return {
      sx: cam.width / 2 + (cam.focal * ex) / ez,
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

  // The distance scale under the flight.
  //
  // This used to be a five-line ground plane, on the idea that a receding
  // grid gives the eye something to judge depth against. It never could:
  // the plane is only ever about a hundredth of the camera distance wide,
  // so all five lines landed within a pixel of each other and drew a grey
  // smear. What it was really for is reading a range off the bottom, so
  // that is what it now is -- a ruled axis with a tick per gridline.
  function drawGrid(cam) {
    const lines = Math.floor(cam.range / cam.step + 1e-6);
    // Label every second tick once they start crowding each other.
    const every = lines > 8 ? 2 : 1;
    // The axis sits at one depth and one height, so it is exactly level:
    // every foot below shares this y.
    const foot = (i) => project(i * cam.step, cam.floor, -cam.lateral, cam);
    const start = foot(0);
    const end = foot(lines);
    if (!start || !end) return;

    // Faint verticals at the labelled ranges, so the drop at 300 m can be
    // read off without tracing the curve back by eye.
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

    ctx.strokeStyle = css("--ink-muted", "#898781");
    ctx.beginPath();
    ctx.moveTo(start.sx, start.sy);
    ctx.lineTo(end.sx, end.sy);
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

  function drawSightLine(cam) {
    // The trajectory is measured from the line of sight, so in this frame
    // the sight line is simply y = 0 -- the reference the bullet crosses
    // at the zero and falls away from afterwards.
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

    // Already flown: solid. Still to come: only once the flight is over,
    // so watching it is a flight rather than a bullet chasing a drawn line.
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

    // Mark where it went supersonic-to-transonic, if it did.
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
        // Below the dot: on a long shot this point is right on the line of
        // sight, and above it the two labels sit on top of each other.
        ctx.fillText("transonic", p.sx, p.sy + 16);
      }
    }
  }

  function drawMuzzle(cam) {
    const start = sim.data.points[0];
    const p = project(0, start ? start.y : 0, 0, cam);
    if (!p) return;
    ctx.fillStyle = css("--ink-secondary", "#52514e");
    ctx.fillRect(p.sx - 16, p.sy - 2.5, 18, 5);
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("muzzle", p.sx - 7, p.sy - 9);
  }

  function drawTarget(cam) {
    const last = sim.data.points[sim.data.points.length - 1];
    if (!last) return;
    const aim = project(last.x, 0, 0, cam);              // on the line of sight
    const hit = project(last.x, last.y, last.z, cam);
    if (!aim || !hit) return;

    // A post in the target's plane rather than a drawing of a target face.
    // The vertical here is stretched, so a face at true scale would be a
    // sliver and one big enough to see would be a lie about the scale.
    // Spanning aim to impact says the useful thing anyway: that gap is how
    // far under your point of aim the shot lands.
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
    // No distance here: the axis already labels the far end, and the two
    // labels landed on top of each other at short range.
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
    drawSightLine(cam);
    drawPath(cam, sim.t);
    drawMuzzle(cam);
    drawTarget(cam);
    if (finished) drawImpact(cam);
    const at = drawBullet(cam, sim.t);
    readout(at, finished);
    // Both numbers matter: the one on the slider, and what it works out to
    // against the real shape of the flight.
    $("sim-exaggeration-value").textContent =
      sim.stretch.toFixed(2) + "x fit (" + Math.round(cam.exaggeration)
      + "x true)";
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
    // Starting from the end means starting again, which is what pressing
    // play on a finished flight is asking for.
    if (sim.t >= sim.data.flight_time_s) sim.t = 0;
    sim.playing = true;
    sim.lastFrame = performance.now();
    $("sim-play").textContent = "Pause";
    sim.raf = requestAnimationFrame(tick);
  }

  function stop(finished) {
    sim.playing = false;
    if (sim.raf) cancelAnimationFrame(sim.raf);
    // Stopping shows the completed flight rather than freezing mid-air:
    // the whole point of stopping is to look at the trajectory.
    if (!finished && sim.data) sim.t = sim.data.flight_time_s;
    $("sim-play").textContent = "Play";
    render();
  }

  // ---- the come-up table, and choosing a range off it -------------------

  // The same rows the Come-up tab shows. Clicking one flies the shot to
  // that range, so the picture and the number you would dial sit together.
  function renderTable(card) {
    const body = $("sim-card").querySelector("tbody");
    body.textContent = "";
    if (!card || !card.rows || !card.rows.length) {
      $("sim-pick-hint").textContent =
        "No solution yet — work one out on the Come-up tab.";
      return;
    }
    const unit = card.unit.toUpperCase();
    card.rows.forEach((row) => {
      const tr = document.createElement("tr");
      tr.className = "sim-row" + (row.transonic ? " is-transonic" : "");
      tr.dataset.distance = row.distance_m;
      [
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
      ].forEach((text) => {
        const td = document.createElement("td");
        td.textContent = text;
        tr.append(td);
      });
      tr.addEventListener("click", () => choose(Number(tr.dataset.distance)));
      body.append(tr);
    });
    $("sim-pick-hint").textContent = "Pick a range to fly it.";
  }

  function markChosen(distance) {
    $("sim-card").querySelectorAll("tbody tr").forEach((tr) => {
      tr.classList.toggle("is-chosen", Number(tr.dataset.distance) === distance);
    });
  }

  async function choose(distance) {
    sim.range = distance;
    markChosen(distance);
    await load();
  }

  // Called by the page when a fresh solution has been worked out.
  function cardChanged(card) {
    sim.card = card;
    renderTable(card);
    const rows = (card && card.rows) || [];
    const wanted = rows.some((r) => r.distance_m === sim.range)
      ? sim.range
      : (rows.length ? rows[rows.length - 1].distance_m : null);
    if (wanted !== null) choose(wanted);
  }

  // Opening the tab: show the solution that exists, or ask for one.
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
      // Name the load. The flight is solved from the selected ammo, and the
      // whole thing is meaningless if you are looking at the wrong one.
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

  $("sim-speed").addEventListener("change", (ev) => {
    sim.speed = Number(ev.target.value);
  });
  $("sim-exaggeration").addEventListener("input", (ev) => {
    sim.stretch = Number(ev.target.value);
    render();
  });

  window.addEventListener("resize", () => { if (sim.data) render(); });

  // The page only draws this once you open the tab, so the canvas has a
  // measured size to project into.
  window.SPOTS_SIM = {
    load, open, reset, cardChanged,
    stop: () => stop(false),
    hasData: () => !!sim.data,
  };
})();
