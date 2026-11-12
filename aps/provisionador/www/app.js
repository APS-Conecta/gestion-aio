// SPDX-FileCopyrightText: 2026 APS Conecta
// SPDX-License-Identifier: AGPL-3.0-or-later
// The Provisionador's page: it exchanges the link's secret (the URL fragment, never sent in a request
// line) for a session, then drives «Centro», «Dirección», «Equipos», «Almacenamiento», «Revisar» and «Listo» through
// the HTTP API.
"use strict";

const $ = (id) => document.getElementById(id);
const CHILE = [-35.7, -71.5];
const STEPS = ["centro", "direccion", "equipos", "almacenamiento", "revisar", "listo"];
const STATES = {stopped: "En espera", starting: "Iniciando", running: "En ejecución"};
const STEP_STATES = {pending: "Pendiente", running: "En curso", green: "Completado", failed: "No se completó",
  waiting: "En espera"};
const ADMINISTRATOR = "primer/a administrador/a de la suite";
const APPLY = " Los cambios se aplican al presionar «Reintentar» en «Revisar».";
const DROPPED = "Retirado/a: ya no está en la planilla";
const CHOOSE_BACKUP = "Elija dónde se guarda la copia de seguridad de cada noche.";
const NO_BACKUP_DISK = "Ningún disco del servidor tiene espacio suficiente para la copia de seguridad. Libere espacio o "
  + "conecte otro disco y vuelva a abrir «Almacenamiento».";
const NO_FILES_DISK = "Ningún disco del servidor tiene espacio suficiente para los archivos del personal. Libere "
  + "espacio o conecte otro disco y vuelva a abrir «Almacenamiento».";
const SAME_DISK = "La copia de seguridad queda en el mismo disco que los archivos del personal: si ese disco falla, "
  + "se pierden ambos. Para protegerlos, conecte otro disco al servidor.";
const KEPT_BACKUP = (place) => `La actualización mantiene la copia de seguridad de cada noche en ${place}, donde AIO `
  + "la guarda; su destino se cambia en la página de AIO, como explica el manual.";
const AI = "Un modelo de lenguaje en este servidor redacta textos para las aplicaciones, como la nota de Estadística, "
  + "sin enviar datos fuera de él. Puede activarla o desactivarla aquí más adelante.";
let register = null;
let selected = null;
let map = null;
let marker = null;
let searching = null;
let polling = null;
let typing = null;
let installed = false;
let following = true;  // the install's log keeps its newest line in view, unless the operator scrolled up to read

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
    throw Object.assign(new Error(answer.error || "Ocurrió un error inesperado. Intente nuevamente."), {answer});
  }
  return answer;
}

// The screens in order; the navigation opens every one up to the furthest reached. The install's log shows on «Revisar»
// and «Listo» only.
function show(step) {
  clearTimeout(polling);
  if (step !== "revisar" && step !== "listo") $("log-panel").hidden = true;
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

// «Equipos»: the sectors and programs, one per line, and the team each becomes, as the Provisionador names it. What is
// typed is kept as it changes, so «Revisar» shows it; once «Instalar» was pressed the teams stay, while a corrected
// spreadsheet and «Retirar» wait for «Reintentar».
async function equipos() {
  try {
    const state = await api("GET", "/api/state");
    installed = Boolean(state.install);
    for (const kind of ["sector", "program"]) {
      $(`${kind}s`).value = state.teams.filter((team) => team.kind === kind).map((team) => team.name).join("\n");
      $(`${kind}s`).disabled = installed;
    }
    showTeams(state);
    showPeople(state);
  } catch (e) {
    status("teams-status", e.message, true);
  }
  show("equipos");
}

function showTeams({teams: declared}) {
  $("teams").replaceChildren(...declared.map(
    (team) => item(team.name, "", `Carpeta ${team.folder}`)));
}

async function keepTeams() {
  clearTimeout(typing);
  if ($("sectors").disabled) return true;
  try {
    const plan = await api("POST", "/api/teams", {sectors: $("sectors").value.split("\n"),
      programs: $("programs").value.split("\n")});
    showTeams(plan);
    showPeople(plan);
    status("teams-status", "");
    return true;
  } catch (e) {
    status("teams-status", e.message, true);
    return false;
  }
}

// The people the uploaded spreadsheet loads, each with their cargo and the box «Retirar» takes, or each of its errors
// by line. The first administrator is not retired; a person a corrected spreadsheet drops once the install made their
// account is.
function showPeople({groups, people, errors, retired, dropped}) {
  $("people-errors").replaceChildren(...errors.map((error) => element("li", error)));
  $("people").replaceChildren(...people.map((person) => {
    const gone = retired.includes(person.user);
    const box = Object.assign(document.createElement("input"),
      {type: "checkbox", value: person.user, checked: gone, disabled: gone || person.admin});
    const label = element("label", "");
    label.append(box, element("strong", person.name));
    const node = element("li", "");
    node.dataset.state = gone ? "retired" : "";
    node.append(label, element("span", [gone && "Retirado/a", person.user, person.email,
      groups[person.cargo], person.admin && ADMINISTRATOR].filter(Boolean).join(" · ")));
    return node;
  }), ...dropped.map((person) => item(person.name, "retired", `${DROPPED} · ${person.user}`)));
  $("retire").disabled = true;
  $("retire-confirm").hidden = true;
}

function ticked() {
  return [...$("people").querySelectorAll("input:checked:enabled")];
}

// «Retirar» asks first: the ticked people's accounts are disabled and leave every group the install manages, keeping
// their files and shares.
function askRetire() {
  $("retire-question").textContent = `¿Retirar a ${ticked().map((box) => box.parentElement.textContent).join(", ")}? `
    + "Su cuenta se desactivará y saldrá de todos los grupos que asigna la instalación; sus archivos y lo que "
    + "compartió se conservan, y podrá reactivarse en «Cuentas», en APS Conecta Gestión.";
  $("retire-confirm").hidden = false;
}

async function retire() {
  const users = ticked().map((box) => box.value);
  try {
    showPeople(await api("POST", "/api/retire", {users}));
    status("people-status", `Se retiró a ${users.length} ${users.length === 1 ? "persona" : "personas"}.`
      + (installed ? APPLY : ""));
  } catch (e) {
    status("people-status", e.message, true);
  }
}

// The people workbook, built for the centre: the Provisionador sends its bytes in base64.
async function template() {
  try {
    const {file, content} = await api("GET", "/api/spreadsheet");
    const bytes = Uint8Array.from(atob(content), (char) => char.charCodeAt(0));
    const link = Object.assign(document.createElement("a"), {download: file, href: URL.createObjectURL(
      new Blob([bytes], {type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}))});
    link.click();
  } catch (e) {
    status("people-status", e.message, true);
  }
}

// The spreadsheet's bytes as the operator saved it, in base64: the Provisionador reads them.
async function upload() {
  const file = $("people-file").files[0];
  if (!file) return;
  const bytes = new Uint8Array(await file.arrayBuffer());
  $("people-file").value = "";
  try {
    const plan = await api("POST", "/api/people",
      {file: file.name, content: btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""))});
    showPeople(plan);
    status("people-status", plan.errors.length ? "La planilla tiene errores: corríjalos y súbala de nuevo."
      : `Se cargó la planilla: ${plan.people.length} ${plan.people.length === 1 ? "persona" : "personas"}.`
        + (installed ? APPLY : ""),
    plan.errors.length > 0);
  } catch (e) {
    status("people-status", e.message, true);
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

// «Almacenamiento»: every disk of the server, as GET /api/storage reads it now, with what it may hold or why it can't,
// where the staff's files go and, once chosen, the nightly backup; the backup folder on the system disk stands for that
// disk.
async function almacenamiento() {
  show("almacenamiento");
  try {
    showStorage(await api("GET", "/api/storage"));
  } catch (e) {
    status("storage-status", e.message, true);
  }
}

// Both parts are choices: the files disk until «Instalar» was pressed, the backup destination until «Listo», never
// during an upgrade, which keeps the one AIO holds. «Continuar» waits for both, and the page says why one cannot be
// chosen.
function showStorage(storage) {
  const system = storage.disks.find((disk) => disk.system);
  $("storage-files").replaceChildren(...storage.disks.map((disk) => placeRow("files", disk.id, diskName(disk), disk,
    storage.files === disk.id, storage.files_locked)));
  const locked = storage.backups_locked;
  $("storage-backups").replaceChildren(
    placeRow("backups", "folder", folderName(storage), system, Boolean(storage.backups?.folder), locked),
    ...storage.disks.filter((disk) => !disk.system).map((disk) => placeRow("backups", disk.id, diskName(disk), disk,
      storage.backups?.disk === disk.id, locked)),
    placeRow("backups", "ssh", "Otro computador (SSH)", null, Boolean(storage.backups?.ssh), locked));
  const ssh = storage.backups?.ssh;
  if (ssh) {
    for (const field of ["host", "port", "user", "folder"]) $(`ssh-${field}`).value = ssh[field];
    showKey(storage.ssh_key);
  }
  $("storage-ssh").hidden = !ssh && !$("storage-ssh").dataset.open;
  $("storage-warning").textContent = SAME_DISK;
  $("storage-warning").hidden = !storage.same_disk;
  $("storage-continue").disabled = !(storage.files && storage.backups);
  const anywhere = storage.disks.some((disk) => disk.backups);
  const kept = storage.backups?.kept && KEPT_BACKUP(storage.backups.kept);
  status("storage-status", !storage.files ? NO_FILES_DISK : kept || (storage.backups ? "" : anywhere ? CHOOSE_BACKUP
    : NO_BACKUP_DISK), !storage.files || !anywhere);
}

// A row of one part: a disk or the backup folder, with the radio button that chooses it, «Se usará» where the part goes,
// else whether it may hold it, its size and free space and, where it may not, why.
function placeRow(part, value, name, disk, chosen, locked = false) {
  const reason = disk?.reasons[part];
  const radio = Object.assign(document.createElement("input"), {type: "radio", name: part, value, checked: chosen,
    disabled: Boolean(reason) || locked});
  const label = element("label", "");
  label.append(radio, element("strong", name));
  const node = element("li", "");
  node.dataset.id = value;
  node.dataset.state = chosen ? "chosen" : reason ? "refused" : "";
  node.append(label, element("span", chosen ? "Se usará" : reason ? "No disponible" : "Disponible"));
  const facts = disk ? [gigabytes(disk.size), disk.free === null ? null : `${gigabytes(disk.free)} libres`, reason]
    : ["Por SSH, a un computador del establecimiento o a un servicio de copias con Borg"];
  node.append(element("p", facts.filter(Boolean).join(" · ")));
  return node;
}

async function choosePlace(event) {
  const {name, value} = event.target;
  if (value === "ssh") {  // the other computer is chosen once its test passes («Probar la conexión»)
    $("storage-ssh").dataset.open = "yes";
    $("storage-ssh").hidden = false;
    return;
  }
  if (name === "backups") delete $("storage-ssh").dataset.open;
  const body = name === "files" ? {files: value} : {backups: value === "folder" ? {folder: true} : {disk: value}};
  try {
    showStorage(await api("POST", "/api/storage", body));
  } catch (e) {
    showStorage(await api("GET", "/api/storage"));
    status("storage-status", e.message, true);
  }
}

// The other computer: AIO's key made and shown, the computer tested at once; a refusal says why, with the key to add.
async function testRemote() {
  const ssh = {host: $("ssh-host").value.trim(), port: Number($("ssh-port").value), user: $("ssh-user").value.trim(),
    folder: $("ssh-folder").value.trim()};
  status("ssh-status", "Probando el otro computador…");
  try {
    showStorage(await api("POST", "/api/storage", {backups: {ssh}}));
    status("ssh-status", "El otro computador responde: la copia de seguridad de cada noche irá allí.");
  } catch (e) {
    if (e.answer?.key) showKey(e.answer.key);
    status("ssh-status", e.message, true);
  }
  $("ssh-test").textContent = "Probar de nuevo";
}

function showKey(key) {
  $("ssh-key").textContent = key || "";
  $("ssh-key-note").hidden = !key;
}

// Where the nightly backup goes, as «Revisar» and «Listo» say it, with the warning when it shares the files disk.
function backupPlace(storage) {
  const byId = (id) => storage.disks.find((disk) => disk.id === id);
  const ssh = storage.backups?.ssh;
  const place = !storage.backups ? "Aún sin elegir: se elige en «Almacenamiento»."
    : storage.backups.kept ? `Cada noche, en ${storage.backups.kept}, donde AIO la guarda.`
    : storage.backups.folder ? `Cada noche, en la carpeta ${storage.folder} del servidor.`
      : ssh ? `Cada noche, en el otro computador ${ssh.host}, carpeta ${ssh.folder} del usuario ${ssh.user}.`
        : `Cada noche, en el disco ${diskName(byId(storage.backups.disk))}.`;
  return storage.same_disk ? `${place} ${SAME_DISK}` : place;
}

function gigabytes(bytes) {
  return `${Math.round(bytes / 1e9).toLocaleString("es-CL")} GB`;
}

function diskName(disk) {
  if (disk.system) return "Disco del sistema";
  return `${disk.model || "Disco"} (${disk.device}${disk.usb ? ", USB" : ""})`;
}

function folderName(storage) {
  return `Carpeta de copias de este servidor, ${storage.folder}`;
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
    $("summary-teams").replaceChildren(...(state.teams.length ? state.teams.map((team) => element("li", team.name))
      : [element("li", "Sin sectores ni programas")]));
    $("summary-people").replaceChildren(...(state.people.length ? state.people.map((person) => element("li",
      `${person.name} (${person.user})${person.admin ? `, ${ADMINISTRATOR}` : ""}`
        + (state.retired.includes(person.user) ? ", retirado/a" : "")))
      : [element("li", state.errors.length ? "La planilla del personal tiene errores: corríjala en «Equipos»"
        : "Sin planilla del personal")]));
    $("summary-people").append(...state.dropped.map(
      (person) => element("li", `${person.name} (${person.user}), ${DROPPED.toLowerCase()}`)));
    $("summary-cargo").replaceChildren(...state.cargo_accounts.map(
      (account) => element("li", `${account.name} (${account.user})`)));
    $("summary-components").replaceChildren(...state.components.map((name) => element("li", name)));
    $("summary-apps").replaceChildren(...state.apps.map((name) => element("li", name)));
    $("optional-apps").replaceChildren(...state.optional_apps.map(choice));
    $("ai").replaceChildren(choice({id: "ai", name: "IA local", summary: `${AI} ${state.ai.gpu}`,
      chosen: state.ai.chosen}));
    follow(state, false);
    const storage = await api("GET", "/api/storage");
    const files = storage.disks.find((disk) => disk.id === storage.files);
    $("summary-files").textContent = files ? diskName(files) : NO_FILES_DISK;
    $("summary-backups").textContent = backupPlace(storage);
  } catch (e) {
    status("install-status", e.message, true);
  }
}

// An optional app, off unless the operator ticked it; the choice of the first «Instalar» stays.
function choice(app) {
  const box = Object.assign(document.createElement("input"), {type: "checkbox", value: app.id, checked: app.chosen});
  const label = element("label", "");
  label.append(box, element("strong", app.name), element("span", app.summary));
  const node = element("li", "");
  node.append(label);
  return node;
}

function item(name, state, label, reason) {
  const node = element("li", "");
  node.dataset.state = state;
  node.append(element("strong", name), element("span", label));
  if (reason) node.append(element("p", reason));
  return node;
}

// The install's log, newest at the bottom, each line at this computer's time.
function showLog(lines) {
  $("log").replaceChildren(...lines.map((line) => {
    const node = element("li", ` ${line.text}`);
    node.prepend(Object.assign(element("time", new Date(line.time).toLocaleTimeString("es-CL", {hourCycle: "h23"})),
      {dateTime: line.time}));
    return node;
  }));
  if (following) $("log").scrollTop = $("log").scrollHeight;
}

// `jump`: at «Listo», go on to its screen (not when the operator came back to «Revisar»). A failed or waiting step is
// red, with its reason; «Reintentar» runs those again. The install's log follows, there and on «Listo».
function follow(state, jump = true) {
  const install = state.install;
  $("log-panel").hidden = !install;
  showLog(install?.log || []);
  $("install").hidden = Boolean(install) && install.status !== "failed";
  $("install").textContent = install?.status === "failed" ? "Reintentar" : "Instalar";
  for (const box of $("optional-apps").querySelectorAll("input")) box.disabled = Boolean(install);
  for (const box of $("ai").querySelectorAll("input")) box.disabled = install?.status === "installing";
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

// «IA local», off by default: the choice is kept, and once «Instalar» was pressed its step runs again.
async function chooseAi(event) {
  const box = event.target;
  try {
    await api("POST", "/api/ai", {on: box.checked});
    follow(await api("GET", "/api/state"));
  } catch (e) {
    box.checked = !box.checked;
    status("install-status", e.message, true);
  }
}

async function install() {
  $("install").hidden = true;
  status("install-status", "");
  try {
    const optional = [...$("optional-apps").querySelectorAll("input:checked")].map((box) => box.value);
    await api("POST", "/api/install", {optional});
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
    const storage = await api("GET", "/api/storage");
    const files = storage.disks.find((disk) => disk.id === storage.files);
    $("files-place").textContent = `Los archivos del personal quedan en: ${files ? diskName(files) : storage.files}.`;
    $("backups-place").textContent = `La copia de seguridad: ${backupPlace(storage)}`;
  } catch {
    $("files-place").textContent = $("backups-place").textContent = "";
  }
  try {
    const {passwords, file} = await api("POST", "/api/close", {});
    $("passwords").href = URL.createObjectURL(new Blob([passwords], {type: "text/plain;charset=utf-8"}));
    $("passwords").download = file;
    $("passwords").hidden = false;
    status("closed", "El asistente de instalación se cerró y su enlace ya no funciona. Descargue ahora el archivo de "
      + "contraseñas, con la del administrador de la suite, la frase de contraseña de la página de AIO y la contraseña "
      + "de cifrado de las copias de seguridad, sin la cual no se pueden restaurar, y guárdelo en un lugar seguro. Una "
      + "copia queda en el servidor, legible solo por root.");
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
  $("motto").textContent = state.motto;
  $("fingerprint").querySelector("code").textContent = state.fingerprint;
  $("fingerprint").hidden = false;
  $("register-date").textContent = ` del ${register.date.split("-").reverse().join("-")}`;
  fillFilters();
  startMap();
  const open = {
    centro: () => show("centro"), direccion, equipos, almacenamiento, revisar,
    listo: () => api("GET", "/api/state").then((now) => listo(now.address.name), (e) => say(e.message, true)),
  };
  for (const link of document.querySelectorAll(".step-link")) {
    link.addEventListener("click", () => open[link.dataset.step]());
  }
  $("retry").addEventListener("click", direccion);
  $("continue").addEventListener("click", equipos);
  for (const list of ["sectors", "programs"]) {
    $(list).addEventListener("input", () => {
      clearTimeout(typing);
      typing = setTimeout(keepTeams, 400);
    });
  }
  $("people-template").addEventListener("click", template);
  $("people-file").addEventListener("change", upload);
  $("people").addEventListener("change", () => {
    $("retire").disabled = !ticked().length;
    $("retire-confirm").hidden = true;
  });
  $("retire").addEventListener("click", askRetire);
  $("retire-yes").addEventListener("click", retire);
  $("retire-no").addEventListener("click", () => {
    $("retire-confirm").hidden = true;
  });
  $("teams-continue").addEventListener("click", async () => (await keepTeams()) && almacenamiento());
  $("storage-continue").addEventListener("click", revisar);
  $("storage-files").addEventListener("change", choosePlace);
  $("storage-backups").addEventListener("change", choosePlace);
  $("ssh-test").addEventListener("click", testRemote);
  $("install").addEventListener("click", install);
  $("ai").addEventListener("change", chooseAi);
  $("log").addEventListener("scroll", () => {
    const log = $("log");
    following = log.scrollTop + log.clientHeight >= log.scrollHeight - 1;
  });
  if (state.centre) {
    select({...state.centre, point: state.centre.official_point}, state.centre.point);
    confirmed(state.centre);
  }
  if (state.install?.status === "done") follow(state);
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
