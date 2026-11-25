# Kinds of edit in the targets' own fixes that concord's rewrites do not make

Question: which kinds of source edit have people used to make functions exact that concord's searches cannot make? Each one found is a rewrite to add; if few recur, adding rewrites from history is not worth pursuing.

Result: of 197 fixes that mattered in the last 1,000 commits of gruntz-decomp and Harvest, one existing rewrite makes 24. Among the rest, the kind that recurs most is the shape of a branch (about 13 fixes), then variants of naming a value (about 9), then reading a member through an accessor other than a trivial getter (about 4). Many others are not edits a search can make: a function renamed, a different callee, a corrected constant.

`concord bench --history 1000 --levers` measured this. It takes each function that is exact now and that one of the last 1,000 commits changed, puts back its body from before the commit, and keeps the fix when that body no longer matches (the history cases of [causes.md](causes.md)). For each fix it asks whether one of the single rewrites concord's searches make turns the old body into the present one, comparing the two with whitespace and comments dropped and with a name that only one side uses (a rewrite's invented local, a person's chosen one) taken as a placeholder. Nothing else is compiled. Checkouts: gruntz-decomp 8c3a113ca, Harvest 1e5eb17.

| | gruntz-decomp | Harvest |
| --- | ---: | ---: |
| Fixes that mattered | 67 | 130 |
| Made by one existing rewrite | 15 (named temporary 8, statement order 6, accessor read 1) | 9 (branch sense 5, condition nesting 3, statement order 1) |

The fixes no rewrite makes were grouped by reading their changed lines.

| Kind of edit no rewrite makes | gruntz-decomp | Harvest |
| --- | ---: | ---: |
| Branch shape: a combined condition split into an early `return` or `continue` (or the reverse), a ternary written as `if` (or the reverse), an explicit `else` after a `return` | about 3 | 10 |
| Naming a value in a form the naming rewrite does not make: a loop bound named before its loop, a value named in a block of its own, an expression split into steps (`x = a - 1` as `x = a; x -= 1;`) | 2 | about 7 |
| A member read through an accessor other than a trivial getter, or a wrapper call dropped (`t->m_data` as `t->GetData()`, `found->GetSound()->IsPlaying()` as `found->IsPlaying()`) | about 4 | none |
| The old body defined or called another symbol: a function renamed, `_strcmpi` for `stricmp` | about 8 | some |
| A constant, a type or other one-off semantic fix | most of the rest | most of the rest |

Harvest's naming rewrite spells a local's type as `__typeof__(...)`, so a fix that names a value with its written type does not compare equal to the rewrite's result even when GCC compiles both alike; the naming counts above leave those fixes out.

## Branch shape, tried and removed

Three branch-shape rewrites were added and then removed: a returned ternary as an `if` and a `return` (and back), the rest of a block as an explicit `else` after a branch that always leaves (and back), and a loop body's last combined test split into an early `continue`. They reproduced 1 of the roughly 10 Harvest branch-shape fixes they were built from, because the fixes are each a different form (an `||` early exit split in two, a parameter clamped with a ternary moved into an `if`, a `break` moved into the loop's condition, a ternary inside a returned comparison spread over two returns), each seen once or twice. Out of sample they made nothing exact: a permute sweep of all 270 open Harvest functions (64 candidates, depth 2, seed 1) made the same 5 functions exact as the sweep without them, all through older rewrites, and none of its 60 improvements used a branch-shape step; on the 98 open gruntz-decomp functions with a control-flow cause, the same search made none exact and used no branch-shape step.

The stop rule set before building them, fewer than 15 new exact functions on gruntz-decomp and fewer than 10 on Harvest, was met at 0 and 0, so rewrites mined from history stop here. Most fixes in both histories are one-off forms, renames or semantic corrections that a fixed set of rewrites does not reproduce.
