// Runs camera_status.js's describe() over a set of camera readings and
// prints what the settings page would show for each, as JSON.
//
// Usage: node camera_status.js <camera_status.js>
const fs = require("fs");

global.window = global;
global.document = { getElementById: () => null };   // no panel, so no polling
eval(fs.readFileSync(process.argv[2], "utf8"));

const base = {
  available: true, connected: true, auto: true,
  exposure_ms: 6.4, max_exposure_ms: 20, gain: 50, min_gain: 50, max_gain: 300,
  level: 112, target_level: 110,
};
const cases = {
  none: { available: false },
  waiting: { ...base, level: null },
  settled: base,
  brightening: { ...base, level: 40 },
  gaining: { ...base, level: 40, exposure_ms: 20, gain: 140 },
  out_of_light: { ...base, level: 40, exposure_ms: 20, gain: 300 },
  fixed_dark: { ...base, level: 40, auto: false },
};
const out = {};
for (const [name, status] of Object.entries(cases)) {
  out[name] = window.SPOTS_CAMERA_STATUS.describe(status);
}
console.log(JSON.stringify(out));
