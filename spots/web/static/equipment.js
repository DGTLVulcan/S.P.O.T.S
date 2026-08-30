// Equipment manager. The three kinds record genuinely different things --
// a rifle's twist rate, a scope's turret click value, a bullet's weight --
// so the forms are built from the schema the server sends rather than
// hardcoded here, which keeps them in step with the validation.
(function () {
  const root = document.getElementById("equipment-root");
  if (!root) return;
  const errorEl = document.getElementById("equipment-error");

  let schema = {};

  function escapeHtml(text) {
    return String(text === null || text === undefined ? "" : text).replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  async function api(url, body) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    return data;
  }

  function setError(message) {
    errorEl.textContent = message || "";
  }

  function fieldInput(field, value) {
    const val = value === null || value === undefined ? "" : value;
    if (field.type === "select") {
      const options = field.options
        .map(
          (o) =>
            `<option value="${escapeHtml(o.value)}"${
              String(o.value) === String(val) ? " selected" : ""
            }>${escapeHtml(o.label)}</option>`
        )
        .join("");
      return `<select class="equip-field" data-key="${field.key}">${options}</select>`;
    }
    const type = field.type === "number" ? "number" : "text";
    const step = field.type === "number" ? ` step="${escapeHtml(field.step)}"` : "";
    return (
      `<input class="equip-field" data-key="${field.key}" type="${type}"${step}` +
      ` value="${escapeHtml(val)}" placeholder="${escapeHtml(field.placeholder)}">`
    );
  }

  function fieldBlock(field, value) {
    return `
      <label class="field equip-field-block">
        <span class="equip-field-label">${escapeHtml(field.label)}${
          field.unit ? ` <span class="hint">(${escapeHtml(field.unit)})</span>` : ""
        }</span>
        ${fieldInput(field, value)}
      </label>`;
  }

  function itemCard(kind, item) {
    const specs = item.specs || {};
    // click_value/click_unit are real columns rather than specs entries, but
    // they belong in the same form, in schema order.
    const valueFor = (field) => (field.column ? item[field.key] : specs[field.key]);
    return `
      <div class="equip-card" data-id="${item.id}" data-kind="${kind}">
        <div class="equip-card-head">
          <input class="equip-name" type="text" value="${escapeHtml(item.name)}"
                 placeholder="Name" aria-label="Name">
          <div class="row-actions">
            <button type="button" class="equip-save">Save</button>
            <button type="button" class="equip-delete session-delete-btn">Delete</button>
          </div>
        </div>
        <div class="equip-fields">
          ${schema[kind].fields.map((f) => fieldBlock(f, valueFor(f))).join("")}
        </div>
        <label class="field equip-notes-block">
          <span class="equip-field-label">Notes</span>
          <input class="equip-notes" type="text" value="${escapeHtml(item.notes)}"
                 placeholder="anything else worth remembering">
        </label>
      </div>`;
  }

  function section(kind, items) {
    const meta = schema[kind];
    return `
      <section class="equip-section" data-kind="${kind}">
        <h2>${escapeHtml(meta.title)}</h2>
        ${items.length
          ? items.map((i) => itemCard(kind, i)).join("")
          : `<p class="empty-state">No ${escapeHtml(meta.title.toLowerCase())} yet.</p>`}
        <div class="controls equip-add-row">
          <input class="equip-new-name" type="text"
                 placeholder="${escapeHtml(meta.name_placeholder)}">
          <button type="button" class="equip-add primary">Add ${escapeHtml(meta.singular)}</button>
        </div>
      </section>`;
  }

  function collect(container) {
    const specs = {};
    container.querySelectorAll(".equip-field").forEach((el) => {
      specs[el.dataset.key] = el.value;
    });
    return specs;
  }

  async function render() {
    let data;
    try {
      data = await (await fetch("/api/equipment")).json();
    } catch (err) {
      root.innerHTML = `<p class="empty-state">Couldn't load equipment.</p>`;
      return;
    }
    schema = data.schema;
    root.innerHTML = Object.keys(schema)
      .map((kind) => section(kind, data.items[kind] || []))
      .join("");
  }

  // Delegated: cards are rebuilt on every change, so per-card listeners
  // would go stale immediately.
  root.addEventListener("click", async (ev) => {
    const addBtn = ev.target.closest(".equip-add");
    if (addBtn) {
      const wrap = addBtn.closest(".equip-section");
      const kind = wrap.dataset.kind;
      const nameInput = wrap.querySelector(".equip-new-name");
      const name = nameInput.value.trim();
      if (!name) {
        setError("Give it a name first.");
        nameInput.focus();
        return;
      }
      try {
        // Added with just a name; the specs are filled in on the card that
        // appears, which avoids a second sprawling form up front.
        await api("/api/equipment", { kind, name });
        setError("");
        await render();
      } catch (err) {
        setError("Add failed: " + err.message);
      }
      return;
    }

    const saveBtn = ev.target.closest(".equip-save");
    if (saveBtn) {
      const card = saveBtn.closest(".equip-card");
      try {
        await api(`/api/equipment/${card.dataset.id}`, {
          name: card.querySelector(".equip-name").value.trim(),
          notes: card.querySelector(".equip-notes").value.trim(),
          specs: collect(card),
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
      const card = deleteBtn.closest(".equip-card");
      const name = card.querySelector(".equip-name").value;
      if (!window.confirm(`Delete "${name}"?`)) return;
      try {
        await api(`/api/equipment/${card.dataset.id}/delete`);
        setError("");
        await render();
      } catch (err) {
        setError("Delete failed: " + err.message);
      }
    }
  });

  // Enter in the "add" box adds, rather than doing nothing.
  root.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.target.classList.contains("equip-new-name")) {
      ev.preventDefault();
      ev.target.closest(".equip-add-row").querySelector(".equip-add").click();
    }
  });

  render();
})();
