import json
import logging
import sys

import pytest

from forever_ha_bridge import cli


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    game = tmp_path / "game"
    (game / "WTF").mkdir(parents=True)
    data = {"home_assistant_url": "https://ha.example", "installations": [{"path": str(game)}]}
    file = tmp_path / "config.json"
    file.write_text(json.dumps(data))
    monkeypatch.delenv("FOREVER_HA_TOKEN", raising=False)
    return file, data


def test_missing_token_has_actionable_message(config_file):
    with pytest.raises(cli.ConfigurationError, match="--prompt-token"):
        cli.read_config(config_file[0])


@pytest.mark.parametrize("change,message", [
    ({"installations": [{"path": "/path/to/your/Forever/client"}]}, "does not exist"),
    ({"installations": [{}]}, "actual Forever"),
    ({"installations": "game"}, "list of objects"),
    ({"token_env": []}, "environment-variable name"),
    ({"home_assistant_url": "https://user:private-secret@ha.example"}, "base URL"),
    ({"home_assistant_url": "https://ha.example/?token=private-secret"}, "base URL"),
    ({"max_queue_records": 0}, "positive integers"),
])
def test_bad_config_is_explained_without_echoing_secrets(config_file, change, message):
    file, data = config_file
    data.update(change)
    file.write_text(json.dumps(data))
    with pytest.raises(cli.ConfigurationError, match=message) as error:
        cli.read_config(file, token="private-secret")
    assert "private-secret" not in str(error.value)


def test_wrong_game_folder_is_explained(config_file, tmp_path):
    file, data = config_file
    data["installations"][0]["path"] = str(tmp_path)
    file.write_text(json.dumps(data))
    with pytest.raises(cli.ConfigurationError, match="no WTF folder"):
        cli.read_config(file, token="private-secret")


def test_windows_json_error_does_not_echo_token(tmp_path):
    file = tmp_path / "config.json"
    file.write_text(r'{"path":"C:\Users\private-secret"}')
    with pytest.raises(cli.ConfigurationError, match="line 1, column") as error:
        cli.read_config(file)
    assert "private-secret" not in str(error.value)


def test_check_config_needs_no_token_and_writes_no_state(config_file, monkeypatch, caplog):
    file, _ = config_file
    monkeypatch.setattr(sys, "argv", ["forever-ha", "--config", str(file), "--check-config"])
    monkeypatch.setattr(cli, "Store", lambda *a, **k: pytest.fail("Configuration check wrote state"))
    with caplog.at_level(logging.INFO), pytest.raises(SystemExit) as exit_code:
        cli.main()
    assert exit_code.value.code == 0
    assert "Token is missing" in caplog.text
    assert "No export files yet" in caplog.text


def test_prompted_token_reaches_runtime_without_being_logged(config_file, monkeypatch, caplog):
    file, _ = config_file
    monkeypatch.setattr(sys, "argv", ["forever-ha", "--config", str(file), "--prompt-token", "--once"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "private-secret")
    async def fake_run(config, once):
        assert config["token"] == "private-secret" and once
        return 0
    monkeypatch.setattr(cli, "run", fake_run)
    with pytest.raises(SystemExit) as exit_code:
        cli.main()
    assert exit_code.value.code == 0
    assert "private-secret" not in caplog.text


def test_utf8_bom_config_is_supported(config_file):
    file, data = config_file
    file.write_text(json.dumps(data), encoding="utf-8-sig")
    assert cli.read_config(file, token="private-secret")["token"] == "private-secret"
