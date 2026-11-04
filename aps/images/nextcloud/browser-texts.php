<?php
// SPDX-FileCopyrightText: 2026 APS Conecta
// SPDX-License-Identifier: AGPL-3.0-or-later
// The browser's Spanish of each app a theme overrides texts of. Nextcloud's server adds a theme's <folder>/l10n/es.json
// (folder: core, lib, apps/<id> or custom_apps/<id>) to the folder's own translations; its browser, though, loads only
// the first translations file it finds for an app, and a theme's core/<app>/l10n/es.js is the first it looks for
// (JSResourceLocator). There each theme in Nextcloud's themes folder gets the app's own Spanish, as Nextcloud holds it
// now, with the theme's overrides on top; an app Nextcloud does not hold gets none, which would hide its own Spanish.
// The theme and apps install steps run it through the suite port (aps/provisionador/steps/theme.py and apps.py), and
// the image's start once upstream's start installed and updated the apps.

set_error_handler(static fn (int $level, string $message) => throw new ErrorException($message, 0, $level));

const ROOT = '/var/www/html';

function read(string $file): array {
    return json_decode(file_get_contents($file), true, 512, JSON_THROW_ON_ERROR);
}

function js(mixed $value): string {
    return json_encode($value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
}

// A theme's overrides of core's and lib's texts, then of each app's.
$overrides = array_merge(glob(ROOT . '/themes/*/*/l10n/es.json') ?: [],
    glob(ROOT . '/themes/*/*/*/l10n/es.json') ?: []);
foreach ($overrides as $override) {
    [$theme, $folder] = explode('/', substr(dirname($override, 2), strlen(ROOT . '/themes/')), 2);
    $app = basename($folder);
    $script = ROOT . "/themes/$theme/core/$app/l10n/es.js";
    if (!is_file(ROOT . "/$folder/l10n/es.json")) {
        is_file($script) && unlink($script);
        continue;
    }
    $own = read(ROOT . "/$folder/l10n/es.json");
    $translations = (object) array_replace($own['translations'], read($override)['translations']);
    is_dir(dirname($script)) || mkdir(dirname($script), 0755, true);
    file_put_contents("$script.new", 'OC.L10N.register(' . js($app) . ', ' . js($translations) . ', '
        . js($own['pluralForm']) . ");\n");
    rename("$script.new", $script);
}
