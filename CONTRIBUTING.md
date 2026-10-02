# Contributing to Gate Drift

Gate Drift looks for changes that weaken tests and automated quality checks. It is a small, offline review aid. Findings should explain a concrete change and leave the decision to the reviewer.

## Develop locally

Use Python 3.10 or newer. The runtime uses only the Python standard library.

```sh
python3 -m gate_drift --demo
python3 -m unittest discover -s tests -v
```

Keep runtime dependencies out of new features. Do not add network calls, telemetry, or execution of code from the repository being scanned.

## Report a missed change or a false positive

Open an issue with the Python version, the command you ran, the rule ID, and a small before/after example. A synthetic example is enough. Remove secrets, private paths, and private source code before sharing output or HTML reports.

Describe why the change matters, or why it is harmless. A finding is a review prompt; a clean report does not establish that a change is correct.

## Add or refine a rule

Prefer a narrow signal with a clear explanation over a broad pattern that flags ordinary code. A rule must describe the syntax it recognizes and the cases it cannot determine.

Include fixtures or tests that show:

- A minimal change that should trigger the rule.
- A legitimate change that should remain quiet.
- Existing matching syntax on an unchanged line.
- Relevant multiline, comment, and quoting cases.

Keep rule IDs stable so existing ignore lists keep working. Update [the rule reference](docs/rules.md) when behavior changes. Run the test suite and the demo before submitting a pull request.

## Scope of a pull request

Keep each pull request focused on one detector, one bug, or one user-facing improvement. Explain the before/after behavior and the verification performed. Improvements to parsing, false-positive handling, accessible reports, and useful examples are welcome.

When evaluating the tool on another project, distinguish hand-written fixtures from real changes. Do not describe a synthetic example as evidence of an agent's behavior, or claim a finding proves intent.
