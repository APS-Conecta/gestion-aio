<?php

/**
 * SPDX-FileCopyrightText: 2026 APS Conecta
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */

// What Nextcloud's legacy screens (maintenance, update and error), which skip the theming app, call the suite: its
// name, its address and the motto, which the theme step writes in (aps/provisionador/steps/theme.py). The footer says
// them as the login page's does, with no link to Nextcloud's own site.
class OC_Theme {
	public function getName(): string {
		return '{suite}';
	}

	public function getTitle(): string {
		return $this->getName();
	}

	public function getHTMLName(): string {
		return $this->getName();
	}

	public function getBaseUrl(): string {
		return '{site}';
	}

	public function getSlogan(?string $lang = null): string {
		return '{motto}';
	}

	public function getShortFooter(): string {
		return '<a href="' . htmlspecialchars($this->getBaseUrl()) . '" target="_blank" rel="noreferrer noopener">'
			. htmlspecialchars($this->getName()) . '</a> – ' . htmlspecialchars($this->getSlogan());
	}

	public function getLongFooter(): string {
		return $this->getShortFooter();
	}
}
