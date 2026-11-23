# gruntz-decomp flag audit

Question: was any unit of [gruntz-decomp](https://github.com/sushi-shi/gruntz-decomp), a matching reconstruction of Gruntz (1999), compiled with [Microsoft Visual C++ 5.0](https://learn.microsoft.com/en-us/cpp/build/reference/compiler-options) (MSVC) options other than the ones its configuration gives it? A function compiled with other options cannot be matched by changing its source alone, so the question comes before source work on a unit.

Result: no unit shows evidence of other options. The audit compiled each of the 145 units with a function below 100 in the checkout's report with its configured flags and with 15 variants, and compared every function with the configured compile. Twenty-six unit and variant pairs gain an exact function or raise an open one. Twenty-two of them also lose exact functions. The other four change a single function and lose nothing.

The audit used the checkout's own driver and its [objdiff](https://github.com/encounter/objdiff) scoring, so each variant is measured by the same rule as the build.

## Results

- 26 unit and variant pairs gain an exact function or raise an open function by 1 point or more, across 18 units. An exact function is one whose code and relocations match the retail object, under the checkout's exact rule.
- 22 of the 26 lose at least one exact function. A unit compiled with other options would be expected to gain several exact functions without losing any, so these pairs are trades, not evidence.
- Four pairs lose no exact function: `statusbartabbuilders` with `no-gx`, `sbi_image` with `no-gx`, and `wwdgameobjectrender` with `gr` each gain one exact function, and `lightfxrender` with `no-gr` raises one open function. Each changes one function's score and no other function's score in its unit.
- No variant gains more than two exact functions in any unit. The largest gain is `triggermgr` with `ox`, which gains two and loses eight.
- 127 of the 145 units have no variant that gains or raises any function.
- No variant failed to compile in any unit.
- All 145 unedited replays of the captured intermediate language reproduce the configured compile. In every unit, the variant that replaces the code generator's -G5 with -G6 gives the same result as the variant that adds /G6.

## Conclusion

No evidence shows a unit compiled with other options. The largest gain in exact functions is two, from `ox` in `triggermgr`, which loses eight. Removing /GX, where the configured flags have it, changes at least one function's score in each of the 96 units whose report lists exception-handling (EH) funclets, and it costs exact functions in 81 of them, so the flag matters where those units use it. The four pairs that gain or raise without loss are discussed below. None is a whole-unit signal, and none establishes a different option set. The audit covers 15 variants per unit, so it can miss an option outside them (see Limits).

## Four single-function signals

Each signal changes one function's score in its unit. The other functions in the same unit keep their scores.

- `statusbartabbuilders`, `no-gx`: `CSBI_GruntMachine::Render` rises from 99.54 to 100. Without /GX, the compile matches retail. Its [objdump](https://sourceware.org/binutils/docs/binutils/objdump.html) disassembly differs from the configured compile in three pairs of loads that come in the opposite order, so this is an instruction-order difference within one function.
- `sbi_image`, `no-gx`: `CSBI_Image::Render` rises from 74.04 to 100. Both compiles make the same calls, but compute two operands in a different register order, and one jump target moves by one byte.
- `wwdgameobjectrender`, `gr`: `CWwdDotObject::Render` rises from 99.84 to 100. Its disassembly and relocations are identical in both compiles, so the score change comes from something outside the function's code as objdump prints it. I did not trace the cause further.
- `lightfxrender`, `no-gr`: `CMinimap::DrawBorderRaw` rises from 96.79 to 98.31, an open function raised but not made exact. Its disassembly and relocations are identical in both compiles, so its cause is also outside the code as printed and was not traced.

Two of the four are instruction-order differences, and two have a score change that the printed code does not explain. None shows a structural difference in the unit's code, and none shows that the unit was compiled with other options.

## Units where no variant helps

actionoptionsmenubar, animationregistry, aniplayer, areamgr, attract, attractstate, battlezmapconfig, battlezrepath, battlezretarget, battlezspawncheck, battlezunitstep, behindcandy, behindcandyani, bitarray, blowfish, bootystateactivate, brickz, brickzcellflags, brickzload, chatboxowner, cheatmgr, cimage, creditsstate, ddrawptrcollections, ddrawshadeblit, ddrawsubmgr, ddrawsubmgrpages, ddrawsurfacemgr, ddrawsurfacepair, ddsurface, demo, dialogs, donothing, droppedobject, exittrigger, eyecandy, eyecandyani, faderflat, faderlight, fadermesh, fadershape, fadersine, feccrypt, fileimage, font, fontconfig, fortressflag, frontcandy, gamelevel, goowellmgr, gruntarrivalupdate, gruntassetloaders, gruntbricklayerstep, gruntchargestep, gruntcombat, gruntcreationpoint, gruntdefensealt, gruntdefenselean, gruntdefensestep, gruntdiggerstep, gruntentrancemove, gruntgoosuckerstep, grunthealthsprite, gruntphasestep, gruntpickupload, gruntpowerupsprite, gruntreticlescan, gruntscantarget, gruntseektarget, gruntspawnconfig, gruntstatestep, gruntvoice, gruntwanderstep, gruntzmgrcmd, gruntzwnd, imagepolyclip, imagepool, imageset1, imageset2, imageset3g, kitchenslime, levelrezpath, lightfx, loadgamemenu, mapmgr, menutree, movinglogic, multi, multistartdlg, netcmdslot, netmgr, netsessionmgr, pathhazard, projectile, rollingball, sbi_imageset, sbi_imagesetani, sbi_menuitem, sbi_rectonly, sbi_sidetab, sbi_statztabarrow, sbi_statztabgruntbar, sbi_wellgoo, secretteleportertrigger, simpleanimation, singleframemessage, spotlight, spriteloaders, statemgrbz, statereleaseresources, statichazard, terraintileloader, tilelogicpump, tilescan, tileswitchlogic, tiletriggercontainer, toobspikez, triggermgrgrid, triggermgrhittest, typekeycoll, videoconfig, warlord, worldsoundset, wormholeacts, wwdfactoryobject, wwdgameobject, wwdgrid

## Variants

Each variant changes the configured flags of its unit, except `c2-g6`, which replays the captured intermediate language.

| Variant | Change |
| --- | --- |
| `ox` | /O2 replaced by /Ox (maximum optimization) |
| `o1` | /O2 replaced by /O1 (minimize size) |
| `ob0` | /Ob0 added (no inline expansion) |
| `ob2` | /Ob2 added (inline any suitable function) |
| `oy-` | /Oy- added (keep frame pointers) |
| `op` | /Op added (improve floating-point consistency) |
| `o2-no-g` | /O2 replaced by /Oi /Ot /Oy /Ob2, which leaves out global optimization |
| `gy` | /Gy added (function-level linking) |
| `gf` | /Gf added (pool duplicate strings) |
| `g4` | /G4 added (486 code) |
| `g6` | /G6 added (Pentium Pro code) |
| `gb` | /GB added (blended Pentium and Pentium Pro code) |
| `c2-g6` | The captured intermediate language replayed with -G5 replaced by -G6 in the flags of c2.exe, MSVC's code generator |
| `no-gx`, `gx` | /GX removed, or added where the unit lacks it (exception handling) |
| `no-gr`, `gr` | /GR removed, or added where the unit lacks it (run-time type information) |

The variant `/Og-` was not run. `o2-no-g` tests the same question, global optimization off, by replacing /O2 with the options that /O2 combines, less /Og.

## Results table

Each row is a unit and variant whose compile gains an exact function or raises an open function by at least 1 point against the configured compile. Rows with 0 in the Exact lost column lose nothing. A variant that fails to compile would appear as an error and is absent here; none did.

| Unit | Variant | Exact gained | Exact lost | Open functions raised by 1 point or more (includes functions that became exact) | Largest score gain |
| --- | --- | ---: | ---: | ---: | --- |
| butemgr | ob0 | 0 | 36 | 1 | `?TagList@CButeMgr@@QAE_NXZ` 75.58 to 91.14 |
| gamelevelmove | no-gx | 1 | 4 | 0 | `?BroadPhase@CGameLevel@@QAEHPAUCGameObject@@HH@Z` 99.82 to 100.00 |
| grunt | o1 | 0 | 12 | 1 | `?MoveTo@CGrunt@@QAEHHHHHHH@Z` 32.52 to 35.00 |
| gruntentrancearrival | ox | 0 | 8 | 1 | `?RectSegProbe@CGrunt@@QAEHPAUtagRECT@@PAUtagPOINT@@1@Z` 92.20 to 97.29 |
| gruntentrancearrival | no-gx | 0 | 1 | 1 | `?RectSegProbe@CGrunt@@QAEHPAUtagRECT@@PAUtagPOINT@@1@Z` 92.20 to 97.29 |
| gruntsteps | oy- | 0 | 11 | 1 | `?ClaimSwitchTile@CGrunt@@QAEHXZ` 79.24 to 84.46 |
| gruntzmgr | ox | 1 | 28 | 1 | `?ScanObjectsInRect@CGruntzMgr@@QAEHHHPAUtagRECT@@HP6AHPAUCGameObject@@H@ZH@Z` 91.95 to 100.00 |
| ingameicon | oy- | 0 | 17 | 1 | `?UpdateAvailablePickup@CInGameIcon@@QAEHXZ` 93.72 to 99.83 |
| levelplane | no-gx | 1 | 4 | 0 | `?Load@CLevelPlane@@QAEHPAVCFileMemBase@@@Z` 99.98 to 100.00 |
| lightfxrender | no-gr | 0 | 0 | 1 | `?DrawBorderRaw@CMinimap@@QAEXPAUtagRECT@@PADH@Z` 96.79 to 98.31 |
| play | ox | 1 | 19 | 1 | `?StepScroll@CPlay@@QAEXXZ` 88.03 to 100.00 |
| rezcoll | no-gx | 2 | 5 | 0 | `?GetLast@CBaseHash@@QAEPAVCBaseHashItem@@XZ` 99.47 to 100.00 |
| sbi_image | g6 | 0 | 2 | 1 | `?Render@CSBI_Image@@UAEHXZ` 74.04 to 78.00 |
| sbi_image | c2-g6 | 0 | 2 | 1 | `?Render@CSBI_Image@@UAEHXZ` 74.04 to 78.00 |
| sbi_image | no-gx | 1 | 0 | 1 | `?Render@CSBI_Image@@UAEHXZ` 74.04 to 100.00 |
| shadetablecache | ob0 | 0 | 14 | 1 | `?HsvShiftTable@CShadeTableCache@@QAEPAUCShadeTable@@PAUtagPALETTEENTRY@@HHHH@Z` 87.90 to 90.51 |
| statusbartabbuilders | no-gx | 1 | 0 | 0 | `?Render@CSBI_GruntMachine@@UAEHXZ` 99.54 to 100.00 |
| triggermgr | ox | 2 | 8 | 2 | `?HandleTargetSelection@CTriggerMgr@@QAEHHHHHHW4TargetSelectionKind@@H@Z` 90.75 to 97.36 |
| triggermgr | ob2 | 1 | 4 | 0 | `?OpenActionOptionsMenu@CTriggerMgr@@QAEHHHHH@Z` 99.85 to 100.00 |
| triggermgr | oy- | 1 | 27 | 0 | `?ToggleToyTargeting@CTriggerMgr@@QAEHXZ` 99.73 to 100.00 |
| triggermgr | no-gx | 1 | 3 | 0 | `?ToggleToyTargeting@CTriggerMgr@@QAEHXZ` 99.73 to 100.00 |
| triggermgr | no-gr | 1 | 1 | 0 | `?OpenActionOptionsMenu@CTriggerMgr@@QAEHHHHH@Z` 99.85 to 100.00 |
| wormhole | oy- | 0 | 20 | 1 | `??0CGruntPuddle@@QAE@PAUCGameObject@@@Z` 56.17 to 89.01 |
| wormhole | no-gx | 0 | 7 | 1 | `??0CGruntPuddle@@QAE@PAUCGameObject@@@Z` 56.17 to 79.34 |
| wwdgameobjectrender | gr | 1 | 0 | 0 | `?Render@CWwdDotObject@@UAEXPAVCRenderBuffer@@@Z` 99.84 to 100.00 |
| wwdobjmgr | ob0 | 0 | 37 | 1 | `?PruneOrphans@CDDrawChildGroup@@QAEHXZ` 60.72 to 62.15 |

## Method

- Units: every unit whose report entry has a function below 100 with exception funclets excluded: 145 units, holding 442 functions below 100 in the report.
- Compiles: each unit's unchanged source was compiled by the checkout's driver with the configured flags, then with each variant's flags. The driver scores each compile with the checkout's objdiff comparison and its exact rule, which needs an unrounded 100, the retail size and equal relocation streams. Four compiles ran at once. The run made 2,175 compiles of unit sources, 145 captures of intermediate language, and 290 replays, in about twelve minutes of wall time over two runs.
- Comparison: a function is gained when it is exact in a variant and was not exact in the configured compile, and lost in the reverse case. A function is raised when it was open in the configured compile and its score rises by at least 1 point in the variant.
- Replays: each unit's captured intermediate language was copied and its c2.exe option replaced, then replayed. An unedited copy was replayed as a control, and it reproduces the configured compile in all 145 units.
- Checkout: no tracked file in the checkout changed. Each compile is written under the checkout's build/concord directory, except the candidate source, which the driver places beside its unit's source under a temporary name, as every gruntz command does, and removes after the compile. Each unit's work directories were removed when the unit finished.
- Reproduce: `CONCORD_JOBS=4 uv --no-config run --locked concord flag-audit --gruntz GRUNTZ_CHECKOUT --all-open --save FILE`, with `--budget SECONDS` to stop between units. A save file resumes an interrupted run. The report measured was `build/objdiff/compare-new/report.json` with SHA-256 `3e4029befddd95774a5a2e075acf36ccc514dc6ffd62988a6d5f7329de326b77`.
- Evidence: [results/flag-audit-gruntz.json.gz](../results/flag-audit-gruntz.json.gz) holds, for each unit, the configured compile's scores, each variant's scores with its gained, lost and raised functions, and the replay control.

## Limits

- Option coverage: the audit tests 15 variants per unit. An option outside them, such as /Gs, /Zp or the runtime library choice, was not tested.
- Report and configured compile: 45 of the 145 units differ between the report's exact set and the configured compile's. The report counts a function as exact through its banked best score in `config/match_baseline.tsv`, the best score the project has recorded for it, so 88 functions the report counts exact score below 100 in the configured compile. The exact rule also rejects 7 functions that objdiff scores at 100, and the driver does not score 11 data symbols the report lists. The configured compile's scores match the report for 2,976 of the 2,984 functions it scores. The other 8 differ by up to 0.3 points, and the checkout's normal compile path gives the same numbers as this audit, so the difference lies in the report.
- EH funclets as evidence: the units whose report lists EH funclets are a proxy for units built with /GX, not proof. `worldsoundset` lists none, yet its destructors match only with /GX, so the proxy does not decide which units have /GX.
