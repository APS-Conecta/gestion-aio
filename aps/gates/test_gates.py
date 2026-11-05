# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behaviour of the static gates and the release bundle, driven through their command lines."""

import json
import os
import re
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


DEIS = GATES.parent / "provisionador" / "deis.py"
HEADER = "codigo,tipo,nombre,direccion,comuna_codigo,comuna,region_codigo,region,servicio_salud,dependencia,latitud,longitud\n"


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
                         {"VERSION", "images.lock", "services.json", "provisionador", "deis", "brand", "theme"})
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
    PUBLISH = "needs: [images, release, bootstrap, host-checks, acceptance]\n    if: inputs.version != ''\n"

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
        code, out = self.gate(self.PUBLISH, self.PUBLISH.replace(", acceptance]", "]"))
        self.assertEqual(code, 1, out)
        self.assertIn("publish makes a release «Latest» without waiting for acceptance", out)

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
    the configuration one left, is refused before anything changes. `docker`, `id` and `curl` are fakes first on PATH;
    each answers only the calls the case names and fails loudly on any other, and every call is recorded."""

    TAG = "v1.0.1"
    INSPECT = "docker container inspect -f {{.Config.Image}} nextcloud-aio-mastercontainer"
    VOLUME = "docker volume inspect nextcloud_aio_mastercontainer"

    def bootstrap(self, answers):
        """The exit code, the output and the calls made of the release's install.sh, with `answers` as
        {call: (exit code, output)}; and what its folder's parent holds afterwards besides the test's own files."""
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        text = (REPO / "aps" / "bootstrap" / "install.sh").read_text(encoding="utf-8")
        for old, new in (('\nRELEASE_TAG=""\n', f'\nRELEASE_TAG="{self.TAG}"\n'),
                         ('\nBUNDLE_SHA256=""\n', f'\nBUNDLE_SHA256="{"0" * 64}"\n'),
                         ("\nINSTALL_DIR=/opt/aps-conecta/gestion\n", f"\nINSTALL_DIR={tmp}/gestion\n")):
            self.assertEqual(text.count(old), 1, old)
            text = text.replace(old, new)
        (tmp / "install.sh").write_text(text, encoding="utf-8")
        fake = ["#!/bin/sh", 'printf "%s\\n" "${0##*/} $*" >> "$CALLS"', 'case "${0##*/} $*" in']
        fake += [f"  {shlex.quote(call)}) printf %s {shlex.quote(out)}; exit {code} ;;"
                 for call, (code, out) in {"id -u": (0, "0"), **answers}.items()]
        fake += ['  *) echo "fake: unexpected call: ${0##*/} $*" >&2; exit 99 ;;', "esac", ""]
        (tmp / "bin").mkdir()
        for name in ("docker", "id", "curl"):
            (tmp / "bin" / name).write_text("\n".join(fake), encoding="utf-8")
            (tmp / "bin" / name).chmod(0o755)
        (tmp / "calls").touch()
        r = subprocess.run([shutil.which("bash"), str(tmp / "install.sh")], capture_output=True, encoding="utf-8",
                           env={"PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", "CALLS": str(tmp / "calls")})
        made = sorted({p.name for p in tmp.iterdir()} - {"install.sh", "bin", "calls"})
        return r.returncode, r.stdout + r.stderr, (tmp / "calls").read_text().splitlines(), made

    def test_another_aio_installation_is_refused_naming_its_image(self):
        code, out, calls, made = self.bootstrap({self.INSPECT: (0, "nextcloud/all-in-one:latest")})
        self.assertEqual((code, calls, made), (1, ["id -u", self.INSPECT], []), out)
        self.assertIn("ya tiene otra instalación de Nextcloud All-in-One (imagen nextcloud/all-in-one:latest)", out)
        self.assertIn("Elimínela, o use otro servidor, antes de volver a ejecutar el instalador. No se modificó nada.",
                      out)

    def test_another_release_of_ours_is_refused_naming_the_command_that_opens_its_wizard(self):
        ours = "ghcr.io/aps-conecta/gestion-mastercontainer"
        code, out, calls, made = self.bootstrap({self.INSPECT: (0, f"{ours}:v1.0.0")})
        self.assertEqual((code, calls, made), (1, ["id -u", self.INSPECT], []), out)
        self.assertIn("ya tiene APS Conecta Gestión v1.0.0. Para abrir su asistente, ejecute:\n"
                      "  curl -fsSL https://github.com/APS-Conecta/gestion-aio/releases/download/v1.0.0/install.sh"
                      " | sudo bash\nNo se ofrece actualizar una instalación. No se modificó nada.", out)

    def test_a_volume_another_installation_left_is_refused_on_a_fresh_install(self):
        code, out, calls, made = self.bootstrap({self.INSPECT: (1, ""), self.VOLUME: (0, "[]")})
        self.assertEqual((code, calls, made), (1, ["id -u", self.INSPECT, self.VOLUME], []), out)
        self.assertIn("No se modificó nada.", out)
        self.assertIn("  - Docker conserva el volumen nextcloud_aio_mastercontainer de una instalación anterior de"
                      " Nextcloud All-in-One, que el instalador no toca; elimínelo, o use otro servidor, antes de"
                      " volver a ejecutar el instalador\n", out)


if __name__ == "__main__":
    unittest.main()
