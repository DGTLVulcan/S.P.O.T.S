// Live View's Distance Estimate card: how far away the target is, from its
// calibrated size, the telescope's focal length and the ZWO's pixel size.
(function () {
  const card = document.getElementById("depth-card");
  if (!card) return;

  const POLL_MS = 3000;
  const valueEl = document.getElementById("depth-value");
  const detailEl = document.getElementById("depth-detail");
  const hintEl = document.getElementById("depth-hint");
  const actions = document.getElementById("depth-actions");
  const useBtn = document.getElementById("depth-use");
  const correctBtn = document.getElementById("depth-correct");
  const resetBtn = document.getElementById("depth-reset");

  let latest = null;

  const trim = (n, places) => String(Number(n.toFixed(places)));

  function formatDistance(m) {
    return (m >= 1000 ? m.toFixed(0) : m.toFixed(1)) + " m";
  }

  // What the card shows for an estimate from /api/depth.
  function describe(est) {
    if (!est || !est.available) {
      return { value: "–", detail: (est && est.message) || "", hint: "", actions: false };
    }
    const focal = est.focal_from === "measured"
      ? `Focal length ${trim(est.focal_length_mm, 1)} mm, measured at `
        + `${trim(est.measured_at_m, 1)} m`
      : `Focal length ${trim(est.focal_length_mm, 1)} mm `
        + `(${trim(est.telescope_focal_length_mm, 1)} mm × ${trim(est.reducer, 3)} reducer)`;
    const parts = [
      focal,
      `${trim(est.pixel_size_um, 2)} µm pixels`
        + (est.pixel_from === "camera" ? " from the camera" : " from Settings"),
      `scale ${trim(est.units_per_px, 4)} ${est.unit_name}/px`
        + (est.zoom_level !== 1 ? ` at ${trim(est.zoom_level, 2)}× zoom` : ""),
    ];
    const hint = est.focal_from === "measured"
      ? "Calibrate across the widest measurement you know on the target for the steadiest reading."
      : "From the specs alone this can be a few percent out: the 4SE focuses by moving its "
        + "mirror, and a reducer's strength depends on its spacing. Correct it once with a "
        + "distance you've measured.";
    return {
      value: (est.focal_from === "measured" ? "" : "≈ ") + formatDistance(est.distance_m),
      detail: parts.join(" · "),
      hint,
      actions: true,
      measured: est.focal_from === "measured",
    };
  }

  function render(est, problem) {
    latest = est;
    const view = describe(est);
    valueEl.textContent = view.value;
    detailEl.textContent = problem || view.detail;
    hintEl.textContent = problem ? "" : view.hint;
    actions.hidden = !view.actions;
    resetBtn.hidden = !view.measured;
  }

  async function post(url, body) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try {
      data = await resp.json();
    } catch (err) {
      /* an empty or HTML error page */
    }
    return { ok: resp.ok, data };
  }

  async function refresh() {
    if (document.visibilityState === "hidden") return;
    try {
      const resp = await fetch("/api/depth", { cache: "no-store" });
      render(await resp.json());
    } catch (err) {
      /* the next poll tries again */
    }
  }

  // Fills in the setup strip's distance and lets the page save it as if
  // it had been typed.
  useBtn.addEventListener("click", () => {
    if (!latest || !latest.available) return;
    const input = document.getElementById("distance-input");
    if (!input) return;
    input.value = latest.distance_m.toFixed(1);
    input.dispatchEvent(new Event("input"));
    input.dispatchEvent(new Event("change"));
  });

  correctBtn.addEventListener("click", async () => {
    const answer = window.prompt("Measured distance to the target, in metres:");
    if (answer === null) return;
    const distance = parseFloat(answer);
    if (!(distance > 0)) {
      render(latest, "The distance must be a number of metres above 0.");
      return;
    }
    try {
      const { ok, data } = await post("/api/depth/correct", { distance_m: distance });
      if (ok) render(data);
      else render(latest, data.error || "Couldn't correct the focal length.");
    } catch (err) {
      render(latest, "Couldn't reach S.P.O.T.S.");
    }
  });

  resetBtn.addEventListener("click", async () => {
    try {
      const { data } = await post("/api/depth/reset", {});
      render(data);
    } catch (err) {
      render(latest, "Couldn't reach S.P.O.T.S.");
    }
  });

  refresh();
  setInterval(refresh, POLL_MS);

  window.SPOTS_DEPTH = { describe };
})();
