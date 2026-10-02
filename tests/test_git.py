"""Integration checks against real, isolated Git repositories."""
from pathlib import Path
import os
import shlex
import subprocess
import sys
import tempfile
import unittest

from gate_drift.engine import analyze
from gate_drift.git import GitError, MAX_FILE_BYTES, read_changes


class GitSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gate drift git ")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Gate Drift Test")
        self.git("config", "user.email", "gate-drift-test@example.invalid")
        self.write(".coveragerc", "[report]\nfail_under = 90\n")
        self.commit("Baseline")

    def git(self, *args):
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args], capture_output=True,
            text=True, check=True,
        )
        return result.stdout.strip()

    def write(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def test_working_tree_and_index_are_distinct_snapshots(self):
        self.write(".coveragerc", "[report]\nfail_under = 80\n")
        self.git("add", ".coveragerc")
        self.write(".coveragerc", "[report]\nfail_under = 30\n")

        working, working_metadata = read_changes(self.repo)
        staged, staged_metadata = read_changes(self.repo, staged=True)

        self.assertEqual(len(working), 1)
        self.assertEqual(len(staged), 1)
        self.assertEqual(working[0].before, "[report]\nfail_under = 90\n")
        self.assertEqual(working[0].after, "[report]\nfail_under = 30\n")
        self.assertEqual(staged[0].before, working[0].before)
        self.assertEqual(staged[0].after, "[report]\nfail_under = 80\n")
        self.assertEqual(working_metadata["head"], "working tree")
        self.assertEqual(staged_metadata["head"], "index")

    def test_base_head_comparison_ignores_later_local_changes(self):
        base = self.git("rev-parse", "HEAD")
        self.write(".coveragerc", "[report]\nfail_under = 60\n")
        head = self.commit("Lower coverage requirement")
        self.write(".coveragerc", "[report]\nfail_under = 10\n")
        self.git("add", ".coveragerc")

        changes, metadata = read_changes(self.repo, base=base, head=head)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].before, "[report]\nfail_under = 90\n")
        self.assertEqual(changes[0].after, "[report]\nfail_under = 60\n")
        self.assertEqual(metadata["base"], base)
        self.assertEqual(metadata["head"], head)

    def test_paths_with_spaces_are_preserved(self):
        path = "tests/auth with spaces.test.ts"
        before = "test('authentication', () => {});\n"
        after = "test.skip('authentication', () => {});\n"
        self.write(path, before)
        self.commit("Add authentication test")
        self.write(path, after)

        changes, metadata = read_changes(self.repo)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].path, path)
        self.assertEqual(changes[0].before, before)
        self.assertEqual(changes[0].after, after)
        self.assertEqual(metadata["files_inspected"], 1)

    def test_pure_rename_does_not_create_a_finding(self):
        before_path = "tests/old name.test.ts"
        after_path = "tests/new name.test.ts"
        content = "test('rejects invalid credentials', () => { expect(login('bad')).toBe(401); });\n"
        self.write(before_path, content)
        self.commit("Add test")
        self.git("mv", before_path, after_path)

        changes, metadata = read_changes(self.repo)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].path, after_path)
        self.assertEqual(changes[0].before, content)
        self.assertEqual(changes[0].after, content)
        self.assertEqual(analyze(changes), [])
        self.assertEqual(metadata["files_inspected"], 1)

    def test_untracked_files_are_not_included(self):
        self.write("tests/untracked.test.ts", "test.skip('hidden', () => {});\n")

        changes, metadata = read_changes(self.repo)

        self.assertEqual(changes, [])
        self.assertEqual(metadata["files_inspected"], 0)
        self.assertIn("untracked files are not included", metadata["scope"])

    def test_new_staged_file_has_empty_before_content(self):
        path = "tests/new test.test.ts"
        after = "test.skip('new test', () => {});\n"
        self.write(path, after)
        self.git("add", path)

        changes, _ = read_changes(self.repo, staged=True)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].path, path)
        self.assertEqual(changes[0].before, "")
        self.assertEqual(changes[0].after, after)

    def test_deleted_file_has_empty_after_content(self):
        (self.repo / ".coveragerc").unlink()

        changes, _ = read_changes(self.repo)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].before, "[report]\nfail_under = 90\n")
        self.assertEqual(changes[0].after, "")

    def test_binary_changes_are_disclosed_as_excluded(self):
        path = self.repo / "fixture.bin"
        path.write_bytes(b"before\0binary")
        self.commit("Add binary fixture")
        path.write_bytes(b"after\0binary")

        changes, metadata = read_changes(self.repo)

        self.assertEqual(changes, [])
        self.assertEqual(metadata["excluded"], ["fixture.bin (binary)"])

    def test_symbolic_link_is_not_followed_into_another_directory(self):
        target = self.repo / ".coveragerc"
        target.unlink()
        target.symlink_to(self.repo / "outside.ini")
        self.write("outside.ini", "[report]\nfail_under = 0\n")

        changes, metadata = read_changes(self.repo)

        self.assertEqual(changes, [])
        self.assertEqual(metadata["excluded"], [".coveragerc (symbolic link)"])

    def test_symlinked_parent_directory_is_not_followed(self):
        relative = "tests/auth.test.ts"
        self.write(relative, "test('authentication', () => {});\n")
        self.commit("Add test")
        external = tempfile.TemporaryDirectory(prefix="gate drift external ")
        self.addCleanup(external.cleanup)
        (Path(external.name) / "auth.test.ts").write_text("test.skip('outside repository', () => {});\n", encoding="utf-8")
        (self.repo / relative).unlink()
        (self.repo / "tests").rmdir()
        (self.repo / "tests").symlink_to(external.name, target_is_directory=True)

        changes, metadata = read_changes(self.repo)

        self.assertEqual(changes, [], "Source outside the repository must not be inspected through a parent symlink")
        self.assertTrue(any(relative in item for item in metadata["excluded"]))

    def test_invalid_base_and_head_refs_are_errors(self):
        for kwargs in ({"base": "no-such-ref"}, {"head": "no-such-ref"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(GitError):
                    read_changes(self.repo, **kwargs)

    def test_working_tree_inspection_does_not_execute_git_clean_filters(self):
        self.write(".gitattributes", ".coveragerc filter=tripwire\n")
        self.commit("Configure a repository filter")
        marker = self.repo / "filter-executed"
        script = self.repo / "untrusted-filter.py"
        script.write_text(
            "from pathlib import Path\nimport sys\n"
            f"Path({str(marker)!r}).touch()\n"
            "sys.stdout.buffer.write(sys.stdin.buffer.read())\n",
            encoding="utf-8",
        )
        self.git("config", "filter.tripwire.clean", shlex.quote(sys.executable) + " " + shlex.quote(str(script)))
        self.write(".coveragerc", "[report]\nfail_under = 30\n")

        changes, _ = read_changes(self.repo)

        self.assertFalse(marker.exists(), "Inspecting a repository must not execute its clean filter")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].after, "[report]\nfail_under = 30\n")

    def test_oversized_working_tree_file_is_an_error(self):
        (self.repo / ".coveragerc").write_bytes(b"a" * (MAX_FILE_BYTES + 1))

        with self.assertRaisesRegex(GitError, "2 MiB"):
            read_changes(self.repo)

    def test_inspection_does_not_execute_fsmonitor_hook(self):
        marker = self.repo / "fsmonitor-executed"
        script = self.repo / "untrusted-fsmonitor"
        script.write_text(
            "#!/bin/sh\n"
            f"touch {shlex.quote(str(marker))}\n"
            "printf 'test-token\\0'\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        self.git("config", "core.fsmonitor", shlex.quote(str(script)))
        self.write(".coveragerc", "[report]\nfail_under = 30\n")
        self.git("status", "--short")
        self.assertTrue(marker.exists(), "The fsmonitor fixture must be active")
        marker.unlink()

        changes, _ = read_changes(self.repo)

        self.assertFalse(marker.exists(), "Inspecting a repository must not execute its fsmonitor hook")
        self.assertEqual(len(changes), 1)

    def test_oversized_base_file_is_an_error(self):
        (self.repo / ".coveragerc").write_bytes(b"a" * (MAX_FILE_BYTES + 1))
        self.commit("Store oversized fixture")
        self.write(".coveragerc", "[report]\nfail_under = 0\n")

        with self.assertRaisesRegex(GitError, "2 MiB"):
            read_changes(self.repo)

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "Requires Unix file permissions without root bypass")
    def test_unreadable_working_tree_file_is_an_error(self):
        self.write(".coveragerc", "[report]\nfail_under = 0\n")
        path = self.repo / ".coveragerc"
        path.chmod(0)
        try:
            with self.assertRaises(GitError):
                read_changes(self.repo)
        finally:
            path.chmod(0o600)


if __name__ == "__main__":
    unittest.main()
