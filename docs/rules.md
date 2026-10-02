# Rule reference

Gate Drift reports recognizable changes to tests and automated quality gates. Findings are prompts for review, not proof of intent, correctness, or test adequacy. High and medium are review priorities, not measured probabilities.

The scanner compares the before and after text of changed, tracked files. For added markers, it counts occurrences per file after removing whitespace from the matched syntax. Existing occurrences normally stay quiet when moved; changing the arguments or scope of an existing marker may not produce a finding. Coverage rules compare numeric values under recognized keys.

## `test-skip` — medium

Reports newly added skipping or expected-failure markers in recognized test files:

- JavaScript and TypeScript: `it.skip`, `test.skip`, `describe.skip`, their `skipIf` and `runIf` modifiers, `xit(...)`, `xtest(...)`, and `xdescribe(...)`. Static named imports of `test`, `it`, or `describe` from the literal module `vitest` also establish aliases, such as `import { test as baseTest } from 'vitest'` followed by `baseTest.skipIf(condition)`.
- Vitest test contexts: `context.skip(...)` inside a recognized `test` or `it` call with a simple arrow callback whose single parameter names that context. The test factory must be a static named import from `vitest`; a method on an arbitrary object named `ctx` is not sufficient.
- Python: `pytest.mark.skip`, `pytest.mark.skipif`, `pytest.mark.xfail`, `pytest.skip`, `pytest.xfail`, `unittest.skip`, `unittest.skipIf`, `unittest.skipUnless`, and `self.skipTest`.
- Go: `t.Skip`, `t.Skipf`, `t.SkipNow`, and the same calls on a variable named `tb`.

Comments, string literals, and fixture template strings are masked before matching. Conditional skips and expected failures are still review signals; Gate Drift does not evaluate their condition, justification, or framework settings. The Vitest extension handles static named imports and simple arrow callbacks with balanced call and body delimiters. Context calls inside nested helper functions, reassigned contexts, destructured or typed callback parameters, curried conditional callbacks, namespace/default imports, computed properties, and custom wrappers are outside this limited syntax. This is a text scanner with bounded callback scopes, not a full JavaScript or TypeScript syntax tree or binding analysis.

Common regular-expression literals are masked using expression context. Simple parameter shadows and nested method bodies are excluded, but unusual regex/division positions, factory rebinding, and more complex lexical scopes can still be misclassified. Reassigning the context anywhere in a callback body, including a nested helper, conservatively excludes that entire callback and can hide an otherwise recognizable skip.

## `test-focus` — high

Reports new `it.only`, `test.only`, or `describe.only` markers in recognized JavaScript or TypeScript test files, including aliases established by static named imports from `vitest`. A focused selection can stop the rest of a suite from running.

Other alias forms, custom wrappers, and other frameworks' focus syntax are not supported.

## `deleted-test-file` — high

Reports a recognized test source file whose before text is nonempty and whose after text is empty. This includes deletion and emptying a file. Review whether another file preserves its checks.

Deleting individual test functions or assertions is not detected. Moving checks between files can still deserve a finding; Gate Drift does not establish equivalent coverage across files.

## `continue-on-error` — high

Reports an added `continue-on-error: true` in a `.github/workflows/*.yml` or `.yaml` file. Literal quoted `true` values are also recognized.

This rule uses a small YAML-aware scanner. It ignores comments and block scalar contents, and does not evaluate expressions, anchors, job dependencies, or matrix behavior. A step or job intentionally allowed to fail will also be reported.

## `swallowed-check` — high

Reports a newly added recognized check command containing `|| true` or `; exit 0` in a GitHub Actions `run` command or a `package.json` script. Quoted messages containing those operators are ignored.

Recognized command forms include `pytest`, `jest`, `vitest`, `eslint`, `tsc`, `mypy`, `pyright`, `unittest`, `python -m pytest`, `python -m unittest`, `go test`, `cargo test`, and `ruff check`. Common `npm`, `pnpm`, `yarn`, `bun`, and `make` invocations of test, lint, and type-check tasks are recognized, along with some `npx` and `uv run` prefixes. Package scripts named `test`, `lint`, `typecheck`, or `type-check`, optionally followed by a colon suffix, are treated as checks.

This is pattern matching, not a shell interpreter. Custom executables, wrappers, control flow, indirect script calls, and other ways to hide an exit status can be missed. A modified command that already swallowed failure may appear as a new occurrence.

## `coverage-lowered` — high

Reports a recognized numeric threshold decreased under the same key. Examples include:

- `--cov-fail-under` in supported config or workflow files.
- `fail_under` in `.coveragerc`'s `[report]`, `[tool.coverage.report]` in `pyproject.toml`, or `[coverage:report]` in supported INI files.
- `lines`, `statements`, `branches`, and `functions` under Jest's `coverageThreshold`, Vitest's `coverage.thresholds`, or NYC configuration.

The rule recognizes common `package.json`, Python coverage config, Jest/Vitest/NYC config, and workflow filenames. It does not execute configuration files, resolve imports, interpret arithmetic or variables, compare external coverage reports, or detect a threshold removed entirely. Changes to dynamic or inherited configuration can be missed. Repeated thresholds under identical keys are paired heuristically.

## `typescript-strict-disabled` — high

Reports an explicit `"strict": true` changed to `"strict": false` in a `tsconfig*.json`, `tsconfig*.jsonc`, `jsconfig*.json`, or `jsconfig*.jsonc` file. An added `false` without a previous explicit `true` stays quiet.

Options must appear directly inside `compilerOptions`. Gate Drift does not resolve `extends`, compiler defaults, overrides, or project references. Removing an explicit `strict` value is not detected.

## `typescript-nocheck-enabled` — high

Reports a new literal `"noCheck": true` in the same TypeScript/JavaScript config filename patterns. It does not determine whether the compiler version supports this option, or how inherited configuration affects the project.

## `typescript-suppression` — medium

Reports newly added `@ts-ignore` or `@ts-nocheck` directives in actual JavaScript or TypeScript comment tokens. Strings and prose mentions are ignored. `@ts-nocheck` must be a leading single-line pragma, allowing a shebang. `@ts-expect-error` is not flagged.

The rule does not determine whether a directive is required or whether the suppressed error is meaningful.

## `eslint-suppression` — medium

Reports newly added `eslint-disable`, `eslint-disable-next-line`, or `eslint-disable-line` directives in actual JavaScript or TypeScript comments.

Changing the list of disabled rules on an existing directive can be missed. Gate Drift does not load ESLint configuration or determine whether a directive has any effect.

## Test file recognition

Supported source extensions are `.js`, `.jsx`, `.ts`, `.tsx`, `.mjs`, `.cjs`, `.mts`, `.cts`, `.py`, and `.go`. A file is treated as a test when it is under `test/`, `tests/`, or `__tests__/`, starts with `test_`, ends in a `.test`, `_test`, `-test`, `.spec`, `_spec`, or `-spec` filename suffix before its extension, or ends in `_test.go`.

Custom test discovery layouts may be missed. Files with test-like names can be classified even if a framework does not run them. The detector does not run your test runner.

## Review intentional changes

Ignore a rule explicitly when appropriate:

```sh
python3 -m gate_drift --base main --ignore test-skip
```

`--ignore` is repeatable. Reports record ignored rule IDs and the number of omitted findings. An ignore applies to the whole rule for that invocation, not one line or file. For a report without failing the command, use `--no-fail`.

The default exit codes are `0` when no unignored findings exist, `1` when findings exist, and `2` when inspection fails. Text, JSON, Markdown, and offline HTML output are available. HTML and other reports can contain source snippets; review them before sharing.

## Related work

[GreenLie](https://github.com/adindamochamad/GreenLie) focuses on assertion weakening. [VibeGuard](https://github.com/dgenio/vibeguard) offers a broader offline review toolkit, including test-integrity checks. Gate Drift's small scope is a dependency-free report of selected changes to quality gates. These checks are not a claim of a new security technique, and no related project's code was used to implement them.
