// The rifle / scope / ammo / target pickers in the app bar.
//
// Shared by every page that shows them, so the dashboard and the ballistics
// page cannot drift apart about what is selected or how it is presented.
// The selection lives on the Pi, so it is the same on every device looking
// at S.P.O.T.S.
(function () {
  const KINDS = ["rifle", "scope", "ammo", "target"];
  const LABELS = { rifle: "Rifle", scope: "Scope", ammo: "Ammo", target: "Target" };

  const listeners = [];
  let statusHandler = () => {};

  const select = (kind) => document.getElementById("equip-" + kind);

  function present() {
    return KINDS.some((kind) => select(kind));
  }

  async function load() {
    let data;
    try {
      data = await (await fetch("/api/equipment")).json();
    } catch (err) {
      return;   // the pickers are furniture; an empty one is survivable
    }
    for (const kind of KINDS) {
      const element = select(kind);
      if (!element) continue;
      // Only offer ammo that suits the rifle's chambering. The server
      // decides that, so this filter can't disagree with it.
      const all = data.items[kind] || [];
      const usable = all.filter((item) => item.compatible !== false);
      const hidden = all.length - usable.length;
      const selected = data.selected[kind];

      let placeholder = `${LABELS[kind]}: none`;
      if (kind === "ammo" && !usable.length && data.calibres && data.calibres.rifle) {
        placeholder = `No ${data.calibres.rifle} ammo`;
      }

      element.innerHTML =
        `<option value="">${placeholder}</option>` +
        usable.map((item) => {
          const detail = kind === "scope" && item.click_value
            ? [item.summary, `${item.click_value} ${item.click_unit || "moa"}/click`]
                .filter(Boolean).join(", ")
            : item.summary;
          const label = detail ? `${item.name} (${detail})` : item.name;
          return `<option value="${item.id}"${item.id === selected ? " selected" : ""}>${label}</option>`;
        }).join("");

      element.title = hidden
        ? `${LABELS[kind]} — ${hidden} hidden: wrong calibre for the selected rifle`
        : LABELS[kind];
    }
  }

  async function choose(kind, element) {
    try {
      const res = await fetch("/api/equipment/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, id: element.value || null }),
      });
      const result = await res.json();
      if (!res.ok) throw new Error(result.error || `HTTP ${res.status}`);

      let message = `${LABELS[kind]} set to ${element.options[element.selectedIndex].text}.`;
      if (result.cleared_ammo) {
        message += ` ${result.cleared_ammo} unselected -- wrong calibre for this rifle.`;
      }
      statusHandler(message);
      // Changing the rifle changes which ammo is on offer, and the scope
      // changes the turret click value, so the whole bar is re-read.
      await load();
      listeners.forEach((fn) => fn(kind, result));
    } catch (err) {
      statusHandler(`Error selecting ${kind}: ${err.message}`);
      load();
    }
  }

  if (present()) {
    KINDS.forEach((kind) => {
      const element = select(kind);
      if (element) element.addEventListener("change", () => choose(kind, element));
    });
    load();
  }

  window.SPOTS_EQUIPMENT = {
    load,
    kinds: KINDS,
    labels: LABELS,
    onChange: (fn) => listeners.push(fn),
    onStatus: (fn) => { statusHandler = fn; },
  };
})();
