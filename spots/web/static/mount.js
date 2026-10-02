// Live View's mount card: hold an arrow to move the mount, let go to stop.
//
// While an arrow is held the move is re-sent every HEARTBEAT_MS. The server
// stops any motor it stops hearing about, so a dropped connection can't
// leave the mount slewing. Losing the button any other way -- the pointer
// cancelled, the window losing focus, the page hidden -- stops it here too.
//
// With the card's toggle on, the keyboard's arrow keys do the same as the
// arrows on screen.
(function () {
  const card = document.getElementById("mount-card");
  if (!card) return;

  const HEARTBEAT_MS = 300;
  const POLL_MS = 3000;
  const SPEED_KEY = "spots.mountSpeed";
  const KEYS_KEY = "spots.mountKeys";
  const ARROW_KEYS = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };
  const speeds = window.SPOTS_MOUNT_SPEEDS || {};

  const arrows = Array.from(card.querySelectorAll(".mount-btn"));
  const stopBtn = document.getElementById("mount-stop");
  const speedInput = document.getElementById("mount-speed");
  const speedValue = document.getElementById("mount-speed-value");
  const speedRate = document.getElementById("mount-speed-rate");
  const stateEl = document.getElementById("mount-state");
  const connectBtn = document.getElementById("mount-connect");
  const keysToggle = document.getElementById("mount-keys");

  // direction -> { timer, button }
  const held = new Map();
  // Directions held down on the keyboard, so a key only lets go of its own.
  const keysHeld = new Set();
  let connected = false;

  function speed() {
    return Number(speedInput.value) || 5;
  }

  function showSpeed() {
    speedValue.textContent = speedInput.value;
    speedRate.textContent = speeds[speedInput.value] || "";
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

  function render(status, problem) {
    connected = Boolean(status && status.connected);
    arrows.forEach((b) => { b.disabled = !connected; });
    stopBtn.disabled = !connected;
    connectBtn.hidden = connected || Boolean(status && status.connecting);
    let text;
    let bad = false;
    if (connected) {
      text = `Connected${status.model ? ` to the ${status.model}` : ""}`
        + (status.port ? ` on ${status.port}` : "") + ".";
    } else if (status && status.connecting) {
      text = "Connecting…";
    } else {
      text = `Not connected${status && status.error ? `: ${status.error}` : "."}`;
      bad = true;
    }
    if (problem) {
      text = problem;
      bad = true;
    }
    stateEl.textContent = text;
    stateEl.className = "mount-state" + (bad ? " is-bad" : "");
    if (!connected) dropAll();
  }

  async function send(direction) {
    try {
      const { ok, data } = await post("/api/mount/move", { direction, speed: speed() });
      if (!ok) {
        release(direction, false);
        render(data, data.error || "The mount didn't move.");
      }
    } catch (err) {
      // Nothing to do: the next beat tries again, and the server stops the
      // motor if none get through.
    }
  }

  function press(direction, button) {
    if (!connected || held.has(direction)) return;
    held.set(direction, { timer: setInterval(() => send(direction), HEARTBEAT_MS), button });
    button.classList.add("is-held");
    send(direction);
  }

  // Forgets a held arrow without telling the server.
  function drop(direction) {
    const hold = held.get(direction);
    if (!hold) return false;
    clearInterval(hold.timer);
    hold.button.classList.remove("is-held");
    held.delete(direction);
    return true;
  }

  function dropAll() {
    Array.from(held.keys()).forEach(drop);
  }

  function release(direction, tell = true) {
    if (drop(direction) && tell) {
      post("/api/mount/stop", { direction }).catch(() => {});
    }
  }

  function releaseAll(closing) {
    if (!held.size) return;
    dropAll();
    if (closing && navigator.sendBeacon) {
      // A fetch may not survive the page going away; a beacon does.
      navigator.sendBeacon("/api/mount/stop", JSON.stringify({}));
    } else {
      post("/api/mount/stop", {}).catch(() => {});
    }
  }

  arrows.forEach((button) => {
    const direction = button.dataset.dir;
    button.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      // Keeps the release coming here even if the finger slides off.
      if (button.setPointerCapture) button.setPointerCapture(ev.pointerId);
      press(direction, button);
    });
    ["pointerup", "pointercancel", "lostpointercapture"].forEach((name) => {
      button.addEventListener(name, () => release(direction));
    });
    button.addEventListener("contextmenu", (ev) => ev.preventDefault());
    // Enter held on a focused arrow; space is left to the range status.
    button.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter") return;
      ev.preventDefault();
      if (!ev.repeat) press(direction, button);
    });
    button.addEventListener("keyup", (ev) => {
      if (ev.key === "Enter") release(direction);
    });
    button.addEventListener("blur", () => release(direction));
  });

  stopBtn.addEventListener("click", () => {
    dropAll();
    post("/api/mount/stop", {}).then(({ ok, data }) => {
      if (!ok) render(data, data.error || "Couldn't stop the mount.");
    }).catch(() => {
      render({ connected }, "Couldn't reach S.P.O.T.S to stop the mount. "
        + "Use the hand controller.");
    });
  });

  connectBtn.addEventListener("click", async () => {
    connectBtn.disabled = true;
    render({ connecting: true });
    try {
      const { data } = await post("/api/mount/connect", {});
      render(data);
    } catch (err) {
      render({}, "Couldn't reach S.P.O.T.S.");
    } finally {
      connectBtn.disabled = false;
    }
  });

  // ---- the keyboard's arrow keys -----------------------------------------

  // Where the arrow keys already do something: typing, sliders, dropdowns,
  // radio buttons and the site menu.
  function arrowsBusy() {
    const el = document.activeElement;
    if (!el || el === document.body) return false;
    if (el.isContentEditable) return true;
    if (el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true;
    if (el.tagName === "INPUT" && !["checkbox", "button", "submit", "reset"].includes(el.type)) {
      return true;
    }
    return Boolean(el.closest && el.closest('[role="menu"], [role="listbox"]'));
  }

  function arrowFor(direction) {
    return arrows.find((b) => b.dataset.dir === direction);
  }

  document.addEventListener("keydown", (ev) => {
    const direction = ARROW_KEYS[ev.key];
    if (!direction || !keysToggle.checked) return;
    if (ev.defaultPrevented || ev.ctrlKey || ev.altKey || ev.metaKey || arrowsBusy()) return;
    ev.preventDefault();                  // or the page scrolls as well
    if (ev.repeat || keysHeld.has(direction)) return;
    keysHeld.add(direction);
    press(direction, arrowFor(direction));
  });

  document.addEventListener("keyup", (ev) => {
    const direction = ARROW_KEYS[ev.key];
    if (!direction || !keysHeld.delete(direction)) return;
    release(direction);
  });

  function releaseKeys() {
    keysHeld.forEach((direction) => release(direction));
    keysHeld.clear();
  }

  try {
    keysToggle.checked = localStorage.getItem(KEYS_KEY) === "1";
  } catch (err) {
    /* private browsing -- starts off */
  }
  keysToggle.addEventListener("change", () => {
    if (!keysToggle.checked) releaseKeys();
    try {
      localStorage.setItem(KEYS_KEY, keysToggle.checked ? "1" : "0");
    } catch (err) {
      /* ignore */
    }
  });
  // A dragged speed slider keeps focus, which would give it the arrow keys.
  speedInput.addEventListener("pointerup", () => {
    if (keysToggle.checked && speedInput.blur) speedInput.blur();
  });

  window.addEventListener("blur", () => {
    keysHeld.clear();
    releaseAll(false);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      keysHeld.clear();
      releaseAll(false);
    }
  });
  window.addEventListener("pagehide", () => {
    keysHeld.clear();
    releaseAll(true);
  });

  try {
    const saved = localStorage.getItem(SPEED_KEY);
    if (saved && speeds[saved]) speedInput.value = saved;
  } catch (err) {
    /* private browsing -- the speed just isn't remembered */
  }
  speedInput.addEventListener("input", () => {
    showSpeed();
    try {
      localStorage.setItem(SPEED_KEY, speedInput.value);
    } catch (err) {
      /* ignore */
    }
  });
  showSpeed();

  async function refresh() {
    if (document.visibilityState === "hidden" || held.size) return;
    try {
      const resp = await fetch("/api/mount", { cache: "no-store" });
      render(await resp.json());
    } catch (err) {
      /* the next poll tries again */
    }
  }
  refresh();
  setInterval(refresh, POLL_MS);

  window.SPOTS_MOUNT = { held: () => Array.from(held.keys()) };
})();
