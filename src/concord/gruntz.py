"""Read a gruntz-decomp checkout's matching outputs and compile candidates in it.

gruntz-decomp reconstructs a Windows game built with Microsoft Visual C++ 5.0.
Its `gruntz build` compiles every unit under Wine, cuts the retail executable
into one object per unit, and scores each function with objdiff
(build/objdiff/compare-new/report.json); config/match_baseline.tsv keeps, per
function, the best score its present source has reached, which is the project's
definition of a match. concord reads those files and has the checkout's own
Python, inside its Nix dev shell, compile and compare candidates
(gruntz_driver.py).
"""

from __future__ import annotations

import atexit
import csv
import fcntl
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from tree_sitter import Node, Parser

from concord import coff
from concord.diff import diff_code, read_functions
from concord.harvest import Evaluation, FunctionVerdict
from concord.model import Cause, DiffFinding, DiffResult
from concord.temporaries import DECLARATION
from concord.transforms import ALL, CPP, Rewrite, name_temporaries

DRIVER = Path(__file__).with_name("gruntz_driver.py")
REPORT = Path("build/objdiff/compare-new/report.json")
IMAGE = "docker.io/nixos/nix:latest"
STORE = "gruntz-nix-store"  # the Podman volume holding the container's /nix
# Exception-handling funclets the report scores; they are no reconstruction targets.
FUNCLETS = ("__ehreg$", "__ehunwind$")

_drivers: dict[Path, subprocess.Popen] = {}  # one driver per checkout, for the life of the process


@atexit.register
def _stop_drivers() -> None:
    processes = list(_drivers.values())
    _drivers.clear()
    for process in processes:
        if process.poll() is None:
            process.stdin.close()
            process.terminate()


def _podman() -> list[str]:
    """podman with the user's own storage. A harness can point XDG_DATA_HOME and its siblings into its run's
    state: Devin's aop runs do, and there Podman looked for its containers in a new store under the run, which
    SELinux keeps the container runtime from opening, so every gruntz command failed in those runs."""
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    places = {"XDG_DATA_HOME": home / ".local/share", "XDG_CONFIG_HOME": home / ".config", "XDG_RUNTIME_DIR": Path(f"/run/user/{os.getuid()}")}
    settings = [f"{key}={value}" for key, value in places.items() if key != "XDG_RUNTIME_DIR" or value.is_dir()]
    return ["env", "-u", "CONTAINERS_STORAGE_CONF", "-u", "CONTAINERS_CONF", f"HOME={home}", *settings, "podman"]


PODMAN = _podman()


def shell_command(root: Path) -> list[str]:
    """The command prefix that runs its arguments inside the checkout's dev shell,
    with the checkout as the working directory. CONCORD_GRUNTZ_SHELL names one;
    otherwise it is `nix develop` where Nix is installed, and elsewhere the same
    inside one long-lived rootless Podman container per checkout, since two Wine
    servers on one prefix would race."""
    if os.environ.get("CONCORD_GRUNTZ_SHELL"):
        return shlex.split(os.environ["CONCORD_GRUNTZ_SHELL"])
    if shutil.which("nix"):
        return ["nix", "develop", str(root), "--command"]
    name = "concord-gruntz-" + hashlib.sha256(str(root).encode()).hexdigest()[:8]
    _ensure_container(name, root)
    return [*PODMAN, "exec", "-i", "-w", str(root), "-e", "HOME=/root", "-e", f"GRUNTZ_DIR={root}", name,
            "nix", "develop", str(root), "--profile", "/nix/var/nix/profiles/gruntz-devshell", "--command"]  # fmt: skip


def _running(name: str) -> bool:
    found = subprocess.run([*PODMAN, "container", "inspect", "-f", "{{.State.Running}}", name],
                           capture_output=True, text=True, check=False)  # fmt: skip
    return found.stdout.strip() == "true"


def _ensure_container(name: str, root: Path, wait: float = 30) -> None:
    """Create and start the checkout's container unless it runs, one caller at a time. Where systemd is the user's
    service manager it runs as the user unit NAME, so it outlives the process that started it: started from an
    agent's shell, its monitor process was in that shell's process tree and the container stopped within minutes,
    in the middle of a compile. A unit can outlive its container, and after a reboot crun kept runtime state for a
    stopped container and refused to start it, so a stale unit is stopped first and a container that still does
    not start is removed and created again. It holds nothing of its own: the Nix store is the volume STORE."""
    with open(Path(tempfile.gettempdir()) / f"{name}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for _ in range(2):
            if _running(name):
                return
            if subprocess.run([*PODMAN, "container", "exists", name], check=False).returncode:
                settings = "experimental-features = nix-command flakes\nsandbox = false\nwarn-dirty = false\nmax-jobs = 2\ncores = 8"
                subprocess.run(
                    [*PODMAN, "create", "--name", name, "--init", "--security-opt", "label=disable",
                     "-v", f"{STORE}:/nix", "-v", f"{root}:{root}", "-e", f"NIX_CONFIG={settings}", IMAGE, "sleep", "infinity"],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )  # fmt: skip
            if shutil.which("systemd-run"):
                quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
                subprocess.run(["systemctl", "--user", "stop", name], check=False, **quiet)
                subprocess.run(["systemd-run", "--user", "--quiet", "--collect", f"--unit={name}",
                                *PODMAN, "start", "--attach", name], check=False, **quiet)  # fmt: skip
            else:
                subprocess.run([*PODMAN, "start", name], check=False, stdout=subprocess.DEVNULL)
            deadline = time.monotonic() + wait
            while not _running(name) and time.monotonic() < deadline:
                time.sleep(0.5)
            if _running(name):
                return
            subprocess.run([*PODMAN, "rm", "-f", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raise RuntimeError(f"the dev shell container {name} did not start")


def _install(source: Path, destination: Path) -> None:
    """Copy a file into the checkout unless it is already there. The copy keeps
    the source's time, so the driver rebuilds the reader only when it changed, and
    is renamed into place, since another concord may be running the old file."""
    if destination.exists() and destination.read_bytes() == source.read_bytes():
        return
    fresh = destination.with_name(f"{destination.name}.{os.getpid()}")
    shutil.copy2(source, fresh)
    fresh.replace(destination)


class Gruntz:
    compiler = "msvc-5.0"
    log = "gruntz.jsonl"
    oracle = False  # nothing checks an inexact rewrite's behavior, so only exact matches are written
    # cl 5.0's code for one function shifts when another function of the unit changes, which the project
    # calls a dip and does not count against the unchanged function. A search may therefore lower another
    # function's score, as long as no function exact in the build stops being exact and the unit gains.
    net_rule = True
    # cl 5.0 has no __typeof__, which name_temporaries writes; name_typed_temporaries stands in for it.
    untyped: ClassVar[list] = [transform for transform in ALL if transform is not name_temporaries]

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.rewrites = [*self.untyped, self.name_typed_temporaries]
        # (unit, function address) -> expression text -> (type, whether narrower than int)
        self.types: dict[tuple[str, int], dict[bytes, tuple[str, bool]]] = {}
        self.using: tuple[str, ...] = ()  # the driver's shortcuts that held for the last unit evaluated
        if not (self.root / REPORT).is_file():
            raise FileNotFoundError(f"{self.root / REPORT} is missing; run `gruntz build` in {root}")
        manifest = tomllib.loads((self.root / "config/units.toml").read_text())
        self.sources = {unit["unit"]: unit["source"] for unit in manifest["unit"]}
        with (self.root / "build/gen/bindings.tsv").open(newline="") as stream:
            rows = [row for row in csv.DictReader((line for line in stream if not line.startswith("#")), delimiter="\t")
                    if row["space"] == "text" and row["name"]]  # fmt: skip
        self.addresses = {row["name"]: int(row["rva"], 0) for row in rows}
        self.owners = {int(row["rva"], 0): row["unit"] for row in rows if row["unit"] in self.sources}
        self.best: dict[tuple[str, str], float] = {}  # (unit, symbol) -> the best score of its present source
        for line in (self.root / "config/match_baseline.tsv").read_text().splitlines():
            fields = line.split("\t")
            if not line.startswith("#") and len(fields) >= 8:
                self.best[fields[0], fields[1]] = float(fields[2])

    def slug(self, unit: str) -> str:
        """Accept a unit name or its source path."""
        if unit in self.sources:
            return unit
        found = [name for name, source in self.sources.items() if source == unit]
        if len(found) != 1:
            raise KeyError(f"{unit} is not a unit or a unit's source in config/units.toml")
        return found[0]

    def source(self, unit: str) -> str:
        """The unit's source path under the checkout."""
        return self.sources[self.slug(unit)]

    def source_path(self, unit: str) -> Path:
        return self.root / self.source(unit)

    def verdicts(self, unit: str | None = None) -> Iterator[FunctionVerdict]:
        """Every function the last `gruntz build` scored. A function is exact when
        the best score of its present source is 100, as the project counts it: its
        banked best, or its score in this build where that is not banked yet."""
        wanted = self.slug(unit) if unit else None
        for entry in json.loads((self.root / REPORT).read_text()).get("units", []):
            name = entry["name"].split("/")[-1]
            if wanted not in (None, name):
                continue
            for row in entry.get("functions", []):
                symbol = row["name"]
                if symbol.startswith(FUNCLETS):
                    continue
                fuzzy = float(row.get("fuzzy_match_percent") or 0.0)
                best = self.best.get((name, symbol), fuzzy)
                address = self.addresses.get(symbol)
                yield FunctionVerdict(
                    name,
                    symbol,
                    {"symbol": symbol, "exact": 100.0 in (best, fuzzy), "fuzzy": fuzzy, "best": best, "size": int(row.get("size") or 0),
                     "address": f"{address:#x}" if address is not None else None},
                    {},
                )  # fmt: skip

    def verdict(self, unit: str, symbol: str) -> FunctionVerdict:
        for found in self.verdicts(unit):
            if found.symbol == symbol:
                return found
        raise KeyError(f"{symbol} is not in {unit}'s report")

    def find_function(self, source: bytes, symbol: str) -> Node:
        """The function definition that follows the symbol's `RVA(address, size)`
        label in the source."""
        if symbol not in self.addresses:
            raise KeyError(f"{symbol} has no address in build/gen/bindings.tsv")
        marker = source.find(f"RVA(0x{self.addresses[symbol]:08x},".encode())
        if marker < 0:
            raise KeyError(f"no RVA label for {symbol} in the source")
        stack = [Parser(CPP).parse(source).root_node]
        found = []
        while stack:
            node = stack.pop()
            if node.type == "function_definition" and node.start_byte > marker:
                found.append(node)
            elif node.end_byte > marker:
                stack.extend(node.children)
        if not found:
            raise KeyError(f"no function definition follows the RVA label for {symbol}")
        return min(found, key=lambda node: node.start_byte)

    def name_typed_temporaries(self, source: bytes, scope: Node) -> Iterator[Rewrite]:
        """name_temporaries with each temporary declared by its type, `i32 tmp = E;`
        for `__typeof__(E) tmp = E;`. The types of a function's expressions come
        from one libclang parse per function and process, so an expression a rewrite
        has since changed has none and is not named. A value narrower than int is
        also offered as an int, the type such a result is most often kept in
        (`i32 ok = surface->Blt(other);` for a call returning bool), which widens it
        where it is named instead of where it is used."""
        label = source.rfind(b"RVA(0x", 0, scope.start_byte)
        address = int(source[label + 4 : source.index(b",", label)], 16) if label >= 0 else None
        if address not in self.owners:
            return
        unit = self.owners[address]
        if (unit, address) not in self.types:
            out = self.root / "build" / "concord" / unit / uuid.uuid4().hex
            out.mkdir(parents=True)
            path = out / f"types{Path(self.sources[unit]).suffix}"
            path.write_bytes(source)
            request = {"unit": unit, "out": str(out), "types": str(path), "range": [scope.start_byte, scope.end_byte], "wibo": self._wibo()}
            self.types[unit, address] = {text.encode(): (kind, narrow) for text, (kind, narrow) in self._driver(request).items()}
        types = self.types[unit, address]
        for rewrite in name_temporaries(source, scope):
            declared = DECLARATION.search(rewrite.source)
            if not declared or declared["expr"] not in types:
                continue
            kind, narrow = types[declared["expr"]]
            # written as the checkout writes types: no elaborating keyword, the pointer with the type
            kind = re.sub(r"\b(class|struct|enum|union) ", "", kind).replace(" *", "*").replace(" &", "&")
            for spelling, note in [(kind, ""), *([("int", " as int")] if narrow else [])]:
                typed = rewrite.source.replace(b"__typeof__(" + declared["expr"] + b")", spelling.encode(), 1)
                yield Rewrite(rewrite.transform, rewrite.cause, rewrite.description + note, typed)

    name_typed_temporaries.stands_for = name_temporaries  # the benchmark's mutation it replaces

    def _driver(self, request: dict) -> dict:
        """Send one request to the checkout's driver and return its result. The
        driver is started inside the dev shell on the first request and kept for
        the life of this process; one that has died is started again."""
        process = _drivers.get(self.root)
        if process is None or process.poll() is not None:
            script = self.root / "build" / "concord" / DRIVER.name
            script.parent.mkdir(parents=True, exist_ok=True)
            _install(DRIVER, script)  # the dev shell sees the checkout, not concord
            _install(DRIVER.with_name("objdiff_scores.rs"), script.with_name("objdiff_scores.rs"))
            if os.environ.get("CONCORD_GRUNTZ_WIBO"):
                _install(Path(os.environ["CONCORD_GRUNTZ_WIBO"]), script.with_name("wibo-loader"))
            log = tempfile.TemporaryFile(mode="w+")  # noqa: SIM115 (read when the driver stops, which is later)
            process = subprocess.Popen(
                [*shell_command(self.root), "python3", str(script)],
                cwd=self.root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
            )
            process.log = log
            _drivers[self.root] = process
            while (line := process.stdout.readline()) and line.strip() != '{"ready": true}':
                pass  # the shell's own start-up output
            if not line:
                process.log.seek(0)
                raise RuntimeError(f"gruntz_driver.py did not start:\n{process.log.read()[-3000:]}")
        try:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
        except BrokenPipeError:
            line = ""
        if not line:
            process.log.seek(0)
            raise RuntimeError(f"gruntz_driver.py stopped:\n{process.log.read()[-3000:]}")
        reply = json.loads(line)
        if "error" in reply:
            raise RuntimeError(f"gruntz_driver.py failed:\n{reply['error']}")
        self.using = tuple(reply.get("using", self.using))
        return reply["ok"]

    def evaluate(self, unit: str, sources: dict[str, bytes]) -> dict[str, Evaluation]:
        """Compile each candidate source of the unit with the checkout's compiler,
        in one batch, and compare it with the retail object. Work files go under
        build/concord/."""
        slug = self.slug(unit)
        out = self.root / "build" / "concord" / slug / uuid.uuid4().hex
        out.mkdir(parents=True)
        paths = {}
        for name, text in sources.items():
            paths[name] = out / f"{name}{Path(self.sources[slug]).suffix}"
            paths[name].write_bytes(text)
        request = {"unit": slug, "out": str(out), "candidates": {n: str(p) for n, p in paths.items()},
                   "jobs": int(os.environ.get("CONCORD_JOBS", "8")), "wibo": self._wibo()}  # fmt: skip
        evaluations = {}
        for name, row in self._driver(request).items():
            functions = row.get("functions", [])
            evaluations[name] = Evaluation(
                name,
                slug,
                Path(row["object"]) if "object" in row else None,
                row.get("error", ""),
                [{"functions": functions, "retail": str(out / "retail.obj")}],
                {function["symbol"]: function["fuzzy"] for function in functions},
            )
        return evaluations

    def debug_objects(self, units: list[str], sources: dict[str, bytes] | None = None) -> dict[str, Path]:
        raise RuntimeError("cl 5.0 objects carry no variable types concord reads")

    def source_lines(self, unit: str, symbol: str, source: bytes, offsets: list[int]) -> set[int]:
        """Lines of the unit's source behind the code at `offsets` in `symbol`, for
        the unit compiled from `source` with cl's /Zd line records. Empty when
        that compile fails."""
        data = self.line_object(unit, source)
        if data is None:
            return set()
        records = coff.lines(data, symbol)
        found = set()
        for offset in offsets:
            before = [line for start, line in records if start <= offset]
            if before:
                found.add(before[-1])
        return found

    def line_object(self, unit: str, source: bytes) -> bytes | None:
        """The unit compiled from `source` with cl's /Zd line records, which leave
        every function's code as the build has it; None when the compile fails."""
        slug = self.slug(unit)
        out = self.root / "build" / "concord" / slug / uuid.uuid4().hex
        out.mkdir(parents=True)
        path = out / f"lines{Path(self.sources[slug]).suffix}"
        path.write_bytes(source)
        reply = self._driver({"unit": slug, "out": str(out), "lines": str(path), "wibo": self._wibo()})
        return Path(reply["object"]).read_bytes() if "object" in reply else None

    def _wibo(self) -> str | None:
        """The loader the driver may compile under, as it sees the path."""
        return str(self.root / "build" / "concord" / "wibo-loader") if os.environ.get("CONCORD_GRUNTZ_WIBO") else None

    def diff(self, verdict: FunctionVerdict, candidate_object: Path | None = None) -> DiffResult:
        """Attribute the function's remaining differences, for the checkout's own
        compile or for a candidate object from evaluate(). Whether it is exact is
        the checkout's verdict, never concord's own comparison of the bytes."""
        if verdict.row["exact"]:
            return DiffResult(score=100.0, exact=True)
        if candidate_object is None:
            pair = (self.root / REPORT).parent  # the canonical copies the report was scored from
            retail, candidate_object = pair / "target" / f"{verdict.unit}.c.obj", pair / "base" / f"{verdict.unit}.obj"
        else:
            retail = Path(verdict.section["retail"])
        target = read_functions(retail, {verdict.symbol}).get(verdict.symbol)
        candidate = read_functions(candidate_object, {verdict.symbol}).get(verdict.symbol)
        if target is None or candidate is None:
            raise KeyError(f"{verdict.symbol} is not a function in both objects of {verdict.unit}")
        result = diff_code(target, candidate)
        if result.exact:
            reason = "bytes and relocation offsets identical; a relocation's target, addend or the function's extent differs"
            result = DiffResult(score=100.0, exact=False, findings=[DiffFinding(Cause.REFERENCE, 0, reason)])
        return result

    def clean(self, older_than: float) -> tuple[int, int]:
        """Remove concord's work directories under build/concord last changed more
        than `older_than` seconds ago; (directories removed, bytes freed)."""
        cutoff = time.time() - older_than
        removed = freed = 0
        base = self.root / "build" / "concord"
        for group in sorted(base.iterdir()) if base.is_dir() else []:
            for batch in sorted(group.iterdir()) if group.is_dir() else []:
                if batch.is_dir() and batch.stat().st_mtime < cutoff:
                    freed += sum(f.stat().st_size for f in batch.rglob("*") if f.is_file())
                    shutil.rmtree(batch)
                    removed += 1
        return removed, freed
