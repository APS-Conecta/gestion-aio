# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behaviour of the static gates, driven through their command lines against throwaway repos."""

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


CODE = "999999"


class DeisCodeCheck(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({"readme.md": "r\n"})
        self.addCleanup(self.repo.close)

    def test_tracked_file_naming_a_deis_code_fails_with_its_location(self):
        self.repo.write({"aps/sites/demo.sh": "SITE_NOMBRE=x\nSITE_DEIS=" + CODE + "\n"})
        self.repo.commit()
        code, out = run_gate("deis_codes.py", self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("aps/sites/demo.sh:2", out)

    def test_other_shapes_of_a_named_code_fail(self):
        self.repo.write({"docs.md": "DEIS " + CODE + "\nrun deis.py " + CODE + "\n--deis=" + CODE + "\n"})
        self.repo.commit()
        code, out = run_gate("deis_codes.py", self.repo)
        self.assertEqual(code, 1, out)
        for n in (1, 2, 3):
            self.assertIn(f"docs.md:{n}:", out)

    def test_register_data_and_clean_text_pass(self):
        self.repo.write({
            "aps/deis/establecimientos-2026-07-23.csv": "codigo;nombre\n" + CODE + ";CESFAM\n",
            "notes.md": "the DEIS register lists every establishment\nport 8443\ndeis.slug(name)\n",
        })
        self.repo.commit()
        code, out = run_gate("deis_codes.py", self.repo)
        self.assertEqual(code, 0, out)

    def test_untracked_files_are_not_scanned(self):
        self.repo.write({"aps/sites/local.sh": "SITE_DEIS=" + CODE + "\n"})
        code, out = run_gate("deis_codes.py", self.repo)
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
