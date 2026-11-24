# Author trial of concord brief

Status: the first run stopped after 3 of its 38 functions to change the brief, and the 12-function pilot of the changed brief stopped after 7, once its exact-match bar could no longer be met. Neither run made a function exact; the brief found the right place each time.

Question: does `concord brief` help whoever writes a function's source reach a byte match, compared with concord's plain diff? The brief lists the sites of a function's primary causes, most causes first, with the source around each and the known idioms; on real fixes from both targets' histories its first site held the fix in about three cases of four where something structural differs ([causes.md](causes.md)). This trial measures whether an author working from it matches more functions, or with fewer compiles.

## Protocol

The sample below was drawn at random (seed 20261124) from the open functions of gruntz-decomp (checkout 8c3a113ca) and Harvest (checkout 1e5eb17) and committed before any of its functions was opened. Strata, per target: 5 functions with one source site over 500 bytes, 5 with one site of 500 bytes or less, and 6 with two or three sites over 500 bytes; on Harvest also 6 functions defined in a project header. Left out: functions where only register allocation differs, functions with 10 or more sites, functions with no placed cause, library template instances, and functions concord's sweeps made exact on 2026-11-24. Within each stratum the functions alternate between the two arms in the order drawn.

- Author: the concord lead agent, one function at a time, in the order of the table.
- Brief arm: the author starts from `concord brief` and the unit's source. Diff arm: from `concord diff` and the unit's source.
- Budget: at most 5 compiles of a candidate source per function. Before the first edit the author records, in the brief arm, whether site 1 looks like the place to change, and in both arms what it had to look up beyond its starting material.
- Outcome per function: exact (byte-identical, with no other function of the unit scoring lower), improved, or unchanged, with the compiles used and the line of the change that mattered. Candidate sources are compiled through concord; nothing is written to the targets' checkouts.
- Pass bars, set beforehand: the brief's first site within three lines of the change that mattered in at least 75 percent of the single-site functions where a change mattered; at least half of the brief arm's single-site functions exact; and the brief arm ahead of the diff arm in exact functions or in compiles per exact function.

## Sample

| # | Target | Stratum | Function | Bytes | Score | Sites | Arm |
| ---: | --- | --- | --- | ---: | ---: | ---: | --- |
| 1 | gruntz | single site, over 500 bytes | `?BlitShadeFlipV@CImage@@QAEXPAVCRenderState@@PAVCRenderBuffer@@@Z` | 629 | 99.28 | 1 | brief |
| 2 | gruntz | single site, over 500 bytes | `??0CInGameText@@QAE@PAUCGameObject@@@Z` | 533 | 97.01 | 1 | diff |
| 3 | gruntz | single site, over 500 bytes | `?AddLogic@CTileTriggerContainer@@QAEPAVCTileTriggerLogic@@W4TileCollisionKind@@W4TrigLogicId@@HHHUtagRECT@@22222HHHH@Z` | 836 | 99.24 | 1 | brief |
| 4 | gruntz | single site, over 500 bytes | `?BlitNorm@CImage@@QAEXPAVCRenderState@@PAVCRenderBuffer@@@Z` | 599 | 99.87 | 1 | diff |
| 5 | gruntz | single site, over 500 bytes | `?RotateRasterize@@YAHPAUClipVtx@@HPAVCDDSurface@@1HHHHHH@Z` | 1,226 | 96.74 | 1 | brief |
| 6 | gruntz | single site, 500 bytes or less | `?Load@CLevelPlane@@QAEHPAVCFileMemBase@@@Z` | 320 | 99.98 | 1 | brief |
| 7 | gruntz | single site, 500 bytes or less | `?BltDirtyEx@CWwdSpriteObject@@UAEXPAVCRenderSurface@@PAVCRenderBuffer@@1@Z` | 492 | 98.68 | 1 | diff |
| 8 | gruntz | single site, 500 bytes or less | `?StepScroll@CPlay@@QAEXXZ` | 79 | 88.03 | 1 | brief |
| 9 | gruntz | single site, 500 bytes or less | `?Init@CWwdGridIter@@QAEPAUWwdRegion@@PAVCWwdGrid@@UWwdRect@@H@Z` | 273 | 95.50 | 1 | diff |
| 10 | gruntz | single site, 500 bytes or less | `?InflateMainBlock@CGameLevel@@QAEPAEPAUWwdHeader@@PAEI@Z` | 210 | 88.74 | 1 | brief |
| 11 | gruntz | 2 or 3 sites, over 500 bytes | `?TrySpawnGrunt@CBattlezAiController@@QAEHH@Z` | 669 | 90.23 | 3 | brief |
| 12 | gruntz | 2 or 3 sites, over 500 bytes | `?RepathAroundBlockedTiles@CBattlezAiController@@QAEHPAVCGrunt@@@Z` | 1,222 | 97.42 | 3 | diff |
| 13 | gruntz | 2 or 3 sites, over 500 bytes | `?ChooseIdleBehavior@CBattlezAiController@@QAEHPAVCGrunt@@@Z` | 2,161 | 98.51 | 3 | brief |
| 14 | gruntz | 2 or 3 sites, over 500 bytes | `??0CSpotLight@@QAE@PAUCGameObject@@@Z` | 715 | 97.95 | 2 | diff |
| 15 | gruntz | 2 or 3 sites, over 500 bytes | `?StartWalkAnimation@CGrunt@@QAEHXZ` | 760 | 98.28 | 3 | brief |
| 16 | gruntz | 2 or 3 sites, over 500 bytes | `??0CDroppedObject@@QAE@PAUCGameObject@@@Z` | 501 | 94.72 | 2 | diff |
| 17 | harvest | single site, over 500 bytes | `harvest::entity::CDefenseTowerEntity::CDefenseTowerEntity` | 24,325 | - | 1 | brief |
| 18 | harvest | single site, over 500 bytes | `harvest::entity::CSparkMoverEntity::CSparkMoverEntity` | 1,026 | - | 1 | diff |
| 19 | harvest | single site, over 500 bytes | `daisy::io::CFileSystem::createFileList` | 1,336 | - | 1 | brief |
| 20 | harvest | single site, over 500 bytes | `daisy::gui::CGUIContextMenu::~CGUIContextMenu` | 609 | - | 1 | diff |
| 21 | harvest | single site, over 500 bytes | `harvest::states::CPlayState::initializeNewGame` | 2,466 | - | 1 | brief |
| 22 | harvest | single site, 500 bytes or less | `harvest::game::harvest_getSelectedBuildings` | 261 | - | 1 | brief |
| 23 | harvest | single site, 500 bytes or less | `daisy::video::CSpriteAnimationImageBundle::getSize` | 110 | - | 1 | diff |
| 24 | harvest | single site, 500 bytes or less | `harvest::states::CPlayState::buyBuildingAtPlacementPos` | 410 | - | 1 | brief |
| 25 | harvest | single site, 500 bytes or less | `daisy::gui::CGUIEnvironment::~CGUIEnvironment` | 495 | - | 1 | diff |
| 26 | harvest | single site, 500 bytes or less | `harvest::game::CThreatLevel::alienOccursOnPlanet` | 103 | - | 1 | brief |
| 27 | harvest | 2 or 3 sites, over 500 bytes | `harvest::states::CShuttleRaceState::updateState` | 1,618 | - | 3 | brief |
| 28 | harvest | 2 or 3 sites, over 500 bytes | `harvest::entity::CAlienEntity::updateSprite` | 1,053 | - | 3 | diff |
| 29 | harvest | 2 or 3 sites, over 500 bytes | `daisy::scene::CXFileReader::parseDataObjectAnimationSet` | 1,165 | - | 2 | brief |
| 30 | harvest | 2 or 3 sites, over 500 bytes | `harvest::entity::CAlienEntity::replaceWithLookerSprites` | 7,045 | - | 3 | diff |
| 31 | harvest | 2 or 3 sites, over 500 bytes | `daisy::gui::CGUIEnvironment::updateHoveredElement` | 2,405 | - | 2 | brief |
| 32 | harvest | 2 or 3 sites, over 500 bytes | `daisy::gui::CGUIButton::~CGUIButton` | 671 | - | 2 | diff |
| 33 | harvest | defined in a project header | `ox::gui::IGUILayout::~IGUILayout` | 341 | - | 1 | brief |
| 34 | harvest | defined in a project header | `ox::core::CString<char>::append` | 230 | - | 1 | diff |
| 35 | harvest | defined in a project header | `ox::gui::IGUIElement::setText` | 236 | - | 2 | brief |
| 36 | harvest | defined in a project header | `harvest::game::CConditionalHarvestEvent::~CConditionalHarvestEvent` | 231 | - | 2 | diff |
| 37 | harvest | defined in a project header | `ox::scene::ISceneNode::~ISceneNode` | 356 | - | 2 | brief |
| 38 | harvest | defined in a project header | `ox::gui::IGUIElement::bringToFront` | 119 | - | 2 | diff |

## Results: stopped after 3 functions

| # | Arm | Outcome | Compiles | What the author found |
| ---: | --- | --- | ---: | --- |
| 1 | brief | unchanged | 1 | Site 1 was the right lines, the `x` and `y` computations. The function is a dip: it scored 100 in an earlier build, retail computes both values in the source's own term order, and the present build reorders them, so the cause is outside the function. |
| 2 | diff | unchanged | 5 | The global register allocator holds the object pointer in eax where retail uses ecx; forcing retail's round-robin grants with `concord regtrace` reaches only 97.61, and accessor spellings, snap ordering and a named object local did not move it. |
| 3 | brief | unchanged | 2 | Site 1 was the right call: the differing stores are the inlined body of `Build`, whose order is set in a header only this unit includes. Two reorderings there scored lower. |

Every function needed the instructions around the differing rows, which the brief quoted only as the rows themselves, and four of the sixteen gruntz-decomp functions in the sample are dips, functions whose source compiled to the original before. The trial was stopped so that the brief would show both sides' instructions around each site and flag a dip; a run of the old brief would have measured a version already known to change. The diff arm also exposed that `concord diff` reported a dip as exact; it now shows its differences.

## Pilot with the changed brief

Registered before any of its functions was opened. Twelve open functions with a single source site, drawn at random (seed 20261125) from the same checkouts, three per target over 500 bytes and three at or under; left out besides the first run's exclusions are dips (functions whose banked best score is 100) and the first run's sample. The arms alternate down the table. The protocol and pass bars are the first run's, with the brief that now shows both sides' instructions around each site and flags a dip.

| # | Target | Stratum | Function | Bytes | Score | Arm |
| ---: | --- | --- | --- | ---: | ---: | --- |
| 1 | gruntz | single site, over 500 bytes | `??0CKitchenSlime@@QAE@PAUCGameObject@@@Z` | 1,016 | 99.70 | brief |
| 2 | gruntz | single site, over 500 bytes | `?HandleCombatContact@CGrunt@@QAEHHHHHH@Z` | 1,094 | 97.41 | diff |
| 3 | gruntz | single site, over 500 bytes | `?PlayMovieEntry@CGruntzMgr@@QAEHH@Z` | 792 | 98.21 | brief |
| 4 | gruntz | single site, 500 bytes or less | `?CreateSession@CMulti@@QAEHXZ` | 440 | 97.62 | diff |
| 5 | gruntz | single site, 500 bytes or less | `??0CSingleFrameMessage@@QAE@PAUCGameObject@@@Z` | 397 | 96.99 | brief |
| 6 | gruntz | single site, 500 bytes or less | `?UpdateFreezeAnimation@CGrunt@@QAEHXZ` | 481 | 97.07 | diff |
| 7 | harvest | single site, over 500 bytes | `harvest::gui::CHighscoreScreen::parseSummaryPage` | 3,109 | - | brief |
| 8 | harvest | single site, over 500 bytes | `daisy::gui::CGUIEnvironment::getFont` | 1,110 | - | diff |
| 9 | harvest | single site, over 500 bytes | `harvest::entity::CAlienEntity::updateLogic` | 2,928 | - | brief |
| 10 | harvest | single site, 500 bytes or less | `daisy::gui::CGUIDetachableFrame::lockToItemInside` | 261 | - | diff |
| 11 | harvest | single site, 500 bytes or less | `daisy::video::CVideoNull::makeColorKeyTexture` | 491 | - | brief |
| 12 | harvest | single site, 500 bytes or less | `harvest::gui::CStatisticsScreen::renderGraph` | 162 | - | diff |

### Pilot results: stopped after 7 functions

| # | Arm | Outcome | Compiles | What the author found |
| ---: | --- | --- | ---: | --- |
| 1 | brief | unchanged | 3 | Identical code up to +0x158; retail stores m_y first and loads m_object into ecx, ours stores m_x first and loads it into eax; swapping the stores, dropping the o local and a local for SetSpeedX's object each left the bytes unchanged; regtrace's later grants give only 99.79. |
| 2 | diff | unchanged | 2 | Regtrace: the switch index (xor ecx, ecx) is a round-robin grant in ours and not in retail, and the case blocks' cursor drift then alternates direction; !isAttacker and an int cast of the switch value left the bytes unchanged. |
| 3 | brief | unchanged | 0 | Retail tracks one more destructible object in the EH states while destroying player (state 8 for the member at player+0x868c, and one more destructor call), so retail's CMoviePlayer has one more member or base with a destructor; that is a layout change to include/Io/MoviePlayer.h, shared by three units, with no single candidate indicated, so no compile was spent. |
| 4 | diff | unchanged | 4 | Regtrace: from +0xf9 ours has one more round-robin grant than retail, with identical instructions before it; m_session for Session(), m_localPlayer for LocalPlayer() left the bytes unchanged, and Initialize through m_session or without the session local scored 96.31. |
| 5 | brief | unchanged | 4 | Storing Y through m_object reproduces retail's ending exactly but changes which registers hold origin and the evaluation order upstream: 86.31 with the local declared late or absent, 92.85 with it declared at the top; retail loads m_object straight into ecx for SetImageFrameByName and keeps origin.y and origin.x in edi and ebx. |
| 6 | diff | unchanged | 3 | Retail computes the tile x, fetches the grid, then computes y and compares against the grid width in memory; ours computes both before the grid and caches the width in edi. Shifts in the call kept the pixel values for the later WireTileSwitchLogic call (89.5); the direct CellFlagsAt and a shifted copy left the bytes unchanged. |
| 7 | brief | unchanged | 2 | Retail cmp esi, r14d; jle against ours cmp r14d, esi; jge, after GCC turned the counter into a pointer difference; l <= len left the bytes unchanged and l < used scored 87.08 and lowered twelve other functions. |

The pilot stopped after its seventh function. With none of the brief arm's first four exact, the bar of half its single-site functions exact could not be reached even if its last two matched, and neither arm had an exact function to compare compiles on. The first site of the brief was the right place in all four brief functions, which with the first run's two makes six of six, but in each the difference came from an allocation or scheduling decision that the edits within five compiles did not move: which register the global allocator gives a pointer, the order cl schedules two independent stores, the round-robin cursor around a switch, the operand order GCC gives a rewritten loop test, or a declaration in a shared header (an extra destructible member in `CMoviePlayer`, the widening loop in `CString`). The two arms did not differ: across both runs the diff arm also made none of its four functions exact.

What this says about concord: the brief answers where to look, and finding the place was not what kept these functions open. What they lacked was a mapping from a scheduling or allocation difference to the source form that produces it, which no tool in concord yet gives for c2's global allocator or GCC's loop rewriting.
