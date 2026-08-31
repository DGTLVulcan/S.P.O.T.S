// The range page: switching between the map and the rules, choosing which
// version of the map to look at, and the map's own zoom and pan.
//
// The map is a site plan -- legible only when you can zoom into the part you
// care about -- so it gets drag-to-pan, wheel and pinch zoom, and a
// fit-to-window reset. Pointer events throughout, because this is read on a
// phone at the range at least as often as on a laptop.
(function () {
  // ---- Map / Rules ---------------------------------------------------

  const tabs = document.querySelector(".range-tabs");
  if (tabs) {
    tabs.addEventListener("click", (ev) => {
      const button = ev.target.closest("button[data-view]");
      if (!button) return;
      tabs.querySelectorAll("button").forEach((b) => {
        b.classList.toggle("primary", b === button);
      });
      document.querySelectorAll(".range-view").forEach((view) => {
        view.hidden = view.id !== `range-view-${button.dataset.view}`;
      });
      if (button.dataset.view === "map") fit();
    });
  }

  // ---- Map viewer ----------------------------------------------------

  const stage = document.getElementById("map-stage");
  const image = document.getElementById("map-image");
  if (!stage || !image) return;

  const readout = document.getElementById("map-zoom-level");
  const note = document.getElementById("map-note");
  const MIN_SCALE = 0.2;
  const MAX_SCALE = 12;

  let scale = 1;
  let fitScale = 1;
  let x = 0;
  let y = 0;
  // Live pointers, so one is a drag and two are a pinch.
  const pointers = new Map();
  let pinchStart = null;

  function naturalSize() {
    return [
      image.naturalWidth || Number(image.getAttribute("width")) || 1,
      image.naturalHeight || Number(image.getAttribute("height")) || 1,
    ];
  }

  function apply() {
    const [w, h] = naturalSize();
    // Zoom by changing the laid-out size rather than with transform:
    // scale(). For the vector map that makes the browser re-render the SVG
    // at the new size -- which is the whole point of having a vector map --
    // instead of blowing up a bitmap of it. Panning stays a transform, so
    // dragging doesn't trigger a re-render on every pointer move.
    image.style.width = `${w * scale}px`;
    image.style.height = `${h * scale}px`;
    image.style.transform = `translate(${x}px, ${y}px)`;
    if (readout) readout.textContent = `${Math.round((scale / fitScale) * 100)}%`;
  }

  function clamp() {
    // Keep part of the map on screen, so it can't be flung out of view and
    // lost with no way back but Fit.
    const box = stage.getBoundingClientRect();
    const [w, h] = naturalSize();
    const slackX = Math.max(0, (w * scale - box.width) / 2) + box.width * 0.4;
    const slackY = Math.max(0, (h * scale - box.height) / 2) + box.height * 0.4;
    x = Math.max(-slackX, Math.min(slackX, x));
    y = Math.max(-slackY, Math.min(slackY, y));
  }

  function fit() {
    const box = stage.getBoundingClientRect();
    const [w, h] = naturalSize();
    if (!w || !box.width) return;
    fitScale = Math.min(box.width / w, box.height / h);
    scale = fitScale;
    x = 0;
    y = 0;
    apply();
  }

  function zoomAt(factor, clientX, clientY) {
    const next = Math.max(MIN_SCALE * fitScale, Math.min(MAX_SCALE * fitScale, scale * factor));
    if (next === scale) return;
    const box = stage.getBoundingClientRect();
    // Keep whatever is under the cursor under the cursor.
    const originX = clientX - box.left - box.width / 2;
    const originY = clientY - box.top - box.height / 2;
    const ratio = next / scale;
    x = originX - (originX - x) * ratio;
    y = originY - (originY - y) * ratio;
    scale = next;
    clamp();
    apply();
  }

  function zoomCentre(factor) {
    const box = stage.getBoundingClientRect();
    zoomAt(factor, box.left + box.width / 2, box.top + box.height / 2);
  }

  stage.addEventListener("pointerdown", (ev) => {
    stage.setPointerCapture(ev.pointerId);
    pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinchStart = { distance: Math.hypot(a.x - b.x, a.y - b.y), scale };
    }
    stage.classList.add("dragging");
  });

  stage.addEventListener("pointermove", (ev) => {
    const previous = pointers.get(ev.pointerId);
    if (!previous) return;
    const current = { x: ev.clientX, y: ev.clientY };
    pointers.set(ev.pointerId, current);

    if (pointers.size >= 2 && pinchStart) {
      const [a, b] = [...pointers.values()];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinchStart.distance > 0) {
        const wanted = pinchStart.scale * (distance / pinchStart.distance);
        zoomAt(wanted / scale, (a.x + b.x) / 2, (a.y + b.y) / 2);
      }
      return;
    }
    x += current.x - previous.x;
    y += current.y - previous.y;
    clamp();
    apply();
  });

  function release(ev) {
    pointers.delete(ev.pointerId);
    if (pointers.size < 2) pinchStart = null;
    if (pointers.size === 0) stage.classList.remove("dragging");
  }
  stage.addEventListener("pointerup", release);
  stage.addEventListener("pointercancel", release);

  stage.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    zoomAt(ev.deltaY < 0 ? 1.15 : 1 / 1.15, ev.clientX, ev.clientY);
  }, { passive: false });

  stage.addEventListener("dblclick", (ev) => zoomAt(1.6, ev.clientX, ev.clientY));

  const zoomIn = document.getElementById("map-zoom-in");
  const zoomOut = document.getElementById("map-zoom-out");
  const reset = document.getElementById("map-zoom-reset");
  if (zoomIn) zoomIn.addEventListener("click", () => zoomCentre(1.4));
  if (zoomOut) zoomOut.addEventListener("click", () => zoomCentre(1 / 1.4));
  if (reset) reset.addEventListener("click", fit);

  // ---- scan / vector -------------------------------------------------

  const kinds = document.querySelector(".map-kinds");
  if (kinds) {
    kinds.addEventListener("click", (ev) => {
      const button = ev.target.closest("button[data-map-view]");
      if (!button || button.classList.contains("primary")) return;
      kinds.querySelectorAll("button").forEach((b) => {
        b.classList.toggle("primary", b === button);
      });
      // Both versions cover the same ground, so hold the zoom and position
      // across the swap -- the point is to compare the two, and being
      // thrown back to Fit every time would make that impossible.
      const heldScale = scale / fitScale;
      const [heldX, heldY] = [x, y];
      image.setAttribute("width", button.dataset.width);
      image.setAttribute("height", button.dataset.height);
      image.addEventListener("load", () => {
        fit();
        scale = fitScale * heldScale;
        x = heldX;
        y = heldY;
        clamp();
        apply();
      }, { once: true });
      image.src = button.dataset.src;

      const source = document.querySelector(`template[data-note-for="${button.dataset.mapView}"]`);
      if (note && source) {
        note.textContent = `${(note.dataset.caption || "").trim()} ${source.innerHTML.trim()}`.trim();
      }
    });
  }

  // The fit depends on the stage's measured size, so it is worked out once
  // the image has decoded and again whenever the window changes shape.
  if (image.complete && image.naturalWidth) fit();
  else image.addEventListener("load", fit, { once: true });
  window.addEventListener("resize", fit);
})();
