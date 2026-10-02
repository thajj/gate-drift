"""Synthetic examples; these are not claims about any real repository."""
from .engine import FileChange


def demo_changes():
    return [
        FileChange("tests/auth.test.ts", "test('rejects invalid credentials', () => {\n  expect(login('bad')).toBe(401);\n});\n", "test.skip('rejects invalid credentials', () => {\n  expect(login('bad')).toBe(401);\n});\n"),
        FileChange(".github/workflows/ci.yml", "name: CI\njobs:\n  tests:\n    runs-on: ubuntu-latest\n    steps:\n      - run: npm test\n", "name: CI\njobs:\n  tests:\n    runs-on: ubuntu-latest\n    continue-on-error: true\n    steps:\n      - run: npm test || true\n"),
        FileChange(".coveragerc", "[report]\nfail_under = 90\n", "[report]\nfail_under = 30\n"),
        FileChange("tsconfig.json", '{"compilerOptions": {"strict": true}}\n', '{"compilerOptions": {"strict": false}}\n'),
    ]
