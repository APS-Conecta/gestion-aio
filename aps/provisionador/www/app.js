// SPDX-FileCopyrightText: 2026 APS Conecta
// SPDX-License-Identifier: AGPL-3.0-or-later
// The Provisionador's page: it exchanges the link's secret (the URL fragment, never sent in a request
// line) for a session, then drives «Centro», «Dirección», «Revisar» and «Listo» through the HTTP API.
"use strict";

const $ = (id) => document.getElementById(id);
const CHILE = [-35.7, -71.5];
const STEPS = ["centro", "direccion", "revisar", "listo"];
const STATES = {stopped: "En espera", starting: "Iniciando", running: "En ejecución"};
const STEP_STATES = {pending: "Pendiente", running: "En curso", green: "Completado", failed: "No se completó",
  waiting: "En espera"};
let register = null;
let selected = null;
let map = null;
let marker = null;
let searching = null;
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
  if (!response.ok) throw new Error(answer.error || "Ocurrió un error inesperado. Intente nuevamente.");
  return answer;
}

// The screens in order; the navigation opens every one up to the furthest reached.
function show(step) {
  clearTimeout(polling);
  for (const link of document.querySelectorAll(".step-link")) {
    const current = link.dataset.step === step;
    $(link.dataset.step).hidden = !current;
    if (current) link.parentElement.setAttribute("aria-current", "step");
    else link.parentElement.removeAttribute("aria-current");
    if (STEPS.indexOf(link.dataset.step) <= STEPS.indexOf(step)) link.disabled = false;
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

// «Dirección»: the centre's name, claimed by itself; a name in use or an outage offers «Reintentar».
function status(id, text, isError = false) {
  $(id).textContent = text;
  $(id).classList.toggle("error", isError);
}

async function direccion() {
  show("direccion");
  $("retry").hidden = true;
  $("continue").hidden = true;
  try {
    let {address} = await api("GET", "/api/state");
    $("address-name").textContent = address.name;
    if (!address.claimed) {
      status("address-status", "Obteniendo la dirección del servicio de nombres de APS Conecta…");
      ({address} = await api("POST", "/api/address", {}));
    }
    status("address-status", `Dirección asignada: https://${address.name}`);
    $("continue").hidden = false;
  } catch (e) {
    status("address-status", e.message, true);
    $("retry").hidden = false;
  }
}

// Whether this browser opens the suite at its name.
async function opens(name) {
  try {
    await fetch(`https://${name}/status.php`, {mode: "no-cors", cache: "no-store", signal: AbortSignal.timeout(8000)});
    return true;
  } catch {
    return false;
  }
}

// On «Listo», where the suite answers at its name: asked again every 15 seconds, the wizard closed or not, as the
// operator may change the router meanwhile.
async function reach(name) {
  if (await opens(name)) {
    status("reach", `Este equipo abre https://${name} correctamente.`);
  } else {
    status("reach", `Este equipo no logra abrir https://${name}, aunque la suite ya responde en el servidor: `
      + "el DNS de esta red descarta las respuestas que apuntan a direcciones internas. En el router, permita esas "
      + `respuestas para ${name.split(".").slice(1).join(".")} (una excepción a la protección contra «DNS `
      + "rebinding») y vuelva a conectar este equipo a la red. Esta página lo comprobará de nuevo por sí sola.", true);
  }
  polling = setTimeout(() => reach(name), 15000);
}

// «Revisar»: one summary of what will be installed, then «Instalar»; the progress follows each step and, once AIO runs
// our components, each container as AIO reports it, until «Listo».
async function revisar() {
  show("revisar");
  try {
    const state = await api("GET", "/api/state");
    const {centre, address} = state;
    $("summary-centre").textContent = `${centre.name} (${centre.comuna}, ${centre.region})`;
    $("summary-address").textContent = `https://${address.name}`;
    $("summary-components").replaceChildren(...state.components.map((name) => element("li", name)));
    $("summary-backups").textContent = `Cada noche, en la carpeta ${state.backup_folder} del servidor.`;
    follow(state, false);
  } catch (e) {
    status("install-status", e.message, true);
  }
}

function item(name, state, label, reason) {
  const node = element("li", "");
  node.dataset.state = state;
  node.append(element("strong", name), element("span", label));
  if (reason) node.append(element("p", reason));
  return node;
}

// `jump`: at «Listo», go on to its screen (not when the operator came back to «Revisar»). A failed or waiting step is
// red, with its reason; «Reintentar» runs those again.
function follow(state, jump = true) {
  const install = state.install;
  $("install").hidden = Boolean(install) && install.status !== "failed";
  $("install").textContent = install?.status === "failed" ? "Reintentar" : "Instalar";
  $("steps").replaceChildren(...(install?.steps || []).map(
    (step) => item(step.name, step.status, STEP_STATES[step.status], step.reason)));
  $("progress").replaceChildren(...(install?.containers || []).map((c) => item(c.name, c.state, STATES[c.state])));
  if (!install) {
    status("install-status", "");
  } else if (install.status === "failed") {
    status("install-status", "Algunos pasos no se completaron; su motivo está en rojo. Presione «Reintentar».", true);
  } else if (install.status === "done") {
    status("install-status", "La instalación terminó.");
    if (jump) listo(state.address.name);
  } else {
    status("install-status", install.error
      || "Instalando: cada paso sigue por sí solo; descargar e iniciar los contenedores puede tardar varios minutos.",
    Boolean(install.error));
    polling = setTimeout(() => api("GET", "/api/state").then(follow, () => follow(state)), 3000);
  }
}

async function install() {
  $("install").hidden = true;
  status("install-status", "");
  try {
    await api("POST", "/api/install", {});
    follow(await api("GET", "/api/state"));
  } catch (e) {
    $("install").hidden = false;
    status("install-status", e.message, true);
  }
}

// «Listo»: the suite's address and whether this computer opens it. The wizard hands over the passwords file, kept for
// the download, and closes; the page stays.
async function listo(name) {
  show("listo");
  $("suite-address").textContent = `https://${name}`;
  $("suite-address").href = `https://${name}`;
  for (const link of document.querySelectorAll(".step-link")) link.disabled = true;
  reach(name);
  try {
    const {passwords, file} = await api("POST", "/api/close", {});
    $("passwords").href = URL.createObjectURL(new Blob([passwords], {type: "text/plain;charset=utf-8"}));
    $("passwords").download = file;
    $("passwords").hidden = false;
    status("closed", "El asistente de instalación se cerró y su enlace ya no funciona. Descargue ahora el archivo de "
      + "contraseñas, con la del administrador de la suite y la frase de contraseña de la página de AIO, y guárdelo en "
      + "un lugar seguro. Una copia queda en el servidor, legible solo por root.");
  } catch {
    status("closed", "No se pudo confirmar el cierre del asistente. Vuelva a ejecutar el comando de instalación: si el "
      + "asistente sigue abierto, imprimirá un enlace nuevo para descargar el archivo de contraseñas; si ya se cerró, "
      + "indicará dónde quedó en el servidor.", true);
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
  const open = {
    centro: () => show("centro"), direccion, revisar,
    listo: () => api("GET", "/api/state").then((now) => listo(now.address.name), (e) => say(e.message, true)),
  };
  for (const link of document.querySelectorAll(".step-link")) {
    link.addEventListener("click", () => open[link.dataset.step]());
  }
  $("retry").addEventListener("click", direccion);
  $("continue").addEventListener("click", revisar);
  $("install").addEventListener("click", install);
  if (state.centre) {
    select({...state.centre, point: state.centre.official_point}, state.centre.point);
    confirmed(state.centre);
  }
  if (state.install?.status === "done") listo(state.address.name);
  else if (state.install) revisar();
  else if (state.centre) direccion();
  else show("centro");
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
