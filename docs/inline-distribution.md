# Harvest CString inline-site measurements

The source search retains two small sprite-replacement improvements and recovers no additional exact functions. It does not reproduce the target's inline allocation. The normalized whole-build gate exits 0: `2 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0`. Both retained candidates agree with the preceding source on all 5,000 generated cases under [harvest-oracle](https://github.com/wakamex/harvest-oracle).

The comparison uses Harvest's `9a39d4d` seed, its pinned [GNU Compiler Collection (GCC)](https://gcc.gnu.org/) 4.4.3 toolchain and canonical flags. [objdiff](https://github.com/encounter/objdiff) supplies the scores below. The measurements include every literal site's reference count and offsets, compiler decisions, source and object hashes, parameter defaults, candidate scores, oracle results and the whole-build result in [the compressed evidence](../results/inline-distribution-9a39d4d.json.gz).

## Retained source and scores

The two retained changes name one string in a scope ending at its original full-expression boundary. `concord author` writes the authored construction and sprite-assignment lines for `BrainWave` in `replaceWithMagnetoSprites` and `AlienLooker0049` in `replaceWithLookerSprites`. Each scores higher without lowering another function in the unit. Each oracle comparison reports 5,000 agreements, zero differences and zero undefined cases. The final whole-build comparison covers 5,240 functions and 1,629 matched data runs.

The table compares the baseline with the retained source. Target sizes are the measured target-object function extents. These objdiff scores use a different metric from Harvest's instruction-diff percentages.

| Function | Target bytes | Baseline objdiff | Retained objdiff | Result |
| --- | ---: | ---: | ---: | --- |
| CDefenseTowerEntity complete constructor, C1 | 15,365 | 80.469635 | 80.469635 | No eligible source change |
| CDefenseTowerEntity base constructor, C2 | 24,437 | 87.70007 | 87.70007 | No eligible source change |
| CAlienEntity::replaceWithMagnetoSprites | 12,437 | 83.05959 | 83.14168 | Authored scoped BrainWave string |
| CAlienEntity::replaceWithLookerSprites | 8,485 | 70.076836 | 70.20847 | Authored scoped AlienLooker0049 string |
| CAlienEntity::replaceWithTinySprites | 4,741 | 79.030334 | 79.030334 | Candidate rejected by the oracle |
| CDropshipEntity::loadSprite | 11,205 | 98.48337 | 98.48337 | Two regressing forms rejected |
| CPlayState::OnEvent | 13,487 | 70.17368 | 70.17368 | Its explicit pointer-literal choices already agree |

The retained alien unit has unchanged compiler size estimates, caller/callee inline-edge counts and literal-site choices. Its gains concern local code generation around the named values. The source construct responsible for the target's different allocation remains unidentified.

## Literal choices differ despite identical name sequences

For the explicit pointer constructions measured here, one literal reference identifies an out-of-line CString construction and three identify its inlined form. The site report retains the source line, encoding and each instruction offset on both sides. Repeated literals and other reference counts remain unresolved.

The table names every differing literal group. A range includes both endpoints. The complete constructor and base constructor each use the same sequence of 147 names as the target; the alien and dropship sequences also agree.

| Function | Inlined in ours / target | Inline only in ours | Inline only in target |
| --- | --- | --- | --- |
| Tower C1 | 20 / 19 | DefenceTower0008 through DefenceTower0019 | DefenceTowerOn0064 through DefenceTowerOn0071; Laser; LaserGlow; LaserSprite |
| Tower C2 | 106 / 107 | DefenceTowerOn0005 through DefenceTowerOn0016 | DefenceTower0000; DefenceTowerOn0045 through DefenceTowerOn0056 |
| Magneto | 74 / 58 | BrainBottom0058 through BrainBottom0071; BrainRotor; BrainWave | None |
| Looker | 8 / 22 | AlienLooker0000; AlienLooker0001; AlienLooker0070; AlienLooker0071 | AlienLooker0048 through AlienLooker0065 |
| Tiny | 20 / 22 | AlienTiny0000 through AlienTiny0007 | AlienTiny0014 through AlienTiny0023 |
| Dropship | 43 / 44 | None | DragonFly0019 |

## The three sprite units exhaust their growth allowance

GCC's interprocedural analysis (IPA) inliner records the consideration order, callee instruction estimate, growth across all calls, badness, frequency, net unit change and resulting caller size. `inline-budget` now preserves those fields and keeps graph nodes separate when constructor variants have the same printed name.

The budget calculation reads the compiler source's `params.def`, with overrides from the recorded command. The relevant defaults are `large-unit-insns=10000`, `inline-unit-growth=30`, `large-function-insns=2700`, `large-function-growth=100` and `inline-call-cost=12`. GCC uses integer arithmetic: the unit cap is the larger of its minimum observed size and the unit-size floor, multiplied by 130 and divided by 100. A successful edge may cross the cap before the next heap pop. A function's growth limit uses the larger caller/callee self estimate, expanded by 100 percent, together with the 2,700-instruction floor.

The following unit estimates account for every recorded net change. They measure compiler instructions rather than machine-code bytes. Diagnostic function bytes and relocation offsets agree with canonical objects, and their recorded source inputs are verified.

| Translation unit | Starting estimate | Final estimate | Integer unit cap | Last successful greedy edge |
| --- | ---: | ---: | ---: | --- |
| CDefenseTowerEntity.cpp | 25,206 | 32,807 | 32,767 | 68-instruction narrow pointer constructor, net growth 54 |
| CAlienEntity.cpp | 22,509 | 29,295 | 29,261 | 68-instruction narrow pointer constructor into Tiny, net growth 54 |
| CDropshipEntity.cpp | 10,330 | 13,436 | 13,429 | 68-instruction narrow pointer constructor into loadSprite, net growth 54 |
| CPlayState.cpp | 43,084 | 50,908 | 56,009 | Wide CString::append(int) into updateState, net change -14 |

The compiler is `g++ (Ubuntu 4.4.3-4ubuntu5) 4.4.3`, using `-O2` in image `b94ef6033199879fb811edc1d642923532b9213f245eb081646157559e3d45dd`. The evidence retains its complete command, input hashes and parameter-source hashes.

The default IPA dump omits edge identifiers and literal arguments. It therefore supplies ordered caller/callee decisions and final literal choices separately; it cannot assign an individual literal to an individual heap pop. It also prints both tower constructors under the same demangled caller name. The tool leaves that identity unresolved instead of attaching a decision to the wrong variant. Recovering a per-literal consideration order would require additional compiler observation.

## The bounded source search leaves inline choices unchanged

`inline-budget --probe-constructions` measures a scoped named string at each mismatched literal construction as a source overlay and compares every emitted function of its unit. The search screened 23 tower C1 forms, 25 C2 forms, 16 Magneto forms, 22 Looker forms and 18 Tiny forms. All 104 forms leave their literal inline choices unchanged. No tower form improves its selected constructor while preserving every other unit score. Two alien forms yield the retained local gains.

The Tiny candidate names AlienTiny0001 and raises its score to 79.67678, with no lower unit score. The oracle rejects it: 2,405 of 5,000 cases differ, beginning at seed 1 with one external call versus two. Comparison with the original executable reports zero differing cases before and 2,405 after. Tiny retains its baseline source.

For dropship, naming DragonFly0019 lowers the score to 98.40589. Replacing the existing DragonFly0018 result temporary with direct assignment lowers it to 98.39116. Neither candidate reaches the oracle, and these two failed interventions end its trials. No shared-header edit is retained, and no definition order is changed.

## OnEvent's remaining difference concerns destructor calls

The current CPlayState unit stays below its unit cap. OnEvent's estimate is 6,853 instructions both before and after IPA inlining. Its four wide pointer-constructor calls agree with the target. The two explicit pointer literals, Game saved and Write error! Unable to save game!, each have one reference on both sides.

The target calls the wide CString destructor 36 times and ours calls it 30 times. These counts do not locate individual differing sites or distinguish inline expansion from shared cleanup tails. The dump repeatedly rejects OnEvent's wide pointer-constructor and destructor edges because their calls are unlikely and code size would grow. Its residual difference is not established as another saturated-unit-budget case, and the pointer-literal probe has no differing construction to rewrite there.

## Validation and reproduction

The kept Harvest tree passes both [Zig](https://ziglang.org/) builds, `zig build harvest -j2` and `zig build census -j2`, from `port`. The toolchain-enabled [pytest](https://docs.pytest.org/) run, `HARVEST_TEST_TOOLCHAIN=1 uv --no-config run --locked pytest -q`, reports 232 passed and 3 skipped. Using [uv](https://docs.astral.sh/uv/), Concord's `uv --no-config run --locked python -m unittest` reports 496 tests with 16 skipped, and its [Ruff](https://docs.astral.sh/ruff/) check, `uv --no-config run --locked ruff check`, passes. Both diffs pass `git diff --check`.

Run host commands with `CONCORD_JOBS=2` and unset `XDG_DATA_HOME` for container-backed commands. With a current `causal` dump and its adjacent metadata, the extended command is:

```sh
env -u XDG_DATA_HOME CONCORD_JOBS=2 uv --no-config run --locked concord inline-budget DUMP --params-def PARAMS_DEF --harvest HARVEST --unit UNIT --symbol SYMBOL --save REPORT
```

Add `--probe-constructions` to screen the differing literal sites. Add `--apply` to send an eligible winner through `author` and its oracle. Gate a retained change with `concord scores --harvest HARVEST --base 9a39d4d --save REPORT` and judge its exit status. The retained result has zero worse functions and zero matched data bytes lost. No source form reproducing the target's complete inline allocation is established by this search.
