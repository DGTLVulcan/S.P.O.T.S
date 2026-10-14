// Drives depth.js under a stub DOM with a fake server.
//
// Checks what the card shows with and without the ZWO, that "Use as target
// distance" hands the figure to the setup strip, and the correction flow.
//
// Usage: node depth_card.js <depth.js>
const fs = require("fs");

function makeEl(id, extra) {
  return Object.assign({
    id, hidden: false, textContent: "", value: "", handlers: {}, events: [],
    addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); },
    dispatch(name) { (this.handlers[name] || []).forEach((fn) => fn({})); },
    dispatchEvent(ev) { this.events.push(ev.type); this.dispatch(ev.type); return true; },
  }, extra || {});
}

const ids = ["depth-card", "depth-value", "depth-detail", "depth-hint", "depth-actions",
  "depth-use", "depth-correct", "depth-reset", "distance-input"];
const els = Object.fromEntries(ids.map((id) => [id, makeEl(id)]));

global.window = global;
global.Event = class { constructor(type) { this.type = type; } };
global.setInterval = () => 0;
global.document = { visibilityState: "visible", getElementById: (id) => els[id] || null };

const SPECS = {
  available: true, distance_m: 101.234, magnification: 0.0066, focal_length_mm: 662.5,
  focal_from: "specs", telescope_focal_length_mm: 1325, reducer: 0.5, measured_at_m: null,
  pixel_size_um: 2.9, pixel_from: "camera", zoom_level: 1, units_per_px: 0.0443, unit_name: "cm",
};
const MEASURED = Object.assign({}, SPECS, {
  distance_m: 104, focal_length_mm: 689.2, focal_from: "measured", measured_at_m: 104,
});
const NO_ZWO = { available: false, reason: "feed", message: "Needs the ZWO ASI camera as the live feed" };

let serving = SPECS;
const posts = [];
let refuse = null;
global.fetch = async (url, opts) => {
  if (!opts || opts.method !== "POST") return { ok: true, json: async () => serving };
  const body = JSON.parse(opts.body);
  posts.push({ url, body });
  if (refuse) return { ok: false, json: async () => ({ error: refuse }) };
  return { ok: true, json: async () => (url === "/api/depth/reset" ? SPECS : MEASURED) };
};
let answer = null;
window.prompt = () => answer;

eval(fs.readFileSync(process.argv[2], "utf8"));

const settle = () => new Promise((r) => setImmediate(r));
const fail = (msg) => { console.log("FAIL - " + msg); process.exit(1); };
const { describe } = window.SPOTS_DEPTH;

(async () => {
  await settle();
  if (els["depth-value"].textContent !== "≈ 101.2 m") fail("specs reading: " + els["depth-value"].textContent);
  const detail = els["depth-detail"].textContent;
  if (!/662\.5 mm \(1325 mm × 0\.5 reducer\)/.test(detail)) fail("the focal length's source wasn't shown: " + detail);
  if (!/2\.9 µm pixels from the camera/.test(detail)) fail("the pixel size's source wasn't shown");
  if (!/correct it once/i.test(els["depth-hint"].textContent)) fail("no nudge to correct the specs");
  if (els["depth-actions"].hidden || !els["depth-reset"].hidden) fail("wrong buttons for a specs reading");
  console.log("specs      : " + els["depth-value"].textContent + " | " + detail);

  // ---- without the ZWO ----------------------------------------------------
  const off = describe(NO_ZWO);
  if (off.value !== "–" || off.actions || !/ZWO/.test(off.detail)) fail("no-ZWO view: " + JSON.stringify(off));
  if (describe(Object.assign({}, SPECS, { zoom_level: 2 })).detail.indexOf("at 2× zoom") < 0) {
    fail("a zoomed calibration wasn't mentioned");
  }
  console.log("no ZWO     : " + off.detail);

  // ---- handing the distance to the setup strip ------------------------------
  els["depth-use"].dispatch("click");
  const input = els["distance-input"];
  if (input.value !== "101.2") fail("the distance box got " + input.value);
  if (input.events.join(",") !== "input,change") fail("the page wasn't told: " + input.events);
  console.log("use        : distance box set to 101.2 and saved by the page");

  // ---- correcting with a measured distance ------------------------------------
  answer = "104";
  els["depth-correct"].dispatch("click");
  await settle();
  await settle();
  const sent = posts.find((p) => p.url === "/api/depth/correct");
  if (!sent || sent.body.distance_m !== 104) fail("the correction wasn't sent: " + JSON.stringify(posts));
  if (els["depth-value"].textContent !== "104.0 m") fail("measured reading: " + els["depth-value"].textContent);
  if (!/689\.2 mm, measured at 104 m/.test(els["depth-detail"].textContent)) fail("measured detail");
  if (els["depth-reset"].hidden) fail("no way back to the specs");
  console.log("corrected  : " + els["depth-value"].textContent + " | " + els["depth-detail"].textContent);

  posts.length = 0;
  answer = "lots";
  els["depth-correct"].dispatch("click");
  await settle();
  if (posts.length) fail("a non-number was sent");
  answer = null;
  els["depth-correct"].dispatch("click");
  await settle();
  if (posts.length) fail("cancelling still sent something");

  refuse = "That would make the focal length 6625 mm";
  answer = "1000";
  els["depth-correct"].dispatch("click");
  await settle();
  await settle();
  if (!/6625 mm/.test(els["depth-detail"].textContent)) fail("the refusal wasn't shown");
  refuse = null;
  console.log("refused    : " + els["depth-detail"].textContent);

  els["depth-reset"].dispatch("click");
  await settle();
  await settle();
  if (!posts.some((p) => p.url === "/api/depth/reset")) fail("reset wasn't sent");
  if (els["depth-value"].textContent !== "≈ 101.2 m") fail("not back on the specs");
  console.log("reset      : back to " + els["depth-value"].textContent);

  console.log("PASS");
})();
