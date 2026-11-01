"use strict";

// GET (no body) or POST JSON to the gateway's API. Never throws:
// {ok, status, data}; status 0 means the gateway couldn't be reached.
async function api(path, body) {
  const options = {credentials: "same-origin", cache: "no-store"};
  if (body !== undefined) {
    options.method = "POST";
    options.headers = {"Content-Type": "application/json"};
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (e) {
    return {ok: false, status: 0, data: {error: "Can't reach the gateway."}};
  }
  let data = {};
  try {
    data = await response.json();
  } catch (e) {
    // no JSON body
  }
  return {ok: response.ok, status: response.status, data};
}

// Show text in an element (hidden when empty).
function show(id, text) {
  const element = document.getElementById(id);
  element.textContent = text || "";
  element.hidden = !text;
}
