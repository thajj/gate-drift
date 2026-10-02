"""Reproduce source-addressed findings using already fetched public Git objects."""
from dataclasses import asdict
from collections import Counter
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gate_drift import __version__
from gate_drift.engine import FileChange, analyze
from gate_drift.git import MAX_FILE_BYTES, GitError, _git, read_changes


def identity(item):
    address = (item["rule_id"], item["path"], item["line"], item["side"])
    if (not all(isinstance(value, str) and value for value in address[:2])
            or type(address[2]) is not int or address[2] < 1
            or address[3] not in ("left", "right")):
        raise ValueError("Invalid rule/path/line/side source address: " + repr(address))
    return address


def tool_metadata():
    """Identify the code under review without reporting local file names."""
    try:
        commit = _git(ROOT, "rev-parse", "--verify", "HEAD").decode().strip()
        dirty = bool(_git(ROOT, "status", "--porcelain=v1", "-z", "--untracked-files=normal"))
        return {"tool_commit": commit, "working_tree_dirty": dirty}
    except GitError:
        # A source archive can run validation without having Git metadata.
        return {"tool_commit": None, "working_tree_dirty": None}


def evaluate(dataset, repos_root):
    results = []
    for case in dataset["cases"]:
        repo = repos_root / case["repository"].rsplit("/", 1)[-1]
        try:
            if _git(repo, "for-each-ref", "--format=%(refname)", "refs/replace/").strip():
                raise GitError("Local Git replace refs can substitute pinned objects; use a fresh validation repository.")
            if case.get("paths"):
                changes = []
                for path in case["paths"]:
                    before = _git(repo, "show", case["base"] + ":" + path, limit=MAX_FILE_BYTES).decode("utf-8", "replace").replace("\r\n", "\n")
                    after = _git(repo, "show", case["head"] + ":" + path, limit=MAX_FILE_BYTES).decode("utf-8", "replace").replace("\r\n", "\n")
                    changes.append(FileChange(path, before, after))
                metadata = {"files_inspected":len(changes),"excluded":[],"scope":"Selected configuration files only."}
            else:
                changes, metadata = read_changes(repo, case["base"], case["head"])
            findings = [asdict(f) for f in analyze(changes)]
            expected = Counter(identity(item) for item in case["expected"])
            if any(count > 1 for count in expected.values()):
                raise ValueError("Dataset contains duplicate expected source addresses.")
            actual = Counter(identity(item) for item in findings)
            # Check every emitted address against the actual source snapshot.
            files = {change.path: change for change in changes}
            invalid_addresses = []
            for finding in findings:
                change = files.get(finding["path"])
                if change is None:
                    invalid_addresses.append(identity(finding))
                    continue
                text = change.after if finding["side"] == "right" else change.before
                lines = text.splitlines()
                evidence = finding["after"] if finding["side"] == "right" else finding["before"]
                if not 1 <= finding["line"] <= len(lines) or lines[finding["line"] - 1].strip() != evidence:
                    invalid_addresses.append(identity(finding))
            results.append({**case,"findings":findings,"metadata":metadata,"missing":sorted((expected - actual).elements()),
                            "unexpected":sorted((actual - expected).elements()),"invalid_addresses":invalid_addresses,"error":None,
                            "passed":actual == expected and not invalid_addresses})
        except (GitError, OSError, ValueError) as exc:
            results.append({**case,"findings":[],"missing":[],"unexpected":[],"invalid_addresses":[],"error":str(exc),"passed":False})
    return {"tool_version":__version__,**tool_metadata(),"dataset_date":dataset["date"],"method":dataset["method"],
            "total":len(results),"passed":sum(r["passed"] for r in results),"errors":sum(r["error"] is not None for r in results),
            "expected_sites":sum(len(r["expected"]) for r in results),"emitted_sites":sum(len(r["findings"]) for r in results),"results":results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repos-root", type=Path, required=True, help="Folder containing pytest, vitest, playwright, eslint, EventRelay, and ebg-protrack Git repositories.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dataset = json.loads((ROOT / "validation" / "dataset.json").read_text())
    report = evaluate(dataset, args.repos_root)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key != "results"}, indent=2))
    for result in report["results"]:
        if not result["passed"]:
            print(json.dumps({"repository":result["repository"],"head":result["head"],"missing":result["missing"],"unexpected":result["unexpected"],"invalid_addresses":result["invalid_addresses"],"error":result["error"]}))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
