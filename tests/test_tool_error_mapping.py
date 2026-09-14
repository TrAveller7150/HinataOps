from hinataops.ops_mcp.adapters.ssh import SshCommandError
from hinataops.ops_mcp.toolsets.aoi_learn_judge.plugin import AoiLearnJudgePlugin


def test_plugin_maps_ssh_timeout_to_retryable_observation_error() -> None:
    error = AoiLearnJudgePlugin._observation_error(
        "Docker 判题运行时",
        SshCommandError("ssh_command_timeout", "SSH command timed out"),
    )

    assert error.kind == "ssh_command_timeout"
    assert error.retryable is True


def test_plugin_keeps_output_budget_error_non_retryable() -> None:
    error = AoiLearnJudgePlugin._observation_error(
        "Docker 判题运行时",
        SshCommandError("output_budget_exceeded", "SSH command exceeded configured output budget"),
    )

    assert error.kind == "output_budget_exceeded"
    assert error.retryable is False
