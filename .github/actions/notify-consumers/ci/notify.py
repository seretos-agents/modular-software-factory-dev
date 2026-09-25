"""Files a ``chore(deps)`` "bump me" ticket in every consumer of a released
lib and places each newly created one in Backlog on the ecosystem board.

Contract: only "no ticket could be filed for a consumer" is an error (exit
1, after every other consumer was still attempted). A missing label, a
missing changelog and a board placement that fails are warnings.

Entry point: :func:`run` (``ci.notify.run(env) -> int``), reading
``VERSION``, ``SOURCE_REPO``, ``GH_TOKEN`` and optionally ``CONSUMERS``,
``LABELS``, ``BOARD_OWNER``, ``BOARD_NUMBER``, ``GITHUB_STEP_SUMMARY`` from
the passed-in mapping -- see ``REQUIRED_ENV``.
"""

from __future__ import annotations

import os
import re
import sys

import ci.actions_io as actions_io
import ci.board as board
import ci.bump_ticket as bump_ticket
import ci.gh as gh

REQUIRED_ENV = ("VERSION", "SOURCE_REPO", "GH_TOKEN")

_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class _BoardPlacer:
    """Places issues in Backlog, resolving the board once and only when the
    first ticket actually needs placing. Every failure is a warning: the
    ticket already exists and is usable without a board placement."""

    def __init__(self, owner: str, number: str) -> None:
        self._owner = owner
        self._number = number
        self._target: board.BacklogTarget | None = None
        self._unresolvable = False

    @property
    def enabled(self) -> bool:
        return bool(self._number)

    def place(self, issue_url: str) -> bool:
        if not self.enabled or self._unresolvable:
            return False
        try:
            if self._target is None:
                self._target = board.resolve_backlog_target(self._owner, self._number)
            board.place_in_backlog(self._target, issue_url)
        except (gh.GhError, actions_io.ScriptError) as exc:
            if self._target is None:
                self._unresolvable = True
            actions_io.warn(f"Board placement of {issue_url} failed (the token needs the 'project' scope): {exc}")
            return False
        return True


def run(env: dict[str, str]) -> int:
    version = env["VERSION"]
    source_repo = env["SOURCE_REPO"]
    lib = source_repo.split("/", 1)[-1]
    tag = f"v{version}"

    consumers = bump_ticket.parse_list(env.get("CONSUMERS", ""))
    wanted_labels = bump_ticket.parse_list(env.get("LABELS", ""))
    placer = _BoardPlacer(env.get("BOARD_OWNER", ""), env.get("BOARD_NUMBER", ""))

    actions_io.append_summary(f"### Dependency-update tickets for {lib} {tag}", env)

    if not consumers:
        actions_io.notice(f"No consumers configured for {lib}; no dependency tickets filed.")
        actions_io.append_summary("- no consumers configured -- nothing filed", env)
        return 0

    failed = 0
    for consumer in consumers:
        if not _REPO_PATTERN.match(consumer):
            actions_io.error(f"{consumer!r} is not an 'owner/repo' consumer.")
            actions_io.append_summary(f"- {consumer}: **invalid consumer name**", env)
            failed += 1
            continue

        try:
            ticket = bump_ticket.file_ticket(
                source_repo=source_repo,
                version=version,
                consumer_repo=consumer,
                wanted_labels=wanted_labels,
            )
        except actions_io.ScriptError as exc:
            actions_io.error(f"{exc} (check the token's Issues: write scope for {consumer})")
            actions_io.append_summary(f"- {consumer}: **FAILED** to file", env)
            failed += 1
            continue

        if not ticket.created:
            print(f"Ticket already exists in {consumer}: {ticket.url}")
            actions_io.append_summary(f"- {consumer}: already filed -- {ticket.url}", env)
            continue

        print(f"Ticket opened in {consumer}: {ticket.url}")
        placed = placer.place(ticket.url)
        labels = ", ".join(ticket.labels) or "none"
        board_note = "Backlog" if placed else ("not placed" if placer.enabled else "board disabled")
        actions_io.append_summary(f"- {consumer}: {ticket.url} (labels: {labels}; board: {board_note})", env)

    return 1 if failed else 0


def main() -> int:
    return actions_io.run_main(lambda: run(dict(os.environ)))


if __name__ == "__main__":
    sys.exit(main())
