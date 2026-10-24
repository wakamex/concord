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

Most matches so far came from the third kind through an ancestor: 1,132 of the 1,159 functions in the table below. The rewrite searches found the rest and have a low yield on functions that are already close (`match` made 0 of 146 exact, `permute` 3 of 79). concord does not write a function's source from its disassembly; a person, an agent or a model does that, and concord's part is to tell that author what to change and to prove the result.

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
| [banteg/harvest#30](https://github.com/banteg/harvest/pull/30) | `concord seed` on `CSkyBoxSceneNode` with daisy's skybox UV order | 45 |
| [banteg/harvest#34](https://github.com/banteg/harvest/pull/34) | `concord permute` with the nested-`if` condition rewrite | 3 |
| [banteg/harvest#35](https://github.com/banteg/harvest/pull/35) | `concord seed --complete --stub` on nineteen daisy scene and PCX loader units | 231 |
| [banteg/harvest#36](https://github.com/banteg/harvest/pull/36) | `concord seed --complete --stub` on the remaining twenty-two Irrlicht daisy units (`CSceneManager`, `CImage`, `CVideoSoftware`, `CXAnimationPlayer`, `CAnimatedMeshSceneNode`, `CTerrainSceneNode`, `CQ3LevelMesh`, XML/text readers, BMP/TGA/JPG loaders, and exact completions of `COctTreeTriangleSelector` and `CMetaTriangleSelector`) | 383 |
| [banteg/harvest#37](https://github.com/banteg/harvest/pull/37) | `concord seed --complete --stub` again on twenty-one Irrlicht daisy units, with the container, colour and inline-helper translations that let their stubbed methods take Irrlicht's bodies; 12 of the 21 units exact | 202 |

### Requirements

- A Harvest checkout on which `hv match` has run, with its pinned GCC 4.4.3 container image (Harvest's `just toolchain`) and your own copy of the original game binaries in its `orig/`, which Harvest does not include. concord compiles through Harvest's own toolchain and matcher, under `build/concord/` in that checkout, and calls only what Harvest's public `hv` package provides. It keeps one driver process and one toolchain container per checkout for the length of a command, compiles and compares up to `CONCORD_JOBS` candidates at a time (8 by default; each comparison worker holds the loaded target, about 0.75 GB), and `concord clean` removes old work files.
- `c++filt` and `addr2line` from [GNU Binutils](https://www.gnu.org/software/binutils/), and `as` and `g++` for some tests.
- Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

### Commands

`concord seed --harvest ROOT UNIT...` recovers daisy files from Irrlicht 0.7. Harvest's engine, daisy, is a fork of [Irrlicht](https://irrlicht.sourceforge.io/) 0.7, and Harvest keeps Irrlicht's source in `third_party/irrlicht-0.7` as a reference, so a daisy file without recovered source can start from Irrlicht's file of the same name. seed translates it (the `irr` namespace to `daisy`, Irrlicht's integer typedefs to plain types, `core::rect<s32>` to `ox::core::CRect<int>` and the other core and video types to their `ox` equivalents, names Harvest declares in `ox` qualified there), translates any Irrlicht header the file needs that Harvest lacks, to the path and namespace Harvest's Mac debug map records for it (daisy kept Irrlicht's public headers under `ox`), adds Irrlicht's packed 16-bit colour helpers to `ox/video/ColorPacking.h` when the file calls them, and adds the `<iostream>` include when the Mac debug map shows the unit had a static initializer. It then registers the unit and places its `.text` where the target has a run of function extents with the sizes of the compiled object's functions, just before the unit's inline copies the matcher placed from known symbols (ld lays out an object's `.text` first), takes the `.bss` from the object its static initializer passes to `std::ios_base::Init`, and runs `hv match --learn`. A file that does not compile is unregistered and reported. The searches then work on whatever does not match yet.

From a clean checkout it seeds 16 daisy files (the eight software rasterizers, six scene node animators, the fade-out particle affector and the PSD image loader) to 112 matching functions, 14 files exactly, with no hand edits. With `--complete`, seed also adds the Irrlicht members and enums a file needs to Harvest's partial `ox` headers, including members Irrlicht declares in the class and defines after it, as `matrix4` does. Each added declaration shifts the declaration count of every unit that includes the header (see `concord phase`), so seed recompiles those units and keeps a completion only if none of their functions stops matching or scores lower; when one does, it adds further Irrlicht members of the same class, one at a time, until those units are back where they were, and otherwise restores the header and reports the file as blocked. `--allow-worse SYMBOL` accepts a completion that costs the named function, for a trade made knowingly. It is off by default. With `--stub`, a function of a seeded file whose body does not compile keeps its declarator and gets a body that traps, so the rest of the unit can compile and match while the stubbed function stays inexact. Files whose daisy versions changed Irrlicht's interfaces, such as the scene graph (daisy's `ISceneNode` and `ISceneManager` differ) or the image loaders (daisy's logger and colour formats), need more than translation.

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

`concord blockers --harvest ROOT [UNIT...]` says why each stubbed method of a seeded unit still traps. It puts the method's Irrlicht body, translated as `seed` translates it, back into the unit as it stands, compiles each unit's candidates in one batch, keeps the errors inside the method, reduces each to the type or member it is about, and ranks the causes by how many stubs each blocks. `--apply` replaces only the stub bodies whose candidates compile, after checking that they also compile together; other functions, declarations and headers keep their existing bytes. It rejects compilation failures outside the restored method as well. Compilation alone does not prove a match: run symbol learning twice and `concord scores --capture --against BEFORE.json` before keeping the change.

`concord rename --harvest ROOT UNIT...` gives each file-local helper the translation left in a unit the plain name a person would write: where a member a shared ox header cannot add is expanded to a file-local helper (`CMatrix4::interpolate`, `CTriangle3d::getIntersectionWithLine`), translate marks it `_concord_` and the name it gets in the unit is picked so it cannot collide with the names the unit or its headers already use. Renaming a file-local helper does not change the unit's compiled code; `concord scores --capture --against` a snapshot from before the change shows it.

`concord learn-calls --harvest ROOT [UNIT...] [--apply] [--save FILE]` supplements Harvest's exact-function symbol learning with aligned direct calls from callers whose addresses are already named, or whose complete non-relocation bytes identify exactly one target function of the same size. Unnamed callers with unsupported relocation widths are excluded, and their learned callees carry `aligned-unique-call:` evidence. It uses the diff's instruction shapes and requires unique matching context around each call. Every aligned call site naming a callee must agree on its address, every compiled copy must have the target extent's size, and no existing or proposed symbol may claim that address. Calls are checked across every available match report; optional units restrict which callees may be named. `--apply` saves the addresses through Harvest's symbol writer with `aligned-call:` evidence naming the caller and both call offsets. `--save` records all aligned sites and accepted addresses for review. Score the learning step separately before restoring bodies; naming a previously misplaced function can lower its reported score even when its code is unchanged.

`concord unguard --harvest ROOT HEADER --port SOURCE` gives the matching build what a Harvest header keeps for the port. Harvest's port reimplements parts of daisy, and where it needed a class's fields or constructor they sit in the `ox` header behind `#ifdef HARVEST_PORT`, with the member functions out of line in a port source file. unguard makes the guarded declarations unconditional and writes each member's body inside the class, taken from the port source or, with `--irrlicht HEADER --unit UNIT`, from Irrlicht's class (also without its null check) when that scores no lower in the unit. The bodies go in the class because a virtual member declared there and defined later is the class's key function, and GCC then emits the vtable and every inline virtual in each unit that includes the header; on Harvest's `ISceneNode` that cost seven exact matches in three units until the bodies moved inside. `concord header --harvest ROOT NAME.h` translates one Irrlicht header to the path the Mac debug map records, and `concord complete --harvest ROOT TYPE.member...` adds Irrlicht members to a partial `ox` core header under the same check as `seed --complete`; `--fill TYPE UNIT` keeps adding the type's members until the functions `concord phase` reports locked in the unit are exact again.

`concord inlines --harvest ROOT [UNIT...]` compares the out-of-line copies a unit emits (the weak COMDAT `.text._Z...` functions GCC writes for ODR-used inline definitions, implicit constructors and destructors, and template instantiations) with what the target holds beside that unit, and says where they differ: copies whose size disagrees with the extent the matcher placed them at (`different definition`, a fatter or thinner body than the original's), copies the matcher could not place classed by the evidence for them (`expected` when the Mac debug map credits the unit, `elsewhere` when it credits only other units, `possible-home` when an unnamed extent of the same size sits in the unit's span, `surplus` when nothing anywhere records the name), target extents in the unit's span that no emitted copy accounts for, Mac-map rows the unit no longer emits, and per-function call differences: a direct call in ours where the target's copy has none is an inline the original inlined that we call out of line, and the reverse. The map's N_SOL markers name the header each credited function was defined in, so a finding reads as "a header shows this unit a definition the original did not have, or hides one it had" before anyone searches a body. On `CTerrainSceneNode` it names the 671-byte destructors the original wrote at 345 by way of a base-class destructor the unit inlines differently, and the `std::vector<S3DVertex2TCoords>` copies the stubbed `createIndexBuffers` suppresses; `--save FILE` writes the findings as JSON.

`concord phase --harvest ROOT [UNIT...]` finds functions whose code depends on how many declarations come before them. GCC 4.4 numbers every declaration it parses and keeps a function's variables in a hash table keyed by that number, 31 slots wide for most functions, so the order it visits them in, and with it which operand of a comparison comes first, can change when one declaration is added anywhere earlier in the file, including an unused inline member in a header. phase compiles each unit with 0 to 30 extra declarations and lists two kinds of function: locked ones, exact only at another count, whose body is right and whose headers declare the wrong number of things; and witnesses, exact at the present count but not at every count, which stop matching when a header they include gains or loses a declaration. On Harvest at 2c9a9f5 the scan of all 135 units finds 1 locked function (the `std::sort` helper in `daisy/video/CVideoModeList.cpp`, exact with 14 to 30 more declarations) and 15 witnesses, two of them exact at one count in 31 (`CSpritePackage::removeAnimationState` and `CSimplePress::compressData`). The padding is a measurement and never a fix.

`concord scores` checks a batch the way decomp.dev will: it reads Harvest's progress report (`hv.progress`), with objdiff's fuzzy score and match state for every function and the bytes each matched data run covers. `--ref master --save FILE` snapshots the evidence committed on master; `--capture --against FILE` recaptures the checkout's evidence and lists every function whose score changed, marking the ones that scored lower or stopped matching, plus the matched data bytes gained and lost, and exits nonzero on any loss. Data is compared by bytes because two matched runs that merge into one look like a broken match in the report. The searches use the same objdiff score: candidates rank by it first, and a candidate that lowers it for any other function of the unit is rejected. `concord rerun --harvest ROOT` repeats each function's best logged partial gain with the same command and settings on the current checkout and writes the improvement, so gains found on an older commit come out in today's form.

Every `concord match`, `concord permute` and `concord sweep` run appends a line to `results/harvest.jsonl`: the Harvest commit it ran against, the unit and function, the settings and transforms, the scores before and after, and the functions it made exact. An improved source is saved as a patch against that commit in `results/patches/`, applicable with `git apply` from the Harvest checkout. The log is the record of what has been tried, and the knowledge base summarizes what it taught.

The knowledge base in `src/concord/knowledge/` records a compiler's codegen idiosyncrasies, one TOML file per compiler version. Each idiom names the cause the diff reports, the symptom, the source change that fixes it, whether that is confirmed or a hypothesis, the limits found when applying it, and the evidence (a Harvest commit or note). `concord diff` lists the idioms for the causes it finds. Add an idiom, or evidence to one, whenever a fix makes a function match.

`concord place --harvest ROOT UNIT...` runs symbol learning and verifies placement candidates for unplaced template sections called by each unit, preserving its existing placements. It exposes the same placement recovery used by `seed` without regenerating source. `--restore-literals` also tries the corresponding ancestor string literals in existing functions; it keeps a repair only when the target matcher proves the function exact and no other function in the unit scores lower. Whole-build scoring remains the final gate. For misnamed complete/base constructor copies, `learn-calls --constructor-aliases --apply` can exchange their existing address claims when both copies already match exactly, their compiled code and relocation targets are identical, no other symbol claims either address, and all aligned call sites agree on the exchange. These claims carry `aligned-alias:` evidence.

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
3. Context checks before body searches. Compare the inline functions each unit emits with what the target holds beside that unit; a difference means a header shows the unit a definition the original did not have, or hides one it had. `concord inlines` does this, joining `vtables`, `layout` and `phase`.
4. Findings any author can consume. Each diff, cause and suggested edit is available as structured output with stable names, so a model harness or another project's agents can use them without parsing prose.

### Finishing Harvest's platform layer

`python -m hv.progress report`, with [banteg/harvest#37](https://github.com/banteg/harvest/pull/37) applied to Harvest `b4def65`, splits Harvest's Linux binary into four layers (plus 264 PLT thunks, all matched). The table below divides each layer's functions into those already exact, those in a recovered unit that do not match yet, and those in no recovered unit at all. Bytes are target code bytes; more exact is better.

| Layer | Functions (bytes) | Exact functions (bytes, share of layer) | Inexact, in recovered units (bytes) | In no recovered unit (bytes) |
| --- | ---: | ---: | ---: | ---: |
| game | 1,452 (707,867) | 1,338 (426,703, 60.3%) | 107 (280,971) | 7 (193) |
| engine | 1,611 (385,833) | 1,508 (277,732, 72.0%) | 98 (107,875) | 5 (226) |
| toolkit | 558 (231,777) | 487 (147,269, 63.5%) | 69 (84,113) | 2 (395) |
| platform | 1,619 (623,132) | 1,288 (365,119, 58.6%) | 167 (168,296) | 164 (89,717) |

All 42 daisy units in the Mac debug map that have a file in `third_party/irrlicht-0.7/source/Irrlicht/` are seeded; the only two unrecovered daisy units without a `.cpp` file are the Winsock network device and its socket object (46 functions, 9.6 KB). The 164 functions in the last column of `platform` sit inside the address ranges of seeded units: unclaimed `.text.*` template instantiations and unnamed `.text` functions shifted behind the units' remaining stubs. When a stubbed method gets its real body, the template instantiations it calls are emitted and the functions after it move back to their target FDEs (`COctTreeTriangleSelector` went from 5/11 to 13/13 exact when `constructOctTree` was un-stubbed).

Eight units still hold 33 out-of-line `__builtin_trap()` stubs (`CXFileReader` 10, `CAnimatedMeshSceneNode` 6, `CQ3LevelMesh` and `CTerrainSceneNode` 4 each, `CSceneManager` and `CSceneCollisionManager` 3 each, `CAnimatedMeshMD2` 2, `CBspTree` 1), and two headers hold one each (`CBspTree::BspPoly::operator==`, `CSceneManager::DefaultNodeEntry`'s constructor). `concord blockers` restores each stub's Irrlicht body into its unit as the unit now stands and compiles it: 21 of the 32 with an Irrlicht body compile as they are, because the translation has grown since they were stubbed, and the other 11 stop on one or two causes each:

| Cause | Stubs it blocks |
| --- | ---: |
| `ox::algo` not included (`CTerrainSceneNode::createIndexBuffers`, `CXFileReader::parseDataObjectSkinWeights`) | 2 |
| a cast from a pointer to a narrower integer (`CQ3LevelMesh::createCurvedSurface`, `Blend`) | 2 |
| conversions between Irrlicht's and ox's types (`CTerrainSceneNode::getIdealHeightMap`, `CQ3LevelMesh::loadTextures`) | 2 |
| `CQ3LevelMesh::loadTextures` also needs `RGB16`, `IFileSystem::existFile` and `IVideoDriver::addTexture` as daisy declares them | 1 |
| `createSceneManager` constructs a class still abstract in daisy | 1 |
| `ISceneManager::addDummyTransformationSceneNode` with daisy's signature (`CAnimatedMeshSceneNode::getMS3DJointNode`) | 1 |
| the 9-argument `S3DVertex` constructor (`CBspTree::splitPoly`) | 1 |
| `CTriangle3d::getIntersectionWithLine` (`CSceneCollisionManager::getCollisionPoint`) | 1 |
| `CVector3d::setLength` (`CSceneCollisionManager::collideWithWorld`) | 1 |

So the platform work is, in order:

1. Re-seed the eight units with stubs, so the 21 bodies that compile now replace their stubs, and check each unit and the whole build; a compiling body is not yet a matching one.
2. Fix the shared causes above, smallest first: the `ox::algo` include, the narrowing casts, and the three `ox` core members, each by translation at the call site where completing the shared header would shift other units' declaration counts. The abstract class and the changed `ISceneManager` and driver signatures are where class reconciliation from Harvest's reference data starts: build each class's header from Irrlicht's declaration aligned against `reference/1.18-mac-i386/functions.csv` (6,406 compiled methods with demangled signatures), `reference/1.18-mac-i386/vtables.csv` (354 virtual tables slot by slot) and the field offsets `layout` and `harvest-oracle`'s `accesses` read from the original's memory accesses.
3. Diagnose the 20 inexact functions left in the units of banteg/harvest#37 with `phase`, `layout` and the emitted inline copies before more body search: definition-order moves and single rewrites did not make `CMeshSceneNode::render` exact, and the camera transforms and text-node destructors cannot be fixed by placement, since Harvest's matcher places a known function at its recorded address whatever the section placement says.

## Development

concord uses uv.

```sh
uv run concord --help
uv run --locked ruff check src tests
uv run --locked python -m unittest discover -s tests
```

The Harvest tests in `tests/test_diff.py` run when a Harvest checkout after `hv match` is at `../harvest` or `CONCORD_HARVEST` names one, and skip otherwise.

## Goals

What concord is for. These are meant to stay fixed; they change only if the aim does.

1. Progress is functions made exact in a target project and accepted there. Benchmark recoveries, fuzzy scores and search counts are means to that, since a score can rise while nothing new matches.
2. A result counts only when the target's own matcher proves it and no other function in the build scores lower, because a gain in one function has repeatedly cost an exact match elsewhere through a shared header or the declaration count.
3. A function's behavior is kept, except where the oracle shows a change brings it closer to the original's; a matching search must not drift away from what the code does.
4. What goes upstream, and every trial in a target checkout, is concord's output: never hand-edit the target. A new matching step is built as a general concord capability with tests first, so the next function gets it for free and the result can be reproduced.
5. Concord tells whoever writes source, a person, an agent or a separate model harness, what to change, and proves the result. It does not write a function from its disassembly itself.
6. Submitted source reads as if a person wrote it for the project: its layout, names and idioms, since a maintainer reviews and keeps it.

## How we work now

The current best way toward the goals. It changes as tools and knowledge improve; an item that is superseded is rewritten or removed.

Method
- Delegate implementation and unit-by-unit runs to implementer sessions, keeping the master session on diagnosis, work-order framing, and whole-build verification; doing implementation in the master session burns its turns on compile-and-test loops that a lighter session or parallel bake-off handles faster and cheaper.
- Find out what blocks a function or file by measuring before building: the benchmark for searches, `phase` for declaration-count sensitivity, `--locate` for where the diff points. The two findings that paid most, the declaration count modulo 31 and the key function, came from understanding the compiler, not from more search.
- Check a unit's context before searching its function bodies: virtual table slots (`vtables`), field offsets (`layout`), emitted inline copies, and the declaration count (`phase`). On Harvest, wrong headers and out-of-class key functions kept functions inexact across whole units where body rewrites could not move them.
- Un-stub seeded ancestor methods before running body searches on them. All 42 Irrlicht-backed daisy units are now seeded, and a 2-byte `__builtin_trap()` stub not only leaves that method inexact but also omits the `.text.*` template instantiations it calls and shifts the `.text` offsets of unnamed functions and `_GLOBAL__I_a` after it. Derive class changes from the target's recorded symbols, virtual tables and memory accesses, and keep a shared-header change only if every file that includes it is no worse.
- When a seeded unit calls a short inline `ox::core` or `ox::video` helper (`CRect::clipAgainst`, `SColor::toA1R5G5B5`, `SColor::set`, `CVector3d::rotateXYBy`, `CVector3d::rotateXZBy`, `CMatrix4::interpolate`) and completing the shared `ox` header cannot settle without shifting `DECL_UID` modulo 31 in other units, expand the inline body at the call site in `translate` or place the free helper in `ox/video/ColorPacking.h` rather than touching the shared header or stubbing the caller. When a `.cpp` unit constructs a 9-argument `S3DVertex`, `qualify` includes `ox/video/S3DVertexInline.h` so `S3DVertex.h` keeps its declaration count.
- Grow the search's vocabulary on evidence: add a rewrite, with its inverse, when the benchmark or a real near miss shows a change the search cannot undo. The loop, assignment and condition rewrites came that way, and the condition rewrite then matched three real functions.
- Try single rewrites nearest the attributed lines before random ones; on the benchmark that recovered more cases with about a third fewer compiles.

Evidence
- Judge a search change against the previous version on the same benchmark cases, then confirm it on the target's real near misses, since benchmark cases are easier than real ones.
- When a change is meant to preserve output, compare hashes of every output before and after, as for each driver and delinker speedup.
- Run lint and the tests after the last edit and before every push; a push that ran only the tests has failed CI.

Upstream
- Open a match pull request in the target only after closing the stragglers in the batch (remaining `__builtin_trap()` stubs, unplaced sections, and near-exact functions in the touched units), once the batch raises the target's progress by at least one percentage point (matched code or fuzzy match, as `python -m hv.progress report` gives them against master); completeness per unit avoids follow-up churn on the same files. It links the concord commits behind the matches; push concord first so the links resolve. Tooling fixes and behavior fixes the oracle supports can go in their own pull request at any time, with any score they cost stated.
- When replacing a provisional class header with Irrlicht's full class, keep the provisional stand-in under `#ifdef HARVEST_PORT` so the port's stub implementations still compile. Before calling a Harvest pull request ready, run the whole-build score comparison, `zig build harvest`, `zig build census` (which Web CI links with `--whole-archive`), `git diff --check`, and the tests, and wait for every CI check.
- Rebase and recapture evidence when the target's master moves; a conflicting pull request gets no CI at all.

The machine
- Cap parallel work: `CONCORD_JOBS` workers (8 by default, 2 when memory is short) and only start long jobs with at least 4 GB free. Sizing pools by processor count once took 22 GB and the kernel killed other services.
- Run long jobs as their own systemd units, one at a time, and remove old work files with `concord clean`.

Speed
- Profile before rewriting for speed. The slow Python was repeated work (re-parsing a symbol table, a container start per request) and was fixed in Python with identical output; a native rewrite waits until the profile says it is worth it.

## Implementer comparison

Implementation work is handed to implementer sessions, so which one gets a work order matters. Five implementers were given the same task once, in a single turn with no help: extend concord's translation so that nine stubbed methods across seven seeded daisy units (`CSceneManager`, `CXAnimationPlayer`, `C3DSMeshFileLoader`, `CCameraMayaSceneNode`, `CTestSceneNode`, `CBspTreeSceneNode`, `CWaterSurfaceSceneNode`) compile under Harvest's GCC 4.4 dialect (`-std=gnu++98`), with lint and tests passing. Each ran in its own worktree of concord.

| Implementer | Units compiling, of 7 | Methods compiling, of 9 | Wall time | Tokens | Cost | Notes |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| [Devin](https://devin.ai/) with `swe-2-high` | 7 | 9 | 558 s | 9.79M | none on the subscription | the cleanest C++98 and concord idioms, reusing `ox/algo/CArrayFunctions.h` |
| [Codex](https://openai.com/codex/) with `gpt-6.1-sol` | 7 | 9 | 490 s | 3.16M | $0.74 at API rates | 3.1 times fewer tokens; also fixed a curly-quote bug in `missing_members` |
| [Cursor](https://cursor.com/) with `composer-2.5` | 5 | 4 | 383 s | 4.30M | | wrote C++11 lambdas, which GCC 4.4 rejects |
| [Claude Code](https://www.anthropic.com/claude-code) with `claude-haiku-5-5` | 1 | 2 | 900 s, timed out | 11.59M | $0.67 at API rates | broke the include order of `SColor.h` for six units |
| Codex with `gpt-6-luna` | 1 | 1 | 61 s | 0.71M | $0.01 at API rates | stopped after one translation rule |

`swe-2-high` and `gpt-6.1-sol` are the implementers for open-ended work across several units, such as un-stubbing by shared blocker; `swe-2-high`'s changes were the ones applied, and re-seeding with them made 141 functions newly exact. `claude-haiku-5-5` suits only tightly specified mechanical steps, and the other two are not reliable for GCC 4.4 C++98 work. This is one task run once, so it guides the choice of implementer rather than ranking them.

## License

concord is under the [MIT License](LICENSE). `results/harvest.jsonl` and the knowledge base quote short fragments of Harvest source as evidence. The source patches that `concord match` and `concord permute` save under `results/patches/` stay local and untracked, since Harvest carries no license that would allow redistributing them.
