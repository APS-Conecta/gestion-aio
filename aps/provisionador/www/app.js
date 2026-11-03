// SPDX-FileCopyrightText: 2026 APS Conecta
// SPDX-License-Identifier: AGPL-3.0-or-later
// The Provisionador's page: it exchanges the link's secret (the URL fragment, never sent in a request
// line) for a session, then drives «Centro» through the HTTP API.
"use strict";

const $ = (id) => document.getElementById(id);
const CHILE = [-35.7, -71.5];
let register = null;
let selected = null;
let map = null;
let marker = null;
let searching = null;

async function api(method, path, body) {
  let response;
  let answer;
  try {
    response = await fetch(path, {
      method,
      headers: body ? {"Content-Type": "application/json"} : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    answer = await response.json();
  } catch {
    throw new Error("No se pudo comunicar con el Provisionador. Revise la conexión e intente nuevamente.");
  }
  if (!response.ok) throw new Error(answer.error || "Ocurrió un error inesperado. Intente nuevamente.");
  return answer;
}

function element(tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function say(text, isError = false) {
  $("message").textContent = text;
  $("message").classList.toggle("error", isError);
}

function locked(message) {
  $("locked-message").textContent = message;
  $("locked").hidden = false;
}

function fillFilters() {
  const form = $("search");
  for (const r of register.regions) form.region.append(Object.assign(element("option", r.name), {value: r.code}));
  for (const t of register.types) form.type.append(Object.assign(element("option", t.name), {value: t.code}));
  fillComunas();
}

function fillComunas() {
  const form = $("search");
  form.comuna.length = 1;
  for (const c of register.comunas) {
    if (!form.region.value || c.region === form.region.value) {
      form.comuna.append(Object.assign(element("option", c.name), {value: c.code}));
    }
  }
}

function search() {
  clearTimeout(searching);
  searching = setTimeout(async () => {
    try {
      showResults(await api("GET", "/api/establishments?" + new URLSearchParams(new FormData($("search")))));
    } catch (e) {
      say(e.message, true);
    }
  }, 200);
}

function typeName(code) {
  return (register.types.find((t) => t.code === code) || {name: code}).name;
}

function showResults(found) {
  $("total").textContent = found.total === 0 ? "Ningún establecimiento coincide con la búsqueda."
    : found.total > found.rows.length
      ? `Se muestran ${found.rows.length} de ${found.total} establecimientos. `
        + "Precise la búsqueda para encontrar el suyo."
      : `${found.total} ${found.total === 1 ? "establecimiento" : "establecimientos"}.`;
  $("results").replaceChildren(...found.rows.map((row) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.code = row.code;
    button.setAttribute("aria-pressed", String(selected?.code === row.code));
    button.append(element("strong", row.name),
      element("span", `${typeName(row.type)} · ${row.comuna} · ${row.address}`));
    button.addEventListener("click", () => select(row, row.point));
    const item = document.createElement("li");
    item.append(button);
    return item;
  }));
}

// `establishment.point` is the register's official point; `point` is where the marker goes.
function select(establishment, point) {
  selected = establishment;
  place(point);
  map.setView([point.latitude, point.longitude], 16);
  for (const b of $("results").querySelectorAll("button")) {
    b.setAttribute("aria-pressed", String(b.dataset.code === establishment.code));
  }
  $("map-note").textContent = `${establishment.name}. Si el marcador no está sobre el establecimiento, `
    + "haga clic en el lugar correcto o arrástrelo.";
  $("confirm").disabled = false;
  $("reset").disabled = false;
  say("");
}

function place(point) {
  const at = [point.latitude, point.longitude];
  if (marker) {
    marker.setLatLng(at);
    return;
  }
  const icon = L.divIcon({className: "pin", iconSize: [22, 22]});
  marker = L.marker(at, {draggable: true, icon, title: "Ubicación del establecimiento"}).addTo(map);
}

function confirmed(centre) {
  say(`Centro confirmado: ${centre.name}, ${centre.comuna}. Zona horaria: ${centre.timezone}.`);
}

async function confirm() {
  const at = marker.getLatLng().wrap();
  const round = (degrees) => Math.round(degrees * 1e6) / 1e6;
  try {
    const {centre} = await api("POST", "/api/centre", {
      code: selected.code, point: {latitude: round(at.lat), longitude: round(at.lng)},
    });
    confirmed(centre);
  } catch (e) {
    say(e.message, true);
  }
}

function startMap() {
  map = L.map("map").setView(CHILE, 4);
  const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });
  tiles.once("tileerror", () => {
    $("map-error").hidden = false;
  });
  tiles.addTo(map);
  map.on("click", (e) => selected && place({latitude: e.latlng.lat, longitude: e.latlng.lng}));
}

async function start() {
  const code = location.hash.slice(1);
  if (code) {
    history.replaceState(null, "", location.pathname);
    try {
      await api("POST", "/api/session", {link: code});
    } catch (e) {
      return locked(e.message);
    }
  }
  let state;
  try {
    state = await api("GET", "/api/state");
    register = await api("GET", "/api/register");
  } catch (e) {
    return locked(e.message);
  }
  $("fingerprint").querySelector("code").textContent = state.fingerprint;
  $("fingerprint").hidden = false;
  $("register-date").textContent = ` del ${register.date.split("-").reverse().join("-")}`;
  fillFilters();
  $("centro").hidden = false;
  startMap();
  if (state.centre) {
    select({...state.centre, point: state.centre.official_point}, state.centre.point);
    confirmed(state.centre);
  }
  const form = $("search");
  form.addEventListener("submit", (e) => e.preventDefault());
  form.region.addEventListener("change", fillComunas);
  form.addEventListener("input", search);
  form.addEventListener("change", search);
  $("confirm").addEventListener("click", confirm);
  $("reset").addEventListener("click", () => place(selected.point));
  search();
}

start();
