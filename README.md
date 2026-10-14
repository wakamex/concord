# concord

Tools for matching recompilation of x86-64 C++: finding source that, compiled with the original toolchain and flags, produces object code byte-identical to a target binary. concord verifies candidate source with the target project's own matcher and says what to change; it works today against [Harvest](https://github.com/banteg/harvest), a matching decompilation of a GCC 4.4.3 Linux amd64 game.

## The problem concord solves

A decompiler recovers what a function does. A match needs source whose compiled bytes equal the target's, and the compiler discards most of what would identify that source: many source forms give the same bytes, and a one-line change can move hundreds. Functions that are nearly right differ in register choice, block order, comparison operand order and inlining order, which are the compiler's decisions and respond only indirectly to how the source is written.

On Harvest, what decided those cases was more often the function's context than its body: which declarations and inline definitions the translation unit sees, in what number and order, and where a class's member bodies are written. Two examples concord found there: GCC 4.4 orders some operands by a declaration counter taken modulo 31, so one more declaration in a header can change a function elsewhere; and a virtual member defined after its class instead of inside it makes every including unit emit the class's vtable.

## Approach

concord has three kinds of tool, each a module under `src/concord/`.

- Verification. Every candidate is compiled with the target's pinned compiler and compared by the target's matcher and by [objdiff](https://github.com/encounter/objdiff), the score Harvest reports upstream. A change is kept only if no other function in the build scores lower. A rewrite that leaves a function inexact is also run against its previous form on generated inputs by [harvest-oracle](https://github.com/wakamex/harvest-oracle), and dropped if the behavior differs.
- Diagnosis. `diff` attributes each mismatch to a cause and lists the compiler idioms recorded for that cause in `src/concord/knowledge/`. `vtables`, `layout`, `phase` and `survey` check a build's context: virtual tables, struct layout, sensitivity to the declaration count, and which source lines stand behind the most mismatches.
- Source. `seed` recovers a file from the source of a known ancestor, which for Harvest's engine is [Irrlicht](https://irrlicht.sourceforge.io/) 0.7, translated into the target's names and placed in the target. `unguard`, `complete` and `header` recover the headers such files need. `match`, `permute` and `sweep` search rewrites of existing source: definition order, branch sense, named temporaries.

Most matches so far came from the third kind through an ancestor: 271 of the 295 functions in the table below. The rewrite searches found the rest and have a low yield on functions that are already close (`match` made 0 of 146 exact, `permute` 3 of 79). concord does not write a function's source from its disassembly; a person, an agent or a model does that, and concord's part is to tell that author what to change and to prove the result.

## Relationship to existing tools

- [decomp-permuter](https://github.com/simonlindholm/decomp-permuter) applies randomized permutations to source and keeps the ones that score better. concord's `permute` and `match` do the same for C++ on x86-64, choose rewrites by the cause the diff found, and reject a rewrite that changes behavior or lowers another function's score.
- [objdiff](https://github.com/encounter/objdiff) and [asm-differ](https://github.com/simonlindholm/asm-differ) score the gap. concord uses objdiff's score and adds cause attribution.
- [m2c](https://github.com/matt-kempster/m2c) decompiles MIPS, ARM, PowerPC and SuperH assembly into C aimed at matching. concord has no decompiler: it starts from source that exists, the target project's or an ancestor's.
- [decomp.me](https://decomp.me/) is the compile-in-the-loop scoreboard. concord runs that loop locally against a project's own matcher.

## Origin and name

concord came out of work on Harvest, where the functions left inexact differ in codegen artifacts and closing them was mostly people guessing source forms. It began as a plan for a decompiler-seeded search checked against a lift of the original executable by [rev.ng](https://rev.ng/); measured on Harvest, neither rev.ng's nor [Ghidra](https://ghidra-sre.org/)'s output addressed what keeps functions inexact, and rev.ng's lifted code could not run beside native code on that executable, so the oracle compares two compiles instead and seeding uses ancestor source. That plan and the evaluation are in the repository's history (`docs/` before its removal).

The name is the goal state: the recompiled object in agreement with the target byte for byte.

## Status

Every command works against Harvest and only against Harvest: concord compiles and compares through that project's toolchain and matcher. An exact match needs no oracle: the target's own matcher proves the function byte-identical, which makes it equivalent at the machine level. A partial gain carries no such proof, so `match`, `permute` and `rerun` compile the function before and after each rewrite, run both on 5,000 generated inputs with [harvest-oracle](https://github.com/wakamex/harvest-oracle) (comparing return values, calls out of the function and memory written), and drop the rewrite on any difference. concord uses `harvest-oracle` when it is on `PATH` (`uv tool install git+https://github.com/wakamex/harvest-oracle`); `CONCORD_ORACLE` names another command with the same `check BEFORE.o AFTER.o --symbol SYMBOL --unit UNIT --cases N` interface, or turns the check off when empty. A case that indexes past the end of a sized table, which reads whatever each build places after it, is reported as undefined and left out of the verdict. The oracle compares two compiles of Harvest's source, and `repair` uses its comparison with the original image to move a function's behavior toward the original's.

Upstream results so far, each verified by Harvest's `hv match` on every unit:

| Pull request | Produced by | Functions newly exact |
| --- | --- | ---: |
| [banteg/harvest#19](https://github.com/banteg/harvest/pull/19) | `concord sweep` (definition order) | 9 |
| [banteg/harvest#21](https://github.com/banteg/harvest/pull/21) | `concord sweep` at a deeper budget | 7 |
| [banteg/harvest#22](https://github.com/banteg/harvest/pull/22) | `concord vtables` (an extra pure virtual in two interfaces) | 3 |
| [banteg/harvest#23](https://github.com/banteg/harvest/pull/23) | `concord permute` branch flips, `concord diff` pointing at `hv match --learn`, and 20 partial gains from `concord rerun` | 4 |
| [banteg/harvest#26](https://github.com/banteg/harvest/pull/26) | `concord permute --near-miss 95` (a branch flip), with a NaN-sense repair and 2 partial gains checked by harvest-oracle | 1 |
| [banteg/harvest#27](https://github.com/banteg/harvest/pull/27) | `concord seed` from Irrlicht 0.7 (eight software rasterizers, six scene node animators, the fade-out particle affector, the PSD loader) | 112 |
| [banteg/harvest#28](https://github.com/banteg/harvest/pull/28) | `concord unguard` on `ISceneNode` (fields and inline bodies for the matching build), then `concord seed --complete` with headers placed by the Mac debug map (four scene nodes, the mesh manipulator, the collision response animator, two triangle selectors) | 159 |

### Requirements

- A Harvest checkout on which `hv match` has run, with its pinned GCC 4.4.3 container image (Harvest's `just toolchain`) and your own copy of the original game binaries in its `orig/`, which Harvest does not include. concord compiles through Harvest's own toolchain and matcher, under `build/concord/` in that checkout, and calls only what Harvest's public `hv` package provides. It keeps one driver process and one toolchain container per checkout for the length of a command, compiles and compares up to `CONCORD_JOBS` candidates at a time (8 by default; each comparison worker holds the loaded target, about 0.75 GB), and `concord clean` removes old work files.
- `c++filt` and `addr2line` from [GNU Binutils](https://www.gnu.org/software/binutils/), and `as` and `g++` for some tests.
- Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

### Commands

`concord seed --harvest ROOT UNIT...` recovers daisy files from Irrlicht 0.7. Harvest's engine, daisy, is a fork of [Irrlicht](https://irrlicht.sourceforge.io/) 0.7, and Harvest keeps Irrlicht's source in `third_party/irrlicht-0.7` as a reference, so a daisy file without recovered source can start from Irrlicht's file of the same name. seed translates it (the `irr` namespace to `daisy`, Irrlicht's integer typedefs to plain types, `core::rect<s32>` to `ox::core::CRect<int>` and the other core and video types to their `ox` equivalents, names Harvest declares in `ox` qualified there), translates any Irrlicht header the file needs that Harvest lacks, to the path and namespace Harvest's Mac debug map records for it (daisy kept Irrlicht's public headers under `ox`), adds Irrlicht's packed 16-bit colour helpers to `ox/video/ColorPacking.h` when the file calls them, and adds the `<iostream>` include when the Mac debug map shows the unit had a static initializer. It then registers the unit and places its `.text` where the target has a run of function extents with the sizes of the compiled object's functions, just before the unit's inline copies the matcher placed from known symbols (ld lays out an object's `.text` first), takes the `.bss` from the object its static initializer passes to `std::ios_base::Init`, and runs `hv match --learn`. A file that does not compile is unregistered and reported. The searches then work on whatever does not match yet.

From a clean checkout it seeds 16 daisy files (the eight software rasterizers, six scene node animators, the fade-out particle affector and the PSD image loader) to 112 matching functions, 14 files exactly, with no hand edits. With `--complete`, seed also adds the Irrlicht members a file needs to Harvest's partial `ox` core headers, including members Irrlicht declares in the class and defines after it, as `matrix4` does. Each added member shifts the declaration count of every unit that includes the header (see `concord phase`), so seed recompiles those units and keeps a completion only if none of their functions stops matching or scores lower; when one does, it adds further Irrlicht members of the same class, one at a time, until those units are back where they were, and otherwise restores the header and reports the file as blocked. `--allow-worse SYMBOL` accepts a completion that costs the named function, for a trade made knowingly. It is off by default. Files whose daisy versions changed Irrlicht's interfaces, such as the scene graph (daisy's `ISceneNode` and `ISceneManager` differ) or the image loaders (daisy's logger and colour formats), need more than translation.

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

Rewrites come out the way a person would write them: a negated condition inverts its comparison or applies De Morgan's laws, and swapped branches keep the file's brace layout. Searches write two forms that are safe without type information, and `concord.finish` rewrites both before a result is saved or applied, keeping each rewrite only if the function compiles to the same bytes. A negated comparison is searched as `!(a < b)` and inverted to `a >= b`; GCC folds the first into the second only when no operand can be NaN, so identical bytes also prove the two mean the same. A named temporary is searched as `__typeof__(E) concordTmpN = E;`; concord compiles the result with `-g`, reads each temporary's type from its DWARF entry, names it after its expression (`getScreenRes()` gives `screenRes`, `CString<char>(extension)` gives `extensionString`), and prefers direct initialization for a conversion. `--apply-improved` writes partial gains as well as exact matches. A found match is submitted as concord produced it.

`concord repair --harvest ROOT --unit UNIT SYMBOL --base REV` brings a function's behavior back toward the original code. A search keeps behavior fixed, so source that treats NaN differently from the original stays that way; repair tries each NaN-sense flip of a relational comparison (`a > b` against `!(a <= b)`, the same test for integers and different for floats only when an operand is NaN) whose compiled code differs, runs the original executable's own code and each candidate on the same 5,000 generated inputs with harvest-oracle, and keeps the flip that differs from the original in the fewest cases, as long as the function still scores above its score at `REV` and no other function of the unit scores lower. `match` and `permute` run the same step on their result. On `CPlayState::renderMinimap` it rewrote `!(it->Time > 4.0f)` as `it->Time <= 4.0f`, which sends a NaN marker time through the sized box as the original does, cutting differences from the original from 11 to 7 of 5,000 cases.

`concord layout --harvest ROOT --unit UNIT SYMBOL`, or `--struct-layout` for every inexact function whose diff names struct layout, compares where the original and our compile access memory. harvest-oracle's `accesses` follows each pointer from its source (`this`, argument N, a global, or a pointer loaded from one of those, such as `this.0x98`) and lists each load and store with its offset and size; concord compares the two lists per base as sets, since instruction order differs between compiles, and reports offsets only one side uses. On Harvest, 13 of the 14 functions the diff labels as struct layout access the same offsets as the original from every base, so the label there marks other differences; `CSpritePackage::load` shows a real one.

`concord unguard --harvest ROOT HEADER --port SOURCE` gives the matching build what a Harvest header keeps for the port. Harvest's port reimplements parts of daisy, and where it needed a class's fields or constructor they sit in the `ox` header behind `#ifdef HARVEST_PORT`, with the member functions out of line in a port source file. unguard makes the guarded declarations unconditional and writes each member's body inside the class, taken from the port source or, with `--irrlicht HEADER --unit UNIT`, from Irrlicht's class (also without its null check) when that scores no lower in the unit. The bodies go in the class because a virtual member declared there and defined later is the class's key function, and GCC then emits the vtable and every inline virtual in each unit that includes the header; on Harvest's `ISceneNode` that cost seven exact matches in three units until the bodies moved inside. `concord header --harvest ROOT NAME.h` translates one Irrlicht header to the path the Mac debug map records, and `concord complete --harvest ROOT TYPE.member...` adds Irrlicht members to a partial `ox` core header under the same check as `seed --complete`; `--fill TYPE UNIT` keeps adding the type's members until the functions `concord phase` reports locked in the unit are exact again.

`concord phase --harvest ROOT [UNIT...]` finds functions whose code depends on how many declarations come before them. GCC 4.4 numbers every declaration it parses and keeps a function's variables in a hash table keyed by that number, 31 slots wide for most functions, so the order it visits them in, and with it which operand of a comparison comes first, can change when one declaration is added anywhere earlier in the file, including an unused inline member in a header. phase compiles each unit with 0 to 30 extra declarations and lists two kinds of function: locked ones, exact only at another count, whose body is right and whose headers declare the wrong number of things; and witnesses, exact at the present count but not at every count, which stop matching when a header they include gains or loses a declaration. On Harvest at 2c9a9f5 the scan of all 135 units finds 1 locked function (the `std::sort` helper in `daisy/video/CVideoModeList.cpp`, exact with 14 to 30 more declarations) and 15 witnesses, two of them exact at one count in 31 (`CSpritePackage::removeAnimationState` and `CSimplePress::compressData`). The padding is a measurement and never a fix.

`concord scores` checks a batch the way decomp.dev will: it reads Harvest's progress report (`hv.progress`), with objdiff's fuzzy score and match state for every function and the bytes each matched data run covers. `--ref master --save FILE` snapshots the evidence committed on master; `--capture --against FILE` recaptures the checkout's evidence and lists every function whose score changed, marking the ones that scored lower or stopped matching, plus the matched data bytes gained and lost, and exits nonzero on any loss. Data is compared by bytes because two matched runs that merge into one look like a broken match in the report. The searches use the same objdiff score: candidates rank by it first, and a candidate that lowers it for any other function of the unit is rejected. `concord rerun --harvest ROOT` repeats each function's best logged partial gain with the same command and settings on the current checkout and writes the improvement, so gains found on an older commit come out in today's form.

Every `concord match`, `concord permute` and `concord sweep` run appends a line to `results/harvest.jsonl`: the Harvest commit it ran against, the unit and function, the settings and transforms, the scores before and after, and the functions it made exact. An improved source is saved as a patch against that commit in `results/patches/`, applicable with `git apply` from the Harvest checkout. The log is the record of what has been tried, and the knowledge base summarizes what it taught.

The knowledge base in `src/concord/knowledge/` records a compiler's codegen idiosyncrasies, one TOML file per compiler version. Each idiom names the cause the diff reports, the symptom, the source change that fixes it, whether that is confirmed or a hypothesis, the limits found when applying it, and the evidence (a Harvest commit or note). `concord diff` lists the idioms for the causes it finds. Add an idiom, or evidence to one, whenever a fix makes a function match.

## Roadmap

concord is the deterministic half of matching: it verifies candidates with the target's own matcher and says what to change. Something else writes the source, whether a person, an agent or a model harness, and such a harness is a separate project. The steps below are in order, and each is judged against the first.

1. A benchmark from functions that already match. Harvest has about 3,800 exact functions with known source. Mutation recovery perturbs one (a flipped branch, an inlined temporary, reordered declarations) and counts how often concord returns it to exact; held-out recovery measures how close concord's feedback brings an author who starts from the binary alone. Results are broken down by cause, so a search that never pays is visible. On real near misses, `match` made 0 of 146 exact and `permute` 3 of 79. `concord bench` measures mutation recovery, and `concord bench --locate` whether a case's diff points at the changed line. The current figures, on Harvest with 900 sampled functions, at most 20 cases per kind and 128 candidates per case:

   | Change applied to an exact function | concord has a rewrite that undoes it | Cases | Recovered by `match` | Recovered by `permute` |
   | --- | --- | ---: | ---: | ---: |
   | Swap a comparison's operands | yes | 20 | 10 | 20 |
   | Swap an if's branches | yes | 20 | 12 | 20 |
   | Swap adjacent statements | yes | 20 | 0 | 20 |
   | Move a declaration | yes | 20 | 0 | 20 |
   | Name a temporary | yes | 20 | 0 | 17 |
   | Inline a temporary | yes | 18 | 0 | 8 |
   | For loop as while loop | yes | 4 | 0 | 4 |
   | Spell out a compound assignment | yes | 9 | 0 | 9 |
   | Nest the halves of an `&&` | yes | 17 | 0 | 16 |
   | Merge nested ifs | yes | 3 | 0 | 3 |
   | Swap commutative operands | no | 4 | 0 | 2 |
   | Two changes of the kinds above | yes | 17 | 0 | 15 |
   | All | | 172 | 22 | 154 |

   Three findings shaped the search. The diff points at the changed line in 82% of cases and at 17% of a function's lines on average, so where to edit is a strong signal, while the cause it reports identifies the kind of edit for swapped comparisons and branches only; `match`, which picks rewrites by cause, recovers little. `permute` therefore tries every single rewrite first, nearest the attributed lines, before its random stage: on the same cases that recovered 111 of 151 where the random search alone recovered 104, at 20 compiled candidates per recovery where it took 32. And a change is recovered only when a rewrite undoes it: the loop, assignment and condition rows were 0 of 29 before those rewrites existed, and on the 73 cases the two runs share, adding them took recoveries from 38 to 62 with none lost, at 19.5 candidates per recovery where it took 17.3.
2. Causal diffs. For a differing instruction, find the GCC pass that made the decision, from the dumps the pinned compiler writes after each pass, and name the source construct that feeds it, so the finding reads "these two values are created in the wrong order at this line" instead of "operand order differs". The two findings that paid most on Harvest, the declaration count modulo 31 and the key function, were of this kind.
3. Context checks before body searches. Compare the inline functions each unit emits with what the target holds beside that unit; a difference means a header shows the unit a definition the original did not have, or hides one it had. This joins `vtables`, `layout` and `phase`.
4. Findings any author can consume. Each diff, cause and suggested edit is available as structured output with stable names, so a model harness or another project's agents can use them without parsing prose.

Seeding from Irrlicht continues beside these while it pays: 41 Irrlicht-named daisy files remain, most of them where daisy's classes differ from Irrlicht's, and the rename tables in `concord.irrlicht` would be better derived by aligning Irrlicht's names with the Mac symbols than written by hand.

## Development

concord uses [uv](https://docs.astral.sh/uv/).

```sh
uv run concord --help
uv run --locked ruff check src tests
uv run --locked python -m unittest discover -s tests
```

The Harvest tests in `tests/test_diff.py` run when a Harvest checkout after `hv match` is at `../harvest` or `CONCORD_HARVEST` names one, and skip otherwise. `AGENTS.md` holds the working rules for agents: manual steps in matching work become concord capabilities, and every complete match goes upstream with the partial gains found alongside it.

## Goals

What concord is for. These are meant to stay fixed; they change only if the aim does.

1. Progress is functions made exact in a target project and accepted there. Benchmark recoveries, fuzzy scores and search counts are means to that, since a score can rise while nothing new matches.
2. A result counts only when the target's own matcher proves it and no other function in the build scores lower, because a gain in one function has repeatedly cost an exact match elsewhere through a shared header or the declaration count.
3. A function's behavior is kept, except where the oracle shows a change brings it closer to the original's; a matching search must not drift away from what the code does.
4. What goes upstream is concord's output. A step done by hand in matching work becomes a general concord capability with tests first, so the next function gets it for free and the result can be reproduced.
5. Concord tells whoever writes source, a person, an agent or a separate model harness, what to change, and proves the result. It does not write a function from its disassembly itself.
6. Submitted source reads as if a person wrote it for the project: its layout, names and idioms, since a maintainer reviews and keeps it.

## How we work now

The current best way toward the goals. It changes as tools and knowledge improve; an item that is superseded is rewritten or removed.

Method
- Find out what blocks a function or file by measuring before building: the benchmark for searches, `phase` for declaration-count sensitivity, `--locate` for where the diff points. The two findings that paid most, the declaration count modulo 31 and the key function, came from understanding the compiler, not from more search.
- Seed from ancestor source while it pays: on Harvest, Irrlicht 0.7 gave most of the functions concord has upstreamed. Place headers where the target's reference tables put them, and keep a shared-header change only if every file that includes it is no worse.
- Grow the search's vocabulary on evidence: add a rewrite, with its inverse, when the benchmark or a real near miss shows a change the search cannot undo. The loop, assignment and condition rewrites came that way, and the condition rewrite then matched three real functions.
- Try single rewrites nearest the attributed lines before random ones; on the benchmark that recovered more cases with about a third fewer compiles.

Evidence
- Judge a search change against the previous version on the same benchmark cases, then confirm it on the target's real near misses, since benchmark cases are easier than real ones.
- When a change is meant to preserve output, compare hashes of every output before and after, as for each driver and delinker speedup.
- Run lint and the tests after the last edit and before every push; a push that ran only the tests has failed CI.

Upstream
- Open a pull request in the target whenever concord makes functions exact, with the partial gains it found along the way, linking the concord commits behind them; push concord first so the links resolve. Tooling fixes and behavior fixes the oracle supports can go in their own pull request, with any score they cost stated.
- Before calling a Harvest pull request ready, run the whole-build score comparison, `zig build harvest` and the tests, and wait for every CI check; one pull request failed the port build that had not been run.
- Rebase and recapture evidence when the target's master moves; a conflicting pull request gets no CI at all.

The machine
- Cap parallel work: `CONCORD_JOBS` workers (8 by default, 2 when memory is short) and only start long jobs with at least 4 GB free. Sizing pools by processor count once took 22 GB and the kernel killed other services.
- Run long jobs as their own systemd units, one at a time, and remove old work files with `concord clean`.

Speed
- Profile before rewriting for speed. The slow Python was repeated work (re-parsing a symbol table, a container start per request) and was fixed in Python with identical output; a native rewrite waits until the profile says it is worth it.

## License

concord is under the [MIT License](LICENSE). `results/harvest.jsonl` and the knowledge base quote short fragments of Harvest source as evidence. The source patches that `concord match` and `concord permute` save under `results/patches/` stay local and untracked, since Harvest carries no license that would allow redistributing them.
