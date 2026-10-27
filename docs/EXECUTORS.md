# Executor scorecard

Implementation work goes to implementer sessions, each task a fresh run through [aop](https://github.com/wakamex/agent-orchestration-process). Until 2026-10-27 Sol ran in one long-lived native Codex session, and its earlier rows come from that session. This file records every finished task so that each new task goes to the cheapest executor whose record on that kind of task is clean. The master session appends one row per finished task to the log and keeps the scorecard current. Finished work is the evidence; tasks are not rerun only to compare executors, and an executor is judged on all its rows of one kind together, since two runs of one task can differ as much as two executors.

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
| Devin `swe-2-high` | diagnosis | 12 | 12 of 12 | 12 of 12 | 9 | $0.50 to $3.92, mean $2.06 where recorded |
| Devin `swe-2-high` | search | 2 | 2 of 2 | 2 of 2 | 3 | not recorded |
| Devin `swe-2-high` | rule | 5 | 4 of 5 | 4 of 5 | 12, and 141 after re-seeding with its five-way changes | $1.83 to $3.08 where recorded |
| Codex `gpt-6.1-terra` | diagnosis | 4 | 4 of 4 | 4 of 4 | 1 | $0.17 to $0.19 at default effort; $1.38 at max |
| Codex `gpt-6.1-terra` | rule | 2 | 2 of 2 | 2 of 2 | 0 | $0.08 at default effort; $1.54 at max |
| Muse `muse-wonder-full-1.0` | search | 1 | 1 of 1 | 1 of 1 | 0 | subscription only; 5.2M input tokens over two sessions |
| Muse `muse-wonder-full-1.0` | capability | 1 | 1 of 1 | 1 of 1 | 0 | subscription only; 29.6M input tokens |
| Cursor `composer-2.5` | capability | 1 | 1 of 1 | 1 of 1 | 0 | not recorded |
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

## Grades (2026-10-27, afternoon)

Each executor is scored out of 100 on four parts: results (40), what its work moved, from exact functions to findings that closed or opened a lever; reliability (30), clean gates, diffs that hold and rules kept; efficiency (20), cost and wall time for what it delivered; and reports (10), whether its report was accurate and complete enough to act on without rereading its work. The score is the sum of the four parts. A is 90 and above, B 80 to 89, C 70 to 79, D 60 to 69; within a band, x0 to x3 is a minus, x4 to x6 plain and x7 to x9 a plus. These are the master session's judgment from the rows below, not a benchmark, and they move as rows are added.

| Executor | Grade | Score | Results | Reliability | Efficiency | Reports | Median wall time per task | What it is for |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Codex `gpt-6.1-sol` | A- | 91 | 39 | 27 | 16 | 9 | 19.6 minutes in its one concord aop run; 17 minutes over 31 aop runs in w3sim; about 1 to 4 hours per task in its earlier native session | multi-unit recoveries that need new tooling and a whole-build gate; its first aop task cost $1.75, near its w3sim median |
| Codex `gpt-6.1-terra` at `--effort max` | B+ | 88 | 34 | 29 | 16 | 9 | 8.4 minutes (6.2 to 18.4, eight runs) | diagnosing a concrete source lead from the sweep; found the `sortRiver` fallback (1 newly matched, five copies up to 87.79) and showed two sweep leads were tool artifacts, fixing the tool each time |
| Antigravity Gemini 4 Argon (second reader) | B | 85 | 37 | 22 | 18 | 8 | not timed | choosing the next lever; its levers held every time, about half its details did not |
| Devin `swe-2-high` | B- | 81 | 33 | 22 | 19 | 7 | 23.4 minutes (under 1 to 120, 39 runs) | bounded diagnoses and capability work at no cost; matched MD2 `loadFile` and generalized the reserve rule; one knowledge entry that failed the suite, reported as passing, and one commit made with losses |
| Muse `muse-wonder-full-1.0` | C+ | 78 | 30 | 28 | 10 | 10 | 49 minutes (12.5 to 128, three runs) | a capability with a careful proof (the report naming fix, merged upstream as #39); its subscription runs out fast |
| Cursor `composer-2.5` | D+ | 67 | 20 | 20 | 18 | 9 | 12.6 minutes (1.3 to 23, seven runs; one held 4 hours by an aop bug, now fixed) | small tool fixes written as numbered checkable rules; its string-pool comparison holds after fixes, but it added co-author trailers twice, a test tied to its own worktree, and a mechanism that accepted a zero-gain edit |

Muse's and Cursor's earlier scores (80 and 74) did not equal the sum of their parts; the sums are 78 and 67.

Wall time is from aop's start and finish records for each run, waiting for memory excluded. Many of Devin's runs report `failed` only because of aop's trajectory-export bug while their work completed; the times count them.

## Cost: Sol against Terra (2026-10-24 to 2026-10-27)

Sol now runs through aop like every other executor, so the comparison that matters going forward is Sol through aop against Terra through aop. Sol has no concord aop runs yet; its column uses its 31 aop runs in w3sim on 2026-10-24 and 2026-10-25 (`--effort high`), the nearest measured per-task figures, beside its native concord session for reference. Both models draw on the same Codex subscription, so the binding limit is the weekly quota rather than money; the dollar figures are API prices for the tokens each run used, from aop's records and clanker-analytics, which agree. The tasks differ: Sol's were multi-unit recoveries and new tools, Terra's single-function diagnoses.

| | Sol through aop (w3sim, high effort; one concord run so far) | Terra through aop (concord) | Sol native session (concord, until 2026-10-27) |
| --- | ---: | ---: | ---: |
| Tasks | 31 runs in w3sim; 1 in concord | 8 tasks over 11 runs (5 tasks at max effort) | about 12 |
| Total API-price cost | $92.19 in w3sim; $1.75 in concord | $6.75 | $43.58 |
| Cost per task | median $1.77, mean $2.97 ($0.16 to $24.28); the concord task $1.75 | about $0.84; median $1.38 at max ($0.62 to $1.54) | about $3.60 |
| Share of input tokens read from cache | 98.9% | 98.4% | 98.8% |
| Wall time per task | median 17 minutes (1.4 to 280); the concord task 19.6 minutes | median 8.4 minutes per run at max (6.2 to 18.4) | about 1 to 4 hours, not timed |
| Newly exact functions | 0 from the concord task (one function 66.20 to 77.45) | 2 | 56 |

Every Codex run reads nearly all its input from cache, through aop or not: each call re-sends the conversation so far, so a run's cost grows with its number of calls and the length of its context. The native session's cost per task, about $3.60, sits within the spread of Sol's aop runs, so moving Sol to aop changes accounting more than price: each task now gets its own costed, timed row, and one task's context no longer carries into the next. On cost per task, Sol through aop is about twice Terra at max effort at the median; on results, Sol's native session produced 56 newly exact functions to Terra's 1. Leads from the sweep go to Terra, and a lead that becomes a whole-unit recovery goes to Sol, now as an aop run; Sol's first concord aop task cost $1.75, at its w3sim median; a few more replace the w3sim figures.

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
| 2026-10-27 | CCGMaterialRenderer::printVariables constant | rule | Codex `gpt-6.1-terra` | 0 | clean | holds | $1.54 over two sessions | effort max; Harvest's CgApi.h defined CG_PROGRAM as 4125 (NVIDIA's CG_LINE) where the target passes 4109; authored header correction through concord; printVariables 99.4848 to 99.4862 |
| 2026-10-27 | CPlayState renderMinimap and updateState | rule | Devin `swe-2-high` | 0 | clean | holds | | authored per-branch marker drawing, the literal progress-bar colour and NextState on both arms; renderMinimap 78.10 to 88.93, updateState 95.21 to 96.90, oracle agrees; author now keeps oracle-checked improved candidates and combines edits; rebased cleanly onto the header-constant change |
| 2026-10-27 | calls-constants inline-copy noise | capability | Cursor `composer-2.5` | 0 | clean | holds | | different-callees entries 176 to 123; resize against reserve still a substitution; all rules kept; 5 minutes; its sweep read stale reports because the brief left out hv match |
| 2026-10-27 | CHighscoreScreen::OnEvent and CGUIEditBox::processKey extra comparisons | diagnosis | Codex `gpt-6.1-terra` | 0 | clean | holds | $1.34 | effort max; both reported extras exist in the target, so not source differences; fixed calls-constants to stop assigning shared instructions to one side; two processKey variants rejected |
| 2026-10-27 | CSpritePackage::load out-of-line destructor | diagnosis | Devin `swe-2-high` | 0 | clean | holds | | the target's ~SAnimationData copy is exact and implicit; its inliner skipped one loop edge ours takes; destructor forms and definition orders each lost an exact function; commit authored as Devin, corrected on integration |
| 2026-10-27 | MD2 loadFile reserve and the set_used rule | rule | Devin `swe-2-high` | 1 | clean | holds | | generalized the indexed-buffer guard to five proven sites and set_used(0) to clear(); learned the SFrameData _M_insert_aux address; 25.6 minutes |
| 2026-10-27 | CHighscoreScreen::parseHighscoreString status tables | diagnosis | Cursor `composer-2.5` | 0 | clean | does not hold | | the tables were right; a file-scope type table shifted the status lookups by 0x20; moving it gained nothing and literal keys lowered setVisible 89.8 to 86.0; its unit-edit mechanism accepted a zero-gain edit and was not taken, only the knowledge entry; a `Co-authored-by: Cursor` trailer again; 25.6 minutes |
| 2026-10-27 | calls-constants string pools by text | capability | Cursor `composer-2.5` | 0 | clean | holds after fixes | | string-section references compare and report by their text; the remaining 16 functions with string differences are real sprite-name, credits and message differences; data entries 316 to 401 since unpaired strings are now one-sided; its real-object test read a path in its own worktree and asserted a string the next fix would remove, both corrected on integration; 11.5 minutes |
| 2026-10-27 | six sprite-name functions from the string sweep | diagnosis | Codex `gpt-6.1-terra` | 0 | clean | holds | $0.62 | effort max; the sprite lists already match the target's names and order; an inlined string constructor references a literal three times where an out-of-line one references it once, so the sweep's one-sided strings were counts; fixed calls-constants to report shared references as count differences; 8.9 minutes; the master session's brief had read the counts as source differences |
| 2026-10-27 | IGUILayout::sortRiver and the CImage message | diagnosis | Codex `gpt-6.1-terra` | 1 | clean | holds | $1.43 over two runs | effort max; sortRiver substitutes "tab" for a missing layout flag where ours used "": 1 newly matched, copies 0 to 87.79 and 25.83 to 58.1 in five units; the literal-only change gained nothing; oracle 52 trace differences to 0, every run crashing partway; CImage's compared extent was drawRectangle, no source change; built gated authored header corrections; stopped once to ask for IGUILayoutInline.h, which the brief had left out; 26.6 minutes |
| 2026-10-27 | CDefenseTowerEntity unit recovery | capability | Codex `gpt-6.1-sol` | 0 | clean | holds | $1.75 | first Sol task as an aop run, effort high; updateLogic 66.20 to 77.45 with authored declaration order, alpha clamp and sprite-bank order, oracle agrees on 5,000 cases; both constructors blocked by saturated CString inlining and updateTowerLinks by branch layout, recorded with compiler budgets; 10.2M input tokens, 98.1% cached; 19.6 minutes |
| 2026-10-27 | CVideoSoftware alpha bit | rule | Devin `swe-2-high` | 0 | failed: 4 data bytes lost | holds | | the original packs A1R5G5B5 with a bool alpha flag shifted left 15 where ours used `?: 0x8000`; both drawIndexedTriangleList overloads 97.91 to 99.80, oracle agrees on 5,000 cases, but the gate lost 4 matched bytes in an unrelated wide-string table at 0x602a86, so it reverted; fixed find_function choosing between same-arity overloads; it reported its Harvest tests failing on `docker image inspect`, which other runs on this host passed (cause not checked); its knowledge entry used a cause and a status concord does not accept, which failed four tests until fixed on integration; 119.6 minutes |
