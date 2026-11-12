#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""«Equipos», «Almacenamiento», «Revisar» and «Instalar», from a fresh link as an operator who comes back to the wizard:
«Dirección» goes on to «Equipos», where the operator types the sectors and programs and sees the team each becomes,
downloads the people workbook, uploads the people as Excel saves a CSV, then in a workbook made from the downloaded one,
and each time sees the people the install will create with their cargos, on a page in the brand fonts with no console
error and no content-security-policy violation, then to «Almacenamiento», which lists the system disk and the machine's
second disk, ext4 and not mounted: the staff's files go to the second disk, which has the most free space, and the
backup, which may go to either disk, waits for the operator's choice, «Continuar» with it: the runner, another computer
over SSH, refuses AIO's key, which the page shows, until it is authorized there and then answers with Borg, and the
operator goes back to the backup folder, then to «Revisar», which summarises the centre's address, the teams, the
people, the cargo accounts, the components, where the staff's files go and that the backup goes to the backup folder;
«Instalar» shows each step and, once AIO runs our components, each container's progress live, and below them «Registro
de la instalación», which logs the suite's start as it runs. Meanwhile the operator goes back to «Equipos», ticks a
person who left and presses «Retirar», then confirms: the install applies the people as they were at «Instalar», so that
person can still store a file in their own files, and stops with the people step red until «Reintentar» applies
«Retirar» (48-retire checks the person). Then «Listo» shows the suite's address. There the wizard hands over the
passwords file, the same as the root-only copy on the machine, with the administrator's first password, AIO's passphrase
and the backup's encryption password AIO made (its own copy of it unchanged), and closes; the page then still tells,
behind a resolver that drops the name's answer, the router change, and otherwise that this computer opens the suite (no
check goes back to «Dirección» after «Instalar»). The second disk is mounted for good by its UUID, its empty mount point
immutable underneath, and AIO keeps the staff's files on it, as «Listo» says, with where the backup goes. AIO reports
our components on, every other optional one off (Talk recording follows the hardware: 70-talk checks it) and nightly
backups to the backup folder. AIO's passphrase reaches the browser only in the passwords file, and on the machine only
root reads the file AIO keeps it in; no answer carries a private key."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
import spreadsheet  # noqa: E402
import teams  # noqa: E402
from browser import aio_config, aio_file, as_person, chromium, files_folder, hardware, machine, suite_name  # noqa: E402
from browser import verified  # noqa: E402
from browser import STORED, first_password, provisionador_file, provisionador_state, wizard_link  # noqa: E402
from browser import PEOPLE, XLSX, faults, fonts, people_workbook, upload_people  # noqa: E402
from playwright.sync_api import expect  # noqa: E402
from steps.backups import BACKUP_FOLDER, BACKUP_TIME  # noqa: E402
from steps.components import chosen  # noqa: E402
from steps.passwords import FILE  # noqa: E402
from steps.people import CHANGED, NAME as ACCOUNTS  # noqa: E402
from steps.start import NAME as START  # noqa: E402
from steps.storage import FOLDER as FILES_FOLDER  # noqa: E402
from suite import MOUNTS  # noqa: E402

LISTO_WITHIN = 20  # minutes: the install's GitHub downloads alone can take several on a slow day
SSH = {field: os.environ[f"BACKUP_SSH_{field.upper()}"] for field in ("host", "user", "folder")}  # run.sh's target
SECTORS, PROGRAMS = ["Norte", "Sector Sur"], ["Cardiovascular"]  # as an operator types them; 45-equipos checks them
DECLARED = teams.declare(SECTORS, PROGRAMS)
# As Excel saves a Spanish sheet as plain CSV: «;» and Windows-1252.
CSV = "".join(";".join(row) + "\r\n" for row in (spreadsheet.COLUMNS, *PEOPLE)).encode("cp1252")
RETIRED = "luis.vera"  # retired with «Retirar» while the install runs
CHOSEN = list(chosen(hardware()).values())  # «Revisar» lists them, and the progress names their containers so
SWITCHES = {"isTalkEnabled": True, "officeSuite": "eurooffice", "isImaginaryEnabled": True, "isClamavEnabled": True,
            "isFulltextsearchEnabled": True, "isHarpEnabled": False,
            "isDockerSocketProxyEnabled": False, "isWhiteboardEnabled": False, "aio_community_containers": "caddy",
            "borg_backup_host_location": BACKUP_FOLDER, "borg_remote_repo": ""}


def whole(page, path):
    """A screenshot of the whole step: the window grown, for the shot alone, to the height of the wizard's content,
    which scrolls inside its own pane, so the page never scrolls."""
    window = page.viewport_size
    grown = page.evaluate("() => { const c = document.querySelector('.app-content');"
                          " return innerHeight + c.scrollHeight - c.clientHeight; }")
    page.set_viewport_size({**window, "height": max(window["height"], grown)})
    page.screenshot(path=path)
    page.set_viewport_size(window)


def red(page):
    """Each failed or waiting step on «Revisar», with its reason."""
    return page.eval_on_selector_all("#steps li[data-state=failed], #steps li[data-state=waiting]",
                                     "items => items.map(i => [i.querySelector('strong').textContent, "
                                     "i.querySelector('p')?.textContent])")


def store(user, deadline):
    """`user` stores STORED in their own files at the suite's name with the first password the passwords file lists,
    once the suite answers there with its certificate."""
    password = first_password(provisionador_file(FILE), user) or sys.exit(f"the passwords file does not list {user}")
    while True:
        try:
            status, _ = as_person(user, password, "PUT", f"{suite}/remote.php/dav/files/{user}/{quote(STORED)}",
                                  user.encode())
            if status not in (201, 204):  # 204: a retry after a PUT that timed out but stored it
                sys.exit(f"{user} does not store {STORED} in their files at {suite}: HTTP {status}")
            return
        except OSError as error:
            if time.monotonic() > deadline:
                sys.exit(f"{suite} does not answer with its certificate, so {user} stores no file: {error}")
            time.sleep(10)


centre = provisionador_state()["centre"]  # as 15-wizard chose it
people = spreadsheet.read(spreadsheet.rows(CSV), DECLARED, centre["type"])[0] or sys.exit("PEOPLE loads nobody")
passphrase, name = aio_config().get("password"), suite_name()
if not passphrase:  # it stays in memory: run.sh masks it in the log
    sys.exit("AIO stored no passphrase")
suite = f"https://{name}"
router = f"En el router, permita esas respuestas para {name.split('.', 1)[1]}"
dropped = f"{suite}/**"  # what the page fetches to tell whether this computer opens the suite
evidence = Path(os.environ["EVIDENCE"])
with tempfile.NamedTemporaryFile() as console:  # one-command.sh masks the fresh link's secret before the check reads it
    subprocess.run([REPO / "aps" / "acceptance" / "one-command.sh", console.name,
                    "sudo python3 /opt/aps-conecta/gestion/provisionador/provisionador.py link"], check=True)
    link, printed = wizard_link(Path(console.name).read_text(encoding="utf-8"))
url = urlsplit(link)

def other_computer(page):
    """«Almacenamiento»'s other computer, the runner over SSH: refused until AIO's key, which the page shows, is
    authorized there, then chosen; the backup goes back to the backup folder afterwards."""
    page.check('#storage-backups input[value="ssh"]')
    for field, value in SSH.items():
        page.fill(f"#ssh-{field}", value)
    page.click("#ssh-test")  # the first test also pulls AIO's borg image
    expect(page.locator("#ssh-status")).to_contain_text("no acepta la clave de AIO", timeout=600_000)
    key = page.inner_text("#ssh-key").strip()
    if not key.startswith("ssh-") or "PRIVATE" in key:
        sys.exit(f"«Almacenamiento» shows no public key for AIO's remote backups: {key!r}")
    page.screenshot(path=evidence / "almacenamiento-ssh-clave.png")
    authorized = f"/home/{SSH['user']}/.ssh/authorized_keys"
    subprocess.run(["sudo", "sh", "-c", f"cat >> {authorized} && chown {SSH['user']}: {authorized} && chmod 600 "
                    f"{authorized}"], input=key + "\n", text=True, check=True)
    page.click("#ssh-test")
    expect(page.locator("#ssh-status")).to_contain_text("El otro computador responde", timeout=300_000)
    expect(page.locator('#storage-backups li[data-id="ssh"] span')).to_have_text("Se usará")
    page.screenshot(path=evidence / "almacenamiento-ssh.png")
    print(f"the backup's other computer {SSH['user']}@{SSH['host']} refused AIO's key until it was authorized, then "
          f"answered with Borg")


with tempfile.TemporaryDirectory() as tmp, chromium(pinned=[verified(url.hostname, url.port, printed)]) as browser:
    har = Path(tmp, "wizard.har")  # every answer the browser got, bodies included; it stays out of the evidence
    context = browser.new_context(viewport={"width": 1280, "height": 900}, record_har_path=har,
                                  record_har_content="embed")
    page = context.new_page()
    found = faults(page)
    page.goto(link)
    page.click("#continue")
    expect(page.get_by_role("heading", name="Equipos")).to_be_visible()
    page.fill("#sectors", "\n".join(SECTORS))
    page.fill("#programs", "\n".join(PROGRAMS))
    expect(page.locator("#teams li strong")).to_have_text([team["name"] for team in DECLARED])
    workbook = people_workbook(page)
    for file, kind, data in (("planilla-personal.csv", "text/csv", CSV), (spreadsheet.FILE, XLSX, workbook)):
        upload_people(page, file, kind, data, people)
    expect(page.locator("#people li span")).to_contain_text(
        [teams.ROLES[person["cargo"]][0].format(type=centre["type"]) for person in people])
    page.screenshot(path=evidence / "equipos.png")
    if found or not {"Fraunces", "Nunito Sans"} <= set(fonts(page)):
        sys.exit(f"«Equipos» logged {found} or did not load the brand fonts")
    page.click("#teams-continue")
    expect(page.get_by_role("heading", name="Almacenamiento")).to_be_visible()
    data = machine(f"sudo blkid --label {os.environ['DATA_LABEL']}").strip()
    data_id = machine(f"sudo blkid --match-tag UUID --output value {data}").strip()
    expect(page.locator("#storage-files li")).to_have_count(2)
    expect(page.locator(f'#storage-files li[data-id="{data_id}"] span')).to_have_text("Se usará")
    expect(page.locator("#storage-backups li").first.locator("strong")).to_contain_text(BACKUP_FOLDER)
    expect(page.locator("#storage-backups li[data-state=chosen]")).to_have_count(0)
    expect(page.locator("#storage-continue")).to_be_disabled()
    other_computer(page)
    page.check('#storage-backups input[value="folder"]')
    expect(page.locator("#storage-ssh")).to_be_hidden()
    expect(page.locator('#storage-backups li[data-id="folder"] span')).to_have_text("Se usará")
    expect(page.locator("#storage-continue")).to_be_enabled()
    expect(page.locator("#storage-warning")).to_be_hidden()
    whole(page, evidence / "almacenamiento.png")
    page.click("#storage-continue")
    expect(page.get_by_role("heading", name="Revisar")).to_be_visible()
    expect(page.locator("#summary-files")).to_contain_text(data)
    expect(page.locator("#summary-address")).to_have_text(f"https://{name}")
    expect(page.locator("#summary-teams li")).to_have_text([team["name"] for team in DECLARED])
    expect(page.locator("#summary-people li")).to_contain_text([f"{p['name']} ({p['user']})" for p in people])
    expect(page.locator("#summary-cargo li")).to_have_text(
        [f"{account['name']} ({account['user']})" for account in teams.cargo_accounts(DECLARED, centre["type"])])
    expect(page.locator("#summary-components li")).to_have_text(CHOSEN)
    expect(page.locator("#summary-backups")).to_have_text(f"Cada noche, en la carpeta {BACKUP_FOLDER} del servidor.")
    whole(page, evidence / "revisar.png")

    page.route(dropped, lambda route: route.abort("namenotresolved"))  # a resolver that drops the name's answer
    page.click("#install")
    expect(page.locator("#steps li")).not_to_have_count(0)
    page.click(".step-link[data-step=equipos]")
    page.check(f"#people input[value='{RETIRED}']")
    page.click("#retire")
    leaving = next(person["name"] for person in people if person["user"] == RETIRED)
    expect(page.locator("#retire-question")).to_contain_text(leaving)
    page.click("#retire-yes")
    expect(page.locator("#people li[data-state=retired] strong")).to_have_text([leaving])
    page.screenshot(path=evidence / "retirar.png")
    page.click("#teams-continue")
    page.click("#storage-continue")
    seen = {}  # each container's states, in the order the page showed them
    deadline, stored = time.monotonic() + LISTO_WITHIN * 60, False
    while not page.locator("#listo").is_visible():
        for container, state in page.eval_on_selector_all(
                "#progress li", "items => items.map(i => [i.querySelector('strong').textContent, i.dataset.state])"):
            if seen.setdefault(container, [state])[-1] != state:
                seen[container].append(state)
        if page.locator("#install").is_visible() and not stored and red(page) == [[ACCOUNTS, CHANGED]]:
            page.screenshot(path=evidence / "retirar-reintentar.png")
            store(RETIRED, deadline)
            stored = True
            print(f"{RETIRED} stored {STORED}; «Reintentar» applies «Retirar»")
            page.click("#install")
        elif page.locator("#install").is_visible() or time.monotonic() > deadline:
            page.screenshot(path=evidence / "revisar-failed.png")
            sys.exit(f"no «Listo» within {LISTO_WITHIN} minutes: {page.inner_text('#install-status')}\n{page.inner_text('#steps')}")
        if {"running", "stopped"} <= {s[-1] for s in seen.values()} and not (evidence / "progress.png").exists():
            expect(page.locator("#log")).to_contain_text(f"Paso «{START}»: en curso.")
            page.locator("#log").scroll_into_view_if_needed()
            page.screenshot(path=evidence / "progress.png")
        page.wait_for_timeout(2000)
    progress = "".join(f"{c}: {' → '.join(s)}\n" for c, s in seen.items())
    (evidence / "progress.txt").write_text(progress, encoding="utf-8")
    print(progress, end="")
    expect(page.locator("#suite-address")).to_have_text(suite)
    expect(page.locator("#suite-address")).to_have_attribute("href", suite)
    expect(page.locator("#closed")).to_contain_text("se cerró y su enlace ya no funciona")
    with page.expect_download() as download:
        page.click("#passwords")
    handed = Path(download.value.path()).read_text(encoding="utf-8")
    if page.evaluate("fetch('/api/state').then(r => r.status, () => 'closed')") not in ("closed", 401):
        sys.exit("the wizard's session still works after «Listo»")
    expect(page.locator("#reach")).to_contain_text(router, timeout=60_000)
    page.screenshot(path=evidence / "router.png")
    print(f"router: {page.inner_text('#reach')}")
    page.unroute(dropped)
    expect(page.locator("#reach")).to_have_text(f"Este equipo abre {suite} correctamente.", timeout=180_000)
    expect(page.locator("#files-place")).to_contain_text(data)
    expect(page.locator("#backups-place")).to_contain_text(BACKUP_FOLDER)
    page.screenshot(path=evidence / "listo.png")
    print(f"reach: {page.inner_text('#reach')}")
    context.close()
    answers = [(e["request"]["url"], e["response"]["content"].get("text", ""))
               for e in json.loads(har.read_text(encoding="utf-8"))["log"]["entries"]]

unshown = [c for c in CHOSEN if c not in seen or seen[c][0] == "running" or seen[c][-1] != "running"]
if unshown:
    sys.exit(f"the progress did not show these containers waiting or starting, then running: {unshown}")

config = aio_config()
administrator = config["secrets"]["NEXTCLOUD_PASSWORD"]  # never printed; run.sh masks it once AIO made it
backup_password = config["secrets"].get("BORGBACKUP_PASSWORD") or sys.exit("AIO holds no backup encryption password")
if aio_file("backupsecret") != backup_password:  # AIO's own copy, written when it made the password; neither is printed
    sys.exit("AIO's backup encryption password is not the one it made")
carried = [url for url, text in answers if not url.endswith("/api/close")
           and (any(secret in text for secret in (passphrase, administrator, backup_password)) or "PRIVATE KEY" in text)]
if carried:
    sys.exit(f"these answers of the wizard carried AIO's passphrase, the administrator's password or the backup's "
             f"encryption password: {carried}")
kept = provisionador_file(FILE)
if not (handed == kept and passphrase in kept and administrator in kept
        and backup_password in kept):
    sys.exit("the passwords file «Listo» handed over is not the machine's copy with the administrator's first "
             "password, AIO's passphrase and AIO's backup encryption password")
reported = {key: config.get(key) for key in SWITCHES}
backup = aio_file("daily_backup_time").splitlines()
print(f"AIO reports {reported}; daily backup {backup}")
if reported != SWITCHES or backup[:2] != [BACKUP_TIME, ""]:  # line 2 names automatic updates off when AIO writes it
    sys.exit(f"AIO does not report our components, every other one off, nightly backups to {BACKUP_FOLDER} "
             "and its nightly update job on")

store = machine("sudo docker volume inspect --format '{{.Mountpoint}}' nextcloud_aio_mastercontainer").strip()
machine(f"sudo test -s {store}/data/configuration.json && ! test -r {store}/data/configuration.json")
print(f"«Listo» shows {suite} and hands over the passwords file; AIO keeps its passphrase in "
      f"{store}/data/configuration.json, which only root reads, and only the passwords file carried it to the browser")

point, folder = f"{MOUNTS}/{data_id}", f"{MOUNTS}/{data_id}/{FILES_FOLDER}"
source = machine(f"findmnt --noheadings --output SOURCE --target {point}").strip()
entry = [line.split() for line in machine("cat /etc/fstab").splitlines() if line.startswith(f"UUID={data_id} ")]
if source != data or not entry or entry[0][1] != point or "nofail" not in entry[0][3].split(","):
    sys.exit(f"{data} is not mounted for good at {point}: mounted from {source!r}, /etc/fstab holds {entry}")
under = machine(f"sudo sh -c 'd=$(mktemp -d) && mount --bind {MOUNTS} $d && lsattr -d $d/{data_id}; "
                "umount $d; rmdir $d'")
if "i" not in under.split()[0]:
    sys.exit(f"the empty mount point under {point} is not immutable: {under.strip()}")
ncdata = machine("sudo docker container inspect --format "
                 "'{{range .Mounts}}{{if eq .Destination \"/mnt/ncdata\"}}{{.Source}}{{end}}{{end}}' "
                 "nextcloud-aio-nextcloud").strip()
if files_folder() != folder or ncdata != folder:
    sys.exit(f"AIO does not keep the staff's files in {folder}: Nextcloud's data folder is {ncdata!r}")
print(f"{data} is mounted for good at {point}, immutable underneath, and AIO keeps the staff's files in {folder}")
