// Drives ballistics_sim.js under a stub DOM and a stub canvas.
//
// The behaviour worth pinning down is the one that was asked for: stopping
// shows the completed flight, not a bullet frozen in mid-air.
const fs = require("fs");

function makeEl(id) {
  const el = {
    id, tagName: "DIV", value: "", innerHTML: "", hidden: false, _text: "",
    // The real DOM stringifies everything put in dataset; the code under
    // test relies on that when it reads a distance back out.
    dataset: new Proxy({}, { set: (o, k, v) => { o[k] = String(v); return true; } }),
    children: [], type: "", step: "",
    clientWidth: 900, clientHeight: 400, width: 900, height: 400,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    handlers: {},
    addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); },
    dispatch(name, ev) { (this.handlers[name] || []).forEach((fn) => fn(ev || {})); },
    click() { this.dispatch("click"); },
    append(...kids) { kids.forEach((k) => this.children.push(k)); },
    remove() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    // Every canvas call is a no-op: this checks the flight logic, not pixels.
    getContext: () => new Proxy({}, {
      get: (_t, prop) => (prop === "canvas" ? el
        : (prop === "setTransform" || prop === "measureText"
            ? () => ({ width: 10 }) : () => {})),
      set: () => true,
    }),
  };
  Object.defineProperty(el, "textContent", {
    get() { return this._text; },
    set(v) { this._text = v; this.children = []; },
  });
  return el;
}

const registry = {};
const byId = (id) => (registry[id] ||= makeEl(id));

// The come-up table above the stage needs a tbody rows can go into.
const simBody = makeEl("sim-tbody");
simBody.tagName = "TBODY";
simBody.querySelectorAll = function (sel) {
  return sel.includes("tr") ? this.children.filter((c) => c.tagName === "TR") : [];
};
const simTable = byId("sim-card");
simTable.append(simBody);
simTable.querySelector = (sel) => (sel === "tbody" ? simBody : null);
simTable.querySelectorAll = (sel) =>
  (sel.includes("tbody tr") ? simBody.children.filter((c) => c.tagName === "TR") : []);

global.window = global;
global.addEventListener = () => {};
global.performance = { now: () => Date.now() };
global.getComputedStyle = () => ({ getPropertyValue: () => "" });
global.devicePixelRatio = 1;

let frames = [];
global.requestAnimationFrame = (fn) => { frames.push(fn); return frames.length; };
global.cancelAnimationFrame = () => {};

global.document = {
  getElementById: byId,
  createElement: (tag) => { const e = makeEl(null); e.tagName = tag.toUpperCase(); return e; },
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  body: makeEl("body"),
};

// A trajectory shaped like the real one: starts below the sight line,
// crosses it at the zero, ends nearly two metres low.
const points = [];
for (let i = 1; i <= 240; i += 1) {
  const x = (500 / 240) * i;
  const t = x / 700;
  points.push({ x, y: -0.045 + x * 0.0011 - x * x * 0.0000094,
                z: -x * x * 0.0000033, v: 800 - x * 0.55, t,
                mach: (800 - x * 0.55) / 340 });
}
const trajectory = {
  points, launch_angle_deg: 0.0712, sight_height_mm: 45, zero_distance_m: 100,
  max_distance_m: 500, flight_time_s: points[points.length - 1].t,
  impact_velocity_ms: points[points.length - 1].v, speed_of_sound_ms: 340,
  transonic_mach: 1.2,
};

const card = {
  unit: "mrad", click_value: 0.1, transonic_from_m: null, stability: null,
  air_density: 1.225, density_ratio: 1, speed_of_sound_ms: 340,
  spin_drift_included: false,
  rows: [100, 200, 300, 400, 500].map((d) => ({
    distance_m: d, drop_cm: -d * 0.4, elevation: d * 0.008, elevation_clicks: Math.round(d * 0.08),
    windage: d * 0.002, windage_clicks: Math.round(d * 0.02), velocity_fps: 2600 - d * 1.8,
    energy_j: 4000 - d * 4, time_s: d / 700, mach: 2.3 - d * 0.0016, transonic: false,
  })),
};

let requestedRange = null;
global.fetch = async (url, opts) => {
  if (opts && opts.body) {
    const body = JSON.parse(opts.body);
    if (body.max_distance_m) requestedRange = body.max_distance_m;
  }
  return { ok: true, json: async () => trajectory };
};
global.SPOTS_BALLISTICS = {
  values: () => ({ max_distance_m: 500 }), unit: () => "mrad",
  solved: () => card, solve: async () => {},
};

eval(fs.readFileSync(process.argv[2], "utf8"));

const fail = (msg) => { console.log("FAIL - " + msg); process.exit(1); };

(async () => {
  await window.SPOTS_SIM.load();
  if (!window.SPOTS_SIM.hasData()) fail("the trajectory never loaded");
  console.log("loaded:", byId("sim-state").textContent);

  // On arrival it should already show the finished flight, so the
  // trajectory is visible before you press anything.
  if (!byId("sim-readout").innerHTML.includes("Range")) fail("no readout after load");
  const atLoad = byId("sim-state").textContent;

  // Play, advance a couple of frames, and it should be in flight.
  byId("sim-play").click();
  if (byId("sim-play").textContent !== "Pause") fail("play didn't become pause");
  const start = performance.now();
  frames.forEach((fn) => fn(start + 30));
  frames = [];
  const midway = byId("sim-state").textContent;
  console.log("mid-flight:", midway);

  // Stop must show the impact frame, not freeze the bullet where it is.
  byId("sim-play").click();
  const stopped = byId("sim-state").textContent;
  console.log("stopped   :", stopped);
  if (!stopped.startsWith("Impact at")) fail("stopping did not show the impact frame");
  if (byId("sim-play").textContent !== "Play") fail("pause didn't become play");

  // The readout at the stop should be the impact numbers.
  const readout = byId("sim-readout").innerHTML;
  if (!readout.includes("500 m")) fail("the stopped readout isn't at the target");

  // Playing again from the finished state should restart the flight.
  byId("sim-play").click();
  frames.forEach((fn) => fn(performance.now() + 5));
  frames = [];
  if (byId("sim-state").textContent.startsWith("Impact at")) {
    fail("play from the end didn't restart");
  }
  console.log("replayed  :", byId("sim-state").textContent);

  // ---- the come-up table above the stage ----
  await window.SPOTS_SIM.open();
  const rows = simBody.children.filter((c) => c.tagName === "TR");
  if (rows.length !== 5) fail(`expected 5 come-up rows, got ${rows.length}`);
  console.log(`table rows : ${rows.length}`);

  // The furthest range is flown by default.
  const chosen = rows.filter((r) => r.classList.contains("is-chosen"));
  if (chosen.length !== 1) fail(`expected exactly one chosen row, got ${chosen.length}`);
  if (chosen[0].dataset.distance !== "500") fail("the longest range wasn't picked by default");
  if (requestedRange !== 500) fail(`asked for ${requestedRange} m, expected 500`);

  // Clicking a different row flies that range instead.
  rows[2].dispatch("click");
  await new Promise((r) => setTimeout(r, 20));
  if (requestedRange !== 300) fail(`clicking 300 m asked for ${requestedRange} m`);
  const nowChosen = rows.filter((r) => r.classList.contains("is-chosen"));
  if (nowChosen.length !== 1 || nowChosen[0].dataset.distance !== "300") {
    fail("the clicked row didn't become the chosen one");
  }
  console.log(`clicked 300m -> requested ${requestedRange} m, one row marked`);

  console.log("PASS - stop shows the impact frame, play restarts, rows pick the range");
})().catch((err) => fail(err.stack));
