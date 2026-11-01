// combat.js — The webapp's own layout for the combat tab: compact, one line
// per combatant. Overrides the feature's default view (unless [api.views]
// says otherwise) for the state versions listed in `schemas`; for any other
// the tab falls back to the feature's view.

export const schemas = ["combat/1"];

const HEALTH = {
  green: "Healthy", yellow: "Bloodied", orange: "Wounded",
  red: "Near Death", dead: "Defeated",
};

function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function line(c, state, ctx) {
  const item = el("li", "cc-row");
  if (!c.revealed || c.pending || c.left || c.status !== "active") item.classList.add("dim");
  if (state.active && c.name === state.current) item.classList.add("current");
  if (ctx.character && c.name === ctx.character) item.classList.add("you");
  item.append(el("span", "cc-init", c.initiative ?? "–"), el("span", "cc-name", c.name));
  let detail = "";
  if (c.hp) detail = `${c.hp.current}/${c.hp.max}${c.hp.temp ? ` +${c.hp.temp}` : ""}`;
  else if (c.health) detail = HEALTH[c.health] || c.health;
  if (c.conditions && c.conditions.length) detail += ` · ${c.conditions.join(", ")}`;
  item.append(el("span", "cc-detail", detail));
  return item;
}

export function mount(element, ctx) {
  const root = el("div", "cc");
  element.append(root);
  return {
    update(state) {
      if (!state.active && !state.rolling) {
        root.replaceChildren(el("p", "muted", "No combat."));
        return;
      }
      const title = state.active
        ? `Round ${state.round}${state.current ? ` · ${state.current}` : ""}`
        : `Initiative: waiting for ${state.waiting_for.join(", ") || "nobody"}`;
      const list = el("ol", "cc-list");
      list.append(...state.combatants.map((c) => line(c, state, ctx)));
      root.replaceChildren(el("p", "cc-title", title), list);
    },
    unmount() {
      root.remove();
    },
  };
}
