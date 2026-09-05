// Drive ballistics.js under a minimal DOM to reproduce the reported bug:
// "fill from solution" flashes the rows, then they vanish.
const fs = require("fs");

function makeEl(id) {
  const el = {
    id, tagName: "DIV", value: "", innerHTML: "", hidden: false, _text: "",
    // The real DOM stringifies everything put in dataset; the code under
    // test relies on that when it reads a distance back out.
    dataset: new Proxy({}, { set: (o, k, v) => { o[k] = String(v); return true; } }),
    children: [], type: "", step: "",
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    handlers: {},
    addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); },
    click() { (this.handlers.click || []).forEach((fn) => fn({})); },
    append(...kids) { kids.forEach((k) => { this.children.push(k); k.parent = this; }); },
    remove() {},
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    querySelectorAll(sel) {
      const out = [];
      const walk = (node) => {
        node.children.forEach((kid) => {
          if (sel.includes("tbody tr") && kid.tagName === "TR") out.push(kid);
          else if (sel.includes("[data-key]") && kid.dataset.key) out.push(kid);
          walk(kid);
        });
      };
      walk(this);
      return out;
    },
  };
  // Setting textContent replaces every child, which is exactly how the
  // real DOM clears a <tbody> -- and the behaviour the bug depended on.
  Object.defineProperty(el, "textContent", {
    get() { return this._text; },
    set(value) { this._text = value; this.children = []; },
  });
  Object.defineProperty(el, "firstChild", { get() { return this.children[0] || null; } });
  return el;
}

const registry = {};
const byId = (id) => (registry[id] ||= makeEl(id));

// The table needs a real tbody whose rows we can count.
const tbody = makeEl("tbody");
tbody.tagName = "TBODY";
const dopeTable = byId("dope-card");
dopeTable.append(tbody);
dopeTable.querySelector = (sel) => (sel === "tbody" ? tbody : null);

const ballTable = byId("ball-card");
const ballBody = makeEl("ball-tbody");
ballBody.tagName = "TBODY";
ballTable.append(ballBody);
ballTable.querySelector = (sel) => (sel === "tbody" ? ballBody : null);

// A browser has a window; the page hangs its cross-file hooks off it.
global.window = global;
global.addEventListener = () => {};
global.performance = global.performance || { now: () => Date.now() };
global.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 16);
global.cancelAnimationFrame = (id) => clearTimeout(id);
global.getComputedStyle = () => ({ getPropertyValue: () => "" });

global.document = {
  getElementById: byId,
  createElement(tag) {
    const el = makeEl(null);
    el.tagName = tag.toUpperCase();
    return el;
  },
  querySelector: () => null,
  querySelectorAll: (sel) => {
    if (sel === ".ball-panel") return [byId("p-solve"), byId("p-dope"), byId("p-true")];
    if (sel === ".ball-nav") return [];
    return [];
  },
  addEventListener() {},
};

// The saved card is empty -- which is the case that made the rows vanish.
let dopeFetches = 0;
global.fetch = async (url, opts) => {
  if (url.startsWith("/api/ballistics/inputs")) {
    return { ok: true, json: async () => ({
      unit: "mrad", shot: {}, defaults: {}, missing: [],
      equipment: { rifle: "R", scope: "S", ammo: "A", click_value: 0.1 } }) };
  }
  if (url.startsWith("/api/ballistics/dope")) {
    dopeFetches += 1;
    return { ok: true, json: async () => ({ key: "1:1", card: null, unit: "mrad",
                                            equipment: {} }) };
  }
  return { ok: true, json: async () => ({}) };
};

byId("p-solve").dataset.panel = "solve";
byId("p-dope").dataset.panel = "dope";
byId("p-true").dataset.panel = "true";

eval(fs.readFileSync(process.argv[2], "utf8"));

(async () => {
  await new Promise((r) => setTimeout(r, 10));   // let loadInputs settle

  // Pretend a solution exists, then press "Fill from the solution".
  const solved = { rows: [
    { distance_m: 100, elevation: 0, windage: 0, transonic: false },
    { distance_m: 300, elevation: 1.68, windage: 0.4, transonic: false },
    { distance_m: 500, elevation: 4.1, windage: 0.9, transonic: false },
  ]};
  // reach the module's state through the handler it exposes
  byId("ball-solve").click();
  await new Promise((r) => setTimeout(r, 5));

  // The module keeps `state` private, so drive the documented path: set a
  // solution by calling the solve handler with a stubbed response.
  global.fetch = async (url) => {
    if (url.startsWith("/api/ballistics/solve")) {
      return { ok: true, json: async () => Object.assign({
        unit: "mrad", click_value: 0.1, rows: solved.rows, transonic_from_m: null,
        stability: null, air_density: 1.225, density_ratio: 1, speed_of_sound_ms: 340,
        spin_drift_included: false, equipment: {} }) };
    }
    if (url.startsWith("/api/ballistics/dope")) {
      dopeFetches += 1;
      return { ok: true, json: async () => ({ card: null, unit: "mrad", equipment: {} }) };
    }
    return { ok: true, json: async () => ({}) };
  };
  byId("ball-solve").click();
  await new Promise((r) => setTimeout(r, 20));

  byId("ball-to-dope").click();
  const immediately = tbody.children.length;
  await new Promise((r) => setTimeout(r, 60));   // let any stray fetch land
  const afterAsync = tbody.children.length;

  console.log(`rows right after filling : ${immediately}`);
  console.log(`rows once async settles  : ${afterAsync}`);
  console.log(`saved-card fetches fired : ${dopeFetches}`);
  const ok = afterAsync === immediately && afterAsync === 3;
  console.log(ok ? "PASS - the filled rows survive"
                 : "FAIL - the rows were wiped, which is the reported bug");
  process.exit(ok ? 0 : 1);
})();
