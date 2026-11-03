# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behaviour of the static gates, driven through their command lines against throwaway repos."""

import json
import re
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


class ReleaseBundle(unittest.TestCase):
    """aps/release/package.sh: only a test build's bundle carries Let's Encrypt's staging roots, one per file, for the
    server to trust; a release's bundle carries what it always did, with production's services alone."""

    PINS = [f"ghcr.io/aps-conecta/{image}@sha256:{n * 64}"
            for image, n in (("gestion-mastercontainer", "0"), ("gestion-nextcloud", "1"), ("gestion-caddy", "2"))]

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
                         {"VERSION", "images.lock", "services.json", "provisionador", "deis"})
        self.assertNotIn(b"STAGING", b"".join(files.values()))
        self.assertEqual(json.loads(files["services.json"]), {
            "url": "https://gestion-nombres.apsconecta.workers.dev", "suffix": "gestion.apsconecta.cl",
            "acme_ca": "https://acme-v02.api.letsencrypt.org/directory"})

    def test_a_test_build_carries_each_staging_root_the_test_browser_trusts_in_a_file_of_its_own(self):
        files = self.bundle("v0.0.0-ci.1")
        roots = re.findall(rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----\n",
                           (REPO / "aps" / "acceptance" / "letsencrypt-staging-roots.pem").read_bytes(), re.S)
        trusted = {name: text for name, text in files.items() if name.startswith("trusted-cacerts/")}
        self.assertEqual(len(roots), 4)
        self.assertTrue(all(name.endswith(".crt") for name in trusted), trusted.keys())
        self.assertEqual(sorted(trusted.values()), sorted(roots))
        self.assertIn(b"acme-staging-v02", files["services.json"])


if __name__ == "__main__":
    unittest.main()
