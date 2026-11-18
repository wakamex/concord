# Review of the engine choice in docs/functional-equivalence.md by Gemini 4 Carbon (high)

Reviewer: gemini-4-carbon-high through agy, sealed profile, the note as its only input

Date: 2026-11-17
Reviewed: docs/functional-equivalence.md at concord 5d603b0
Prompt: docs/reviews/functional-equivalence-engine-carbon-prompt.md
Run: 091ad107-50b2-459e-9341-604f3713fd05, 923 seconds, $0.57

The review text below is unedited, including its own formatting.

### 1. Headline: Which engine, and why

**Do not run the proposed test; the shortlist in [functional-equivalence.md](file:///inputs/functional-equivalence.md#L96-L103) can be settled on paper.**

- **Eliminate `angr` and `Qiling` immediately.** `angr` is orders of magnitude too slow in Python/VEX, its `UNICORN` bridge constantly bails back to VEX on `SimProcedure` stubs and unmapped accesses, and VEX corrupts x87 state. `Qiling` is a leaky Python Win32-syscall emulator built to boot whole executables from `entry`; since your design stubs *every* callee at function boundaries, Qiling's OS layer is dead weight that lacks `thiscall`, lacks working 32-bit SEH dispatch, and adds severe Python hook overhead per run.
- **Eliminate in-process `native execution` (especially 32-bit PE under Wine) as the bulk per-function runner.** Address-space collisions, per-call trap latency on indirect calls, dirty-memory diffing across 4 GB, and non-deterministic wall-clock loop timeouts make it a trap at 135k functions.

**What to build instead:** a **combination** written in C++ or Rust (not Python):
1. **Primary per-function runner: `bochscpu` (the Bochs CPU core wrapper used by `0vercl0k/wtf`), or raw `Unicorn 2` in C/Rust if restricted to the note's list.** `bochscpu` is missing from the note and fixes Unicorn's worst pain points here: it lets you load 32-bit hidden segment descriptor caches (`fs.base`) directly without synthesizing a guest GDT, implements full 80-bit softfloat including transcendentals and `FCW` precision control, tracks dirty pages natively for sub-microsecond CoW resets, counts instructions deterministically, and avoids QEMU TCG's JIT translation-block cache thrashing across 100k short stubbed functions. (If you only ever run on bare-metal/VM x86-64 hosts, `wtf`'s other backend — a bare `KVM_RUN` / WHP VCPU — gives literal hardware x87 + EPT dirty-page logging + hypercall stubs with none of user-space native execution's address collisions.)
2. **Ground-truth oracle: Build Step 1's native detour rig ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L107))** for functions flagged by static scan as using x87 transcendentals/rounding tricks or Windows `/GX` exception unwinding.
3. **Decoupled input generation:** Coverage-guided fuzzing / concolic execution on the **reconstructed C/LLVM IR** (`LibFuzzer`/CMPLOG, `SymCC`, or `KLEE` — you have source on one side, which the note ignores) plus `Triton` on concrete binary traces only for branches unique to the original binary.

---

### 2. Factual claims about the tools

Several claims in [functional-equivalence.md](file:///inputs/functional-equivalence.md#L83-L102) are wrong, confused, or 10–15 years out of date:

- **`angr`'s x87 precision through VEX (and concrete execution):**
  - `libvex`/`pyvex` does lower 80-bit x87 registers to 64-bit IEEE `F64` (and leaves many x87 instructions/tag/flag edge cases unimplemented in `pyvex`/Claripy).
  - **What the note gets wrong about MSVC x87:** Line 26 notes MSVC sets `FCW = 0x027F`, then lines 91 and 98 claim Gruntz computes at "80-bit precision" and 64-bit VEX would therefore misreport it. **`0x027F` sets Precision Control bits 8–9 to `10b` (`_PC_53`), which *is* 53-bit significand (64-bit double) precision — not 80-bit (`0x037F`).** Under `0x027F`, real x87 already rounds `fadd`/`fsub`/`fmul`/`fdiv`/`fsqrt` significands to 53 bits. Where VEX's `F64` actually diverges from `0x027F` hardware is: (a) x87's 15-bit exponent range (`[-16382, +16383]`) in `ST(i)`, changing intermediate overflow/underflow and subnormal double-rounding on spills; (b) `fld tbyte` and transcendentals (`fsin`, `fpatan`, `f2xm1`, `fyl2x`), which ignore `PC` and retain 64-bit significands; and (c) dynamic `fldcw` changes (MSVC's `_ftol` setting chop rounding, or DirectX clobbering `FCW` to 24-bit `0x007F`).
  - **Self-contradiction & outdated backend claim:** Line 83 says `angr` *"runs concrete code through Unicorn"*; line 98 says *"Concrete runs go through Python. It translates instructions through VEX."* In reality, `SimEngineVEX` is default, `SimEngineUnicorn` is opt-in (`angr.options.UNICORN`) and falls back to VEX at every `SimProcedure` stub or unsupported sync, and `angr` also has a Ghidra SLEIGH backend (`SimEnginePcode`), so it is not VEX-only. Saying *"angr's current behaviour has not been checked"* for a 5-line check is just lazy.
- **Unicorn's x87 precision ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L100)):**
  - False that Unicorn matches the real CPU on x87. Unicorn 1 forked from QEMU 2.1.2; Unicorn 2 forked from **QEMU 5.0.1** (May 2020). Joseph Myers's patches rewriting QEMU's x87 helpers (`f2xm1`, `fyl2x`, `fpatan`, `fptan`, `fsin`, `fcos`, `fprem`) into `floatx80` softfloat landed in **QEMU 5.1**. In QEMU 5.0.1 (`target/i386/fpu_helper.c`), softfloat builds cast `floatx80` **down to 64-bit host `double`**, call host `libm` (`pow`, `atan2`, `sin`, `cos`), and cast back. Even with `USE_X86LDOUBLE` or QEMU ≥ 5.1 softfloat, QEMU uses full-range $\pi$ reduction rather than Intel's 66-bit microcode $\pi$, diverges on C1/C2 status flags and NaN payload propagation, and Unicorn has had chronic `uc_reg_read`/`uc_reg_write` bugs indexing `ST(0)..ST(7)` relative to `FPSW.TOP`.
- **What `angr` and `Qiling` supply for 32-bit MSVC (`thiscall`, SEH, `fs`):**
  - **`fs`:** Both map a dummy TIB page (`fs:[0] = -1`, `fs:[0x18] = TIB`, `fs:[0x30] = PEB`). Doing that yourself in raw Unicorn 32-bit mode is ~30 lines of C to populate one GDT entry (`UC_X86_REG_GDTR`; `FS_BASE` MSR only works in 64-bit mode).
  - **`thiscall`:** `angr` has `SimCCMicrosoftThiscall`, which puts `this` in `ecx` and cleans the stack, **but does not know MSVC C++ ABI rules** from line 39 (e.g., member functions returning small POD structs by value always taking a hidden stack pointer where free functions return in `eax`/`edx:eax`, or `ctor` returning `this`) without manual per-prototype setup. **Qiling has no `thiscall` convention support at all** in its function-hook/API layer (`STDCALL`/`CDECL` only); you read `ecx` and `[esp]` manually just like raw Unicorn.
  - **SEH:** **Neither `angr` nor `Qiling` implements 32-bit Windows SEH or MSVC `/GX` C++ exception dispatching.** Mapping `fs:[0]` only stops prologues (`push -1; push __ehhandler; mov eax, fs:[0]; mov fs:[0], esp`) from faulting. Neither walks `fs:[0]`, interprets `_EH3_EXCEPTION_REGISTRATION` scope tables, calls local destructors, or executes `RtlUnwind` when a fault or stubbed `throw` occurs (see Qiling Issue #76).
- **`angr`'s lack of an equivalence analysis ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L83)):**
  - Substantively true (`BinDiff` is structural), though `angr` also has `CongruencyCheck` (built specifically to check state equivalence of Unicorn vs VEX step-by-step). Writing a loop-free relational miter over two `angr` states is trivial; what `angr` lacks is frame/pointer alignment and loop invariants, which no generic symex engine supplies out of the box.
- **UC-KLEE ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L83)):**
  - Calling a 2011 LLVM-bitcode tool *"the closest published equivalence checker"* to binary-vs-recompilation verification in 2026 is wildly out of date (see §3). And the note misses *why* UC-KLEE is relevant: UC-KLEE's main idea is **on-demand lazy initialization of under-constrained pointer graphs** plus pruning false failures caused by violated implicit preconditions — the exact trap waiting in your unreached-function input generator ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L25)) and Z3 tier ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L65)).
- **Triton ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L83), [#L99](file:///inputs/functional-equivalence.md#L99)):**
  - The note treats Triton as an engine alongside `angr`. Triton has **no binary loader and no concrete executor**; it is purely an instruction-semantics/AST/taint library fed instruction-by-instruction from an external tracer.

---

### 3. Missing prior work

The note reinvents mechanisms that have a decade of published tools and papers behind them:

1. **Dynamic micro-execution & cross-compiler differential testing of binary functions:**
   - **Micro Execution (`MicroX`) — Godefroid (ICSE 2014):** Executes isolated x86 Windows binary functions without harnesses by trapping memory accesses, lazily allocating pointed-to buffers under memory policies, and stubbing callees.
   - **Blanket Execution (`BLEX`) — Egele et al. (USENIX Security 2014)** and **In-Memory Fuzzing (`IMF`) — Wang et al. (ASE 2017):** Run two binary functions compiled across different compilers/optimizations from controlled initial states, lazily satisfy unmapped reads, intercept calls, and compare observable side effects (heap/global final state, return registers, call arguments) rather than ordered write traces.
   - **`0vercl0k/wtf` (What The Fuzz, built on `bochscpu` + KVM/WHP):** Snapshot-based Windows user/kernel runner taking live debugger dumps (registers, x87, `fs`/`gs` TEB, memory pages) with deterministic dirty-page restoration and breakpoint/hypercall stubs.
   - *How much this replaces:* **`wtf` replaces virtually the entire execution, snapshot-loading, and dirty-page reset engine.** `MicroX`/`BLEX` replace the conceptual design of unreached-function execution and store-order-insensitive side-effect comparison, though neither handles MSVC C++ ABI details (`thiscall` struct returns, `/GX` SEH, stack-local pointer canonicalization).
2. **Formal & data-driven equivalence checking of x86 binaries:**
   - **DDEC (*Data-Driven Equivalence Checking*) — Sharma, Schkufza, Churchill, Aiken (OOPSLA 2013)** and ***Semantic Program Alignment for Equivalence Checking* — Churchill, Padon, Sharma, Aiken (PLDI 2019), built on STOKE:** Prove equivalence of two compiled x86 binaries across `-O0` vs `-O3` **including loops and vectorization**, using concrete runs to infer simulation/alignment invariants for Z3, with x86 semantics cross-checked against real CPU hardware.
   - **Galois SAW + Crucible-x86 / Macaw (and GREASE / `uc-crux-x86`)** and **BINSEC (`BINSEC/Rel`):** Equivalence and relational symbolic execution directly on x86/x86-64 machine code and LLVM bitcode (bridging binary-vs-recompiled-IR directly, unlike UC-KLEE).
   - **Myreen's *Decompilation into Logic* (FMCAD 2008/2012) & Sewell, Myreen, Klein (*Translation Validation for a Verified OS Kernel*, PLDI 2013); FoxDec (PLDI 2022):** Proof-producing binary lifting and binary-to-C refinement proofs at whole-kernel scale.
   - **Dasgupta et al. (*Scalable Validation of Binary Lifters*, PLDI 2020) & Alive2 (PLDI 2021):** Hardware-validated x86-64 semantics (K-x86) and compositional translation validation of lifted LLVM IR — directly applicable to `rev.ng`'s IR before recompilation.
   - *How much this replaces:* Replaces the entire [Formal proofs](file:///inputs/functional-equivalence.md#L59-L69) section and shows UC-KLEE is nowhere near the state of the art.
3. **Differential testing of decompilers and lifters:**
   - **BinRec — Altinay et al. (EuroSys 2020)**, **Liu & Wang (*How Far We Have Come: Testing Decompilation Correctness of C Decompilers*, ISSTA 2020; *DecFuzz*)**, and **DecompileBench (2025)** already recompile lifted/decompiled functions, re-host or link them, and differentially test them against original binaries.

**Net replacement:** Existing work replaces ~65% of the note (the snapshot VM, lazy memory, store-insensitive state diffing, and binary SMT equivalence). What *is* genuinely specific to your problem — and unaddressed by off-the-shelf tools — is the **32-bit MSVC 5.0 C++ ABI / MFC / DirectX world model** (live callee effect replay, `thiscall` quirks, stack-argument object comparison). Spend engineering effort there, not on engine bake-offs.

---

### 4. The four criteria and native execution's isolation cost

**Problems with the four criteria ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L91-L94)):**
- **Throughput is overweighted because the note never did the arithmetic.** 135,606 functions × 20 runs × 2 sides = 5.4M executions. Because *all callees are stubbed*, an average function body is ~500–3,000 instructions (~10¹⁰ instructions total). Across 24 cores, even an interpreter (`bochscpu` at ~15M inst/s/core) finishes 10¹⁰ instructions in **~30 seconds**. Throughput is bottlenecked by **snapshot dirty-page reset and FFI hook crossings** (e.g., Python callbacks), not instruction emulation speed.
- **Reach wrongly bundles the symbolic solver into the concrete runner.** Line 57 explicitly states solver outputs are just candidate inputs fed back into the concrete runner. Penalizing Unicorn/Native because *"the solver comes from elsewhere"* ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L99-L101)) confuses a data format with an engine API, and artificially props up `angr`.
- **Fidelity conflates CPU semantics with OS/CRT runtime.** `fs` base translation is CPU; **Structured Exception Handling is `ntdll.dll` + `msvcrt.dll`** (`RtlUnwind`, `__CxxFrameHandler`). Putting SEH under CPU fidelity guarantees every bare emulator fails unless you either map `ntdll`/`msvcrt` into the snapshot or write an `_EH3` unwinder yourself.
- **Three critical criteria are missing:**
  1. **Fast dirty-memory tracking** (needed not just to restore snapshots, but to know *which* memory outside the frame changed at *every call boundary* ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L36)) without scanning 4 GB).
  2. **Deterministic instruction-count bounding** (required so `inconclusive` on line 43 is reproducible).
  3. **Edge-coverage & comparison-operand (`CMP`) instrumentation** (required by the branch-coverage floor on line 43 and boundary generation on line 44).

**Native execution's isolation cost ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L101)) is severely UNDERSTATED:**
- **Address-space collisions:** A 1999 non-relocatable PE (`0x00400000`), live heap snapshots at fixed Win32 addresses, an explicitly unmapped page 0 ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L25)), and lazy/hash-mapped mutated pointers collide directly with Wine's own `ntdll`/heap/thread stacks and the runner itself inside one process. A mutated pointer hitting Wine's internal heap reads garbage without faulting — silently breaking both soundness and determinism.
- **Indirect calls & per-call memory diffs:** Direct calls can be detoured, but C++/MFC vtable calls (`call [eax+0x14]`) jump into retail code unless all non-target code pages are marked `PAGE_NOACCESS`, turning *every stubbed virtual call* into a 10–50 µs OS signal/VEH round-trip under Wine/Windows — slower than C emulation. And how do you diff "all memory outside the frame" at every call site natively without `mprotect`/`uffd-wp` page-faulting every write? (`GetWriteWatch` on Windows doesn't cover PE sections, works only at 4 KB granularity, and doesn't isolate intermediate call boundaries cleanly.)
- **Timers break determinism:** Catching runaway loops with wall-clock timers ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L101)) makes `inconclusive` verdicts depend on host CPU load, and asynchronously ripping control out of a stuck native thread under Wine without corrupting Wine/TEB state is notoriously fragile.
- **Multi-ABI fragmentation:** "Native" for 32-bit Win32 PE (Wine/WOW64 `fs` + 32-bit VEH) and 64-bit Linux/Windows binaries is really two or three separate platform-specific signal/trampoline runtimes.

---

### 5. Would the proposed test decide the choice? What to change

**No.** As designed ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L103)), it measures the wrong things and suffers fatal selection bias:

1. **"Cheap enough to prototype" rigs the outcome:** Prototyping `angr` or `Qiling` in Python is cheap; prototyping raw `Unicorn`/`bochscpu` in C/Rust with GDT/`fs` + CoW page tracking, or `Native` with `uffd-wp`/VEH isolation + Wine non-collision, is most of the real runner. You will either drop the best engines as "not cheap to prototype," test Unicorn through slow Python bindings, or benchmark a toy Native prototype without page-dirty tracking and Wine collision protection — massively underestimating Native's real complexity and runtime cost.
2. **"Correct verdicts" on clean known-verdict pairs tests your harness logic, not the engine:** Off-by-one constants, swapped args, clobbered `ebx`, and reordered stores produce identical verdicts on *every* engine once your prototype/comparison code is written. Engine differences only appear on wild pointers, mid-run faults, infinite loops, `/GX` unwinds, and tricky x87 ops.
3. **A "small set of functions" misses scale and x87 corner cases:** Ten clean functions won't trigger Unicorn TCG cache-flush pathologies, Wine address collisions on 50 MB game heaps, or specific x87 opcodes (if your sample only has `fadd`/`fmul` on normal doubles under `0x027F`, even 64-bit VEX will accidentally agree with native execution).

**What to change:**
- **Scrap the multi-engine bake-off.** Pick `bochscpu` (or C/Rust `Unicorn 2`) + Native detour oracle up front.
- **Replace it with a two-part falsification spike on that single architecture:**
  1. **Targeted x87 micro-benchmark against native hardware** covering every nasty opcode class explicitly: `0x027F` vs `0x037F` vs `0x007F`, 15-bit exponent overflow/subnormal double-rounding, MSVC `_ftol` (`fldcw` chop), `fcompp; fnstsw ax`, `fld tbyte`, and `fsin`/`fcos`/`fpatan`/`f2xm1`/`fyl2x`/`fprem`. Use the failures to define the exact static opcode filter that routes functions to the native oracle.
  2. **Hostile snapshot stress test:** Take **one real 32-bit Gruntz mid-game process dump** and run 10,000 reset-and-execute iterations featuring 20 virtual stub calls/run, deliberate wild reads across the 4 GB space, an infinite loop, and a stubbed `/GX` exception throw. Measure µs/reset, per-call dirty-memory diff time, and 100% run-to-run determinism.

---

### 6. Shared harness for two producers (`rev.ng` lift vs hand decompilations)

**Unsound at the ABI/harness layer; it adds a heavy abstraction tax the note ignores.**

Sharing a low-level CPU/memory snapshot library is fine. Pretending one per-function harness cleanly serves both `gruntz-decomp` and 135,606 `rev.ng`-lifted Warcraft III functions ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L87-L89), [#L103](file:///inputs/functional-equivalence.md#L103)) glosses over fundamental incompatibilities:

- **You have no headers or prototype database for 135,606 `rev.ng` functions.** Build Step 2 ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L108)) and the comparison rules ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L36-L40)) rely on *ground-truth C++ headers* to know incoming stack-arg byte counts, `bool` (`al`) vs `int` (`eax`) vs `st(0)` returns, hidden struct-return pointers, `thiscall` vs `stdcall`, and struct padding masks. Automated `rev.ng` `detect-abi` on stripped binaries is heuristic, frequently wrong on C++/custom conventions, or falls back to raw register-state structs (`RawFunctionDefinition` / QEMU CSVs).
- **`rev.ng` recompiled code does not look like native target-ABI code:**
  - `rev.ng` lifts via QEMU TCG to LLVM IR/C, emitting calls to **internal `rev.ng`/QEMU runtime helpers** (softfloat helpers, flag helpers, `function_dispatcher` for indirect calls) that must *run*, not be stubbed as external game callees.
  - Floating point in a `rev.ng` lift executes inside **compiled QEMU softfloat helpers on LLVM scalars**, not in the host CPU's hardware `ST(0)` registers! Comparing hardware `ST(0)` and `FCW` ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L36)) doesn't even apply without custom CSV-to-FPU marshaling wrappers.
  - `rev.ng` lifts often maintain an explicit emulated stack pointer CSV alongside the host stack, whereas hand-recompiled C++ uses a single native stack with completely re-laid-out local frames.
  - Hand-recompiled C++ links via symbolic relocations (`?g_pGameMgr...`); `rev.ng` bakes raw integer addresses or image-base offsets from the original binary.

Designing around Warcraft III's 135,606 x86-64 lifted functions before verifying Gruntz's 346 32-bit functions forces premature generalization across two incompatible ABIs. Share the low-level snapshot/emulator library; keep the producer frontends and ABI comparators separate.

---

### 7. Unclear, overclaimed, or self-contradictory points in the note

1. **Fatal hole in stack-frame exclusion + local pointer canonicalization ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L32), [#L36-L37](file:///inputs/functional-equivalence.md#L36-L37)):**
   - Line 32 says if a function calls `GetClientRect(hWnd, &rc)`, you canonicalize `&rc` to "the local it points at." Line 37 excludes the function's own stack frame from memory comparison at calls.
   - **What if the local is an *in-parameter* or *in/out-parameter* struct** (e.g., `DDSURFACEDESC ddsd; ddsd.dwSize = sizeof(ddsd); lpDDS->Lock(..., &ddsd, ...)` — ubiquitous in DirectDraw/MFC)? Because `ddsd` lives inside the caller's excluded stack frame, **your comparison never checks the bytes of `ddsd` passed *into* `Lock()`!** Worse, between `-O0` and `-O2` builds (due to SROA, slot reuse, and reordering), there is no well-defined 1-to-1 mapping of "which local `[esp+0x18]` points at" in stripped machine code. You cannot canonicalize stack pointers symbolically; at every call you must recursively dereference pointer arguments using their type definitions, compare pointed-to values (modulo padding), and write stub outputs back through each side's distinct stack address.
2. **Circular reliance on reconstructed prototypes ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L36-L40)):**
   - For the 346 open functions in `gruntz-decomp`, *wrong reconstructed signatures and struct layouts* are major bug classes. Using the reconstruction's own prototype header to decide whether to compare only `al` vs `eax`, which struct bytes are "padding" to mask out, and how many incoming stack bytes to ignore is circular: if the reconstruction wrongly types an `int`-returning function as `bool`, masking comparison to `al` hides the bug.
3. **x87 exactness across optimization levels is false even with the same x87 compiler ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L45) vs [#L49](file:///inputs/functional-equivalence.md#L49)):**
   - Line 45 claims *"A recompilation with a compiler that also emits x87 code at the same precision can be compared exactly."* Line 49 validates the harness on *"the same reconstructed source compiled with different optimization, inlining and declaration order."*
   - Under MSVC's `0x027F` (53-bit significand), **32-bit `float` (`single`, 24-bit) variables are NOT exact across optimization levels**: `-O0` spills intermediates to 32-bit stack slots (`fstp dword ptr`, rounding to 24 bits), while `-O2` keeps them in 53-bit `ST(i)` registers or reorders non-associative FP ops. Your own line-49 validation suite will fail exact comparison on `float` code.
4. **Byte identity is NOT a complete proof with only symbol-level relocation checks ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L13)):**
   - Identical instruction bytes + relocation targets prove equivalence only if **the target `.rdata`/`.data` bytes** (float constants, jump tables, string literals, initializers) and referenced struct layouts *also* match retail — or you link strictly against retail's data sections. Relocation symbol matching alone misses wrong `.rdata` constant values.
5. **Misleading `fs:[0]` exception rule ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L38)):**
   - Treating `fs:[0]` writes *"as part of the frame"* is only safe *at intermediate calls*. **At function return**, `fs:[0]` *must* equal its entry value; failing to pop the SEH registration record before `ret` is catastrophic stack corruption in Win32. And how "stubs raise" unwinds two differently compiled binaries without `ntdll`/`msvcrt`'s unwinder is completely unspecified.
6. **Overclaiming Z3 ("usually in seconds", [functional-equivalence.md](file:///inputs/functional-equivalence.md#L65)):**
   - Loop-free binary functions with 32/64-bit `imul`/`idiv`, `QF_FP` floating point, or symbolic table lookups over flat byte-addressed `QF_ABV` memory routinely stall Z3. Worse, unconstrained symbolic inputs generate **false counterexamples** whenever the original code relies on unstated caller preconditions (non-aliasing pointers, aligned buffers, bounded counts).
7. **Pure getters keyed only on arguments ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L31)):**
   - Keying a getter stub solely on `(this, args)` returns stale values if the function mutates `*this` in memory between two getter calls, and assumes an oracle telling you which functions are pure.
8. **Lifting project contradiction ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L3) vs [#L85](file:///inputs/functional-equivalence.md#L85)) & unexplained terms:**
   - Line 3 and line 85 introduce the lifting project as lifting Warcraft III to C with `rev.ng` and checking 1,525 function rewrites, then line 85 admits *"it compares a binary with a jump-rewritten copy of itself, so both sides share a compiler and layout"* — meaning it didn't actually validate `rev.ng` C recompilations across compilers at all.
   - *"Harvest, the game concord matches against"* ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L87)) appears out of thin air after the entire note framed concord around *Gruntz* ([functional-equivalence.md](file:///inputs/functional-equivalence.md#L3), [#L7](file:///inputs/functional-equivalence.md#L7)).
   - The [Bend 2](file:///inputs/functional-equivalence.md#L71-L75) section is an irrelevant detour that belongs in a review reply, not an architecture doc.

