// The range-hot / cease-fire banner.
//
// The state is held on the Pi, not in the browser, so every phone looking
// at S.P.O.T.S shows the same thing -- which is the only way a range
// indicator is worth anything. Pages other than the dashboard poll for it,
// since someone else may have called the cease fire.
(function () {
  const banner = document.getElementById("range-banner");
  const toggle = document.getElementById("range-toggle");
  const big = document.getElementById("range-big");
  if (!toggle) return;

  const POLL_MS = 15000;
  const spacebar = toggle.dataset.spacebar === "1";
  let state = toggle.classList.contains("is-cease") ? "cease" : "hot";
  let busy = false;

  function paint(next) {
    state = next;
    const hot = state === "hot";
    if (banner) {
      banner.classList.toggle("is-hot", hot);
      banner.classList.toggle("is-cease", !hot);
      const text = banner.querySelector(".range-banner-text");
      if (text) text.textContent = hot ? "Range Hot" : "Cease Fire";
    }
    toggle.classList.toggle("is-hot", hot);
    toggle.classList.toggle("is-cease", !hot);
    if (big) {
      big.classList.toggle("is-hot", hot);
      big.classList.toggle("is-cease", !hot);
      const stateEl = big.querySelector(".range-big-state");
      const action = big.querySelector(".range-big-action");
      if (stateEl) stateEl.textContent = hot ? "Range Hot" : "Cease Fire";
      if (action) {
        const tap = hot ? "Tap to call a cease fire" : "Tap to make the range hot";
        action.textContent = spacebar ? `${tap}, or press space` : tap;
      }
      const note = document.querySelector(".range-big-note");
      if (note) {
        note.textContent = hot
          ? "Detection is running."
          : "Detection is paused — shots fired now are not recorded.";
      }
    }
    toggle.setAttribute("aria-pressed", hot ? "false" : "true");
    if (!toggle.disabled) {
      toggle.title = hot
        ? "Range is hot — call a cease fire"
        : "Cease fire — make the range hot";
    }
  }

  async function set(next) {
    if (busy || toggle.disabled) return;
    busy = true;
    const previous = state;
    paint(next);                       // move now; the range won't wait
    try {
      const res = await fetch("/api/range_state", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: next }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      paint(data.state);
    } catch (err) {
      // Put it back rather than leave the banner claiming something the
      // Pi never agreed to.
      paint(previous);
      if (banner) {
        const text = banner.querySelector(".range-banner-text");
        if (text) text.textContent = "Range status could not be changed";
      }
    } finally {
      busy = false;
    }
  }

  function flip() {
    set(state === "hot" ? "cease" : "hot");
  }

  toggle.addEventListener("click", flip);
  if (big) big.addEventListener("click", flip);

  // Space anywhere on the page, when it isn't already doing something else.
  // A shortcut that flips a safety state must not go off while someone is
  // typing a session name, nor steal the key from a focused control -- space
  // presses buttons, ticks checkboxes and opens <summary>.
  function busyElsewhere() {
    const el = document.activeElement;
    if (!el || el === document.body) return false;
    if (el.isContentEditable) return true;
    return ["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A", "SUMMARY", "OPTION"]
      .includes(el.tagName);
  }

  if (spacebar && !toggle.disabled) {
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== " " && ev.code !== "Space") return;
      if (ev.repeat || ev.ctrlKey || ev.altKey || ev.metaKey) return;
      if (busyElsewhere()) return;
      ev.preventDefault();      // otherwise the page scrolls as well
      flip();
    });
  }

  async function poll() {
    if (busy) return;
    try {
      const res = await fetch("/api/range_state", { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      if (data.enabled && data.state !== state) paint(data.state);
    } catch (err) {
      /* the next tick can try again */
    }
  }

  if (!toggle.disabled) {
    poll();                            // another device may have called it
    setInterval(poll, POLL_MS);
    // Coming back to a page that has been open in a pocket all morning
    // should not show a stale banner.
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) poll();
    });
  }
})();
