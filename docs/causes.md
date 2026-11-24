# Primary causes of open functions on both targets

Question: of the differences concord's diff reports for an open function, which are causes an author or a search should act on, and which are fallout of an earlier cause? A function a few lines from a match can show hundreds of differing rows: one extra local grows the stack frame and moves every stack offset, one extra out-of-line call shifts what follows, and any structural change reshuffles register choices across the whole function.

Result: on both targets nearly two thirds of differing rows are fallout. Grouping the remaining primary causes by source line gives source sites, and one source change usually explains one site: the median open function has 2 sites on both targets. 165 open gruntz-decomp functions and 130 Harvest functions have a single site, but most of the bytes are in functions with four or more.

`concord causes` measured this, on every open function of gruntz-decomp ([Microsoft Visual C++](https://learn.microsoft.com/en-us/cpp/) 5.0, i386 COFF, checkout at 8c3a113ca) and of Harvest (GCC 4.4, x86-64 ELF, checkout at 1e5eb17). It reads the findings concord's diff already makes and keeps as primary: a different stack frame size (any stack-only displacement difference and any offset moved by exactly that size are its fallout); each callee called a different number of times, from the relocations, placed at the first differing call to it (one-sided call rows are its fallout); exits and branches of another shape, different constants, a member reached at another offset (`field`), a different operation or computation on one side only (`operation`), and any other differing instruction, grouped by site; and, only when nothing structural differs, register allocation, placed at its first differing row. Register choices, copies between registers, stack spills and reloads, and callee-saved saves are fallout whenever anything structural differs, since register allocation is decided over the whole function. Jump-table data after the last return and alignment padding are left out. Each primary cause gets the source line of its code from a compile with line information (cl's line records on gruntz-decomp, DWARF on Harvest), and causes within two lines of each other form one site.

| Measure | gruntz-decomp | Harvest |
| --- | ---: | ---: |
| Open functions | 442 (475,342 bytes) | 560 (694,746 bytes) |
| Differing rows | 38,628 | 44,601 |
| Of them fallout | 24,613 (64%) | 28,499 (64%) |
| Functions whose causes have source lines | 403 (455,417 bytes) | 287 (527,780 bytes) |
| Of those, median sites | 2 | 2 |
| One site | 165 (80,922 bytes, 18%) | 130 (181,039 bytes, 34%) |
| 2 or 3 sites | 112 (68,457 bytes, 15%) | 89 (145,012 bytes, 27%) |
| 4 to 9 sites | 89 (153,351 bytes, 34%) | 56 (140,023 bytes, 27%) |
| 10 or more sites | 37 (152,687 bytes, 34%) | 12 (61,706 bytes, 12%) |
| Functions with no cause in the unit's own source | 39 (19,925 bytes) | 270 (165,258 bytes) |
| Register allocation only | 73 (28,950 bytes) | 95 (94,886 bytes) |

The functions with no cause in the unit's own source are code from headers: on Harvest, 83 library template instances (`std::vector` and others) and inline methods emitted in many units, such as `IGUIElement::removeChild` in every GUI unit.

## Checked against known changes

`concord bench --causes` takes exact functions, changes each with one of concord's own rewrites (or two, for the two-edit cases), keeps the cases where only that function stops matching, and asks whether the causes point at the changed lines. A cause counts as near when one of its source lines is within two lines of a changed line. Both runs used 120 sampled exact functions, at most 15 cases of each kind and up to 30 two-edit cases.

| Measure | gruntz-decomp | Harvest |
| --- | ---: | ---: |
| Cases | 68 | 42 |
| Mean primary causes per case | 2.1 | 3.2 |
| Mean sites per case | 1.5 | 1.7 |
| Cases with no more sites than edits | 49 | 24 |
| First cause listed is near the change | 46 | 29 |
| Some cause is near the change | 58 | 37 |

Placing call-count and register-allocation causes at their first differing row raised the cases whose first cause is near the change from 40 to 46 on gruntz-decomp and from 20 to 29 on Harvest, on the same cases; before, those two kinds had no place.

## Primary causes by kind

Functions that have at least one primary cause of the kind, and their bytes.

| Kind | gruntz-decomp | Harvest |
| --- | --- | --- |
| operation | 339 (428,891 bytes) | 359 (502,570 bytes) |
| control-flow | 98 (233,585 bytes) | 289 (482,794 bytes) |
| instruction | 102 (198,984 bytes) | 107 (252,795 bytes) |
| field | 83 (165,435 bytes) | 28 (62,419 bytes) |
| frame | 51 (134,455 bytes) | 61 (115,918 bytes) |
| calls | 41 (123,076 bytes) | 212 (249,624 bytes) |
| slots | 75 (110,308 bytes) | 31 (78,050 bytes) |
| register-allocation only | 73 (28,950 bytes) | 95 (94,886 bytes) |
| constant | 9 (25,828 bytes) | 9 (26,629 bytes) |

## Checked against hand diagnoses

- `CGruntzMgr::HandleCommand` (99.54%): 763 differing rows reduce to 9 primary causes: a stack frame 8 bytes smaller than retail's, six exit sites where retail jumps to a shared `return 1` and ours returns inline, and two operations: an `xor` and `and` in the other order, and one extra load in the screenshot command. These are the causes a hand session found and fixed (the frame, the exits) or classified as what remains.
- `TmDeflectStep` (91.10%): 4,217 rows reduce to 71, led by a stack frame 160 bytes larger and 19 more calls to `CMapMgr::CellFlagsAt` than retail, the cause a hand session found (a shared inline helper whose extra cost exhausted the inline budget), with three related helpers.

## Register allocation only, checked with regtrace

`concord regtrace` replays each of the 73 gruntz-decomp functions whose only cause is register allocation with retail's register at every round-robin grant of c2's local allocator, which hands out eax, ecx and edx. 22 become exact, among them `CGrunt::CreateHealthSprite` and its six siblings and seven `CGameObject`-taking constructors; 13 rise without becoming exact and 6 do not rise; in 32 the local allocator's grants already agree with retail, so their differing registers come from c2's global allocator, which assigns ebx, esi and edi and which regtrace does not steer. The label holds in each case, since every differing row has the same instruction shape on both sides; what it does not say is which allocator made the choice.

## Limits

- `operation` is the largest kind, and a site counts as one whenever the two sides compute with a different multiset of non-copy instructions; around extra out-of-line calls some of these are still fallout (in `TmDeflectStep`, 36 of its 71 causes are operations near the extra calls). `instruction` holds what fits no other kind.
- The frame rule treats any displacement or constant off by exactly the frame difference as fallout; a real difference of the same size would be missed.
- A cause in code from a header has no line in the unit's own source, so a function made only of such code (a header inline method or a library template) gets no sites; these are reported separately above.
- A site is a cluster of nearby lines, not a proven edit: in the benchmark one change still gives more than one site in 19 of 68 gruntz-decomp cases and 18 of 42 Harvest cases.
