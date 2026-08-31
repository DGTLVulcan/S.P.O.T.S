// Rearranging the dashboard cards.
//
// The page already arrives in the saved arrangement (rendered server-side
// from spots/layout.py), so nothing here runs on a normal load -- this only
// adds the editing mode: drag handles, the per-column controls, and saving.
// Rearranging is deliberately a mode you switch on, because the feed is
// itself a click target for placing shots and a stray drag must never move
// a card out from under you mid-string.
(function () {
  const layoutEl = document.getElementById("layout");
  const bar = document.getElementById("layout-bar");
  const toggle = document.getElementById("layout-toggle");
  if (!layoutEl || !bar || !toggle) return;

  const hint = bar.querySelector(".layout-bar-hint");
  const addColumnBtn = document.getElementById("layout-add-column");
  const hiddenBar = document.getElementById("layout-hidden-bar");
  const resetBtn = document.getElementById("layout-reset");
  const doneBtn = document.getElementById("layout-done");

  const LABELS = window.SPOTS_TILES || {};
  const MAX_COLUMNS = 4;
  const MAX_TILE_WIDTH = 6;
  const MAX_TILE_HEIGHT = 900;
  const TILE_HEIGHT_STEP = 80;
  const MIN_WEIGHT = 1;
  const MAX_WEIGHT = 6;
  const DEFAULT_HINT = hint ? hint.textContent : "";

  let editing = false;
  let drag = null;
  let saveTimer = null;

  // Per-card sizes, seeded from what the server rendered.
  const SIZES = new Map(Object.entries(
    (window.SPOTS_LAYOUT && window.SPOTS_LAYOUT.sizes) || {}));

  function sizeOf(tile) {
    const stored = SIZES.get(tile) || {};
    return { w: Number(stored.w) || 1, h: Number(stored.h) || 0 };
  }

  function applySize(card, size) {
    card.style.setProperty("--tile-w", size.w);
    if (size.h) card.style.setProperty("--tile-h", `${size.h}px`);
    else card.style.removeProperty("--tile-h");
  }

  function resize(card, dw, dh) {
    const tile = card.dataset.tile;
    const size = sizeOf(tile);
    size.w = Math.max(1, Math.min(MAX_TILE_WIDTH, size.w + dw));
    size.h = Math.max(0, Math.min(MAX_TILE_HEIGHT, size.h + dh * TILE_HEIGHT_STEP));
    SIZES.set(tile, size);
    applySize(card, size);
    refreshHandle(card);
    save();
  }

  function columns() {
    return Array.from(layoutEl.querySelectorAll(".layout-column"));
  }

  // Hidden cards stay in the page, parked here, so putting one back is a
  // move rather than a re-render.
  const store = document.getElementById("layout-hidden");

  function hiddenCards() {
    return store ? Array.from(store.querySelectorAll(".card[data-tile]")) : [];
  }

  function say(message) {
    if (hint) hint.textContent = message;
  }

  // ---- reading the page back out ------------------------------------

  function serialize() {
    return {
      columns: columns().map((column) => ({
        weight: Number(column.dataset.weight) || 2,
        flow: column.classList.contains("flow-wrap") ? "wrap" : "stack",
        tiles: Array.from(column.querySelectorAll(".card[data-tile]"))
          .map((card) => card.dataset.tile),
      })),
      hidden: hiddenCards().map((card) => card.dataset.tile),
      sizes: Object.fromEntries(
        [...SIZES].filter(([, size]) => size.w !== 1 || size.h)),
    };
  }

  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        const res = await fetch("/api/layout", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(serialize()),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        say("Layout saved.");
      } catch (err) {
        // Worth saying plainly: the arrangement on screen is right but
        // won't survive a reload, which is otherwise invisible until the
        // next reboot puts everything back.
        say(`Could not save the layout (${err.message}). It will revert on reload.`);
      }
    }, 400);
  }

  // ---- editing furniture, added and removed with the mode ------------

  function makeHandle(card) {
    // A div holding real buttons, not a button holding clickable spans:
    // there are three controls in here now, and nesting them inside a
    // button is neither valid nor reachable from a keyboard.
    const handle = document.createElement("div");
    handle.className = "tile-handle";

    const drag = document.createElement("span");
    drag.className = "tile-drag";
    drag.title = "Drag to move this card";
    const grip = document.createElement("span");
    grip.className = "tile-grip";
    const label = document.createElement("span");
    label.textContent = LABELS[card.dataset.tile] || card.dataset.tile;
    drag.append(grip, label);
    drag.addEventListener("pointerdown", (ev) => startDrag(ev, card));

    const size = document.createElement("span");
    size.className = "tile-size";
    size.append(
      sizeButton("−", "Narrower", () => resize(card, -1, 0), "narrow"),
      readout("tile-width"),
      sizeButton("+", "Wider", () => resize(card, 1, 0), "widen"),
      sizeButton("−", "Shorter", () => resize(card, 0, -1), "shorten"),
      readout("tile-height"),
      sizeButton("+", "Taller", () => resize(card, 0, 1), "heighten"),
    );

    const hide = document.createElement("button");
    hide.type = "button";
    hide.className = "tile-hide";
    hide.textContent = "Hide";
    hide.title = "Put this card away";
    hide.addEventListener("click", () => hideCard(card));

    handle.append(drag, size, hide);
    return handle;
  }

  function sizeButton(text, title, onClick, role) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.title = title;
    button.dataset.size = role;
    button.addEventListener("click", onClick);
    return button;
  }

  function readout(className) {
    const span = document.createElement("span");
    span.className = className;
    return span;
  }

  function refreshHandle(card) {
    const handle = card.querySelector(".tile-handle");
    if (!handle) return;
    const size = sizeOf(card.dataset.tile);
    const column = card.closest(".layout-column");
    const sideBySide = column && column.classList.contains("flow-wrap");
    const width = handle.querySelector(".tile-width");
    const height = handle.querySelector(".tile-height");
    if (width) width.textContent = sideBySide ? `W${size.w}` : "full";
    if (height) height.textContent = size.h ? `${size.h}px` : "auto";
    handle.querySelectorAll("[data-size]").forEach((button) => {
      const role = button.dataset.size;
      if (role === "narrow" || role === "widen") {
        // Width is a share of a row, so it means nothing where each card
        // already has the whole row to itself.
        button.disabled = !sideBySide
          || (role === "narrow" && size.w <= 1)
          || (role === "widen" && size.w >= MAX_TILE_WIDTH);
        button.title = sideBySide
          ? (role === "narrow" ? "Narrower" : "Wider")
          : "Set the column to side by side to change widths";
      } else {
        button.disabled = (role === "shorten" && size.h <= 0)
          || (role === "heighten" && size.h >= MAX_TILE_HEIGHT);
      }
    });
  }

  function makeStrip(column) {
    const strip = document.createElement("div");
    strip.className = "column-strip";
    strip.innerHTML =
      '<button type="button" data-act="narrow" title="Narrower">&minus;</button>' +
      '<span class="column-weight"></span>' +
      '<button type="button" data-act="wide" title="Wider">+</button>' +
      '<button type="button" data-act="flow"></button>' +
      '<span class="column-strip-spacer"></span>' +
      '<button type="button" data-act="remove">Remove column</button>';
    column.prepend(strip);
    refreshStrip(column);
    return strip;
  }

  function refreshStrip(column) {
    const strip = column.querySelector(".column-strip");
    if (!strip) return;
    const weight = Number(column.dataset.weight) || 2;
    strip.querySelector(".column-weight").textContent = weight;
    strip.querySelector('[data-act="flow"]').textContent =
      column.classList.contains("flow-wrap") ? "Side by side" : "Stacked";
    strip.querySelector('[data-act="narrow"]').disabled = weight <= MIN_WEIGHT;
    strip.querySelector('[data-act="wide"]').disabled = weight >= MAX_WEIGHT;
    strip.querySelector('[data-act="remove"]').disabled = columns().length < 2;
  }

  function refreshAll() {
    columns().forEach(refreshStrip);
    refreshHidden();
    document.querySelectorAll(".card[data-tile]").forEach(refreshHandle);
    if (addColumnBtn) addColumnBtn.disabled = columns().length >= MAX_COLUMNS;
  }

  function hideCard(card) {
    if (!store) return;
    store.appendChild(card);
    refreshAll();
    save();
  }

  function restoreCard(tile) {
    const card = store && store.querySelector(`.card[data-tile="${tile}"]`);
    const target = columns()[0];
    if (!card || !target) return;
    target.appendChild(card);
    if (editing) card.prepend(makeHandle(card));
    refreshAll();
    save();
  }

  function refreshHidden() {
    if (!hiddenBar) return;
    const cards = hiddenCards();
    hiddenBar.hidden = cards.length === 0;
    hiddenBar.textContent = "";
    if (!cards.length) return;
    const caption = document.createElement("span");
    caption.className = "layout-hidden-label";
    caption.textContent = "Hidden:";
    hiddenBar.append(caption);
    cards.forEach((card) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "layout-chip";
      chip.textContent = LABELS[card.dataset.tile] || card.dataset.tile;
      chip.title = "Put this card back";
      chip.addEventListener("click", () => restoreCard(card.dataset.tile));
      hiddenBar.append(chip);
    });
  }

  function setEditing(on) {
    editing = on;
    layoutEl.classList.toggle("editing", on);
    bar.hidden = !on;
    toggle.setAttribute("aria-pressed", on ? "true" : "false");
    say(DEFAULT_HINT);

    if (on) {
      columns().forEach(makeStrip);
      layoutEl.querySelectorAll(".card[data-tile]").forEach((card) => {
        card.prepend(makeHandle(card));
      });
      refreshAll();
    } else {
      document.querySelectorAll(".tile-handle, .column-strip")
        .forEach((el) => el.remove());
      // An empty column has nothing to show and no way to drop into it
      // once the handles are gone, so it goes when editing ends.
      const emptied = columns().filter((c) => !c.querySelector(".card[data-tile]"));
      // ...but never the last one: with every card hidden there has to be
      // somewhere to put them back.
      const removable = emptied.length === columns().length ? emptied.slice(1) : emptied;
      if (removable.length) {
        removable.forEach((c) => c.remove());
        save();
      }
    }
  }

  // ---- dragging ------------------------------------------------------

  function startDrag(ev, card) {
    if (!editing || ev.button > 0) return;
    ev.preventDefault();
    drag = card;
    card.classList.add("tile-dragging");
    window.addEventListener("pointermove", onDragMove);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
  }

  function onDragMove(ev) {
    if (!drag) return;
    // The dragged card is pointer-events:none while in hand, so this
    // reports what is underneath it rather than the card itself.
    const under = document.elementFromPoint(ev.clientX, ev.clientY);
    if (!under) return;
    const column = under.closest(".layout-column");
    if (!column) return;

    columns().forEach((c) => c.classList.toggle("drop-target", c === column));

    const over = under.closest(".card[data-tile]");
    if (over && over !== drag) {
      const box = over.getBoundingClientRect();
      // A side-by-side column is read left to right, a stacked one top to
      // bottom, so the halfway line that decides before/after differs.
      const sideways = column.classList.contains("flow-wrap");
      const past = sideways
        ? ev.clientX > box.left + box.width / 2
        : ev.clientY > box.top + box.height / 2;
      over.parentNode.insertBefore(drag, past ? over.nextSibling : over);
    } else if (!column.contains(drag)) {
      column.appendChild(drag);
    }
  }

  function endDrag() {
    if (!drag) return;
    drag.classList.remove("tile-dragging");
    drag = null;
    columns().forEach((c) => c.classList.remove("drop-target"));
    window.removeEventListener("pointermove", onDragMove);
    window.removeEventListener("pointerup", endDrag);
    window.removeEventListener("pointercancel", endDrag);
    refreshAll();
    save();
  }

  // ---- column controls -----------------------------------------------

  layoutEl.addEventListener("click", (ev) => {
    const button = ev.target.closest(".column-strip button");
    if (!button) return;
    const column = button.closest(".layout-column");
    const act = button.dataset.act;
    let weight = Number(column.dataset.weight) || 2;

    if (act === "narrow" || act === "wide") {
      weight = Math.max(MIN_WEIGHT, Math.min(MAX_WEIGHT, weight + (act === "wide" ? 1 : -1)));
      column.dataset.weight = weight;
      column.style.setProperty("--column-weight", weight);
    } else if (act === "flow") {
      column.classList.toggle("flow-wrap");
      column.classList.toggle("flow-stack");
    } else if (act === "remove") {
      if (columns().length < 2) return;
      const others = columns().filter((c) => c !== column);
      const target = others[Math.max(0, columns().indexOf(column) - 1)] || others[0];
      column.querySelectorAll(".card[data-tile]").forEach((card) => target.appendChild(card));
      column.remove();
    }
    refreshAll();
    save();
  });

  if (addColumnBtn) {
    addColumnBtn.addEventListener("click", () => {
      if (columns().length >= MAX_COLUMNS) return;
      const column = document.createElement("section");
      column.className = "layout-column flow-stack";
      column.dataset.weight = "2";
      column.style.setProperty("--column-weight", "2");
      layoutEl.appendChild(column);
      makeStrip(column);
      refreshAll();
      save();
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      try {
        const res = await fetch("/api/layout/reset", { method: "POST" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        // Reloading rather than rebuilding the DOM: the server renders the
        // arrangement, so this is the one path guaranteed to agree with it.
        window.location.reload();
      } catch (err) {
        say(`Could not reset the layout (${err.message}).`);
      }
    });
  }

  toggle.addEventListener("click", () => setEditing(!editing));
  if (doneBtn) doneBtn.addEventListener("click", () => setEditing(false));
})();
