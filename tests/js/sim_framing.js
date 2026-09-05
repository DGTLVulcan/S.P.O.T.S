// Checks that the simulation actually frames the shot it is given.
//
// The reported fault was that the distance scale along the bottom only
// appeared once you asked for more than 2000 m, and that 50 m drew nothing
// worth looking at. Both came from a camera and a height scale that were
// hard-coded for a 500 m .308, so this drives the real trajectories out of
// the solver at a spread of ranges and asserts that what gets drawn lands
// inside the canvas.
//
// Usage: node sim_framing.js <ballistics_sim.js> <trajectories.json>
const fs = require("fs");

const SOURCE = process.argv[2];
const CASES = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

// ---- a canvas that writes down what it was asked to draw ---------------
const ops = [];
function recorder() {
  const state = { strokeStyle: "", fillStyle: "", font: "", lineWidth: 1 };
  const note = (op, args) => ops.push({ op, args, stroke: state.strokeStyle,
                                        fill: state.fillStyle });
  const target = {
    setTransform() {}, clearRect() {}, save() {}, restore() {},
    beginPath() {}, closePath() {}, setLineDash() {},
    stroke() {}, fill() {},
    measureText: () => ({ width: 10 }),
    moveTo: (x, y) => note("moveTo", [x, y]),
    lineTo: (x, y) => note("lineTo", [x, y]),
    arc: (x, y, r) => note("arc", [x, y, r]),
    fillRect: (x, y, w, h) => note("fillRect", [x, y, w, h]),
    strokeRect: (x, y, w, h) => note("strokeRect", [x, y, w, h]),
    fillText: (t, x, y) => note("fillText", [t, x, y]),
  };
  return new Proxy(target, {
    get: (t, prop) => (prop in t ? t[prop] : state[prop]),
    set: (t, prop, value) => { state[prop] = value; return true; },
  });
}

// ---- the smallest DOM the module needs --------------------------------
let SIZE = { w: 900, h: 400 };
function makeEl(id) {
  const el = {
    id, tagName: "DIV", value: "", innerHTML: "", hidden: false, _text: "",
    dataset: new Proxy({}, { set: (o, k, v) => { o[k] = String(v); return true; } }),
    children: [], classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    handlers: {},
    addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); },
    dispatch(name, ev) { (this.handlers[name] || []).forEach((fn) => fn(ev || {})); },
    append(...kids) { kids.forEach((k) => this.children.push(k)); },
    remove() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    getContext: () => el._ctx || (el._ctx = recorder()),
  };
  Object.defineProperty(el, "clientWidth", { get: () => SIZE.w });
  Object.defineProperty(el, "clientHeight", { get: () => SIZE.h });
  Object.defineProperty(el, "textContent", {
    get() { return this._text; },
    set(v) { this._text = v; this.children = []; },
  });
  return el;
}

const registry = {};
const byId = (id) => (registry[id] ||= makeEl(id));

const simBody = makeEl("sim-tbody");
simBody.tagName = "TBODY";
const simTable = byId("sim-card");
simTable.querySelector = (sel) => (sel === "tbody" ? simBody : null);
simTable.querySelectorAll = () => [];

global.window = global;
global.addEventListener = () => {};
global.performance = { now: () => Date.now() };
global.getComputedStyle = () => ({ getPropertyValue: () => "" });
global.devicePixelRatio = 1;
global.requestAnimationFrame = () => 1;
global.cancelAnimationFrame = () => {};
global.document = {
  getElementById: byId,
  createElement: (tag) => { const e = makeEl(null); e.tagName = tag.toUpperCase(); return e; },
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  body: makeEl("body"),
};

let served = null;
global.fetch = async () => ({ ok: true, json: async () => served });
global.SPOTS_BALLISTICS = {
  values: () => ({}), unit: () => "mrad", solved: () => null, solve: async () => {},
};

eval(fs.readFileSync(SOURCE, "utf8"));

// ---- what the recorded ops mean ---------------------------------------
// The module picks colours through css(), and the stub returns nothing, so
// every mark carries its documented fallback colour. That is what tells
// the trajectory apart from the grid.
const ACCENT = "#2a78d6";       // the flight path
const MUTED = "#898781";        // the distance axis and its labels
const GRID = "#e1e0d9";         // the range gridlines
const SECONDARY = "#52514e";    // the muzzle, and the post at the target

const fail = (msg) => { console.log("FAIL - " + msg); process.exit(1); };

function inspect(name) {
  const path = ops.filter((o) => o.stroke === ACCENT &&
                          (o.op === "moveTo" || o.op === "lineTo"));
  const labels = ops.filter((o) => o.op === "fillText" && o.fill === MUTED &&
                            /^\d+ m$/.test(o.args[0]));
  const grid = ops.filter((o) => o.stroke === GRID &&
                          (o.op === "moveTo" || o.op === "lineTo"));
  const target = ops.filter((o) => o.stroke === SECONDARY &&
                            (o.op === "moveTo" || o.op === "lineTo"));
  const muzzle = ops.filter((o) => o.op === "fillRect");
  return { name, path, labels, grid, target, muzzle };
}

function extent(items, axis) {
  const values = items.map((o) => o.args[axis]);
  return { lo: Math.min(...values), hi: Math.max(...values) };
}

(async () => {
  const report = [];
  for (const size of [{ w: 900, h: 400 }, { w: 520, h: 260 }]) {
    SIZE = size;
    for (const shot of CASES) {
      served = shot;
      ops.length = 0;
      window.SPOTS_SIM.reset();
      ops.length = 0;
      await window.SPOTS_SIM.load();
      if (!window.SPOTS_SIM.hasData()) fail(`${shot.max_distance_m} m never loaded`);

      const label = `${shot.max_distance_m} m @ ${size.w}x${size.h}`;
      const drawn = inspect(label);

      // 1. The distance scale has to be on screen. This is the reported bug:
      //    it used to sit thousands of pixels below the canvas.
      if (drawn.labels.length < 2) {
        fail(`${label}: only ${drawn.labels.length} distance labels drawn`);
      }
      const labelY = extent(drawn.labels, 2);
      const labelX = extent(drawn.labels, 1);
      if (labelY.lo < 0 || labelY.hi > size.h) {
        fail(`${label}: distance scale off canvas, y ${labelY.lo.toFixed(0)}`
             + `..${labelY.hi.toFixed(0)} of ${size.h}`);
      }
      if (labelX.lo < 0 || labelX.hi > size.w) {
        fail(`${label}: distance labels off the side, x ${labelX.lo.toFixed(0)}`
             + `..${labelX.hi.toFixed(0)} of ${size.w}`);
      }

      // 2. The whole flight is in frame, muzzle and target included.
      if (!drawn.path.length) fail(`${label}: no trajectory drawn`);
      const pathX = extent(drawn.path, 0);
      const pathY = extent(drawn.path, 1);
      if (pathX.lo < 0 || pathX.hi > size.w) {
        fail(`${label}: flight runs off the side, x ${pathX.lo.toFixed(0)}`
             + `..${pathX.hi.toFixed(0)} of ${size.w}`);
      }
      if (pathY.lo < 0 || pathY.hi > size.h) {
        fail(`${label}: flight runs off the top or bottom, y `
             + `${pathY.lo.toFixed(0)}..${pathY.hi.toFixed(0)} of ${size.h}`);
      }
      if (!drawn.muzzle.length) fail(`${label}: no muzzle drawn`);
      if (!drawn.target.length) fail(`${label}: no target drawn`);
      const muzzleX = drawn.muzzle[0].args[0];
      if (muzzleX < 0) fail(`${label}: muzzle off the left at x ${muzzleX.toFixed(0)}`);
      const postX = extent(drawn.target, 0);
      const postY = extent(drawn.target, 1);
      if (postX.hi > size.w) {
        fail(`${label}: target off the right at x ${postX.hi.toFixed(0)} of ${size.w}`);
      }
      if (postY.lo < 0 || postY.hi > size.h) {
        fail(`${label}: target post off the frame, y ${postY.lo.toFixed(0)}`
             + `..${postY.hi.toFixed(0)} of ${size.h}`);
      }

      // 3. And the curve has actual shape -- a flat line is what 50 m used
      //    to draw, and it tells you nothing.
      const rise = pathY.hi - pathY.lo;
      if (rise < size.h * 0.2) {
        fail(`${label}: flight is flat, only ${rise.toFixed(0)}px of `
             + `${size.h} tall`);
      }

      report.push(`${label.padEnd(20)} labels ${String(drawn.labels.length).padStart(2)}`
        + `  scale y=${labelY.hi.toFixed(0)}`
        + `  flight x=${pathX.lo.toFixed(0)}..${pathX.hi.toFixed(0)}`
        + `  rise=${rise.toFixed(0)}px`
        + `  ${byId("sim-exaggeration-value").textContent}`);
    }
  }
  report.forEach((line) => console.log(line));
  console.log("PASS - every range frames the flight and its distance scale");
})().catch((err) => fail(err.stack));
