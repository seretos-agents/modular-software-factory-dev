"""Behavioural tests for ``ci.notify`` -- the multi-consumer orchestration the
composite action runs: one ticket per consumer, best-effort labels and board,
and an error only when a consumer's ticket could not be filed at all."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fake_gh import FakeGitHub

LIB = "lib-python-projects"
SOURCE_REPO = f"seretos-agents/{LIB}"
VERSION = "0.2.0"
TAG = f"v{VERSION}"
TITLE = f"chore(deps): bump {LIB} to {TAG}"

CONSUMER_A = "seretos-agents/agent-project-issues"
CONSUMER_B = "seretos-agents/workboard"

FIELDS = [
    {
        "id": "PVTSSF_status",
        "name": "Status",
        "options": [{"id": "opt_backlog", "name": "Backlog"}, {"id": "opt_todo", "name": "Todo"}],
    }
]


@pytest.fixture
def fake(monkeypatch):
    import ci.gh  # noqa: PLC0415

    sim = FakeGitHub()
    sim.add_release(SOURCE_REPO, TAG, "the changelog")
    sim.set_fields(FIELDS)
    monkeypatch.setattr(ci.gh, "run_gh", sim.run_gh)
    return sim


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    env = {
        "VERSION": VERSION,
        "SOURCE_REPO": SOURCE_REPO,
        "GH_TOKEN": "fake-token",
        "CONSUMERS": f"{CONSUMER_A}\n{CONSUMER_B}",
        "LABELS": "ai-generated,task",
        "BOARD_OWNER": "seretos-agents",
        "BOARD_NUMBER": "2",
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    env.update(overrides)
    return env


def _run(env: dict[str, str]) -> int:
    import ci.notify  # noqa: PLC0415

    return ci.notify.run(env)


def _url(fake: FakeGitHub, repo: str) -> str:
    return fake.issues_matching(repo, TITLE)[0]["url"]


def test_files_a_ticket_per_consumer_and_places_each_in_backlog(fake, tmp_path):
    fake.set_labels(CONSUMER_A, ["ai-generated", "task"])
    fake.set_labels(CONSUMER_B, ["task"])

    exit_code = _run(_env(tmp_path))

    assert exit_code == 0
    assert fake.issue_labels(CONSUMER_A, TITLE) == [["ai-generated", "task"]]
    assert fake.issue_labels(CONSUMER_B, TITLE) == [["task"]]
    assert fake.board_item_status(_url(fake, CONSUMER_A)) == "Backlog"
    assert fake.board_item_status(_url(fake, CONSUMER_B)) == "Backlog"
    assert "the changelog" in fake.created_issue_bodies(CONSUMER_A, TITLE)[0]


def test_board_is_resolved_once_for_all_consumers(fake, tmp_path):
    _run(_env(tmp_path))

    assert len([c for c in fake.calls if c[:2] == ["project", "view"]]) == 1
    assert len([c for c in fake.calls if c[:2] == ["project", "field-list"]]) == 1


def test_a_reused_ticket_is_not_moved_on_the_board(fake, tmp_path):
    existing = fake.add_open_issue(CONSUMER_A, TITLE)

    exit_code = _run(_env(tmp_path, CONSUMERS=CONSUMER_A))

    assert exit_code == 0
    assert fake.board_items(existing) == [], "a human may have moved the ticket since; never re-place it"
    assert [c for c in fake.calls if c[:2] == ["issue", "create"]] == []


def test_a_second_run_creates_no_duplicate(fake, tmp_path):
    _run(_env(tmp_path))
    _run(_env(tmp_path))

    for repo in (CONSUMER_A, CONSUMER_B):
        assert len(fake.issues_matching(repo, TITLE)) == 1


def test_one_failing_consumer_fails_the_run_but_the_others_are_still_filed(fake, tmp_path, capsys):
    fake.fail_all_creates(CONSUMER_A)

    exit_code = _run(_env(tmp_path))

    assert exit_code == 1
    assert fake.issues_matching(CONSUMER_A, TITLE) == []
    assert len(fake.issues_matching(CONSUMER_B, TITLE)) == 1
    assert "::error::" in capsys.readouterr().out


def test_no_consumers_is_a_notice_and_files_nothing(fake, tmp_path, capsys):
    exit_code = _run(_env(tmp_path, CONSUMERS=""))

    assert exit_code == 0
    assert [c for c in fake.calls if c[:2] == ["issue", "create"]] == []
    assert "::notice::" in capsys.readouterr().out


def test_board_that_cannot_be_resolved_only_warns(fake, tmp_path, capsys):
    fake.set_fields([])

    exit_code = _run(_env(tmp_path))

    assert exit_code == 0
    assert len(fake.issues_matching(CONSUMER_A, TITLE)) == 1
    assert len(fake.issues_matching(CONSUMER_B, TITLE)) == 1
    assert fake.board_items(_url(fake, CONSUMER_A)) == []
    assert "::warning::" in capsys.readouterr().out
    assert len([c for c in fake.calls if c[:2] == ["project", "field-list"]]) == 1, (
        "an unresolvable board must not be re-resolved for every consumer"
    )


def test_board_item_add_failure_only_warns(fake, tmp_path, capsys):
    fake.fail_item_add()

    exit_code = _run(_env(tmp_path, CONSUMERS=CONSUMER_A))

    assert exit_code == 0
    assert len(fake.issues_matching(CONSUMER_A, TITLE)) == 1
    assert "::warning::" in capsys.readouterr().out


def test_empty_board_number_disables_the_board_placement(fake, tmp_path):
    exit_code = _run(_env(tmp_path, BOARD_NUMBER=""))

    assert exit_code == 0
    assert [c for c in fake.calls if c[0] == "project"] == []


def test_invalid_consumer_name_is_an_error_and_the_rest_still_run(fake, tmp_path, capsys):
    exit_code = _run(_env(tmp_path, CONSUMERS=f"not-a-repo\n{CONSUMER_B}"))

    assert exit_code == 1
    assert len(fake.issues_matching(CONSUMER_B, TITLE)) == 1
    assert "::error::" in capsys.readouterr().out


def test_consumers_accept_commas_newlines_and_comment_lines(fake, tmp_path):
    exit_code = _run(_env(tmp_path, CONSUMERS=f"# who pins this lib\n{CONSUMER_A}, {CONSUMER_B}\n"))

    assert exit_code == 0
    assert len(fake.issues_matching(CONSUMER_A, TITLE)) == 1
    assert len(fake.issues_matching(CONSUMER_B, TITLE)) == 1


def test_job_summary_lists_every_consumer(fake, tmp_path):
    fake.fail_all_creates(CONSUMER_B)
    env = _env(tmp_path)

    _run(env)

    summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
    assert TAG in summary
    assert CONSUMER_A in summary and _url(fake, CONSUMER_A) in summary
    assert CONSUMER_B in summary and "FAILED" in summary
