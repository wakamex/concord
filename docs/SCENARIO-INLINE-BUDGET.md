# CScenario credit calls exhaust the unit budget and then the caller budget

Harvest's `CScenario::createScenarioEvents` inlines the Jens credit and leaves Jonas out of line, while the shipped binary does the reverse. The deciding call is `CDialogueItemInfo` construction. Its nested `CString` constructors already inline into that shared body. With [GNU Compiler Collection (GCC)](https://gcc.gnu.org/) 4.4.3, the candidate accepts four credit edges before exhausting its unit budget; allowing more unit growth exposes a second limit, the caller's size. Two independent source overlays leave the function's bytes and score unchanged, so no Harvest change is retained.

The measurements use Harvest b8938c7, its pinned compiler at `-O2`, early-inliner dumps, interprocedural analysis (IPA) inline dumps and `-Winline` diagnostics. Unchanged-source compiles at six unit-growth caps identify accepted sites by their literal references and identical decision prefixes. The canonical function's code and relocation offsets equal the default diagnostic compile. The whole-build gate exits 0 with `0 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0`.

## The differing call encloses four strings

Both objects have two direct calls to `CDialogueItemInfo::CDialogueItemInfo(const wchar_t*, const wchar_t*, const char*, const char*, float, bool)`. Neither has a direct pointer-string constructor call in this function. The target calls the wrapper at offsets +0x2d4 and +0xa24; the candidate calls it at +0x9a7 and +0xa23. The first pair corresponds to Jens versus Jonas; Steve is out of line in both. Daniel, Pontus and Alexander inline in both.

In the early pass, GCC inlines both wide and both narrow pointer-string constructors into the wrapper. The pointer constructor's assignment call remains out of line at this stage: the wide assignment would grow its caller by 54 estimated instructions. All six calls from `createScenarioEvents` to the wrapper are rejected with `Not inlining: code size would grow by 136 insns.` These are estimated compiler instructions, rather than target byte counts.

The wrapper starts IPA at 155 instructions. Its two narrow assignments add 51 each, its two wide assignments add 54 each, and one narrow destructor adds 2: 155 + 51 + 51 + 54 + 54 + 2 = 367. These decisions occur before the wrapper is selected for the credits. The site difference therefore belongs to distribution of this 367-instruction wrapper, which carries the already inlined strings and exception cleanup.

## Accepted credit edges and the cutoff

The unit starts at 11,090 instructions. The default unit limit is floor(11,090 * 130 / 100) = 14,417. GCC tests this limit before the next heap pop, so the last accepted edge can cross it. The caller starts at 797 instructions and reaches 989 through earlier decisions. Its function limit is max(2,700, 797 * 2) = 2,700. Each wrapper call costs 19 instructions to retain out of line, giving edge growth 367 - 19 = 348. All six credit edges have frequency 1.00.

The following order is measured by lowering only `inline-unit-growth` and requiring the complete printed heap-decision prefix to agree. One newly accepted edge and one newly expanded literal are required for each join. Candidate declarations inserted into the heap are excluded from the heap-pop count.

| Credit site | Heap pop | Badness | Growth across remaining calls | Caller before -> after | Unit before -> after | Default decision |
| --- | ---: | ---: | ---: | --- | --- | --- |
| Jens, line 379 | 233 | 2,611 | +1,721 | 989 -> 1,337 | 13,356 -> 13,704 | Inline |
| Daniel, line 382 | 278 | 2,263 | +1,373 | 1,337 -> 1,685 | 13,704 -> 14,052 | Inline |
| Pontus, line 385 | 325 | 1,915 | +1,025 | 1,685 -> 2,033 | 14,052 -> 14,400 | Inline |
| Alexander, line 388 | 374 | 1,567 | +677 | 2,033 -> 2,381 | 14,400 -> 14,748 | Inline, crossing the unit cap |
| Jonas, line 391 | Unvisited | Unobserved at default cap | Unobserved at default cap | 2,381; another copy would reach 2,729 | Unit already 14,748 > 14,417 | Heap drained; `inline-unit-growth` |
| Steve, line 394 | Unvisited | Unobserved at default cap | Unobserved at default cap | 2,381; another copy would reach 2,729 | Unit already 14,748 > 14,417 | Heap drained; `inline-unit-growth` |

At a 34% unit cap, the same first four credit edges are accepted. The unit limit becomes 14,860. The two remaining edges reach heap pops 425 and 426, both at badness 1,219, frequency 1.00 and remaining-call growth +329. GCC rejects both with `--param large-function-growth limit reached`: 2,381 + 348 = 2,729 > 2,700. The dump does not identify which of those two rejected pops is Jonas; the warnings identify both source sites. The unit finishes at 14,735, because the extra decisions also eliminate an out-of-line helper. Increasing the unit cap alone preserves the credit distribution.

## Equal-cost edges depend on call-graph context

With guessed branch probabilities enabled and measured profiling disabled, GCC's [edge badness calculation](https://github.com/gcc-mirror/gcc/blob/releases/gcc-4.4.3/gcc/ipa-inline.c#L494) uses edge growth, frequency and the callee's growth across all remaining calls. It does not inspect a credit literal's spelling or length. For Jens, the calculation is floor(348 * 256 / 100) + 1,721 = 2,611. Calls to the same wrapper at the same frequency tie at any given instant. Each accepted copy changes the remaining-call growth and updates the heap keys. The [heap compares keys without a source-location tiebreaker](https://github.com/gcc-mirror/gcc/blob/releases/gcc-4.4.3/libiberty/fibheap.c#L69); call-graph insertion and key-update order can therefore determine which equal-cost edges consume the available growth.

The source properties feeding those quantities are the caller and callee bodies, the callee parameter move costs, call frequency and the graph's creation/update order. The candidate's measured winner order differs from the target's observed choice of four sites. The target's original source and compiler trace are unavailable, so its numeric estimates, heap keys and heap shape cannot be recovered from this binary. The evidence establishes the candidate's two gates and the target's different selection, but does not identify which original source property changed that selection. It does not establish a literal-specific cost difference or prove that changing definition order fixes it.

## Two source overlays produce identical function code

The first hypothesis was that moving the shared caller's definition changes graph construction or visitation order. The second was that direct nesting changes when the wrapper edge is created relative to allocation and event calls. Each overlay changes one source property and is measured through concord without writing Harvest source.

| Source property tested | Overlay | Specimen score before -> after | Function code and relocation offsets | Disposition |
| --- | --- | --- | --- | --- |
| Definition placement | Move the unchanged `getCharacterDialog` definition after `createScenarioEvents` | 90.367966 -> 90.367966 | Identical | Reject, no gain |
| Credit expression form | Nest each of the six `new CDialogueItemInfo` expressions directly in its `CAddDialogueEvent` call, removing the shared `credit` pointer | 90.367966 -> 90.367966 | Identical | Reject, no gain |

These scores are [objdiff](https://github.com/encounter/objdiff) percentages. The separate concord residual score remains 78.80 for the 2,981-byte target function. No candidate reaches author or its oracle because neither improves any score. No source lines are authored, no header is changed, and no Harvest commit is made. Stop source retries for this specimen until evidence identifies a change to the accepted-edge order or one of the measured estimates.

## Reproduce the diagnosis

Collect the default compiler trace with `concord causal`, then use its reported dump directory. Collection includes `-Winline` and saves the compiler command, source inputs and artifact hashes. `--warnings` with a Harvest checkout requires the unchanged warning artifact from that same collection.

```sh
concord causal --harvest ROOT --unit HarvestFull/harvest/game/CScenario.cpp --limit 1 --save trace.json _ZN7harvest4game9CScenario20createScenarioEventsEPN2ox5event14IEventReceiverE
concord inline-budget DUMP/trace.042i.inline --params-def GCC_SOURCE/gcc/params.def --warnings DUMP/compiler.stderr --harvest ROOT --unit HarvestFull/harvest/game/CScenario.cpp --symbol _ZN7harvest4game9CScenario20createScenarioEventsEPN2ox5event14IEventReceiverE --literal-callee CDialogueItemInfo --probe-unit-growth 20 --probe-unit-growth 21 --probe-unit-growth 24 --probe-unit-growth 27 --probe-unit-growth 30 --probe-unit-growth 34 --save frontier.json
```

The probe changes diagnostic compiler parameters only. Each sample retains its command, inputs, raw dumps, warning artifact and object hash. `site_order_witnesses` joins a site only when the prefix, unique newly accepted constructor edge and unique literal transition agree. Other cases remain unordered. `rejected_sites` names the final deciding parameter and reports final unit state and a projected caller size inferred from accepted calls to the same callee. A projection is not a claim that a drained edge was visited or passed another gate.

Compare the 34% dump to the default with `concord inline-budget CAP34/trace.042i.inline --against DEFAULT/trace.042i.inline --params-def GCC_SOURCE/gcc/params.def --warnings CAP34/compiler.stderr --save comparison.json`. Do not join the 34% object to the canonical compile as though its instructions explained the default build.

## Independent 37-line reproducer

[`frontier.cpp`](../tests/fixtures/gcc-4.4.3-inline/frontier.cpp) defines a text owner, a box containing four text owners and six consecutive box constructions. It has no Harvest dependency. Compile it with the pinned GCC 4.4.3 toolchain:

```sh
g++ -O2 -Winline -fdump-tree-einline-details -fdump-ipa-inline --param large-unit-insns=1 --param inline-unit-growth=30 -c frontier.cpp 2> warnings.txt
```

The lowered large-unit floor makes the same mechanism observable in a small translation unit. Each row below changes one parameter from the preceding row.

| Unit growth | Large-function threshold | Unit initial -> final | Unit cap | Box sites inlined | Final gate on remaining box sites |
| ---: | ---: | --- | ---: | --- | --- |
| 30% | 2,700 | 822 -> 1,154 | 1,068 | First | `inline-unit-growth` |
| 60% | 2,700 | 822 -> 1,397 | 1,315 | First, Second | `inline-unit-growth` |
| 60% | 700 | 822 -> 1,154 | 1,315 | First | `large-function-growth`; projected caller 822 > 700 |

GCC estimates `Box` at 257 instructions and each edge at +243. The first accepted edge is heap pop 22 and the second is pop 35. The checked-in `frontier-g30`, `frontier-g60` and `frontier-caller` dump excerpts retain the complete decision block and non-clone graph estimates in compiler order. Their warning files are unmodified. Adjacent provenance files record commands and source, raw-dump and excerpt hashes. Unit tests exercise both gates against these compiler-produced artifacts, including the caller rejection record the old parser omitted.

## Validation and effective inputs

The baseline `hv match` exits 1 for the existing residuals. The unchanged Harvest gate compares 5,240 functions and exits 0. It reports no configuration changes. Both `zig build harvest -j2` and `zig build census -j2` pass. Harvest's toolchain-enabled test run passes 232 tests with 3 skips. Concord's unit suite runs 562 tests with 22 skips and exits 0, and its lint passes. `git diff --check` passes in both checkouts.

The compiler is `g++ (Ubuntu 4.4.3-4ubuntu5) 4.4.3`, container image `b94ef6033199879fb811edc1d642923532b9213f245eb081646157559e3d45dd`. Effective options include `-O2`, `-Isrc`, `-Isrc/HarvestFull` and `-Ithird_party/luajit-2.0.0-beta8/include`. `-Q --help=optimizers` confirms early inlining, small-function inlining and guessed branch probabilities enabled, and measured branch probabilities disabled. Defaults are `large-unit-insns=10000`, `inline-unit-growth=30`, `large-function-insns=2700`, `large-function-growth=100` and `inline-call-cost=12`.

Input hashes use the [256-bit Secure Hash Algorithm (SHA-256)](https://csrc.nist.gov/pubs/fips/180-4/upd1/final).

| Input | SHA-256 |
| --- | --- |
| `CScenario.cpp` | `ee54f9e22125acdf1618e439f48b8260ae80675db6a74f93997242e9dfb2afab` |
| `CScenario.h` | `801d404664019cfeec2585cf4c1c54f6c6af47ddef17b16242d92b7063bac7c5` |
| `CStoryScreen.h` | `7690e2c8da9b58e486f968cc1dc50e1ae230c29c8b1358595b2e05865e76ec43` |
| `CString.h` | `7cc2521534a1b7864708fdc416a25279f05507d6c045abeca04545f4a88b95ee` |
| `flags.json` | `9ebb8c16c904108ddb5cbaa50345a733d400ee2bd3994c72e5545e676fb03ad8` |
| `units.toml` | `3778e24f111650836f8595ff618bd1661fc73ff02178f0c91e1a34d123a81f32` |
| `symbols.tsv` | `3b156c561057e2eac32112e9abbb0b842174d530df82b1ae1bd37091eaa5c21d` |
| Canonical unit object | `b247a2543d0f1d278a85278727c454d11bf3a8b5f4b132280b227620cfc5fd44` |
| Target unit object | `6c16af7ae7e196918e4c7cd721b79aeae1cff3f90dcc9fed350daf9f973bcc5e` |
| GCC `params.def` | `61b83e481583570c0727f228720d4948d8ff933c6cb2485de5e6c40c58c86e95` |
