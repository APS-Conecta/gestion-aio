# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behaviour of the static gates and the release bundle, driven through their command lines."""

import json
import os
import re
import runpy
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

GATES = Path(__file__).resolve().parent
REPO = GATES.parents[1]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class Repo:
    """A throwaway git repo: an 'upstream' base commit, then fork changes on top."""

    def __init__(self, files):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        git(self.path, "init", "-q")
        git(self.path, "config", "user.email", "t@example.org")
        git(self.path, "config", "user.name", "t")
        self.write(files)
        self.base = self.commit()
        git(self.path, "tag", "v0.0.1")

    def write(self, files):
        for name, text in files.items():
            p = self.path / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

    def delete(self, name):
        git(self.path, "rm", "-q", name)

    def commit(self):
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", "x")
        return git(self.path, "rev-parse", "HEAD")

    def keep_list(self, *entries, base=None):
        lines = [f"# base: {base or self.base} upstream fixture v0.0.1"] + list(entries) + ["aps/ APS layer"]
        self.write({"aps/keep-list.txt": "\n".join(lines) + "\n"})

    def close(self):
        self._tmp.cleanup()


def run_gate(name, repo, *extra):
    r = subprocess.run([sys.executable, "-I", str(GATES / name), "--repo", str(repo.path), *extra],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class ConformanceGate(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({"php/a.php": "a\n", "php/b.php": "b\n", "readme.md": "r\n"})
        self.addCleanup(self.repo.close)

    def test_undeclared_change_to_an_upstream_file_fails_naming_it(self):
        self.repo.write({"php/a.php": "changed\n"})
        self.repo.keep_list()
        self.repo.commit()
        code, out = run_gate("conformance.py", self.repo, "--upstream", str(self.repo.path))
        self.assertEqual(code, 1, out)
        self.assertIn("M php/a.php", out)

    def test_undeclared_deletion_of_an_upstream_file_fails_naming_it(self):
        self.repo.delete("php/b.php")
        self.repo.keep_list()
        self.repo.commit()
        code, out = run_gate("conformance.py", self.repo, "--upstream", str(self.repo.path))
        self.assertEqual(code, 1, out)
        self.assertIn("D php/b.php", out)

    def test_changes_declared_by_file_or_directory_entries_pass(self):
        self.repo.write({"php/a.php": "changed\n", "readme.md": "ours\n", "aps/tool.py": "x\n"})
        self.repo.delete("php/b.php")
        self.repo.keep_list("php/ PHP rebranding", "readme.md APS front page")
        self.repo.commit()
        code, out = run_gate("conformance.py", self.repo, "--upstream", str(self.repo.path))
        self.assertEqual(code, 0, out)

    def test_entry_that_matches_no_difference_fails_as_stale(self):
        self.repo.keep_list("readme.md APS front page")
        self.repo.commit()
        code, out = run_gate("conformance.py", self.repo, "--upstream", str(self.repo.path))
        self.assertEqual(code, 1, out)
        self.assertIn("stale", out)
        self.assertIn("readme.md", out)

    def test_entry_without_a_reason_fails(self):
        self.repo.write({"readme.md": "ours\n"})
        self.repo.keep_list("readme.md")
        self.repo.commit()
        code, out = run_gate("conformance.py", self.repo, "--upstream", str(self.repo.path))
        self.assertEqual(code, 1, out)
        self.assertIn("no reason", out)

    def test_base_that_is_not_the_named_upstream_tag_fails(self):
        self.repo.write({"php/a.php": "changed\n"})
        fork_commit = self.repo.commit()
        self.repo.keep_list("php/a.php PHP rebranding", base=fork_commit)
        self.repo.commit()
        code, out = run_gate("conformance.py", self.repo, "--upstream", str(self.repo.path))
        self.assertEqual(code, 1, out)
        self.assertIn("is not upstream tag v0.0.1", out)

    def test_keep_list_without_a_base_commit_fails(self):
        self.repo.write({"aps/keep-list.txt": "aps/ APS layer\n"})
        self.repo.commit()
        code, out = run_gate("conformance.py", self.repo, "--upstream", str(self.repo.path))
        self.assertEqual(code, 1, out)
        self.assertIn("no '# base", out)
        self.assertNotIn("Traceback", out)


# Fixture codes only: the gate itself proves no real register code is named here.
CODE, OTHER_CODE = "999999", "999998"
REGISTER = "aps/deis/establecimientos-deis-2026-07-23.csv"


class DeisCodeCheck(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({
            "readme.md": "r\n",
            REGISTER: f"codigo,tipo,nombre\n{CODE},CESFAM,Uno\n{OTHER_CODE},SAPU,Dos\n",
        })
        self.addCleanup(self.repo.close)

    def test_tracked_file_naming_a_register_code_fails_with_its_location(self):
        self.repo.write({"aps/sites/demo.sh": "SITE_NOMBRE=x\nSITE_DEIS=" + CODE + "\n"})
        self.repo.commit()
        code, out = run_gate("deis_codes.py", self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("aps/sites/demo.sh:2", out)

    def test_a_code_without_a_deis_label_fails(self):
        self.repo.write({"docs.md": "El centro " + OTHER_CODE + " queda en la comuna\nsites/" + CODE + "/site.sh\n"})
        self.repo.commit()
        code, out = run_gate("deis_codes.py", self.repo)
        self.assertEqual(code, 1, out)
        for n in (1, 2):
            self.assertIn(f"docs.md:{n}:", out)

    def test_numbers_that_are_not_register_codes_pass(self):
        self.repo.write({"notes.md": "DEIS 2026-07\nDEIS 123456\nport 8443\n1" + CODE + "\n"
                                     "sha256:ab" + CODE + "cd\n"})
        self.repo.commit()
        code, out = run_gate("deis_codes.py", self.repo)
        self.assertEqual(code, 0, out)

    def test_the_register_and_untracked_files_are_not_scanned(self):
        self.repo.write({"aps/sites/local.sh": "SITE_DEIS=" + CODE + "\n"})
        code, out = run_gate("deis_codes.py", self.repo)
        self.assertEqual(code, 0, out)

    def test_a_tree_without_the_register_fails(self):
        self.repo.delete(REGISTER)
        self.repo.commit()
        code, out = run_gate("deis_codes.py", self.repo)
        self.assertNotEqual(code, 0, out)
        self.assertIn("no DEIS register", out)


MANIFEST = [
    {"id": "inicio", "name": "Inicio", "source": "fork", "repo": "APS-Conecta/inicio"},
    {"id": "propia", "name": "Propia", "source": "own", "repo": "APS-Conecta/propia"},
    {"id": "tienda", "name": "Tienda", "source": "store"},
    {"id": "opcional", "name": "Opcional", "source": "own", "repo": "APS-Conecta/opcional", "optional": True,
     "summary": "Lo que «Revisar» dice de ella."},
]


class SuiteManifestGate(unittest.TestCase):
    def check(self, manifest):
        repo = Repo({"aps/provisionador/apps.json": manifest if isinstance(manifest, str) else json.dumps(manifest)})
        self.addCleanup(repo.close)
        return run_gate("manifest.py", repo)

    def test_own_fork_store_and_optional_apps_pass(self):
        code, out = self.check(MANIFEST)
        self.assertEqual(code, 0, out)

    def test_each_planted_defect_fails_naming_its_app(self):
        defects = {
            "a version": ({**MANIFEST[1], "version": "la última"}, "unknown fields ['version']"),
            "an id that is not an app id": ({**MANIFEST[2], "id": "Tienda"}, "not a Nextcloud app id"),
            "an id listed twice": ({**MANIFEST[2], "id": "inicio"}, "id is listed twice"),
            "no name": ({**MANIFEST[2], "name": " "}, "no Spanish name"),
            "an unknown source": ({**MANIFEST[2], "source": "zip"}, "not own, fork or store"),
            "an own app without its repo": ({k: v for k, v in MANIFEST[1].items() if k != "repo"},
                                            "an own app needs its APS-Conecta repo"),
            "a repo outside the org": ({**MANIFEST[1], "repo": "nextcloud/propia"}, "an own app needs its APS-Conecta"),
            "a repo listed twice": ({**MANIFEST[1], "repo": "APS-Conecta/inicio"}, "repo is listed twice"),
            "a store app with a repo": ({**MANIFEST[2], "repo": "APS-Conecta/tienda"}, "a store app has no repo"),
            "optional off": ({**MANIFEST[3], "optional": False}, "optional is true or left out"),
            "an optional app without its summary": ({k: v for k, v in MANIFEST[3].items() if k != "summary"},
                                                    "needs the summary"),
            "a default app with a summary": ({**MANIFEST[1], "summary": "x"}, "only an optional app has a summary"),
        }
        for defect, (app, message) in defects.items():
            with self.subTest(defect):
                code, out = self.check([MANIFEST[0], app])
                self.assertEqual(code, 1, out)
                self.assertIn(f"app 2 ({app.get('id')}): ", out)
                self.assertIn(message, out)

    def test_a_manifest_that_is_not_a_list_of_apps_fails(self):
        for manifest in ("[{", {"apps": MANIFEST}, [], ["inicio"]):
            with self.subTest(manifest):
                code, out = self.check(manifest)
                self.assertEqual(code, 1, out)
                self.assertNotIn("Traceback", out)
# Fixture colours only: the gate itself proves no brand colour is written here.
TOKENS = """:root {
  --aps-violet: #123abc;
  --aps-gold: #F28;
  --aps-paper: white;
}
"""


class BrandGate(unittest.TestCase):
    """Every brand colour comes from the brand token file, and our own stylesheets carry no colour literal at all."""

    def setUp(self):
        self.repo = Repo({
            "aps/brand/tokens.css": TOKENS,
            "aps/provisionador/www/app.css": ".header { background: var(--aps-violet); color: var(--aps-paper); }\n",
            "aps/theme/core/css/server.css": "#header { background: var(--aps-violet); }\n",
            "php/public/style.css": ":root { --color-primary-element: var(--aps-violet); --color-error: #db0606; }\n",
            "php/public/img/logo.svg": '<svg><path fill="#ffffff" stroke="var(--aps-gold)"/></svg>\n',
        })
        self.addCleanup(self.repo.close)

    def test_a_tree_whose_brand_colours_all_come_from_the_token_file_passes(self):
        code, out = run_gate("brand.py", self.repo)
        self.assertEqual(code, 0, out)

    def test_a_stray_brand_colour_fails_naming_where_in_any_case_or_notation(self):
        self.repo.write({"php/public/img/logo.svg": '<svg>\n<path fill="#123ABC"/>\n</svg>\n',
                         "php/templates/page.twig": "<p style=\"color: rgba(18, 58, 188, .5)\">\n",
                         "aps/brand/favicon.svg": '<svg><rect fill="#ff2288"/></svg>\n',
                         "php/templates/alpha.twig": '<i style="color: #123abc80">\n<b style="color: #F288">\n'})
        self.repo.commit()
        code, out = run_gate("brand.py", self.repo)
        self.assertEqual(code, 1, out)
        for where in ("php/public/img/logo.svg:2", "php/templates/page.twig:1", "aps/brand/favicon.svg:1",
                      "php/templates/alpha.twig:1", "php/templates/alpha.twig:2"):
            self.assertIn(where, out)

    def test_a_colour_literal_in_our_own_stylesheets_fails_and_upstreams_are_left_alone(self):
        self.repo.write({"aps/provisionador/www/app.css": ".pin { border: 4px solid #fff; }\n"
                                                          ".map { color: color(srgb 0.1 0.2 0.3); }\n",
                         "aps/theme/core/css/server.css": "body { --blur: rgba(255, 255, 255, .9); }\n"})
        self.repo.commit()
        code, out = run_gate("brand.py", self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("aps/provisionador/www/app.css:1", out)
        self.assertIn("aps/provisionador/www/app.css:2", out)
        self.assertIn("aps/theme/core/css/server.css:1", out)
        self.assertNotIn("php/public/style.css", out)

    def test_a_tree_without_the_brand_token_file_fails(self):
        self.repo.delete("aps/brand/tokens.css")
        self.repo.commit()
        code, out = run_gate("brand.py", self.repo)
        self.assertNotEqual(code, 0, out)
        self.assertIn("no brand token file", out)


JOBS, IMAGE = "aps/provisionador/steps/jobs.py", "aps/images/nextcloud/Dockerfile"
WINDOW = {JOBS: 'NAME = "Tareas"\nMAINTENANCE_WINDOW = 1\nSETTINGS = {}\n',
          IMAGE: "FROM nextcloud\nENV NEXTCLOUD_MAINTENANCE_WINDOW=1\n"}


class MaintenanceWindowGate(unittest.TestCase):
    """The jobs install step and the thin Nextcloud image name one maintenance-window hour, the same one."""

    def setUp(self):
        self.repo = Repo(WINDOW)
        self.addCleanup(self.repo.close)

    def test_the_same_hour_in_both_passes(self):
        code, out = run_gate("maintenance_window.py", self.repo)
        self.assertEqual(code, 0, out)

    def test_hours_that_differ_fail_naming_each(self):
        self.repo.write({IMAGE: "FROM nextcloud\nENV NEXTCLOUD_MAINTENANCE_WINDOW=5\n"})
        code, out = run_gate("maintenance_window.py", self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn(f"{JOBS}: 1", out)
        self.assertIn(f"{IMAGE}: 5", out)

    def test_a_file_that_names_no_hour_fails_naming_it(self):
        for path, text in ((JOBS, 'NAME = "Tareas"\n'), (IMAGE, "FROM nextcloud\n")):
            with self.subTest(path):
                repo = Repo({**WINDOW, path: text})
                self.addCleanup(repo.close)
                code, out = run_gate("maintenance_window.py", repo)
                self.assertEqual(code, 1, out)
                self.assertIn(f"{path} names no maintenance-window hour", out)


DIGEST = "sha256:" + "a" * 64


def services(*images):
    """A container definition file running each (image, image_tag)."""
    return json.dumps({"aio_services_v1": [{"container_name": f"c{n}", "image": image, "image_tag": tag}
                                           for n, (image, tag) in enumerate(images)]})


PINS = {
    "aps/keep-list.txt": "# base: 0 upstream fixture\ncommunity-containers/ollama/ IA local\n",
    "php/containers.json": services(("ghcr.io/nextcloud-releases/aio-apache", DIGEST),
                                    ("ghcr.io/aps-conecta/gestion-nextcloud", "%AIO_CHANNEL%")),
    "community-containers/ollama/ollama.json": services(("ollama/ollama", DIGEST)),
    "community-containers/jellyfin/jellyfin.json": services(("jellyfin/jellyfin", "latest")),  # never run
    "aps/provisionador/model.json": json.dumps({"model": "gemma4:e2b", "digest": DIGEST}),
}


class PinsGate(unittest.TestCase):
    """Every image the suite runs but our own, and the model «IA local» pulls, is pinned by digest: the official images
    and the community containers the keep-list declares, and the model's tag with its digest, never a bare tag."""

    def check(self, **changes):
        repo = Repo({**PINS, **changes})
        self.addCleanup(repo.close)
        return run_gate("pins.py", repo)

    def test_the_committed_tree_passes(self):
        r = subprocess.run([sys.executable, "-I", str(GATES / "pins.py")], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_images_by_digest_ours_by_release_and_a_model_with_its_digest_pass(self):
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_an_image_by_tag_fails_naming_its_file_and_container(self):
        for path in ("php/containers.json", "community-containers/ollama/ollama.json"):
            with self.subTest(path):
                code, out = self.check(**{path: services(("ollama/ollama", DIGEST), ("ollama/ollama", "0.40.16"))})
                self.assertEqual(code, 1, out)
                self.assertIn(f"{path}: c1 runs ollama/ollama:0.40.16, not pinned by digest", out)

    def test_a_bare_model_tag_or_a_model_without_its_tag_fails_naming_it(self):
        for model, digest in (("gemma4:e2b", None), ("gemma4:e2b", "e2b"), ("gemma4", DIGEST)):
            with self.subTest((model, digest)):
                pin = {"model": model, **({"digest": digest} if digest else {})}
                code, out = self.check(**{"aps/provisionador/model.json": json.dumps(pin)})
                self.assertEqual(code, 1, out)
                self.assertIn(f"aps/provisionador/model.json: {model} is not pinned by its tag and digest", out)


class ModelBenchmark(unittest.TestCase):
    """The model benchmark measures the Model alone, at the tag and digest the release pins (ADR 0002): it chooses
    no model."""

    def test_the_benchmark_measures_the_pinned_model_alone(self):
        pin = json.loads((REPO / "aps" / "provisionador" / "model.json").read_text(encoding="utf-8"))
        benchmark = runpy.run_path(str(REPO / "aps" / "benchmark" / "benchmark.py"))
        self.assertEqual(benchmark.get("MODEL"), pin, "the benchmark measures other models than the Model")


class AioStartAnswer(unittest.TestCase):
    """The suite port reads AIO's start answer for the text AIO writes as it begins to download a container's image:
    once AIO writes another text, the gates fail instead of the install's log going quiet."""

    def test_aio_writes_before_each_image_download_the_text_the_suite_port_reads(self):
        text = runpy.run_path(str(REPO / "aps" / "provisionador" / "suite.py"))["DOWNLOADING"]
        pull = (REPO / "php" / "src" / "Docker" / "DockerActionManager.php").read_text(encoding="utf-8")
        self.assertEqual(pull.count(f'$addToStreamingResponseBody("{text}", $container);'), 1,
                         f"DockerActionManager::PullImage no longer writes «{text}»")


DEIS = GATES.parent / "provisionador" / "deis.py"
HEADER ="codigo,tipo,nombre,direccion,comuna_codigo,comuna,region_codigo,region,servicio_salud,dependencia,latitud,longitud\n"


def slug_table(rows, tests=(), command="slugs"):
    """Runs a slug-table command, `slugs` or `tests`, on a register of `rows` (code, type, name, comuna), with the test
    establishments `tests` beside it."""
    def csv(rows):
        return HEADER + "".join(f"{c},{t},{n},Calle 1,13101,{comuna},13,Metropolitana,Servicio,Municipal,-33.4,-70.6\n"
                                for c, t, n, comuna in rows)

    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "establecimientos-deis-2026-07-23.csv").write_text(csv(rows))
        if tests:
            Path(tmp, "establecimientos-prueba.csv").write_text(csv(tests))
        r = subprocess.run([sys.executable, "-I", str(DEIS), command, tmp], capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


class SlugTable(unittest.TestCase):
    def test_each_code_gets_its_short_name_folded_and_hyphenated(self):
        code, out, err = slug_table([
            ("999001", "CESFAM", "Centro de Salud Familiar Los Castaños", "Santiago"),
            ("999002", "CECOSF", "Centro Comunitario de Salud Familiar Estrecho", "Punta Arenas"),
            ("999003", "SAPU", "SAPU Rapa Nui", "Isla de Pascua"),
            ("999004", "CESFAM", "Centro de Salud Familiar Dr. Juan Pérez (ex Ñuble)", "Santiago"),
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"999001": "cesfam-los-castanos", "999002": "cecosf-estrecho",
                                           "999003": "sapu-rapa-nui", "999004": "cesfam-dr-juan-perez-ex-nuble"})

    def test_short_names_that_fold_the_same_add_their_comuna(self):
        code, out, err = slug_table([
            ("999001", "CESFAM", "Centro de Salud Familiar Bellavista", "Tomé"),
            ("999002", "CESFAM", "CESFAM Bellavista", "La Florida"),
            ("999003", "CESFAM", "Centro de Salud Familiar Bellavista Norte", "Tomé"),
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"999001": "cesfam-bellavista-tome", "999002": "cesfam-bellavista-la-florida",
                                           "999003": "cesfam-bellavista-norte"})

    def test_two_codes_left_with_one_slug_fail_naming_both(self):
        code, out, err = slug_table([
            ("999001", "CESFAM", "Centro de Salud Familiar Bellavista", "Tomé"),
            ("999002", "CESFAM", "CESFAM Bellavista", "Tome"),
        ])
        self.assertEqual(code, 1, out + err)
        self.assertIn("cesfam-bellavista-tome", err)
        self.assertIn("999001", err)
        self.assertIn("999002", err)
        self.assertEqual(out, "")

    def test_test_establishments_get_a_table_of_their_own_apart_from_the_listed_ones(self):
        rows = [("999001", "CESFAM", "Centro de Salud Familiar Los Castaños", "Santiago")]
        tests = [("999009", "CESFAM", "Centro de Salud Familiar Prueba", "Santiago")]
        for command, table in (("slugs", {"999001": "cesfam-los-castanos"}), ("tests", {"999009": "cesfam-prueba"})):
            code, out, err = slug_table(rows, tests, command)
            self.assertEqual((code, json.loads(out)), (0, table), err)

    def test_a_test_establishment_with_a_register_code_or_a_listed_slug_fails_either_table(self):
        rows = [("999001", "CESFAM", "Centro de Salud Familiar Los Castaños", "Santiago")]
        for tests, named in (([("999001", "CESFAM", "Centro de Salud Familiar Prueba", "Santiago")], "999001"),
                             ([("999009", "CESFAM", "CESFAM Los Castaños", "Santiago")], "cesfam-los-castanos")):
            for command in ("slugs", "tests"):
                code, out, err = slug_table(rows, tests, command)
                self.assertEqual((code, out), (1, ""), err)
                self.assertIn(named, err)


class ReleaseBundle(unittest.TestCase):
    """aps/release/package.sh: only a test build's bundle carries Let's Encrypt's staging roots, one per file, for the
    server to trust; a release's bundle carries what it always did, with production's services alone."""

    PINS = [f"ghcr.io/aps-conecta/{image}@sha256:{n * 64}"
            for image, n in (("gestion-mastercontainer", "0"), ("gestion-nextcloud", "1"), ("gestion-caddy", "2"))]
    ROOTS = re.findall(rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----\n",
                       (REPO / "aps" / "acceptance" / "letsencrypt-staging-roots.pem").read_bytes(), re.S)

    def bundle(self, tag):
        """Each file of the bundle package.sh makes for the tag, by its path, with its bytes."""
        out = tempfile.TemporaryDirectory()
        self.addCleanup(out.cleanup)
        subprocess.run([str(REPO / "aps" / "release" / "package.sh"), tag, *self.PINS, out.name], cwd=REPO,
                       check=True, capture_output=True)
        with tarfile.open(Path(out.name) / f"gestion-aio-{tag}.tar.gz") as tar:
            return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}

    def test_a_release_carries_no_staging_root_and_names_production_alone(self):
        files = self.bundle("v1.0.0")
        self.assertEqual({name.split("/")[0] for name in files},
                         {"VERSION", "images.lock", "services.json", "bootstrap", "provisionador", "deis", "brand",
                          "theme"})
        self.assertFalse([r for r in self.ROOTS for t in files.values() if r in t])
        self.assertEqual(json.loads(files["services.json"]), {
            "url": "https://gestion-nombres.apsconecta.workers.dev", "suffix": "gestion.apsconecta.cl",
            "acme_ca": "https://acme-v02.api.letsencrypt.org/directory"})

    def test_a_test_build_carries_each_staging_root_the_test_browser_trusts_in_a_file_of_its_own(self):
        files = self.bundle("v0.0.0-ci.1")
        trusted = {name: text for name, text in files.items() if name.startswith("trusted-cacerts/")}
        self.assertEqual(len(self.ROOTS), 4)
        self.assertTrue(all(name.endswith(".crt") for name in trusted), trusted.keys())
        self.assertEqual(sorted(trusted.values()), sorted(self.ROOTS))
        self.assertIn(b"acme-staging-v02", files["services.json"])


class ReleaseWorkflowGate(unittest.TestCase):
    """aps/gates/release.py: the release workflow, as committed and with one defect planted, makes a release «Latest»
    only once every check of that candidate passed, and creates every release as a pre-release kept from «Latest»."""

    WORKFLOW = REPO / ".github" / "workflows" / "aps-release.yml"
    PUBLISH = "needs: [images, release, bootstrap, host-checks, acceptance, acceptance-upgrade]\n    if: inputs.version != ''\n"

    def gate(self, old="", new=""):
        """The gate's exit code and output on the committed workflow with `old` replaced by `new`."""
        text = self.WORKFLOW.read_text(encoding="utf-8")
        if old:
            self.assertEqual(text.count(old), 1, f"the workflow holds {old!r} once")
            text = text.replace(old, new)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yml") as planted:
            planted.write(text)
            planted.flush()
            r = subprocess.run([sys.executable, "-I", str(GATES / "release.py"), "--workflow", planted.name],
                               capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_the_committed_workflow_passes(self):
        code, out = self.gate()
        self.assertEqual(code, 0, out)

    def test_latest_before_the_acceptance_run_passed_fails_naming_it(self):
        code, out = self.gate(self.PUBLISH, self.PUBLISH.replace(", acceptance-upgrade]", "]"))
        self.assertEqual(code, 1, out)
        self.assertIn("publish makes a release «Latest» without waiting for acceptance-upgrade", out)

    def test_latest_whatever_the_checks_did_fails(self):
        code, out = self.gate(self.PUBLISH, self.PUBLISH.replace("if: ", "if: always() && "))
        self.assertEqual(code, 1, out)
        self.assertIn("publish makes a release «Latest» even when a check failed", out)

    def test_latest_under_a_block_scalar_if_that_runs_whatever_the_checks_did_fails(self):
        code, out = self.gate(self.PUBLISH, self.PUBLISH.replace("if: ", "if: >-\n      always() &&\n      "))
        self.assertEqual(code, 1, out)
        self.assertIn("publish makes a release «Latest» even when a check failed", out)

    def test_latest_after_an_explicit_success_passes(self):
        code, out = self.gate(self.PUBLISH, self.PUBLISH.replace("if: ", "if: success() && "))
        self.assertEqual(code, 0, out)

    def test_a_check_that_passes_when_it_fails_fails(self):
        code, out = self.gate("    timeout-minutes: 60\n", "    timeout-minutes: 60\n    continue-on-error: true\n")
        self.assertEqual(code, 1, out)
        self.assertIn("bootstrap passes even when it fails", out)

    def test_a_release_created_that_could_become_latest_fails(self):
        code, out = self.gate(" --prerelease --latest=false ", " --prerelease ")
        self.assertEqual(code, 1, out)
        self.assertIn("is not created as a pre-release kept from «Latest»", out)

    def test_a_workflow_that_never_makes_a_release_latest_fails(self):
        code, out = self.gate(" --prerelease=false --latest ", " ")
        self.assertEqual(code, 1, out)
        self.assertIn("no job makes a release «Latest»", out)


class DockerPackages(unittest.TestCase):
    """aps/bootstrap/install.sh: Docker's packages for a system, as its docker_repo names them. A derivative takes the
    Ubuntu or Debian release it is built on, as its own codename has no packages."""

    FUNCTION = re.search(r"^docker_repo\(\) \(.*?^\)$", (REPO / "aps" / "bootstrap" / "install.sh").read_text(),
                         re.S | re.M).group(0)

    def repo(self, os_release, debian_version=None):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "os-release").write_text(os_release)
            if debian_version:
                Path(tmp, "debian_version").write_text(debian_version)
            env = {"OS_RELEASE": f"{tmp}/os-release", "DEBIAN_VERSION": f"{tmp}/debian_version"}
            return subprocess.run(["bash", "-c", self.FUNCTION + "\ndocker_repo"], env=env, capture_output=True,
                                  text=True, check=True).stdout.strip()

    def test_debian_and_ubuntu_take_their_own_codename(self):
        self.assertEqual(self.repo("ID=debian\nVERSION_CODENAME=bookworm\n"), "debian bookworm")
        self.assertEqual(self.repo("ID=ubuntu\nVERSION_CODENAME=noble\nUBUNTU_CODENAME=noble\n"), "ubuntu noble")

    def test_a_derivative_takes_the_release_it_is_built_on(self):
        lmde = 'PRETTY_NAME="LMDE 7 (gigi)"\nID=linuxmint\nID_LIKE=debian\nVERSION_CODENAME=gigi\nDEBIAN_CODENAME=trixie\n'
        mint = "ID=linuxmint\nID_LIKE=\"ubuntu debian\"\nVERSION_CODENAME=wilma\nUBUNTU_CODENAME=noble\n"
        self.assertEqual(self.repo(lmde), "debian trixie")
        self.assertEqual(self.repo(mint), "ubuntu noble")

    def test_a_debian_derivative_naming_no_release_takes_it_from_debian_version(self):
        self.assertEqual(self.repo("ID=mx\nID_LIKE=debian\nVERSION_CODENAME=libretto\n", "12.7\n"), "debian bookworm")

    def test_another_system_gets_none(self):
        self.assertEqual(self.repo("ID=fedora\nVERSION_CODENAME=\n"), "")


class BootstrapRefusals(unittest.TestCase):
    """aps/bootstrap/install.sh, as a release fills it in, as root on a server with Docker: another installation, or
    the configuration one left, is refused before anything changes; an older release of ours is upgraded, and an
    upgrade the bootstrap cut short is retaken. `docker`, `id`, `curl`, `python3`, `systemctl` and `qrencode` are fakes
    first on PATH; each answers only the calls the case names and fails loudly on any other, and every call is
    recorded. A `@DIR@` in a call's name stands for the test's own directory."""

    TAG = "v1.0.1"
    IMAGE = "ghcr.io/aps-conecta/gestion-mastercontainer"
    DIGEST = f"{IMAGE}@sha256:{'a' * 64}"
    INSPECT = "docker container inspect -f {{.Config.Image}} nextcloud-aio-mastercontainer"
    VOLUME = "docker volume inspect nextcloud_aio_mastercontainer"
    RUN = ("docker run --init --sig-proxy=false --name nextcloud-aio-mastercontainer --restart always --detach "
           "--publish 80:80 --publish 8080:8080 --publish 8443:8443 "
           "--volume nextcloud_aio_mastercontainer:/mnt/docker-aio-config "
           f"--volume /var/run/docker.sock:/var/run/docker.sock:ro {IMAGE}:{TAG}")

    def bootstrap(self, answers, holds=(), dri=False, files=None, command=("install.sh",)):
        """The exit code, the output and the calls made of the release's install.sh, with `answers` as
        {call: (exit code, output)} (nothing answers a call that fails loudly); `holds` names files the server already
        holds, with their text, under the install folder, where the bundle's mastercontainer start always is; `dri`,
        whether the server has render devices; `files`, the staff's files folder the Provisionador stored; `command`,
        what runs, relative to the test's own directory; and what that folder's parent holds afterwards besides the
        test's own files."""
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        if dri:
            (tmp / "dri").mkdir()
        if files:
            (tmp / "files-folder").write_text(f"{files}\n", encoding="utf-8")
        for source, target, replaced in (
                ("install.sh", tmp / "install.sh",
                 (('\nRELEASE_TAG=""\n', f'\nRELEASE_TAG="{self.TAG}"\n'),
                  ('\nBUNDLE_SHA256=""\n', f'\nBUNDLE_SHA256="{"0" * 64}"\n'),
                  ("\nINSTALL_DIR=/opt/aps-conecta/gestion\n", f"\nINSTALL_DIR={tmp}/gestion\n"))),
                ("mastercontainer.sh", tmp / "gestion" / "bootstrap" / "mastercontainer.sh",
                 (("\nDRI=/dev/dri ", f"\nDRI={tmp}/dri "),
                  ("\nFILES=/var/lib/aps-conecta/files-folder ", f"\nFILES={tmp}/files-folder ")))):
            text = (REPO / "aps" / "bootstrap" / source).read_text(encoding="utf-8")
            for old, new in replaced:
                self.assertEqual(text.count(old), 1, old)
                text = text.replace(old, new)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        for name, content in holds:
            path = tmp / "gestion" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        answers = {call.replace("@DIR@", str(tmp)): value for call, value in answers.items()}
        fake = ["#!/bin/sh", 'printf "%s\\n" "${0##*/} $*" >> "$CALLS"', 'case "${0##*/} $*" in']
        fake += [f"  {shlex.quote(call)}) printf %s {shlex.quote(out)}; exit {code} ;;"
                 for call, (code, out) in {"id -u": (0, "0"), **answers}.items()]
        fake += ['  *) echo "fake: unexpected call: ${0##*/} $*" >&2; exit 99 ;;', "esac", ""]
        (tmp / "bin").mkdir()
        for name in ("docker", "id", "curl", "python3", "systemctl", "qrencode"):
            (tmp / "bin" / name).write_text("\n".join(fake), encoding="utf-8")
            (tmp / "bin" / name).chmod(0o755)
        (tmp / "calls").touch()
        r = subprocess.run([shutil.which("bash"), str(tmp / command[0]), *command[1:]], capture_output=True,
                           encoding="utf-8",
                           env={"PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", "CALLS": str(tmp / "calls")})
        made = sorted({p.name for p in tmp.iterdir()} - {"install.sh", "bin", "calls", "gestion", "dri", "files-folder"})
        return r.returncode, r.stdout + r.stderr, (tmp / "calls").read_text().splitlines(), made

    def test_another_aio_installation_is_refused_naming_its_image(self):
        code, out, calls, made = self.bootstrap({self.INSPECT: (0, "nextcloud/all-in-one:latest")})
        self.assertEqual((code, calls, made), (1, ["id -u", self.INSPECT], []), out)
        self.assertIn("ya tiene otra instalación de Nextcloud All-in-One (imagen nextcloud/all-in-one:latest)", out)
        self.assertIn("Elimínela, o use otro servidor, antes de volver a ejecutar el instalador. No se modificó nada.",
                      out)

    def test_a_newer_release_of_ours_is_refused_naming_the_latest_installer(self):
        code, out, calls, made = self.bootstrap({self.INSPECT: (0, f"{self.IMAGE}:v1.0.2")})
        self.assertEqual((code, calls, made), (1, ["id -u", self.INSPECT], []), out)
        self.assertIn("ya tiene APS Conecta Gestión v1.0.2, una versión más nueva que v1.0.1. Use el instalador de la "
                      "versión vigente:\n  curl -fsSL https://github.com/APS-Conecta/gestion-aio/releases/latest/"
                      "download/install.sh | sudo bash\nNo se modificó nada.", out)

    def test_an_older_release_of_ours_is_upgraded_not_refused(self):
        code, out, calls, made = self.bootstrap({self.INSPECT: (0, f"{self.IMAGE}:v1.0.0")})
        self.assertNotEqual(code, 0, out)
        self.assertIn("Actualizando APS Conecta Gestión de v1.0.0 a v1.0.1...", out)
        self.assertIn("Descargando y verificando APS Conecta Gestión v1.0.1...", out)
        self.assertEqual(made, [], "something was unpacked before the bundle's checksum verified")
        self.assertTrue(any(call.startswith("curl -fsSL --retry 3 -o ") for call in calls), calls)

    def test_a_server_with_render_devices_hands_them_to_the_mastercontainer_for_aio_to_pass_on(self):
        run = self.RUN.replace(f" {self.IMAGE}:{self.TAG}", f" --device @DIR@/dri {self.IMAGE}:{self.TAG}")
        answers = {self.INSPECT: (1, ""), self.VOLUME: (0, "[]"),
                   f"docker pull --quiet {self.DIGEST}": (0, ""),
                   f"docker tag {self.DIGEST} {self.IMAGE}:{self.TAG}": (0, ""),
                   "docker rm -f nextcloud-aio-mastercontainer": (0, ""), run: (0, ""),
                   "systemctl stop aps-provisionador.service": (0, ""),
                   "python3 @DIR@/gestion/provisionador/provisionador.py upgrade": (1, "")}
        code, out, calls, made = self.bootstrap(answers, holds=(("VERSION", f"{self.TAG}\n"),
                                                                ("images.lock", f"{self.DIGEST}\n")), dri=True)
        self.assertEqual(code, 1, out)  # the fake wizard's upgrade refuses: nothing after it matters here
        runs = [call for call in calls if call.startswith("docker run ")]
        self.assertEqual(len(runs), 1, calls)
        self.assertTrue(runs[0].endswith(f"/dri {self.IMAGE}:{self.TAG}") and " --device " in runs[0], runs)

    def test_an_upgrade_the_bootstrap_cut_short_is_retaken(self):
        # the server holds this release's bundle, its volume and the wizard's state, but no mastercontainer; the
        # wizard's own marking refuses (an install that never finished), and nothing else is touched
        answers = {self.INSPECT: (1, ""), self.VOLUME: (0, "[]"),
                   f"docker pull --quiet {self.DIGEST}": (0, ""),
                   f"docker tag {self.DIGEST} {self.IMAGE}:{self.TAG}": (0, ""),
                   "docker rm -f nextcloud-aio-mastercontainer": (0, ""), self.RUN: (0, ""),
                   "systemctl stop aps-provisionador.service": (0, ""),
                   "python3 @DIR@/gestion/provisionador/provisionador.py upgrade":
                       (1, "La instalación de este servidor no terminó...")}
        code, out, calls, made = self.bootstrap(answers, holds=(("VERSION", f"{self.TAG}\n"),
                                                                ("images.lock", f"{self.DIGEST}\n")))
        self.assertEqual(code, 1, out)
        for call in (f"docker pull --quiet {self.DIGEST}", f"docker tag {self.DIGEST} {self.IMAGE}:{self.TAG}",
                     "docker rm -f nextcloud-aio-mastercontainer", self.RUN,
                     "systemctl stop aps-provisionador.service"):
            self.assertIn(call, calls)
        self.assertTrue(calls[-1].startswith("python3 ") and calls[-1].endswith(" upgrade"), calls)
        self.assertIn("no se pudo preparar la actualización", out)
        self.assertEqual(made, [])

    def started(self, files=None, command=("install.sh",)):
        """The `docker run` calls of a mastercontainer start, through an upgrade the bootstrap cut short or through
        `command`, on a server whose Provisionador stored the staff's files folder `files`, or none."""
        run = self.RUN.replace(f" {self.IMAGE}:{self.TAG}", f"{{files}} {self.IMAGE}:{self.TAG}")
        run = run.replace("{files}", f" --env NEXTCLOUD_DATADIR={files}" if files else "")
        answers = {self.INSPECT: (1, ""), self.VOLUME: (0, "[]"),
                   f"docker pull --quiet {self.DIGEST}": (0, ""),
                   f"docker tag {self.DIGEST} {self.IMAGE}:{self.TAG}": (0, ""),
                   "docker rm -f nextcloud-aio-mastercontainer": (0, ""), run: (0, ""),
                   "systemctl stop aps-provisionador.service": (0, ""),
                   "python3 @DIR@/gestion/provisionador/provisionador.py upgrade": (1, "")}
        code, out, calls, _ = self.bootstrap(answers, holds=(("VERSION", f"{self.TAG}\n"),
                                                             ("images.lock", f"{self.DIGEST}\n")),
                                             files=files, command=command)
        return [call for call in calls if call.startswith("docker run ")], run, out

    def test_every_start_passes_the_staff_files_folder_the_provisionador_stored_to_aio(self):
        files = "/mnt/aps-conecta/5b0c/aps-conecta/archivos"
        for command in (("install.sh",), ("gestion/bootstrap/mastercontainer.sh", "keep")):
            runs, run, out = self.started(files, command)
            self.assertEqual(runs, [run], out)
            self.assertIn(f" --env NEXTCLOUD_DATADIR={files} ", run)

    def test_with_no_stored_folder_aio_keeps_the_staff_files_in_its_own_volume(self):
        runs, run, out = self.started()
        self.assertEqual(runs, [self.RUN], out)

    def test_a_volume_another_installation_left_is_refused_on_a_fresh_install(self):
        code, out, calls, made = self.bootstrap({self.INSPECT: (1, ""), self.VOLUME: (0, "[]")})
        self.assertEqual((code, calls, made), (1, ["id -u", self.INSPECT, self.VOLUME], []), out)
        self.assertIn("No se modificó nada.", out)
        self.assertIn("  - Docker conserva el volumen nextcloud_aio_mastercontainer de una instalación anterior de"
                      " Nextcloud All-in-One, que el instalador no toca; elimínelo, o use otro servidor, antes de"
                      " volver a ejecutar el instalador\n", out)


if __name__ == "__main__":
    unittest.main()
