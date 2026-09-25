"""The action definition and the ``ci.notify`` module must agree: every env
var the module reads has to be set by ``action.yml``, and the run line must
invoke the module the way the tests exercise it."""

from __future__ import annotations

import re
from pathlib import Path

ACTION_DIR = Path(__file__).resolve().parent.parent
ACTION_YML = (ACTION_DIR / "action.yml").read_text(encoding="utf-8")

# env var -> the action input it must be wired from
EXPECTED_WIRING = {
    "VERSION": "version",
    "SOURCE_REPO": "source_repo",
    "CONSUMERS": "consumers",
    "GH_TOKEN": "gh_token",
    "LABELS": "labels",
    "BOARD_OWNER": "board_owner",
    "BOARD_NUMBER": "board_number",
}


def test_every_env_var_is_wired_from_its_input():
    for env_name, input_name in EXPECTED_WIRING.items():
        assert re.search(rf"^\s+{env_name}: \$\{{\{{ inputs\.{input_name} \}}\}}\s*$", ACTION_YML, re.M), (
            f"{env_name} must be set from inputs.{input_name}"
        )


def test_required_env_of_the_module_is_covered_by_the_wiring():
    import ci.notify  # noqa: PLC0415

    assert set(ci.notify.REQUIRED_ENV) <= set(EXPECTED_WIRING)


def test_run_line_invokes_the_module_from_the_action_directory():
    assert "run: python3 -m ci.notify" in ACTION_YML
    assert "working-directory: ${{ github.action_path }}" in ACTION_YML


def test_label_wish_list_defaults_to_ai_generated_and_task_only():
    match = re.search(r"^  labels:\n(?:    .*\n)+?    default: '([^']*)'", ACTION_YML, re.M)
    assert match, "the labels input must declare a default"
    assert match.group(1) == "ai-generated,task"
    assert "dependencies" not in ACTION_YML


def test_the_only_credential_is_the_gh_token_input():
    assert not re.search(r"\$\{\{\s*secrets\.", ACTION_YML), (
        "a composite action gets its token through an input, never by reading a secret expression"
    )
