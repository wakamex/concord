# Functional equivalence as a decompilation target

A decompilation is usually judged by rebuilding the original binary byte for byte. This note proposes that a future decompilation project make functional equivalence its target instead: each reconstructed function must behave like the original, and no effort goes into making its bytes identical. It describes how to build a behavioural check that a pass can be trusted from. It draws on concord's work on [gruntz-decomp](https://github.com/sushi-shi/gruntz-decomp), on a verification harness built for a [rev.ng](https://rev.ng/)-based lifting project that checked rewrites of 1,525 Warcraft III functions, and on a review of an earlier version of this note (docs/reviews/functional-equivalence-carbon-2026-11-17.md).

## Why not byte matching

gruntz-decomp rebuilds the 1999 game Gruntz with Microsoft Visual C++ 5.0 (MSVC) and has 4,080 of 4,426 functions byte-exact (92.18%). Its last few percent show what byte matching costs. Of the 110 open functions at 97% or more whose first difference is a register choice, giving them retail's registers alone (`concord regtrace`, which forces the code generator's own register grants) makes 21 byte-exact: the code generator's input from their current source already yields retail's bytes when only the order in which it handed out eax, ecx and edx changes. Searching with concord's source rewrites, hand reconstruction, model agents and a debugger reading of the code generator's allocator closed one of those 21. For the other 325 open functions, whether they are behaviourally right is unknown, and a byte-matching percentage cannot say which are right and which are wrong.

Byte matching is also only possible with the original toolchain. A project that recompiles with a modern compiler never produces identical bytes, so byte identity cannot be its measure of correctness at all.

## Byte identity as a shortcut proof

When a function rebuilt with the original compiler happens to come out byte-identical, with relocations checked, it is proven equivalent for every input and path, and needs no behavioural run. That is a certificate the check can accept, not a goal to pursue: the project spends nothing on reaching it, and it never applies to a recompilation with another compiler.

The other things byte matching is credited with do not need it either. Linking reconstructed code into the original executable needs compatible struct layouts, calling conventions and name mangling, which the detour rig in the build order below needs and checks anyway. Exception tables and destructor order on exception paths are checked by the behavioural gate's exception-path runs.

## The check

The behavioural gate runs the original machine code and the recompiled machine code of one function from the same starting state and compares what each does that its callers can observe.

### Starting state

- Start from real states, not random ones. Run the original program (the retail executable exists and runs) under instrumentation, with function-entry hooks or full-system tracing, through recorded play, and capture at each function's entry its registers, x87 and segment state, the memory it reads (copy-on-write page snapshots), and what each of its callees returned and wrote. Every object, vtable, list and DirectDraw descriptor in such a snapshot is valid without hand-written seeding.
- Reach code those runs did not with mutations on top of real snapshots: scalar fields, counts within bounds, error results from callees (DirectX's DD_OK is zero, so an injected failure takes the error path).
- Where no snapshot reaches a function, allocate its inputs type-guided from the reconstructed headers: pointers into a controlled arena, objects stamped with their real vtable pointer, lists ended by NULL with a probability that grows with depth, enums within their values, page zero unmapped. Address-derived bytes (every unmapped byte a hash of its address) remain the last resort and do not terminate on linked structures.
- Respect the entry ABI: the direction flag clear, the x87 control word as MSVC sets it (0x027F), all x87 stack slots empty, a thread environment block mapped at fs so that structured exception registration works.

### The world outside the function

- Map the original image's read-only data, vtables and initial data, and resolve the recompiled function's references to globals, string literals, constants, vtables and jump tables to the original's addresses (link it against the original image, or detour it into a copy of the retail executable). Without this, every reference differs by raw address.
- Stub callees, with each stub's behaviour taken from the callee's recorded effects in the snapshot or from its contract: the prototype (argument count, stack cleanup, whether a struct return goes through a hidden pointer, whether the result is in eax, edx:eax or the x87 stack), pure getters keyed on their arguments, not their position in the call sequence, out-parameters written identically on both sides, allocators returning fresh arena memory, and volatile registers and flags clobbered after every stubbed call so code that wrongly relies on them surviving fails.
- Canonicalize pointers into the function's own stack: a pointer to a local passed to a callee (`GetClientRect(hWnd, &rc)`) differs between correct builds, so arguments are compared after mapping each stack pointer to the local it points at.

### What is compared

- At each call and at return, the state the rest of the program can see: memory outside the function's own frame (compared as state at those points, not as an ordered trace of stores, since a correct build may reorder, merge or drop independent stores; padding bytes masked), the call's callee and its arguments as the prototype defines them, and at return the result as the prototype defines it (al for a bool, eax, edx:eax, st(0), or the hidden struct pointer echoed in eax), the stack pointer (which checks the bytes popped), the callee-saved registers ebx, esi, edi and ebp, the direction flag, and the x87 stack depth and control word.
- The function's own frame is excluded, and so are the incoming argument slots, which MSVC uses as scratch; this needs the function's argument size, from its prototype.
- MSVC 5.0's C++ exception handling (/GX) registers a frame through fs:[0] in any function with a local that has a destructor, writing the function's own stack pointer outside its frame; the comparison treats that registration as part of the frame, and exception paths get their own runs in which stubs raise.
- Constructors return this in eax; member functions returning a struct by value always do so through a hidden pointer; variadic member functions and COM interfaces do not use thiscall. The prototype database states which applies, function by function.

### Results

- Pass (or certified, for a byte-identical rebuild with the original compiler, or proved, under Formal proofs below), fail, inconclusive (a run hit its step, time or page limit) and under-exercised. A behavioural pass needs a coverage floor on both the original's and the reconstruction's branches, since a branch only the reconstruction has (a wrong guard) is otherwise never counted; below the floor the result is under-exercised, not pass.
- Branch coverage is not equivalence: covering both sides of `if (x > 100)` does not catch `x >= 100` or a signedness error. Inputs at boundaries (each comparison's constant and its neighbours, signs, zero, overflow) are part of the generated set.
- Floating point: MSVC 5.0 computes with the x87 unit. A recompilation with a compiler that also emits x87 code at the same precision can be compared exactly; one that emits SSE cannot, and a tolerance on results misses a rounding difference that flips a comparison or a float-to-int truncation. Functions using floating point are reported as a class of their own until both sides use the same floating-point model.

## Making a pass mean something

- Validate the harness on pairs known to be equivalent that are not byte-identical: the same reconstructed source compiled with different optimization, inlining and declaration order. Byte-exact pairs share stack layout, registers and addresses and hide every false failure a real recompilation causes.
- Seeded wrong variants (a flipped condition, a constant off by one, a missing or extra write, swapped arguments, a clobbered callee-saved register, an x87 stack leak) must fail; the suite reruns whenever the harness changes.
- Every input a tool skips is a counted class: an instruction the emulator or solver cannot execute, an offset format a parser ignores, an implicit register write a disassembler does not report. In the lifting project, one field check silently ignored every hexadecimal offset and reported zero problems.
- Every disagreement is attributed to one side before anything changes. Most of the lifting project's refutations were checker bugs, not defects: [Triton](https://triton-library.github.io/)'s instruction semantics, [Capstone](https://www.capstone-engine.org/)'s flag details and LLVM's register descriptions were each wrong somewhere. A behavioural check's failures point at real defects only once its own failures have been worked through.
- Two implementations agreeing proves nothing when they share a blind spot; compare their results item by item.

## Symbolic tools

A symbolic engine (Triton, or [angr](https://angr.io/)) is safest as an input finder: it solves for a state that reaches an untaken branch, and both functions then run from that state in the emulator, so a wrong instruction semantics in the solver costs coverage, not correctness. Proving small loop-free functions equivalent outright is possible but trusts the engine's semantics, which the lifting project found wrong in several places; such proofs need the semantics cross-checked against the emulator on the same instructions first. The lifting project's per-step proofs (about 400,000 steps without a refutation, at 30 to 40 milliseconds per step) were facts about one program, not equivalence of two, and do not carry over as a scale estimate.

## Formal proofs

Proving a reconstructed function equivalent to the original for every input needs three formal models: the instruction semantics of x86, so the original machine code means something to a prover; the world, meaning what memory, globals and callees may do; and an invariant for each loop that cannot be unrolled. The first has to be cross-checked against real execution before it is trusted, as above. The second is the same problem the emulation harness has, stated in logic instead of sampled, and for a function that calls into MFC, DirectX or the game's own objects it means specifying every callee's behaviour formally, which is harder than recording it.

That makes proofs a tier for one slice of the functions, not the gate for all of them:

- Leaf functions without calls, and functions without loops, can be proved equivalent outright with an SMT solver such as [Z3](https://github.com/Z3Prover/z3), usually in seconds: arithmetic, bit manipulation, parsing, table lookups. A proved function needs no behavioural run, like a byte-identical one.
- Functions with loops can be checked with the loops unrolled a fixed number of times: equivalence up to that depth, stronger than sampled runs and weaker than a proof. The result records the bound.
- Everything else goes through the emulation gate.

The result classes gain one: proved, for a function the solver showed equivalent for all inputs under the stated semantics.

## A reimplementation with stated laws

A language built around machine-checked properties, such as [Bend 2](https://github.com/bendlang/bend), whose compiler demands a proof of each law a program declares in a `LAWS.bend` file whenever the program changes, fits a different project from this one. Its programs are pure and linear, without mutable memory or pointers, so stating a Gruntz function in it means first modelling memory and the world explicitly, and proving such a function equivalent to the original binary needs x86 semantics inside the language's logic, which does not exist. Laws state properties (a grunt's health never goes negative, a level can always be finished), not equivalence to a binary, so proving them does not show that a reconstruction does what the original did.

Where it fits is a clean reimplementation of a game's logic, a remake rather than a decompilation: the laws carry the specification, and the original binary, run through the emulation harness or the replay rig, supplies concrete behaviour to test the laws and the reimplementation against. There the decompilation's checked functions are the oracle, not the product.

## What per-function checks miss

Stubbed callees cannot see a function breaking a contract another relies on: an object left in a state its next method does not expect, a buffer sized differently, an order of side effects that only matters across calls. Whole-program replay covers this: recorded inputs replayed against the original and against the original with reconstructed functions linked or detoured in, comparing frames, saved games and network messages.

## Build order

1. A rig that detours one reconstructed function into a copy of the retail executable, and deterministic replay of recorded play with it in place. It gives ground truth from day one and records the entry snapshots and callee effects the per-function runner needs.
2. A prototype and vtable database built from the reconstructed headers and the binary (conventions, argument sizes, struct returns, result registers), and address canonicalization between the original image and recompiled code.
3. The two-sided per-function runner with the comparison above, validated on equivalent-but-different builds and on the seeded-wrong suite before it reports on anything.
4. Snapshot replay with mutation, then type-guided allocation for functions no snapshot reaches, then boundary and solver-generated inputs for untaken branches.
5. Proofs with an SMT solver for leaf and loop-free functions, and bounded checks for functions with loops, once the instruction semantics are cross-checked against the emulator.

## Where it is hard for a game like Gruntz

The world is most of the work: Gruntz calls MFC (Microsoft Foundation Classes), DirectDraw and DirectSound, keeps much of its state in globals and long-lived objects, and walks MFC lists whose iterators advance through reference parameters. Snapshots from the running game should remove much of that work; functions that only run in rare states remain the hard cases. On an existing byte-matching project such as gruntz-decomp, the check would sort the 346 open functions into behaviourally passing, failing, under-exercised and not yet checkable, which is the measure of correctness a reader of its progress actually wants.
