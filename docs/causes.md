# Primary causes of open functions on both targets

Question: of the differences concord's diff reports for an open function, which are causes an author or a search should act on, and which are fallout of an earlier cause? A function a few lines from a match can show hundreds of differing rows: one extra local grows the stack frame and moves every stack offset, one extra out-of-line call shifts what follows, and any structural change reshuffles register choices across the whole function.

Result: on both targets about 60% of differing rows are fallout, and the median open function has 3 or 4 primary causes. 152 open gruntz-decomp functions and 185 open Harvest functions have exactly one.

`concord causes` measured this, on every open function of gruntz-decomp ([Microsoft Visual C++](https://learn.microsoft.com/en-us/cpp/) 5.0, i386 COFF, checkout at 8c3a113ca) and of Harvest (GCC 4.4, x86-64 ELF, the `harvest-sol` checkout). It reads the findings concord's diff already makes and keeps as primary: a different stack frame size (any stack-only displacement difference and any offset moved by exactly that size are its fallout); each callee called a different number of times, from the relocations (one-sided call rows are its fallout); exits and branches of another shape, different constants, and other differing instructions, grouped by site; and, only when nothing structural differs, register allocation. Register choices, spills, reloads and callee-saved saves are fallout whenever anything structural differs, since register allocation is decided over the whole function. Jump-table data after the last return and alignment padding are left out.

| Measure | gruntz-decomp | Harvest |
| --- | ---: | ---: |
| Open functions | 442 (475,342 bytes) | 550 (685,709 bytes) |
| Differing rows | 38,628 | 41,342 |
| Of them fallout | 23,672 (61%) | 26,516 (64%) |
| Median primary causes per function | 3 | 4 |
| Functions with one primary cause | 152 (67,083 bytes) | 185 (161,528 bytes) |
| Of those, register allocation only | 58 | 86 |
| Functions with 10 or more primary causes | 94 | 109 |

## Primary causes by kind

Functions that have at least one primary cause of the kind, and their bytes.

| Kind | gruntz-decomp | Harvest |
| --- | --- | --- |
| instruction | 366 (441,196 bytes) | 388 (518,248 bytes) |
| control-flow | 111 (246,955 bytes) | 288 (478,765 bytes) |
| frame | 51 (134,455 bytes) | 59 (110,576 bytes) |
| calls | 41 (123,076 bytes) | 199 (230,439 bytes) |
| slots | 78 (111,435 bytes) | 30 (70,418 bytes) |
| register-allocation only | 58 (22,421 bytes) | 86 (88,399 bytes) |
| constant | 11 (30,349 bytes) | 7 (20,458 bytes) |

On gruntz-decomp the single-cause functions are 82 with one differing instruction site, 58 with register allocation alone, 7 with a slot order and 5 with one control-flow site; on Harvest, 86 with register allocation alone, 49 with one instruction site, 46 with one control-flow site and 4 with a call count.

## Checked against known causes

- `CGruntzMgr::HandleCommand` (99.54%): 763 differing rows reduce to 9 primary causes: a stack frame 8 bytes smaller than retail's, six exit sites where retail jumps to a shared `return 1` and ours returns inline, an `xor` and `and` in the other order, and one load in the screenshot command. These are the causes a hand session found and fixed (the frame, the exits) or classified as what remains.
- `TmDeflectStep` (91.10%): 4,217 rows reduce to 76, led by a stack frame 160 bytes larger and 19 more calls to `CMapMgr::CellFlagsAt` than retail, the cause a hand session found (a shared inline helper whose extra cost exhausted the inline budget), with three related helpers.

## Limits

- `instruction` is the largest kind and the least specific: it holds every differing site that is not a frame, call count, branch, constant or slot difference, including reloads around extra calls that are really fallout. In `TmDeflectStep` most of its 62 such sites are of that kind.
- The frame rule treats any displacement or constant off by exactly the frame difference as fallout; a real difference of the same size would be missed.
- Source lines come from cl's line records on gruntz-decomp; Harvest causes are placed by offset only.
