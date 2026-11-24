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
| Defined outside the unit's source, in the project's headers | 39 open copies | 174 open copies of 46 functions (33,006 bytes) |
| Defined outside the unit's source, in library headers | none | 82 open copies of 67 functions (62,317 bytes) |
| Register allocation only | 73 (28,950 bytes) | 95 (94,886 bytes) |

A function defined in a header is compiled again in every unit that uses it, so it can be open in many units at once. On Harvest each cause of such a function is placed at the line of the function's own body in its header, from the outermost frame of the debug information's inline chain. Its 174 open copies in project headers are 46 functions, and five `IGUIElement` methods compiled in up to 23 GUI units make up 106 of them; of the 15 functions with more than one copy, 11 have the same causes in every copy, so one change to the header would close all of their copies. The 82 copies in library headers are `std::` template instances in libstdc++'s headers under `/usr/include`, which the project cannot edit. They do not point at the toolchain: of 90 such instances, 37 call some function a different number of times than retail, and in 25 of those the function is a member of the project's `ox::core::CString` (ours makes fewer out-of-line calls to its `operator=` in 18 and more calls to `operator new[]`, which its inlined copy constructor makes, in 18), and 20 differ only in register allocation. Their largest cause is how GCC inlines `CString`'s members, set in `src/ox/core/CString.h`, which the knowledge base describes as `cstring-copy-assign-not-inlined`. cl's line records name no file, so on gruntz-decomp the 39 copies of header functions keep no line.

## Checked against known fixes

`concord bench --causes` asks whether the causes point at a known change. A cause counts as near when one of its source lines is within two lines of a changed line, and sites are listed with the most causes first, so the first site is where concord sends an author first.

The first test uses real fixes. `concord bench --history 300` takes each function that is exact now and that one of the last 300 commits changed (and no later commit), and puts back its body from before that commit, so the change to find is the one a person made to match it. Cases where only that function stops matching are kept, at most 80. A fix made in a header or across several functions is replayed only in part.

| Real fixes from history | gruntz-decomp | Harvest |
| --- | ---: | ---: |
| Cases | 57 | 80 |
| Mean primary causes / sites per case | 2.3 / 1.5 | 4.0 / 2.0 |
| First site near the fix | 37 | 62 |
| Some cause near the fix | 39 | 65 |
| Of them with a structural cause: cases, first site near | 41, 31 | 76, 60 |
| Register allocation only: cases, first site near | 16, 6 | 4, 2 |

Where something structural differs, the first site holds the fix in about three cases of four on both targets. Where only register allocation differs, the place the registers start to differ is not where the source changed: in the gruntz-decomp misses the fix (a temporary named or inlined) was 5 to 19 lines before or after it, since c2's choices shift over a stretch of code.

The second test uses concord's own rewrites: it changes exact functions with one or two of them and keeps the cases where only that function stops matching. Both runs used 120 sampled exact functions, at most 15 cases of each kind and up to 30 two-edit cases.

| Synthetic rewrites | gruntz-decomp | Harvest |
| --- | ---: | ---: |
| Cases | 68 | 42 |
| Mean primary causes per case | 2.1 | 3.2 |
| Mean sites per case | 1.5 | 1.7 |
| Cases with no more sites than edits | 49 | 24 |
| First site near the change | 56 | 35 |
| Some cause near the change | 58 | 37 |

Three changes raised these numbers, measured on the same cases: placing call-count and register-allocation causes at their first differing row (before, they had no place), grouping causes into sites, and listing the site with the most causes first rather than the first cause in a fixed order of kinds. On the real Harvest fixes the first site holds the fix in 62 cases, the first cause in kind order in 39.

## Checked against hand diagnoses

- `CGruntzMgr::HandleCommand` (99.54%): 763 differing rows reduce to 9 primary causes: a stack frame 8 bytes smaller than retail's, six exit sites where retail jumps to a shared `return 1` and ours returns inline, and two operations: an `xor` and `and` in the other order, and one extra load in the screenshot command. These are the causes a hand session found and fixed (the frame, the exits) or classified as what remains.
- `TmDeflectStep` (91.10%): 4,217 rows reduce to 71, led by a stack frame 160 bytes larger and 19 more calls to `CMapMgr::CellFlagsAt` than retail, the cause a hand session found (a shared inline helper whose extra cost exhausted the inline budget), with three related helpers.

## Register allocation only, checked with regtrace

`concord regtrace` replays each of the 73 gruntz-decomp functions whose only cause is register allocation with retail's register at every round-robin grant of c2's local allocator, which hands out eax, ecx and edx. 22 become exact, among them `CGrunt::CreateHealthSprite` and its six siblings and seven `CGameObject`-taking constructors; 13 rise without becoming exact and 6 do not rise; in 32 the local allocator's grants already agree with retail, so their differing registers come from c2's global allocator, which assigns ebx, esi and edi and which regtrace does not steer. The label holds in each case, since every differing row has the same instruction shape on both sides; what it does not say is which allocator made the choice.

## Limits

- `operation` is the largest kind, and a site counts as one whenever the two sides compute with a different multiset of non-copy instructions; around extra out-of-line calls some of these are still fallout (in `TmDeflectStep`, 36 of its 71 causes are operations near the extra calls). `instruction` holds what fits no other kind.
- The frame rule treats any displacement or constant off by exactly the frame difference as fallout; a real difference of the same size would be missed.
- On gruntz-decomp a function defined in a header gets no line, since cl's line records do not name the file; 17 Harvest functions defined in their unit get none either, because their differing code comes only from inlined callees.
- A site is a cluster of nearby lines, not a proven edit: in the benchmark one change still gives more than one site in 19 of 68 gruntz-decomp cases and 18 of 42 Harvest cases.
