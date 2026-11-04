// view.js — The combat tab's default view in the player webapp: the combat
// screen as the table shows it, plus a player's actions. Gets the combat/1
// public state (features/combat/api.py) and never shows more than it holds.
//
// A webapp view module: mount(element, ctx) → {update(state), unmount()},
// ctx = {user, role, character, actions, send(action, args)}. Styles use the
// webapp's CSS variables (--health-*, --type-* when the webapp sets them, with
// fallbacks for a dark background).
//
// Actions (checked again by the app, which has the last word):
//   * tap a combatant: damage, heal, add or remove a condition; out of turn
//     the player must pick what it uses (reaction, legendary, nothing);
//   * "End your turn" on your own turn; "Your initiative" while it's missing;
//   * the dm picks who acts ("Acting as").
// Rows and controls are kept and updated in place, so a state update while
// a player types doesn't take their input or focus away.

export const schemas = ["combat/1"];

const HEALTH = {
  green: "Healthy", yellow: "Bloodied", orange: "Wounded",
  red: "Near Death", dead: "Defeated",
};
const TYPES = {pc: "PC", npc: "NPC", monster: "Monster"};
const KINDS = [["damage", "Damage"], ["heal", "Heal"],
               ["condition", "Add condition"], ["remove_condition", "Remove condition"]];
const DAMAGE_TYPES = ["acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic",
                      "piercing", "poison", "psychic", "radiant", "slashing", "thunder"];
const CONDITIONS = ["blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled",
                    "incapacitated", "invisible", "paralyzed", "petrified", "poisoned", "prone",
                    "restrained", "stunned", "unconscious"];
const RESULT_MS = 8000;     // how long the result of a top-bar action stays

const CSS = `
.cv-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px;
  margin-bottom: 12px; }
.cv-round { font-size: 1.15rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--accent); }
.cv-turn { font-weight: 700; color: var(--text); }
.cv-empty { color: var(--muted); margin: 0; }
.cv-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.cv-row { display: grid; grid-template-columns: 2.6em 1fr auto; gap: 2px 10px;
  align-items: center; padding: 8px 10px; border: 1px solid var(--border);
  border-left: 4px solid var(--cv-accent, var(--border)); border-radius: 8px;
  background: var(--bg); }
.cv-row.pc { --cv-accent: var(--type-pc, #4493f8); }
.cv-row.npc { --cv-accent: var(--type-npc, #bc8cff); }
.cv-row.monster { --cv-accent: var(--type-monster, #ff7b72); }
.cv-row.current { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent);
  background: var(--card); }
.cv-row.dim { opacity: 0.62; --cv-accent: var(--border); }
.cv-row.tappable { cursor: pointer; }
.cv-row.open { border-color: var(--text); }
.cv-init { grid-row: span 2; font-size: 1.2rem; font-weight: 700; text-align: center;
  color: var(--muted); }
.cv-row.current .cv-init { color: var(--accent); }
.cv-name { font-weight: 700; overflow-wrap: anywhere; }
.cv-tag { display: inline-block; margin-left: 6px; padding: 0 6px; font-size: 0.75rem;
  font-weight: 700; border: 1px solid currentColor; border-radius: 999px;
  color: var(--muted); vertical-align: 1px; }
.cv-tag.you { color: var(--accent); }
.cv-hp { font-weight: 700; text-align: right; white-space: nowrap; }
.cv-hp.green { color: var(--health-green, #3fb950); }
.cv-hp.yellow { color: var(--health-yellow, #d29922); }
.cv-hp.orange { color: var(--health-orange, #e3652b); }
.cv-hp.red { color: var(--health-red, #f85149); }
.cv-hp.dead { color: var(--muted); }
.cv-extra { grid-column: 2 / 4; display: flex; flex-wrap: wrap; gap: 4px 12px;
  font-size: 0.85rem; font-weight: 700; color: var(--muted); }
.cv-conditions { color: var(--accent); }
.cv-waiting { margin: 0 0 12px; font-weight: 700; color: var(--accent); }
.cv-controls { display: grid; gap: 8px; margin-bottom: 12px; }
.cv-controls:empty { display: none; }
.cv-line { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0; }
.cv-line input { width: 6em; }
.cv-line select { padding: 8px; font: inherit; color: var(--text); background: var(--bg);
  border: 1px solid var(--border); border-radius: 8px; }
.cv-note { margin: 0; font-weight: 600; color: var(--muted); }
.cv-result { margin: 0; font-weight: 700; }
.cv-result.ok { color: var(--success); } .cv-result.err { color: var(--error); }
.cv-panel { list-style: none; padding: 12px; border: 1px solid var(--text);
  border-radius: 8px; background: var(--card); }
.cv-panel-form { display: grid; gap: 10px; }
.cv-panel-head { display: flex; justify-content: space-between; align-items: center;
  gap: 8px; margin: 0; font-weight: 700; }
.cv-seg { display: flex; flex-wrap: wrap; gap: 6px; }
.cv-seg button { color: var(--text); background: transparent; border-color: var(--border);
  padding: 8px 12px; }
.cv-seg button.on { color: var(--accent-text); background: var(--accent);
  border-color: var(--accent); }
.cv-fields { display: flex; flex-wrap: wrap; gap: 8px; }
.cv-fields input { flex: 1 1 8em; }
.cv-fields select { flex: 1 1 8em; padding: 10px 12px; font: inherit; color: var(--text);
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px; }
.cv-uses-label { margin: 0; color: var(--accent); font-weight: 700; }
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

function button(text, className, onClick, type = "button") {
  const element = el("button", className, text);
  element.type = type;
  if (onClick) element.addEventListener("click", onClick);
  return element;
}

function input(type, placeholder, extra = {}) {
  const element = el("input");
  element.type = type;
  element.placeholder = placeholder;
  for (const [key, value] of Object.entries(extra)) element.setAttribute(key, value);
  return element;
}

// One datalist per page for each suggestion list.
function suggestions(id, values) {
  if (document.getElementById(id)) return;
  const list = el("datalist");
  list.id = id;
  list.append(...values.map((value) => {
    const option = el("option");
    option.value = value;
    return option;
  }));
  document.body.append(list);
}

// Buttons of which one is on; onPick(value).
function segmented(choices, onPick) {
  const box = el("div", "cv-seg");
  const buttons = new Map();
  let value = null;
  const pick = (v) => {
    value = v;
    for (const [key, b] of buttons) b.classList.toggle("on", key === v);
  };
  const set = (pairs) => {
    buttons.clear();
    box.replaceChildren(...pairs.map(([key, label]) => {
      const b = button(label, "", () => {
        pick(key);
        onPick(key);
      });
      buttons.set(key, b);
      return b;
    }));
    if (!buttons.has(value)) value = null;
    pick(value);
  };
  set(choices);
  return {element: box, set, pick, get value() { return value; }};
}

// Put `nodes` in `parent` in this order, moving as little as possible
// (moving the element that has focus would close a phone's keyboard).
function arrange(parent, nodes) {
  nodes.forEach((node, i) => {
    if (parent.children[i] !== node) parent.insertBefore(node, parent.children[i] || null);
  });
  while (parent.children.length > nodes.length) parent.children[nodes.length].remove();
}

function fillRow(item, c, state, ctx, tappable, open) {
  const greyed = !c.revealed || c.pending || c.left
    || ["dead", "dying", "incapacitated"].includes(c.status);
  item.className = `cv-row ${c.type}`;
  if (greyed) item.classList.add("dim");
  if (state.active && c.name === state.current) item.classList.add("current");
  if (tappable) item.classList.add("tappable");
  if (open) item.classList.add("open");

  const parts = [el("span", "cv-init", c.initiative ?? "–")];
  const name = el("span", "cv-name", c.name);
  const tags = [TYPES[c.type] || c.type];
  if (c.status !== "active") tags.push(c.status);
  if (c.pending) tags.push("next round");
  if (c.left) tags.push("left");
  for (const tag of tags) name.append(el("span", "cv-tag", tag));
  if (ctx.character && c.name === ctx.character) name.append(el("span", "cv-tag you", "you"));
  parts.push(name);

  if (c.hp) {
    const temp = c.hp.temp ? ` +${c.hp.temp}` : "";
    parts.push(el("span", "cv-hp", `${c.hp.current}/${c.hp.max} HP${temp}`));
  } else if (c.health) {
    parts.push(el("span", `cv-hp ${c.health}`, HEALTH[c.health] || c.health));
  } else {
    parts.push(el("span"));
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
    parts.push(line);
  }
  item.replaceChildren(...parts);
}

export function mount(element, ctx) {
  useStyles();
  suggestions("cv-damage-types", DAMAGE_TYPES);
  suggestions("cv-condition-names", CONDITIONS);
  const offered = new Set((ctx.actions || []).map((a) => a.name));

  let state = null;
  let chosenActor = null;     // the dm's pick; null: whoever's turn it is
  let panel = null;           // the open action panel
  let resultTimer = null;
  const rows = new Map();     // name → <li>

  const root = el("div", "cv");
  const controls = el("div", "cv-controls");
  const empty = el("p", "cv-empty", "No combat running.");
  const head = el("div", "cv-head");
  const waiting = el("p", "cv-waiting");
  const list = el("ol", "cv-list");
  root.append(controls, empty, head, waiting, list);
  element.append(root);

  // ── Who acts ──
  const isDm = () => ctx.role === "dm";
  const names = () => state.combatants.map((c) => c.name);
  function actorName() {
    if (!isDm()) return ctx.character;
    if (chosenActor && names().includes(chosenActor)) return chosenActor;
    return state.current || names()[0] || null;
  }
  const actorRow = () => state.combatants.find((c) => c.name === actorName());
  const myTurn = () => state.active && state.current === actorName();
  const withActor = (args) => (isDm() ? {...args, actor: actorName()} : args);
  // Why this account can't act, or null.
  function cantAct() {
    if (!ctx.user) return "";                          // a guest: no controls
    if (!isDm() && !ctx.character) {
      return "Your account isn't linked to a character yet: ask the game master.";
    }
    if (!actorRow()) return isDm() ? "" : `${ctx.character} isn't in this combat.`;
    return null;
  }

  // ── The top controls (kept, shown or hidden) ──
  const result = el("p", "cv-result");
  const note = el("p", "cv-note");
  const actorLine = el("label", "cv-line", "Acting as");
  const actorSelect = el("select");
  actorSelect.addEventListener("change", () => {
    chosenActor = actorSelect.value;
    draw();
  });
  actorLine.append(actorSelect);
  const endTurn = button("End your turn", "", async () => {
    show(result, await send("end_turn", {}, endTurn));
  });
  const initiative = el("form", "cv-line");
  const initiativeLabel = el("span", "", "Your initiative");
  const initiativeValue = input("number", "Roll", {min: "-10", max: "50", inputmode: "numeric"});
  const initiativeSend = button("Send", "", null, "submit");
  initiative.append(initiativeLabel, initiativeValue, initiativeSend);
  initiative.addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = Number.parseInt(initiativeValue.value, 10);
    if (Number.isNaN(value)) {
      show(result, {ok: false, message: "Type the number you rolled."});
      return;
    }
    const reply = await send("initiative", withActor({value}), initiativeSend);
    if (reply.ok) initiativeValue.value = "";
    show(result, reply);
  });

  async function send(action, args, busyButton) {
    if (busyButton) busyButton.disabled = true;
    try {
      return await ctx.send(action, args);
    } finally {
      if (busyButton) busyButton.disabled = false;
    }
  }

  function show(target, reply) {
    target.textContent = reply.message || (reply.ok ? "Done." : "That didn't work.");
    target.className = `cv-result ${reply.ok ? "ok" : "err"}`;
    target.hidden = false;
    if (target === result) {
      clearTimeout(resultTimer);
      resultTimer = setTimeout(() => {
        result.hidden = true;
        draw();
      }, RESULT_MS);
      draw();
    }
  }

  function drawControls() {
    const parts = [];
    const reason = state.active || state.rolling ? cantAct() : "";
    if (reason) {
      note.textContent = reason;
      parts.push(note);
    }
    if (reason === null) {
      if (isDm() && state.combatants.length) {
        const wanted = names();
        if (actorSelect.children.length !== wanted.length
            || wanted.some((n, i) => actorSelect.children[i].value !== n)) {
          actorSelect.replaceChildren(...wanted.map((n) => {
            const option = el("option", "", n);
            option.value = n;
            return option;
          }));
        }
        actorSelect.value = actorName();
        parts.push(actorLine);
      }
      if (offered.has("initiative") && state.waiting_for.includes(actorName())) {
        initiativeLabel.textContent = isDm() ? `${actorName()}'s initiative` : "Your initiative";
        parts.push(initiative);
      }
      if (offered.has("end_turn") && state.active && (myTurn() || isDm())) {
        endTurn.textContent = isDm() ? `End ${state.current}'s turn` : "End your turn";
        parts.push(endTurn);
      }
    }
    if (!result.hidden && result.textContent) parts.push(result);
    arrange(controls, parts);
  }

  // ── The action panel under a tapped row ──
  function makePanel(target) {
    const item = el("li", "cv-panel");
    const form = el("form");
    form.className = "cv-panel-form";
    const title = el("span");
    const close = button("✕", "secondary", () => {
      panel = null;
      draw();
    });
    const top = el("p", "cv-panel-head");
    top.append(title, close);
    const kinds = segmented(KINDS, () => refresh());
    kinds.pick("damage");
    const amount = input("number", "Amount", {min: "1", inputmode: "numeric"});
    const damageType = input("text", "Damage type (optional)", {list: "cv-damage-types",
                                                                maxlength: "32"});
    const condition = input("text", "Condition", {list: "cv-condition-names", maxlength: "32"});
    const remove = el("select");
    const fields = el("div", "cv-fields");
    const usesLabel = el("p", "cv-uses-label", "It isn't your turn: what does this use?");
    const uses = segmented([], () => {});
    const sendButton = button("Send", "", null, "submit");
    const out = el("p", "cv-result");
    out.hidden = true;
    form.append(top, kinds.element, fields, usesLabel, uses.element, sendButton, out);
    item.append(form);

    function refresh() {
      const row = state.combatants.find((c) => c.name === target);
      const conditions = (row && row.conditions) || [];
      title.textContent = `${actorName()} → ${target}`;
      const kindChoices = KINDS.filter(([key]) => key !== "remove_condition" || conditions.length);
      if (kinds.key !== kindChoices.length) {      // rebuilt only when it changes (focus)
        kinds.set(kindChoices);
        kinds.key = kindChoices.length;
      }
      if (!kinds.value) kinds.pick("damage");
      const kind = kinds.value;
      const shown = [];
      if (kind === "damage" || kind === "heal") shown.push(amount);
      if (kind === "damage") shown.push(damageType);
      if (kind === "condition") shown.push(condition);
      if (kind === "remove_condition") {
        const current = remove.value;
        remove.replaceChildren(...conditions.map((c) => {
          const option = el("option", "", c);
          option.value = c;
          return option;
        }));
        remove.value = conditions.includes(current) ? current : (conditions[0] || "");
        shown.push(remove);
      }
      arrange(fields, shown);
      const actor = actorRow();
      const choices = [];
      if (actor && actor.reaction) {
        choices.push(["reaction", `Reaction (${actor.reaction.current}/${actor.reaction.max})`]);
      }
      if (actor && actor.legendary) {
        choices.push(["legendary",
                      `Legendary action (${actor.legendary.current}/${actor.legendary.max})`]);
      }
      choices.push(["none", "Nothing"]);
      const key = JSON.stringify(choices);
      if (uses.key !== key) {
        uses.set(choices);
        uses.key = key;
      }
      const outOfTurn = !myTurn();
      usesLabel.textContent = isDm() ? `It isn't ${actorName()}'s turn: what does this use?`
        : "It isn't your turn: what does this use?";
      usesLabel.hidden = !outOfTurn;
      uses.element.hidden = !outOfTurn;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const kind = kinds.value;
      const args = {kind, target};
      if (kind === "damage" || kind === "heal") {
        const value = Number.parseInt(amount.value, 10);
        if (!(value >= 1)) {
          show(out, {ok: false, message: "How much? Type an amount of 1 or more."});
          return;
        }
        args.amount = value;
        if (kind === "damage" && damageType.value.trim()) {
          args.damage_type = damageType.value.trim();
        }
      } else if (kind === "condition") {
        if (!condition.value.trim()) {
          show(out, {ok: false, message: "Which condition?"});
          return;
        }
        args.condition = condition.value.trim();
      } else {
        if (!remove.value) {
          show(out, {ok: false, message: "Which condition?"});
          return;
        }
        args.condition = remove.value;
      }
      if (!myTurn()) {
        if (!uses.value) {
          show(out, {ok: false, message: "Pick what this uses first."});
          return;
        }
        args.uses = uses.value;
      }
      const reply = await send("action", withActor(args), sendButton);
      show(out, reply);
      if (reply.ok) {
        amount.value = "";      // the damage type stays: the same weapon, again
        condition.value = "";
        uses.pick(null);        // picked again for every action out of turn
      }
    });

    return {target, item, refresh};
  }

  // ── Drawing ──
  function header() {
    if (state.active) {
      const parts = [el("span", "cv-round", `Round ${state.round}`)];
      if (state.current) parts.push(el("span", "cv-turn", `${state.current}'s turn`));
      return parts;
    }
    return [el("span", "cv-round", "Rolling initiative")];
  }

  function draw() {
    if (!state) return;
    const running = state.active || state.rolling;
    empty.hidden = running;
    head.hidden = !running;
    waiting.hidden = !(state.rolling && !state.active);
    list.hidden = !running;
    drawControls();
    if (!running) {
      panel = null;
      arrange(list, []);
      return;
    }
    head.replaceChildren(...header());
    waiting.textContent = state.waiting_for.length
      ? `Waiting for initiative: ${state.waiting_for.join(", ")}`
      : "Everyone's initiative is in.";

    const canTap = state.active && offered.has("action") && cantAct() === null;
    if (panel && (!canTap || !names().includes(panel.target))) panel = null;
    const nodes = [];
    for (const c of state.combatants) {
      let item = rows.get(c.name);
      if (!item) {
        item = el("li");
        item.addEventListener("click", () => {
          if (!item.classList.contains("tappable")) return;
          panel = panel && panel.target === c.name ? null : makePanel(c.name);
          draw();
        });
        rows.set(c.name, item);
      }
      fillRow(item, c, state, ctx, canTap, panel && panel.target === c.name);
      nodes.push(item);
      if (panel && panel.target === c.name) {
        panel.refresh();
        nodes.push(panel.item);
      }
    }
    for (const name of [...rows.keys()]) {
      if (!names().includes(name)) rows.delete(name);
    }
    const focused = document.activeElement;
    arrange(list, nodes);
    if (focused && panel && panel.item.contains(focused) && document.activeElement !== focused) {
      focused.focus();
    }
  }

  return {
    update(newState) {
      state = newState;
      draw();
    },
    unmount() {
      clearTimeout(resultTimer);
      root.remove();
    },
  };
}
