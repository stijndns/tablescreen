// generic.js — The view for any tab without a better one: shows the public
// state as a readable tree. Works for every state version.

function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function tree(value) {
  if (Array.isArray(value)) {
    if (!value.length) return el("span", "gv-empty", "none");
    const list = el("ol", "gv-list");
    list.replaceChildren(...value.map((item) => {
      const entry = el("li");
      entry.append(tree(item));
      return entry;
    }));
    return list;
  }
  if (value !== null && typeof value === "object") {
    const table = el("dl", "gv-object");
    for (const [key, item] of Object.entries(value)) {
      const term = el("dt", "", key.replaceAll("_", " "));
      const description = el("dd");
      description.append(tree(item));
      table.append(term, description);
    }
    return table;
  }
  if (value === null || value === undefined) return el("span", "gv-empty", "—");
  return el("span", "gv-value", String(value));
}

export function mount(element) {
  const root = el("div", "gv");
  element.append(root);
  return {
    update(state) {
      root.replaceChildren(tree(state));
    },
    unmount() {
      root.remove();
    },
  };
}
