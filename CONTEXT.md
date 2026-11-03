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

**Establishment** (establecimiento):
One CESFAM, CECOSF or SAPU, with one installation of the suite.

**Upstream**:
`nextcloud/all-in-one` at the base commit the keep-list names.

**Keep-list**:
The declaration of every path where this repo differs from upstream, each with a reason. The
conformance gate enforces it.

**Suite release**:
A published version of this repo: bootstrap (carrying its bundle's checksum), our images, the pins of the official images, theme and data.
It contains no apps.

**Operator** (operador):
The person who runs the install.

**Provisionador**:
The single web wizard that runs the install, opened through the one-time link.

**One-time link**:
The link the bootstrap prints. Its secret opens a Provisionador session until the wizard closes.

**Suite port**:
The one adapter through which the Provisionador talks to AIO, the name service and Nextcloud's command
line. It has a real implementation and a fake one for tests.

**Name service** (servicio de nombres):
APS's Cloudflare Worker at `nombres.apsconecta.cl`, the only holder of APS's Cloudflare token. A DEIS code
claims its dirección once and gets a credential that changes only that name's address record and, through
acme-dns, its certificate's challenge record. PR runs use a separate CI deployment.

**Dirección**:
The establishment's name, `<slug>.gestion.apsconecta.cl`, one per DEIS code. The slug is the short name,
lowercase with accents and «ñ» folded and hyphens, plus the comuna when two short names fold the same.
It points to the server's inside address.

**Install step**:
One idempotent unit of the install, with the steps it depends on.

**End-of-install check**:
The comparison of the running suite with what the Provisionador declared. «Listo» needs it clean.

**Team** (equipo):
A sector or program of the establishment. It becomes a group with its folders.

**Person** (persona):
A staff member loaded from the people spreadsheet.

**People spreadsheet** (planilla):
The CSV the operator uploads: `usuario;nombre;apellidos;correo;grupos;primer_admin`.

**Cargo account** (cuenta de cargo):
A standing account for a position: director, subdirector, the jefaturas.

**Retire** (retirar):
Disable a person's account and remove it from every team, keeping their files and shares.

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

**Suite manifest**:
The only list of the suite's apps.

**Lock file**:
The per-server record of the installed app tags.

**Brand token file** (ficha de tokens de marca):
The one committed source of the palette and the font pairing. No other file carries a brand colour.
