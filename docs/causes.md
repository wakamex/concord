# Primary causes of open functions on both targets

Question: of the differences concord's diff reports for an open function, which are causes an author or a search should act on, and which are fallout of an earlier cause? A function a few lines from a match can show hundreds of differing rows: one extra local grows the stack frame and moves every stack offset, one extra out-of-line call shifts what follows, and any structural change reshuffles register choices across the whole function.

Result: on both targets nearly two thirds of differing rows are fallout, and the median open function has 3 or 3.5 primary causes. 166 open gruntz-decomp functions and 195 open Harvest functions have exactly one.

`concord causes` measured this, on every open function of gruntz-decomp ([Microsoft Visual C++](https://learn.microsoft.com/en-us/cpp/) 5.0, i386 COFF, checkout at 8c3a113ca) and of Harvest (GCC 4.4, x86-64 ELF, the `harvest-sol` checkout). It reads the findings concord's diff already makes and keeps as primary: a different stack frame size (any stack-only displacement difference and any offset moved by exactly that size are its fallout); each callee called a different number of times, from the relocations (one-sided call rows are its fallout); exits and branches of another shape, different constants, a member reached at another offset (`field`), a different operation or computation on one side only (`operation`), and any other differing instruction, grouped by site; and, only when nothing structural differs, register allocation. Register choices, copies between registers, stack spills and reloads, and callee-saved saves are fallout whenever anything structural differs, since register allocation is decided over the whole function. Jump-table data after the last return and alignment padding are left out.

| Measure | gruntz-decomp | Harvest |
| --- | ---: | ---: |
| Open functions | 442 (475,342 bytes) | 550 (685,709 bytes) |
| Differing rows | 38,628 | 41,342 |
| Of them fallout | 24,613 (64%) | 27,005 (65%) |
| Median primary causes per function | 3 | 3.5 |
| Functions with one primary cause | 166 (75,779 bytes) | 195 (167,121 bytes) |
| Of those, register allocation only | 73 | 96 |
| Functions with 10 or more primary causes | 87 | 105 |

## Primary causes by kind

Functions that have at least one primary cause of the kind, and their bytes.

| Kind | gruntz-decomp | Harvest |
| --- | --- | --- |
| operation | 339 (428,891 bytes) | 348 (489,660 bytes) |
| control-flow | 98 (233,585 bytes) | 282 (469,678 bytes) |
| instruction | 102 (198,984 bytes) | 105 (246,543 bytes) |
| field | 83 (165,435 bytes) | 27 (61,383 bytes) |
| frame | 51 (134,455 bytes) | 59 (110,576 bytes) |
| calls | 41 (123,076 bytes) | 199 (230,439 bytes) |
| slots | 75 (110,308 bytes) | 29 (68,936 bytes) |
| register-allocation only | 73 (28,950 bytes) | 96 (96,687 bytes) |
| constant | 9 (25,828 bytes) | 7 (20,458 bytes) |

On gruntz-decomp the single-cause functions are 73 with register allocation alone, 72 with one differing operation, 7 with a slot order, 7 with a member offset, 5 with one control-flow site and 2 others; on Harvest, 96 with register allocation alone, 49 with one control-flow site, 34 with one operation, 5 with a call count and 11 others.

## Checked against known causes

- `CGruntzMgr::HandleCommand` (99.54%): 763 differing rows reduce to 9 primary causes: a stack frame 8 bytes smaller than retail's, six exit sites where retail jumps to a shared `return 1` and ours returns inline, and two operations: an `xor` and `and` in the other order, and one extra load in the screenshot command. These are the causes a hand session found and fixed (the frame, the exits) or classified as what remains.
- `TmDeflectStep` (91.10%): 4,217 rows reduce to 71, led by a stack frame 160 bytes larger and 19 more calls to `CMapMgr::CellFlagsAt` than retail, the cause a hand session found (a shared inline helper whose extra cost exhausted the inline budget), with three related helpers.

## Limits

- `operation` is the largest kind, and a site counts as one whenever the two sides compute with a different multiset of non-copy instructions; around extra out-of-line calls some of these are still fallout (in `TmDeflectStep`, 36 of its 71 causes are operations near the extra calls). `instruction` holds what fits no other kind.
- The frame rule treats any displacement or constant off by exactly the frame difference as fallout; a real difference of the same size would be missed.
- Source lines come from cl's line records on gruntz-decomp; Harvest causes are placed by offset only.
