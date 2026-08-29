(function () {
  const KEY = "spots-theme";
  const CYCLE = [null, "light", "dark"]; // null = follow OS

  function apply(theme) {
    if (theme === "light" || theme === "dark") {
      document.documentElement.setAttribute("data-theme", theme);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
  }

  // Applied immediately (before DOMContentLoaded) to minimize theme flash.
  apply(localStorage.getItem(KEY));

  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("theme-toggle");
    if (!btn) return;

    function updateIcon() {
      const current = localStorage.getItem(KEY);
      btn.textContent = current === "dark" ? "☀" : current === "light" ? "☽" : "◑";
      btn.title = "Theme: " + (current || "auto") + " (click to change)";
    }

    btn.addEventListener("click", () => {
      const current = localStorage.getItem(KEY);
      const next = CYCLE[(CYCLE.indexOf(current) + 1) % CYCLE.length];
      if (next) localStorage.setItem(KEY, next);
      else localStorage.removeItem(KEY);
      apply(next);
      updateIcon();
    });

    updateIcon();
  });
})();
