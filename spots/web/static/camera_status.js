// Live exposure, gain and brightness for the ZWO ASI camera, on the
// Settings camera panel. Polled only while that panel is on screen.
(function () {
  const el = document.getElementById("asi-status");

  // How far under the target brightness counts as a dark picture.
  const DARK_MARGIN = 20;

  const at = (value, limit) => value >= limit - 1e-6;

  // The readout, and a line saying why the picture looks as it does.
  function describe(status) {
    if (!status || !status.available) {
      return { text: "A live readout appears here while the ZWO ASI camera is the live feed.",
               level: "" };
    }
    if (!status.connected || status.level === null) {
      return { text: "Waiting for frames from the camera…", level: "warn" };
    }
    const exposure = `${status.exposure_ms.toFixed(2)} ms`
      + (status.auto && at(status.exposure_ms, status.max_exposure_ms) ? " (longest)" : "");
    const gain = `gain ${status.gain}`
      + (status.auto && at(status.gain, status.max_gain) ? " (highest)" : "");
    const now = `Now: ${exposure} · ${gain} · brightness ${Math.round(status.level)} of 255`
      + ` (aiming for ${Math.round(status.target_level)}).`;

    const dark = status.level < status.target_level - DARK_MARGIN;
    if (!dark) return { text: now, level: "ok" };
    if (!status.auto) {
      return { text: `${now} Auto exposure is off, so this is the fixed exposure and gain `
               + "set below.", level: "warn" };
    }
    if (at(status.exposure_ms, status.max_exposure_ms) && at(status.gain, status.max_gain)) {
      return { text: `${now} Out of light: exposure and gain are both at their limits. `
               + "Open the lens up, add light, or raise Longest exposure or Highest gain.",
               level: "bad" };
    }
    if (at(status.exposure_ms, status.max_exposure_ms)) {
      return { text: `${now} Exposure is at its longest; gain is rising to make up the rest.`,
               level: "warn" };
    }
    return { text: `${now} Brightening…`, level: "warn" };
  }

  async function refresh() {
    const panel = el && el.closest(".settings-panel");
    if (!el || (panel && panel.hidden)) return;
    try {
      const resp = await fetch("/api/camera/status", { cache: "no-store" });
      const view = describe(await resp.json());
      el.textContent = view.text;
      el.className = "asi-status" + (view.level ? ` is-${view.level}` : "");
    } catch (err) {
      /* diagnostic only -- leave the last reading up */
    }
  }

  if (el) {
    refresh();
    setInterval(refresh, 2000);
  }

  window.SPOTS_CAMERA_STATUS = { describe };
})();
