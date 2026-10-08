# rev.ng for Harvest matching

This evaluation was run against [Harvest](https://github.com/banteg/harvest), a matching decompilation, in a local checkout. Repository paths below, such as `src/`, `config/1.18-linux-amd64/symbols.tsv`, `docs/matching.md`, `tools/` and `build/`, are relative to that checkout.

Question: can this machine's patched rev.ng make material progress on the Linux amd64 matching percentage, either by drafting new units faster or by closing the inexact functions that recovered units still have?

Result: no. rev.ng lifts the whole 2.5 MB executable in under five minutes, but its default raw-ABI C is harder to read than Ghidra's output on every sampled function, it drops strings and switch tables, and its float code goes through QEMU softfloat helpers. The analysis that would give it real prototypes, `detect-abi`, ran out of memory after 42 minutes at a 14 GB RAM plus 8 GB swap cap. Ghidra 12.1.3, already installed at `~/.local/opt/ghidra/ghidra_12.1.3_PUBLIC`, imported, analyzed and decompiled the same functions in 67 seconds with a 1 GB peak, with correct `this` pointers, parameters, named library calls, strings and switch cases. Neither tool addresses what keeps recovered functions inexact, which is GCC 4.4 register allocation, block layout, operand order and inlining order.

Rerun on 2026-10-04 with runtime 2026-10-03c1: `detect-abi` now completes in 4 minutes at 2.2 GB, and calls in the emitted C are named, but strings, jump-table cases and softfloat are unchanged, and the C cannot yet run next to native code on this executable. See [the rerun section](#rerun-on-runtime-2026-10-03c1-2026-10-04), which also judges rev.ng in concord's oracle and types roles.

How it was tested: rev.ng ran through the shared `revng-current` launcher, which resolved to runtime `revng-runtime-2026-09-29d.chunk01` (rev.ng consolidated commit 4d788cc86) for every step, with a private cache directory. Both tools received the executable plus the function names and addresses from `config/1.18-linux-amd64/symbols.tsv`, then decompiled the same seven functions. Five have recovered source in `src/` to check against; two are still unrecovered. Upstream was at `e62c486` for the baseline and the sample status.

## Baseline at e62c486

`just progress` reports 141,016 of 1,998,094 code bytes matched (7.06%) and 928 of 4,976 unwind-table functions exact (18.6%), with 13.1% fuzzy. The recovery table in `docs/matching.md` shows new game units being drafted quickly, while the functions left inexact differ almost entirely in register choice, block order, loop-compare operand order or inlining order.

## Sample functions

| Function | Size (bytes) | Status at e62c486 |
| --- | ---: | --- |
| `ox::io::CMemReadFile::read` | 65 | exact |
| `ox::core::CHiddenFloat::modifyValue(float, float, float)` | 129 | exact |
| `ox::net::CVariablePacketParser::fetchFixedString` | 351 | exact |
| `ox::net::CHTTPConnectionHandler::OnEvent` | 1,682 | inexact, 98.5% fuzzy |
| `harvest::entity::CSparkMoverEntity::acceptsSparkFrom` | 20 | exact |
| `harvest::CHarvestSuperReceiver::OnEvent` | 487 | not recovered |
| `harvest::gui::CIngameMenuScreen::OnEvent` | 667 | not recovered |

## Cost of each tool on this host

| Step | Tool | Wall time | Peak memory | Outcome |
| --- | --- | ---: | ---: | --- |
| Project init without auto-analysis | rev.ng | 1.8 s | 0.2 GB | succeeded |
| `parse-binary` (stripped ELF: 3 functions found) | rev.ng | 13 s | 0.2 GB | succeeded |
| `set-model` with 2,356 named text symbols | rev.ng | seconds | small | succeeded |
| `emit-c` for the seven functions, which lifts the whole image | rev.ng | 4 min 46 s | 10.4 GB RSS, 6.9 GB swap | succeeded, raw register ABI |
| `import-prototypes-from-db` | rev.ng | 7.8 s | 0.3 GB | succeeded |
| `detect-abi` | rev.ng | 42 min | 14 GB RAM cap plus 8 GB swap cap | killed by the OOM killer |
| Import, full auto-analysis, 2,812 names, decompile seven functions | Ghidra 12.1.3 headless | 67 s | 1.0 GB | succeeded |

The rev.ng model was built from the previous day's `symbols.tsv` (2,356 text names) and Ghidra from the current one (2,812). All seven sample functions are named in both. The host carried other workloads during the rev.ng runs, and the Ghidra run overlapped `detect-abi`; neither changes the order of magnitude.

## Output quality

| Function | Ghidra lines | Ghidra calls shown as `FUN_` addresses | Ghidra string literals | rev.ng lines | rev.ng calls shown as segment offsets | rev.ng `undef` operands | rev.ng softfloat helper calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `CMemReadFile::read` | 22 | 0 | 0 | 9 | 1 | 0 | 0 |
| `CHiddenFloat::modifyValue(fff)` | 28 | 0 | 0 | 29 | 0 | 2 | 3 |
| `CVariablePacketParser::fetchFixedString` | 68 | 0 | 0 | 89 | 5 | 0 | 0 |
| `CHTTPConnectionHandler::OnEvent` | 257 | 14 | 11 | 407 | 12 | 0 | 0 |
| `CSparkMoverEntity::acceptsSparkFrom` | 16 | 0 | 0 | 7 | 0 | 0 | 0 |
| `CHarvestSuperReceiver::OnEvent` | 82 | 1 | 3 | 127 | 6 | 0 | 0 |
| `CIngameMenuScreen::OnEvent` | 95 | 14 | 2 | 190 | 10 | 0 | 0 |

Lower counts in the address, `undef` and helper columns are better. Ghidra's `FUN_` calls go to functions `symbols.tsv` does not name yet; rev.ng shows those and also imported library functions such as `memcpy` and `operator new[]` as offsets from `segment_0.plt` or `.text`. rev.ng printed no string literals in any sample; Ghidra printed each localized text key and resource path.

Observations against the recovered source:

- `acceptsSparkFrom` and `CMemReadFile::read`: both tools recover the logic. rev.ng takes six register arguments and returns an artificial struct of `rax` and `rdx`, so `this`, parameters and member offsets must be mapped by hand. Ghidra gives `CSparkMoverEntity *this, int param_1` and a `bool` result.
- `CHiddenFloat::modifyValue`: Ghidra recovers the clamp as ordinary float comparisons, calls `CRand::rand` by name, and shows the XOR decoding; it prints the decoded bits as a numeric `(float)(uint)` conversion where the source reinterprets them. rev.ng lifts the SSE code through `float32_add`, `float32_compare_quiet` and `float32_lt`, passes `undef` operands, writes zeros to absolute address 64 (lifted CPU-state bookkeeping), and loses the stack slot that holds the result. Harvest is float-heavy game code, so this affects most gameplay functions.
- `fetchFixedString`: both show the inlined `CString` assignment loop. Ghidra names `operator_new__` and `memcpy`.
- `CIngameMenuScreen::OnEvent` (unrecovered): Ghidra resolves the jump table into cases, shows `L"menu:abandonGame"`, names `CSystemConfig::getLocalizedText`, and marks the exception-handler range. rev.ng switches on raw jump-table targets (`case 4667424U`) and shows no strings.

## Uses rev.ng could still have

- Whole-program LLVM IR of the executable, produced in five minutes. The matching workflow has no consumer for it: `hv match` compares GCC objects byte for byte, and fuzzy scoring already comes from objdiff.
- Data-layout and prototype recovery through `analyze-data-layout` and `convert-functions-to-cabi`. Both depend on `detect-abi`, which did not finish within 22 GB here. The Mac debug map, the ported vtables and the Irrlicht 0.7 headers already supply class and member structure more reliably.

Trigger to revisit, as first written: rev.ng gains a bounded `detect-abi` that completes on this executable within the host's memory, and its C then names imports, strings and jump tables. The 2026-10-04 rerun meets the `detect-abi` half and the imports part of the C half; the updated trigger is at the end of that section.

## Rerun on runtime 2026-10-03c1 (2026-10-04)

Question: can rev.ng now serve [concord](../../README.md)'s oracle role on this executable, which means lifting a target function once and running candidate functions differentially against it, and is the trigger above met?

Result: the analysis half of the trigger is met and the execution side is not ready. `detect-abi` completes on the whole executable in 4 min 22 s at 2.2 GB peak resident memory, where runtime 29d was killed at 42 minutes under a 22 GB cap, and all 5,001 functions get a prototype. After it, the C calls imports and internal functions by name instead of through computed segment addresses. String literals still print as segment offsets, jump-table cases are still raw target addresses, and scalar float still goes through QEMU softfloat helpers. The emitted C for all seven sample functions compiles with Fedora clang 22, and the two oracle candidates link as shared objects, but neither can run next to its native function yet: the closure-batch tooling that does this for hl-node does not transfer to Harvest unchanged, and calls into imports pass arguments in rev.ng's raw register order. `analyze-data-layout` does not finish within an hour.

How it was tested: the same executable (SHA-256 `ac381fa0...`), the same 2,356 `symbols.tsv` names taken from the 29d run's model, and the same seven functions, through `revng-current`, which resolved to `work/revng-runtime-2026-10-03c1` (fork commit `3e0bffbff`). Every step ran network-isolated inside one capped transient user unit holding the shared heavy-job lock. The host was also running a 100-hour training job, so the cap was 9 GB of RAM plus 11 GB of swap, a 20 GB limit split to leave that job its memory. A second pass on a copy of the project ran `analyze-data-layout` before `convert-functions-to-cabi`, rev.ng's usual order, after the first pass had run it afterwards. Scripts, step logs (`steps.txt`, `steps2.txt`), models and emitted C are in `build/revng-rerun-2026-10-04/`.

### Cost on this host

| Step | Wall time | Peak resident memory | Outcome |
| --- | ---: | ---: | --- |
| `parse-binary`, adding the names, `set-model` | 7 s | 0.2 GB | succeeded: 2,356 names on 2,359 functions |
| Whole-image lift | 3 min 29 s | 9.4 GB at the 9 GB cap, plus 1.2 GB swap | succeeded (29d: 4 min 46 s, 10.4 GB plus 6.9 GB swap) |
| `import-prototypes-from-db` | 2 s | 0.2 GB | succeeded |
| `detect-abi` | 4 min 22 s | 2.2 GB | succeeded: 5,001 raw register prototypes (29d: killed by the OOM killer at 42 min) |
| `detect-stack-size` | 12 min | 7.7 GB | succeeded |
| `detect-c-strings` | 10 min | 7.7 GB | succeeded |
| `convert-functions-to-cabi` | 1 to 3.5 min | 0.7 GB | succeeded: 2,026 of 5,001 functions get System V prototypes |
| `emit-c` for the seven functions, raw prototypes | 4 min 32 s | 8.8 GB | succeeded |
| `emit-c` for the seven functions, after conversion | 53 s | 1.6 GB | succeeded in the second pass; aborted on one function in the first (see below) |
| `emit-type-and-global-header` | 21 s | 1.4 GB | succeeded: 7.6 MB of plain C |
| `emit-helper-header` | 25 min | 8.9 GB | succeeded |
| `analyze-data-layout`, before conversion | over 60 min | at the RAM cap | stopped at the 1-hour limit |
| `analyze-data-layout`, after conversion | 11 min | 7.0 GB | assertion `CABIFT->Arguments().size() == F.arg_size()` in `DLACreateInterProceduralTypes.cpp:87` |

The raw-prototype `emit-c` time includes recomputing the isolated functions after the second pass replaced the model.

### Prototypes

`detect-abi` gives every function a raw register prototype. For the samples:

- Exact: `CSparkMoverEntity::acceptsSparkFrom` and `CHTTPConnectionHandler::OnEvent`, each `this` plus one argument in `rdi` and `rsi`, with the result in `rax`. These two are the samples `convert-functions-to-cabi` turns into System V prototypes.
- The true arguments plus extras: `CMemReadFile::read` gets `rdi`, `rsi` and `rdx` plus `zmm0` to `zmm7`. `CHiddenFloat::modifyValue`, which takes `this` and three floats, gets `rcx`, `rdx`, `rsi`, `rdi` and `zmm0` to `zmm7`, and returns `zmm0`. The functions that make virtual calls get every argument register, consistent with the runtime passing positional argument registers through at indirect call sites. The extras keep these five raw, and appear as `undef` operands where they are called.

### Output quality

The rev.ng columns use the raw-prototype C after `detect-abi`, and the 29d columns the first run's raw C. A call through a computed address has `&segment_0 + N` or `&segment_0.plt + N` as its target; any other `&segment_0 + N` operand is a data address. The first table's "calls shown as segment offsets" counted both kinds.

| Function | 29d lines | c1 lines | 29d calls through computed addresses | c1 calls through computed addresses | c1 data addresses printed as offsets | 29d `undef` | c1 `undef` | c1 softfloat calls | c1 string literals | c1 cases labeled by raw address |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `CMemReadFile::read` | 9 | 7 | 1 | 0 | 0 | 0 | 2 | 0 | 0 | 0 |
| `CHiddenFloat::modifyValue(fff)` | 29 | 45 | 0 | 0 | 0 | 2 | 0 | 3 | 0 | 0 |
| `CVariablePacketParser::fetchFixedString` | 89 | 81 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `CHTTPConnectionHandler::OnEvent` | 407 | 502 | 15 | 0 | 11 | 0 | 66 | 0 | 0 | 0 |
| `CSparkMoverEntity::acceptsSparkFrom` | 7 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `CHarvestSuperReceiver::OnEvent` | 127 | 331 | 2 | 0 | 7 | 0 | 0 | 0 | 0 | 0 |
| `CIngameMenuScreen::OnEvent` | 190 | 166 | 15 | 0 | 2 | 0 | 0 | 0 | 0 | 20 |

Lower is better in every column except the lines. Imports print by name (`memcpy`, `CString` methods) and unnamed functions as `function_0x5ede40_Code_x86_64`. Every remaining data address in the samples is a string in `.rodata`, such as `"GET "`, `"HTTP/1.1"`, `"$HARVEST_USERDATA$/screenshots/"` and `L"menu:abandonGame"`, apart from one code address used as a value in `CHarvestSuperReceiver::OnEvent`. The 66 `undef` operands in `CHTTPConnectionHandler::OnEvent` are the extra arguments of callees whose prototypes were over-approximated, such as the `CString` destructor. The Ghidra columns of the first table still apply, so the seed comparison is unchanged.

### Oracle role

The oracle needs lifted C for a target function that compiles, links with the rest of the program delegated to native code, and runs next to the native function on the same inputs. Each function's `emit-c` output went through `revng ptml --plain`, prefixed with the project's emitted `types-and-globals.h` and `helpers.h`, and compiled against the runtime's `attributes.h`, `primitive-types.h` and `runtime-library.h`. Linking reused the closure batch's target-independent support: `revng_runtime_library.c`, `runnable_traps.c` and the wrappers from `revng_intrinsic_wrappers.py`.

| Function | Compiles with clang 22.1.8 | Links as a shared object | Unresolved after linking that support |
| --- | --- | --- | --- |
| `CMemReadFile::read` | yes | yes | libc only, including `memcpy` |
| `CHiddenFloat::modifyValue(fff)` | yes | yes | `float32_add`, `float32_compare_quiet`, `float32_lt` and `ox::algo::CRand::rand` |
| The other five samples | yes | not tried | |

Running either one next to its native function is blocked by these, in the order a run would meet them:

1. Calls into imports use rev.ng's raw register order. `memcpy` is declared `memcpy(rcx, rdx, rsi, rdi, r8, r9)`, so a C compiler passes the destination in `rcx`'s slot and libc's `memcpy` receives its arguments in the wrong registers. `import-prototypes-from-db` did not give it a C prototype. The closure batch adapts such calls with providers generated from the slice exporter's manifest.
2. The slice exporter (`export_retained_slice.py`) is fed by retained hl-node fact files: a default ABI model, function rows with body hashes, a function table and a prototype model. The whole-image `detect-abi` model, `symbols.tsv` and the unwind table could supply Harvest's, but nothing produces them yet. `run_revng_control.py` cannot take the whole image instead: it emits the whole program as one file and compiles all of it, with a 240-second limit per command.
3. `native_image.py`, which maps the original executable next to the generated library, assumes a position-independent image with a TLS segment. Harvest is fixed at `0x400000` and has no `PT_TLS` (`native_image.py host-defines` fails at once), its 10 `R_X86_64_COPY` relocations are a type `relocate()` rejects, and the host lacks `libCg`, `libCgGL` and `libalut` among its needed libraries; LuaJIT and SFML come from the game's `bin/`.
4. `build_qemu_helpers.py` builds a fixed list of ten float64 and condition-code helpers. Harvest's float code calls float32 helpers.
5. `undef` operands trap through `undef_value` unless a benign version is linked, which affects `CMemReadFile::read` (two) and `CHTTPConnectionHandler::OnEvent` (66).

Runtime 10-03c1 had a caching defect. In a project that had emitted C before `detect-abi`, `emit-c` after `convert-functions-to-cabi` asserted in the Clifter on `acceptsSparkFrom` (`F->arg_size() == Function.getArgumentTypes().size()`, `Clifter.cpp:1926`), while a project that received the same converted model through `set-model` emitted all seven functions. The cached C for that function was not rebuilt when `detect-abi` gave it a prototype. Runtime 2026-10-04e fixes it, as the next section shows.

### Runtime 2026-10-04e fixes the C emission abort; data layout still aborts (2026-10-05)

Runtime 2026-10-04e fixes the stale cached output behind the Clifter assertion. It ran the same sequence on a fresh project, including the `emit-c` before `detect-abi` that left stale C in 10-03c1's project, under the same cap. Its run directory is `build/revng-rerun-2026-10-05e/`.

| Check | Runtime 10-03c1 | Runtime 2026-10-04e |
| --- | --- | --- |
| `emit-c` after `convert-functions-to-cabi`, in a project that emitted C before `detect-abi` | aborted on `acceptsSparkFrom` (`Clifter.cpp:1926`) | succeeded for all seven functions in 46 s |
| That C against a fresh project's C | not applicable | identical apart from generated type IDs (such as `struct_14235` against `struct_14215`): three files byte for byte, four after normalizing those IDs |
| `analyze-data-layout` after conversion | asserted `CABIFT->Arguments().size() == F.arg_size()` (`DLACreateInterProceduralTypes.cpp:87`) at 11 min | aborted later, at 12 min and 7.3 GB, in `DLATypeSystemLLVMBuilder::getLayoutType` (`DLATypeSystemBuilder.h:196`), reached from `connectToFuncsWithSamePrototype` (`DLACreateIntraProceduralTypes.cpp:764`), without an assertion message |
| Functions emitted with a "starts at non-executable address" abort, which the runtime's string-detection fix addresses | none | none |

The lift, `detect-abi` (4 min 31 s, 2.4 GB) and the later analyses took within 10% of the 10-03c1 times.

### Runtime 2026-10-05f completes data-layout analysis (2026-10-06)

Candidate runtime 2026-10-05f, which was still in qualification, removes the step that aborted on runtime e (`connectToFuncsWithSamePrototype`). It ran the same sequence on a fresh project under the same cap, followed by a second `convert-functions-to-cabi` and `emit-c` once data-layout analysis had finished. Its run directory is `build/revng-rerun-2026-10-05f/`.

| Step after the first `convert-functions-to-cabi` | Wall time | Peak resident memory | Outcome |
| --- | ---: | ---: | --- |
| `analyze-data-layout` | 12 min 1 s | 7.1 GB | completed: structs grow from 3,749 to 25,261, plus 137,946 unions |
| `convert-functions-to-cabi` again | 12 s | 2.3 GB | completed: 2,023 System V and 2,964 raw prototypes, as before; 20,747 structs and 16,006 unions remain |
| `emit-c` for the seven functions | 51 s | 2.4 GB | completed |

After data-layout analysis, member accesses print as struct fields named by offset. `acceptsSparkFrom` takes `struct_22803 *argument_0` and reads `argument_0->offset_56.member_0` and `argument_0->offset_64`, and `CMemReadFile::read` reads `offset_24`, `offset_32` and `offset_36` of its `this` struct. The recovered types carry layout without class identity or names, so they can check a recovered class's member offsets but cannot name its members. The earlier steps took within 10% of runtime e's times.

### Verdict per role

- Seed: Ghidra, unchanged. rev.ng's calls are now named, but strings, switch cases, softfloat and the extra register arguments keep its C further from source than Ghidra's.
- Oracle: not usable on Harvest yet. The analysis side now fits this host easily and the C compiles. Execution needs a C prototype or register-order adaptation for imports, Harvest facts for the slice exporter, native image host support for a fixed-address executable without TLS and with copy relocations, and float32 helpers.
- Types: a cross-check at most. On runtime 2026-10-05f, `analyze-data-layout` completes in 12 minutes and recovers member offsets for the sample functions, but without class identity or names. The Mac debug map, ported vtables and Irrlicht 0.7 headers remain the source of class layout; the recovered offsets can confirm it. The Mac debug map, ported vtables and Irrlicht 0.7 headers remain the source of class layout.

Trigger to revisit, updated: for the oracle role, one Harvest function's lifted C runs next to its native function through the closure-batch method, which requires the four tooling items above. For the seed and types roles, the emitted C names strings and jump-table cases, and, for types, a runtime that completes `analyze-data-layout` here (candidate 2026-10-05f does) becomes the shared `revng-current`.

## Recommendation

Use Ghidra headless for first drafts of new units: it is already installed, costs about a minute for the whole image, and can take the current `symbols.tsv` names before analysis. The two scripts used here (a pre-analysis name importer and a per-address decompiler) are small enough to fold into `tools/` if drafting throughput becomes the bottleneck. Upstream already works with Binary Ninja, so a Binary Ninja license on this host would add little; if one is bought anyway, only the Commercial or Ultimate tiers run headless, which this machine requires.

For concord, rev.ng is still the intended oracle, but it cannot run candidates on Harvest until the oracle blockers in the rerun section are fixed. concord's first slice does not need it: a byte-exact match is equivalent at the machine level by construction, and definition-order moves keep semantics. The oracle becomes necessary once transforms can change behavior.

For the matching percentage itself, the leverage is in the inexact tail and in new units, and neither decompiler reproduces GCC 4.4 codegen choices. Time is better spent on `hv search` definition-order experiments and on drafting untouched units from Ghidra output, the Mac names and the Irrlicht 0.7 sources.
