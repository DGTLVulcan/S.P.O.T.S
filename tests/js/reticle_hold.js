// Drives reticle.js under a stub DOM and a recording canvas.
//
// What actually needs pinning down here is the sign convention, because it
// is the one thing in this feature that is easy to get backwards and
// impossible to spot by eye: the hold is the OPPOSITE of the dial, so a
// shot needing "4.4 up, 3.2 left" on the turrets has to be drawn with the
// target sitting low and RIGHT of the reticle centre. Get it inverted and
// the picture still looks perfectly plausible while telling you to miss by
// twice the correction.
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

console.log("PASS - the hold is drawn opposite the dial, in every unit and reticle");
