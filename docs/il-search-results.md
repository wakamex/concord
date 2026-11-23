# IL search results: 20 open register-allocation functions

This run applies `concord il-search` with its defaults (depth 2, budget 400 replays, beam 8, four replays at once) to 20 open functions of gruntz-decomp at 95 percent or more, whose first difference the census classifies as register allocation. Each row is one search. The search captures the unit from its unmodified source, splits the function's intermediate language (IL) into statements, replays the legal edits through the unmodified code generator, and scores each variant against retail. A variant is exact when the target's matcher proves it byte-identical.

Result: no function reached an exact variant in this run. Thirteen of the 20 searches generated no legal edit at all, six generated edits that did not raise the score, and one raised it without becoming exact. The table gives the reason: the parser covers too little of most bodies (see the section on edit counts). SwitchDown, which is not in this table, is found exact with two edits by the same command on the same checkout (see Method).

## Method

Checkout: `/code/gruntz-match-3` at commit `8c3a113ca`, with no tracked changes at the start or end of the run. Each row records whether its unit's source was clean before and after its search (column "Source clean"); all 20 were. Untracked concord temporaries in `src/Image/` and `src/Gruntz/` appeared during the run from other concord processes and were not read as sources.

Concord: `776615d`, the commit with which the batch ran. The batch is `/tmp` tooling and is not committed; its per-function JSON reports are the source of this table.

Selection: the census in `concord first-differences` was regenerated on the checkout (358 functions, 143 at 95 to 99.99 percent with cause register allocation). Eleven functions were chosen by name: the five `CGrunt::Create*Sprite` functions, including `?CreateHealthSprite@CGrunt@@QAEHXZ`, and the constructors of the macro family that `REGISTER-PATHS.md` discusses, as far as they are open at 95 percent or more here (six of them, by the census). The other nine are the candidates at evenly spaced positions in the rest of the list by score, so the sample is not only the nearly exact functions. Group "named" and "spread" in the table say which rule chose a row.

Columns: Statements is the number of statements the parser decoded. Body bytes is the size of the function's IL body. Statement bytes is how many of those bytes lie in decoded statements, and Parse coverage is their ratio. Baseline is the score of the unedited capture, replayed. Best is the highest score any variant reached, the baseline included. Replays counts c2.exe runs, the baseline included. Seconds is the wall time of the search.

Census scores and current scores differ for one function. The census reports the banked best from `config/match_baseline.tsv`. For `?ApplySwitch@CTriggerMgr@@QAEHPAVCGrunt@@HH@Z` the banked best is 99.9548, but the current build of its source scores 94.6968, below the 95 percent criterion. The row is kept, because the selection was made on the census value, and the Baseline column shows the current score.

## Results

| # | Unit | Function | Group | Statements | Body bytes | Statement bytes | Parse coverage | Baseline | Best | Exact IL | Edits in best | Replays | Seconds | Source clean |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| 1 | behindcandy | `??0CBehindCandy@@QAE@PAUCGameObject@@@Z` | named | 4 | 960 | 127 | 13.2% | 99.829 | 99.829 | no | 0 | 1 | 11.3 | yes |
| 2 | behindcandyani | `??0CBehindCandyAni@@QAE@PAUCGameObject@@@Z` | named | 5 | 1164 | 168 | 14.4% | 99.851 | 99.851 | no | 0 | 1 | 11.1 | yes |
| 3 | donothing | `??0CDoNothing@@QAE@PAUCGameObject@@@Z` | named | 3 | 938 | 95 | 10.1% | 99.829 | 99.829 | no | 0 | 1 | 11.2 | yes |
| 4 | eyecandy | `??0CEyeCandy@@QAE@PAUCGameObject@@@Z` | named | 5 | 1149 | 203 | 17.7% | 98.722 | 98.722 | no | 0 | 4 | 12.0 | yes |
| 5 | eyecandyani | `??0CEyeCandyAni@@QAE@PAUCGameObject@@@Z` | named | 6 | 1334 | 244 | 18.3% | 98.874 | 98.874 | no | 0 | 4 | 11.6 | yes |
| 6 | frontcandy | `??0CFrontCandy@@QAE@PAUCGameObject@@@Z` | named | 4 | 964 | 127 | 13.2% | 99.831 | 99.831 | no | 0 | 1 | 10.7 | yes |
| 7 | grunt | `?CreateHealthSprite@CGrunt@@QAEHXZ` | named | 1 | 865 | 33 | 3.8% | 99.655 | 99.655 | no | 0 | 1 | 11.1 | yes |
| 8 | grunt | `?CreateStaminaSprite@CGrunt@@QAEHXZ` | named | 1 | 865 | 33 | 3.8% | 99.649 | 99.649 | no | 0 | 1 | 11.2 | yes |
| 9 | grunt | `?CreateToyTimeSprite@CGrunt@@QAEHXZ` | named | 3 | 1105 | 89 | 8.1% | 99.722 | 99.722 | no | 0 | 1 | 11.0 | yes |
| 10 | grunt | `?CreateWingzTimeSprite@CGrunt@@QAEHXZ` | named | 2 | 1018 | 61 | 6.0% | 99.706 | 99.706 | no | 0 | 1 | 11.1 | yes |
| 11 | grunt | `?CreatePowerupSprite@CGrunt@@QAEHH@Z` | named | 1 | 811 | 33 | 4.1% | 99.630 | 99.630 | no | 0 | 1 | 11.1 | yes |
| 12 | statichazard | `??0CStaticHazard@@QAE@PAUCGameObject@@@Z` | spread | 9 | 2147 | 333 | 15.5% | 95.251 | 95.251 | no | 0 | 1 | 10.8 | yes |
| 13 | sbi_sidetab | `?UpdateSampleIcon@CSBI_SideTab@@QAEHXZ` | spread | 6 | 1116 | 198 | 17.7% | 96.162 | 96.162 | no | 0 | 1 | 10.9 | yes |
| 14 | gamelevel | `?LoadWwd@CGameLevel@@UAEHPAUWwdHeader@@@Z` | spread | 19 | 3017 | 598 | 19.8% | 96.833 | 97.493 | no | 2 | 83 | 24.0 | yes |
| 15 | fontconfig | `?DrawCenteredText@CGameText@@QAEHPBVCString@@PAUHDC__@@PAUtagRECT@@HHHHHHH@Z` | spread | 1 | 1943 | 16 | 0.8% | 97.245 | 97.245 | no | 0 | 1 | 10.6 | yes |
| 16 | sbi_rectonly | `?StartResourceGrinderDrop@CStatusBarMgr@@QAEHHHH@Z` | spread | 6 | 1007 | 200 | 19.9% | 97.909 | 97.909 | no | 0 | 7 | 13.3 | yes |
| 17 | ddrawshadeblit | `?BuildRle@CDDrawShadeBlit@@QAEHPAEHHHHPAUtagPALETTEENTRY@@@Z` | spread | 14 | 2253 | 285 | 12.6% | 98.579 | 98.579 | no | 0 | 9 | 12.6 | yes |
| 18 | imagepolyclip | `?WarpTextureBlit@@YAHPAUClipVtx@@HPAVCDDSurface@@1HH@Z` | spread | 33 | 5800 | 960 | 16.6% | 99.160 | 99.160 | no | 0 | 58 | 18.0 | yes |
| 19 | multistartdlg | `?RefreshWorldControls@CMultiStartDlg@@QAEHXZ` | spread | 2 | 2169 | 77 | 3.6% | 99.724 | 99.724 | no | 0 | 1 | 10.8 | yes |
| 20 | triggermgrgrid | `?ApplySwitch@CTriggerMgr@@QAEHPAVCGrunt@@HH@Z` | spread | 19 | 5901 | 555 | 9.4% | 94.697 | 94.697 | no | 0 | 16 | 13.7 | yes |

Exact IL found: 0 of 20.

## Edits that improved a score

Only one function had a variant better than its baseline. `?LoadWwd@CGameLevel@@UAEHPAUWwdHeader@@@Z` (row 14) went from 96.833 to 97.493 with two moves, one for each of the two statements that read the header fields. Neither edit is exact, and its search used 83 replays.

- Row 14 (`gamelevel`, `?LoadWwd@CGameLevel@@UAEHPAUWwdHeader@@@Z`), move at lines 208, 210: move `startX = *((source + 720))` (line 208) after `mp = *((this + 92))` (line 210)
- Row 14 (`gamelevel`, `?LoadWwd@CGameLevel@@UAEHPAUWwdHeader@@@Z`), move at lines 209, 210: move `startY = *((source + 724))` (line 209) after `mp = *((this + 92))` (line 210)

## Why most functions had few or no legal edits

The search can only move, swap or replace what the parser decodes as a statement. Parse coverage, the share of the body in decoded statements, is between 0.8 and 19.9 percent for all 20 functions. SwitchDown, which the parser covers at 68.9 percent, is the one function where the command found its fix. Four things account for the rest:

1. Prologue and epilogue. The function entry (`4f 1f 80 00 08 a0 00 ...`), the parameter list and the exit sequence (`54 ...`) are the largest undecoded runs in most bodies. They are opaque by design and no edit reaches them.

2. Undecoded operators. The operators `16`, `03`, `29` and `2c` are followed by `a7 01 00`-style operands in the constructors and in `CreateHealthSprite`. They are probably declaration or initialisation forms, but the parser has not decoded them. Each one ends the statement it is in.

3. Call argument lists. In the bodies examined, `55` appears between the arguments of a call, and the parser does not decode it. A call with more than one argument therefore breaks its statement, which is why `CreateHealthSprite` decodes to a single 33-byte statement. The argument swap that the register notes describe for that function cannot be reached until these lists are decoded.

4. Block markers. `53` and `54` open and close scopes. The parser does not decode them as operators, so a block marker ends the statement before it. Only runs made of block markers and line markers are kept as scope bytes.

The undecoded bytes that begin a bad token, counted across the 20 bodies, are `00` and `01` in prologues, `53` and `54` markers, `43` and `41` inside argument lists and declarations, and `55`, `29` and `2c`. Decoding `55` and the operators above is the next step. This document then needs regenerating.

A search with no legal edit is a bound on what the decoded statements allow. It is not evidence that no front-end output matches retail.

## Limits

Introducing a local for a repeated expression is not generated. c2.exe rejected each `sy` symbol record added by hand in the attempts made so far, with internal compiler errors. The edit kinds generated are statement moves, operand and argument swaps, and replacement of a local by its single assignment's value.
