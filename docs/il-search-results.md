# IL search results: 20 open register-allocation functions

This run asks how much of each register-allocation near miss `concord il-search` can edit, and whether any edit gives retail's bytes. Before the parser decoded the constructs these functions use, statements covered 0.8 to 19.9 percent of each body (median 12.9 percent), and 13 of the 20 functions had no legal edit. The parser now covers 50.9 to 89.8 percent (median 70.7 percent), and every function has between 1 and 51 legal single edits (median 20). One function, `?StartResourceGrinderDrop@CStatusBarMgr@@QAEHHHH@Z`, has an exact variant: one statement moved, found with five replays. The other 19 have no exact variant at the default depth, beam and budget.

The search ran with its defaults (depth 2, budget 400 replays, beam 8) on the gruntz-decomp checkout at `/code/gruntz-match-3`, at commit 8c3a113ca, with no tracked changes at the start or the end. Every body of all 289 units gives back its own bytes through the parser (102,524 bodies).

## Coverage and legal edits before and after

Before is the parser as it stood before the decoder grew, rerun on the same captures. Coverage is the share of body bytes that lie in statements. Legal edits are the distinct bodies one legal single edit away from the unedited body. Best is the highest score any variant reached, the unedited body included.

| # | Unit | Function | Parse coverage before | Parse coverage after | Legal edits before | Legal edits after | Best before | Best after | Exact IL after |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | behindcandy | `??0CBehindCandy@@QAE@PAUCGameObject@@@Z` | 13.2% | 58.6% | 0 | 5 | 99.829 | 99.829 | no |
| 2 | behindcandyani | `??0CBehindCandyAni@@QAE@PAUCGameObject@@@Z` | 14.4% | 64.3% | 0 | 6 | 99.851 | 99.851 | no |
| 3 | donothing | `??0CDoNothing@@QAE@PAUCGameObject@@@Z` | 10.1% | 57.7% | 0 | 4 | 99.829 | 99.829 | no |
| 4 | eyecandy | `??0CEyeCandy@@QAE@PAUCGameObject@@@Z` | 17.7% | 62.7% | 2 | 8 | 98.722 | 98.722 | no |
| 5 | eyecandyani | `??0CEyeCandyAni@@QAE@PAUCGameObject@@@Z` | 18.3% | 66.4% | 2 | 9 | 98.874 | 98.874 | no |
| 6 | frontcandy | `??0CFrontCandy@@QAE@PAUCGameObject@@@Z` | 13.2% | 58.8% | 0 | 5 | 99.831 | 99.831 | no |
| 7 | grunt | `?CreateHealthSprite@CGrunt@@QAEHXZ` | 3.8% | 83.6% | 0 | 20 | 99.655 | 99.655 | no |
| 8 | grunt | `?CreateStaminaSprite@CGrunt@@QAEHXZ` | 3.8% | 83.6% | 0 | 20 | 99.649 | 99.649 | no |
| 9 | grunt | `?CreateToyTimeSprite@CGrunt@@QAEHXZ` | 8.1% | 83.7% | 0 | 20 | 99.722 | 99.722 | no |
| 10 | grunt | `?CreateWingzTimeSprite@CGrunt@@QAEHXZ` | 6.0% | 84.2% | 0 | 20 | 99.706 | 99.706 | no |
| 11 | grunt | `?CreatePowerupSprite@CGrunt@@QAEHH@Z` | 4.1% | 83.1% | 0 | 20 | 99.630 | 99.630 | no |
| 12 | statichazard | `??0CStaticHazard@@QAE@PAUCGameObject@@@Z` | 15.5% | 77.5% | 0 | 12 | 95.251 | 95.251 | no |
| 13 | sbi_sidetab | `?UpdateSampleIcon@CSBI_SideTab@@QAEHXZ` | 17.7% | 75.3% | 0 | 3 | 96.162 | 96.162 | no |
| 14 | gamelevel | `?LoadWwd@CGameLevel@@UAEHPAUWwdHeader@@@Z` | 19.8% | 71.0% | 14 | 49 | 97.493 | 97.498 | no |
| 15 | fontconfig | `?DrawCenteredText@CGameText@@QAEHPBVCString@@PAUHDC__@@PAUtagRECT@@HHHHHHH@Z` | 0.8% | 50.9% | 0 | 48 | 97.245 | 97.250 | no |
| 16 | sbi_rectonly | `?StartResourceGrinderDrop@CStatusBarMgr@@QAEHHHH@Z` | 19.9% | 89.8% | 3 | 31 | 97.909 | 100.000 | yes |
| 17 | ddrawshadeblit | `?BuildRle@CDDrawShadeBlit@@QAEHPAEHHHHPAUtagPALETTEENTRY@@@Z` | 12.6% | 74.0% | 3 | 26 | 98.579 | 98.579 | no |
| 18 | imagepolyclip | `?WarpTextureBlit@@YAHPAUClipVtx@@HPAVCDDSurface@@1HH@Z` | 16.6% | 56.4% | 10 | 69 | 99.160 | 99.160 | no |
| 19 | multistartdlg | `?RefreshWorldControls@CMultiStartDlg@@QAEHXZ` | 3.6% | 63.8% | 0 | 1 | 99.724 | 99.724 | no |
| 20 | triggermgrgrid | `?ApplySwitch@CTriggerMgr@@QAEHPAVCGrunt@@HH@Z` | 9.4% | 70.4% | 5 | 51 | 94.697 | 94.774 | no |

## Rerun with the decoder

| # | Unit | Function | Group | Statements | Body bytes | Statement bytes | Parse coverage | Baseline | Best | Exact IL | Edits in best | Legal edits | Replays | Seconds | Source clean |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |
| 1 | behindcandy | `??0CBehindCandy@@QAE@PAUCGameObject@@@Z` | named | 14 | 960 | 563 | 58.6% | 99.829 | 99.829 | no | 0 | 5 | 16 | 13.8 | yes |
| 2 | behindcandyani | `??0CBehindCandyAni@@QAE@PAUCGameObject@@@Z` | named | 18 | 1164 | 748 | 64.3% | 99.851 | 99.851 | no | 0 | 6 | 22 | 14.6 | yes |
| 3 | donothing | `??0CDoNothing@@QAE@PAUCGameObject@@@Z` | named | 13 | 938 | 541 | 57.7% | 99.829 | 99.829 | no | 0 | 4 | 11 | 12.9 | yes |
| 4 | eyecandy | `??0CEyeCandy@@QAE@PAUCGameObject@@@Z` | named | 17 | 1149 | 720 | 62.7% | 98.722 | 98.722 | no | 0 | 8 | 41 | 16.6 | yes |
| 5 | eyecandyani | `??0CEyeCandyAni@@QAE@PAUCGameObject@@@Z` | named | 21 | 1334 | 886 | 66.4% | 98.874 | 98.874 | no | 0 | 9 | 50 | 18.6 | yes |
| 6 | frontcandy | `??0CFrontCandy@@QAE@PAUCGameObject@@@Z` | named | 14 | 964 | 567 | 58.8% | 99.831 | 99.831 | no | 0 | 5 | 16 | 13.9 | yes |
| 7 | grunt | `?CreateHealthSprite@CGrunt@@QAEHXZ` | named | 13 | 865 | 723 | 83.6% | 99.655 | 99.655 | no | 0 | 20 | 142 | 44.1 | yes |
| 8 | grunt | `?CreateStaminaSprite@CGrunt@@QAEHXZ` | named | 13 | 865 | 723 | 83.6% | 99.649 | 99.649 | no | 0 | 20 | 142 | 44.0 | yes |
| 9 | grunt | `?CreateToyTimeSprite@CGrunt@@QAEHXZ` | named | 19 | 1105 | 925 | 83.7% | 99.722 | 99.722 | no | 0 | 20 | 142 | 45.4 | yes |
| 10 | grunt | `?CreateWingzTimeSprite@CGrunt@@QAEHXZ` | named | 17 | 1018 | 857 | 84.2% | 99.706 | 99.706 | no | 0 | 20 | 142 | 45.6 | yes |
| 11 | grunt | `?CreatePowerupSprite@CGrunt@@QAEHH@Z` | named | 12 | 811 | 674 | 83.1% | 99.630 | 99.630 | no | 0 | 20 | 142 | 44.2 | yes |
| 12 | statichazard | `??0CStaticHazard@@QAE@PAUCGameObject@@@Z` | spread | 39 | 2147 | 1663 | 77.5% | 95.251 | 95.251 | no | 0 | 12 | 72 | 22.3 | yes |
| 13 | sbi_sidetab | `?UpdateSampleIcon@CSBI_SideTab@@QAEHXZ` | spread | 25 | 1116 | 840 | 75.3% | 96.162 | 96.162 | no | 0 | 3 | 7 | 14.1 | yes |
| 14 | gamelevel | `?LoadWwd@CGameLevel@@UAEHPAUWwdHeader@@@Z` | spread | 63 | 3017 | 2142 | 71.0% | 96.833 | 97.498 | no | 2 | 49 | 398 | 78.4 | yes |
| 15 | fontconfig | `?DrawCenteredText@CGameText@@QAEHPBVCString@@PAUHDC__@@PAUtagRECT@@HHHHHHH@Z` | spread | 32 | 1943 | 989 | 50.9% | 97.245 | 97.250 | no | 1 | 48 | 382 | 67.0 | yes |
| 16 | sbi_rectonly | `?StartResourceGrinderDrop@CStatusBarMgr@@QAEHHHH@Z` | spread | 23 | 1007 | 904 | 89.8% | 97.909 | 100.000 | yes | 1 | 31 | 5 | 13.7 | yes |
| 17 | ddrawshadeblit | `?BuildRle@CDDrawShadeBlit@@QAEHPAEHHHHPAUtagPALETTEENTRY@@@Z` | spread | 53 | 2253 | 1667 | 74.0% | 98.579 | 98.579 | no | 0 | 26 | 199 | 47.0 | yes |
| 18 | imagepolyclip | `?WarpTextureBlit@@YAHPAUClipVtx@@HPAVCDDSurface@@1HH@Z` | spread | 102 | 5800 | 3269 | 56.4% | 99.160 | 99.160 | no | 0 | 69 | 400 | 64.3 | yes |
| 19 | multistartdlg | `?RefreshWorldControls@CMultiStartDlg@@QAEHXZ` | spread | 42 | 2169 | 1383 | 63.8% | 99.724 | 99.724 | no | 0 | 1 | 2 | 12.1 | yes |
| 20 | triggermgrgrid | `?ApplySwitch@CTriggerMgr@@QAEHPAVCGrunt@@HH@Z` | spread | 131 | 5901 | 4154 | 70.4% | 94.697 | 94.774 | no | 2 | 51 | 400 | 69.4 | yes |

Group is the selection rule from the earlier run: named (the sprite and constructor functions the register notes discuss) or spread (evenly spaced positions in the census list). Edits in best counts the edits of the best variant. Replays include the baseline, and seconds include the capture. Source clean is yes when the checkout's tracked files were unchanged across the row's search.

## The exact variant

The exact variant is one statement move in `?StartResourceGrinderDrop@CStatusBarMgr@@QAEHHHH@Z` (unit `sbi_rectonly`). It takes the statement at line 3179, `grinderImage = *((this + 1280))`, and places it after the statement at line 3183, `itemBottom = barY + 12`. The unedited capture scores 97.909. The variant scores 100.000.

In source, the declaration `CSBI_ImageSet* grinderImage = m_grinderItemDisplay;` in `src/Gruntz/SBI_RectOnly.cpp` moves from before `i32 itemLeft` to after `i32 itemBottom = barY + 0xc;`, before `SET_RECT_COMPONENTS`. Its readers, the `if (grinderImage)` test and the `SetBounds` call inside it, both come after the new position.

The variant was checked twice. Replayed on its own, outside the batched search, it is byte-exact, and the unedited capture replays to 97.909 and is not exact. A scratch compile of the source form, written to a candidate and not into the checkout, scores 100.0 and exact against 97.909 for the original. This is a function-level verdict. It has not passed the whole-build gate, and no source change has been committed to the checkout or upstream.

## What the parser still leaves opaque

Of the 36,526 body bytes in the 20 functions, 5,670 (15.5 percent) stay opaque. An opaque byte ends the statement it sits in, so no edit touches it. Four causes account for the opaque bytes that remain:

- The vtable record `5c`: 30 occurrences in 12 units. The captures show three operand shapes (`5c 43 01 4b`, `5c 06 34 01 4b`, and `5c 43 03` followed by a load), and none fixes the operand's width, so the record stays undecoded.
- Records beginning `4f 16` and `4f 19`: 67 occurrences, all in `WarpTextureBlit` (unit `imagepolyclip`). Their operands are not identified.
- Constants whose first value byte has its high bit set and is not the `80` four-byte marker: 6 occurrences, one in each of six functions (for example `33 a0 41 f9 17` in `behindcandy`). The one-byte constants with the `a0` type prefix are decoded.
- Symbol references: 48 of 1,660 handles name no symbol in the capture's `sy` and `gl` tables. A handle with no name is treated as memory, which removes edits around it but cannot produce a wrong one. Requiring `01 00` in the bytes of a four-byte table handle would recover 2 of the 50 unresolved references in these functions. That is too few to justify rerunning the searches, so the table reader is unchanged.

## Method

Each unit is compiled through the checkout's own route (the dev shell, Wine, the unit's `cl.exe` flags, and `c2.exe` under `c2wrap`). Each body is parsed against its capture's symbol tables. A replay runs `c2.exe` alone on a variant and scores it with the function's matcher. The search runs four replays at once.

Coverage counts statement bytes only. Markers (labels, branches, switch dispatches and cases, returns, declarations, headers) and block and line markers are not statements and are not counted.

The round-trip test compiles each of the 289 units without scoring (`capture_il(..., score=False)`) and checks that `emit_body(parse(body))` equals the captured body. It takes about 14 minutes for all units. It was run in three pieces of 100, 100 and 89 units with `CONCORD_IL_UNITS`, and reported 102,524 bodies.

The decoding catalog has 103 snippet functions in `tests/fixtures/il/catalog/`, compiled with the flags of the unit `grunt`, and their decodings are in `tests/fixtures/il/catalog.json`. `tests/test_il_catalog.py` checks each decoding, checks that only the vtable record keeps opaque bytes, and, with a checkout, checks that the compiler still gives the recorded bytes.

## Limits

Legal means allowed by the edit rules in `src/concord/il.py`. The exact verdict is the matcher's byte comparison and does not depend on those rules. A rule that is too loose spends replays, and a rule that is too tight hides edits.

An exact IL variant is a statement order, not a source change. The source form above was checked by compiling it, not by `concord scores --base`.
