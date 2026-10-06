# concord

Matching recompilation for x86-64 and C++. concord searches for source that, recompiled with the original toolchain and flags, produces byte-identical object code against a target binary.

## The problem concord solves

A decompiler recovers what a function does. Matching recompilation needs source in the original language whose compiled bytes equal the target's. In practice the gap between a correct decompile and a match is codegen rather than semantics. The functions that stay unmatched differ in register choice, basic-block order, loop-compare operand order and inlining order, which are compiler decisions. So the hard problem is finding the source form and flags that reproduce the original compiler's exact choices, with the original compiler in the loop as the scorer.

## Approach

concord is a compiler-in-the-loop source synthesizer: a search over source rewrites intended to preserve semantics, scored by the original toolchain, directed by a diff that attributes each mismatch to a cause, and checked by a semantics oracle so the search can chase bytes without drifting away from the target's behavior.

The pieces, each a module under `src/concord/`:

- seed: take the most source-shaped correct decompile, from [Ghidra](https://ghidra-sre.org/) or [Binary Ninja](https://binary.ninja/), as the initial candidate. The seed only has to be semantically right and roughly structured.
- types: recover class and struct layout from [run-time type information (RTTI)](https://en.wikipedia.org/wiki/Run-time_type_information) and virtual function tables (vtables), a cross-platform debug map, and data-layout analysis (DLA). Field order, size and padding change codegen directly, so layout is a fixed input before any codegen search.
- toolchain: the pinned original compiler in a reproducible container, plus flag inference from anchor functions. Once anchored, the version and flags are constants.
- compile: compile a candidate and extract the object section for one function.
- diff: compare the candidate object against the target and attribute each mismatch to a source- or flag-level cause on top of a score.
- oracle: lift the target function once with [rev.ng](https://rev.ng/) as the semantic reference, and reject candidates that disagree with it under differential testing on generated inputs. Equivalence between a compiled candidate and a lift is undecidable in general, so the oracle catches wrong rewrites with evidence rather than proving their absence. A candidate that matches the target byte for byte is equivalent at the machine level by construction; the oracle matters for the intermediate and best-effort candidates.
- transforms: a library of source rewrites intended to preserve semantics that map to codegen knobs (operand reorder, declaration order, loop form, inlining control, temporaries, field order within recovered constraints).
- search: a search engine over transforms that minimizes the diff, with each candidate gated by the oracle.

The decompiler plays two separate roles. A source-shaped decompile is the seed, and a machine-shaped lift (rev.ng) is the oracle. rev.ng keeps virtual calls as indirect calls and its DLA recovers field-level layout without class identity, which is the right behavior for faithful reconstruction but leaves its output far from the C++ source a match needs. That is why it serves as the reference rather than the starting point.

## Relationship to existing tools

- [m2c](https://github.com/matt-kempster/m2c) decompiles 32-bit MIPS, ARM, PowerPC and SuperH assembly into C aimed at matching, with partial C++ support. concord targets x86-64 and C++ and automates the convergence instead of emitting one candidate.
- [decomp-permuter](https://github.com/simonlindholm/decomp-permuter) applies randomized permutations to source and keeps the ones that score better. concord directs the search with cause attribution and checks candidates against an oracle.
- [objdiff](https://github.com/encounter/objdiff) and [asm-differ](https://github.com/simonlindholm/asm-differ) score the gap. concord's diff adds cause attribution on top of a scorer.
- [decomp.me](https://decomp.me/) is the compile-in-the-loop scoreboard. concord internalizes that loop and runs it under search.

## Origin and name

concord came out of work on the Harvest matching decompilation (`../harvest`, a GCC 4.4.3 Linux amd64 C++ game). There the functions left inexact differ in codegen artifacts, and closing them is mostly humans guessing source forms. The two pieces that would automate that loop, a cause-attributing diff and a transformation search gated by a semantics check, did not exist for x86-64 and C++, so concord is built around them.

Its conceptual ancestors are m2c, objdiff, decomp-permuter and decomp.me; the closest is decomp-permuter, to which concord adds directed search and an oracle. The name follows that lineage and names the goal state: the recompiled object in agreement with the target byte for byte. parity (byte parity with the target) was the runner-up.

## Status

The cause-attributing diff works; the other stages are stubs. See `docs/DESIGN.md` for the full design and `src/concord/` for the interfaces.

`concord diff` disassembles one function from a target object and a candidate object with [Capstone](https://www.capstone-engine.org/), aligns the instructions, and labels each mismatch: register allocation, operand order, block order, inlining, stack or struct layout, immediate, or instruction selection. Pointed at a Harvest checkout after `hv match`, it reads Harvest's delinked target and compiled objects, and for functions whose bytes already match it reports the references or placement that keep Harvest's matcher from proving them exact:

```sh
uv run concord diff --harvest ../harvest --unit HarvestFull/harvest/gui/CProfileScreen.cpp _ZN7harvest3gui14CProfileScreen11saveProfileEb
uv run concord diff --target target.o --candidate candidate.o SYMBOL
uv run concord survey --harvest ../harvest
```

`concord survey` counts the causes over every inexact function in a Harvest build.

The knowledge base in `src/concord/knowledge/` records a compiler's codegen idiosyncrasies, one TOML file per compiler version. Each idiom names the cause the diff reports, the symptom, the source change that fixes it, whether that is confirmed or a hypothesis, and the evidence (a Harvest commit or note). `concord diff` lists the idioms for the causes it finds, and the transform search will read the same entries to choose its moves. Add an idiom whenever a fix makes a function match.

## Development

concord uses [uv](https://docs.astral.sh/uv/).

```sh
uv run concord --help
uv run --locked python -m unittest discover -s tests
```
