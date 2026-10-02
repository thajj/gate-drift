"""Explicit network setup for validation; fetches Git objects without checkout."""
from pathlib import Path
import argparse
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args, check=True, quiet=False):
    return subprocess.run(
        ["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
         "-c", "core.hooksPath=" + os.devnull, "-c", "gc.auto=0",
         "-c", "maintenance.auto=false", "-c", "protocol.allow=never",
         "-c", "protocol.https.allow=always", "-C", str(repo), *args],
        check=check, timeout=60,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"},
    )


def fetch_repositories(dataset, destination):
    repositories = sorted({case["repository"] for case in dataset["cases"]})
    for name in repositories:
        repo = destination / name.rsplit("/", 1)[-1]
        repo.mkdir(parents=True, exist_ok=True)
        if not (repo / ".git").exists():
            git(repo, "init", "-b", "codex/validation")
        # Use the recorded public repository directly, even when a destination
        # already has an origin for a different server or transport helper.
        url = "https://github.com/" + name + ".git"
        cases = [case for case in dataset["cases"] if case["repository"] == name]
        heads = sorted({case["head"] for case in cases})
        bases = sorted({case["base"] for case in cases})
        for sha in dict.fromkeys(heads + bases):
            if git(repo, "cat-file", "-e", sha + "^{commit}", check=False, quiet=True).returncode:
                git(repo, "fetch", "--no-tags", "--no-write-fetch-head", "--depth=2", url, sha)
                # Never accept a successful transport that omitted an object
                # required by the declared comparison.
                git(repo, "cat-file", "-e", sha + "^{commit}", quiet=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / ".validation-repos")
    args = parser.parse_args()
    dataset = json.loads((ROOT / "validation" / "dataset.json").read_text())
    fetch_repositories(dataset, args.destination)


if __name__ == "__main__":
    main()
