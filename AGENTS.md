# Working on concord

Matches come from concord. When matching work calls for a manual step (rewriting or reformatting a found source, choosing how to spell a condition, picking which functions to search, checking a result across units, filling in a symbol), build it into concord as a general capability with tests, then produce the result through concord. A change submitted to a target project is concord's output, verified by the target's own matcher, not a hand edit on top of it.
