<!--
  - SPDX-FileCopyrightText: 2026 APS Conecta
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# APS Conecta Gestión AIO

The suite repo: a fork of Nextcloud All-in-One plus the APS layer (bootstrap, Provisionador, suite port,
install steps, Suite manifest).

## Language

**Suite**:
«APS Conecta Gestión», everything one establishment installs.

**Motto** (lema):
«La salud que compartimos es la que mejora», the suite's slogan. It follows the suite's name on the login and share
pages, in the suite's emails and in the Provisionador.

**Establishment** (establecimiento):
One CESFAM, CECOSF or SAPU, with one installation of the suite.

**Upstream**:
`nextcloud/all-in-one` at the base commit the keep-list names.

**Keep-list**:
The declaration of every path where this repo differs from upstream, each with a reason. The
conformance gate enforces it.

**Suite release**:
A published version of this repo: bootstrap (carrying its bundle's checksum), our images, the pins of the official images, of
Ollama's image and of the Model, theme and data.
It contains no apps.

**Release candidate** (candidata):
A Suite release before its clean-machine acceptance run: a pre-release under its version, never «Latest». It becomes the
release «Latest» once every check passes, and is withdrawn otherwise.

**Upgrade** (actualización):
Rerunning the one command on an installed server moves it to the latest release: the bundle and the mastercontainer move to
it, the suite stops, every install step but the people's runs again (their Users page owns them), keeping the group admins
and folder grants administrators added after «Listo» (but for IntraVox's own groups, whose grants are IntraVox's
setup's, never an administrator's), the files disk with AIO's data folder on it, and the backup destination AIO holds, which
an administrator may have changed on AIO's page, and the reopened wizard shows the reconcile to «Listo». Between releases, AIO's nightly job — on, after the 06:00 UTC backup — keeps the containers on the
release's pins, and the store's apps update nightly (own and fork apps move only with a release, their releases above
the store's versions).

**Test establishment** (establecimiento de prueba):
APS Conecta's own entry beside the DEIS register, under a code outside DEIS's. «Centro» never lists it, and only APS's
token claims its name: a release's acceptance run claims it in production.

**Operator** (operador):
The person who runs the install.

**Provisionador**:
The single web wizard that runs the install, opened through the one-time link.

**One-time link**:
The link the bootstrap prints. Its secret opens a Provisionador session until the wizard closes.

**Suite port**:
The one adapter through which the Provisionador talks to AIO, the name service and Nextcloud's command
line, reads the server's cores, memory and disks, and mounts the files disk for good. It has a real implementation and
a fake one for tests.

**Name service** (servicio de nombres):
APS's Cloudflare Worker at `gestion-nombres.apsconecta.workers.dev`. Installs never hold APS's Cloudflare
token, only their name's credential: a DEIS code claims its dirección once and gets a credential that changes
only that name's address record and, through acme-dns, its certificate's challenge record, and can release
that name. PR runs use a separate CI deployment, `gestion-nombres-ci.apsconecta.workers.dev`.

**Dirección**:
The establishment's name, `<slug>.gestion.apsconecta.cl`, one per DEIS code. The slug is the short name,
lowercase with accents and «ñ» folded and hyphens, plus the comuna when two short names fold the same.
It points to the server's inside address.

**Install step**:
One idempotent unit of the install, with the steps it depends on.

**End-of-install check**:
The differences, step by step, between what each install step declared and what holds now: the running suite, and
for people also what «Equipos» declares now. «Listo» needs it clean.

**Passwords file** (archivo de contraseñas):
The root-only file on the server with each person's first password, the administrator's, AIO's passphrase and the
encryption password of AIO's backup, which a restore asks for. «Listo» hands it over; then the wizard closes.

**Component** (componente):
An optional AIO container the suite runs, switched on with AIO's own switch: Talk, Euro-Office, Imaginary, ClamAV and
full-text search, and Talk recording on a server that passes the recording threshold. Every other optional container
stays off.

**IA local**:
The Suite AI, an optional install step, off by default, that the operator turns on or off on «Revisar» while the wizard is
open: our «ollama» community container beside Nextcloud, on the server's GPU when it has one, serves the Model, and
Nextcloud's OpenAI-compatible connector points at it, so Nextcloud's apps have a text-generation provider. No data leaves
the server.

**Model** (modelo):
`gemma4:e2b`, the one language model «IA local» serves, which each Suite release pins by its tag and digest.

**Files disk** (disco de archivos):
The disk the staff's files live on, chosen on «Almacenamiento» until «Instalar»: by default the one with the most free
space that may hold them. The system disk may hold them with room for the suite's and the install's own files', and for
a first backup's too when the backup has nowhere else to go; it keeps them in AIO's own volume. Another disk needs ext4,
xfs or btrfs and room for the install's own files, a USB disk never may, and the install mounts it for good and has AIO
keep the files in its aps-conecta/archivos folder.

**Backup disk** (disco de copias):
A disk other than the system disk that the operator chose on «Almacenamiento» as the backup destination: the backup goes
in its aps-conecta/copias folder, and the install mounts it for good, as it mounts the files disk. A USB disk may be
one.

**Backup destination** (destino de las copias):
The one place AIO's nightly backup goes, which the operator chooses on «Almacenamiento» until the install's «Listo»:
the backup folder, the aps-conecta/copias folder of another disk, USB included, which the install mounts for good, or a
repository on another computer over SSH, the establishment's own or a Borg hosting service's, which takes AIO's key. On
a server where no other disk may hold it, one drive being one disk, the backup folder, chosen already when the system
disk has room for it. The install tests it before handing it to AIO, a test file written in a folder or the other
computer reached with Borg, and warns when it sits on the files disk's drive. After «Listo», an upgrade included, AIO's
page owns it.

**Backup folder** (carpeta de copias de seguridad):
`/var/backups/aps-conecta` on the server: the backup destination on the system disk.

**Team** (equipo):
A sector or program of the establishment: a shared folder with no member list, where every staff member works.

**Person** (persona):
A staff member loaded from the people spreadsheet.

**People spreadsheet** (planilla):
The workbook the operator downloads on «Equipos», fills in and uploads, saved as Excel or CSV: one person per row with
usuario, nombre, apellidos, correo, cargo and primer administrador, the cargo chosen from the establishment's list.

**Cargo**:
A person's job, chosen from the establishment's list. It places the person in one category: Jefaturas, Clínicos,
Técnicos or Administrativos.

**Cargo account** (cuenta de cargo):
A standing account for a position: director, subdirector, the jefaturas. Each one can set a temporary password for a
staff member and create, disable or delete staff accounts, never an administrator's: the director's and subdirector's for
every staff member, the other jefaturas' for the recovery group's members alone.

**Recovery group** («Personal fuera del equipo directivo»):
Every staff member outside the directorate: Director/a, Subdirector/a, Secretario/a de Dirección and their cargo
accounts. The jefaturas set temporary passwords for its members; the directorate's come from the director, the
subdirector or an administrator.

**Retire** (retirar):
Disable a person's account and remove it from every group the install manages, keeping their files and shares.

**Temporary password** (contraseña temporal):
The password a jefatura or an administrator sets, in person, for someone who lost theirs. The person replaces it at
once with one only they know.

**Own app**:
An app whose code lives in an APS-Conecta repo.

**Fork app**:
An upstream app kept as an APS-Conecta fork, with our changes as commits.

**Store app**:
An unmodified upstream app from the Nextcloud app store.

**Default app**:
An app installed on every establishment, without a question.

**Optional app** (aplicación opcional):
An app offered in the Provisionador, off by default.

**App release**:
A GitHub Release of an own or fork app with four assets: `<id>.tar.gz`, `info.xml`, `SHA256SUMS` and
`SHA256SUMS.minisig`, signed with the app key.

**App key** (clave de aplicaciones):
The key that signs every App release. Its public half ships in each Suite release, and the install verifies each own
and fork app against it before unpacking it.

**Suite manifest**:
The only list of the apps the suite adds, and of the forks that replace a component's app; the other components' apps
come with AIO's switches, and «IA local»'s connector with its step.

**Lock file**:
The per-server record of the installed app tags.

**Brand token file** (ficha de tokens de marca):
The one committed source of the palette and the font pairing. No other file carries a brand colour.
