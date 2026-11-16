# Review of docs/il-search.md by Gemini 4 Carbon (high)

Reviewer: gemini-4-carbon-high through agy, sealed profile, the proposal as its only input
Date: 2026-11-16
Reviewed: docs/il-search.md as committed with this review
Prompt: docs/reviews/il-search-carbon-prompt.md
Run: 95d0a14e-a3e0-4ee5-b07a-948197d3d802, 351 seconds, $0.30

### 1. Headline: soundness, expected yield, and fundamental flaw

**No, this approach is not sound, and I expect it to make almost none of the remaining C++ functions byte-exact** (at best a low single-digit handful, and likely zero on the flagship constructor family).

Even before looking at the mechanism, your own ceiling is tiny: of the ~350 open functions (232 with register diffs), forcing retail's local allocator grants makes **only 21** exact ([il-search.md](file:///inputs/il-search.md#L7-L15)), and `permute` already closed 1. Over 90% of open functions—and 89 of the 110 functions at ≥97%—cannot be fixed solely through the local scratch allocator this targets.

**The fundamental flaw:** **You are searching synthetic, single-function `ex` tree mutations unconstrained by front-end realizability (`Im(c1xx)`) while freezing `sy` and `gl`.**
That space gets both boundaries wrong simultaneously:
1. **It contains uninhabited and semantically broken IL (`IL ∉ Im(c1xx)`):** `c2.exe` is many-to-one, and hand-permuted `ex` postfix trees do not have to correspond to *any* valid C++ program. Your very first proposed test—swapping two call-argument subtrees in `ex` on `CreateHealthSprite` ([il-search.md](file:///inputs/il-search.md#L27-L30))—illustrates this: in MSVC CIL, call argument subtrees are positional (`OP_ARG` order defines parameter/push order), so swapping argument subtrees swaps *which value is passed to which parameter* (`f(a, b)` → `f(b, a)`), changing `push` order and program semantics rather than just register grant order.
2. **It excludes the multi-table IL changes C++ actually produces:** Real C++ changes that alter `c2`'s register allocation—`inline` helpers/accessors expanded by `c2.exe`'s inliner (`gl` + `ex`), compiler-generated or explicit temporaries and local declaration order (`sy`), and type/symbol IDs (`sy`)—span outside a single function's `ex` expression trees.

---

### 2. Does "What is established" support the premise, and what alternatives are underweighted?

**No—the evidence in [il-search.md](file:///inputs/il-search.md#L13-L18) argues *against* the premise that the fix lies in permuting each function's own `ex` statements:**
- **43 of 110** high-similarity functions have **zero** differing grants in the local allocator ([il-search.md](file:///inputs/il-search.md#L15)); their diffs come from `c2`'s global register allocator (`ebx`/`esi`/`edi`), instruction selection, or upstream optimization passes.
- **`CBehindCandy`'s constructor (and 6 siblings):** Matching retail instruction-for-instruction requires suppressing the round-robin cursor advance on one member load, yet [il-search.md](file:///inputs/il-search.md#L16-L18) explicitly establishes that `c2`'s pre-pass sets preferences *only* on moves to/from physical registers, and 3,000 `permute` candidates + 30 macro spellings moved nothing. That directly contradicts the idea that rearranging that constructor's own statement ASTs will grant a plain member load a preference without changing the emitted instructions.
- **`CreateHealthSprite` (and 4 siblings):** Five sibling methods sharing the exact same reversed argument-load order ([il-search.md](file:///inputs/il-search.md#L17)) is the classic signature of a **shared declaration, macro, or inline helper outside each function body**, not five independent local statement quirks.

**Underweighted explanations for both phenomena:**
1. **`c2.exe` inlining + post-inline copy propagation/coalescing (`gl` + `ex`):**
   - In MSVC 5.0, `c1xx.dll` does not inline C++ `inline` functions; it emits them as separate bodies in `ex`/`gl`, and **`c2.exe` inlines them** before register allocation.
   - *Swapped argument loads (`CreateHealthSprite` + 4 siblings):* If all five call an `inline` wrapper `Helper(argB, argA)` whose parameter list is ordered `(B, A)` and whose body calls `RealCallee(A, B)` (or a member wrapper shifting `this` vs args), `c2` evaluates the wrapper's arguments in `(A, B)` order at the call site, splices the callee body, copy-propagates the parameters away, and emits `push`es in retail's `(B, A)` order with `eax`/`ecx` swapped.
   - *Unadvanced cursor (`CBehindCandy` + 6 siblings):* If the frame-image pointer comes from an `inline` method returning in `eax` (or passing `ecx`), `c2` inlines the body (creating a physical-register return move that gets an `eax` preference in the pre-pass) and coalesces/eliminates the move—leaving a single load instruction that takes the **preference path** and therefore **does not advance the round-robin cursor**.
2. **Modulo-3 cursor wraparound (`-1 ≡ +2 mod 3`) and liveness overlap:**
   - On a 3-register ring (`eax`, `ecx`, `edx`), "undoing 1 cursor advance" has the exact same downstream state as **2 extra cursor advances** (e.g., temporaries allocated before dead-code/copy elimination) or changing whether `ecx` (`this`) / `eax` (prior call return) is still live across an adjacent grant, forcing round-robin to skip a busy slot.
3. **`c2` optimizer / tuple-ordering dependencies on `sy`:**
   - `c2` lowers `ex` + `sy` into internal tuples/DAGs and runs CSE, value numbering, alias disambiguation, Sethi-Ullman cost ordering, and Pentium (`/G5`/`/GB`) scheduling before/during codegen. Tie-breaking in `c2`'s tables routinely depends on **symbol IDs and declaration order in `sy`**—which are shifted by `#include` order, Precompiled Headers (`.pch`, standard in 1999 Visual Studio 97 projects), class member order, or preceding functions in the TU.
4. **Untested compiler service packs and flags:**
   - [il-search.md](file:///inputs/il-search.md#L34) admits **SP1 and SP2 are completely untested**, and says nothing about testing PCH (`/Yu` vs non-PCH), `/G5` vs `/GB` vs `/G6`, or `/Ob1` vs `/Ob2`.

---

### 3. Is the inverse lookup (target IL back to C++) realistic? What makes it fail?

**No. Step 3 ([il-search.md](file:///inputs/il-search.md#L28)) is hand-waving—it restates the unsolved source-search problem in one sentence.** It will fail for five concrete reasons:

1. **Uninhabited target IL (`target IL ∉ Im(c1xx)`):** `c1xx.dll` has deterministic AST-to-CIL lowering rules. Most mutated `ex` trees cannot be emitted by `c1xx` from *any* C++ input. There is no C++ spelling of `f(a, b)` that makes `c1xx` emit `a` and `b` in swapped positional `ex` argument slots without calling `f(b, a)`.
2. **Wrong representative of `c2`'s equivalence class:** Because `c2`'s optimizer collapses many distinct IL inputs to identical machine code, the mutator can stumble onto an unnatural `ex` hack that reproduces retail's bytes even though retail's actual C++ produced a *completely different* IL (e.g., an inlined call in `gl` + `sy` temp) that `c2` optimized to the same object code. Searching for C++ that emits the mutator's synthetic `ex` then searches an empty set.
3. **`ex` does not exist in isolation from `sy` and `gl`:** Almost any non-trivial C++ rewrite (adding a local temp, binding a reference, using `?:` or `&&`, calling an inline wrapper) mutates `sy` (symbol records, temp scopes, types) or `gl`. Diffing `ex` alone gives an incomplete target that no standalone C++ snippet matches.
4. **Context-dependent `c1xx` lowering:** How `c1xx` lowers expressions depends on exact types, inheritance offsets, lvalue/rvalue categories, overload resolution, and existing `sy` state. Toy standalone corpora frequently lower to different opcodes than expressions inside the real `CGrunt` class hierarchy.
5. **Circularity:** "Compile small C++ variants and compare their IL" is still blind C++ mutation search—the exact failure mode described in [il-search.md](file:///inputs/il-search.md#L21-L22)—just moved from `c2`'s output to `c1xx`'s output without knowing `c1xx`'s emission code.

---

### 4. Is the "empty search result = cause lies outside the function's statements" inference valid?

**No, it is logically invalid, and the proposal contradicts itself six lines later** ([il-search.md](file:///inputs/il-search.md#L34) vs [il-search.md](file:///inputs/il-search.md#L40)).

An empty search only proves that **none of your four hand-picked `ex` tree edits on the current AST's opcodes matched retail**. It fails to rule out intra-function C++ fixes because:
- It never varies function-local **`sy` records** (local variable declaration order, explicit temps, compiler-generated temps from `?:`/`,`/`&&`, `const T&` bindings, type signedness).
- It only permutes existing nodes from ~30 decoded opcodes ([il-search.md](file:///inputs/il-search.md#L26)), missing alternative `c1xx` opcode encodings for equivalent statements (`a += b` vs `a = a + b`, `?:` vs `if`, pointer vs array indexing, loop forms, casts).
- Statement-level calls to `inline` helpers in C++ are changes to the function's statements whose IL footprint lands in `gl` and `sy`, outside a single-function `ex` mutator.
- Combinatorial depth limits prune multi-step edits.

**What you would actually need to make the inference valid:**
1. Reverse-engineer `c1xx.dll`'s CIL emitter well enough to characterize the **complete set of `(ex, sy_local, gl_inline)` outputs** `c1xx` can emit for any type-correct C++ body of that function in the current TU context.
2. Co-mutate `sy` local/temp entries (and symbol order) alongside `ex`, including alternative opcode encodings—not just AST node swaps on the existing compile's `ex`.
3. Search that bounded image **exhaustively** without depth truncation on the differing basic block. Anything short of `Im(c1xx)` completeness cannot justify stopping source search "on evidence."

---

### 5. What to do first instead (concrete experiments within the same ~1-session budget)

1. **Hand-test the 1-off `ex` mutation on `CreateHealthSprite` *before* writing a parser/mutator (20 minutes):**
   - You already have ~30 opcodes decoded for hand edits, `gl` offset shifting working, and `concord il replay` ([il-search.md](file:///inputs/il-search.md#L9-L26)). Manually swap the two argument byte ranges in `CreateHealthSprite`'s captured `ex` file right now and replay. You will find out in 20 minutes whether swapping argument subtrees in `ex` breaks `push` order / semantics or matches retail—before spending a session building infrastructure for it.
2. **Read `c2.exe` and `c1xx.dll` in IDA/Ghidra instead of treating them as black boxes (2–3 hours):**
   - You already hook `c2.exe` in a debugger ([il-search.md](file:///inputs/il-search.md#L16)). Read `c2.exe`'s preference pre-pass, tuple builder, and inliner directly: list **every** cross-reference in `c2.exe` that sets the preference field on a temporary or skips advancing the round-robin cursor, and trace how `c2` orders call-argument tuples.
   - Read `c1xx.dll`'s call-emission routine (`OP_CALL` / `OP_ARG`): check directly whether `c1xx` *ever* reverses argument emission order and under what AST flags. Disassembling 200 lines of deterministic 1997 x86 compiler code beats guessing from black-box corpora.
3. **Test `c2`-inlined wrappers and explicit temp/declaration-order rewrites on the two sibling groups (1 hour):**
   - For `CreateHealthSprite` + 4 siblings (which only got 20 canned `permute` rewrites and 5 Haiku tries, [il-search.md](file:///inputs/il-search.md#L18)): test (a) calling through an `inline` helper `Wrapper(argB, argA)` with reversed parameters so `c2`'s inliner expands it, and (b) extracting arg 1 vs arg 2 into explicit local variables in both `sy` declaration orders.
   - For `CBehindCandy` + 6 siblings: test `inline` getters/helpers returning in `eax` while tracing `c2`'s post-inline pre-pass in `regtrace` to see when return-register preference survives copy propagation onto a member load.
4. **Run the missing compiler/PCH/TU matrix (1–2 hours):**
   - Test **MSVC 5.0 SP1 and SP2** (`c1xx.dll` + `c2.exe`), `/G5` vs `/GB` vs `/G6`, `/Ob1` vs `/Ob2`, and PCH (`/Yu`) vs non-PCH on `CBehindCandy` and `CreateHealthSprite`.
   - Run a **TU-stripping / `sy`-padding experiment**: inject dummy symbols/functions before `CBehindCandy` and `CreateHealthSprite` (or compile them in an isolated TU) and run `regtrace` across all 110 functions to measure directly whether TU context or `sy` symbol IDs move *any* local or global grants.
5. **Permute local variable declaration order (`sy`) for the 43 global-allocator functions (1 hour):**
   - 43 of the 110 functions differ outside the local allocator ([il-search.md](file:///inputs/il-search.md#L15))—typically `ebx`/`esi`/`edi` global register assignment in `c2`, which breaks ties on **`sy` symbol/declaration order**. Permuting local declaration order and scope blocks in C++ directly targets a larger slice (43 functions) than the 20 remaining local-grant-exact functions.

---

### 6. Missed risks and costs

1. **Throughput math doesn't add up ("minutes per function" vs "~1 second per replay"):** At ~1 second per `c2` replay ([il-search.md](file:///inputs/il-search.md#L27)), 60 variants take a minute and 3,000 variants (what `permute` ran on one constructor, [il-search.md](file:///inputs/il-search.md#L18)) take **50 minutes per function**—before counting compound multi-edit combinations across sibling statements.
2. **Parser complexity is massively underestimated ("about one session"):** Toy corpora cover 30 flat opcodes; production 1999 C++ bodies contain intra-function branch offsets/labels (shifting `gl` offsets in [il-search.md](file:///inputs/il-search.md#L26) does **not** fix intra-function jump targets or SEH/C++ EH unwind tables when body length changes!), x87 stack opcodes, bitfields, virtual/multiple-inheritance adjustments, and variable-width immediates. One mis-sized operand mid-function desynchronizes the entire postfix parse from that byte onward.
3. **Mutating temps in `ex` without `sy` corrupts frames or crashes `c2`:** "Introduce or remove a temporary" ([il-search.md](file:///inputs/il-search.md#L27)) requires allocating a typed, sized symbol record in `sy`. Referencing bogus or reused `sy` indices in `ex` causes `c2.exe` (which assumes trusted `c1xx` input and barely validates bounds) to crash, hang, alias stack slots silently, or read out-of-bounds heap memory non-deterministically.
4. **COFF relocation blind spots during byte comparison:** In `.obj` comparisons, external symbols and member calls compile to zeroed displacements backed by COFF relocation records. Swapping two subtrees that reference different globals/offsets of the same addressing form produces identical raw code bytes if relocations aren't compared strictly, or hides valid shapes if stack-frame displacements shift.
5. **Unbudgeted cost of Step 3:** "Minutes per function" ([il-search.md](file:///inputs/il-search.md#L38)) counts only Step 2 (`ex` replay) and treats Step 3 (finding C++ that compiles to the mutated IL) as free, when Step 3 is manual, open-ended, and frequently unsolvable.
6. **Bad engineering ROI ceiling + false-negative lock-in:** Even 100% success on the target slice yields **at most 20** byte-exact functions out of ~350 open ([il-search.md](file:///inputs/il-search.md#L15-L18)), while [il-search.md](file:///inputs/il-search.md#L34) commits to abandoning source search on every function where the incomplete mutator comes up empty.

---

### 7. What is unclear, overclaimed, or contradicted by its own data

- **Direct self-contradiction on empty search results:** [il-search.md](file:///inputs/il-search.md#L34) claims an empty result means *"no front-end output for this function's statements reproduces retail... That stops further source searching on those functions on evidence."* Six lines later, [il-search.md](file:///inputs/il-search.md#L40) admits *"an empty result is a bound on that space, not proof that no IL works."* Both cannot be true.
- **Internal contradiction on `CBehindCandy`'s constructor:** [il-search.md](file:///inputs/il-search.md#L16) says undoing only one grant's cursor advance matches retail instruction-for-instruction, and [il-search.md](file:///inputs/il-search.md#L18) proves no plain member load can get a preference in `c2`'s pre-pass—yet the proposal never explains how *any* of its four `ex` tree mutations ([il-search.md](file:///inputs/il-search.md#L27)) could suppress that cursor advance without changing the emitted instructions.
- **Census arithmetic doesn't add up:**
  - [il-search.md](file:///inputs/il-search.md#L7): `4,426 - 4,080 = 346` open functions, yet the next sentence says *"232 of 359"* open functions. Where did the extra 13 functions come from?
  - [il-search.md](file:///inputs/il-search.md#L15): Out of **110** functions, `21 (exact) + 32 (improved) + 43 (no differing grant) = 96`. **14 functions are unaccounted for** (presumably degraded or unchanged despite differing local grants). Also, describing all 110 as *"first difference is register allocation"* while 43 have *"no differing grant"* conflates global register allocation (`ebx`/`esi`/`edi`) with the local scratch allocator throughout the pitch.
- **Contradiction on per-function isolation in `c2`:** [il-search.md](file:///inputs/il-search.md#L11) states unconditionally that the round-robin cursor *"starts at eax for each function,"* then [il-search.md](file:///inputs/il-search.md#L34) blames failures on *"c2 compiles functions in one process with shared state"* without specifying what state actually leaks across functions or testing it with `concord il replay`.
- **Overclaim that "the IL is where the target can be stated exactly" ([il-search.md](file:///inputs/il-search.md#L22)):** CIL cannot state physical scratch-register choices—`regtrace` already states the target decision, while IL still requires blind search. Worse, [il-search.md](file:///inputs/il-search.md#L9) defines "the IL" as four files (`ex`, `sy`, `gl`, `in`), then silently shrinks "the IL" in [il-search.md](file:///inputs/il-search.md#L26-L27) to a single function's `ex` stream.
- **Overclaim on wrong opcode decodes ([il-search.md](file:///inputs/il-search.md#L39)):** Claiming a misdecoded opcode merely produces an unexpected object caught by comparison ignores **parse-time failure**: wrong opcode arity corrupts tree reconstruction of the *baseline* function, preventing valid subtree swaps and causing silent false negatives.
- **Strawman dismissal of alternatives ([il-search.md](file:///inputs/il-search.md#L46-L47)):** A single $0.14 Haiku run trying 5 variants proves nothing about targeted source search, and "reading `c2`'s allocator fully" was only done for the local scratch grant rule—ignoring `c2`'s global allocator (43/110 functions), `c2`'s inliner/tuple optimizer, and `c1xx.dll`'s emitter (which directly answers which C++ yields a given IL).
