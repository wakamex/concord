"""Where a function's code comes from: the chain of inlined source lines behind
each instruction, read from the line tables of a -g compile with addr2line.

A difference inside inlined code cannot be fixed in the function that contains
it, only in the header that defines the inlined callee, or in how the caller
reaches it. The chain names both.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# A compile in a container sees the checkout at /work or at a private lane directory.
_ROOT = re.compile(r"^/(?:work|lanes/[^/]+/\d+)/")


@dataclass(frozen=True)
class Frame:
    function: str  # demangled
    path: str  # under the checkout, such as src/ox/core/CString.h, or a toolchain header's absolute path
    line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}"


def inline_chains(obj: Path, section: str, addresses: list[int]) -> list[list[Frame]]:
    """For each address in the object's section, its frames from the innermost
    inlined callee out to the function that contains it."""
    if not addresses:
        return []
    output = subprocess.run(
        ["addr2line", "-e", str(obj), "-j", section, "-a", "-i", "-f", "-C", *map(hex, addresses)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    chains: list[list[Frame]] = []
    n = 0
    while n < len(output):
        if re.fullmatch(r"0x[0-9a-f]{16}", output[n]):
            chains.append([])
            n += 1
            continue
        function, location = output[n], output[n + 1]
        path, _, line = location.split(" ")[0].rpartition(":")
        if path != "??":
            chains[-1].append(Frame(function, os.path.normpath(_ROOT.sub("", path)), int(line) if line.isdigit() else 0))
        n += 2
    return chains


def editable(chain: list[Frame]) -> Frame | None:
    """The innermost frame in the checkout's own sources, the first place a source
    change can reach."""
    return next((f for f in chain if f.path.startswith("src/")), None)
