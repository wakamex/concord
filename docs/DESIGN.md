# concord design

## Goal

Given a target binary (x86-64, C++), produce source that recompiles, with the original toolchain and flags, to byte-identical object code. Matching is verified per function against the target image, section by section.

## Why a decompiler is not enough

A correct decompile recovers behavior. The gap between a correct decompile and a match is codegen, not semantics: the unmatched functions differ in register allocation, basic-block order, loop-compare operand order, and inlining order. These are compiler decisions. So the problem concord solves is finding the source form and flags that reproduce the original compiler's exact decisions, with that compiler in the loop as the scorer. A decompiler supplies the seed and, separately, the semantics oracle; neither role solves the matching problem.

## Pipeline

The stages, each a module under src/concord and a subcommand in cli.py:

1. seed (seed.py). Take the most source-shaped correct decompile as the starting candidate. [Ghidra](https://ghidra-sre.org/) or [Binary Ninja](https://binary.ninja/) output, with types and names, beats a machine-shaped lift here. The seed must be semantically right and roughly structured; the search does the rest.

2. types (types_model.py). Recover class and struct layout from a cross-platform debug map, from [run-time type information (RTTI)](https://en.wikipedia.org/wiki/Run-time_type_information) and virtual function tables (vtables) in the target, and from data-layout analysis (DLA) over the lift for the remainder. DLA infers field-level shape from memory accesses but not class identity, so it fills gaps rather than leading. Layout is fixed before codegen search because field order, size and padding change member offsets, this-pointer math and vtable shape, and therefore the bytes.

3. toolchain and flags (toolchain.py). Run the pinned original compiler in a reproducible container. Infer flags once from anchor functions with known source, then hold them fixed so the search varies only the source.

4. compile (compile.py). Compile a candidate's translation unit, isolate the target function's section, and place it like the target so the diff compares like with like.

5. diff (diff.py). Disassemble and align both sides and attribute each mismatch to a cause that maps to a fix (operand order, block order, register allocation, inlining, stack or struct layout, immediate or relocation, instruction selection). Attribution is what makes the search directed instead of random.

6. oracle (oracle.py). Lift the target function once with a machine-shaped lifter such as [rev.ng](https://rev.ng/) as the semantic reference, and reject any candidate that disagrees with it under differential testing on generated inputs. Equivalence between a GCC-built candidate and a lift is undecidable in general, and the two differ in calling convention, memory model and representation, so an IR-level equivalence proof is not a realistic gate. Differential testing finds wrong rewrites but cannot prove their absence; its strength depends on input generation and coverage of the function's paths. The final check is the byte match itself: a candidate that compiles to the target's exact bytes with the same toolchain has the target's machine-level behavior. The oracle protects the intermediate candidates and the best-effort result when no exact match is found. This is the role a machine-shaped lift plays well: rev.ng keeps indirect calls indirect and does not recover class identity, which makes its output a faithful reference and a poor seed.

7. transforms (transforms.py). A library of source rewrites intended to preserve semantics that steer codegen, each tagged with the cause it addresses. Whether a rewrite is safe can depend on the function (signedness, aliasing, evaluation order), so each application is checked against the oracle.

8. search (search.py). Compile, diff, read causes, apply matching transforms, keep the candidates the oracle accepts, move to the best score, repeat until exact or out of budget.

## Relationship to existing tools

concord unifies ideas that already exist in the console-decomp ecosystem and adds the two pieces that are missing for x86-64 and C++: cause-attributed diffs and an oracle-gated transformation search.

- [m2c](https://github.com/matt-kempster/m2c): decompiles 32-bit MIPS, ARM, PowerPC and SuperH assembly into C aimed at matching, with partial C++ support. concord targets x86-64 and C++ and automates convergence rather than emitting one candidate.
- [decomp-permuter](https://github.com/simonlindholm/decomp-permuter): randomized source permutation, keeping what scores better. concord directs the search with cause attribution and checks candidates against the oracle.
- [objdiff](https://github.com/encounter/objdiff) and [asm-differ](https://github.com/simonlindholm/asm-differ): score the gap. concord adds cause attribution on top of a scorer.
- [decomp.me](https://decomp.me/): the compile-in-the-loop scoreboard. concord internalizes that loop under search.

## Hard parts, stated plainly

- The source-to-codegen mapping is specific to each compiler version and underdetermined: many source forms yield the same bytes, and a one-line change can flip many bytes, so the objective is discontinuous and non-monotone.
- Old compilers (for example GCC 4.4) have quirks that must be modeled or searched blindly.
- The search space of source forms is large and scoring is a compile per candidate.
- The oracle gives evidence, not proof: differential testing can miss a behavior difference on paths its inputs never reach.

The two highest-leverage pieces, and the reason concord exists rather than reusing the parts above, are cause-attributed diffs (stage 5) and the oracle-gated transformation search (stages 6 to 8). Those are where automation moves past the current manual loop.

## Build order

The dependency order for implementation is: toolchain and compile first (so anything can be scored), then diff (so the score is actionable), then oracle (so moves are safe), then transforms and search (the loop), with seed and types feeding the front. A thin end-to-end path on a single easy function, seed to exact match, comes before breadth.
