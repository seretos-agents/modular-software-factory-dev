"""``ci`` -- plain-source helper package of the shared notify-consumers
action (originally #243 generation 2 in lib-python-projects, moved here so the
logic exists once for the whole ecosystem).

Not installed anywhere: it sits next to ``action.yml`` and is run from the
action directory as ``python3 -m ci.notify``; the test suite imports it the
same way (``pytest.ini`` puts the action directory on ``pythonpath``).

Invariants enforced structurally across every module in this package (see
``tests/test_ci_gh_discipline.py``):

- every ``gh`` invocation goes through the single choke point in
  :mod:`ci.gh` -- no other module spawns a child process directly;
- no CLI-side filtering/paging flags anywhere -- JSON is always parsed in
  Python, never extracted by gh's own query-string or templating flags;
- no shell execution;
- standard library only, no third-party imports.
"""

from __future__ import annotations
