"use strict";

// The webapp's shell: one live WebSocket (/ws) and one tab per shared
// feature. The gateway sends `hello` (who we are, the table's status, the
// tabs) and `state` (one feature's public state) whenever they change.
//
// Each tab shows the first view that loads, starting at the one [api.views]
// asks for ("auto" = the start): the webapp's own, the feature's, the
// generic one. A view is a JS module: mount(element, ctx) →
// {update(state), unmount()}; it may list the state versions it
// understands in `schemas`.

const LEVELS = ["webapp", "feature", "generic"];
const LABELS = {webapp: "the webapp's view", feature: "the feature's view",
                generic: "the generic view"};
const NAMES = {webapp: "webapp view", feature: "feature view", generic: "generic view"};
const GENERIC = "/static/views/generic.js";
const CLOSE_LOGIN = 4401, CLOSE_PASSWORD = 4403;
const RETRY_MS = [1000, 2000, 5000, 10000];

const ctx = {user: null, role: "guest", character: null};
const states = {};          // feature → its latest state message
const tabs = new Map();     // feature → {key, panel, view, button}
let selected = remembered();
let socket = null, retries = 0, retryTimer = null;

function remembered() {
  try {
    return localStorage.getItem("tab");
  } catch (e) {
    return null;
  }
}

function remember(name) {
  try {
    localStorage.setItem("tab", name);
  } catch (e) {
    // private mode: the first tab next time
  }
}

// ── The connection ─────────────────────────────────────────────────────

function connect() {
  clearTimeout(retryTimer);
  socket = new WebSocket(`wss://${location.host}/ws`);
  socket.onopen = () => {
    retries = 0;
  };
  socket.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch (e) {
      return;
    }
    if (message.type === "hello") onHello(message);
    else if (message.type === "state") onState(message);
  };
  socket.onclose = (event) => {
    socket = null;
    if (event.code === CLOSE_LOGIN) {
      location.replace("/login");
      return;
    }
    if (event.code === CLOSE_PASSWORD) {
      location.replace("/password");
      return;
    }
    showStatus(false, true);
    const delay = RETRY_MS[Math.min(retries, RETRY_MS.length - 1)];
    retries += 1;
    retryTimer = setTimeout(connect, delay);
  };
}

// A phone waking up: don't wait for the next retry.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && socket === null) connect();
});

function showStatus(connected, gatewayLost) {
  const status = document.getElementById("app-status");
  status.textContent = gatewayLost ? "Reconnecting…" : connected ? "Table connected" : "Table offline";
  status.classList.toggle("offline", !connected);
  const banner = document.getElementById("banner");
  const shown = tabs.size > 0;
  if (gatewayLost) {
    banner.textContent = "Lost the connection to the gateway; trying again."
      + (shown ? " Showing the last known state." : "");
  } else if (!connected) {
    banner.textContent = "The table is offline." + (shown ? " Showing the last known state." : "");
  }
  banner.hidden = connected && !gatewayLost;
  document.getElementById("panels").classList.toggle("stale", !connected || gatewayLost);
}

// ── Messages ───────────────────────────────────────────────────────────

function onHello(hello) {
  const changedWho = hello.user !== ctx.user || hello.character !== ctx.character
    || hello.role !== ctx.role;
  Object.assign(ctx, {user: hello.user, role: hello.role, character: hello.character});
  showWho();
  syncTabs(hello.tabs || []);
  showStatus(hello.app_connected, false);
  if (changedWho) {
    for (const [name, tab] of tabs) refresh(name, tab);    // e.g. "you" moves
  }
}

function onState(message) {
  states[message.feature] = message;
  const tab = tabs.get(message.feature);
  if (tab) refresh(message.feature, tab);
}

function showWho() {
  const who = document.getElementById("who");
  if (ctx.user) {
    who.textContent = `Logged in as ${ctx.user}`
      + (ctx.role === "dm" ? " (game master)" : "")
      + (ctx.character ? `, playing ${ctx.character}.` : ".");
  } else {
    who.textContent = "Watching as a guest.";
  }
  document.getElementById("login").hidden = Boolean(ctx.user);
  document.getElementById("password").hidden = !ctx.user;
  document.getElementById("logout").hidden = !ctx.user;
}

document.getElementById("logout").addEventListener("click", async () => {
  await api("/api/logout", {});
  location.reload();
});

// ── Tabs ───────────────────────────────────────────────────────────────

function syncTabs(descriptors) {
  const names = descriptors.map((d) => d.name);
  for (const [name, tab] of tabs) {
    if (!names.includes(name)) {
      removeTab(name, tab);
    }
  }
  for (const descriptor of descriptors) {
    const key = JSON.stringify(descriptor);
    const tab = tabs.get(descriptor.name);
    if (tab && tab.key === key) continue;
    if (tab) removeTab(descriptor.name, tab);
    addTab(descriptor, key);
  }
  // Keep the bar in the gateway's order.
  const bar = document.getElementById("tabbar");
  bar.replaceChildren(...names.map((name) => tabs.get(name).button));
  bar.hidden = names.length === 0;
  document.getElementById("empty").hidden = names.length > 0;
  select(names.includes(selected) ? selected : names[0]);
}

function addTab(descriptor, key) {
  const name = descriptor.name;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "tab";
  button.textContent = name.charAt(0).toUpperCase() + name.slice(1);
  button.addEventListener("click", () => {
    select(name);
    remember(name);
  });
  const panel = document.createElement("section");
  panel.className = "card panel";
  const notice = document.createElement("p");
  notice.className = "notice";
  notice.hidden = true;
  const body = document.createElement("div");
  const caption = document.createElement("p");
  caption.className = "caption";
  panel.append(notice, body, caption);
  document.getElementById("panels").append(panel);
  const tab = {key, panel, button, notice, body, caption, view: null, schema: descriptor.schema};
  tabs.set(name, tab);
  loadView(descriptor, tab);
}

function removeTab(name, tab) {
  tab.removed = true;
  if (tab.view) {
    try {
      tab.view.unmount();
    } catch (e) {
      console.error(e);
    }
  }
  tab.panel.remove();
  tab.button.remove();
  tabs.delete(name);
}

function select(name) {
  selected = name;
  for (const [tabName, tab] of tabs) {
    tab.panel.hidden = tabName !== name;
    tab.button.classList.toggle("selected", tabName === name);
  }
}

// The first view that loads, from the level [api.views] asks for.
async function loadView(descriptor, tab) {
  const forced = descriptor.view && descriptor.view !== "auto" ? descriptor.view : null;
  const start = forced ? LEVELS.indexOf(forced) : 0;
  const notes = [];
  for (const level of LEVELS.slice(Math.max(start, 0))) {
    const url = level === "generic" ? GENERIC : descriptor.views[level];
    if (!url) {
      if (level === forced) notes.push(`This tab has no ${NAMES[level]}.`);
      continue;
    }
    try {
      const module = await import(url);
      if (Array.isArray(module.schemas) && !module.schemas.includes(descriptor.schema)) {
        notes.push(`${capital(LABELS[level])} is written for ${module.schemas.join(", ")},`
          + ` not ${descriptor.schema}.`);
        continue;
      }
      if (tab.removed) return;
      tab.body.replaceChildren();
      tab.view = module.mount(tab.body, ctx);
      tab.caption.textContent = `${descriptor.name} · ${NAMES[level]}`;
      if (notes.length) {
        tab.notice.textContent = `${notes.join(" ")} Showing ${LABELS[level]}.`;
        tab.notice.hidden = false;
      }
      refresh(descriptor.name, tab);
      return;
    } catch (e) {
      console.error(e);
      notes.push(`${capital(LABELS[level])} didn't load.`);
    }
  }
  tab.notice.textContent = `${notes.join(" ")} Nothing can show this tab.`;
  tab.notice.hidden = false;
}

function refresh(name, tab) {
  const message = states[name];
  if (!tab.view || !message) return;
  try {
    tab.view.update(message.data);
  } catch (e) {
    console.error(e);
    tab.notice.textContent = "This view failed to show the latest state.";
    tab.notice.hidden = false;
  }
}

function capital(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

connect();
