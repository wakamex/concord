# Executor scorecard

Implementation work goes to implementer sessions, run through [aop](https://github.com/wakamex/agent-orchestration-process) or, for the Codex session, natively. This file records every finished task so that each new task goes to the cheapest executor whose record on that kind of task is clean. The master session appends one row per finished task to the log and keeps the scorecard current. Finished work is the evidence; tasks are not rerun only to compare executors, and an executor is judged on all its rows of one kind together, since two runs of one task can differ as much as two executors.

Kinds of task:

- capability: new concord tooling, with a Harvest gate when it applies to Harvest
- diagnosis: one bounded question with a stop limit, where a measured negative result is a complete answer
- search: running concord's existing searches over a list of functions
- rule: an Irrlicht-to-daisy source difference fixed through a translation rule

Gates clean means the whole-build gate (`concord scores --base`) showed no function worse and no matched data lost, and the required builds and tests passed. Diff holds means the master session's reading of the diff found nothing to undo; fixing commit text only does not count against it. Cost is the API-rate figure clanker-analytics records for the run (`api_cost_usd`), missing where it recorded none; the Codex session is native, so its cost is a total over its tasks. Reasoning effort is recorded where the executor takes one (`aop run --effort`); a run without `--effort` uses the harness's configured default, which for Codex here is medium, and published comparisons show GPT-6.1 Terra scoring far lower below max effort than at max.

## Scorecard (2026-10-26)

| Executor | Kind | Tasks | Gates clean | Diff holds | Newly exact functions | Cost per task |
| --- | --- | ---: | --- | --- | ---: | --- |
| Codex `gpt-6.1-sol` | capability | 8 | 8 of 8 | 8 of 8 | 39 | about $39 for all its tasks since 2026-10-24 |
| Codex `gpt-6.1-sol` | search | 1 | 1 of 1 | 1 of 1 | 17 | included above |
| Codex `gpt-6.1-sol` | diagnosis | 2 | 2 of 2 | 2 of 2 | 0 | included above |
| Codex `gpt-6.1-sol` | rule | 1 | 1 of 1 | 1 of 1 | not applied | $0.74 |
| Devin `swe-2-high` | capability | 6 | 6 of 6 | 6 of 6 | 1 | $1.88 where recorded |
| Devin `swe-2-high` | diagnosis | 11 | 11 of 11 | 11 of 11 | 9 | $0.50 to $3.92, mean $2.06 where recorded |
| Devin `swe-2-high` | search | 2 | 2 of 2 | 2 of 2 | 3 | not recorded |
| Devin `swe-2-high` | rule | 4 | 3 of 4 | 3 of 4 | 12, and 141 after re-seeding with its five-way changes | $1.83 to $3.08 where recorded |
| Codex `gpt-6.1-terra` | diagnosis | 3 | 3 of 3 | 3 of 3 | 1 | $0.17 to $0.19 at default effort; $1.38 at max |
| Codex `gpt-6.1-terra` | rule | 1 | 1 of 1 | 1 of 1 | 0 | $0.08, default effort |
| Muse `muse-wonder-full-1.0` | search | 1 | 1 of 1 | 1 of 1 | 0 | subscription only; 5.2M input tokens over two sessions |
| Muse `muse-wonder-full-1.0` | capability | 1 | 1 of 1 | 1 of 1 | 0 | subscription only; 29.6M input tokens |
| Cursor `composer-2.5` | diagnosis | 1 | 1 of 1 | 0 of 1 | 0 | not recorded |
| Cursor `composer-2.5` | search | 2 | 2 of 2 | 2 of 2 | 0 | 1.66M input tokens, a fraction of a percent of the month |
| Cursor `composer-2.5` | rule | 2 | 1 of 2 | 1 of 2 | 0 | not recorded |
| Claude `claude-haiku-5-5` | rule | 1 | 0 of 1 | 0 of 1 | 0 | $0.67 |
| Codex `gpt-6-luna` | rule | 1 | 0 of 1 | 0 of 1 | 0 | $0.01 |

Evaluation:

- Codex `gpt-6.1-sol`: the only executor with a clean record on cross-unit capabilities that need new tooling and a whole-build gate (the CSceneManager recovery, `getMS3DJointNode`, the census, `causal`, `frames`, `scores --base`). Kept at no more than 1x pace of its quota.
- Devin `swe-2-high`: free on the subscription and clean on bounded diagnoses, where it measures negatives carefully and corrects the second reader. Its one rule task that failed the gate committed a change with two losses; the brief now states the gate as a rule it must meet before committing.
- Muse `muse-wonder-full-1.0`: one search task, precise and honest, but its 5-hour and weekly subscription windows run out quickly (3.13x pace after one day on 2026-10-26); aop's resume continues a run after the window resets.
- Cursor `composer-2.5`: with its constraints as 12 numbered rules, its first search task kept all of them and passed the gate in 31 minutes. Its earlier rule task wrote C++11 lambdas, which GCC 4.4 rejects, against a constraint stated in prose; elsewhere it keeps constraints written as numbered checkable rules, so briefs for it list them that way, and its process may not exit after its report, so its job is stopped once the report exists.

Gaps: Codex `gpt-6.1-terra` has one diagnosis row and is being evaluated as a cheaper Codex option at half Sol's price;  Muse has no diagnosis or rule row.

## Log

| Date | Task | Kind | Executor | Newly exact | Gates | Diff | Cost | Notes |
| --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| 2026-10-23 | five-way stub translation | rule | Devin `swe-2-high` | 141 after re-seed | clean | holds | none | 7 of 7 units compiling; its changes were applied |
| 2026-10-23 | five-way stub translation | rule | Codex `gpt-6.1-sol` | not applied | clean | holds | $0.74 | 7 of 7 units, 3.1 times fewer tokens than Devin |
| 2026-10-23 | five-way stub translation | rule | Cursor `composer-2.5` | not applied | failed | does not hold | | 5 of 7 units; C++11 lambdas |
| 2026-10-23 | five-way stub translation | rule | Claude `claude-haiku-5-5` | not applied | failed | does not hold | $0.67 | 1 of 7 units; broke `SColor.h` include order; timed out |
| 2026-10-23 | five-way stub translation | rule | Codex `gpt-6-luna` | not applied | failed | does not hold | $0.01 | stopped after one rule |
| 2026-10-24 | eleven stub causes | rule | Devin `swe-2-high` | 11 | clean | holds | | call-site translations, `reconcile_forwards` |
| 2026-10-24 | blockers carry includes and helpers | capability | Devin `swe-2-high` | 0 | clean | holds | | |
| 2026-10-24 | plain helper names | capability | Devin `swe-2-high` | 0 | clean | holds | | `concord rename` |
| 2026-10-24 | near-exact batch | search | Codex `gpt-6.1-sol` | 17 | clean | holds | | 72 functions searched |
| 2026-10-24 | `inlines` | capability | Devin `swe-2-high` | 0 | clean | holds after fixes | $1.88 | two rate-limit restarts |
| 2026-10-24 | `overrides` and Maya `setPosition` | capability | Devin `swe-2-high` | 1 | clean | holds | | |
| 2026-10-24 | GUI `erase` question | diagnosis | Devin `swe-2-high` | 0 | clean | holds | | five runs; closed at two target functions |
| 2026-10-24 | CTerrainSceneNode restoration | capability | Codex `gpt-6.1-sol` | 19 | clean | holds | | `place --object` |
| 2026-10-24 | rejected restorations retried | capability | Codex `gpt-6.1-sol` | 7 | clean | holds | | `blockers --only` |
| 2026-10-24 | unsearched near misses | search | Devin `swe-2-high` | 3 | clean | holds | | `address_arithmetic` rewrite |
| 2026-10-24 | causal diffs and census | capability | Codex `gpt-6.1-sol` | 0 | clean | holds | | `causal`, `causal-census`, `causal-trial` |
| 2026-10-25 | check commands | capability | Codex `gpt-6.1-sol` | 0 | clean | holds | | `scores --base`, `symbols-merge`, `symbols-check` |
| 2026-10-25 | `CString` call-site flips | diagnosis | Devin `swe-2-high` | 0 | clean | holds | $1.97 | measured negative |
| 2026-10-25 | `CString` `operator+` lead | diagnosis | Devin `swe-2-high` | 0 | clean | holds | $0.50 | refuted |
| 2026-10-25 | Q3 blend and joint restoration | capability | Codex `gpt-6.1-sol` | 4 | clean | holds | | companion-first include order |
| 2026-10-25 | inliner distribution and frame layout | diagnosis | Devin `swe-2-high` | 0 | clean | holds | $1.83 | measured negatives |
| 2026-10-25 | CSceneManager recovery | capability | Codex `gpt-6.1-sol` | 9 | clean | holds | | `refresh --header`, `inlines --semantic` |
| 2026-10-25 | build-wide semantic sweep | rule | Devin `swe-2-high` | 1 | clean | holds | $3.08 | TGA field reads, `BspPoly::operator==` |
| 2026-10-25 | CXFileReader inlining budget | diagnosis | Codex `gpt-6.1-sol` | 0 | clean | holds | | `inline-budget`; four levers rejected |
| 2026-10-25 | shared `src/ox` header items | rule | Devin `swe-2-high` | 7 | failed: 2 worse | does not hold | $1.83 | committed with two losses; not integrated |
| 2026-10-25 | terrain without the losses | diagnosis | Devin `swe-2-high` | 8 | clean | holds | $3.92 | definition order from `hv search --blocks` |
| 2026-10-25 | frame layout | capability | Codex `gpt-6.1-sol` | 0 | clean | holds | | `frames`; class far smaller than assumed |
| 2026-10-26 | CAlienEntity neighbours | search | Muse `muse-wonder-full-1.0` | 0 | clean | holds | subscription | one partial gain; quota stop and resume |
| 2026-10-26 | CXFileReader comparison and blockers | diagnosis | Codex `gpt-6.1-sol` | 0 | clean | holds | | 28 newly matched blocked by 2 peephole2 scratch choices |
| 2026-10-26 | CLuaManager and CVideoNull register-only | diagnosis | Devin `swe-2-high` | 0 | clean | holds | | both units stopped at two interventions; `find_function` now resolves namespace-nested free functions |
| 2026-10-26 | stable progress report names (Harvest tooling) | capability | Muse `muse-wonder-full-1.0` | 0 | clean | holds | subscription; 29.6M input tokens | simulated bot diff on #38: 6 broken matches to 0, 34 regressions to 2; measures identical; toolchain tests run by the master session |
| 2026-10-26 | `runPPShader` one-byte difference | diagnosis | Devin `swe-2-high` | 1 | clean | holds | | a missing `BilinearFilter = false;`, not register allocation; `concord author` for authored source in units with no Irrlicht ancestor |
| 2026-10-26 | CAlienEntity `render` and `updateMinerMovement` | search | Cursor `composer-2.5` | 0 | clean | holds | 0.3% of the month | 12 numbered rules, all kept; `updateMinerMovement` 94.28 to 95.96; `render` reached 97.97 in permute but `finish` could not type its temporaries |
| 2026-10-26 | `finish` names template-id temporaries | capability | Devin `swe-2-high` | 0 | clean | holds | | `CRect<int>` temporaries were named `cRect<int>`; `render` written at 97.97 in its unit, whole-build fuzzy 95.85 to 96.66 |
| 2026-10-26 | keep the maintainer's `Blend` comment in translation | rule | Cursor `composer-2.5` | 0 | clean | holds | | 10 numbered rules, all kept; one minute; translation equals the file byte for byte |
| 2026-10-26 | complete peephole scratch-search history | capability | Codex `gpt-6.1-sol` | 0 | concord checks; no Harvest change | pending master review | | Four observed compiles equal canonical objects; reader blockers named by their incoming positions and predecessors; source correction remains unresolved |
| 2026-10-26 | peephole2 scratch-search replay | capability | Codex `gpt-6.1-sol` | 0 | clean | holds | | `scratch-history` traces the pinned compiler; four traced objects identical to canonical; names both CXFileReader blockers' search positions |
| 2026-10-26 | CPlayState::render | diagnosis | Devin `swe-2-high` | 0 | clean | holds | | no source difference: identical calls and immediates; ordering differences from unit context; two searches stopped |
| 2026-10-26 | CXFileReader blockers by scratch position | diagnosis | Devin `swe-2-high` | 0 | clean | holds | | definition order cannot move the emit slots; the restoration emits seven template copies the target never did, so the lever is the parser bodies |
| 2026-10-26 | CXFileReader's extra template copies | diagnosis | Devin `swe-2-high` | 0 | clean | holds | | falsified its own earlier claim: all seven copies exist in the target; the difference is inlining inside them, not a source construct; spent neither intervention |
| 2026-10-26 | hand-solve CXFileReader's size mismatches | diagnosis | Devin `swe-2-high` | 0 | clean | holds | | both size differences are inliner choices of which calls stay out of line; no source difference; four variants rejected |
| 2026-10-26 | CXFileReader's inlined callees against the original's copies | diagnosis | Codex `gpt-6.1-terra` | 0 | clean | holds | $0.19 | both header definitions compile to the original's code; `operator=` copies differ only by peephole2 registers; 2 minutes; default effort (medium) |
| 2026-10-26 | IGUIElement::updateAbsolutePosition header candidate | diagnosis | Codex `gpt-6.1-terra` | 0 | clean | holds | $0.17 | all 22 copies identical, differing from the target by two reversed clipping comparisons; Irrlicht calls `clipAgainst` where the translation expands it; trial blocked by a missing objdiff-cli; default effort (medium) |
| 2026-10-26 | clipAgainst hypothesis for updateAbsolutePosition | rule | Codex `gpt-6.1-terra` | 0 | clean | holds | $0.08 | refuted by overlay before any checkout change (98.16 in all 22 units); stopped as its rules required; did not report the score without the overlay; default effort (medium) |
| 2026-10-26 | eight near misses after the new rewrites | search | Cursor `composer-2.5` | 0 | clean | holds | | report written at 19:40 to Cursor's own transcript but never streamed, so aop waited four hours for its timeout (fixed in aop 296c075); no gains passed: CSpritePackage::load's 98.36 candidate was rejected by harvest-oracle as a behavior change |
| 2026-10-26 | drawIndexedTriangleList's unnamed callee | diagnosis | Codex `gpt-6.1-terra` | 1 | clean | holds | $1.38 over two sessions | effort max; the callee is vector<S2DVertex>::reserve where the translation used resize; both overloads 93.36 to 97.91, reserve copy matched; port keeps resize |
| 2026-10-26 | CSpritePackage::load rerun | search | Devin `swe-2-high` | 0 | clean | holds | | reproduced the 98.36 candidate; harvest-oracle rejected it (it reorders a readSprite call against memmove and moves away from the original), so nothing was written |
| 2026-10-27 | calls-and-constants sweep | capability | Devin `swe-2-high` | 0 | clean | holds | | `concord calls-constants`; both known cases pass; 553 inexact functions compared, 274 differ; inline-copy noise not yet separated from callee substitutions |
| 2026-10-27 | CBase64url::encode2 table reference | diagnosis | Cursor `composer-2.5` | 0 | clean | does not hold | | not a source difference: GCC names the same table by section offset; fixed calls-constants to name references through defined symbols; two source probes rejected; finished on its own in 16 minutes; broke two rules: a `Co-authored-by: Cursor` trailer, and a knowledge cause concord does not accept, which failed two tests until fixed |
