# Caller constructs behind Harvest comparison flips

Caller rewrites were tested to remove Harvest's comparison-only mismatches without lowering another function. No correction was retained. The final screens measured 478 overlays across 25 functions covering 52,322 target bytes, using the pinned [GNU Compiler Collection (GCC)](https://gcc.gnu.org/) 4.4.3 compiler and Harvest's matcher. [GIMPLE](https://gcc.gnu.org/onlinedocs/gccint/GIMPLE.html) dumps resolve the single static assignment (SSA) operand pairs at 29 of 32 comparison sites. Three pointer comparisons retain multiple possible SSA pairs.

0 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0

The original 24-function census covers 46,541 bytes and 27 comparison sites. The additional `CBuildableItems::loadCreativeBuildingList` contributes 5,781 bytes and five sites. The source remains at Harvest `0d482f0`; the one-point upstream submission bar is unchanged. The machine evidence (1 MB compressed, not committed) records each trace, candidate source and object hashes, scores, compiler image and flags, input and dump hashes, and the normalized whole-build gate.

## Function outcomes

Scores below are [objdiff](https://github.com/encounter/objdiff) fuzzy percentages from the normalized gate, before and after the screens. An overlay is one candidate compile. Search counts belong to functions; the two ZIP constructor variants share the same source screen. All functions retain their original source.

| Target function | Address | Target bytes | Measured caller rewrites and disposition | objdiff before / after |
| --- | --- | ---: | --- | ---: |
| `harvest::entity::CBuildableItems::loadCreativeBuildingList` | `0x418a00` | 5,781 | 64 overlays; no retained correction | 99.70882 / 99.70882 |
| `ox::algo::CBase64url::encode2` | `0x5dbd00` | 5,066 | 56 overlays; no retained correction | 99.89426 / 99.89426 |
| `daisy::video::CTRTextureGouraud::drawIndexedTriangleList` | `0x5aeda0` | 4,727 | 64 overlays; no retained correction | 99.92647 / 99.92647 |
| `daisy::io::CZipFileList::CZipFileList [C2]` | `0x536b00` | 4,424 | 21 forms measured with C1; no retained correction | 99.99029 / 99.99029 |
| `daisy::io::CZipFileList::CZipFileList [C1]` | `0x537c50` | 4,424 | 21 overlays; no retained correction | 99.99029 / 99.99029 |
| `ox::core::CStringFunctions::millisecondsToWide` | `0x5e08d0` | 4,325 | 28 overlays; no retained correction | 99.93924 / 99.93924 |
| `daisy::video::CTRTextureGouraudWire::drawIndexedTriangleList` | `0x5b03b0` | 3,779 | 64 overlays; no retained correction | 99.91546 / 99.91546 |
| `harvest::gui::CHighscoreScreen::parseSummaryPage` | `0x46f480` | 3,109 | 53 overlays; no retained correction | 99.896904 / 99.896904 |
| `daisy::io::CFileSystem::intern_resolveAliases` | `0x52e8c0` | 2,837 | 26 overlays; no retained correction | 99.985794 / 99.985794 |
| `ox::core::replaceAll<wchar_t>` | `0x494980` | 2,713 | Definition in a header; no rewrite proposed in the unit source | 99.8987 / 99.8987 |
| `daisy::gui::CGUIFileOpenDialog::OnEvent` | `0x507630` | 2,219 | 14 overlays; no retained correction | 99.86539 / 99.86539 |
| `harvest::settings::CSystemConfig::getAllLanguages` | `0x492960` | 1,812 | 4 overlays; no retained correction | 99.84375 / 99.84375 |
| `ox::game::CConfiguration::getBlockEx` | `0x5e6ab0` | 1,247 | 6 overlays; no retained correction | 99.78125 / 99.78125 |
| `daisy::io::CFileSystem::addDirectoryAlias` | `0x52d880` | 1,042 | No rewrite at the traced constructs in the available vocabulary | 99.965034 / 99.965034 |
| `daisy::gui::CGUIButton::OnEvent` | `0x5b4dc0` | 901 | 4 overlays; no retained correction | 99.92453 / 99.92453 |
| `daisy::gui::CGUITextButton::draw` | `0x5b5c30` | 795 | 26 overlays; no retained correction | 99.90148 / 99.90148 |
| `harvest::gui::CHighscoreScreen::update` | `0x4700b0` | 565 | 2 overlays; no retained correction | 99.46154 / 99.46154 |
| `std::map<CString<char>, CString<char>> insertion` | `0x531760` | 487 | Definition in a header; no rewrite proposed in the unit source | 99.92481 / 99.92481 |
| `daisy::gui::CGUIContextMenu::removeItem` | `0x5be5f0` | 420 | 9 overlays; no retained correction | 99.91525 / 99.91525 |
| `ox::core::CAes::expandKey` | `0x5de6b0` | 417 | 26 overlays; no retained correction | 99.90991 / 99.90991 |
| `harvest::states::CPlayState::buyBuildingAtPlacementPos` | `0x4a0f50` | 410 | No rewrite at the traced constructs in the available vocabulary | 99.213486 / 99.213486 |
| `ox::gui::IGUILayout::sortVertically` | `0x4ff070` | 267 | Definition in a header; no rewrite proposed in the unit source | 99.88095 / 99.88095 |
| `ox::gui::IGUIElement::updateAbsolutePosition` | `0x4fe810` | 233 | Definition in a header; no rewrite proposed in the unit source | 98.1579 / 98.1579 |
| `ox::game::CConfigBlock::getAttribute` | `0x5e42d0` | 176 | 4 overlays; no retained correction | 99.81132 / 99.81132 |
| `harvest::game::harvest_getSelectedBuilding` | `0x449080` | 146 | 7 overlays; no retained correction | 99.652176 / 99.652176 |

The branch-sense rewrite at `CConfigBlock.cpp:65` makes `CConfigBlock::getAttribute` exact from 99.81132%, but both `CConfigBlock(const wchar_t*)` constructor variants fall from 100% to 98.295456%. It is rejected by the unit gate. Naming the pointer load in the unbraced `else` preserves the original bytes and leaves the function inexact. No candidate is selected for a whole-build source intervention, and no oracle run is needed because no inexact gain reaches the acceptance path.

## Comparison traces

SSA names distinguish separate values of a declaration. A PHI statement selects a value from predecessor blocks. The pass names retain GCC's spelling: `vrp1` is value-range propagation and `ch` copies loop headers. The table names the compared versions, their first observed creation passes, prior owners when a version was recycled, and the source constructs feeding them. A function-entry value has a default SSA definition rather than a producing statement.

`millisecondsToWide` supplies the clearest recycling chain. The `L".0"` copy at `CStringFunctions.cpp:39` compares `lD.27351_697` with `lenD.27349_929`. `vrp1` creates `len_929` as a loop-exit PHI. Version 929 previously belonged to `D.27535_929`, whose input chain reaches the append-size calculation for `result.append(hundredths)` at line 42. Numeric operand order reverses at `vrp1`, and the condition mirrors in `dce1`. Compiler processing order can therefore connect a later source call to an earlier call's compare. The measured rewrites of those constructs produce no acceptable correction.

| Target function and offset | Target comparison and jump | Candidate comparison and jump | Candidate SSA creation and source constructs |
| --- | --- | --- | --- |
| `harvest::entity::CBuildableItems::loadCreativeBuildingList` `+0x1cd` | `cmp ebp, eax; jge 0x1b0` | `cmp eax, ebp; jle 0x1b0` | lD.49103_421 (ivopts); reused D.49139_421; lenD.49100_784 (vrp1); reused oldArrayD.49425_784; constructs: src/HarvestFull/harvest/entity/CBuildableItems.cpp:165, src/HarvestFull/harvest/entity/CBuildableItems.cpp:230, src/HarvestFull/harvest/entity/CBuildableItems.cpp:166, src/HarvestFull/harvest/entity/CBuildableItems.cpp:170, src/HarvestFull/harvest/entity/CBuildableItems.cpp:169 |
| `harvest::entity::CBuildableItems::loadCreativeBuildingList` `+0x224` | `cmp eax, ebp; jl 0x208` | `cmp ebp, eax; jg 0x208` | D.46042_173 (ch); reused D.39953_173; iD.46038_194 (phiprop); constructs: src/HarvestFull/harvest/entity/CBuildableItems.cpp:166, src/HarvestFull/harvest/entity/CBuildableItems.cpp:199 |
| `harvest::entity::CBuildableItems::loadCreativeBuildingList` `+0x2fd` | `cmp ebx, eax; jge 0x2e0` | `cmp eax, ebx; jle 0x2e0` | lD.49125_508 (ivopts); reused D.49223_508; lenD.49122_786 (vrp1); reused D.49432_786; constructs: src/HarvestFull/harvest/entity/CBuildableItems.cpp:169, src/HarvestFull/harvest/entity/CBuildableItems.cpp:170, src/HarvestFull/harvest/entity/CBuildableItems.cpp:182, src/HarvestFull/harvest/entity/CBuildableItems.cpp:230 |
| `harvest::entity::CBuildableItems::loadCreativeBuildingList` `+0xa7c` | `cmp ecx, ebx; jle 0xa60` | `cmp ebx, ecx; jge 0xa60` | lenD.49269_104 (vrp1); reused D.39900_104; lD.49272_604 (phiprop); constructs: src/HarvestFull/harvest/entity/CBuildableItems.cpp:199, src/HarvestFull/harvest/entity/CBuildableItems.cpp:200 |
| `harvest::entity::CBuildableItems::loadCreativeBuildingList` `+0xb1a` | `cmp edx, ebx; jle 0xb08` | `cmp ebx, edx; jge 0xb08` | lenD.49291_275 (vrp1); reused D.46181_275; lD.49293_633 (phiprop); constructs: src/HarvestFull/harvest/entity/CBuildableItems.cpp:200 |
| `ox::algo::CBase64url::encode2` `+0x895` | `cmp ebp, eax; jge 0x87a` | `cmp eax, ebp; jle 0x87a` | lD.25812_576 (ivopts); reused D.25946_576; lenD.25809_859 (vrp1); reused D.26181_859; constructs: src/ox/algo/CBase64url.cpp:87, src/ox/algo/CBase64url.cpp:96, src/ox/algo/CBase64url.cpp:89 |
| `ox::algo::CBase64url::encode2` `+0xb69` | `cmp eax, ebp; jle 0xb4e` | `cmp ebp, eax; jge 0xb4e` | lenD.25989_846 (vrp1); reused D.26163_846; lD.25992_1745 (ivopts); constructs: src/ox/algo/CBase64url.cpp:93 |
| `daisy::video::CTRTextureGouraud::drawIndexedTriangleList` `+0x168` | `cmp edx, eax; jg 0xfab` | `cmp eax, edx; jl 0xfab` | prephitmp.314D.26737_698 (pre); reused D.25791_698; prephitmp.314D.26737_994 (pre); reused colorD.26344_994; constructs: src/daisy/video/Software/CTRTextureGouraud.cpp:348, src/daisy/video/Software/CTRTextureGouraud.cpp:244, src/daisy/video/Software/CTRTextureGouraud.cpp:199, src/daisy/video/Software/CTRTextureGouraud.cpp:200, src/daisy/video/Software/CTRTextureGouraud.cpp:401, src/daisy/video/Software/CTRTextureGouraud.cpp:402, src/daisy/video/Software/CTRTextureGouraud.cpp:213, src/daisy/video/Software/CTRTextureGouraud.cpp:233, src/daisy/video/Software/CTRTextureGouraud.cpp:404, src/daisy/video/Software/CTRTextureGouraud.cpp:234, src/daisy/video/Software/CTRTextureGouraud.cpp:214, src/daisy/video/Software/CTRTextureGouraud.cpp:405 |
| `daisy::io::CZipFileList::CZipFileList [C2]` `+0x7a5` | `cmp bl, dl; jne 0x7d8` | `cmp dl, bl; jne 0x7d8` | D.29283_324 (ch); reused iftmp.89D.29287_324; D.29279_582 (ch); reused iD.29243_582; constructs: src/daisy/io/CZipFileList.cpp:37, src/daisy/io/CZipFileList.cpp:22, src/daisy/io/CZipFileList.cpp:40, src/daisy/io/CZipFileList.cpp:24, src/daisy/io/CZipFileList.cpp:36 |
| `daisy::io::CZipFileList::CZipFileList [C1]` `+0x7a5` | `cmp bl, dl; jne 0x7d8` | `cmp dl, bl; jne 0x7d8` | D.29183_324 (ch); reused iftmp.89D.29187_324; D.29179_582 (ch); reused iD.29143_582; constructs: src/daisy/io/CZipFileList.cpp:37, src/daisy/io/CZipFileList.cpp:22, src/daisy/io/CZipFileList.cpp:40, src/daisy/io/CZipFileList.cpp:24, src/daisy/io/CZipFileList.cpp:36 |
| `ox::core::CStringFunctions::millisecondsToWide` `+0x577` | `cmp ebp, eax; jge 0x568` | `cmp eax, ebp; jle 0x568` | lD.27351_697 (phiprop); lenD.27349_929 (vrp1); reused D.27535_929; constructs: src/ox/core/CStringFunctions.cpp:39, src/ox/core/CStringFunctions.cpp:42, src/ox/core/CStringFunctions.cpp:19 |
| `daisy::video::CTRTextureGouraudWire::drawIndexedTriangleList` `+0x6c2` | `cmp ebp, edx; jle 0x750` | `cmp edx, ebp; jge 0x750` | D.25664_644 (phiprop); rightZValueD.25357_969 (ch); reused colorD.25977_969; constructs: src/daisy/video/Software/CTRTextureGouraudWire.cpp:168, src/daisy/video/Software/CTRTextureGouraudWire.cpp:125, src/daisy/video/Software/CTRTextureGouraudWire.cpp:318, src/daisy/video/Software/CTRTextureGouraudWire.cpp:249, src/daisy/video/Software/CTRTextureGouraudWire.cpp:293, src/daisy/video/Software/CTRTextureGouraudWire.cpp:170, src/daisy/video/Software/CTRTextureGouraudWire.cpp:127, src/daisy/video/Software/CTRTextureGouraudWire.cpp:99, src/daisy/video/Software/CTRTextureGouraudWire.cpp:97, src/daisy/video/Software/CTRTextureGouraudWire.cpp:98, src/daisy/video/Software/CTRTextureGouraudWire.cpp:315, src/daisy/video/Software/CTRTextureGouraudWire.cpp:87, src/daisy/video/Software/CTRTextureGouraudWire.cpp:85, src/daisy/video/Software/CTRTextureGouraudWire.cpp:86, src/daisy/video/Software/CTRTextureGouraudWire.cpp:161, src/daisy/video/Software/CTRTextureGouraudWire.cpp:141, src/daisy/video/Software/CTRTextureGouraudWire.cpp:296 |
| `harvest::gui::CHighscoreScreen::parseSummaryPage` `+0x52a` | `cmp esi, r14d; jle 0x518` | `cmp r14d, esi; jge 0x518` | lenD.53752_71 (vrp1); reused D.40504_71; lD.53754_111 (ivopts); reused D.47179_111; constructs: src/HarvestFull/harvest/gui/CHighscoreScreen.cpp:228, src/HarvestFull/harvest/gui/CHighscoreScreen.cpp:225, src/HarvestFull/harvest/gui/CHighscoreScreen.cpp:226 |
| `daisy::io::CFileSystem::intern_resolveAliases` `+0x7ad` | `cmp al, cl; jne 0x7dc` | `cmp cl, al; jne 0x7dc` | prephitmp.3888D.48554_202 (copyrename4); reused D.48184_202; D.48180_221 (ch); reused D.48094_221; constructs: src/daisy/io/CFileSystem.cpp:91, src/daisy/io/CFileSystem.cpp:81, src/daisy/io/CFileSystem.cpp:90 |
| `ox::core::replaceAll<wchar_t>` `+0x45d` | `cmp r8d, ecx; jg 0x450` | `cmp ecx, r8d; jl 0x450` | iD.42257_382 (phiprop); D.42267_461 (ch); reused D.42363_461; constructs: src/ox/core/CString.h:119, src/ox/core/CString.h:119 |
| `daisy::gui::CGUIFileOpenDialog::OnEvent` `+0x625` | `cmp ecx, esi; jl 0x618` | `cmp esi, ecx; jg 0x618` | D.43139_23 (ch); reused D.34268_23; iD.43129_422 (phiprop); constructs: src/daisy/gui/CGUIFileOpenDialog.cpp:134 |
| `harvest::settings::CSystemConfig::getAllLanguages` `+0x3c5` | `cmp ecx, esi; jl 0x3b8` | `cmp esi, ecx; jg 0x3b8` | D.44072_103 (ch); reused D.40649_103; iD.44062_238 (phiprop); constructs: src/HarvestFull/harvest/settings/CSystemConfig.cpp:348 |
| `ox::game::CConfiguration::getBlockEx` `+0x1bd` | `cmp ecx, ebp; jl 0x1b0` | `cmp ebp, ecx; jg 0x1b0` | D.34000_167 (ch); reused D.33984_167; iD.33990_185 (phiprop); constructs: src/ox/game/CConfiguration.cpp:300 |
| `daisy::io::CFileSystem::addDirectoryAlias` `+0x345` | `cmp al, cl; jne 0x374` | `cmp cl, al; jne 0x374` | prephitmp.3132D.46873_534 (copyrename4); reused D.46711_534; D.46707_538 (ch); constructs: src/daisy/io/CFileSystem.cpp:245 |
| `daisy::gui::CGUIButton::OnEvent` `+0x20a` | `cmp r12d, dword ptr [rbx + 0xb8]; je 0x2b8` | `cmp dword ptr [rbx + 0xb8], r12d; je 0x2b8` | D.32976_127 (phiprop); oldStateD.32936_106 (phiprop); constructs: src/daisy/gui/CGUIButton.cpp:155, src/daisy/gui/CGUIButton.cpp:142 |
| `daisy::gui::CGUITextButton::draw` `+0x17e` | `cmp r12, rbp; je 0x1d9` | `cmp rbp, r12; je 0x1d9` | Unresolved: multiple SSA comparisons use this declaration pair; BB 19: if (it$_M_nodeD.39373_141 != D.39372_647); BB 25: if (it$_M_nodeD.39373_168 != D.39372_647); source: src/ox/gui/IGUIElementInline.h:46 |
| `daisy::gui::CGUITextButton::draw` `+0x1d4` | `cmp r12, rbp; jne 0x188` | `cmp rbp, r12; jne 0x188` | Unresolved: multiple SSA comparisons use this declaration pair; BB 19: if (it$_M_nodeD.39373_141 != D.39372_647); BB 25: if (it$_M_nodeD.39373_168 != D.39372_647); source: src/ox/gui/IGUIElementInline.h:46 |
| `harvest::gui::CHighscoreScreen::update` `+0x72` | `cmp r12d, esi; jge 0x60` | `cmp esi, r12d; jle 0x60` | lD.54216_43 (ivopts); reused D.47259_43; lenD.54213_87 (vrp1); reused D.54232_87; constructs: src/HarvestFull/harvest/gui/CHighscoreScreen.cpp:174, src/HarvestFull/harvest/gui/CHighscoreScreen.cpp:176 |
| `std::map<CString<char>, CString<char>> insertion` `+0x162` | `cmp cl, al; jne 0x18c` | `cmp al, cl; jne 0x18c` | prephitmp.2250D.45072_122 (copyrename4); reused D.44981_122; prephitmp.2254D.45076_248 (copyrename4); reused D.44985_248; constructs: src/ox/core/CString.h:185, src/ox/core/CString.h:185 |
| `daisy::gui::CGUIContextMenu::removeItem` `+0xc0` | `cmp rbp, r12; je 0x11a` | `cmp r12, rbp; je 0x11a` | __firstD.38161_110 (ivopts); reused D.41347_110; __resultD.38160_239 (ch); constructs: src/daisy/gui/CGUIContextMenu.cpp:125 |
| `ox::core::CAes::expandKey` `+0xf4` | `cmp r12, r9; jne 0xd7` | `cmp r9, r12; jne 0xd7` | ivtmp.1160D.25142_121 (copyrename4); reused ivtmp.1147D.25106_121; D.25132_320 (ivopts) |
| `harvest::states::CPlayState::buyBuildingAtPlacementPos` `+0x3a` | `cmp eax, r12d; jg 0x158` | `cmp r12d, eax; jl 0x158` | D.64983_2 (phiprop); D.64986_6 (phiprop); constructs: src/HarvestFull/harvest/states/CPlayState.cpp:1848, src/HarvestFull/harvest/states/CPlayState.cpp:1848 |
| `ox::gui::IGUILayout::sortVertically` `+0x8a` | `cmp r15, r12; je 0xd0` | `cmp r12, r15; je 0xd0` | Unresolved: multiple SSA comparisons use this declaration pair; BB 2: if (it$_M_nodeD.37091_62 != D.37082_195); BB 8: if (it$_M_nodeD.37091_90 != D.37082_195); source: src/ox/gui/IGUILayoutInline.h:117 |
| `ox::gui::IGUIElement::updateAbsolutePosition` `+0x80` | `cmp r8d, r12d; jge 0x89` | `cmp r12d, r8d; jle 0x89` | parentAbsoluteClip$LowerRightCorner$XD.35847_21 (copyrename3); reused D.12202_21; parentAbsoluteClip$LowerRightCorner$XD.35847_47 (phiprop); reused D.35819_47; constructs: src/ox/gui/IGUIElement.h:100 |
| `ox::gui::IGUIElement::updateAbsolutePosition` `+0x89` | `cmp edi, ebp; jge 0x90` | `cmp ebp, edi; jle 0x90` | parentAbsoluteClip$LowerRightCorner$YD.35846_24 (copyrename3); reused D.12204_24; parentAbsoluteClip$LowerRightCorner$YD.35846_46 (phiprop); reused D.35838_46; constructs: src/ox/gui/IGUIElement.h:101 |
| `ox::game::CConfigBlock::getAttribute` `+0x17` | `cmp rbp, rbx; je 0x69` | `cmp rbx, rbp; je 0x69` | valueD.28701_5(D) (function-entry); D.28713_6 (phiprop); constructs: src/ox/game/CConfigBlock.cpp:68 |
| `harvest::game::harvest_getSelectedBuilding` `+0x5c` | `cmp rbp, qword ptr [r12 + 8]; je 0x80` | `cmp qword ptr [r12 + 8], rbp; je 0x80` | D.71004_44 (phiprop); it$_M_currentD.71026_50 (phiprop); reused D.71018_50; constructs: /usr/include/c++/4.4/bits/stl_iterator.h:686, /usr/include/c++/4.4/bits/stl_iterator.h:708 |

The three unresolved sites are the two comparisons in `CGUITextButton::draw` and the comparison in `IGUILayout::sortVertically`. Repeated SSA uses map to the same output declarations, and the retained block context does not identify one pair uniquely. Their alternative SSA conditions and definitions remain in the structured evidence. Header-defined functions are diagnosed, while this caller search currently requires the named body in the unit source.

The target executable supplies the required operand order, but has no compiler dumps. These traces establish the candidate's creation and recycling history. None of the measured source changes proves a safe correction for the target's different decision. The requested complete source-cause attribution and a newly matched Harvest function remain unresolved.

## Authored arithmetic probes

The caller arithmetic still contains multiplication and addition in the SSA dumps before the copy loop. Two equivalent remainder spellings test whether changing those earlier value-producing constructs changes the comparison decision. Both lower the function score and are discarded as overlays. This hypothesis stops after its two failed interventions; no oracle is run for a worsening candidate.

| Earlier construct | Authored replacement | objdiff before | objdiff after | Disposition |
| --- | --- | ---: | ---: | --- |
| `milliseconds -= hours * 3600000` at line 18 | `milliseconds %= 3600000` | 99.93924 | 99.886284 | Rejected |
| `milliseconds -= wholeSeconds * 1000` at line 35 | `milliseconds %= 1000` | 99.93924 | 97.00868 | Rejected |

These bodies are generated with the existing authored `Edit` and `candidates` interface and measured with `evaluate_overlays`, keeping the checkout source unchanged. Their source and object hashes are retained in the evidence.

## Acceptance and verification

0 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0

- Concord: 450 [unittest](https://docs.python.org/3/library/unittest.html) tests pass with 8 skips; [Ruff](https://docs.astral.sh/ruff/) and `git diff --check` pass.
- Harvest baseline: `hv match` completed before the trials, returning the expected inexact-build status. All diagnostic functions preserve their canonical bytes and relocation offsets.
- No Harvest source, configuration or learned symbol change is retained. No authored line is submitted and no Harvest commit is created.
- Port `harvest` and `census` builds and Harvest toolchain tests are conditional on a retained change; that condition is false.
- The normalized gate compares 5,240 functions and 1,611 matched data runs. Both captures use the same configuration and source revision.
- Each search is bounded at 64 overlays. The two selected arithmetic interventions in millisecondsToWide fail the unit score gate and reach its stop limit. No whole-build source intervention is selected.
- No push or pull request is made.

Regression coverage includes real pinned compiler dumps for PHI creation and recycling, partition and temporary-expression maps, equal and relational comparisons sharing values, coalesced versions of a declaration, default SSA values, anonymous base members, and rejected ambiguous block contexts. Source line lookup uses byte offsets because repeated parser coordinate reads above line 256 corrupt nodes on Python 3.13; a subprocess regression reproduces the native crash before the fix.

## Reproduction

Use a Harvest checkout at `0d482f0` after `hv match`. Set `HARVEST` to that checkout. Collect fresh diagnostic dumps when reusing an older collection without declaration identifiers, source lines and expansion details.

```sh
export CONCORD_JOBS=2
env -u XDG_DATA_HOME uv --no-config run --locked concord causal --harvest "$HARVEST" --unit ox/core/CStringFunctions.cpp _ZN2ox4core16CStringFunctions18millisecondsToWideEfb --search --budget 64 --save milliseconds.json
env -u XDG_DATA_HOME uv --no-config run --locked concord scores --harvest "$HARVEST" --base 0d482f0 --save gate.json
```

The capability keeps schema `concord.causal.v1` and adds `tree_ssa` evidence to comparison findings. It verifies compiler inputs, dump hashes, canonical function bytes and relocation offsets before attributing a decision. `--search` measures overlays; a selected candidate is finished into ordinary C++ and checked through `author`, including the existing oracle requirement for an inexact improvement. `--apply` writes only after that acceptance path. The whole-build score gate remains required before retaining a source change.
