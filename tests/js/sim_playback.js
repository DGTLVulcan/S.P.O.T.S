// Drives ballistics_sim.js under a stub DOM and a stub canvas.
//
// The behaviour worth pinning down is the one that was asked for: stopping
// shows the completed flight, not a bullet frozen in mid-air.
const fs = require("fs");

function makeEl(id) {
  const el = {
    id, tagName: "DIV", value: "", innerHTML: "", hidden: false, _text: "",
    dataset: {}, children: [], type: "", step: "",
    clientWidth: 900, clientHeight: 400, width: 900, height: 400,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
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

global.fetch = async () => ({ ok: true, json: async () => trajectory });
global.SPOTS_BALLISTICS = { values: () => ({ max_distance_m: 500 }), unit: () => "mrad" };

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

  console.log("PASS - stop shows the impact frame and play restarts");
})().catch((err) => fail(err.stack));
