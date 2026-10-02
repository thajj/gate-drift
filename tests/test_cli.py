"""Exercise the public CLI and reports as users invoke them."""
from html import escape
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gate_drift import __version__


PROJECT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(self, *args, timeout=30):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT) + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "gate_drift", *map(str, args)],
            cwd=PROJECT, env=env, capture_output=True, text=True, timeout=timeout,
        )

    def make_repo(self):
        temp = tempfile.TemporaryDirectory(prefix="gate drift cli ")
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        for args in (
            ("init", "-q"),
            ("config", "user.name", "Gate Drift Test"),
            ("config", "user.email", "gate-drift-test@example.invalid"),
        ):
            self.git(repo, *args)
        (repo / ".coveragerc").write_text("[report]\nfail_under = 90\n", encoding="utf-8")
        self.git(repo, "add", "--all")
        self.git(repo, "commit", "-q", "-m", "Baseline")
        return repo

    def git(self, repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True,
            text=True, check=True,
        ).stdout.strip()

    def test_demo_json_has_documented_schema_and_fails_on_findings(self):
        result = self.run_cli("--demo", "--format", "json")

        self.assertEqual(result.returncode, 1, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["tool"], "gate-drift")
        self.assertEqual(data["version"], __version__)
        self.assertEqual(data["metadata"]["demo"], True)
        self.assertEqual(data["metadata"]["repository"], "synthetic demo")
        self.assertGreater(data["metadata"]["files_inspected"], 0)
        self.assertEqual(data["metadata"]["excluded"], [])
        self.assertIn("No repository was inspected", data["metadata"]["scope"])
        self.assertTrue(data["findings"])
        self.assertIn("Heuristic", data["note"])
        required = {"rule_id", "severity", "path", "line", "side", "title", "before", "after", "explanation"}
        for finding in data["findings"]:
            with self.subTest(rule=finding.get("rule_id")):
                self.assertTrue(required.issubset(finding))
                self.assertIsInstance(finding["line"], int)
                self.assertGreaterEqual(finding["line"], 1)
                self.assertIn(finding["side"], {"left", "right"})

    def test_no_fail_keeps_findings_but_exits_successfully(self):
        normal = self.run_cli("--demo", "--format", "json")
        report_mode = self.run_cli("--demo", "--format", "json", "--no-fail")

        self.assertEqual(normal.returncode, 1, normal.stderr)
        self.assertEqual(report_mode.returncode, 0, report_mode.stderr)
        self.assertEqual(json.loads(report_mode.stdout), json.loads(normal.stdout))

    def test_invalid_ref_exits_two_without_producing_success_report(self):
        repo = self.make_repo()
        for args in (("--base", "no-such-ref"), ("--head", "no-such-ref")):
            with self.subTest(args=args):
                result = self.run_cli("--repo", repo, "--format", "json", *args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("inspection failed", result.stderr)

    def test_head_and_staged_are_mutually_exclusive(self):
        result = self.run_cli("--demo", "--head", "HEAD", "--staged")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("not allowed with argument", result.stderr)

    def test_ignored_findings_are_disclosed(self):
        baseline = self.run_cli("--demo", "--format", "json")
        baseline_data = json.loads(baseline.stdout)
        ignored_rule = baseline_data["findings"][0]["rule_id"]
        expected_omitted = sum(f["rule_id"] == ignored_rule for f in baseline_data["findings"])

        result = self.run_cli("--demo", "--format", "json", "--ignore", ignored_rule)
        data = json.loads(result.stdout)

        self.assertNotIn(ignored_rule, {f["rule_id"] for f in data["findings"]})
        self.assertEqual(data["metadata"]["ignored_rules"], [ignored_rule])
        self.assertEqual(data["metadata"]["ignored_findings"], expected_omitted)
        self.assertEqual(len(data["findings"]), len(baseline_data["findings"]) - expected_omitted)
        self.assertIn(ignored_rule, data["metadata"]["scope"])
        self.assertIn("finding(s) omitted", data["metadata"]["scope"])
        self.assertEqual(result.returncode, 1 if data["findings"] else 0)

    def test_ignoring_every_demo_rule_is_recorded_and_exits_zero(self):
        baseline = json.loads(self.run_cli("--demo", "--format", "json").stdout)
        rules = sorted({f["rule_id"] for f in baseline["findings"]})
        args = [arg for rule in rules for arg in ("--ignore", rule)]

        result = self.run_cli("--demo", "--format", "json", *args)
        data = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["findings"], [])
        self.assertEqual(data["metadata"]["ignored_rules"], rules)
        self.assertEqual(data["metadata"]["ignored_findings"], len(baseline["findings"]))

    def test_html_escapes_source_snippets_and_has_no_executable_scripts(self):
        repo = self.make_repo()
        path = repo / "tests" / "auth.test.ts"
        path.parent.mkdir()
        source = "<script>alert('source')</script> <img src=x onerror=alert(1)> & unsafe"
        before = f'test("{source}", () => {{}});\n'
        after = f'test.skip("{source}", () => {{}});\n'
        path.write_text(before, encoding="utf-8")
        self.git(repo, "add", "--all")
        self.git(repo, "commit", "-q", "-m", "Add test with unusual title")
        path.write_text(after, encoding="utf-8")
        report_path = repo / "review report.html"

        result = self.run_cli("--repo", repo, "--format", "json", "--html", report_path)
        document = report_path.read_text(encoding="utf-8")
        data = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(data["findings"])
        self.assertIn(escape(source, quote=True), document)
        self.assertNotIn(source, document)
        self.assertNotIn("<script", document.lower())
        self.assertNotIn("<img", document.lower())
        self.assertIn("Content-Security-Policy", document)
        self.assertIn("default-src 'none'", document)
        self.assertIn(str(report_path), result.stderr)

    def test_clean_repo_exits_zero_with_empty_findings(self):
        result = self.run_cli("--repo", self.make_repo(), "--format", "json")
        data = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["findings"], [])
        self.assertEqual(data["metadata"]["files_inspected"], 0)

    def test_crlf_only_working_tree_change_is_a_clean_report(self):
        repo = self.make_repo()
        (repo / ".gitattributes").write_bytes(b".coveragerc text eol=lf\n")
        (repo / ".coveragerc").write_bytes(b"[report]\nfail_under = 90\n")
        self.git(repo, "add", "--all")
        self.git(repo, "commit", "-q", "-m", "Store LF coverage baseline")
        (repo / ".coveragerc").write_bytes(b"[report]\r\nfail_under = 90\r\n")

        result = self.run_cli("--repo", repo, "--format", "json")
        data = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["findings"], [])
        self.assertEqual(data["metadata"]["files_inspected"], 0)
        self.assertEqual(data["metadata"]["excluded"], [])

    def test_html_write_failure_is_an_inspection_error(self):
        temp = tempfile.TemporaryDirectory(prefix="gate drift report ")
        self.addCleanup(temp.cleanup)
        output = Path(temp.name) / "missing directory" / "report.html"

        result = self.run_cli("--demo", "--html", output)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("inspection failed", result.stderr)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "Requires a Unix named pipe")
    def test_named_pipe_cannot_block_inspection(self):
        repo = self.make_repo()
        path = repo / ".coveragerc"
        path.unlink()
        os.mkfifo(path)

        result = self.run_cli("--repo", repo, "--format", "json", timeout=3)

        self.assertIn(result.returncode, {0, 2}, result.stderr)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            self.assertEqual(data["findings"], [])
            self.assertTrue(any(".coveragerc" in item for item in data["metadata"]["excluded"]))
        else:
            self.assertIn("inspection failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
