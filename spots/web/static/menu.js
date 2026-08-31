// The site menu behind the hamburger button.
//
// The panel is in the markup and merely hidden, so the links are real links
// that work with JavaScript off -- this only handles opening, closing, and
// keyboard use.
(function () {
  const button = document.getElementById("menu-button");
  const panel = document.getElementById("menu-panel");
  if (!button || !panel) return;

  function items() {
    return Array.from(panel.querySelectorAll(".menu-item"));
  }

  function open(focusFirst) {
    panel.hidden = false;
    button.setAttribute("aria-expanded", "true");
    if (focusFirst) {
      const first = panel.querySelector(".is-current") || items()[0];
      if (first) first.focus();
    }
  }

  function close(returnFocus) {
    panel.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (returnFocus) button.focus();
  }

  button.addEventListener("click", (ev) => {
    ev.stopPropagation();
    if (panel.hidden) open(false);
    else close(false);
  });

  // Anywhere else on the page dismisses it, which is what a stray tap on a
  // phone means.
  document.addEventListener("click", (ev) => {
    if (!panel.hidden && !panel.contains(ev.target) && ev.target !== button) close(false);
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !panel.hidden) close(true);
  });

  button.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      open(true);
    }
  });

  panel.addEventListener("keydown", (ev) => {
    if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp") return;
    ev.preventDefault();
    const all = items();
    const at = all.indexOf(document.activeElement);
    const next = ev.key === "ArrowDown" ? at + 1 : at - 1;
    const target = all[(next + all.length) % all.length];
    if (target) target.focus();
  });
})();
