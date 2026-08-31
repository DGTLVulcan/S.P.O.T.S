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
  const resetBtn = document.getElementById("layout-reset");
  const doneBtn = document.getElementById("layout-done");

  const LABELS = window.SPOTS_TILES || {};
  const MAX_COLUMNS = 4;
  const MIN_WEIGHT = 1;
  const MAX_WEIGHT = 6;
  const DEFAULT_HINT = hint ? hint.textContent : "";

  let editing = false;
  let drag = null;
  let saveTimer = null;

  function columns() {
    return Array.from(layoutEl.querySelectorAll(".layout-column"));
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
    const handle = document.createElement("button");
    handle.type = "button";
    handle.className = "tile-handle";
    const grip = document.createElement("span");
    grip.className = "tile-grip";
    const label = document.createElement("span");
    label.textContent = LABELS[card.dataset.tile] || card.dataset.tile;
    handle.append(grip, label);
    handle.addEventListener("pointerdown", (ev) => startDrag(ev, card));
    return handle;
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
    if (addColumnBtn) addColumnBtn.disabled = columns().length >= MAX_COLUMNS;
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
      layoutEl.querySelectorAll(".tile-handle, .column-strip")
        .forEach((el) => el.remove());
      // An empty column has nothing to show and no way to drop into it
      // once the handles are gone, so it goes when editing ends.
      const emptied = columns().filter((c) => !c.querySelector(".card[data-tile]"));
      if (emptied.length) {
        emptied.forEach((c) => c.remove());
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
