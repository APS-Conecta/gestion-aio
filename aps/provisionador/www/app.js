// SPDX-FileCopyrightText: 2026 APS Conecta
// SPDX-License-Identifier: AGPL-3.0-or-later
// The Provisionador's page: it exchanges the link's secret (the URL fragment, never sent in a request
// line) for a session, then drives «Centro» and «Dirección» through the HTTP API.
"use strict";

const $ = (id) => document.getElementById(id);
const CHILE = [-35.7, -71.5];
let register = null;
let selected = null;
let map = null;
let marker = null;
let searching = null;
let checking = null;
let polling = null;

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
  if (!response.ok) {
    throw Object.assign(new Error(answer.error || "Ocurrió un error inesperado. Intente nuevamente."),
      {status: response.status, answer});
  }
  return answer;
}

// The screens in order; the navigation opens one already reached.
function show(step) {
  for (const link of document.querySelectorAll(".step-link")) {
    const current = link.dataset.step === step;
    $(link.dataset.step).hidden = !current;
    link.parentElement.toggleAttribute("aria-current", current);
    if (current) link.disabled = false;
  }
  if (step === "centro") map.invalidateSize();
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
    direccion();
  } catch (e) {
    say(e.message, true);
  }
}

// «Dirección»: the name suggested from the centre, checked as the operator edits it; the registration with
// the operator's email, which the page resumes until deSEC's verification arrives; and whether this computer
// reaches the suite at its free domain.
function status(id, text, isError = false) {
  $(id).textContent = text;
  $(id).classList.toggle("error", isError);
}

// An outage offers «Reintentar», which repeats what failed.
function failed(e, again) {
  status("address-status", e.message, true);
  $("retry").hidden = e.status !== 503;
  $("retry").onclick = () => {
    $("retry").hidden = true;
    status("address-status", "");
    again();
  };
}

function availability(answer) {
  const suggestion = !answer.available && answer.suggestion;
  status("name-status", answer.available ? `${answer.domain} está disponible.`
    : `${answer.domain} ya está registrada.` + (suggestion ? ` Puede usar ${suggestion}.dedyn.io o escribir otra.` : ""),
  !answer.available);
  $("use-suggestion").hidden = !suggestion;
  $("use-suggestion").textContent = suggestion ? `Usar ${suggestion}` : "";
}

// Without a name, the one suggested from the centre.
async function checkName(name) {
  try {
    const answer = await api("GET", "/api/address" + (name === undefined ? "" : "?" + new URLSearchParams({name})));
    availability(answer);
    return answer;
  } catch (e) {
    if (e.status === 503) failed(e, () => checkName(name));
    else status("name-status", e.message, true);
  }
}

async function registerAddress(body) {
  try {
    follow((await api("POST", "/api/address", body)).address);
  } catch (e) {
    if (e.status === 409) availability(e.answer);
    failed(e, () => registerAddress(body));
  }
}

// Whether this browser opens the suite at its free domain, once the server says the suite answers there.
async function opens(domain) {
  try {
    await fetch(`https://${domain}/status.php`, {mode: "no-cors", cache: "no-store", signal: AbortSignal.timeout(8000)});
    return true;
  } catch {
    return false;
  }
}

async function reach(address) {
  if (!address.answers) {
    status("reach", "Cuando la suite esté en marcha, esta página comprobará que este equipo abre la dirección.");
  } else if (await opens(address.domain)) {
    return status("reach", `Este equipo abre https://${address.domain} correctamente.`);
  } else {
    status("reach", `Este equipo no logra abrir https://${address.domain}, aunque la suite ya responde en el `
      + "servidor: el DNS de esta red descarta las respuestas que apuntan a direcciones internas. Para corregirlo, "
      + `configure el router para que entregue por DHCP como servidor DNS la dirección ${location.hostname} de este `
      + "servidor, y luego desconecte y vuelva a conectar este equipo a la red. Esta página lo comprobará de nuevo "
      + "por sí sola.", true);
  }
  polling = setTimeout(async () => follow((await api("GET", "/api/state")).address), 15000);
}

function follow(address) {
  clearTimeout(polling);
  const form = $("address");
  form.name.value = address.name;
  form.email.value = address.email;
  form.name.readOnly = form.email.readOnly = true;
  form.password.value = "";
  if (address.status === "awaiting") {
    status("address-status", `deSEC envió un enlace de verificación a ${address.email}. Ábralo; esta página `
      + "continuará sola en cuanto lo haga. Si no llega, revise la carpeta de correo no deseado. Si ese correo ya "
      + "tenía una cuenta en deSEC, escriba su contraseña de deSEC y presione «Registrar dirección».");
    polling = setTimeout(() => registerAddress({name: address.name, email: address.email}), 15000);
    return;
  }
  form.hidden = true;
  status("address-status", `Dirección registrada: https://${address.domain}`);
  reach(address);
}

async function direccion(state) {
  show("direccion");
  const address = (state || await api("GET", "/api/state")).address;
  if (address) return follow(address);
  const form = $("address");
  if (form.name.value) return;
  const answer = await checkName();
  if (answer?.suggestion) {
    form.name.value = answer.suggestion; // a taken name gets a suffix the operator can edit
    checkName(answer.suggestion);
  } else if (answer) {
    form.name.value = answer.name;
  }
}

function startDireccion() {
  const form = $("address");
  form.name.addEventListener("input", () => {
    clearTimeout(checking);
    checking = setTimeout(() => checkName(form.name.value.trim()), 300);
  });
  $("use-suggestion").addEventListener("click", () => {
    form.name.value = $("use-suggestion").textContent.replace("Usar ", "");
    checkName(form.name.value);
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const body = {name: form.name.value.trim(), email: form.email.value.trim()};
    if (form.password.value) body.password = form.password.value;
    status("address-status", "Registrando la dirección en deSEC…");
    registerAddress(body);
  });
  for (const link of document.querySelectorAll(".step-link")) {
    link.addEventListener("click", () => (link.dataset.step === "direccion" ? direccion() : show("centro")));
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
  startMap();
  startDireccion();
  if (state.centre) {
    select({...state.centre, point: state.centre.official_point}, state.centre.point);
    confirmed(state.centre);
    direccion(state);
  } else {
    show("centro");
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
