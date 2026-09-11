// Drives menu.js under a stub DOM.
//
// Checks the four readings render correctly, and that polling starts when
// the menu opens and stops when it closes.
//
// Usage: node menu_stats.js <menu.js>
const fs = require("fs");

let now = 0;
const timers = [];

function makeEl(id) {
  const el = {
    id, hidden: true, innerHTML: "", handlers: {},
    attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); },
    dispatch(name, ev) { (this.handlers[name] || []).forEach((fn) => fn(ev || {})); },
    contains: () => false,
    focus() {},
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  return el;
}

const registry = {};
const byId = (id) => (registry[id] ||= makeEl(id));

global.window = global;
global.setInterval = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
global.clearInterval = (handle) => { timers[handle - 1] = null; };
global.document = {
  getElementById: byId,
  addEventListener() {},
  querySelector: () => null,
  querySelectorAll: () => [],
};

let served = null;
let fetches = 0;
global.fetch = async () => {
  fetches += 1;
  return { json: async () => served };
};

const HEALTHY = {
  status: "ok",
  levels: { cpu: "ok", disk: "ok", power: "ok", camera: "ok" },
  warnings: [],
  cpu_temp_c: 48.2,
  load_average: [0.42, 0.35, 0.30],
  disk: { free_mb: 22118.4, total_mb: 30720.0, used_percent: 28.0 },
  throttled: { under_voltage_now: false, throttled_now: false,
               under_voltage_since_boot: false, throttled_since_boot: false },
  uptime_s: 93784,
  feed_active: "zcam", camera_connected: true,
};

served = HEALTHY;
eval(fs.readFileSync(process.argv[2], "utf8"));

const fail = (msg) => { console.log("FAIL - " + msg); process.exit(1); };
const button = byId("menu-button");
const rows = byId("menu-stats-rows");
const text = () => rows.innerHTML.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const live = () => timers.filter(Boolean).length;

const settle = () => new Promise((r) => setTimeout(r, 0));

(async () => {
  // ---- nothing happens until the menu is opened -----------------------
  if (fetches !== 0) fail(`polled ${fetches} times before the menu was opened`);
  if (live() !== 0) fail("a timer was running before the menu was opened");

  button.dispatch("click", { stopPropagation() {} });
  await settle();
  if (fetches !== 1) fail(`expected one read on open, got ${fetches}`);
  if (live() !== 1) fail("opening should start exactly one timer");
  console.log("open       :", text());

  for (const needed of ["CPU 48", "Uptime 1d 2h", "Disk 21.6 GB", "Power OK"]) {
    if (!text().includes(needed)) fail(`missing "${needed}" in: ${text()}`);
  }
  if (!text().includes("load 0.42")) fail("the load average should be shown");
  if (!text().includes("28% used")) fail("the disk row should say how full it is");

  // ---- and stops again when it closes ---------------------------------
  button.dispatch("click", { stopPropagation() {} });
  await settle();
  if (live() !== 0) fail("closing the menu must stop the timer");
  const quiet = fetches;
  timers.filter(Boolean).forEach((t) => t.fn());
  await settle();
  if (fetches !== quiet) fail("a closed menu kept polling");
  console.log("closed     : polling stopped");

  // ---- under-voltage is the one that has to shout ---------------------
  served = Object.assign({}, HEALTHY, {
    levels: { cpu: "ok", disk: "ok", power: "critical", camera: "ok" },
    throttled: { under_voltage_now: true, throttled_now: false,
                 under_voltage_since_boot: true, throttled_since_boot: false },
  });
  button.dispatch("click", { stopPropagation() {} });
  await settle();
  if (!text().includes("Power Low volts")) {
    fail("under-voltage now must be reported: " + text());
  }
  if (!/is-critical/.test(rows.innerHTML)) fail("under-voltage should mark the row critical");
  console.log("brownout   :", text().split("Disk")[1]);

  // Sagging right now outranks a dip at boot: they are different problems.
  served = Object.assign({}, HEALTHY, {
    levels: { cpu: "ok", disk: "ok", power: "warn", camera: "ok" },
    throttled: { under_voltage_now: false, throttled_now: false,
                 under_voltage_since_boot: true, throttled_since_boot: false },
  });
  timers.filter(Boolean)[0].fn();
  await settle();
  if (!text().includes("Power Dipped")) fail("a boot-time dip should read differently");
  if (/is-critical/.test(rows.innerHTML)) fail("a boot-time dip is not critical");
  console.log("since boot :", text().split("Disk")[1]);

  // ---- a hot CPU takes its colour from the server ---------------------
  served = Object.assign({}, HEALTHY, {
    levels: { cpu: "critical", disk: "ok", power: "ok", camera: "ok" },
    cpu_temp_c: 83.4,
  });
  timers.filter(Boolean)[0].fn();
  await settle();
  if (!text().includes("CPU 83")) fail("the temperature should be shown: " + text());
  if (!/menu-stat is-critical/.test(rows.innerHTML)) fail("a hot CPU should read critical");
  console.log("hot cpu    :", text().split("Uptime")[0]);

  // ---- a machine that reports none of this still renders --------------
  served = {
    status: "ok", levels: {}, warnings: [],
    cpu_temp_c: null, load_average: null, disk: null, throttled: null,
    uptime_s: null, feed_active: "synthetic", camera_connected: false,
  };
  timers.filter(Boolean)[0].fn();
  await settle();
  for (const label of ["CPU", "Uptime", "Disk", "Power"]) {
    if (!text().includes(label)) fail(`${label} row vanished when nothing was reported`);
  }
  if (!text().includes("not reported")) fail("power should say it has no reading");
  console.log("bare       :", text());

  // ---- and a failed request does not take the menu with it ------------
  global.fetch = async () => { throw new Error("no route to host"); };
  timers.filter(Boolean)[0].fn();
  await settle();
  if (!text().includes("unavailable")) fail("a failed read should say so: " + text());
  console.log("offline    :", text());

  console.log("PASS - the menu reports the Pi, and only while it is open");
})().catch((err) => fail(err.stack));
