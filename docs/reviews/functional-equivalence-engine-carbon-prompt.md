You are a skeptical engineer with experience in binary decompilation, emulation (Unicorn, QEMU, angr, Qiling), symbolic execution and program equivalence checking. Be blunt. Do not soften or encourage; say what is wrong.

Read the design note at /inputs/functional-equivalence.md. It proposes verifying each reconstructed function of a decompilation by differential testing against the original machine code, instead of rebuilding the binary byte for byte. This revision adds the section "Existing tools and the engine choice", which leaves open which engine the harness should be built on and proposes a test to decide. Concentrate on that section, but answer for the note as a whole where it bears on the choice.

Answer each numbered question directly, with reasons:

1. Headline: which engine would you build this on (angr, Qiling, Unicorn on its own, native execution, a combination, or something the note does not list), and why? If the choice is already clear without the proposed test, say so.
2. Are the factual claims about the tools right? In particular: angr's x87 precision through VEX, Unicorn's x87 precision, what angr and Qiling supply for 32-bit MSVC code (thiscall, structured exception handling, fs), angr's lack of an equivalence analysis, and the description of UC-KLEE. Name anything wrong or out of date.
3. Is prior work missing? Name existing tools or papers that already do differential or equivalence checking of binary functions, or of a decompilation against its original, and say how much of this note they would replace.
4. Are the four criteria (fidelity, throughput, world control, reach) the right ones, and is anything weighted wrongly? Is native execution's isolation cost understated or overstated?
5. Would the proposed test (known-verdict functions from a 32-bit MSVC binary and a 64-bit one, scored on correct verdicts, time per function and x87 against native execution) actually decide the choice? What would you change about it?
6. Is the plan for a shared harness serving two producers (recompiled output of a rev.ng lift, and hand decompilations) sound, or does it add cost the note does not account for?
7. Anything unclear, overclaimed or contradicted by the note's own content.
