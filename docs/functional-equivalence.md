# Functional equivalence as a decompilation target

A decompilation is usually judged by rebuilding the original binary byte for byte. This note argues that a future decompilation project should instead check that each reconstructed function behaves like the original one, and describes how to build that check. It draws on two sources: concord's work on [gruntz-decomp](https://github.com/sushi-shi/gruntz-decomp), which pushed byte matching as far as it would go, and a verification harness built for a [rev.ng](https://rev.ng/)-based lifting project, which checked rewrites of 1,525 Warcraft III functions.

## Why byte matching is the wrong target

gruntz-decomp rebuilds the 1999 game Gruntz with Microsoft Visual C++ 5.0 and has 4,080 of 4,426 functions byte-exact (92.18%). The remainder is not mostly wrong code. Of the 110 open functions at 97% or more whose first difference is a register choice, retail's registers alone make 21 byte-exact (`concord regtrace`, which forces the compiler's own register grants): their source already does what the original does, and only the order in which MSVC's code generator handed out eax, ecx and edx differs. Searching with concord's source rewrites, hand reconstruction, model agents and a debugger reading of the code generator's allocator closed one of those 21. A byte-matching project spends most of its late effort on functions that are already correct, and its percentage cannot tell a function that is wrong from one whose registers rotate one place.

A behavioural check accepts those functions now, and its failures point at real defects. Byte identity still certifies a function completely when it happens, so it remains useful as a bonus, not as the gate.

## The check

The gate is differential emulation of each function: run the original machine code and the recompiled machine code from the same starting state, and require the same observable effects.

1. Emulator. [Unicorn](https://www.unicorn-engine.org/), a CPU emulator library, runs both functions in 32-bit or 64-bit mode as the target needs.
2. A deterministic world, the same on both sides by construction:
   - every memory page a function touches outside its own frame is mapped with bytes derived from the page's address;
   - every call leaving the function returns at once, with a result derived from the call's position in the sequence and its arguments (callees are stubbed, not emulated, so each function is checked alone and an interface difference shows as a different call);
   - each run starts from the same random registers and stack, several times per function.
3. Observable effects only, compared in order:
   - the return registers;
   - memory writes outside the function's own stack frame;
   - the calls, as callee plus register and stack arguments;
   - the stack pointer at return, which also checks how many bytes the function pops (`ret N`) under its calling convention.

   Scratch registers, frame layout and spill slots are excluded, because a correct recompilation differs there.
4. Results are pass, fail, inconclusive (a run hit its step, time or page limit) and not exercised. Inconclusive and not exercised are never counted as passes.
5. Coverage is measured and reported per function: how many of the original's branches the runs executed. In the lifting project, random inputs reached 94 of one slice's 6,981 branches, so a pass from random inputs alone mostly says that the common paths agree.
6. Every function has an assumption record: its calling convention (cdecl, stdcall, thiscall, fastcall), which callees are stubbed, how globals and objects are seeded, and the floating-point mode. MSVC 5.0 computes with the x87 unit, whose precision and rounding a modern compiler does not reproduce, so floating-point results need a stated tolerance or exact x87 emulation on both sides.

## Making a pass mean something

- The harness must fail on purpose. A suite of seeded wrong variants (a flipped condition, a constant off by one, a missing or extra write, swapped arguments) runs whenever the harness changes, and each must fail. A check that has never failed is not known to work.
- Every input a tool skips is a counted class: an instruction the emulator or solver cannot execute, an offset format a parser ignores, an implicit register write a disassembler does not report. In the lifting project, one field check had silently ignored every hexadecimal offset and reported zero problems.
- Every disagreement is attributed to one side before anything changes. Most of the lifting project's refutations were checker bugs: [Triton](https://triton-library.github.io/)'s instruction semantics (a 32-bit shift that forgot the upper half is zero, missing SSE conversions and compares), [Capstone](https://www.capstone-engine.org/)'s flag details, and LLVM's register descriptions were each wrong somewhere.
- Two implementations agreeing proves nothing when they share a blind spot; compare their results item by item.

## Reaching the code random inputs miss

Coverage-guided inputs fill the gaps: for a branch of the original no run has taken, a symbolic engine (Triton, or [angr](https://angr.io/)) solves for registers and memory that reach it, and both functions run from that state. Small straight-line functions can be proved equivalent outright with the same engine.

For proofs about one program, the lifting project's approach scales well: prove each instruction's effect on a stated set of facts for all inputs (are the facts after an instruction implied by the facts before it?), which needs no loop unrolling or path enumeration. It proved about 400,000 steps over six slices without a refutation, at 30 to 40 milliseconds per step. Proving equivalence of two whole functions with calls and loops does not scale the same way, which is why emulation is the gate and symbolic solving serves coverage.

## What per-function checks miss

Stubbed callees cannot see a function breaking a contract another function relies on: an object left in a state its next method does not expect, a buffer sized differently, an order of side effects that only matters across calls. Whole-program behaviour tests sit on top: recorded input sequences replayed against the original and the rebuilt program, comparing outputs such as frames, saved games and network messages.

## Where it is hard for a game like Gruntz

The world is the hard part. Gruntz calls MFC (Microsoft Foundation Classes), DirectDraw and DirectSound, and keeps much of its state in globals and long-lived objects. Stubbing calls by call count keeps both sides consistent, but a function that reads an object's fields needs those objects seeded identically on both sides and in shapes the code expects; deriving them from addresses works for raw loads and fails for code that walks linked structures. Expect most of the harness work, and most inconclusive results, to come from seeding objects and globals.

## Build order

1. A two-sided harness for one function pair: the deterministic world, the effect comparison, limits that report inconclusive, the coverage counter, and the seeded-wrong suite. Validate it on functions already byte-exact (which must pass) and on seeded variants of them (which must fail).
2. A corpus runner with per-function assumption records and a report of pass, fail, inconclusive and coverage per function.
3. Symbolic or coverage-guided input generation for branches no run has taken.
4. Whole-program replay tests once enough functions pass.

On an existing byte-matching project the check can start at once on the open functions: gruntz-decomp's 346 would split into functionally correct, wrong, and not yet checkable, which is the number a reader of its progress actually wants.
