// Drives mount.js under a stub DOM with fake timers and a fake server.
//
// Checks a held arrow keeps re-sending its move, that letting go -- or
// losing the button any other way -- stops it, and that a refused move
// stops the heartbeat.
//
// Usage: node mount_pad.js <mount.js>
const fs = require("fs");

// ---- fake timers -----------------------------------------------------
let now = 0;
const timers = [];
global.setInterval = (fn, ms) => { timers.push({ fn, ms, next: now + ms }); return timers.length; };
global.clearInterval = (handle) => { timers[handle - 1] = null; };
function advance(ms) {
  const end = now + ms;
  for (;;) {
    const due = timers.filter((t) => t && t.next <= end).sort((a, b) => a.next - b.next)[0];
    if (!due) break;
    now = due.next;
    due.next += due.ms;
    due.fn();
  }
  now = end;
}

// ---- stub DOM --------------------------------------------------------
function makeEl(id, extra) {
  const classes = new Set();
  return Object.assign({
    id, disabled: false, hidden: false, textContent: "", className: "", value: "",
    dataset: {}, handlers: {},
    classList: {
      add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
    },
    addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); },
    dispatch(name, ev) {
      const event = Object.assign({ preventDefault() {}, pointerId: 1 }, ev || {});
      (this.handlers[name] || []).forEach((fn) => fn(event));
    },
    setPointerCapture() {},
  }, extra || {});
}

const arrows = ["up", "left", "right", "down"].map((dir) => {
  const el = makeEl("arrow-" + dir);
  el.dataset.dir = dir;
  el.disabled = true;
  return el;
});
const els = {
  "mount-card": makeEl("mount-card", { querySelectorAll: () => arrows }),
  "mount-stop": makeEl("mount-stop"),
  "mount-speed": makeEl("mount-speed", { value: "5" }),
  "mount-speed-value": makeEl("mount-speed-value"),
  "mount-speed-rate": makeEl("mount-speed-rate"),
  "mount-state": makeEl("mount-state"),
  "mount-connect": makeEl("mount-connect"),
};
const windowEl = makeEl("window");
const documentEl = makeEl("document");

global.window = global;
window.addEventListener = windowEl.addEventListener.bind(windowEl);
global.document = {
  visibilityState: "visible",
  getElementById: (id) => els[id] || null,
  addEventListener: documentEl.addEventListener.bind(documentEl),
};
global.localStorage = { getItem: () => null, setItem() {} };
const beacons = [];
// Newer Node has a read-only navigator of its own, so it is replaced outright.
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { sendBeacon: (url, body) => { beacons.push({ url, body }); return true; } },
});
window.SPOTS_MOUNT_SPEEDS = { 5: "16× sidereal", 7: "1°/s" };

// ---- fake server ------------------------------------------------------
const CONNECTED = { connected: true, model: "NexStar 4/5 SE", port: "/dev/ttyUSB0" };
const posts = [];
let refuseMoves = false;
global.fetch = async (url, opts) => {
  if (!opts || opts.method !== "POST") return { ok: true, json: async () => CONNECTED };
  const body = JSON.parse(opts.body);
  posts.push({ url, body });
  if (url === "/api/mount/move" && refuseMoves) {
    return { ok: false, json: async () => ({ connected: false, error: "Lost the hand controller" }) };
  }
  return { ok: true, json: async () => CONNECTED };
};

eval(fs.readFileSync(process.argv[2], "utf8"));

// ---- the checks -------------------------------------------------------
const settle = () => new Promise((r) => setImmediate(r));
const fail = (msg) => { console.log("FAIL - " + msg); process.exit(1); };
const moves = () => posts.filter((p) => p.url === "/api/mount/move");
const stops = () => posts.filter((p) => p.url === "/api/mount/stop");
const arrow = (dir) => arrows.find((a) => a.dataset.dir === dir);
const reset = () => { posts.length = 0; beacons.length = 0; };

(async () => {
  await settle();
  if (arrows.some((a) => a.disabled)) fail("the arrows stayed disabled once connected");

  // ---- holding an arrow keeps the move alive ---------------------------
  arrow("up").dispatch("pointerdown");
  await settle();
  if (moves().length !== 1) fail(`pressing should send one move at once, sent ${moves().length}`);
  if (moves()[0].body.direction !== "up" || moves()[0].body.speed !== 5) {
    fail("the move didn't carry the direction and speed: " + JSON.stringify(moves()[0].body));
  }
  advance(1000);
  await settle();
  if (moves().length < 4) fail(`a held arrow should re-send its move, sent ${moves().length} in 1 s`);
  console.log(`held 1 s   : ${moves().length} moves sent`);

  els["mount-speed"].value = "7";
  els["mount-speed"].dispatch("input");
  advance(300);
  await settle();
  if (moves().at(-1).body.speed !== 7) fail("a speed change mid-move wasn't picked up");
  if (els["mount-speed-rate"].textContent !== "1°/s") fail("the speed label didn't follow the slider");
  console.log("speed      : changed mid-move to 7 (1°/s)");

  // ---- letting go stops it ----------------------------------------------
  reset();
  arrow("up").dispatch("pointerup");
  await settle();
  if (stops().length !== 1 || stops()[0].body.direction !== "up") fail("letting go didn't stop the move");
  advance(2000);
  await settle();
  if (moves().length) fail("moves kept coming after letting go");
  console.log("released   : one stop, no more moves");

  // ---- every other way of losing the button stops it too -----------------
  const losses = [
    ["pointer cancelled", () => arrow("left").dispatch("pointercancel"), "left"],
    ["window blurred", () => windowEl.dispatch("blur"), "right"],
    ["page hidden", () => { document.visibilityState = "hidden"; documentEl.dispatch("visibilitychange"); }, "down"],
  ];
  for (const [what, lose, dir] of losses) {
    document.visibilityState = "visible";
    reset();
    arrow(dir).dispatch("pointerdown");
    await settle();
    lose();
    await settle();
    if (!stops().length) fail(`${what}: no stop was sent`);
    const before = moves().length;
    advance(2000);
    await settle();
    if (moves().length !== before) fail(`${what}: moves kept coming`);
    console.log(`${what.padEnd(11)}: stopped`);
  }

  document.visibilityState = "visible";
  reset();
  arrow("up").dispatch("pointerdown");
  await settle();
  windowEl.dispatch("pagehide");
  await settle();
  if (!beacons.length) fail("closing the page didn't send a stop beacon");
  console.log("page closed: stop sent as a beacon");

  // ---- the stop button ----------------------------------------------------
  reset();
  els["mount-stop"].dispatch("click");
  await settle();
  if (stops().length !== 1 || stops()[0].body.direction !== undefined) fail("Stop didn't stop both motors");
  console.log("stop button: both motors");

  // ---- a refused move ends the heartbeat ------------------------------------
  reset();
  refuseMoves = true;
  arrow("right").dispatch("pointerdown");
  await settle();
  await settle();
  const sent = moves().length;
  advance(2000);
  await settle();
  if (moves().length !== sent) fail("moves kept coming after the server refused one");
  if (!/Lost the hand controller/.test(els["mount-state"].textContent)) fail("the refusal wasn't shown");
  if (!arrows.every((a) => a.disabled)) fail("the arrows stayed live with the mount gone");
  console.log("refused    : heartbeat ended, error shown, arrows disabled");

  console.log("PASS");
})();
