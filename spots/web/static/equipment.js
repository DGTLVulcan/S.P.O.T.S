// A sidebar of everything you own plus one detail form for the selection,
// because all three kinds at once was a wall of inputs. Fields come from
// the server's schema, which is also what validates them.
(function () {
  const sidebarEl = document.getElementById("equip-sidebar");
  const detailEl = document.getElementById("equip-detail");
  if (!sidebarEl || !detailEl) return;

  const KIND_ICONS = { rifle: "&#127919;", scope: "&#128301;", ammo: "&#9679;", target: "&#127919;" };

  let schema = {};
  let order = [];        // kinds in display order, from the server
  let items = {};       // kind -> [item]
  let selectedIds = {}; // kind -> id currently chosen on the live view
  let current = null;   // {kind, id} being edited
  let dirty = false;

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

  function findItem(ref) {
    if (!ref) return null;
    return (items[ref.kind] || []).find((i) => i.id === ref.id) || null;
  }

  function toast(message, isError) {
    const el = document.getElementById("equip-toast");
    if (!el) return;
    el.textContent = message || "";
    el.className = "equip-toast" + (isError ? " is-error" : message ? " is-ok" : "");
    if (message && !isError) {
      setTimeout(() => {
        if (el.textContent === message) el.textContent = "";
      }, 2500);
    }
  }

  // ---------------------------------------------------------------- sidebar

  function sidebarItem(kind, item) {
    const isOpen = current && current.kind === kind && current.id === item.id;
    const inUse = selectedIds[kind] === item.id;
    return `
      <button type="button" class="equip-nav-item${isOpen ? " is-open" : ""}"
              data-kind="${kind}" data-id="${item.id}">
        <span class="equip-nav-text">
          <span class="equip-nav-name">${escapeHtml(item.name)}</span>
          ${item.summary ? `<span class="equip-nav-sub">${escapeHtml(item.summary)}</span>` : ""}
        </span>
        ${inUse ? '<span class="equip-in-use" title="Selected on the live view">in use</span>' : ""}
      </button>`;
  }

  function renderSidebar() {
    sidebarEl.innerHTML = order
      .map((kind) => {
        const list = items[kind] || [];
        return `
          <div class="equip-nav-group">
            <div class="equip-nav-head">
              <span class="equip-nav-title">
                <span class="equip-nav-icon">${KIND_ICONS[kind] || ""}</span>
                ${escapeHtml(schema[kind].title)}
                <span class="equip-nav-count">${list.length}</span>
              </span>
              <button type="button" class="equip-add-btn" data-kind="${kind}"
                      title="Add ${escapeHtml(schema[kind].singular)}"
                      aria-label="Add ${escapeHtml(schema[kind].singular)}">+</button>
            </div>
            ${list.length
              ? list.map((i) => sidebarItem(kind, i)).join("")
              : '<p class="equip-nav-empty">None yet</p>'}
          </div>`;
      })
      .join("");
  }

  // ----------------------------------------------------------------- detail

  function fieldControl(field, value) {
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
    const step = field.type === "number" ? ` step="${escapeHtml(field.step)}"` : "";
    const type = field.type === "number" ? "number" : "text";
    return (
      `<input class="equip-field" data-key="${field.key}" type="${type}"${step}` +
      ` value="${escapeHtml(val)}" placeholder="${escapeHtml(field.placeholder)}">`
    );
  }

  // Scoring rings are a list, not a scalar field, so they get their own
  // editor rather than coming from the schema-driven form above.
  function ringRow(ring) {
    return `
      <div class="ring-row">
        <input class="ring-value" type="number" step="any" placeholder="score"
               value="${ring && ring.value !== undefined ? escapeHtml(ring.value) : ""}">
        <input class="ring-diameter" type="number" step="any" min="0" placeholder="diameter"
               value="${ring && ring.diameter !== undefined ? escapeHtml(ring.diameter) : ""}">
        <button type="button" class="ring-remove row-delete-btn" title="Remove ring">&times;</button>
      </div>`;
  }

  function ringEditor(rings) {
    return `
      <div class="equip-field-block equip-notes-block">
        <span class="equip-field-label">
          Scoring rings
          <span class="hint">score and ring diameter, in your target unit</span>
        </span>
        <div class="ring-row" style="font-size:0.72rem;color:var(--ink-muted);">
          <span>Score</span><span>Diameter across</span><span></span>
        </div>
        <div id="ring-list">
          ${(rings.length ? rings : [null]).map(ringRow).join("")}
        </div>
        <button type="button" id="ring-add">Add ring</button>
        <p class="hint">
          A shot counts for the smallest ring it falls inside; outside them all
          scores zero. Leave empty to not score this target.
        </p>
      </div>`;
  }

  function collectRings() {
    return Array.from(detailEl.querySelectorAll(".ring-row"))
      .filter((row) => row.querySelector(".ring-value"))
      .map((row) => ({
        value: row.querySelector(".ring-value").value,
        diameter: row.querySelector(".ring-diameter").value,
      }))
      .filter((r) => r.value !== "" || r.diameter !== "");
  }

  function renderDetail() {
    const item = findItem(current);
    if (!item) {
      detailEl.innerHTML =
        '<div class="equip-placeholder"><p class="empty-state">Nothing selected.<br>' +
        "Pick something on the left, or add one with &plus;.</p></div>";
      return;
    }
    const meta = schema[item.kind];
    const specs = item.specs || {};
    // click_value/click_unit are real columns rather than specs entries, but
    // they belong in the same form, in schema order.
    const valueFor = (f) => (f.column ? item[f.key] : specs[f.key]);
    const inUse = selectedIds[item.kind] === item.id;

    detailEl.innerHTML = `
      <div class="equip-detail-head">
        <div class="equip-detail-titles">
          <span class="equip-detail-kind">${escapeHtml(meta.singular)}</span>
          <input id="equip-name" class="equip-title-input" type="text"
                 value="${escapeHtml(item.name)}" placeholder="Name" aria-label="Name">
        </div>
        <div class="row-actions">
          ${inUse
            ? '<span class="equip-in-use">in use</span>'
            : '<button type="button" id="equip-use">Use this</button>'}
          <button type="button" id="equip-delete" class="session-delete-btn">Delete</button>
        </div>
      </div>

      <div class="equip-fields">
        ${meta.fields
          .map(
            (f) => `
          <label class="equip-field-block">
            <span class="equip-field-label">${escapeHtml(f.label)}${
              f.unit ? ` <span class="hint">(${escapeHtml(f.unit)})</span>` : ""
            }</span>
            ${fieldControl(f, valueFor(f))}
          </label>`
          )
          .join("")}
      </div>

      ${item.kind === "target" ? ringEditor(item.rings || []) : ""}

      <label class="equip-field-block equip-notes-block">
        <span class="equip-field-label">Notes</span>
        <input id="equip-notes" class="equip-field-notes" type="text"
               value="${escapeHtml(item.notes)}" placeholder="anything else worth remembering">
      </label>

      <div class="equip-detail-foot">
        <button type="button" id="equip-save" class="primary">Save changes</button>
        <span id="equip-toast" class="equip-toast"></span>
      </div>`;
    dirty = false;
  }

  function render() {
    renderSidebar();
    renderDetail();
  }

  async function load(keepSelection) {
    let data;
    try {
      data = await (await fetch("/api/equipment")).json();
    } catch (err) {
      sidebarEl.innerHTML = '<p class="empty-state">Could not load equipment.</p>';
      detailEl.innerHTML = "";
      return;
    }
    schema = data.schema;
    order = data.order || Object.keys(data.schema);
    items = data.items;
    selectedIds = data.selected;
    if (!keepSelection || !findItem(current)) {
      const firstKind = order.find((k) => (items[k] || []).length);
      current = firstKind ? { kind: firstKind, id: items[firstKind][0].id } : null;
    }
    render();
  }

  // ---------------------------------------------------------------- actions

  function collectSpecs() {
    const specs = {};
    detailEl.querySelectorAll(".equip-field").forEach((el) => {
      specs[el.dataset.key] = el.value;
    });
    return specs;
  }

  async function save() {
    const item = findItem(current);
    if (!item) return;
    try {
      await api(`/api/equipment/${item.id}`, {
        name: document.getElementById("equip-name").value.trim(),
        notes: document.getElementById("equip-notes").value.trim(),
        specs: collectSpecs(),
        rings: collectRings(),
      });
      await load(true);
      toast("Saved");
    } catch (err) {
      toast(err.message, true);
    }
  }

  sidebarEl.addEventListener("click", async (ev) => {
    const addBtn = ev.target.closest(".equip-add-btn");
    if (addBtn) {
      const kind = addBtn.dataset.kind;
      const name = window.prompt(`Name for the new ${schema[kind].singular.toLowerCase()}:`, "");
      if (name === null || !name.trim()) return;
      try {
        const created = await api("/api/equipment", { kind, name: name.trim() });
        current = { kind, id: created.id };
        await load(true);
        // Land in the first (empty) field of the thing just created.
        const first = detailEl.querySelector(".equip-field");
        if (first) first.focus();
      } catch (err) {
        toast(err.message, true);
      }
      return;
    }

    const navItem = ev.target.closest(".equip-nav-item");
    if (navItem) {
      const next = { kind: navItem.dataset.kind, id: Number(navItem.dataset.id) };
      const same = current && current.kind === next.kind && current.id === next.id;
      if (dirty && !same && !window.confirm("Discard unsaved changes?")) return;
      current = next;
      render();
    }
  });

  detailEl.addEventListener("click", async (ev) => {
    if (ev.target.closest("#equip-save")) {
      save();
      return;
    }
    if (ev.target.closest("#ring-add")) {
      document.getElementById("ring-list").insertAdjacentHTML("beforeend", ringRow(null));
      dirty = true;
      return;
    }
    const ringRemove = ev.target.closest(".ring-remove");
    if (ringRemove) {
      const list = document.getElementById("ring-list");
      ringRemove.closest(".ring-row").remove();
      if (!list.querySelector(".ring-row")) list.insertAdjacentHTML("beforeend", ringRow(null));
      dirty = true;
      return;
    }
    if (ev.target.closest("#equip-use")) {
      const item = findItem(current);
      try {
        await api("/api/equipment/select", { kind: item.kind, id: item.id });
        await load(true);
        toast(item.name + " selected on the live view");
      } catch (err) {
        toast(err.message, true);
      }
      return;
    }
    if (ev.target.closest("#equip-delete")) {
      const item = findItem(current);
      if (!window.confirm(`Delete "${item.name}"?`)) return;
      try {
        await api(`/api/equipment/${item.id}/delete`);
        current = null;
        await load(false);
      } catch (err) {
        toast(err.message, true);
      }
    }
  });

  detailEl.addEventListener("input", () => {
    dirty = true;
  });

  // Enter anywhere in the form saves, the way a form would.
  detailEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.target.tagName === "INPUT") {
      ev.preventDefault();
      save();
    }
  });
  document.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
      ev.preventDefault();
      save();
    }
  });
  // Leaving with unsaved edits is almost always a mistake here.
  window.addEventListener("beforeunload", (ev) => {
    if (dirty) {
      ev.preventDefault();
      ev.returnValue = "";
    }
  });

  load(false);
})();
