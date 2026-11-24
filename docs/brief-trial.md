# Author trial of concord brief

Status: stopped after 3 of the 38 functions, to change the brief; see the results below. A new run needs a new registered sample.

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
