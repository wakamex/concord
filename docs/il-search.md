# Searching MSVC 5.0's intermediate language for register-allocation fixes

Status: built. This proposal is kept as the design it was reviewed as; the README's IL search section and `docs/il-search-results.md` describe what is now true.

This proposes a deterministic search for the near misses that differ from retail only in register choices: search the intermediate language (IL) that MSVC 5.0's front end hands its code generator, find the IL that compiles to retail's code, then find the C++ that produces that IL. It is written for review before it is built.

## The problem

[gruntz-decomp](https://github.com/sushi-shi/gruntz-decomp) reconstructs the 1999 game Gruntz with Microsoft Visual C++ 5.0 (Visual Studio 97 Service Pack 3). It has 4,080 of 4,426 functions byte-exact. Of the open functions concord's census classified, 232 of 359 first differ from retail in a register choice, with the same calls and branches.

MSVC 5.0 compiles in two stages. The front end, c1xx.dll, turns C++ into IL: postfix bytecode in four files (`ex` function bodies, `sy` symbols, `gl` a global table, `in` initializers). The code generator, c2.exe, turns IL into machine code, choosing registers. `concord il capture` keeps the IL a compile hands c2, and `concord il replay` runs c2 alone on it; c2 is a deterministic function of the IL and replay reproduces the object byte for byte.

`concord regtrace` traces c2's local register allocator. c2 gives each temporary a register while it selects instructions: a temporary moved to or from a physical register (a call's result, `this`, an argument, a return value) prefers that register and gets it when free; otherwise it gets the next free one of eax, ecx and edx in round-robin order from a cursor that starts at eax for each function. regtrace ties each grant to its instruction and checks whether retail's registers alone make the function exact.

## What is established

- On the 110 open functions at 97% or more whose first difference is register allocation, retail's registers alone make 21 exact and improve 32 more; 43 show no differing grant (their difference lies outside the local allocator).
- On `CBehindCandy`'s constructor, undoing in the debugger only the cursor advance of one grant (the frame-image pointer load) makes the function match retail instruction for instruction. Six sibling constructors share the cause.
- On `CGrunt::CreateHealthSprite` and four siblings, two grants have their registers swapped: the loads of two arguments to one call, which retail creates in the other order.
- Source searches have not closed these. `concord permute` (about 20 source rewrites, plus a new accessor rewrite) made 1 of the 21 exact. More than 30 hand-written spellings of the constructors' macro, a Haiku agent's 5 variants of CreateHealthSprite, and 3,000 permute candidates on the expanded constructor moved none of the target grants. c2's preference rule, read from its pre-pass, sets a preference only on moves to or from a physical register, so no spelling of a plain member load can acquire one.

## Why source search fails

Every search so far mutates C++ and hopes the front end emits IL that moves the one allocator decision regtrace names. The rewrite sets do not contain the needed change, and the mapping from C++ to IL is not known well enough to aim at it. The decision is made in c2 from the IL, so the IL is where the target can be stated exactly.

## Proposal

1. An IL parser. Built: the decoder covers the constructs in the decoding catalog (see the README's IL search section), and the measured results are in il-search-results.md. The parser turns a function's `ex` bytes into an expression tree per statement, with operand arity per opcode, and writes the tree back; `gl`'s offsets are shifted for a body whose length changes, which is already done and checked (deleting a store at the IL level reproduces the matching source variant exactly).
2. A structured IL mutator over the function's trees: swap commutative operands and independent statements, introduce or remove a temporary, and move statements. Call arguments are not swapped: an argument's place in the IL is its parameter position, so swapping two passes the callee other values. A sweep that allowed it improved 41 functions with a single argument swap and made none exact, and on `CBattlezAiController::IsCoordOccupied` the IL swap and the same swap written in C++ (`CellFlagsAt(y, x)`) scored the same, 96.68367. Each variant is replayed (about a second) and compared with retail; a variant that matches retail is the target IL.
3. An inverse lookup. Given the target IL's difference from ours (for example two call arguments' subtrees in the other order), compile small C++ variants and compare their IL, as the corpora already do, to find the construct the front end turns into it.

CreateHealthSprite's two argument loads in the other order are not reachable by swapping the argument subtrees, since that swaps the parameters they are passed as.

## What it can and cannot conclude

If an IL variant matches retail, the remaining question is narrow and testable. If none does within the mutator's space, no front-end output for this function's statements reproduces retail, and the cause lies outside them: the rest of the unit (c2 compiles functions in one process with shared state), or a compiler build not on hand (the release build and SP3 compile the constructor identically; SP1 and SP2 are untested). That stops further source searching on those functions on evidence.

## Costs and risks

- About one session to build the parser and mutator; then minutes per function.
- The IL format is undocumented and decoded by observation; an opcode decoded wrong produces IL c2 rejects or misreads. Replay catches rejection; a misread shows as an unexpected object, which the search compares with retail anyway.
- The mutator's space is chosen by hand; an empty result is a bound on that space, not proof that no IL works.
- c2 may run front-end-dependent passes whose input the mutator never varies, such as symbol order in `sy`.

## Alternatives considered

- More source rewrites in permute: the rewrite set grows blind without knowing which IL change is needed.
- Parallel model agents per function: one cheap test (Haiku, $0.14) tried the same kinds of changes and stopped early.
- Reading c2's allocator fully: done for the preference and round-robin rules; it explains why a register is chosen, not which source yields a given IL.
