import pytest

from hinataops.evaluation_runner import (
    DEFAULT_LLM_CONFIG_PATH,
    DEFAULT_MCP_URL,
    DEEPSEEK_API_KEY_ENV,
    _load_api_key_from_file,
    _parse_args,
    _require_api_key,
)
from hinataops import evaluation_runner


def test_runner_uses_default_local_mcp_url() -> None:
    assert _parse_args([]).mcp_url == DEFAULT_MCP_URL
    assert _parse_args([]).llm_config == DEFAULT_LLM_CONFIG_PATH
    assert _parse_args([]).output_file is None


def test_runner_requires_api_key_from_environment_or_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv(DEEPSEEK_API_KEY_ENV, raising=False)

    with pytest.raises(ValueError, match="未找到 LLM"):
        _require_api_key(tmp_path / "missing.toml")


def test_runner_allows_environment_to_override_local_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEEPSEEK_API_KEY_ENV, "test-key")

    assert _require_api_key(DEFAULT_LLM_CONFIG_PATH) == "test-key"


def test_runner_reads_api_key_from_ignored_local_toml(tmp_path) -> None:
    config_path = tmp_path / "llm.local.toml"
    config_path.write_text('[deepseek]\napi_key = "test-key"\n', encoding="utf-8")

    assert _load_api_key_from_file(config_path) == "test-key"


def test_runner_configures_utf8_for_console_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStream:
        """模拟支持 reconfigure 的标准输出流。"""

        def __init__(self) -> None:
            self.encodings: list[str] = []

        def reconfigure(self, *, encoding: str) -> None:
            self.encodings.append(encoding)

    stdout = FakeStream()
    stderr = FakeStream()
    monkeypatch.setattr(evaluation_runner.sys, "stdout", stdout)
    monkeypatch.setattr(evaluation_runner.sys, "stderr", stderr)

    evaluation_runner._configure_utf8_output()

    assert stdout.encodings == ["utf-8"]
    assert stderr.encodings == ["utf-8"]


def test_runner_writes_result_as_utf8_without_a_powershell_pipeline(tmp_path, capsys) -> None:
    output_file = tmp_path / "nested" / "result.json"

    evaluation_runner._write_result('{"conclusion":"中文"}', output_file)

    assert output_file.read_text(encoding="utf-8") == '{"conclusion":"中文"}\n'
    assert capsys.readouterr().out == '{"conclusion":"中文"}\n'
