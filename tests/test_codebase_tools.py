import json

import pytest

from app.task_context import current_workspace
from app.tool.codebase import ReadFiles


def _read_status(output: str) -> dict:
    status_line = next(
        line for line in output.splitlines() if line.startswith("READ_FILES_STATUS ")
    )
    return json.loads(status_line.removeprefix("READ_FILES_STATUS "))


@pytest.mark.asyncio
async def test_read_files_reports_exact_continuation_for_clipped_output(tmp_path):
    document = tmp_path / "paper.tex"
    document.write_text(
        "\n".join(f"line {line_no} " + "x" * 40 for line_no in range(1, 256))
    )
    workspace_token = current_workspace.set(str(tmp_path))
    try:
        first = await ReadFiles().execute(
            path="/workspace/paper.tex",
            start_line=125,
            max_chars_per_file=1000,
        )
        first_status = _read_status(first.output)

        assert first_status["start_line"] == 125
        assert first_status["end_line"] < 255
        assert first_status["total_lines"] == 255
        assert first_status["response_clipped"] is True
        assert first_status["next_start_line"] == first_status["end_line"] + 1
        assert first.metadata["next_start_line"] == first_status["next_start_line"]

        second = await ReadFiles().execute(
            path="/workspace/paper.tex",
            start_line=first_status["next_start_line"],
            max_chars_per_file=50000,
        )
        second_status = _read_status(second.output)

        assert second_status["start_line"] == first_status["next_start_line"]
        assert second_status["end_line"] == 255
        assert second_status["response_clipped"] is False
        assert second_status["next_start_line"] is None
    finally:
        current_workspace.reset(workspace_token)


@pytest.mark.asyncio
async def test_read_files_status_survives_the_character_limit(tmp_path):
    document = tmp_path / "long.txt"
    document.write_text("a" * 2000 + "\nnext line\n")
    workspace_token = current_workspace.set(str(tmp_path))
    try:
        result = await ReadFiles().execute(
            path="/workspace/long.txt", max_chars_per_file=1000
        )
    finally:
        current_workspace.reset(workspace_token)

    status = _read_status(result.output)
    assert "<line content clipped>" in result.output
    assert status["end_line"] == 1
    assert status["next_start_line"] == 2
