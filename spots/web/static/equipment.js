// Equipment manager on the Settings page: add, edit and remove the rifles,
// scopes and ammo offered by the header dropdowns. Scopes additionally carry
// the turret click value that Scope Correction uses, so two scopes with
// different turrets each give the right answer.
(function () {
  const root = document.getElementById("equipment-root");
  if (!root) return;

  const KINDS = [
    { kind: "rifle", title: "Rifles", placeholder: "e.g. Tikka T3x .308" },
    { kind: "scope", title: "Scopes", placeholder: "e.g. Vortex Viper PST" },
    { kind: "ammo", title: "Ammo", placeholder: "e.g. 168gr HPBT, 42.5gr Varget" },
  ];

  function escapeHtml(text) {
    return String(text === null || text === undefined ? "" : text).replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  async function api(url, body, method) {
    const resp = await fetch(url, {
      method: method || "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    return data;
  }

  function setError(message) {
    const el = document.getElementById("equipment-error");
    if (el) el.textContent = message || "";
  }

  function itemRow(item) {
    const isScope = item.kind === "scope";
    return `
      <tr data-id="${item.id}" data-kind="${item.kind}">
        <td><input class="equip-name" type="text" value="${escapeHtml(item.name)}"></td>
        <td>${
          isScope
            ? `<input class="equip-click-value" type="number" step="0.001" min="0.001"
                      style="width: 6rem;" value="${item.click_value === null ? "" : item.click_value}"
                      placeholder="0.25">`
            : '<span class="hint">&mdash;</span>'
        }</td>
        <td>${
          isScope
            ? `<select class="equip-click-unit">
                 <option value="moa"${item.click_unit !== "mrad" ? " selected" : ""}>MOA</option>
                 <option value="mrad"${item.click_unit === "mrad" ? " selected" : ""}>mrad</option>
               </select>`
            : '<span class="hint">&mdash;</span>'
        }</td>
        <td><input class="equip-notes" type="text" value="${escapeHtml(item.notes)}" placeholder="notes"></td>
        <td class="row-actions">
          <button type="button" class="equip-save">Save</button>
          <button type="button" class="equip-delete session-delete-btn">Delete</button>
        </td>
      </tr>`;
  }

  function section(group, items) {
    return `
      <div class="equipment-section">
        <h3>${group.title}</h3>
        <div class="table-scroll">
          <table>
            <thead>
              <tr><th>Name</th><th>Click value</th><th>Unit</th><th>Notes</th><th></th></tr>
            </thead>
            <tbody data-kind="${group.kind}">
              ${items.length
                ? items.map(itemRow).join("")
                : `<tr><td colspan="5"><span class="empty-state">None yet.</span></td></tr>`}
            </tbody>
          </table>
        </div>
        <div class="controls equipment-add" data-kind="${group.kind}">
          <input class="equip-new-name" type="text" placeholder="${group.placeholder}">
          ${group.kind === "scope"
            ? `<input class="equip-new-click-value" type="number" step="0.001" min="0.001"
                      style="width: 7rem;" placeholder="click value">
               <select class="equip-new-click-unit">
                 <option value="moa">MOA</option>
                 <option value="mrad">mrad</option>
               </select>`
            : ""}
          <button type="button" class="equip-add primary">Add ${group.title.replace(/s$/, "")}</button>
        </div>
      </div>`;
  }

  async function render() {
    let data;
    try {
      data = await (await fetch("/api/equipment")).json();
    } catch (err) {
      root.innerHTML = `<p class="empty-state">Couldn't load equipment.</p>`;
      return;
    }
    root.innerHTML =
      `<p id="equipment-error" class="status"></p>` +
      KINDS.map((group) => section(group, data.items[group.kind] || [])).join("");
  }

  // One delegated handler for the whole panel -- the rows are rebuilt on
  // every change, so per-row listeners would go stale.
  root.addEventListener("click", async (ev) => {
    const addBtn = ev.target.closest(".equip-add");
    if (addBtn) {
      const wrap = addBtn.closest(".equipment-add");
      const kind = wrap.dataset.kind;
      const name = wrap.querySelector(".equip-new-name").value.trim();
      if (!name) {
        setError("Give it a name first.");
        return;
      }
      const clickValue = wrap.querySelector(".equip-new-click-value");
      const clickUnit = wrap.querySelector(".equip-new-click-unit");
      try {
        await api("/api/equipment", {
          kind,
          name,
          click_value: clickValue ? clickValue.value : null,
          click_unit: clickUnit ? clickUnit.value : null,
        });
        setError("");
        await render();
      } catch (err) {
        setError("Add failed: " + err.message);
      }
      return;
    }

    const saveBtn = ev.target.closest(".equip-save");
    if (saveBtn) {
      const row = saveBtn.closest("tr");
      const clickValue = row.querySelector(".equip-click-value");
      const clickUnit = row.querySelector(".equip-click-unit");
      try {
        await api(`/api/equipment/${row.dataset.id}`, {
          name: row.querySelector(".equip-name").value.trim(),
          notes: row.querySelector(".equip-notes").value.trim(),
          click_value: clickValue ? clickValue.value : null,
          click_unit: clickUnit ? clickUnit.value : null,
        });
        setError("");
        saveBtn.textContent = "Saved";
        setTimeout(() => (saveBtn.textContent = "Save"), 1200);
      } catch (err) {
        setError("Save failed: " + err.message);
      }
      return;
    }

    const deleteBtn = ev.target.closest(".equip-delete");
    if (deleteBtn) {
      const row = deleteBtn.closest("tr");
      const name = row.querySelector(".equip-name").value;
      if (!window.confirm(`Delete "${name}"?`)) return;
      try {
        await api(`/api/equipment/${row.dataset.id}/delete`);
        setError("");
        await render();
      } catch (err) {
        setError("Delete failed: " + err.message);
      }
    }
  });

  render();
})();
