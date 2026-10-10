// Visual block editor for the content form. It reads the block JSON from the form's
// <textarea name="blocks">, shows one card per block with fields made from the server's
// description of the block models (#rb-block-spec), and writes the JSON back on every edit.
// The server validates exactly as before. Without JavaScript the textarea stays as it is.
(function () {
  "use strict";

  const specEl = document.getElementById("rb-block-spec");
  const textarea = document.querySelector('textarea[name="blocks"]');
  const root = document.getElementById("rb-blocks-editor");
  if (!specEl || !textarea || !root) return;

  const spec = JSON.parse(specEl.textContent);
  const byType = {};
  spec.blocks.forEach((b) => { byType[b.type] = b; });
  let blocks = [];
  let insertAfter = null; // index of the card whose "Add below" picker is open
  let dragFrom = null; // index of the card being dragged by its handle

  // ------------------------------------------------------------------ helpers

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (v === undefined || v === null || v === false) return;
      if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    });
    (children || []).forEach((c) => c && node.appendChild(c));
    return node;
  }

  function button(label, aria, onclick, extra) {
    return el("button", Object.assign({ type: "button", class: "rb-btn rb-btn--small", "aria-label": aria, onclick: onclick, text: label }, extra || {}));
  }

  function save() {
    textarea.value = JSON.stringify(blocks, null, 2);
    schedulePreview();
  }

  // A new, empty value for a field (what "Add" puts in).
  function blank(field) {
    if (field.default !== undefined) return JSON.parse(JSON.stringify(field.default));
    switch (field.kind) {
      case "checkbox": return false;
      case "select": return field.options[0];
      case "strings": return field.required ? [""] : [];
      case "group": return field.required ? [newItem(field.fields)] : [];
      case "rows": return [];
      case "number": return undefined;
      default: return field.required ? "" : undefined;
    }
  }

  function newItem(fields) {
    const item = {};
    fields.forEach((f) => {
      const v = blank(f);
      if (v !== undefined) item[f.name] = v;
    });
    return item;
  }

  // Set or drop obj[field.name] the way the server stores it: optional fields left empty
  // are left out (unless they have a default, which the server would fill in anyway).
  function assign(obj, field, value) {
    const empty = value === undefined || value === "" || (typeof value === "number" && isNaN(value));
    if (empty && !field.required && field.default === undefined) delete obj[field.name];
    else obj[field.name] = empty && field.kind !== "number" ? "" : value;
    save();
  }

  // ------------------------------------------------------------------ fields

  function fieldControl(obj, field, onStructure) {
    const value = obj[field.name];
    const label = field.label + (field.required ? "" : " (optional)");
    const hint = field.hint ? el("small", { class: "rb-muted", text: field.hint }) : null;
    switch (field.kind) {
      case "text": {
        const input = el("input", {
          value: value === undefined ? "" : value,
          maxlength: field.maxlength,
          oninput: (e) => assign(obj, field, e.target.value),
        });
        return el("label", {}, [document.createTextNode(label + " "), input, hint]);
      }
      case "media":
        return mediaPicker(obj, field, label);
      case "textarea": {
        const area = el("textarea", {
          rows: 3,
          maxlength: field.maxlength,
          oninput: (e) => assign(obj, field, e.target.value),
        });
        area.value = value === undefined ? "" : value;
        return el("label", {}, [document.createTextNode(label + " "), area, hint]);
      }
      case "number": {
        const input = el("input", {
          type: "number",
          min: field.min,
          max: field.max,
          value: value === undefined ? "" : value,
          oninput: (e) => assign(obj, field, e.target.value === "" ? undefined : parseInt(e.target.value, 10)),
        });
        return el("label", {}, [document.createTextNode(label + " "), input]);
      }
      case "checkbox": {
        const box = el("input", { type: "checkbox", checked: !!value, onchange: (e) => assign(obj, field, e.target.checked) });
        return el("label", { class: "rb-check" }, [box, document.createTextNode(" " + field.label)]);
      }
      case "select": {
        const select = el("select", {
          onchange: (e) => assign(obj, field, field.options[parseInt(e.target.value, 10)]),
        }, field.options.map((o, i) => el("option", { value: String(i), selected: o === value, text: String(o) })));
        return el("label", {}, [document.createTextNode(label + " "), select]);
      }
      case "strings":
        return stringList(obj, field, label, hint, onStructure);
      case "group":
        return group(obj, field, label, onStructure);
      case "rows":
        return rows(obj, field, label, onStructure);
      default:
        return el("p", { class: "rb-error", text: "Unknown field kind: " + field.kind });
    }
  }

  // An image from the media library: type to filter, or click a thumbnail.
  const MAX_THUMBS = 12;
  const mediaUrl = (key) => "/media/" + encodeURIComponent(key);

  function mediaPicker(obj, field, label) {
    const input = el("input", { value: obj[field.name] || "", list: "rb-media-list", autocomplete: "off" });
    const preview = el("div", { class: "rb-media-preview" });
    const thumbs = el("div", { class: "rb-media-thumbs", role: "group", "aria-label": "Images from the media library" });

    function choose(key) {
      input.value = key;
      assign(obj, field, key);
      update();
    }

    function update() {
      const key = input.value.trim();
      const chosen = spec.media.find((m) => m.key === key);
      preview.replaceChildren(chosen
        ? el("img", { src: mediaUrl(chosen.key), alt: chosen.label, loading: "lazy" })
        : document.createTextNode(""));
      const query = chosen ? "" : key.toLowerCase();
      const matches = spec.media
        .filter((m) => !query || m.key.toLowerCase().includes(query) || m.label.toLowerCase().includes(query))
        .slice(0, MAX_THUMBS);
      thumbs.replaceChildren(...matches.map((m) => el("button", {
        type: "button",
        class: "rb-media-thumb" + (m.key === key ? " is-chosen" : ""),
        "aria-label": "Use image: " + m.label,
        "aria-pressed": m.key === key ? "true" : "false",
        onclick: () => choose(m.key),
      }, [el("img", { src: mediaUrl(m.key), alt: "", loading: "lazy" })])));
    }

    input.addEventListener("input", () => { assign(obj, field, input.value); update(); });
    update();
    const help = spec.media.length ? null : el("small", { class: "rb-muted", text: "Upload images under Media to pick them here." });
    return el("div", { class: "rb-media-field" }, [
      el("label", {}, [document.createTextNode(label + " "), input]), help, preview, thumbs,
    ]);
  }

  function stringList(obj, field, label, hint, onStructure) {
    const items = Array.isArray(obj[field.name]) ? obj[field.name] : (obj[field.name] = []);
    const list = el("ol", { class: "rb-block-list" });
    items.forEach((v, i) => {
      list.appendChild(el("li", {}, [
        el("input", { value: v, "aria-label": field.label + " " + (i + 1), oninput: (e) => { items[i] = e.target.value; save(); } }),
        button("✕", "Remove " + field.label.toLowerCase() + " " + (i + 1), () => { items.splice(i, 1); assign(obj, field, items); onStructure(); }),
      ]));
    });
    return el("fieldset", { class: "rb-block-field" }, [
      el("legend", { text: label }), hint, list,
      button("+ Add", "Add to " + field.label.toLowerCase(), () => { items.push(""); assign(obj, field, items); onStructure(); }),
    ]);
  }

  function group(obj, field, label, onStructure) {
    const items = Array.isArray(obj[field.name]) ? obj[field.name] : (obj[field.name] = []);
    const box = el("fieldset", { class: "rb-block-field" }, [el("legend", { text: label })]);
    items.forEach((item, i) => {
      const part = el("div", { class: "rb-block-item" }, [
        el("div", { class: "rb-block-item__head" }, [
          el("strong", { text: field.item + " " + (i + 1) }),
          button("✕", "Remove " + field.item + " " + (i + 1), () => { items.splice(i, 1); assign(obj, field, items); onStructure(); }),
        ]),
      ]);
      field.fields.forEach((f) => part.appendChild(fieldControl(item, f, onStructure)));
      box.appendChild(part);
    });
    box.appendChild(button("+ Add " + field.item, "Add " + field.item, () => { items.push(newItem(field.fields)); assign(obj, field, items); onStructure(); }));
    return box;
  }

  // Table rows: one input per header column.
  function rows(obj, field, label, onStructure) {
    const data = Array.isArray(obj[field.name]) ? obj[field.name] : (obj[field.name] = []);
    const width = Math.max(1, (obj.headers || []).length, ...data.map((r) => r.length));
    const box = el("fieldset", { class: "rb-block-field" }, [el("legend", { text: label })]);
    data.forEach((row, r) => {
      while (row.length < width) row.push("");
      const line = el("div", { class: "rb-block-row" });
      row.forEach((cell, c) => line.appendChild(el("input", {
        value: cell, "aria-label": "Row " + (r + 1) + ", column " + (c + 1),
        oninput: (e) => { row[c] = e.target.value; save(); },
      })));
      line.appendChild(button("✕", "Remove row " + (r + 1), () => { data.splice(r, 1); assign(obj, field, data); onStructure(); }));
      box.appendChild(line);
    });
    box.appendChild(button("+ Add row", "Add row", () => { data.push(new Array(width).fill("")); assign(obj, field, data); onStructure(); }));
    return box;
  }

  // ------------------------------------------------------------------ blocks

  function card(block, index) {
    const def = byType[block.type];
    const name = def ? def.label : block.type;
    const move = (to) => {
      const [b] = blocks.splice(index, 1);
      blocks.splice(to, 0, b);
      save();
      render(to);
    };
    // Drag by the handle to reorder; keyboard and screen reader users have the arrow buttons.
    const handle = el("span", {
      class: "rb-block__handle",
      draggable: "true",
      title: "Drag to move",
      "aria-hidden": "true",
      text: "⠿",
      ondragstart: (e) => {
        dragFrom = index;
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", "block " + (index + 1));
        e.dataTransfer.setDragImage(section, 16, 16);
        section.classList.add("is-dragging");
      },
      ondragend: () => {
        dragFrom = null;
        section.classList.remove("is-dragging");
        markDrop(null);
      },
    });
    const head = el("div", { class: "rb-block__head" }, [
      el("span", { class: "rb-block__title" }, [handle, el("strong", { text: (index + 1) + ". " + name })]),
      el("span", { class: "rb-block__tools" }, [
        button("+", "Add a block below block " + (index + 1), () => { insertAfter = index; render(); }),
        button("↑", "Move block " + (index + 1) + " up", () => move(index - 1), { disabled: index === 0 }),
        button("↓", "Move block " + (index + 1) + " down", () => move(index + 1), { disabled: index === blocks.length - 1 }),
        button("✕", "Remove block " + (index + 1), () => { blocks.splice(index, 1); save(); render(Math.min(index, blocks.length - 1)); }),
      ]),
    ]);
    const body = el("div", { class: "rb-block__body" });
    if (!def) {
      body.appendChild(el("p", { class: "rb-muted", text: "Unknown block type; edit it as JSON." }));
    } else {
      if (def.help) body.appendChild(el("p", { class: "rb-muted", text: def.help }));
      def.fields.forEach((f) => body.appendChild(fieldControl(block, f, () => render(index))));
    }
    const section = el("section", { class: "rb-block", "data-index": String(index), tabindex: "-1", "aria-label": "Block " + (index + 1) + ": " + name }, [head, body]);
    return section;
  }

  // Where a dragged block would land: before the first card whose middle is below the
  // pointer, or at the end. Returns the position in the list as it is now.
  function dropPosition(y) {
    const cards = root.querySelectorAll("section.rb-block");
    for (const c of cards) {
      const box = c.getBoundingClientRect();
      if (y < box.top + box.height / 2) return parseInt(c.dataset.index, 10);
    }
    return cards.length;
  }

  function markDrop(position) {
    root.querySelectorAll(".rb-drop-before, .rb-drop-after").forEach((c) => c.classList.remove("rb-drop-before", "rb-drop-after"));
    if (position === null) return;
    const cards = root.querySelectorAll("section.rb-block");
    if (position < cards.length) cards[position].classList.add("rb-drop-before");
    else if (cards.length) cards[cards.length - 1].classList.add("rb-drop-after");
  }

  root.addEventListener("dragover", (e) => {
    if (dragFrom === null) return; // not one of our cards (e.g. a file or text): not a drop target
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    markDrop(dropPosition(e.clientY));
  });
  root.addEventListener("dragleave", (e) => {
    if (!root.contains(e.relatedTarget)) markDrop(null);
  });
  root.addEventListener("drop", (e) => {
    if (dragFrom === null) return;
    e.preventDefault();
    const from = dragFrom;
    let to = dropPosition(e.clientY);
    if (to > from) to -= 1; // the dragged card leaves a gap above the drop point
    dragFrom = null;
    markDrop(null);
    if (to === from) return;
    const [b] = blocks.splice(from, 1);
    blocks.splice(to, 0, b);
    insertAfter = null;
    save();
    render(to);
  });

  // A type picker that adds a block at ``position``. The one under a card can be cancelled.
  function addRow(position, inline) {
    const pick = el("select", {
      "aria-label": inline ? "Type of block to add below block " + position : "Block type",
    }, spec.blocks.map((b) => el("option", { value: b.type, text: b.label })));
    const row = el("div", { class: "rb-block-add" + (inline ? " rb-block-add--inline" : "") }, [
      pick,
      button("Add block", inline ? "Add block below block " + position : "Add block", () => {
        const def = byType[pick.value];
        blocks.splice(position, 0, Object.assign({ type: def.type }, newItem(def.fields)));
        insertAfter = null;
        save();
        render(position);
      }, { class: "rb-btn" }),
      inline ? button("Cancel", "Cancel adding a block", () => { insertAfter = null; render(position - 1); }) : null,
    ]);
    return { row: row, pick: pick };
  }

  function render(focusIndex) {
    root.replaceChildren();
    if (!blocks.length) root.appendChild(el("p", { class: "rb-muted", text: "No blocks yet. Add one below." }));
    let inlinePick = null;
    blocks.forEach((b, i) => {
      root.appendChild(card(b, i));
      if (insertAfter === i) {
        const add = addRow(i + 1, true);
        root.appendChild(add.row);
        inlinePick = add.pick;
      }
    });
    root.appendChild(addRow(blocks.length, false).row);
    if (inlinePick) {
      inlinePick.focus();
    } else if (focusIndex !== undefined && focusIndex >= 0) {
      const target = root.querySelector('[data-index="' + focusIndex + '"]');
      if (target) target.focus();
    }
  }

  // ------------------------------------------------------------------ JSON toggle

  const status = el("p", { class: "rb-error", role: "alert", hidden: true });
  const toggle = el("button", { type: "button", class: "rb-btn rb-btn--small", "aria-pressed": "false" });
  const wrapper = textarea.closest("label") || textarea;

  function showVisual() {
    let parsed;
    try {
      parsed = JSON.parse(textarea.value.trim() || "[]");
      if (!Array.isArray(parsed)) throw new Error("The blocks must be a JSON list.");
    } catch (err) {
      status.textContent = "The JSON isn't valid, so it can't be shown visually: " + err.message;
      status.hidden = false;
      return false;
    }
    status.hidden = true;
    blocks = parsed;
    wrapper.hidden = true;
    root.hidden = false;
    toggle.textContent = "Edit as JSON";
    toggle.setAttribute("aria-pressed", "false");
    render();
    return true;
  }

  function showJSON() {
    save();
    wrapper.hidden = false;
    root.hidden = true;
    toggle.textContent = "Edit visually";
    toggle.setAttribute("aria-pressed", "true");
  }

  // ------------------------------------------------------------------ live preview
  // The server validates as a save would and renders with the public site's templates:
  // the blocks alone, or the whole page (header, title, layout) from the form as it is now.
  // Both are view-only (the blocks are inert; the page is a sandboxed frame without
  // scripts), and nothing is stored.

  const PREVIEW_DELAY = 400;
  const form = textarea.form;
  const pageUrl = root.dataset.previewPage;
  let previewTimer = null;
  let previewSeq = 0;
  let previewOpen = true;
  let previewMode = "blocks"; // or "page"
  const previewBody = el("div", { class: "rb-preview__body", inert: true });
  // allow-same-origin without allow-scripts: nothing in the page runs, and the editor can
  // keep its scroll position. Links open in a new tab, outside the sandbox.
  const previewFrame = el("iframe", {
    class: "rb-preview__frame",
    title: "Full-page preview",
    sandbox: "allow-same-origin allow-popups allow-popups-to-escape-sandbox",
    hidden: true,
  });
  const previewNote = el("p", { class: "rb-muted", "aria-live": "polite", hidden: true });
  function note(text) {
    previewNote.textContent = text;
    previewNote.hidden = !previewOpen || !text;
  }
  const view = () => (previewMode === "page" ? previewFrame : previewBody);
  function showView() {
    previewBody.hidden = !previewOpen || previewMode !== "blocks";
    previewFrame.hidden = !previewOpen || previewMode !== "page";
    note(previewNote.textContent);
  }
  const previewToggle = button("Hide", "Hide the live preview", () => {
    previewOpen = !previewOpen;
    previewToggle.textContent = previewOpen ? "Hide" : "Show";
    previewToggle.setAttribute("aria-label", (previewOpen ? "Hide" : "Show") + " the live preview");
    showView();
    if (previewOpen) refreshPreview();
  });
  const modeButtons = [["blocks", "Blocks"], ["page", "Full page"]].map(([mode, label]) =>
    button(label, null, () => {
      if (previewMode === mode) return;
      previewMode = mode;
      modeButtons.forEach((b, i) => b.setAttribute("aria-pressed", String(i === (mode === "page" ? 1 : 0))));
      showView();
      refreshPreview();
    }, { "aria-pressed": mode === "blocks" ? "true" : "false" }));
  const preview = el("section", { class: "rb-preview", "aria-label": "Live preview" }, [
    el("div", { class: "rb-preview__head" }, [
      el("strong", { text: "Live preview" }),
      el("small", { class: "rb-muted", text: "How it will look. Saved only when you save." }),
      el("span", { class: "rb-preview__modes", role: "group", "aria-label": "Preview" }, modeButtons),
      previewToggle,
    ]),
    previewNote,
    previewBody,
    previewFrame,
  ]);

  function csrfToken() {
    try {
      return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRF-Token"] || "";
    } catch (e) {
      return "";
    }
  }

  function schedulePreview() {
    if (!previewOpen) return;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(refreshPreview, PREVIEW_DELAY);
  }

  function request(list) {
    const headers = { "X-CSRF-Token": csrfToken() };
    if (previewMode === "page" && pageUrl && form) {
      return fetch(pageUrl, {
        method: "POST", credentials: "same-origin", headers: headers,
        body: new URLSearchParams(new FormData(form)),
      });
    }
    headers["Content-Type"] = "application/json";
    return fetch("/admin/content/preview-blocks", {
      method: "POST", credentials: "same-origin", headers: headers,
      body: JSON.stringify({ blocks: list }),
    });
  }

  // The page without its scripts (they wouldn't run in the sandbox anyway).
  function showPage(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    doc.querySelectorAll("script").forEach((s) => s.remove());
    doc.head.prepend(el("base", { target: "_blank" }));
    let y = 0;
    try { y = previewFrame.contentWindow.scrollY; } catch (e) { /* not loaded yet */ }
    previewFrame.addEventListener("load", () => {
      try { previewFrame.contentWindow.scrollTo(0, y); } catch (e) { /* ignore */ }
    }, { once: true });
    previewFrame.srcdoc = "<!doctype html>\n" + doc.documentElement.outerHTML;
  }

  async function refreshPreview() {
    const seq = ++previewSeq;
    const target = view();
    let list;
    try {
      list = JSON.parse(textarea.value.trim() || "[]");
    } catch (e) {
      note("Fix the JSON to update the preview.");
      target.classList.add("is-stale");
      return;
    }
    let data;
    try {
      data = await (await request(list)).json();
    } catch (e) {
      data = { errors: ["The preview couldn't be loaded."] };
    }
    if (seq !== previewSeq) return; // a newer edit (or a switch of view) since
    if (data.errors) {
      note("Not updated until this is fixed: " + data.errors.join(" · "));
      target.classList.add("is-stale");
      return;
    }
    note("");
    previewBody.classList.remove("is-stale");
    previewFrame.classList.remove("is-stale");
    if (target === previewFrame) {
      showPage(data.html);
    } else {
      const doc = new DOMParser().parseFromString(data.html, "text/html");
      previewBody.replaceChildren(...doc.body.childNodes);
    }
  }

  textarea.addEventListener("input", schedulePreview);
  // The full page also shows the title, summary and the other fields.
  if (form) {
    ["input", "change"].forEach((type) => form.addEventListener(type, (e) => {
      if (previewMode === "page" && e.target !== textarea) schedulePreview();
    }));
  }

  toggle.addEventListener("click", () => (root.hidden ? showVisual() : showJSON()));
  const datalist = el("datalist", { id: "rb-media-list" },
    spec.media.map((m) => el("option", { value: m.key, text: m.label })));
  root.parentNode.insertBefore(el("div", { class: "rb-block-toolbar" }, [
    el("strong", { text: "Blocks" }), toggle,
  ]), root);
  root.parentNode.insertBefore(status, root);
  root.parentNode.appendChild(datalist);
  root.parentNode.insertBefore(preview, root.nextSibling);
  if (!showVisual()) showJSON();
  refreshPreview();
})();
