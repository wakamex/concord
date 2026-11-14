"""Compile a gruntz-decomp unit as it was at any commit, without checking the commit out.

The unit's source and every header under include/ are read from the commit and compiled through
the header overlay, which is searched before the checkout's include directory, against today's
retail object. A function's banked best can be a snapshot no build reproduces, since the baseline
records a score when it is banked; this measures whether a commit's source really was exact.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def tree_at(root: Path, commit: str, paths: list[str]) -> dict[str, bytes]:
    """The contents at `commit` of every file under each of `paths` (files or directories)."""
    listing = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "-z", commit, "--", *paths],
                             capture_output=True, check=True).stdout  # fmt: skip
    entries = []
    for record in listing.split(b"\0"):
        if record:
            meta, path = record.split(b"\t", 1)
            if meta.split()[1] == b"blob":
                entries.append((meta.split()[2], path.decode()))
    batch = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"], input=b"".join(sha + b"\n" for sha, _ in entries),
                           capture_output=True, check=True).stdout  # fmt: skip
    files, at = {}, 0
    for _, path in entries:
        header_end = batch.index(b"\n", at)
        size = int(batch[at:header_end].split()[2])
        files[path] = batch[header_end + 1 : header_end + 1 + size]
        at = header_end + 1 + size + 1
    return files


def at_commit(target, unit: str, commits: list[str]) -> list[dict]:
    """For each commit, the unit's function rows compiled from that commit's source and headers,
    or the compiler's complaint."""
    source_path = target.source(unit)
    results = []
    for commit in commits:
        files = tree_at(target.root, commit, ["include", source_path])
        if source_path not in files:
            results.append({"commit": commit, "error": f"{source_path} does not exist at {commit}"})
            continue
        source = files.pop(source_path)
        evaluation = target.evaluate(unit, {"at": source}, files)["at"]
        if evaluation.error:
            results.append({"commit": commit, "error": evaluation.error.strip()[-2000:]})
        else:
            results.append({"commit": commit, "functions": evaluation.sections[0]["functions"]})
    return results
