import unittest

from gate_drift.engine import FileChange, analyze


class EngineTests(unittest.TestCase):
    def findings(self, path, before="", after=""):
        return analyze([FileChange(path, before, after)])

    def test_javascript_skip_and_focus(self):
        findings = self.findings("src/math.test.ts", after="test.skip('sum', () => {});\n describe.only('math', () => {});\nxit('old', () => {});\n")
        self.assertEqual([(f.rule_id, f.line) for f in findings], [("test-skip", 1), ("test-focus", 2), ("test-skip", 3)])
        self.assertEqual(findings[1].severity, "high")
        self.assertEqual(findings[0].side, "right")

    def test_python_and_go_skips(self):
        python = self.findings("tests/test_math.py", after="import pytest\n@pytest.mark.skipif(True, reason='unsupported')\ndef test_math():\n    pytest.xfail('later')\n")
        self.assertEqual([f.line for f in python], [2, 4])
        go = self.findings("math_test.go", after='func TestMath(t *testing.T) {\n t.Skip("later")\n}\n')
        self.assertEqual([(f.rule_id, f.line) for f in go], [("test-skip", 2)])

    def test_unittest_skips(self):
        findings = self.findings("test_math.py", after="@unittest.skip('later')\ndef test_math():\n    self.skipTest('later')\n")
        self.assertEqual(len(findings), 2)

    def test_comments_strings_and_non_test_files_are_ignored(self):
        source = '// it.skip("example")\nconst example = "test.only(123)";\n/* describe.only("example") */\n'
        self.assertEqual(self.findings("math.test.js", after=source), [])
        self.assertEqual(self.findings("src/math.ts", after='it.skip("sum", () => {});'), [])
        self.assertEqual(self.findings("README.md", after='it.skip("sum", () => {});'), [])
        python = '# pytest.skip("comment")\n"""pytest.mark.skip("docstring")"""\nexample = "pytest.xfail()"\n'
        self.assertEqual(self.findings("tests/test_docs.py", after=python), [])

    def test_moved_marker_and_whitespace_do_not_report(self):
        self.assertEqual(self.findings("renamed.test.ts", before="it.skip('a', () => {});\n", after="\n\n it . skip ('a', () => {});\n"), [])

    def test_new_duplicate_marker_reports_only_extra_occurrence(self):
        findings = self.findings("math.test.js", before="test.skip('a', () => {});", after="test.skip('a', () => {});\ntest.skip('b', () => {});")
        self.assertEqual([f.line for f in findings], [2])

    def test_replaced_marker_has_original_line_evidence(self):
        findings = self.findings("math.test.js", "test('sum', () => {});\n", "test.skip('sum', () => {});\n")
        self.assertEqual(findings[0].before, "test('sum', () => {});")
        self.assertEqual(findings[0].after, "test.skip('sum', () => {});")
        findings = self.findings("math.test.js", "test('sum', () => {});\n", "test('sum', () => {});\ntest.skip('extra', () => {});\n")
        self.assertEqual(findings[0].before, "")

    def test_replaced_workflow_flag_has_original_line_evidence(self):
        findings = self.findings(".github/workflows/ci.yml", "continue-on-error: false\n", "continue-on-error: true\n")
        self.assertEqual(findings[0].before, "continue-on-error: false")

    def test_test_file_deletion_and_other_file_deletion(self):
        findings = self.findings("tests/test_math.py", before="def test_math():\n    assert 1 == 1\n")
        self.assertEqual([(f.rule_id, f.side, f.line) for f in findings], [("deleted-test-file", "left", 1)])
        self.assertEqual(self.findings("src/math.py", before="def math(): pass\n"), [])

    def test_continue_on_error_scoped_and_comments_ignored(self):
        after = 'jobs:\n  test:\n    continue-on-error: true # temporary\n    steps:\n      - run: npm test\n'
        findings = self.findings(".github/workflows/ci.yml", after=after)
        self.assertEqual([(f.rule_id, f.line) for f in findings], [("continue-on-error", 3)])
        self.assertEqual(self.findings("notes.yml", after=after), [])
        self.assertEqual(self.findings(".github/workflows/ci.yml", after="# continue-on-error: true\n"), [])

    def test_workflow_literal_block_not_yaml_flags(self):
        after = 'steps:\n  - run: |\n      echo hello\n      continue-on-error: true\n      npm test || true\n'
        findings = self.findings(".github/workflows/ci.yaml", after=after)
        self.assertEqual([(f.rule_id, f.line) for f in findings], [("swallowed-check", 5)])

    def test_swallowed_commands_inline_block_and_package(self):
        workflow = 'steps:\n  - run: npm test || true\n  - run: |\n      pnpm run lint; exit 0\n      python -m pytest || true\n'
        findings = self.findings(".github/workflows/checks.yml", after=workflow)
        self.assertEqual([f.line for f in findings], [2, 4, 5])
        package = '{\n  "scripts": {\n    "test": "vitest || true",\n    "typecheck": "tsc; exit 0"\n  }\n}'
        findings = self.findings("package.json", after=package)
        self.assertEqual([(f.rule_id, f.line) for f in findings], [("swallowed-check", 3), ("swallowed-check", 4)])

    def test_unrelated_errors_and_echo_examples_not_checks(self):
        workflow = 'steps:\n  - run: curl localhost || true\n  - run: echo "npm test || true"\n  - run: echo pytest || true\n'
        self.assertEqual(self.findings(".github/workflows/ci.yml", after=workflow), [])
        package = '{"description": "npm test || true", "scripts": {"download": "curl url || true"}}'
        self.assertEqual(self.findings("package.json", after=package), [])

    def test_existing_swallow_and_continue_moved_are_ignored(self):
        before = "steps:\n  - run: npm test || true\n    continue-on-error: true\n"
        after = "steps:\n\n  - run: npm test || true\n    continue-on-error: true\n"
        self.assertEqual(self.findings(".github/workflows/ci.yml", before, after), [])

    def test_python_coverage_lowered_in_report_section(self):
        before = "[tool.coverage.report]\nfail_under = 90\n"
        after = "[tool.coverage.report]\nfail_under = 70\n"
        findings = self.findings("pyproject.toml", before, after)
        self.assertEqual([(f.rule_id, f.line) for f in findings], [("coverage-lowered", 2)])
        self.assertIn("90 to 70", findings[0].explanation)
        self.assertEqual(self.findings("pyproject.toml", "[tool.random]\nfail_under = 90", "[tool.random]\nfail_under = 70"), [])

    def test_pytest_cli_coverage_lowered(self):
        before = "steps:\n  - run: pytest --cov=src --cov-fail-under=90\n"
        after = "steps:\n  - run: pytest --cov=src --cov-fail-under 70\n"
        findings = self.findings(".github/workflows/ci.yml", before, after)
        self.assertEqual([(f.rule_id, f.line) for f in findings], [("coverage-lowered", 2)])

    def test_jest_and_vitest_coverage_scope(self):
        before = "export default {coverageThreshold: {global: {lines: 90, branches: 80}}};"
        after = "export default {coverageThreshold: {global: {lines: 70, branches: 80}}};"
        self.assertEqual(len(self.findings("jest.config.ts", before, after)), 1)
        before = "export default {test: {coverage: {thresholds: {lines: 90}}}};"
        after = "export default {test: {coverage: {thresholds: {lines: 70}}}};"
        self.assertEqual(len(self.findings("vitest.config.ts", before, after)), 1)
        self.assertEqual(self.findings("jest.config.ts", "export default {lines: 90};", "export default {lines: 70};"), [])

    def test_coverage_increase_and_move_not_reported(self):
        before = '{"coverageThreshold":{"global":{"lines":80,"branches":70}}}'
        after = '{\n"coverageThreshold":{"global":{"branches":70,"lines":90}}}'
        self.assertEqual(self.findings("package.json", before, after), [])

    def test_typescript_options_changed_or_added(self):
        before = '{"compilerOptions": {"strict": true, "noCheck": false}}'
        after = '{\n"compilerOptions": {\n "strict": false,\n "noCheck": true\n}}'
        findings = self.findings("tsconfig.json", before, after)
        self.assertEqual([(f.rule_id, f.line) for f in findings], [("typescript-strict-disabled", 3), ("typescript-nocheck-enabled", 4)])
        self.assertEqual(len(self.findings("tsconfig.build.json", after='{"compilerOptions":{"noCheck":true}}')), 1)
        self.assertEqual(self.findings("tsconfig.json", after='{"compilerOptions":{"strict":false}}'), [])
        self.assertEqual(self.findings("config.json", before, after), [])

    def test_typescript_option_comments_ignored(self):
        self.assertEqual(self.findings("tsconfig.json", after='{// "noCheck": true\n"compilerOptions": {}}'), [])
        self.assertEqual(self.findings("tsconfig.json", after='{"notes":{"noCheck":true}, "compilerOptions":{}}'), [])

    def test_type_and_lint_directives_added(self):
        after = '// @ts-nocheck\n// @ts-ignore: library types\n/* eslint-disable no-console */\n// eslint-disable-next-line no-alert\nconst value = "@ts-ignore and eslint-disable";\n'
        findings = self.findings("src/main.ts", after=after)
        self.assertEqual([(f.rule_id, f.line) for f in findings], [("typescript-suppression", 1), ("typescript-suppression", 2), ("eslint-suppression", 3), ("eslint-suppression", 4)])
        self.assertEqual(self.findings("README.md", after=after), [])

    def test_moved_directives_ignored(self):
        self.assertEqual(self.findings("src/main.ts", "// @ts-ignore\nconst a=1;", "const a=1;\n// @ts-ignore"), [])

    def test_directive_mentions_are_not_directives(self):
        after = '// The @ts-ignore directive hides errors.\n/* Documentation about eslint-disable usage. */\n'
        self.assertEqual(self.findings("src/main.ts", after=after), [])

    def test_nocheck_requires_leading_single_line_pragma(self):
        self.assertEqual(self.findings("src/main.ts", after="const value = 1;\n// @ts-nocheck\n"), [])
        self.assertEqual(self.findings("src/main.ts", after="/* @ts-nocheck */\nconst value = 1;\n"), [])
        findings = self.findings("src/main.ts", after="#!/usr/bin/env node\n// license\n// @ts-nocheck\nconst value = 1;\n")
        self.assertEqual([f.line for f in findings], [3])

    def test_block_ignore_requires_marker_on_last_comment_line(self):
        self.assertEqual(self.findings("src/main.ts", after="/* @ts-ignore\n explanation\n*/\nconst value = 1;"), [])
        findings = self.findings("src/main.ts", after="/* explanation\n * @ts-ignore */\nconst value = 1;\n")
        self.assertEqual([f.line for f in findings], [2])

    def test_expanded_eslint_rule_suppression_is_reported(self):
        findings = self.findings("src/main.ts", "/* eslint-disable no-console */", "/* eslint-disable no-console, no-alert */")
        self.assertEqual([f.rule_id for f in findings], ["eslint-suppression"])
        self.assertEqual(self.findings("src/main.ts", "/* eslint-disable no-console -- old reason */", "/* eslint-disable no-console -- new reason */"), [])

    def test_multiline_test_marker_reports_marker_line(self):
        findings = self.findings("math.test.ts", after="test\n .only('x', () => {});\n")
        self.assertEqual([f.line for f in findings], [2])

    def test_coverage_cli_documentation_strings_are_ignored(self):
        self.assertEqual(self.findings("package.json", '{"description":"pytest --cov-fail-under=90"}', '{"description":"pytest --cov-fail-under=20"}'), [])
        self.assertEqual(self.findings(".github/workflows/ci.yml", 'steps:\n  - run: echo "pytest --cov-fail-under=90"', 'steps:\n  - run: echo "pytest --cov-fail-under=20"'), [])
        self.assertEqual(self.findings("pyproject.toml", '[tool.random]\nexample="--cov-fail-under=90"', '[tool.random]\nexample="--cov-fail-under=20"'), [])

    def test_pytest_addopts_and_nyc_thresholds(self):
        findings = self.findings("pyproject.toml", '[tool.pytest.ini_options]\naddopts="--cov-fail-under=90"', '[tool.pytest.ini_options]\naddopts="--cov-fail-under=70"')
        self.assertEqual([(f.rule_id, f.line) for f in findings], [("coverage-lowered", 2)])
        findings = self.findings(".nycrc.json", '{"lines":90}', '{"lines":70}')
        self.assertEqual([f.rule_id for f in findings], ["coverage-lowered"])

    def test_deterministic_sorting(self):
        changes = [FileChange("z.test.js", "", "it.only('x', ()=>{});"), FileChange("a.test.js", "", "it.skip('x', ()=>{});")]
        self.assertEqual(analyze(changes), analyze(list(reversed(changes))))
        self.assertEqual([f.path for f in analyze(changes)], ["a.test.js", "z.test.js"])


if __name__ == "__main__":
    unittest.main()
