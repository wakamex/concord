"""The progress a Harvest build reports upstream, saved and compared.

decomp.dev scores a pull request from Harvest's progress report
(`python -m hv.progress report`): objdiff's fuzzy score and match state for every
function, and the target bytes each matched data section covers. A batch of
changes is ready to submit when, by that report, no function scores lower or
stops matching and no data byte that matched stops matching. Data ranges come
from Harvest's validated evidence: the report groups disjoint ranges by owner
and represents each group by its total size and lowest address.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

DATA_FORMAT = "matched-ranges-v1"


def report(checkout: Path, capture: bool = False, ref: str | None = None) -> dict:
    """Harvest's progress report for the checkout, after recapturing its evidence
    when `capture` is set, or for the evidence committed at git revision `ref`."""
    with tempfile.TemporaryDirectory() as tmp:
        root = checkout.resolve()
        if ref is not None:
            root = Path(tmp) / "worktree"
            subprocess.run(["git", "-C", str(checkout), "worktree", "add", "-q", "--detach", str(root), ref], check=True)
        try:
            output = Path(tmp) / "report.json"
            command = "capture" if capture and ref is None else "report"
            done = subprocess.run(
                ["uv", "--no-config", "run", "--locked", "python",
                 str(Path(__file__).with_name("scores_driver.py")),
                 command, "--output", str(output)],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
            if done.returncode:
                raise RuntimeError(f"hv.progress {command} failed in {root}:\n{done.stderr[-2000:]}")
            return json.loads(output.read_text())
        finally:
            if ref is not None:
                subprocess.run(["git", "-C", str(checkout), "worktree", "remove", "--force", str(root)], check=True)


def snapshot(progress: dict) -> dict:
    """Save every function and the verified matched data ranges from a report."""
    functions = {}
    for item in progress["units"]:
        measures = item["measures"]
        for function in item.get("functions", []):
            address = function["metadata"]["virtual_address"]
            functions[address] = {
                "name": function["metadata"].get("demangled_name", function["name"]),
                "fuzzy": function.get("fuzzy_match_percent", 0.0),
                "matched": measures["matched_code"] == measures["total_code"],
            }
    return {"functions": functions, "data": progress["concord_matched_data"], "data_format": DATA_FORMAT}


@dataclass
class Change:
    address: str
    name: str
    before: dict | None
    after: dict | None

    @property
    def worse(self) -> bool:
        if self.before is None:
            return False
        if self.after is None:
            return True
        return (self.before["matched"] and not self.after["matched"]) or self.after["fuzzy"] < self.before["fuzzy"]


def compare(before: dict, after: dict) -> tuple[list[Change], int, int]:
    """Changed functions, and the matched data bytes lost and gained."""
    if before.get("data_format") != DATA_FORMAT or after.get("data_format") != DATA_FORMAT:
        raise ValueError("saved data ranges use an obsolete format; recapture the snapshot")
    changes = []
    for address in sorted(before["functions"].keys() | after["functions"].keys(), key=int):
        a, b = before["functions"].get(address), after["functions"].get(address)
        if a != b:
            changes.append(Change(address, (b or a)["name"], a, b))
    old, new = _bytes(before["data"]), _bytes(after["data"])
    return changes, len(old - new), len(new - old)


def _bytes(runs: list) -> set[int]:
    return {address for start, size in runs for address in range(start, start + size)}


def normalized(checkout: Path, base: str) -> tuple[dict, dict, dict]:
    """Compare committed sources using the head's configuration on both sides.

    The isolated worktree owns its captures and build outputs. The caller's
    source, index, matching reports and committed evidence are never replaced.
    """
    checkout = checkout.resolve()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(checkout), *args])
    head = git('rev-parse', 'HEAD').decode().strip()
    base = git('rev-parse', '--verify', base + '^{commit}').decode().strip()
    if git('status', '--porcelain', '--untracked-files=normal', '--', 'src', 'third_party', 'config', 'tools', 'builds.json', 'uv.lock', 'toolchain'):
        raise ValueError('normalized scores require committed measurement inputs; commit or isolate source/config changes first')
    changed = git('diff', '--name-only', '-z', base, head, '--', 'src', 'third_party').decode().split('\0')
    with tempfile.TemporaryDirectory(prefix='concord-gate-') as tmp:
        root = Path(tmp) / 'worktree'
        subprocess.run(['git', '-C', str(checkout), 'worktree', 'add', '-q', '--detach', str(root), head], check=True)
        try:
            if (checkout / 'orig').exists() and not (root / 'orig').exists():
                (root / 'orig').symlink_to(checkout / 'orig', target_is_directory=True)
            tool = checkout / 'build' / 'tools' / 'objdiff-cli'
            if tool.is_file():
                (root / 'build' / 'tools').mkdir(parents=True, exist_ok=True)
                shutil.copy2(tool, root / 'build' / 'tools' / tool.name)
            build = json.loads((root / 'builds.json').read_text())['canonical']
            config_dir = Path('config') / build
            configuration_changes = sorted(filter(None, git(
                'diff', '--name-only', '-z', base, head, '--', str(config_dir)
            ).decode().split('\0')))
            subprocess.run(['git', '-C', str(root), 'restore', '--source', base, '--worktree', '--', 'src', 'third_party'], check=True)
            before = snapshot(report(root, capture=True))
            configuration_before = None
            if configuration_changes:
                subprocess.run([
                    'git', '-C', str(root), 'restore', '--source', base, '--worktree', '--', str(config_dir)
                ], check=True)
                configuration_before = snapshot(report(root, capture=True))
                subprocess.run([
                    'git', '-C', str(root), 'restore', '--source', head, '--worktree', '--', str(config_dir)
                ], check=True)
            subprocess.run(['git', '-C', str(root), 'restore', '--source', head, '--worktree', '--', 'src', 'third_party'], check=True)
            after = snapshot(report(root, capture=True))
            config = root / config_dir
            evidence = {'base': base, 'head': head, 'source_paths': sorted(filter(None, changed)),
                        'configuration': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(config.iterdir()) if p.is_file()},
                        'configuration_changes': configuration_changes,
                        'configuration_baseline': configuration_before,
                        'baseline': before}
            return before, after, evidence
        finally:
            subprocess.run(['git', '-C', str(checkout), 'worktree', 'remove', '--force', str(root)], check=True)
