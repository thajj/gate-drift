"""Regression checks for benchmark accounting and network setup safeguards.

Run with: python -m unittest discover -s scripts -p 'test_*.py'
"""
from pathlib import Path
import copy
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import fetch_validation_history as fetcher
import validate_history as validator
from gate_drift.engine import Finding


class HistoryValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gate history ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "example"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "codex/validation-test")
        self.path = self.repo / "test_a.py"
        self.path.write_text("def test_a():\n    assert True\n", encoding="utf-8")
        self.git("add", "test_a.py")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        self.path.write_text('def test_a():\n    pytest.skip("reason")\n', encoding="utf-8")
        self.git("add", "test_a.py")
        self.git("commit", "-qm", "head")
        self.head = self.git("rev-parse", "HEAD").stdout.strip()
        self.expected = {"rule_id": "test-skip", "path": "test_a.py", "line": 2, "side": "right"}
        self.case = {"repository": "owner/example", "base": self.base,
                     "head": self.head, "expected": [self.expected]}

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
             "-c", "user.name=Validation Tests", "-c", "user.email=tests@example.invalid",
             "-C", str(self.repo), *args], check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def finding(self, **overrides):
        values = dict(rule_id="test-skip", severity="warning", title="Skipped test",
                      path="test_a.py", line=2, side="right", before="",
                      after='pytest.skip("reason")', explanation="Test evidence")
        values.update(overrides)
        return Finding(**values)

    def evaluate(self, case=None):
        dataset = {"date": "test", "method": "regression fixture", "cases": [case or self.case]}
        return validator.evaluate(dataset, self.root)

    def test_real_snapshots_match_exact_source_address(self):
        report = self.evaluate()
        self.assertEqual((report["total"], report["passed"], report["expected_sites"], report["emitted_sites"]), (1, 1, 1, 1))
        self.assertEqual(report["results"][0]["invalid_addresses"], [])
        self.assertRegex(report["tool_commit"], r"^[0-9a-f]{40,64}$")
        self.assertIsInstance(report["working_tree_dirty"], bool)
        self.assertNotIn("status", report)

    def test_unknown_emitted_path_fails_without_crashing(self):
        with patch.object(validator, "analyze", return_value=[self.finding(path="missing.py")]):
            result = self.evaluate()["results"][0]
        self.assertFalse(result["passed"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["invalid_addresses"], [("test-skip", "missing.py", 2, "right")])

    def test_duplicate_emissions_cannot_pass_set_equality(self):
        with patch.object(validator, "analyze", return_value=[self.finding(), self.finding()]):
            report = self.evaluate()
        result = report["results"][0]
        self.assertFalse(result["passed"])
        self.assertEqual(report["emitted_sites"], 2)
        self.assertEqual(result["unexpected"], [validator.identity(self.expected)])

    def test_wrong_evidence_at_right_address_fails(self):
        with patch.object(validator, "analyze", return_value=[self.finding(after="unrelated source")]):
            result = self.evaluate()["results"][0]
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["unexpected"], [])
        self.assertEqual(result["invalid_addresses"], [validator.identity(self.expected)])

    def test_invalid_side_is_not_treated_as_left(self):
        with patch.object(validator, "analyze", return_value=[self.finding(side="elsewhere")]):
            result = self.evaluate()["results"][0]
        self.assertFalse(result["passed"])
        self.assertIn("Invalid rule/path/line/side", result["error"])

    def test_duplicate_gold_is_a_dataset_error(self):
        case = copy.deepcopy(self.case)
        case["expected"].append(copy.deepcopy(self.expected))
        result = self.evaluate(case)["results"][0]
        self.assertFalse(result["passed"])
        self.assertIn("duplicate expected", result["error"])

    def test_missing_git_revision_is_an_explicit_error(self):
        case = {**self.case, "base": "0" * 40}
        result = self.evaluate(case)["results"][0]
        self.assertFalse(result["passed"])
        self.assertIsNotNone(result["error"])

    def test_pinned_revision_cannot_be_silently_replaced(self):
        self.git("replace", self.head, self.base)
        # Substituting the head's tree would otherwise make this falsely quiet.
        result = self.evaluate({**self.case, "expected": []})["results"][0]
        self.assertFalse(result["passed"])
        self.assertIn("replace refs", result["error"])

    def test_selected_path_records_limited_scope(self):
        result = self.evaluate({**self.case, "paths": ["test_a.py"]})["results"][0]
        self.assertTrue(result["passed"])
        self.assertEqual(result["metadata"]["scope"], "Selected configuration files only.")

    def test_fetch_checks_explicit_base_even_if_head_is_present(self):
        present = {self.head}
        fetched = []

        def fake_git(repo, *args, **kwargs):
            if args[0] == "cat-file":
                sha = args[-1].split("^", 1)[0]
                return subprocess.CompletedProcess(args, 0 if sha in present else 1)
            if args[0] == "fetch":
                fetched.append(args)
                present.add(args[-1])
                return subprocess.CompletedProcess(args, 0)
            raise AssertionError("Unexpected command: " + repr(args))

        with patch.object(fetcher, "git", side_effect=fake_git):
            fetcher.fetch_repositories({"cases": [self.case]}, self.root)
        self.assertEqual(len(fetched), 1)
        self.assertEqual(fetched[0][-2:], ("https://github.com/owner/example.git", self.base))

    def test_fetch_git_disables_existing_repository_hooks(self):
        marker = self.root / "hook-ran"
        hook = self.repo / ".git" / "hooks" / "reference-transaction"
        hook.write_text('#!/bin/sh\n: > "' + str(marker) + '"\n', encoding="utf-8")
        hook.chmod(0o755)
        fetcher.git(self.repo, "update-ref", "refs/heads/hook-check", self.head, quiet=True)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
