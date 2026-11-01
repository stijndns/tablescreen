"use strict";

const form = document.getElementById("form");
const MIN = 8, MAX = 128;   // as the gateway checks them

async function load() {
  const {ok, data} = await api("/api/me");
  if (ok && !data.user) {
    location.replace("/login");
    return;
  }
  if (!ok) {
    show("error", data.error || "Something went wrong.");
    return;
  }
  document.getElementById("user").value = data.user;
  document.getElementById("first-time").hidden = !data.must_change;
  document.getElementById("back").hidden = data.must_change;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  show("error", "");
  const current = form.current.value, next = form.new.value;
  if (next !== form.repeat.value) {
    show("error", "The two new passwords aren't the same.");
    return;
  }
  if (next.length < MIN || next.length > MAX) {
    show("error", `A password has ${MIN} to ${MAX} characters.`);
    return;
  }
  const button = form.querySelector("button");
  button.disabled = true;
  const {ok, data} = await api("/api/password", {current, new: next});
  button.disabled = false;
  if (!ok) {
    show("error", data.error || "Something went wrong.");
    return;
  }
  form.reset();
  document.getElementById("user").value = data.user;
  document.getElementById("first-time").hidden = true;
  document.getElementById("done").hidden = false;
  document.getElementById("back").hidden = false;
});

load();
