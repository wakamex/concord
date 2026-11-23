# Open functions below their recorded high

gruntz-decomp's goal is a byte match of the game, and its baseline keeps, for each function, the best score the function's present source has reached (`best_pct`) and the all-time high any version reached (`hist_pct`). At revision 8c3a113ca, 107 open functions score at least 0.5 points below that high. This run asks which of those highs a build can still reproduce, what happened to each function's score, and which headers and inline helpers changed around them. The reference high is the last commit where a function's current score reached its high, and a drop is a later commit that lowered that score. A function's tokens are the words of its code, with comments and spacing removed.

Result: 72 of the 107 highs are confirmed, which means the unit compiled at the commit of the high reproduces it. They cover 73,031 bytes. The other 35 are stale, covering 66,032 bytes: their compile does not reproduce the high, or the function is not emitted under its current name, or the high never appears as a current score in the baseline history.

The largest single drop is PR [#208](https://github.com/sushi-shi/gruntz-decomp/pull/208), which lowers 8 confirmed functions, 37,291 bytes in all. For 53 of the 72 confirmed functions, the function's own tokens are the same at the high and now, so something it compiles against accounts for the lower score. Which header or helper accounts for it is shown by reach, which is a name match and not a measurement.

## Confirmed functions

Sorted by bytes. Each row gives the score now (from the last build), the high and the commit where the high was last reached, and each later drop with its commit, PR, and whether the function's own tokens changed in that commit. The changed-headers column is a count: every header of the unit's include closure whose contents differ between the high and now. The full lists are in [results/lost-ground.json](../results/lost-ground.json). The helper column lists the changed inline functions and macros the function reaches.

| Function | Bytes | Now | High | High at | Drops | Own source, high to now | Changed headers | Changed helpers reached |
|---|---:|---:|---:|---|---|---|---:|---|
| `?TmDeflectStep@@YG?AUGruntDirectionCell@@PAVCGrunt@@HHHHW4GruntDirection@@PAUCoord@@PAH@Z` | 21,031 | 91.0959 | 99.4577 | f2ef469e3 | a1a95c87c PR [#208](https://github.com/sushi-shi/gruntz-decomp/pull/208): 99.4577 to 91.0959, own text same | changed | 50 | CMapMgr::CellFlagsAt (Brickz.h:322, changed); GetArrivalFlags (Grunt.h:336, added) |
| `CDDrawShadeBlit::BlitShadedMirrored` | 4,736 | 76.9843 | 77.6386 | 60db36416 | 4ab11188a no PR: 77.6386 to 77.4272, own text changed; 3d4629280 no PR: 77.4272 to 77.2930, own text same; c8a670137 no PR: 77.3582 to 77.2309, own text same; 9acc08cec no PR: 77.2309 to 76.4962, own text same; 520d5b06b PR [#88](https://github.com/sushi-shi/gruntz-decomp/pull/88): 76.7440 to 76.4555, own text same; 0335a5365 PR [#79](https://github.com/sushi-shi/gruntz-decomp/pull/79): 76.4555 to 76.4266, own text same; f0a1dd0aa PR [#118](https://github.com/sushi-shi/gruntz-decomp/pull/118): 76.6537 to 76.6311, own text same; dbf27726a PR [#124](https://github.com/sushi-shi/gruntz-decomp/pull/124): 76.6311 to 76.4962, own text same; 5e14e7ca7 PR [#181](https://github.com/sushi-shi/gruntz-decomp/pull/181): 77.5866 to 77.3231, own text same; 5903207d6 PR [#189](https://github.com/sushi-shi/gruntz-decomp/pull/189): 77.3231 to 77.1142, own text same; a1a95c87c PR [#208](https://github.com/sushi-shi/gruntz-decomp/pull/208): 77.3231 to 76.7898, own text same | changed | 22 | CDDSurface::Unlock (DDSurface.h:264, added); CShadeTable::Lut16 (ShadeTableCache.h:39, added); Lut16 (ShadeTableCache.h:32, removed); 2 more |
| `CBattlezAiController::StepDefenderUnit` | 3,011 | 84.8084 | 85.3099 | e5af24fef | 18de38373 PR [#374](https://github.com/sushi-shi/gruntz-decomp/pull/374): 85.3099 to 84.8084, own text same | same | 2 | CGrunt::ResetToSeek (GruntMovementInline.h:44, changed) |
| `CFaderLight::RenderFrame` | 2,412 | 93.9789 | 95.6062 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 95.6062 to 93.3553, own text same; 60758b98f PR [#205](https://github.com/sushi-shi/gruntz-decomp/pull/205): 93.3553 to 93.3367, own text same | changed | 12 | CFaderLight::ComputeSpan (FaderLightInline.h:13, changed); FADER_CLAMPW (FaderLightInline.h:11, added) |
| `CMinimap::BuildSpacePalette` | 2,342 | 99.4785 | 100.0000 | 484246ea3 | 0e590d118 PR [#359](https://github.com/sushi-shi/gruntz-decomp/pull/359): 100.0000 to 99.4785, own text same | same | 20 | none |
| `CMinimap::BuildHoneyPalette` | 2,240 | 97.8720 | 100.0000 | 88b8bb349 | bd9216dab PR [#180](https://github.com/sushi-shi/gruntz-decomp/pull/180): 100.0000 to 97.8419, own text same; a1a95c87c PR [#208](https://github.com/sushi-shi/gruntz-decomp/pull/208): 99.9337 to 97.7440, own text same; a159bd8b3 PR [#239](https://github.com/sushi-shi/gruntz-decomp/pull/239): 97.9081 to 97.8419, own text same; 0e590d118 PR [#359](https://github.com/sushi-shi/gruntz-decomp/pull/359): 99.9337 to 97.8720, own text same | same | 60 | none |
| `CMinimap::BuildHighRollerzPalette` | 2,148 | 97.5597 | 99.9209 | 88b8bb349 | bd9216dab PR [#180](https://github.com/sushi-shi/gruntz-decomp/pull/180): 99.9209 to 97.4558, own text same; a1a95c87c PR [#208](https://github.com/sushi-shi/gruntz-decomp/pull/208): 99.8868 to 97.5178, own text same; a159bd8b3 PR [#239](https://github.com/sushi-shi/gruntz-decomp/pull/239): 97.5628 to 97.5287, own text same; 0e590d118 PR [#359](https://github.com/sushi-shi/gruntz-decomp/pull/359): 99.8868 to 97.5597, own text same | same | 60 | none |
| `CMinimap::BuildRockyRoadzPalette` | 2,143 | 97.1630 | 100.0000 | 202dbf7b7 | e17fa3f05 PR [#76](https://github.com/sushi-shi/gruntz-decomp/pull/76): 100.0000 to 99.9689, own text changed; 520d5b06b PR [#88](https://github.com/sushi-shi/gruntz-decomp/pull/88): 99.9689 to 97.7811, own text same; 0335a5365 PR [#79](https://github.com/sushi-shi/gruntz-decomp/pull/79): 97.7811 to 97.1320, own text changed; ba0a98074 PR [#131](https://github.com/sushi-shi/gruntz-decomp/pull/131): 99.3820 to 96.5823, own text same; bd9216dab PR [#180](https://github.com/sushi-shi/gruntz-decomp/pull/180): 99.3820 to 97.0978, own text same; a1a95c87c PR [#208](https://github.com/sushi-shi/gruntz-decomp/pull/208): 99.3820 to 97.2531, own text same; 99a6206b8 PR [#209](https://github.com/sushi-shi/gruntz-decomp/pull/209): 97.2531 to 97.1320, own text same; 0e590d118 PR [#359](https://github.com/sushi-shi/gruntz-decomp/pull/359): 99.3820 to 97.1630, own text same | changed | 102 | PackRgb16 (PixelShift.h:59, added); SetTileColor (Minimap.h:102, added) |
| `CMinimap::BuildTropiczPalette` | 2,133 | 97.2304 | 100.0000 | 202dbf7b7 | e17fa3f05 PR [#76](https://github.com/sushi-shi/gruntz-decomp/pull/76): 100.0000 to 99.9686, own text changed; 520d5b06b PR [#88](https://github.com/sushi-shi/gruntz-decomp/pull/88): 99.9686 to 97.8542, own text same; 0335a5365 PR [#79](https://github.com/sushi-shi/gruntz-decomp/pull/79): 97.8542 to 97.1991, own text changed; ba0a98074 PR [#131](https://github.com/sushi-shi/gruntz-decomp/pull/131): 99.3762 to 96.5752, own text same; bd9216dab PR [#180](https://github.com/sushi-shi/gruntz-decomp/pull/180): 99.3762 to 96.6787, own text same; a1a95c87c PR [#208](https://github.com/sushi-shi/gruntz-decomp/pull/208): 99.3762 to 97.1959, own text same; 0e590d118 PR [#359](https://github.com/sushi-shi/gruntz-decomp/pull/359): 99.3762 to 97.2304, own text same | changed | 102 | PackRgb16 (PixelShift.h:59, added); SetTileColor (Minimap.h:102, added) |
| `CMinimap::BuildGruntziclezPalette` | 2,130 | 96.7965 | 100.0000 | 88b8bb349 | bd9216dab PR [#180](https://github.com/sushi-shi/gruntz-decomp/pull/180): 100.0000 to 97.3221, own text same; a1a95c87c PR [#208](https://github.com/sushi-shi/gruntz-decomp/pull/208): 99.3077 to 97.3862, own text same; 99a6206b8 PR [#209](https://github.com/sushi-shi/gruntz-decomp/pull/209): 97.3862 to 96.7965, own text same; a159bd8b3 PR [#239](https://github.com/sushi-shi/gruntz-decomp/pull/239): 97.3926 to 96.7965, own text same; 0e590d118 PR [#359](https://github.com/sushi-shi/gruntz-decomp/pull/359): 99.3077 to 96.7965, own text same | same | 60 | none |
| `CMinimap::BuildHighOnSweetzPalette` | 2,085 | 96.0569 | 100.0000 | 484246ea3 | 0e590d118 PR [#359](https://github.com/sushi-shi/gruntz-decomp/pull/359): 100.0000 to 96.0569, own text same | same | 20 | none |
| `FontRenderer::DrawWrapped` | 2,028 | 99.4530 | 100.0000 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 100.0000 to 99.4530, own text same | changed | 8 | none |
| `CGrunt::StepGauntletGruntBehavior` | 1,984 | 96.4261 | 97.2870 | 818b3facd | f024de143 PR [#348](https://github.com/sushi-shi/gruntz-decomp/pull/348): 97.2870 to 96.4049, own text changed | changed | 37 | CGrunt::ResetToSeek (GruntMovementInline.h:44, changed); IsGruntAtSavedScreenPos (GruntMovementInline.h:11, changed); MARK_NEAREST_ENEMY_AT_TARGET (GruntMovementMacros.h:64, changed) |
| `FontRenderer::DrawGlyphRun` | 1,516 | 95.3125 | 99.9583 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 99.9583 to 91.6505, own text same; 9cf3a7be1 PR [#202](https://github.com/sushi-shi/gruntz-decomp/pull/202): 94.3796 to 92.5741, own text same | changed | 8 | UnpackPixel16 (PixelShift.h:46, added); BlendPixel16 (FontBlendInline.h:14, changed); RunRightEdge (FontBlendInline.h:6, changed) |
| `CTriggerMgr::ApplySwitch` | 1,500 | 94.6968 | 99.9548 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 99.9548 to 94.6968, own text same | same | 8 | none |
| `?Blowfish_decipher@@YAXPAI0@Z` | 1,166 | 61.4969 | 100.0000 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 100.0000 to 61.4969, own text same | same | 1 | none |
| `CGrunt::HandleCombatContact` | 1,094 | 97.4074 | 99.1168 | ab16c2506 | 0971662a8 PR [#350](https://github.com/sushi-shi/gruntz-decomp/pull/350): 99.1168 to 97.4074, own text same | same | 49 | none |
| `CDDSurface::ShadeBlt` | 1,087 | 72.5925 | 73.4295 | e23dd3b46 | 87ffe90ed no PR: 73.4295 to 72.4232, own text same; 0335a5365 PR [#79](https://github.com/sushi-shi/gruntz-decomp/pull/79): 72.5925 to 72.4765, own text same; 94940c567 PR [#119](https://github.com/sushi-shi/gruntz-decomp/pull/119): 72.4765 to 71.9498, own text same; 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 72.5925 to 71.9498, own text same; 60758b98f PR [#205](https://github.com/sushi-shi/gruntz-decomp/pull/205): 71.9498 to 71.8809, own text same | changed | 21 | Clut16 (ClutTable.h:38, added); CDDSurface::Unlock (DDSurface.h:264, added); CDib::GetBytes (Image.h:55, added); 3 more |
| `CButeMgr::Save` | 1,010 | 97.6341 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 97.6341, own text same | same | 1 | zSymTab::traverse (PTree.h:173, changed) |
| `CDDrawShadeBlit::ConvertRowDouble` | 916 | 75.8262 | 77.0518 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 77.0518 to 72.0671, own text same; 60758b98f PR [#205](https://github.com/sushi-shi/gruntz-decomp/pull/205): 73.8415 to 72.0671, own text same | changed | 9 | none |
| `CDDSurface::Blit816` | 847 | 85.9185 | 92.5259 | 520d5b06b | 0335a5365 PR [#79](https://github.com/sushi-shi/gruntz-decomp/pull/79): 92.5259 to 85.9185, own text same | changed | 16 | CDDSurface::Unlock (DDSurface.h:264, added); FindNearestColor (PaletteColorInline.h:12, changed); PaletteColorDistanceSquared (PaletteColorInline.h:7, added); 1 more |
| `CSpotLight::Tick` | 792 | 83.2984 | 84.2097 | e5af24fef | 18de38373 PR [#374](https://github.com/sushi-shi/gruntz-decomp/pull/374): 84.2097 to 83.2984, own text same | same | 2 | none |
| `CShadeTableCache::HsvShiftTable` | 746 | 87.9037 | 99.5413 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 99.5413 to 87.9037, own text same | same | 6 | none |
| `CFaderFlat::RenderFrame` | 742 | 90.9745 | 93.6582 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 93.6582 to 90.9745, own text same | changed | 8 | none |
| `CTriggerMgr::HandleTargetSelection` | 739 | 90.7510 | 99.8538 | 1ef48b059 | 935026998 PR [#355](https://github.com/sushi-shi/gruntz-decomp/pull/355): 99.8538 to 90.7510, own text same | same | 47 | none |
| `CDDSurface::ShadeRect` | 730 | 78.1409 | 84.7773 | f2ef469e3 | a1a95c87c PR [#208](https://github.com/sushi-shi/gruntz-decomp/pull/208): 84.7773 to 78.1409, own text same | changed | 3 | none |
| `CAreaMgr::LoadObjectAnimResources` | 609 | 99.0805 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 99.0805, own text same | same | 3 | CResourceNameList::FirstEntry (SpawnListInline.h:6, changed); MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `CAreaMgr::LoadObjectSoundResources` | 609 | 99.0805 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 99.0805, own text same | same | 3 | CResourceNameList::FirstEntry (SpawnListInline.h:6, changed); MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `CShadeTableCache::GammaTable` | 520 | 90.2743 | 100.0000 | 26a268642 | dbf27726a PR [#124](https://github.com/sushi-shi/gruntz-decomp/pull/124): 100.0000 to 95.0571, own text changed; 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 95.0571 to 90.2743, own text same | changed | 10 | CShadeTable::GetData (ShadeTableCache.h:35, added) |
| `CGrunt::RectSegProbe` | 517 | 92.1980 | 97.3366 | 7d4bd55b9 | e5af24fef PR [#373](https://github.com/sushi-shi/gruntz-decomp/pull/373): 97.3366 to 92.1980, own text same | same | 3 | none |
| `CGameLevel::ResolveMoveDown` | 401 | 98.5915 | 100.0000 | e065e925a | 531afe13a PR [#321](https://github.com/sushi-shi/gruntz-decomp/pull/321): 100.0000 to 98.5915, own text same | same | 20 | PROBE_TILE (GameLevel.h:22, changed) |
| `CGameObject::SerializeDispatch` | 400 | 92.2069 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 92.2069, own text same | same | 3 | MapLookup (MapTyped.h:10, changed) |
| `CShadeTableCache::GreyTable` | 387 | 83.6742 | 97.5379 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 97.5379 to 81.0985, own text same | changed | 6 | none |
| `CTriggerMgr::FindNearestEnemy` | 317 | 92.7670 | 93.2718 | 520d5b06b | 0335a5365 PR [#79](https://github.com/sushi-shi/gruntz-decomp/pull/79): 93.2718 to 93.2524, own text same; 37dd6d3e8 PR [#141](https://github.com/sushi-shi/gruntz-decomp/pull/141): 93.2524 to 93.0777, own text same; 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 93.0971 to 93.0777, own text same; 478f53683 PR [#206](https://github.com/sushi-shi/gruntz-decomp/pull/206): 93.0971 to 93.0777, own text same; 2c3ee6287 PR [#215](https://github.com/sushi-shi/gruntz-decomp/pull/215): 93.0971 to 93.0777, own text same; 7f28760b6 PR [#231](https://github.com/sushi-shi/gruntz-decomp/pull/231): 93.0971 to 92.7670, own text same; a159bd8b3 PR [#239](https://github.com/sushi-shi/gruntz-decomp/pull/239): 92.7670 to 92.7476, own text same; c1db6b2d4 PR [#273](https://github.com/sushi-shi/gruntz-decomp/pull/273): 92.7670 to 92.7476, own text same; 04064a344 PR [#313](https://github.com/sushi-shi/gruntz-decomp/pull/313): 92.7670 to 92.7476, own text same; 083585f55 PR [#334](https://github.com/sushi-shi/gruntz-decomp/pull/334): 92.7670 to 92.7476, own text same; 2a9dbd693 PR [#362](https://github.com/sushi-shi/gruntz-decomp/pull/362): 92.7670 to 92.7476, own text same | changed | 79 | GetDefenderRadius (Grunt.h:365, added); GetPlayerIndex (Grunt.h:202, added); GetPowerupType (Grunt.h:184, added); 10 more |
| `CActionOptionsMenuBar::Refresh` | 310 | 93.2432 | 100.0000 | e2cea447c | 2dede8a6d PR [#341](https://github.com/sushi-shi/gruntz-decomp/pull/341): 100.0000 to 93.2432, own text same | same | 42 | CDDrawWorker::GetAt (DDrawWorker.h:67, removed); CImageSet::GetAt (DDrawWorker.h:68, added) |
| `AnimationRegistry::RemoveWithPrefix` | 248 | 97.7180 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 97.7180, own text same | same | 2 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `SoundCueRegistry::RemoveWithPrefix` | 248 | 97.7180 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 97.7180, own text same | same | 3 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `SoundCueRegistry::GetNextCueAfter` | 246 | 95.0933 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 95.0933, own text same | same | 3 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `SoundCueRegistry::SumAudioBytes` | 246 | 83.8023 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 83.8023, own text same | same | 3 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `CMinimap::DrawBorderRaw` | 226 | 96.7931 | 98.4483 | 818b3facd | f024de143 PR [#348](https://github.com/sushi-shi/gruntz-decomp/pull/348): 98.4483 to 96.9310, own text same; cd99d138f PR [#353](https://github.com/sushi-shi/gruntz-decomp/pull/353): 96.9310 to 96.7931, own text same | same | 39 | none |
| `AnimationRegistry::HasWithPrefix` | 220 | 98.5714 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 98.5714, own text same | same | 2 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `SoundCueRegistry::HasWithPrefix` | 220 | 98.5714 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 98.5714, own text same | same | 3 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `AnimationRegistry::FindAnimationKey` | 212 | 98.3857 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 98.3857, own text same | same | 2 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `SoundCueRegistry::FindCueKey` | 212 | 98.3857 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 98.3857, own text same | same | 3 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `CActionOptionsMenuBar::UpdateHoverState` | 207 | 94.0741 | 100.0000 | ee03fe170 | 19ad7cb24 PR [#332](https://github.com/sushi-shi/gruntz-decomp/pull/332): 100.0000 to 94.0741, own text same | same | 45 | none |
| `CShadeTableCache::CompareLuma` | 204 | 95.6825 | 100.0000 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 100.0000 to 95.6825, own text same | same | 6 | none |
| `CDDrawChildGroup::SerializeObjects` | 192 | 85.1392 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 85.1392, own text same | same | 2 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `CButeMgr::TagList` | 186 | 75.5797 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 75.5797, own text same | same | 1 | zSymTab::traverse (PTree.h:173, changed) |
| `CGruntzMgr::ScanObjectsInRect` | 186 | 91.9480 | 100.0000 | ee03fe170 | 19ad7cb24 PR [#332](https://github.com/sushi-shi/gruntz-decomp/pull/332): 100.0000 to 91.9480, own text same | same | 72 | CState::World (State.h:163, added) |
| `CGruntHealthSprite::HealthUpdate` | 180 | 95.0725 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 95.0725, own text same | same | 5 | none |
| `AnimationRegistry::RemoveAnimation` | 178 | 98.1818 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 98.1818, own text same | same | 2 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `SoundCueRegistry::RemoveCue` | 178 | 98.1818 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 98.1818, own text same | same | 3 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `CCheatMgr::Empty` | 175 | 98.0909 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 98.0909, own text same | same | 2 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `SoundCueRegistry::GetFirstCue` | 170 | 94.6800 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 94.6800, own text same | same | 3 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `AnimationRegistry::ClearAnimations` | 162 | 97.2449 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 97.2449, own text same | same | 2 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `SoundCueRegistry::ClearCues` | 162 | 97.2449 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 97.2449, own text same | same | 3 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `CDDrawChildGroup::PruneOrphans` | 155 | 60.7213 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 60.7213, own text same | same | 2 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed); MapLookup (MapTyped.h:10, changed) |
| `CDDrawChildGroup::DispatchSerializationToObjects` | 129 | 80.9636 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 80.9636, own text same | same | 2 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `zBitVec::SetSize` | 127 | 85.2979 | 100.0000 | 7d7e44b78 | e6889f0a9 PR [#319](https://github.com/sushi-shi/gruntz-decomp/pull/319): 100.0000 to 85.2979, own text same | same | 17 | none |
| `CDDrawChildGroup::WriteObjectSnapshots` | 118 | 92.8571 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 92.8571, own text same | same | 2 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `CButeMgr::NewTabsSave` | 102 | 81.5484 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 81.5484, own text same | same | 1 | zSymTab::traverse (PTree.h:173, changed) |
| `CSBI_ImageSet::Render` | 94 | 87.8421 | 100.0000 | 0e590d118 | 50a8b1d3c PR [#360](https://github.com/sushi-shi/gruntz-decomp/pull/360): 100.0000 to 87.8421, own text same | same | 9 | none |
| `CDDrawChildGroup::CountSerializableObjects` | 94 | 97.3250 | 100.0000 | d943bc941 | afea6698a PR [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366): 100.0000 to 97.3250, own text same | same | 2 | MapGetNext (MapTyped.h:37, changed); MapGetNext (MapTyped.h:31, changed) |
| `CAniPlayer::RenderCel` | 84 | 77.1515 | 100.0000 | 5287280c9 | 484246ea3 PR [#358](https://github.com/sushi-shi/gruntz-decomp/pull/358): 100.0000 to 99.6364, own text same; 50a8b1d3c PR [#360](https://github.com/sushi-shi/gruntz-decomp/pull/360): 99.6364 to 77.1515, own text same; 96f04fb01 PR [#369](https://github.com/sushi-shi/gruntz-decomp/pull/369): 99.6364 to 77.1515, own text same | same | 23 | CState::World (State.h:163, changed) |
| `CGruntPowerupSprite::Update` | 81 | 89.8214 | 100.0000 | ee03fe170 | 19ad7cb24 PR [#332](https://github.com/sushi-shi/gruntz-decomp/pull/332): 100.0000 to 89.8214, own text same | changed | 43 | none |
| `CPlay::StepScroll` | 79 | 88.0345 | 100.0000 | df7484de0 | d1a66325a no PR: 100.0000 to 88.0345, own text same | same | 2 | none |
| `CDib::FillRect` | 75 | 66.4444 | 100.0000 | bd9216dab | 5e14e7ca7 PR [#181](https://github.com/sushi-shi/gruntz-decomp/pull/181): 100.0000 to 66.4444, own text same | same | 3 | none |
| `CAreaMgr::IsSameWorld` | 73 | 58.5000 | 100.0000 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 100.0000 to 58.5000, own text same | changed | 19 | none |
| `CSBI_Image::Render` | 69 | 74.0357 | 100.0000 | a65a294fb | 96f04fb01 PR [#369](https://github.com/sushi-shi/gruntz-decomp/pull/369): 100.0000 to 74.0357, own text same | same | 1 | none |
| `CBaseHashItem::Prev` | 67 | 99.1379 | 100.0000 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 100.0000 to 99.1379, own text same | same | 1 | none |
| `CBaseHash::Delete` | 37 | 99.2308 | 100.0000 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 100.0000 to 99.2308, own text same | same | 1 | none |
| `CPixelTileImageSet::GetStride` | 11 | 99.5000 | 100.0000 | 751ff63bd | 3b09d668f PR [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160): 100.0000 to 99.5000, own text same | changed | 4 | none |

## Drop commits

The drops of the confirmed functions, by commit, largest first. A commit appears once per function it lowered, so the bytes column adds the bytes of every affected function.

| Commit | PR | Confirmed functions dropped | Bytes | Subject |
|---|---|---:|---:|---|
| a1a95c87c | [#208](https://github.com/sushi-shi/gruntz-decomp/pull/208) | 8 | 37,291 | match: recover surface geometry and map ownership (#208) |
| 0e590d118 | [#359](https://github.com/sushi-shi/gruntz-decomp/pull/359) | 7 | 15,221 | refactor: clarify engine clock and status-bar names (#359) |
| 3b09d668f | [#160](https://github.com/sushi-shi/gruntz-decomp/pull/160) | 16 | 12,229 | structure: one project-wide StdAfx.h prelude; retire Mfc/MfcWin/MfcNoInline/Win32 and the AddrWord/ProcAddr seams (#160) |
| 0335a5365 | [#79](https://github.com/sushi-shi/gruntz-decomp/pull/79) | 6 | 11,263 | model: recover template families and shared header ownership (#79) |
| bd9216dab | [#180](https://github.com/sushi-shi/gruntz-decomp/pull/180) | 5 | 10,794 | match: recover engine helpers and eight exact maxima (#180) |
| 520d5b06b | [#88](https://github.com/sushi-shi/gruntz-decomp/pull/88) | 3 | 9,012 | refactor: restore member helpers and typed map lookups (#88) |
| afea6698a | [#366](https://github.com/sushi-shi/gruntz-decomp/pull/366) | 27 | 8,161 | cleanup: audit casts and repair types without matching losses (#366) |
| a159bd8b3 | [#239](https://github.com/sushi-shi/gruntz-decomp/pull/239) | 4 | 6,835 | match: recover exact asset loading and resource owners (#239) |
| dbf27726a | [#124](https://github.com/sushi-shi/gruntz-decomp/pull/124) | 2 | 5,256 | match: shade tables (LumaSortTable, HueSortTable 0 -> 100) and levelplane cleanups (#124) |
| 5e14e7ca7 | [#181](https://github.com/sushi-shi/gruntz-decomp/pull/181) | 2 | 4,811 | Match PID decoding and recover shared pixel unpacking (#181) |
| 4ab11188a | no PR | 1 | 4,736 | cleanup: classify and name source constants |
| 3d4629280 | no PR | 1 | 4,736 | match: recover HsvShiftTable x87 schedule |
| c8a670137 | no PR | 1 | 4,736 | match: restore LithTech list and hash layers |
| 9acc08cec | no PR | 1 | 4,736 | match: restore LithTech virtual list and RezFile hierarchy |
| f0a1dd0aa | [#118](https://github.com/sushi-shi/gruntz-decomp/pull/118) | 1 | 4,736 | match: Wwd pass (Dot Render to 100%, BltDirtyEx CRect(POINT,SIZE)) (#118) |

## Headers and helpers that recur

Across all 107 open functions, these headers changed inside the include closure of the most functions. A header counts for each function whose unit's closure changed it between the function's high and now. Most of these changes are not reached by a changed helper, so the list shows where the churn is, not which change caused a drop.

| Header | Functions with it changed |
|---|---:|
| `MapTyped.h` | 69 |
| `PTree.h` | 54 |
| `WorldInitError.h` | 50 |
| `WapObj.h` | 48 |
| `DDrawWorker.h` | 46 |
| `DDrawWorkerHost.h` | 45 |
| `SaveGame.h` | 45 |
| `DDrawSurfaceMgr.h` | 44 |
| `CImage.h` | 44 |
| `RezArchiveEntry.h` | 44 |
| `State.h` | 43 |
| `FrameClock.h` | 43 |

These inline functions and macros are changed and reached by the most functions. Reach is matched by name, so a common name such as `GetCount` can match another class's method.

| Helper | Header | Change | Functions reaching it |
|---|---|---|---:|
| `MapGetNext` | `MapTyped.h` | changed | 21 |
| `CDDrawChildGroup::GetHeadPosition` | `DDrawChildGroup.h` | added | 10 |
| `CoordsEmpty` | `Grunt.h` | added | 9 |
| `CDDrawChildGroup::GetCount` | `DDrawChildGroup.h` | added | 9 |
| `CResourceNameList::GetCount` | `SpawnList.h` | added | 9 |
| `CImageSet::GetAt` | `DDrawWorker.h` | added | 9 |
| `CDDrawWorker::GetAt` | `DDrawWorker.h` | removed | 9 |
| `WwdDirtyRect::Set` | `ResolveNode.h` | added | 9 |
| `CState::World` | `State.h` | added | 7 |
| `FreeNodePool::NodeOf` | `FreeNodePool.h` | added | 7 |
| `zBitVec::Set` | `BitVec.h` | added | 7 |
| `CBrickzCellNodePool::Push` | `Brickz.h` | added | 7 |

## Stale highs

A high is stale when no compile at its commit reproduces it, or when the baseline gives no committed evidence for it. Only the first cause is a score comparison. The other three come from the baseline's names and history, or from a source file that moved.

| Cause | Functions | Bytes | Meaning |
|---|---:|---:|---|
| Compiled below the high | 19 | 46,609 | The compile at the reference commit scores below the banked high. Limits gives how often the compile matches a banked score at all. |
| No commit scored the high | 6 | 10,854 | No commit in the baseline's history has the high as the function's current score at its RVA, so no committed build reported this high under this identity. |
| Not emitted under its current name | 9 | 8,528 | The function's class or parameter list changed since the high, so its mangled name differs in more than the identifier. Renames pair only identifier changes, so the function is not compiled under the name it has now. |
| Compile failed | 1 | 41 | The unit's source file did not exist at the commit under its current path. |

## TmDeflectStep's drop is at PR 208, and ApplyPickup's high is stale

TmDeflectStep drops at PR [#208](https://github.com/sushi-shi/gruntz-decomp/pull/208), from 99.4577 to 91.0959, and its high is confirmed at f2ef469e3, the last commit whose current score is 99.4577. The drop's commit leaves the function's own tokens the same. The own-source column reads changed, because the function changed later, in commit 0971662a8 (PR [#350](https://github.com/sushi-shi/gruntz-decomp/pull/350)), which replaced `m_arrivalFlags` with a call to `GetArrivalFlags()`. The drop itself did not touch the function. The changed headers include `Brickz.h`, and the changed helper `CMapMgr::CellFlagsAt` is reported at `Brickz.h:322`, reached from the function. At the first commit that banked 99.4577, the compile scores 99.3394, so that commit's score is not reproducible and the last commit's is.

ApplyPickup has a stale high: its reference high, 0a25f32e4, compiles to 97.3947 against the banked 97.4240. Its drops are: PR [#212](https://github.com/sushi-shi/gruntz-decomp/pull/212), 97.4240 to 96.7773; PR [#217](https://github.com/sushi-shi/gruntz-decomp/pull/217), 96.7773 to 95.3411 with its own text changed; and PR [#341](https://github.com/sushi-shi/gruntz-decomp/pull/341), 95.3411 to 93.9891. The first step from 97.42 is PR #212, and the step to 95.34 is PR #217. PR [#345](https://github.com/sushi-shi/gruntz-decomp/pull/345) leaves the score at 93.9891, so it is not a drop, although it changed 14 headers that ApplyPickup's unit includes.

## Method

The baseline's history is read from the first-parent commits that changed `config/match_baseline.tsv`, 2,366 in all. Each function is followed by its relative virtual address (RVA), the identity the baseline uses for a body. A rename leaves the RVA in place, so a function keeps its history under a new name.

The reference high of a function is the last commit where its current score reached its high, and its drops are the later commits that lowered that score. The drops are the commits where the score fell below the previous commit's score, and each one's own-text check compares the function's tokens at the commit before it and at the drop with the renames that commit made applied.

A reference high is compiled through the same path as `concord at-commit`: the unit's source and every header under `include/` as they were at that commit, against the current retail object. A function renamed since then has its identifiers renamed in that snapshot, as whole words, unless the new name already occurs in the snapshot, which would merge two functions. The high is confirmed when the compile scores it, to four decimals.

The headers each unit includes are read at the commit and now, and compared by their contents. Their functions and macros are compared by their tokens, so comments and spacing do not count. A changed definition is reported when the function calls it, directly or through the definitions it calls, where a call is a name followed by a parenthesis and a macro is an all-caps name.

## Limits

At the reference commits, the compile of each unit reproduces the banked current score of 1,935 of the 2,204 functions it emits (88%). A confirmed high is a reproduction. A stale one is a failed reproduction, which can come from the compiler, the names, or the high itself, so a stale verdict is weaker than a confirmed one.

The vendored SDK headers under `vendor/` are read from the working tree on both sides, which is also what the compile reads, since `at_commit` overlays only `include/`. A vendor change between a high and now is not reflected in the compile, but it is not reported as a changed header either.

Helpers are matched by name. A call to `GetCount` reaches every `GetCount` definition in the closure, whatever its class, so the helper columns over-report where a name is common.

The compile of a candidate writes a temporary sibling file beside the unit's source under `src/` and removes it afterward. That is the existing candidate mechanism of the gruntz driver, which every compile uses, so the checkout's tracked files are not changed, but a transient file does appear there.

A parse that reads `start_point` from a [tree-sitter](https://tree-sitter.github.io/tree-sitter/) 0.26.0 node segfaulted the process during development, so the new code reads line numbers from byte offsets instead. Six other modules in the project read `start_point` and may be exposed to the same fault.

The run was made with the checkout clean at 8c3a113ca, and its build report was written at 2026-11-23 10:14. Two files were touched after that report with contents identical to 8c3a113ca.

To reproduce the run, use `uv run --no-config concord lost --gruntz ../gruntz-decomp --save results/lost-ground.json` from the concord checkout, after `gruntz build` in the gruntz-decomp checkout.
