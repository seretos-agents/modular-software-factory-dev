"""Layer 2 (#243 generation 2, R5-adjacent): proves ``ci.bump_ticket`` /
``ci.board`` work end to end through a *real* subprocess boundary --
genuine ``os.environ`` reading (not a dict passed straight into ``run()``),
a genuine ``$GITHUB_OUTPUT`` file write, and genuine exit-code propagation
via ``python -m ci.<module>``, exactly how the composite actions' rewired
``run:`` lines invoke them.

Layer 1 (``tests/test_bump_ticket.py``, ``tests/test_board_backlog.py``)
already covers the full behavioural matrix by monkeypatching
``ci.gh.run_gh`` in-process; this file exists only to prove the
``python -m`` entry point itself is wired correctly end to end -- one happy
path each, driven by ``tests/fake_gh/executable.py`` standing in for `gh`
on ``PATH`` via a small ``gh``/``gh.cmd`` shim (a real subprocess needs a
real executable on ``PATH`` -- ``shutil.which`` can't resolve an in-process
Python object).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE_GH_EXECUTABLE = REPO_ROOT / "tests" / "fake_gh" / "executable.py"


def _install_fake_gh(bin_dir: Path) -> None:
    """Write a ``gh``/``gh.cmd`` shim into ``bin_dir`` that just execs
    ``tests/fake_gh/executable.py`` under the current interpreter -- mirrors
    the pattern ``tests/test_ci_gh_discipline.py`` already uses for its own
    fake ``gh`` executables (``.cmd`` on Windows, a chmod+x shell script on
    POSIX), since this repo's CI matrix includes windows-latest."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        script = bin_dir / "gh.cmd"
        script.write_text(f'@echo off\r\n"{sys.executable}" "{FAKE_GH_EXECUTABLE}" %*\r\n')
    else:
        script = bin_dir / "gh"
        script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_GH_EXECUTABLE}" "$@"\n', newline="\n")
        mode = script.stat().st_mode
        script.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_module(module: str, env: dict[str, str], tmp_path: Path) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "fakebin"
    _install_fake_gh(bin_dir)

    child_env = dict(os.environ)
    child_env.update(env)
    child_env["PATH"] = os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")])

    return subprocess.run(
        [sys.executable, "-m", module],
        capture_output=True,
        text=True,
        env=child_env,
        cwd=str(REPO_ROOT),
        timeout=60,
    )


def test_bump_ticket_entrypoint_runs_end_to_end_through_a_real_subprocess(tmp_path):
    github_output = tmp_path / "github_output.txt"
    github_output.write_text("", encoding="utf-8")
    expected_issue_url = "https://github.com/seretos-agents/agent-project-issues/issues/123"

    env = {
        "VERSION": "0.2.0",
        "SOURCE_REPO": "seretos-agents/lib-python-projects",
        "CONSUMER_REPO": "seretos-agents/agent-project-issues",
        "GH_TOKEN": "fake-token",
        "GITHUB_OUTPUT": str(github_output),
        "FAKE_GH_ISSUES_PAGE_JSON": "[]",
        "FAKE_GH_ISSUE_URL": expected_issue_url,
        "FAKE_GH_RELEASE_BODY": "some real end-to-end release notes",
    }

    result = _run_module("ci.bump_ticket", env, tmp_path)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    outputs: dict[str, str] = {}
    for line in github_output.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            outputs[k] = v
    assert outputs.get("issue_url") == expected_issue_url, f"outputs={outputs!r}"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the gh.cmd shim cannot pass the '&' in the paginated label listing through cmd.exe; "
    "the behaviour is covered in-process by test_notify.py and runs here on the ubuntu CI",
)
def test_notify_entrypoint_files_and_places_a_ticket_through_a_real_subprocess(tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")

    env = {
        "VERSION": "0.2.0",
        "SOURCE_REPO": "seretos-agents/lib-python-projects",
        "CONSUMERS": "seretos-agents/agent-project-issues",
        "GH_TOKEN": "fake-token",
        "LABELS": "ai-generated,task",
        "BOARD_OWNER": "seretos-agents",
        "BOARD_NUMBER": "2",
        "GITHUB_STEP_SUMMARY": str(summary),
        "FAKE_GH_LABELS_PAGE_JSON": '[{"name": "task"}]',
        "FAKE_GH_ISSUE_URL": "https://github.com/seretos-agents/agent-project-issues/issues/123",
    }

    result = _run_module("ci.notify", env, tmp_path)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    text = summary.read_text(encoding="utf-8")
    assert "https://github.com/seretos-agents/agent-project-issues/issues/123" in text
    assert "labels: task" in text and "board: Backlog" in text
    assert "::warning::" in result.stdout, "the missing 'ai-generated' label must be reported"


def test_board_entrypoint_runs_end_to_end_through_a_real_subprocess(tmp_path):
    env = {
        "ISSUE_URL": "https://github.com/seretos-agents/agent-project-issues/issues/42",
        "BOARD_OWNER": "seretos-agents",
        "BOARD_NUMBER": "2",
        "GH_TOKEN": "fake-token",
    }

    result = _run_module("ci.board", env, tmp_path)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
