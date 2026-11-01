"use strict";

const REFRESH_MS = 15000;   // the app's status; live updates come with the tabs

async function refresh() {
  const {ok, data} = await api("/api/me");
  if (!ok) {
    show("error", data.error || "Something went wrong.");
    return;
  }
  if (data.must_change) {
    location.replace("/password");
    return;
  }
  show("error", "");
  const who = document.getElementById("who");
  if (data.user) {
    who.textContent = `Logged in as ${data.user}` +
      (data.role === "dm" ? " (game master)" : "") +
      (data.character ? `, playing ${data.character}.` : ".");
  } else {
    who.textContent = "Watching as a guest.";
  }
  document.getElementById("login").hidden = Boolean(data.user);
  document.getElementById("password").hidden = !data.user;
  document.getElementById("logout").hidden = !data.user;

  const status = document.getElementById("app-status");
  status.textContent = data.app_connected ? "Table connected" : "Table offline";
  status.classList.toggle("offline", !data.app_connected);
  status.hidden = false;
}

document.getElementById("logout").addEventListener("click", async () => {
  await api("/api/logout", {});
  location.reload();
});

refresh();
setInterval(refresh, REFRESH_MS);
