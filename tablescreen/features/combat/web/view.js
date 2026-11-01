// view.js — The combat tab's default view in the player webapp: the combat
// screen as the table shows it. Gets the combat/1 public state
// (features/combat/api.py) and never shows more than it holds.
//
// A webapp view module: mount(element, ctx) → {update(state), unmount()},
// ctx = {user, role, character}. Styles use the webapp's CSS variables.

export const schemas = ["combat/1"];

const HEALTH = {
  green: "Healthy", yellow: "Bloodied", orange: "Wounded",
  red: "Near Death", dead: "Defeated",
};
const TYPES = {pc: "PC", npc: "NPC", monster: "Monster"};

const CSS = `
.cv-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px;
  margin-bottom: 12px; }
.cv-round { font-size: 1.15rem; font-weight: 700; color: var(--accent); }
.cv-turn { color: var(--muted); }
.cv-empty { color: var(--muted); margin: 0; }
.cv-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.cv-row { display: grid; grid-template-columns: 2.6em 1fr auto; gap: 2px 10px;
  align-items: center; padding: 8px 10px; border: 1px solid var(--border);
  border-left: 4px solid var(--cv-accent, var(--border)); border-radius: 8px;
  background: var(--bg); }
.cv-row.pc { --cv-accent: #4493f8; }
.cv-row.npc { --cv-accent: #bc8cff; }
.cv-row.monster { --cv-accent: #ff7b72; }
.cv-row.current { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent);
  background: var(--card); }
.cv-row.dim { opacity: 0.5; --cv-accent: var(--border); }
.cv-init { grid-row: span 2; font-size: 1.2rem; font-weight: 700; text-align: center;
  color: var(--muted); }
.cv-row.current .cv-init { color: var(--accent); }
.cv-name { font-weight: 600; overflow-wrap: anywhere; }
.cv-tag { display: inline-block; margin-left: 6px; padding: 0 6px; font-size: 0.75rem;
  font-weight: 600; border: 1px solid currentColor; border-radius: 999px;
  color: var(--muted); vertical-align: 1px; }
.cv-tag.you { color: var(--accent); }
.cv-hp { font-weight: 700; text-align: right; white-space: nowrap; }
.cv-hp.green { color: #3fb950; } .cv-hp.yellow { color: #d29922; }
.cv-hp.orange { color: #e3652b; } .cv-hp.red { color: #f85149; }
.cv-hp.dead { color: var(--muted); }
.cv-extra { grid-column: 2 / 4; display: flex; flex-wrap: wrap; gap: 4px 12px;
  font-size: 0.85rem; color: var(--muted); }
.cv-conditions { color: var(--accent); }
.cv-waiting { margin: 0 0 12px; color: var(--accent); }
`;

let sheet = null;

function useStyles() {
  if (!sheet) {
    sheet = new CSSStyleSheet();
    sheet.replaceSync(CSS);
  }
  if (!document.adoptedStyleSheets.includes(sheet)) {
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  }
}

function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function header(state) {
  const head = el("div", "cv-head");
  if (state.active) {
    head.append(el("span", "cv-round", `Round ${state.round}`));
    if (state.current) head.append(el("span", "cv-turn", `${state.current}'s turn`));
  } else {
    head.append(el("span", "cv-round", "Rolling initiative"));
  }
  return head;
}

function row(c, state, ctx) {
  const greyed = !c.revealed || c.pending || c.left
    || ["dead", "dying", "incapacitated"].includes(c.status);
  const item = el("li", `cv-row ${c.type}`);
  if (greyed) item.classList.add("dim");
  if (state.active && c.name === state.current) item.classList.add("current");

  item.append(el("span", "cv-init", c.initiative ?? "–"));

  const name = el("span", "cv-name", c.name);
  const tags = [TYPES[c.type] || c.type];
  if (c.status !== "active") tags.push(c.status);
  if (c.pending) tags.push("next round");
  if (c.left) tags.push("left");
  for (const tag of tags) name.append(el("span", "cv-tag", tag));
  if (ctx.character && c.name === ctx.character) name.append(el("span", "cv-tag you", "you"));
  item.append(name);

  if (c.hp) {
    const temp = c.hp.temp ? ` +${c.hp.temp}` : "";
    item.append(el("span", "cv-hp", `${c.hp.current}/${c.hp.max} HP${temp}`));
  } else if (c.health) {
    item.append(el("span", `cv-hp ${c.health}`, HEALTH[c.health] || c.health));
  } else {
    item.append(el("span"));
  }

  const extra = [];
  if (c.reaction) extra.push(el("span", "", `Reaction ${c.reaction.current}/${c.reaction.max}`));
  if (c.legendary) {
    extra.push(el("span", "", `Legendary ${c.legendary.current}/${c.legendary.max}`));
  }
  if (c.conditions && c.conditions.length) {
    extra.push(el("span", "cv-conditions", c.conditions.join(" · ")));
  }
  if (extra.length) {
    const line = el("div", "cv-extra");
    line.append(...extra);
    item.append(line);
  }
  return item;
}

function render(state, ctx) {
  if (!state.active && !state.rolling) return [el("p", "cv-empty", "No combat running.")];
  const parts = [header(state)];
  if (state.rolling && !state.active) {
    parts.push(el("p", "cv-waiting", state.waiting_for.length
      ? `Waiting for initiative: ${state.waiting_for.join(", ")}`
      : "Everyone's initiative is in."));
  }
  const list = el("ol", "cv-list");
  list.append(...state.combatants.map((c) => row(c, state, ctx)));
  parts.push(list);
  return parts;
}

export function mount(element, ctx) {
  useStyles();
  const root = el("div", "cv");
  element.append(root);
  return {
    update(state) {
      root.replaceChildren(...render(state, ctx));
    },
    unmount() {
      root.remove();
    },
  };
}
