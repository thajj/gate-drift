import argparse
from pathlib import Path
import sys

from . import __version__
from .demo import demo_changes
from .engine import analyze
from .git import GitError, read_changes
from .report import html_report, json_report, markdown_report, text_report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Review changes that may weaken the checks behind green CI.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--demo", action="store_true", help="Inspect a built-in synthetic change.")
    parser.add_argument("--repo", default=".", help="Git repository to inspect (default: current folder).")
    parser.add_argument("--base", default="HEAD", help="Base commit/ref (default: HEAD).")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--head", help="Compare this commit/ref with base. Default: tracked working tree.")
    target.add_argument("--staged", action="store_true", help="Compare the staged index with base.")
    parser.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    parser.add_argument("--html", type=Path, help="Also write a standalone offline HTML report.")
    parser.add_argument("--ignore", action="append", default=[], metavar="RULE_ID", help="Explicitly ignore a rule; repeatable and recorded in the report.")
    parser.add_argument("--no-fail", action="store_true", help="Exit 0 even when findings exist (report mode).")
    args = parser.parse_args(argv)
    try:
        if args.demo:
            changes = demo_changes()
            metadata = {"repository": "synthetic demo", "base": "before", "head": "after", "demo": True,
                        "files_inspected": len(changes), "excluded": [], "scope": "Synthetic examples only. No repository was inspected."}
        else:
            changes, metadata = read_changes(args.repo, args.base, args.head, args.staged)
        all_findings = analyze(changes)
        findings = [f for f in all_findings if f.rule_id not in args.ignore]
        metadata["ignored_rules"] = sorted(set(args.ignore))
        metadata["ignored_findings"] = len(all_findings) - len(findings)
        if args.ignore:
            metadata["scope"] += " Ignored rules: " + ", ".join(metadata["ignored_rules"]) + f" ({metadata['ignored_findings']} finding(s) omitted)."
        if args.html:
            args.html.write_text(html_report(findings, metadata), encoding="utf-8")
        renderer = {"text": text_report, "json": json_report, "markdown": markdown_report}[args.format]
        sys.stdout.write(renderer(findings, metadata))
        if args.html:
            sys.stderr.write(f"HTML report: {args.html}\n")
        return 1 if findings and not args.no_fail else 0
    except (GitError, OSError, UnicodeError) as exc:
        sys.stderr.write(f"gate-drift: inspection failed: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
