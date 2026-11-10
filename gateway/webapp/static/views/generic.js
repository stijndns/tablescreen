// generic.js — The view for any tab without a better one: shows the public
// state as a readable tree, and a plain form per action the app offers
// (built from the action's field descriptions). Works for every state
// version.

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

// One field: a number, a choice or a line of text.
function field(name, spec) {
  const label = el("label", "", name.replaceAll("_", " ") + (spec.required ? "" : " (optional)"));
  let control;
  if (spec.choices) {
    control = el("select");
    const options = spec.required ? [] : [["", "—"]];
    for (const choice of spec.choices) options.push([choice, choice]);
    control.replaceChildren(...options.map(([value, text]) => {
      const option = el("option", "", text);
      option.value = value;
      return option;
    }));
    control.value = options[0][0];
  } else {
    control = el("input");
    if (spec.kind === "int") {
      control.type = "number";
      control.setAttribute("min", String(spec.minimum));
      control.setAttribute("max", String(spec.maximum));
    } else {
      control.type = "text";
      control.setAttribute("maxlength", String(spec.max_length));
    }
  }
  label.append(control);
  const read = () => {
    const text = control.value.trim();
    if (!text) return undefined;
    return spec.kind === "int" ? Number.parseInt(text, 10) : text;
  };
  return {label, read, clear: () => { if (!spec.choices) control.value = ""; }};
}

function actionForm(action, ctx) {
  const form = el("form", "card gv-action");
  form.append(el("h3", "", action.name.replaceAll("_", " ")));
  const fields = Object.entries(action.fields || {}).map(([name, spec]) => [name, field(name, spec)]);
  for (const [, f] of fields) form.append(f.label);
  const send = el("button", "", "Send");
  send.type = "submit";
  const out = el("p", "gv-result");
  out.hidden = true;
  form.append(send, out);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const args = {};
    for (const [name, f] of fields) {
      const value = f.read();
      if (value !== undefined) args[name] = value;
    }
    send.disabled = true;
    const reply = await ctx.send(action.name, args);
    send.disabled = false;
    out.textContent = reply.message || (reply.ok ? "Done." : "That didn't work.");
    out.className = `gv-result ${reply.ok ? "success" : "error"}`;
    out.hidden = false;
    if (reply.ok) for (const [, f] of fields) f.clear();
  });
  return form;
}

export function mount(element, ctx) {
  const root = el("div", "gv");
  const state = el("div");
  const actions = el("div", "gv-actions");
  root.append(state, actions);
  element.append(root);
  let shownFor = null;

  // The forms depend on who is logged in, not on the state: built again
  // only when that changes (so typing isn't lost on an update).
  function drawActions() {
    const who = `${ctx.user}|${ctx.role}`;
    if (who === shownFor) return;
    shownFor = who;
    const allowed = ctx.user
      ? (ctx.actions || []).filter((a) => ctx.role === "dm" || a.permission === "player")
      : [];
    actions.replaceChildren(...allowed.map((a) => actionForm(a, ctx)));
  }

  return {
    update(data) {
      state.replaceChildren(tree(data));
      drawActions();
    },
    unmount() {
      root.remove();
    },
  };
}
