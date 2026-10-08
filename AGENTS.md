# Working on concord

Matches come from concord. When matching work calls for a manual step (rewriting or reformatting a found source, choosing how to spell a condition, picking which functions to search, checking a result across units, filling in a symbol), build it into concord as a general capability with tests, then produce the result through concord. A change submitted to a target project is concord's output, verified by the target's own matcher, not a hand edit on top of it.

## Upstreaming

Whenever concord produces complete matches in a target project, open a pull request there, and include the partial matches (score gains that lose no exact function) concord found along the way. Only concord's output counts; hand-made matches are not submitted. The pull request links the concord commits that introduced the capability behind the matches, or, when no new capability was involved, links concord and says which command and settings produced them. Push concord before opening the pull request so its links resolve. Infrastructure fixes (tooling, configuration, delinker or matcher bugs) and behavior fixes that the oracle shows bring a function closer to the original's behavior can go upstream in their own pull request, outside match pull requests; state any score they cost.
