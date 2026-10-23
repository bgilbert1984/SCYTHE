import {filterEntries, paletteEntries} from "./commandPalette.js";

/**
 * Ctrl+K (or Cmd+K) opens a dialog that jumps to a hub action, a running instance's view, or a mission the hub has loaded.
 * It only navigates or calls an action the page already has; it never fetches. The entries are rebuilt from `getState()` each
 * time it opens, so it shows what the hub knows now. `getState()` returns {instances, missions}; `actions` is [{id, label, run}].
 */
export function mountPalette({getState, actions = [], document: doc = globalThis.document, navigate = (href) => globalThis.location.assign(href)} = {}) {
  const el = (tag, className, text) => {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const overlay = el("div", "palette");
  overlay.hidden = true;
  const box = el("div", "palette__box");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-label", "Go to");
  const input = el("input", "palette__input");
  input.type = "text";
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-expanded", "true");
  input.setAttribute("aria-controls", "palette-list");
  input.setAttribute("aria-autocomplete", "list");
  input.placeholder = "Go to an instance, a view, a mission or an action";
  input.autocomplete = "off";
  const list = el("ul", "palette__list");
  list.id = "palette-list";
  list.setAttribute("role", "listbox");
  const note = el("div", "palette__note");
  box.append(input, list, note);
  overlay.append(box);
  doc.body.append(overlay);

  let entries = [], skipped = 0, shown = [], active = 0, opener = null;

  function draw() {
    shown = filterEntries(entries, input.value);
    active = Math.min(active, Math.max(0, shown.length - 1));
    list.textContent = "";
    shown.forEach((entry, index) => {
      const row = el("li", "palette__row");
      row.id = `palette-row-${index}`;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(index === active));
      row.dataset.entry = entry.id;
      row.append(el("span", "palette__label", entry.label), el("span", "palette__hint", entry.hint));
      row.addEventListener("mousedown", (event) => { event.preventDefault(); choose(entry); });
      list.append(row);
    });
    input.setAttribute("aria-activedescendant", shown.length ? `palette-row-${active}` : "");
    note.textContent = !shown.length ? "Nothing matches."
      : skipped ? `${skipped} instance${skipped === 1 ? " is" : "s are"} not listed (not running, or no usable id).` : "";
  }

  function choose(entry) {
    close();
    if (entry.run) entry.run();
    else if (entry.href) navigate(entry.href);
  }

  function open() {
    if (!overlay.hidden) return;
    opener = doc.activeElement;
    ({entries, skipped} = paletteEntries({...(getState?.() ?? {}), actions}));
    input.value = "";
    active = 0;
    overlay.hidden = false;
    draw();
    input.focus();
  }

  function close() {
    if (overlay.hidden) return;
    overlay.hidden = true;
    opener?.focus?.();
  }

  input.addEventListener("input", () => { active = 0; draw(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (shown.length) active = (active + (event.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length;
      draw();
      list.children[active]?.scrollIntoView?.({block: "nearest"});
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (shown[active]) choose(shown[active]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "Tab") {
      event.preventDefault();   // the dialog holds one control
    }
  });
  overlay.addEventListener("mousedown", (event) => { if (event.target === overlay) close(); });
  doc.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (overlay.hidden) open(); else close();
    }
  });
  return {open, close, element: overlay};
}
