// Drives reticle.js under a stub DOM and a recording canvas.
//
// Two things here are easy to get backwards and impossible to spot by eye,
// because a wrong picture looks exactly as plausible as a right one.
//
// The sign convention: the hold is the OPPOSITE of the dial, so a shot
// needing "4.4 up, 3.2 left" on the turrets has to be drawn with the
// target sitting low and RIGHT of the reticle centre. Inverted, it tells
// you to miss by twice the correction.
//
// The zoom ring: on a second focal plane scope the reading changes with
// the power, and on a first it does not. Invert that ratio and it tells
// you to hold four dots where you need one.
//
// Usage: node reticle_hold.js <reticle.js>
const fs = require("fs");

const SIZE = { w: 440, h: 440 };
const ops = [];

function recorder() {
  const state = { strokeStyle: "", fillStyle: "", font: "", lineWidth: 1,
                  textAlign: "left" };
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
    id, tagName: "DIV", value: "", innerHTML: "", _text: "",
    handlers: {},
    addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); },
    dispatch(name) { (this.handlers[name] || []).forEach((fn) => fn({})); },
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

let SCOPE = null;
global.SPOTS_BALLISTICS = { scope: () => SCOPE, unit: () => "mrad" };

eval(fs.readFileSync(process.argv[2], "utf8"));

const fail = (msg) => { console.log("FAIL - " + msg); process.exit(1); };
const CENTRE = { x: SIZE.w / 2, y: SIZE.h / 2 };

// The marker ring is the only 11px arc drawn; the reticle's own dots are
// sized off the view scale and never reach it.
function marker() {
  const ring = ops.filter((o) => o.op === "arc" && Math.abs(o.args[2] - 11) < 0.01);
  if (ring.length !== 1) fail(`expected one target marker, found ${ring.length}`);
  return { x: ring[0].args[0] - CENTRE.x, y: ring[0].args[1] - CENTRE.y };
}

function show(row, unit) {
  ops.length = 0;
  window.SPOTS_RETICLE.show(row, unit || "mrad");
  return marker();
}

function pick(key) {
  byId("reticle-type").value = key;
  byId("reticle-type").dispatch("change");
}

// Real solver output for this .223 at 500 m in a 16 km/h wind. Elevation is
// what you would dial UP; windage is what you would dial, negative meaning
// left. A 9 o'clock wind blows from the left and pushes the bullet right,
// so it is dialled left.
const FROM_LEFT = { distance_m: 500, elevation: 4.43, windage: -3.17 };
const FROM_RIGHT = { distance_m: 500, elevation: 4.43, windage: 3.17 };
const NO_WIND = { distance_m: 500, elevation: 4.49, windage: 0 };

// ---- the scope's own reticle gets picked -------------------------------
// First, because choosing by hand deliberately stops this happening again.
registry["reticle-type"].value = "";
SCOPE = { scope: "Simmons Pro Target 4-16x40 30mm", reticle: "Mil-Dot",
          focal_plane: "sfp", magnification: "4-16x40" };
window.SPOTS_RETICLE.show({ distance_m: 100, elevation: 0, windage: 0 }, "mrad");
if (byId("reticle-type").value !== "mil-dot") {
  fail("a scope recorded with a Mil-Dot reticle should select mil-dot, got "
       + byId("reticle-type").value);
}
if (!/second focal plane/i.test(byId("reticle-scope").textContent)) {
  fail("an SFP scope has to warn that the spacing only holds at one power");
}
console.log("scope match    : " + byId("reticle-scope").textContent);

// ---- the sign convention ---------------------------------------------
const left = show(FROM_LEFT);
// Canvas y grows downward, so "below centre" is a positive y offset.
if (left.y <= 0) fail(`a dropping shot must sit below centre, got y=${left.y.toFixed(1)}`);
if (left.x <= 0) {
  fail("wind from the left pushes the bullet right, so the target sits RIGHT "
       + `of centre; got x=${left.x.toFixed(1)}`);
}
// The offsets have to be in proportion to the two corrections, or the
// picture is only accidentally in the right quadrant.
const ratio = left.y / left.x;
const want = 4.43 / 3.17;
if (Math.abs(ratio - want) > 0.01) {
  fail(`offsets out of proportion: ${ratio.toFixed(3)}, expected ${want.toFixed(3)}`);
}
console.log(`9 o'clock wind : target ${left.x.toFixed(0)}px right, `
  + `${left.y.toFixed(0)}px below centre`);

const right = show(FROM_RIGHT);
if (right.x >= 0) fail("wind from the right must put the target LEFT of centre");
if (Math.abs(right.x + left.x) > 0.01 || Math.abs(right.y - left.y) > 0.01) {
  fail("reversing the wind should mirror the hold exactly");
}
console.log(`3 o'clock wind : target ${right.x.toFixed(0)}px right, `
  + `${right.y.toFixed(0)}px below centre`);

const calm = show(NO_WIND);
if (Math.abs(calm.x) > 0.01) fail(`no wind must hold dead centre, got x=${calm.x}`);
if (calm.y <= 0) fail("no wind still drops");

// ---- MOA and mrad have to draw the same hold in the same place --------
const MOA_PER_MRAD = 3.437746;
const inMoa = {
  distance_m: 500,
  elevation: 4.43 * MOA_PER_MRAD,
  windage: -3.17 * MOA_PER_MRAD,
};
const converted = show(inMoa, "moa");
if (Math.abs(converted.x - left.x) > 0.05 || Math.abs(converted.y - left.y) > 0.05) {
  fail(`the same hold in MOA landed somewhere else: `
       + `(${converted.x.toFixed(1)}, ${converted.y.toFixed(1)}) vs `
       + `(${left.x.toFixed(1)}, ${left.y.toFixed(1)})`);
}
console.log("MOA card       : same hold, same place on a mrad reticle");

// ---- every reticle agrees on where the target is ----------------------
// The frame changes with the reticle's extent, so the pixels move; the
// direction and the proportion between the two axes must not.
for (const key of window.SPOTS_RETICLE.reticles()) {
  pick(key);
  const at = show(FROM_LEFT);
  if (at.x <= 0 || at.y <= 0) fail(`${key}: target left the low-right quadrant`);
  if (Math.abs(at.y / at.x - want) > 0.01) {
    fail(`${key}: offsets out of proportion at ${(at.y / at.x).toFixed(3)}`);
  }
  console.log(`${key.padEnd(10)} : ${at.x.toFixed(0)}, ${at.y.toFixed(0)} px`);
}

// ---- a hold past the end of the marks has to say so -------------------
pick("mil-dot");
show({ distance_m: 1200, elevation: 18.4, windage: -6.2 });
const note = byId("reticle-note").textContent;
if (!/dial/i.test(note)) {
  fail("a hold past the marks must say to dial instead, got: " + note);
}
console.log("past the marks : " + note.split("—").pop().trim());

// Choosing by hand has to stick, even though the scope still says Mil-Dot.
pick("mrad-tree");
window.SPOTS_RETICLE.show(NO_WIND, "mrad");
if (byId("reticle-type").value !== "mrad-tree") {
  fail("a hand-picked reticle must survive the next update");
}


// ---- the zoom ring, and what each kind of scope does with it -----------
//
// The angle to hold never changes. What the reticle READS changes only on
// a second focal plane scope, because there the marks keep their size on
// the glass while the target grows behind them.
// Some of these nodes are written as markup and some as plain text.
const plain = (id) => (byId(id).innerHTML || byId(id).textContent || "")
  .replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

function reading() {
  const match = /Read off the reticle ([\d.]+) mrad down/.exec(plain("reticle-readout"));
  if (!match) fail("no reticle reading in the readout: " + plain("reticle-readout"));
  return Number(match[1]);
}

function zoomTo(mag) {
  ops.length = 0;
  byId("reticle-zoom").value = mag;
  byId("reticle-zoom").dispatch("input");
}

function useScope(plane) {
  SCOPE = { scope: "Test 4-16x40", reticle: "Mil-Dot", focal_plane: plane,
            magnification: "4-16x40", magnification_min: 4, magnification_max: 16,
            reticle_calibration_x: null };
}

pick("mil-dot");

// Second focal plane: 4.43 mrad is 4.43 dots at the calibrated 16x, half
// that at 8x, a quarter at 4x. Hold the same dot at the wrong power and
// you miss by the ratio, which is the whole reason this control exists.
useScope("sfp");
window.SPOTS_RETICLE.show(FROM_LEFT, "mrad");
const sfp = {};
for (const mag of [16, 8, 4]) {
  zoomTo(mag);
  sfp[mag] = { read: reading(), px: marker() };
  console.log(`sfp ${String(mag).padStart(2)}x  : reads ${sfp[mag].read.toFixed(2)} mrad down`
    + `, marker ${sfp[mag].px.y.toFixed(0)}px below centre`);
}
if (Math.abs(sfp[16].read - 4.43) > 0.01) {
  fail(`at the calibrated power the reading is the angle, got ${sfp[16].read}`);
}
if (Math.abs(sfp[8].read - 4.43 / 2) > 0.01) {
  fail(`half power should halve the reading, got ${sfp[8].read}`);
}
if (Math.abs(sfp[4].read - 4.43 / 4) > 0.01) {
  fail(`quarter power should quarter the reading, got ${sfp[4].read}`);
}
// The reticle is fixed on the glass, so the marker really does move.
if (!(sfp[16].px.y > sfp[8].px.y && sfp[8].px.y > sfp[4].px.y)) {
  fail("on SFP the hold has to move against the marks as the power changes");
}

// First focal plane: the marks grow with the image, so the reading is the
// angle at every power and the hold never leaves its mark.
useScope("ffp");
window.SPOTS_RETICLE.show(FROM_LEFT, "mrad");
for (const mag of [16, 8, 4]) {
  zoomTo(mag);
  const read = reading();
  if (Math.abs(read - 4.43) > 0.01) {
    fail(`FFP must read the same at every power; ${mag}x gave ${read}`);
  }
  console.log(`ffp ${String(mag).padStart(2)}x  : reads ${read.toFixed(2)} mrad down`
    + `, marker ${marker().y.toFixed(0)}px below centre`);
}
// It should still say which plane it is, since that is the difference.
if (!/first focal plane/i.test(plain("reticle-note"))) {
  fail("an FFP scope should say the hold holds at any power: " + plain("reticle-note"));
}

// A calibration the scope actually states beats the assumed top power.
// Plenty of older mil-dots are true at 10x, and assuming 16x there would
// put every hold out by 60%.
SCOPE = { scope: "Test 4-16x40", reticle: "Mil-Dot", focal_plane: "sfp",
          magnification: "4-16x40", magnification_min: 4, magnification_max: 16,
          reticle_calibration_x: 10 };
window.SPOTS_RETICLE.show(FROM_LEFT, "mrad");
zoomTo(10);
if (Math.abs(reading() - 4.43) > 0.01) {
  fail(`at a stated 10x calibration the reading is the angle, got ${reading()}`);
}
zoomTo(16);
if (Math.abs(reading() - 4.43 * 1.6) > 0.01) {
  fail(`16x on a 10x reticle should read 1.6x the angle, got ${reading()}`);
}
if (/assumed/.test(plain("reticle-note"))) {
  fail("a stated calibration must not be reported as assumed");
}
console.log(`stated 10x : reads ${reading().toFixed(2)} mrad down at 16x`);

// A scope nobody has described cannot be zoomed, and must say so rather
// than pretending to a range it does not have.
SCOPE = { scope: "Unknown", reticle: "Mil-Dot", focal_plane: "",
          magnification: "", magnification_min: null, magnification_max: null };
window.SPOTS_RETICLE.show(FROM_LEFT, "mrad");
if (!byId("reticle-zoom").disabled) fail("no magnification means no zoom control");
console.log("no mag     : " + plain("reticle-zoom-value"));

console.log("PASS - the hold is opposite the dial, and tracks the zoom ring the way each focal plane does");
