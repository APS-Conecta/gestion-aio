# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behaviour of the static gates, driven through their command lines against throwaway repos."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GATES = Path(__file__).resolve().parent


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


DEIS = GATES.parent / "provisionador" / "deis.py"
HEADER = "codigo,tipo,nombre,direccion,comuna_codigo,comuna,region_codigo,region,servicio_salud,dependencia,latitud,longitud\n"


def slug_table(rows):
    """Runs the slug-table command on a register of `rows` (code, type, name, comuna)."""
    with tempfile.TemporaryDirectory() as tmp:
        lines = [f"{c},{t},{n},Calle 1,13101,{comuna},13,Metropolitana,Servicio,Municipal,-33.4,-70.6"
                 for c, t, n, comuna in rows]
        Path(tmp, "establecimientos-deis-2026-07-23.csv").write_text(HEADER + "\n".join(lines) + "\n")
        r = subprocess.run([sys.executable, "-I", str(DEIS), "slugs", tmp], capture_output=True, text=True)
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


if __name__ == "__main__":
    unittest.main()
