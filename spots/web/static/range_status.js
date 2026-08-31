// The range-hot / cease-fire banner.
//
// The state is held on the Pi, not in the browser, so every phone looking
// at S.P.O.T.S shows the same thing -- which is the only way a range
// indicator is worth anything. Pages other than the dashboard poll for it,
// since someone else may have called the cease fire.
(function () {
  const banner = document.getElementById("range-banner");
  const toggle = document.getElementById("range-toggle");
  if (!toggle) return;

  const POLL_MS = 15000;
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

  toggle.addEventListener("click", () => set(state === "hot" ? "cease" : "hot"));

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
    setInterval(poll, POLL_MS);
    // Coming back to a page that has been open in a pocket all morning
    // should not show a stale banner.
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) poll();
    });
  }
})();
