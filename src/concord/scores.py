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
import re
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
    """Compare sources using the head's configuration on both sides.

    The isolated worktree owns its captures and build outputs. The caller's
    tracked source changes are snapshotted into it without changing the index,
    matching reports or committed evidence. Configuration must be committed.
    """
    checkout = checkout.resolve()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(checkout), *args])
    head = git('rev-parse', 'HEAD').decode().strip()
    base = git('rev-parse', '--verify', base + '^{commit}').decode().strip()
    def workspace_inputs():
        if (git('status', '--porcelain', '--untracked-files=normal', '--', 'config', 'tools', 'builds.json', 'uv.lock', 'toolchain')
                or git('ls-files', '--others', '--exclude-standard', '-z', '--', 'src', 'third_party')):
            raise ValueError('normalized scores require committed measurement inputs; commit or isolate source/config changes first')
        dirty = filter(None, git(
            'diff', '--name-only', '--no-renames', '-z', 'HEAD', '--', 'src', 'third_party'
        ).decode().split('\0'))
        files = {}
        for name in dirty:
            path = checkout / name
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError(f'cannot snapshot non-file source input: {name}')
            files[name] = path.read_bytes() if path.exists() else None
        return files

    workspace_source = workspace_inputs()
    added_paths = git('diff', '--name-only', '--diff-filter=A', '-z', base, head, '--', 'src') + git(
        'diff', '--name-only', '--diff-filter=A', '-z', base, '--', 'src'
    )
    added_units = sorted({path.removeprefix('src/') for path in added_paths.decode().split('\0')
                          if path.endswith('.cpp')})
    changed = git('diff', '--name-only', '-z', base, '--', 'src', 'third_party').decode().split('\0')
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
            # A newly seeded unit has no source in the base revision. Keep all
            # learned addresses but omit its registration for that capture.
            units_path = root / config_dir / 'units.toml'
            head_units = units_path.read_text() if units_path.exists() else ''
            absent = sorted(unit for unit in added_units
                            if f'["{unit}"]' in head_units and not (root / 'src' / unit).exists())
            base_units = head_units
            for unit in absent:
                pattern = r'^\["' + re.escape(unit) + r'"\]\n.*?(?=^\[|\Z)'
                base_units = re.sub(pattern, '', base_units, flags=re.MULTILINE | re.DOTALL)
            try:
                if absent:
                    units_path.write_text(base_units)
                before = snapshot(report(root, capture=True))
            finally:
                if absent:
                    units_path.write_text(head_units)
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
            for name, content in workspace_source.items():
                path = root / name
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
            after = snapshot(report(root, capture=True))
            try:
                unchanged = git('rev-parse', 'HEAD').decode().strip() == head and workspace_inputs() == workspace_source
            except ValueError as error:
                raise ValueError('measurement inputs changed while capturing scores') from error
            if not unchanged:
                raise ValueError('measurement inputs changed while capturing scores')
            config = root / config_dir
            evidence = {'base': base, 'head': head, 'source_paths': sorted(filter(None, changed)),
                        'workspace_source': {name: hashlib.sha256(content).hexdigest() if content is not None else None
                                             for name, content in workspace_source.items()},
                        'configuration': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(config.iterdir()) if p.is_file()},
                        'configuration_changes': configuration_changes, 'absent_base_units': absent,
                        'configuration_baseline': configuration_before,
                        'baseline': before}
            return before, after, evidence
        finally:
            subprocess.run(['git', '-C', str(checkout), 'worktree', 'remove', '--force', str(root)], check=True)
