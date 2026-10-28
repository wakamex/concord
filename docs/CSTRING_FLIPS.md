# CString spelling search at Harvest 823b979

The search asks whether one CString header spelling can remove the comparison flips without lowering any including unit. No measured form meets that requirement. A bounded search of 53 spellings, comprising 52 authored forms and the existing self-comparison swap, found local matches, but each gain-producing form also lowered other scores. Three selected forms were then compiled in all 155 dependent units. Harvest retains the seed header and all 24 inventoried functions remain inexact, covering 46,541 target bytes.

The trial records use [objdiff](https://github.com/encounter/objdiff) fuzzy scores and Harvest matcher verdicts. Copy counts below refer to emitted copies in individual units. The final whole-build gate compares target functions and matched data against the same committed seed.

0 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0

## Measured families

Each family changes one operator. The initial screens include the callers with the attributed flips, so an existing regression can reject a spelling before compiling every dependent unit. Loop traversal, bound spelling, comparison operand order and explicit character temporaries are tested independently.

| Operator | Spellings | Initial units | Initial exact-copy gains | Initial exact-copy losses | Initial lower scores |
| --- | ---: | ---: | ---: | ---: | ---: |
| CString assignment: `assign-while` | 1 | 3 | 0 | 0 | 1 |
| CString assignment: `assign-index` | 1 | 3 | 0 | 9 | 14 |
| CString assignment: `assign-index-source` | 1 | 3 | 0 | 4 | 7 |
| CString assignment: `assign-count-first` | 1 | 3 | 0 | 5 | 10 |
| CString assignment: `assign-count-after` | 1 | 3 | 0 | 5 | 10 |
| CString assignment: `assign-destination-first` | 1 | 3 | 0 | 2 | 6 |
| CString assignment: `assign-destination-after` | 1 | 3 | 0 | 2 | 6 |
| CString assignment: `assign-two-pointers` | 1 | 3 | 2 | 2 | 6 |
| CString assignment: `assign-pointer-end` | 1 | 3 | 0 | 9 | 16 |
| CString assignment: `assign-index-before-source` | 1 | 3 | 0 | 0 | 0 |
| CString assignment: `assign-increment-source-first` | 1 | 3 | 0 | 1 | 2 |
| CString assignment: `assign-mirrored-bound` | 1 | 3 | 0 | 0 | 0 |
| CString assignment: `assign-not-equal-bound` | 1 | 3 | 0 | 6 | 11 |
| CString assignment: `assign-preincrement-bound` | 1 | 3 | 3 | 2 | 4 |
| Pointer assignment: `pointer-while` | 1 | 3 | 0 | 0 | 0 |
| Pointer assignment: `pointer-inclusive-bound` | 1 | 3 | 0 | 0 | 0 |
| Pointer assignment: `pointer-mirrored-bound` | 1 | 3 | 0 | 0 | 0 |
| Pointer assignment: `pointer-used-bound` | 1 | 3 | 0 | 10 | 16 |
| Pointer assignment: `pointer-count-bound` | 1 | 3 | 0 | 0 | 0 |
| Pointer assignment: `pointer-increment-length` | 1 | 3 | 0 | 0 | 0 |
| Pointer assignment: `pointer-destination` | 1 | 3 | 0 | 6 | 9 |
| Pointer assignment: `pointer-source` | 1 | 3 | 1 | 3 | 6 |
| Pointer assignment: `pointer-two-pointers` | 1 | 3 | 0 | 7 | 12 |
| Pointer assignment: `pointer-end` | 1 | 3 | 0 | 9 | 15 |
| Pointer assignment: `pointer-not-equal-bound` | 1 | 3 | 0 | 9 | 15 |
| Pointer assignment: `pointer-preincrement-bound` | 1 | 3 | 0 | 8 | 13 |
| Pointer assignment: `pointer-guarded-do` | 1 | 3 | 1 | 5 | 10 |
| Equality: `compare-reverse-condition` | 1 | 1 | 0 | 0 | 2 |
| Equality: `compare-reverse-equality` | 1 | 1 | 0 | 0 | 0 |
| Equality: `compare-reverse-initializers` | 1 | 1 | 0 | 0 | 0 |
| Equality: `compare-reverse-increments` | 1 | 1 | 0 | 0 | 0 |
| Equality: `compare-single-index` | 1 | 1 | 0 | 0 | 2 |
| Equality: `compare-while` | 1 | 1 | 0 | 0 | 0 |
| Equality: `compare-temporaries-left-first` | 1 | 1 | 0 | 0 | 0 |
| Equality: `compare-pointers-left-first` | 1 | 1 | 0 | 0 | 2 |
| Equality: `compare-temporaries-right-first` | 1 | 1 | 0 | 0 | 0 |
| Equality: `compare-pointers-right-first` | 1 | 1 | 0 | 0 | 2 |
| Equality: `compare-eager-left-first` | 1 | 1 | 0 | 0 | 2 |
| Equality: `compare-eager-right-first` | 1 | 1 | 0 | 0 | 2 |
| Ordering: `compare-reverse-condition` | 1 | 1 | 0 | 8 | 18 |
| Ordering: `compare-reverse-equality` | 1 | 1 | 0 | 0 | 0 |
| Ordering: `compare-reverse-initializers` | 1 | 1 | 0 | 0 | 0 |
| Ordering: `compare-reverse-increments` | 1 | 1 | 0 | 0 | 0 |
| Ordering: `compare-single-index` | 1 | 1 | 2 | 7 | 13 |
| Ordering: `compare-while` | 1 | 1 | 0 | 0 | 0 |
| Ordering: `compare-temporaries-left-first` | 1 | 1 | 0 | 7 | 15 |
| Ordering: `compare-pointers-left-first` | 1 | 1 | 0 | 11 | 21 |
| Ordering: `compare-temporaries-right-first` | 1 | 1 | 0 | 7 | 15 |
| Ordering: `compare-pointers-right-first` | 1 | 1 | 0 | 11 | 21 |
| Ordering: `compare-eager-left-first` | 1 | 1 | 0 | 8 | 18 |
| Ordering: `compare-eager-right-first` | 1 | 1 | 0 | 8 | 18 |
| Ordering: `compare-mirrored-result` | 1 | 1 | 0 | 8 | 9 |
| CString assignment self-comparison: `line 111: this == &other -> &other == this` | 1 | 2 | 0 | 0 | 0 |

## Full dependency measurements

The selected forms are the gain-producing representatives of the two assignment operators and ordering. Equality supplies no gaining form in its ZIP constructor screen. Each selected form is measured alone; combinations are outside this bounded search.

| Form | Units compiled | Exact copies gained | Exact copies lost | Lower scores | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| `assign-preincrement-bound` | 155 | 4 | 114 | 383 | 217.0 |
| `pointer-source` | 155 | 7 | 116 | 175 | 219.5 |
| `compare-single-index` | 155 | 4 | 41 | 76 | 200.4 |

The preincrement assignment loop gains the most inventoried flipped callers among these selected forms. It starts its index at -1 and increments in the while condition. The pointer-assignment form traverses the input with a separate source pointer. The ordering form uses one shared index. All three are rejected on function regressions. No source is applied, no inexact improvement is kept and no oracle run is required. Matched-data checks on rejected overlays cannot change that decision.

## Newly exact copies in rejected forms

These scores show where the local payoff occurs. A 100 score in this table also has an exact matcher verdict; it does not indicate that the rejected header change is safe elsewhere.

| Form | Unit | Symbol | objdiff before | objdiff after |
| --- | --- | --- | ---: | ---: |
| `assign-preincrement-bound` | `HarvestFull/harvest/settings/CSystemConfig.cpp` | `_ZN2ox4core10replaceAllIwEENS0_7CStringIT_EERKS4_S6_S6_` | 99.8987 | 100.0 |
| `assign-preincrement-bound` | `daisy/gui/CGUIFileOpenDialog.cpp` | `_ZN5daisy3gui18CGUIFileOpenDialog7OnEventERKN2ox5event6SEventE` | 99.86539 | 100.0 |
| `assign-preincrement-bound` | `daisy/io/CXMLReader.cpp` | `_ZN5daisy2io10CXMLReader22parseClosingXMLElementEv` | 96.83036 | 100.0 |
| `assign-preincrement-bound` | `ox/game/CConfiguration.cpp` | `_ZN2ox4game14CConfiguration10getBlockExERKNS_4core7CStringIwEERS4_S7_` | 99.78125 | 100.0 |
| `pointer-source` | `HarvestFull/harvest/game/CLuaManager.cpp` | `_ZN7harvest4game11CLuaManager10includeModERKNS0_7SLuaModE` | 99.52445 | 100.0 |
| `pointer-source` | `HarvestFull/harvest/gui/CHighscoreScreen.cpp` | `_ZN7harvest3gui16CHighscoreScreen16parseSummaryPageERKN2ox4core7CStringIcEE` | 99.896904 | 100.0 |
| `pointer-source` | `daisy/gui/CGUIWindow.cpp` | `_ZN2ox3gui10IGUILayoutD1Ev` | 0.0 | 100.0 |
| `pointer-source` | `daisy/gui/CGUIWindow.cpp` | `_ZN2ox3gui10IGUIWindowD1Ev` | 1.25 | 100.0 |
| `pointer-source` | `daisy/gui/CGUIWindow.cpp` | `_ZN2ox3gui19IGUIDetachableFrameD0Ev` | 0.0 | 100.0 |
| `pointer-source` | `daisy/gui/CGUIWindow.cpp` | `_ZN2ox3gui19IGUIDetachableFrameD1Ev` | 0.0 | 100.0 |
| `pointer-source` | `daisy/io/CFileSystem.cpp` | `_ZN5daisy2io11CFileSystem17addDirectoryAliasEPKcS3_` | 99.965034 | 100.0 |
| `compare-single-index` | `daisy/io/CFileSystem.cpp` | `_ZN5daisy2io11CFileSystem17addDirectoryAliasEPKcS3_` | 99.965034 | 100.0 |
| `compare-single-index` | `daisy/io/CFileSystem.cpp` | `_ZNSt8_Rb_treeIN2ox4core7CStringIcEESt4pairIKS3_S3_ESt10_Select1stIS6_ESt4lessIS3_ESaIS6_EE10_M_insert_EPKSt18_Rb_tree_node_baseSF_RKS6_` | 99.92481 | 100.0 |
| `compare-single-index` | `daisy/io/CZipReader.cpp` | `_ZN5daisy2io10CZipReader15scanLocalHeaderEv` | 99.264336 | 100.0 |
| `compare-single-index` | `daisy/scene/CCameraFPSSceneNode.cpp` | `_ZN5daisy5scene19CCameraFPSSceneNodeC2EPN2ox5scene10ISceneNodeEPNS3_13ISceneManagerEPNS2_3gui14ICursorControlEiffPNS_7SKeyMapEi` | 99.59514 | 100.0 |

## Comparison origins and unchanged final scores

The inventory identifies 27 comparison sites in 24 distinct target functions; 21 functions have one flip. Fifteen functions contain CString-attributed sites. The remaining origins lie in files outside the permitted header. The source location identifies the operation that was inlined, rather than proving a defect in that header.

[GNU Compiler Collection (GCC)](https://gcc.gnu.org/) 4.4 orders the comparison operands by the values it creates before the comparison. In the measured units, comparison swaps, several loop spellings and character temporaries produce identical object hashes. The forms that do move the flipped sites also affect other callers of the shared inline operation. The original source spelling that would isolate the desired changes remains unresolved.

The following scores are objdiff fuzzy scores from the normalized whole-build gate. The machine-readable inventory also retains the instruction-comparison scores reported by `concord comparison-flips`. Every target retains its baseline score and match state.

| Target function | Bytes | objdiff before / after | Comparison offset | Source origin | Target | Candidate, unchanged |
| --- | ---: | ---: | --- | --- | --- | --- |
| `ox::algo::CBase64url::encode2` at `0x5dbd00` | 5066 | 99.89426 / 99.89426 | `+0x895` | `src/ox/core/CString.h:159` | `cmp ebp, eax; jge 0x87a` | `cmp eax, ebp; jle 0x87a` |
| `ox::algo::CBase64url::encode2` at `0x5dbd00` | 5066 | 99.89426 / 99.89426 | `+0xb69` | `src/ox/core/CString.h:159` | `cmp eax, ebp; jle 0xb4e` | `cmp ebp, eax; jge 0xb4e` |
| `daisy::video::CTRTextureGouraud::drawIndexedTriangleList` at `0x5aeda0` | 4727 | 99.92647 / 99.92647 | `+0x168` | `src/daisy/video/Software/CTRTextureGouraud.cpp:160` | `cmp edx, eax; jg 0xfab` | `cmp eax, edx; jl 0xfab` |
| `daisy::io::CZipFileList::CZipFileList [C1]` at `0x537c50` | 4424 | 99.99029 / 99.99029 | `+0x7a5` | `src/ox/core/CString.h:176` | `cmp bl, dl; jne 0x7d8` | `cmp dl, bl; jne 0x7d8` |
| `daisy::io::CZipFileList::CZipFileList [C2]` at `0x536b00` | 4424 | 99.99029 / 99.99029 | `+0x7a5` | `src/ox/core/CString.h:176` | `cmp bl, dl; jne 0x7d8` | `cmp dl, bl; jne 0x7d8` |
| `ox::core::CStringFunctions::millisecondsToWide` at `0x5e08d0` | 4325 | 99.93924 / 99.93924 | `+0x577` | `src/ox/core/CString.h:159` | `cmp ebp, eax; jge 0x568` | `cmp eax, ebp; jle 0x568` |
| `daisy::video::CTRTextureGouraudWire::drawIndexedTriangleList` at `0x5b03b0` | 3779 | 99.91546 / 99.91546 | `+0x6c2` | `src/daisy/video/Software/CTRTextureGouraudWire.cpp:245` | `cmp ebp, edx; jle 0x750` | `cmp edx, ebp; jge 0x750` |
| `harvest::gui::CHighscoreScreen::parseSummaryPage` at `0x46f480` | 3109 | 99.896904 / 99.896904 | `+0x52a` | `src/ox/core/CString.h:159` | `cmp esi, r14d; jle 0x518` | `cmp r14d, esi; jge 0x518` |
| `daisy::io::CFileSystem::intern_resolveAliases` at `0x52e8c0` | 2837 | 99.985794 / 99.985794 | `+0x7ad` | `src/ox/core/CString.h:186` | `cmp al, cl; jne 0x7dc` | `cmp cl, al; jne 0x7dc` |
| `ox::core::CString<wchar_t> ox::core::replaceAll<wchar_t>` at `0x494980` | 2713 | 99.8987 / 99.8987 | `+0x45d` | `src/ox/core/CString.h:119` | `cmp r8d, ecx; jg 0x450` | `cmp ecx, r8d; jl 0x450` |
| `daisy::gui::CGUIFileOpenDialog::OnEvent` at `0x507630` | 2219 | 99.86539 / 99.86539 | `+0x625` | `src/ox/core/CString.h:119` | `cmp ecx, esi; jl 0x618` | `cmp esi, ecx; jg 0x618` |
| `harvest::settings::CSystemConfig::getAllLanguages` at `0x492960` | 1812 | 99.84375 / 99.84375 | `+0x3c5` | `src/ox/core/CString.h:119` | `cmp ecx, esi; jl 0x3b8` | `cmp esi, ecx; jg 0x3b8` |
| `ox::game::CConfiguration::getBlockEx` at `0x5e6ab0` | 1247 | 99.78125 / 99.78125 | `+0x1bd` | `src/ox/core/CString.h:119` | `cmp ecx, ebp; jl 0x1b0` | `cmp ebp, ecx; jg 0x1b0` |
| `daisy::io::CFileSystem::addDirectoryAlias` at `0x52d880` | 1042 | 99.965034 / 99.965034 | `+0x345` | `src/ox/core/CString.h:186` | `cmp al, cl; jne 0x374` | `cmp cl, al; jne 0x374` |
| `daisy::gui::CGUIButton::OnEvent` at `0x5b4dc0` | 901 | 99.92453 / 99.92453 | `+0x20a` | `src/daisy/gui/CGUIButton.cpp:155` | `cmp r12d, dword ptr [rbx + 0xb8]; je 0x2b8` | `cmp dword ptr [rbx + 0xb8], r12d; je 0x2b8` |
| `daisy::gui::CGUITextButton::draw` at `0x5b5c30` | 795 | 99.90148 / 99.90148 | `+0x17e` | `src/ox/gui/IGUIElementInline.h:46` | `cmp r12, rbp; je 0x1d9` | `cmp rbp, r12; je 0x1d9` |
| `daisy::gui::CGUITextButton::draw` at `0x5b5c30` | 795 | 99.90148 / 99.90148 | `+0x1d4` | `src/ox/gui/IGUIElementInline.h:46` | `cmp r12, rbp; jne 0x188` | `cmp rbp, r12; jne 0x188` |
| `harvest::gui::CHighscoreScreen::update` at `0x4700b0` | 565 | 99.46154 / 99.46154 | `+0x72` | `src/ox/core/CString.h:159` | `cmp r12d, esi; jge 0x60` | `cmp esi, r12d; jle 0x60` |
| `std::_Rb_tree<ox::core::CString<char>, std::pair<ox::core::CString<char> const, ox::core::CString<char> >, std::_Select1st<std::pair<ox::core::CString<char> const, ox::core::CString<char> > >, std::less<ox::core::CString<char> >, std::allocator<std::pair<ox::core::CString<char> const, ox::core::CString<char> > > >::_M_insert_` at `0x531760` | 487 | 99.92481 / 99.92481 | `+0x162` | `src/ox/core/CString.h:186` | `cmp cl, al; jne 0x18c` | `cmp al, cl; jne 0x18c` |
| `daisy::gui::CGUIContextMenu::removeItem` at `0x5be5f0` | 420 | 99.91525 / 99.91525 | `+0xc0` | `src/ox/core/CString.h:111` | `cmp rbp, r12; je 0x11a` | `cmp r12, rbp; je 0x11a` |
| `ox::core::CAes::expandKey` at `0x5de6b0` | 417 | 99.90991 / 99.90991 | `+0xf4` | `src/ox/core/CAes.cpp:142` | `cmp r12, r9; jne 0xd7` | `cmp r9, r12; jne 0xd7` |
| `harvest::states::CPlayState::buyBuildingAtPlacementPos` at `0x4a0f50` | 410 | 99.213486 / 99.213486 | `+0x3a` | `src/HarvestFull/harvest/states/CPlayState.cpp:1848` | `cmp eax, r12d; jg 0x158` | `cmp r12d, eax; jl 0x158` |
| `ox::gui::IGUILayout::sortVertically` at `0x4ff070` | 267 | 99.88095 / 99.88095 | `+0x8a` | `src/ox/gui/IGUILayoutInline.h:117` | `cmp r15, r12; je 0xd0` | `cmp r12, r15; je 0xd0` |
| `ox::gui::IGUIElement::updateAbsolutePosition` at `0x4fe810` | 233 | 98.1579 / 98.1579 | `+0x80` | `src/ox/gui/IGUIElement.h:110` | `cmp r8d, r12d; jge 0x89` | `cmp r12d, r8d; jle 0x89` |
| `ox::gui::IGUIElement::updateAbsolutePosition` at `0x4fe810` | 233 | 98.1579 / 98.1579 | `+0x89` | `src/ox/gui/IGUIElement.h:112` | `cmp edi, ebp; jge 0x90` | `cmp ebp, edi; jle 0x90` |
| `ox::game::CConfigBlock::getAttribute` at `0x5e42d0` | 176 | 99.81132 / 99.81132 | `+0x17` | `src/ox/core/CString.h:111` | `cmp rbp, rbx; je 0x69` | `cmp rbx, rbp; je 0x69` |
| `harvest::game::harvest_getSelectedBuilding` at `0x449080` | 146 | 99.652176 / 99.652176 | `+0x5c` | `src/HarvestFull/harvest/game/CLuaManager.cpp:984` | `cmp rbp, qword ptr [r12 + 8]; je 0x80` | `cmp qword ptr [r12 + 8], rbp; je 0x80` |

## Reproduction and verification

The [machine-readable evidence, gzipped](../results/cstring-flips-823b979.json.gz) records each candidate header hash, every affected score, source and object hashes, effective compiler flags, container image, configuration hashes and the original inventory. The spellings are registered in `src/concord/cstring_spellings.py`; no candidate source or object paths in that record depend on a scratch directory.

Use a Harvest checkout at 823b979 after `hv match`. The following command screens all pointer-assignment forms in a selected caller. Omit `--unit` to measure every dependent unit, and add `--variant pointer-source` to select the measured full-dependency form.

```sh
export CONCORD_JOBS=2
env -u XDG_DATA_HOME uv --no-config run --locked concord causal-trial --harvest "$HARVEST" --header src/ox/core/CString.h --authored ox/core/CStringFunctions.cpp _ZN2ox4core16CStringFunctions18millisecondsToWideEfb --unit ox/core/CStringFunctions.cpp --save pointer-trial.json
```

The same family interface is available to `concord author --variant NAME`, which verifies every dependent unit before writing and retains its existing oracle requirement for an inexact keep. None of the forms in this report is accepted.

Validation: 427 concord [unittest](https://docs.python.org/3/library/unittest.html) tests pass with 8 skips; [Ruff](https://docs.astral.sh/ruff/) and `git diff --check` pass. Two feature tests fail against concord 63544f1 because it lacks spelling families and the explicit inline-overload anchor. All 53 forms compile in their pinned-toolchain screens. The 52 authored forms also pass a C++98 compiler test exercising empty strings, prefixes, unequal characters, high-bit characters, and character-to-wide-character copies. The kept-change-only port builds and Harvest test suite are not run because no Harvest change is retained.

The search stops after one selected intervention for each assignment operator and ordering, with no equality intervention selected. No operator reaches the two-failed-selected-form limit. There is no Harvest commit; the branch remains at 823b979. No push or pull request is made.
