"use strict";

const form = document.getElementById("form");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  show("error", "");
  const {ok, data} = await api("/api/login", {
    user: form.user.value.trim(),
    password: form.password.value,
  });
  button.disabled = false;
  if (!ok) {
    show("error", data.error || "Something went wrong.");
    form.password.select();
    return;
  }
  location.replace(data.must_change ? "/password" : "/");
});
