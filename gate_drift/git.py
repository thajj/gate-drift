"""Read tracked Git snapshots without running repository code."""
from pathlib import Path
import hashlib
import os
import stat
import subprocess

from .engine import FileChange

MAX_FILE_BYTES = 2 * 1024 * 1024


class GitError(Exception):
    pass


def _git(root, *args, limit=None):
    try:
        proc = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=30, check=False,
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(str(exc)) from exc
    if proc.returncode:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip())
    if limit and len(proc.stdout) > limit:
        raise GitError("A tracked file exceeds the 2 MiB inspection limit. Split the review.")
    return proc.stdout


def _resolve(root, ref):
    return _git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()


def _working_changes(root, base_sha):
    """Avoid working-tree git diff: it can invoke repository clean filters."""
    if _git(root, "ls-files", "--unmerged", "-z"):
        raise GitError("Resolve unmerged index entries before inspecting changes.")
    base_entries = {}
    for entry in _git(root, "ls-tree", "-r", "-z", base_sha).split(b"\0"):
        if entry:
            details, path = entry.split(b"\t", 1)
            mode, kind, oid = details.decode().split()
            base_entries[path.decode("utf-8", "surrogateescape")] = (mode, kind, oid)
    index_paths = set(_git(root, "ls-files", "--cached", "-z").decode("utf-8", "surrogateescape").split("\0")) - {""}
    # Index-to-commit diff never needs to run a working-tree clean filter.
    staged_parts = _git(root, "diff", "--no-ext-diff", "--no-textconv", "--cached", "--name-status", "-z", "-M", base_sha, "--").decode("utf-8", "surrogateescape").split("\0")
    renames = {}
    i = 0
    while i < len(staged_parts) and staged_parts[i]:
        status, old = staged_parts[i:i + 2]
        i += 2
        if status.startswith(("R", "C")):
            new = staged_parts[i]
            i += 1
            if status.startswith("R"):
                renames[new] = old
    algorithm = _git(root, "rev-parse", "--show-object-format").decode().strip()
    changes, excluded = [], []
    paths = (set(base_entries) | index_paths) - set(renames.values())
    for path in sorted(paths):
        mode, kind, oid = base_entries.get(renames.get(path, path), ("", "blob", ""))
        target = root / path
        if mode == "160000" or (path in index_paths and target.is_dir()):
            excluded.append(path + " (submodule or directory)")
            continue
        path_links = [root.joinpath(*Path(path).parts[:n]) for n in range(1, len(Path(path).parts) + 1)]
        if mode == "120000" or (path in index_paths and any(part.is_symlink() for part in path_links)):
            excluded.append(path + " (symbolic link)")
            continue
        after = b""
        if path in index_paths and target.exists():
            try:
                size = target.stat().st_size
                if not stat.S_ISREG(target.stat().st_mode):
                    excluded.append(path + " (nonregular file)")
                    continue
                digest = hashlib.new(algorithm)
                digest.update(f"blob {size}\0".encode())
                with target.open("rb") as stream:
                    while chunk := stream.read(65536):
                        digest.update(chunk)
                if digest.hexdigest() == oid and path not in renames:
                    continue
                if size > MAX_FILE_BYTES:
                    raise GitError("File exceeds 2 MiB inspection limit: " + path)
                after = target.read_bytes()
            except OSError as exc:
                raise GitError(str(exc)) from exc
        elif not oid:
            continue
        before = _git(root, "cat-file", "blob", oid, limit=MAX_FILE_BYTES) if oid else b""
        if before == after and path not in renames:
            continue
        if b"\0" in before or b"\0" in after:
            excluded.append(path + " (binary)")
            continue
        before_text = before.decode("utf-8", "replace").replace("\r\n", "\n")
        after_text = after.decode("utf-8", "replace").replace("\r\n", "\n")
        if before_text == after_text and path not in renames:
            continue
        changes.append(FileChange(path, before_text, after_text))
    return changes, excluded


def read_changes(repo, base="HEAD", head=None, staged=False):
    root = Path(_git(repo, "rev-parse", "--show-toplevel").decode().strip())
    base_sha = _resolve(root, base)
    head_sha = _resolve(root, head) if head else None
    if not head_sha and not staged:
        changes, excluded = _working_changes(root, base_sha)
        return changes, {"repository": root.name, "base": base_sha, "head": "working tree",
                         "files_inspected": len(changes), "excluded": excluded,
                         "scope": "Tracked files only; untracked files are not included. Git filters are not run; CRLF line endings are normalized for text comparison."}
    args = ["diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z", "-M"]
    if staged:
        args.append("--cached")
    args.append(base_sha)
    if head_sha:
        args.append(head_sha)
    parts = _git(root, *args, "--").decode("utf-8", "surrogateescape").split("\0")
    changes, excluded = [], []
    i = 0
    while i < len(parts) and parts[i]:
        status, old_path = parts[i], parts[i + 1]
        i += 2
        path = old_path
        if status.startswith(("R", "C")):
            path = parts[i]
            i += 1
        if status[0] not in "AMDRCT":
            raise GitError("Cannot inspect unresolved Git change: " + path)
        before = b"" if status.startswith("A") else _git(root, "show", f"{base_sha}:{old_path}", limit=MAX_FILE_BYTES)
        if status.startswith("D"):
            after = b""
        elif head_sha or staged:
            spec = f"{head_sha}:{path}" if head_sha else ":" + path
            after = _git(root, "show", spec, limit=MAX_FILE_BYTES)
        else:
            target = root / path
            # Do not dereference links into other folders.
            if target.is_symlink():
                excluded.append(path + " (symbolic link)")
                continue
            try:
                if target.stat().st_size > MAX_FILE_BYTES:
                    raise GitError("File exceeds 2 MiB inspection limit: " + path)
                after = target.read_bytes()
            except OSError as exc:
                raise GitError(str(exc)) from exc
        if b"\0" in before or b"\0" in after:
            excluded.append(path + " (binary)")
            continue
        changes.append(FileChange(path, before.decode("utf-8", "replace").replace("\r\n", "\n"), after.decode("utf-8", "replace").replace("\r\n", "\n")))
    metadata = {
        "repository": root.name,
        "base": base_sha,
        "head": head_sha or ("index" if staged else "working tree"),
        "files_inspected": len(changes),
        "excluded": excluded,
        "scope": "Tracked files only; untracked files are not included.",
    }
    return changes, metadata
