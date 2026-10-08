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

concord came out of work on [Harvest](https://github.com/banteg/harvest), a matching decompilation of a GCC 4.4.3 Linux amd64 C++ game. There the functions left inexact differ in codegen artifacts, and closing them is mostly humans guessing source forms. The two pieces that would automate that loop, a cause-attributing diff and a transformation search gated by a semantics check, did not exist for x86-64 and C++, so concord is built around them.

Its conceptual ancestors are m2c, objdiff, decomp-permuter and decomp.me; the closest is decomp-permuter, to which concord adds directed search and an oracle. The name follows that lineage and names the goal state: the recompiled object in agreement with the target byte for byte. parity (byte parity with the target) was the runner-up.

## Status

The diff, the transforms and the searches work against Harvest; the seed, types, flag inference and oracle stages are stubs (`concord seed`, `types`, `flags`, `init` and `status` say so). The searches stay safe without the oracle in two ways: every rewrite is meant to preserve behavior, and a change goes upstream only when the target's own matcher proves the function byte-identical, which makes it equivalent at the machine level. `docs/DESIGN.md` has the full design.

Upstream results so far, each verified by Harvest's `hv match` on every unit:

| Pull request | Produced by | Functions newly exact |
| --- | --- | ---: |
| [banteg/harvest#19](https://github.com/banteg/harvest/pull/19) | `concord sweep` (definition order) | 9 |
| [banteg/harvest#21](https://github.com/banteg/harvest/pull/21) | `concord sweep` at a deeper budget | 7 |
| [banteg/harvest#22](https://github.com/banteg/harvest/pull/22) | `concord vtables` (an extra pure virtual in two interfaces) | 3 |

### Requirements

- A Harvest checkout on which `hv match` has run, with its pinned GCC 4.4.3 container image (Harvest's `just toolchain`) and your own copy of the original game binaries in its `orig/`, which Harvest does not include. concord compiles through Harvest's own toolchain and matcher, under `build/concord/` in that checkout, and calls only what Harvest's public `hv` package provides.
- `c++filt` and `addr2line` from [GNU Binutils](https://www.gnu.org/software/binutils/), and `as` and `g++` for some tests.
- Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

### Commands

`concord diff` disassembles one function from a target object and a candidate object with [Capstone](https://www.capstone-engine.org/), aligns the instructions, and labels each mismatch: register allocation, operand order, block order, inlining, stack or struct layout, immediate, or instruction selection. Pointed at a Harvest checkout, it reads Harvest's delinked target and compiled objects. For a function whose bytes already match, it reports the references or placement that keep Harvest's matcher from proving it exact, and when every call to an unknown symbol agrees on its address it names the `hv match --learn` run that places it:

```sh
uv run concord diff --harvest ../harvest --unit HarvestFull/harvest/gui/CProfileScreen.cpp _ZN7harvest3gui14CProfileScreen11saveProfileEb
uv run concord diff --target target.o --candidate candidate.o SYMBOL
uv run concord survey --harvest ../harvest --lines 20
uv run concord permute --harvest ../harvest --near-miss 98
```

`concord survey` counts the causes over every inexact function in a Harvest build. With `--lines N` it also compiles every unit with `-g` (GCC 4.4 emits the same code either way), follows each finding's inline chain with [addr2line](https://sourceware.org/binutils/docs/binutils/addr2line.html), and lists the N source lines in the checkout behind the most findings; a header line there gathers the findings of every copy inlined from it. On Harvest the top lines are all in `CString.h` and `CPosition2d.h`.

`concord vtables` compares every vtable the compiled units emit with the target's, slot by slot; a slot that differs means a class declaration with an extra, missing or misplaced virtual, which shifts the vtable references of every constructor and destructor that uses it.

`concord sweep --harvest ROOT [--apply]` runs Harvest's definition-order search (`hv search`) on every unit with an inexact function and reports the functions its verification compile confirms as newly exact; with `--apply` it writes a winning order only when it is a pure reorder of the unchanged source.

`concord match --harvest ROOT --unit UNIT SYMBOL` searches source rewrites for one function, directed by the diff: it takes the rewrites for the causes the diff reports (comparison operand swaps for operand order, branch-sense flips for block order), compiles them in one batch through Harvest's toolchain, and keeps the best one that improves the function without losing any exact function of the unit, for up to `--rounds` rounds.

`concord permute --harvest ROOT --unit UNIT SYMBOL`, or `--near-miss SCORE` for every function of its own unit's source scoring at least SCORE (one constructor or destructor variant each), is a random multi-step search in the manner of decomp-permuter. Each candidate applies one to three random rewrites from every transform, whatever the diff reports: operand swaps, branch-sense flips, swapping independent neighbouring statements, moving declarations, and naming a subexpression in a local. Batches are compiled together, and the walk moves to the best candidate that scores at least as well without losing an exact function of the unit, so it can cross plateaus where single rewrites change nothing. Its first sweep made `CGUIListBox::draw` and the `CSystemConfig` constructors exact with branch-sense flips that the diff had not attributed to block order. With `--apply` it writes only an exact match.

Rewrites come out the way a person would write them: a negated condition inverts its comparison or applies De Morgan's laws, and swapped branches keep the file's brace layout. A found match is submitted as concord produced it.

Every `concord match`, `concord permute` and `concord sweep` run appends a line to `results/harvest.jsonl`: the Harvest commit it ran against, the unit and function, the settings and transforms, the scores before and after, and the functions it made exact. An improved source is saved as a patch against that commit in `results/patches/`, applicable with `git apply` from the Harvest checkout. The log is the record of what has been tried, and the knowledge base summarizes what it taught.

The knowledge base in `src/concord/knowledge/` records a compiler's codegen idiosyncrasies, one TOML file per compiler version. Each idiom names the cause the diff reports, the symptom, the source change that fixes it, whether that is confirmed or a hypothesis, the limits found when applying it, and the evidence (a Harvest commit or note). `concord diff` lists the idioms for the causes it finds. Add an idiom, or evidence to one, whenever a fix makes a function match.

## Development

concord uses [uv](https://docs.astral.sh/uv/).

```sh
uv run concord --help
uv run --locked ruff check src tests
uv run --locked python -m unittest discover -s tests
```

The Harvest tests in `tests/test_diff.py` run when a Harvest checkout after `hv match` is at `../harvest` or `CONCORD_HARVEST` names one, and skip otherwise. `AGENTS.md` holds the working rules for agents: manual steps in matching work become concord capabilities, and every complete match goes upstream with the partial gains found alongside it.

## License

concord is under the [MIT License](LICENSE). `results/harvest.jsonl` and the knowledge base quote short fragments of Harvest source as evidence. The source patches that `concord match` and `concord permute` save under `results/patches/` stay local and untracked, since Harvest carries no license that would allow redistributing them.
