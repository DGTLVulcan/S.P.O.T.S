// Drives the BDC 3 drawing in reticle.js under a stub DOM and a recording
// canvas.
//
// Measures the bars actually drawn and checks them against the subtensions
// in Meopta's MeoHunter R5 manual, so the picture and the published reticle
// cannot drift apart. Also checks the scope's recorded name selects it, the
// readout names the nearest bar, and a first focal plane scope grows the
// whole reticle with the zoom.
//
// Usage: node reticle_bdc3.js <reticle.js>
const fs = require("fs");

const SIZE = { w: 440, h: 440 };
const CENTRE = { x: SIZE.w / 2, y: SIZE.h / 2 };
const ops = [];

function recorder() {
  const state = { strokeStyle: "", fillStyle: "", lineWidth: 1 };
  const note = (op, args) => ops.push({ op, args, stroke: state.strokeStyle,
                                        fill: state.fillStyle });
  const target = {
    setTransform() {}, clearRect() {}, save() {}, restore() {}, clip() {},
    beginPath() {}, closePath() {}, setLineDash() {}, stroke() {}, fill() {},
    measureText: () => ({ width: 10 }),
    moveTo: (x, y) => note("moveTo", [x, y]),
    lineTo: (x, y) => note("lineTo", [x, y]),
    arc: (x, y, r) => note("arc", [x, y, r]),
    fillText: (t, x, y) => note("fillText", [t, x, y]),
  };
  return new Proxy(target, {
    get: (t, prop) => (prop in t ? t[prop] : state[prop]),
    set: (t, prop, value) => { state[prop] = value; return true; },
  });
}

function makeEl(id) {
  const el = {
    id, value: "", innerHTML: "", _text: "", handlers: {},
    addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); },
    dispatch(name) { (this.handlers[name] || []).forEach((fn) => fn({})); },
    querySelector: () => null,
    querySelectorAll: () => [],
    getContext: () => el._ctx || (el._ctx = recorder()),
  };
  Object.defineProperty(el, "clientWidth", { get: () => SIZE.w });
  Object.defineProperty(el, "clientHeight", { get: () => SIZE.h });
  Object.defineProperty(el, "textContent", {
    get() { return this._text; }, set(v) { this._text = v; },
  });
  return el;
}

const registry = {};
const byId = (id) => (registry[id] ||= makeEl(id));

global.window = global;
global.addEventListener = () => {};
global.devicePixelRatio = 1;
global.getComputedStyle = () => ({ getPropertyValue: () => "" });
global.document = { getElementById: byId, body: makeEl("body") };

// The default scope, as the equipment record describes it.
global.SPOTS_BALLISTICS = {
  scope: () => ({ scope: "Meopta MeoHunter R5 5-25x56 FFP RD BDC 3",
                  reticle: "BDC 3", focal_plane: "ffp", magnification: "5-25x56",
                  magnification_min: 5, magnification_max: 25,
                  reticle_calibration_x: null }),
  unit: () => "mrad",
};

eval(fs.readFileSync(process.argv[2], "utf8"));

const fail = (msg) => { console.log("FAIL - " + msg); process.exit(1); };
const INK = "#14130e";            // the reticle's fallback colour
const MARK = "#d2352f";           // the illuminated dot and target marker
const plain = (id) => (byId(id).innerHTML || byId(id).textContent || "")
  .replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

// Meopta MeoHunter R5 manual, 5-25x56 FFP column: drop below centre and
// full width of each bar, in mrad.
const MANUAL = [
  { drop: 0.63, width: 1.05 },    // M1, L1
  { drop: 1.05, width: 0.52 },    // P, L
  { drop: 1.54, width: 1.64 },    // M2, L2
  { drop: 2.03, width: 0.52 },    // R, L
  { drop: 2.59, width: 2.28 },    // M3, L3
];

function show(elevation, windage) {
  ops.length = 0;
  window.SPOTS_RETICLE.show({ distance_m: 400, elevation, windage }, "mrad");
}

function zoomTo(mag) {
  ops.length = 0;
  byId("reticle-zoom").value = mag;
  byId("reticle-zoom").dispatch("input");
}

// Horizontal reticle-ink segments below centre: the holdover bars.
function bars() {
  const out = [];
  for (let i = 0; i + 1 < ops.length; i += 1) {
    const a = ops[i];
    const b = ops[i + 1];
    if (a.op !== "moveTo" || b.op !== "lineTo" || a.stroke !== INK) continue;
    if (Math.abs(a.args[1] - b.args[1]) > 0.5) continue;
    if (a.args[1] <= CENTRE.y + 0.5) continue;
    out.push({ y: a.args[1] - CENTRE.y, width: Math.abs(b.args[0] - a.args[0]) });
  }
  return out.sort((p, q) => p.y - q.y);
}

// ---- the scope's own reticle is selected ------------------------------
show(2.61, -0.6);
if (byId("reticle-type").value !== "bdc-3") {
  fail(`a scope recorded as "BDC 3" should select bdc-3, got "${byId("reticle-type").value}"`);
}
if (!/matched to the selected scope/i.test(plain("reticle-note"))) {
  fail("the note should say the reticle was matched to the scope");
}
console.log("adopted    : bdc-3 from \"BDC 3\"");

// ---- the drawn bars match the manual ---------------------------------
zoomTo(25);
const drawn = bars();
if (drawn.length !== MANUAL.length) {
  fail(`expected ${MANUAL.length} bars, drew ${drawn.length}`);
}
// The scale comes off the lowest bar; every other bar is then checked
// against it, so this holds whatever the view's pixels per mrad.
const scale = drawn[drawn.length - 1].y / MANUAL[MANUAL.length - 1].drop;
drawn.forEach((bar, i) => {
  const drop = bar.y / scale;
  const width = bar.width / scale;
  if (Math.abs(drop - MANUAL[i].drop) > 0.005) {
    fail(`bar ${i + 1} drawn at ${drop.toFixed(3)} mrad, manual says ${MANUAL[i].drop}`);
  }
  if (Math.abs(width - MANUAL[i].width) > 0.005) {
    fail(`bar ${i + 1} drawn ${width.toFixed(3)} mrad wide, manual says ${MANUAL[i].width}`);
  }
});
console.log("bars       : " + drawn.map((b) => (b.y / scale).toFixed(2)).join(", ")
  + " mrad down, as the manual");

// ---- the illuminated dot is at centre ---------------------------------
const dot = ops.find((o) => o.op === "arc" && o.fill === MARK
  && Math.abs(o.args[0] - CENTRE.x) < 0.01 && Math.abs(o.args[1] - CENTRE.y) < 0.01);
if (!dot) fail("no illuminated dot at the centre");

// ---- first focal plane: the reticle grows with the zoom ----------------
zoomTo(5);
const low = bars();
const ratio = drawn[4].y / low[4].y;
if (Math.abs(ratio - 5) > 0.05) {
  fail(`25x should draw the bars 5x further out than 5x, got ${ratio.toFixed(2)}x`);
}
console.log(`ffp zoom   : bar 5 at ${low[4].y.toFixed(1)}px at 5x, `
  + `${drawn[4].y.toFixed(1)}px at 25x`);

// ---- the readout names the nearest bar ---------------------------------
const cases = [
  [2.61, "bar 5, 0.02 mrad below it"],
  [1.10, "bar 2, 0.05 mrad below it"],
  [1.54, "bar 3, dead on"],
  [0.20, "the dot, 0.20 mrad below it"],
];
for (const [elevation, expected] of cases) {
  show(elevation, 0);
  if (!plain("reticle-readout").includes("Nearest mark " + expected)) {
    fail(`a ${elevation} mrad hold should read "${expected}": ${plain("reticle-readout")}`);
  }
  console.log(`hold ${elevation.toFixed(2)}  : ${expected}`);
}

// Just under the last bar is still on it; well past it is not.
show(2.61, 0);
if (/dial/i.test(plain("reticle-note"))) {
  fail("a hold 0.02 mrad under bar 5 should not be told to dial");
}
show(3.2, 0);
if (!/dial/i.test(plain("reticle-note"))) {
  fail("a hold 0.6 mrad past the last bar should say to dial");
}
console.log("past marks : not at 2.61, yes at 3.20");

console.log("PASS - BDC 3 is drawn to Meopta's subtensions and read by its bars");
