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

## What follows

Branch shape recurs on both targets and its rewrites are mechanical, so it is added first, and measured by the open functions it makes exact on both targets. Naming variants come next.
