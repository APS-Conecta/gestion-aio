<!--
  - SPDX-FileCopyrightText: 2026 APS Conecta
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# 3. The brand reaches apps only through Nextcloud's variables

Accepted, 2026-11-19 (spec #158, ticket #159).

Theming gave Nextcloud only the primary and background colours, so most colours staff saw were Nextcloud's defaults,
and Territorio and Tótem painted palettes of their own. The brand now takes one path. The brand token file holds every
brand colour by role, at the brand manual's values. The theme's variable map gives those roles to Nextcloud 34's
theming variables. Apps style themselves only with `@nextcloud/vue` components and Nextcloud's variables: no app reads
the brand token file or writes a colour literal, and a colour that is data, such as a map category's or a unidad's,
stays data. The theme acceptance check measures the whole map in the browser, and the brand gate keeps every brand
colour in the token file.

## Considered Options

- Giving apps the brand tokens (`--aps-…` in app styles): rejected. Each app would hard-wire the brand and drift from
  it on its own, fork and store apps would never follow, and an own app would lose its colours outside the suite.
- A shared app stylesheet that every own app imports: rejected. It would be a second theme beside Nextcloud's,
  released with each app, that fork and store apps never load, repeating what Nextcloud's variables already carry.

## Consequences

- A token change reaches every app, fork and store apps included, with no app release.
- The brand reaches only what Nextcloud has a variable for. Apps that want the display font use headings.
- A Nextcloud major can rename or add variables. The map names Nextcloud 34's, and the theme acceptance check fails
  when a mapped variable no longer holds its role.
