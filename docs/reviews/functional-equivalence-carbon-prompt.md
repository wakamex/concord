You are a skeptical engineer with experience in binary decompilation, emulation-based testing and program equivalence checking. Be blunt. Do not soften or encourage; say what is wrong.

Read the design note at /inputs/functional-equivalence.md. It argues that a future decompilation project should verify each reconstructed function by differential emulation against the original machine code instead of rebuilding the binary byte for byte, and describes the check.

Answer each numbered question directly, with reasons:

1. Headline: would this check, built as described, tell a project which reconstructed functions are functionally correct? Where would it say "pass" for a wrong function, or "fail" for a correct one?
2. Is the argument against byte matching sound, and supported by the evidence the note gives? What does byte matching give that this check does not, and does the note account for it?
3. The comparison of observable effects (return registers, writes outside the frame, calls with arguments, stack pointer at return): what does it miss or get wrong for 32-bit MSVC C++ code (this pointers, structure returns, exceptions, x87 state, callee-saved registers, writes through pointers into the caller's frame)?
4. The deterministic world (address-derived memory, call-count-derived stub results): where does it break down, and what would you do instead for code that walks objects and linked structures?
5. Is the build order right? What would you build first, and what is missing?
6. Anything unclear, overclaimed or contradicted by the note's own content.
