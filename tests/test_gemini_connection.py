from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from streamlit.testing.v1 import AppTest

from src import config, gemini_connection

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_project_key_is_read_fresh_and_not_from_global_environment(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setenv("GEMINI_API_KEY", "unrelated-project")
    assert config.load_gemini_key(path) == ""
    path.write_text('GEMINI_API_KEY = "first-example"', encoding="utf-8")
    assert config.load_gemini_key(path) == "first-example"
    path.write_text('GEMINI_API_KEY = "second-example"', encoding="utf-8-sig")
    assert config.load_gemini_key(path) == "second-example"


def test_invalid_key_does_not_echo_secret(tmp_path):
    path = tmp_path / ".env"
    path.write_text('GEMINI_API_KEY="leaked-example-secret with space"', encoding="utf-8")
    with pytest.raises(ValueError) as error:
        config.load_gemini_key(path)
    assert "leaked-example-secret" not in str(error.value)


@pytest.mark.parametrize("line", ['GEMINI_API_KEY=example-key', 'GEMINI_API_KEY="example-key"'])
def test_dotenv_accepts_quoted_and_unquoted_key(tmp_path, line):
    path = tmp_path / ".env"
    path.write_text(line, encoding="utf-8")
    assert config.load_gemini_key(path) == "example-key"


def test_legacy_toml_is_not_used_when_env_is_missing(tmp_path, monkeypatch):
    legacy = tmp_path / ".streamlit"
    legacy.mkdir()
    (legacy / "secrets.toml").write_text('GEMINI_API_KEY="old-project-key"', encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    assert config.load_gemini_key() == ""


def test_connection_uses_header_and_does_not_send_voc(monkeypatch):
    def fake_open(request, timeout):
        assert request.full_url == gemini_connection.MODELS_URL
        assert request.get_header("X-goog-api-key") == "example-key"
        assert request.data is None
        assert "example-key" not in request.full_url
        assert timeout == 15
        return BytesIO(b'{"models":[{"name":"models/example"}]}')

    monkeypatch.setattr(gemini_connection, "urlopen", fake_open)
    gemini_connection.check_gemini_connection("example-key")


@pytest.mark.parametrize("status", [400, 403, 429, 500])
def test_service_errors_are_masked(monkeypatch, status):
    def fail(*args, **kwargs):
        raise HTTPError("https://example.test", status, "example-secret", {}, None)

    monkeypatch.setattr(gemini_connection, "urlopen", fail)
    with pytest.raises(ValueError) as error:
        gemini_connection.check_gemini_connection("example-secret")
    assert "example-secret" not in str(error.value)


def test_timeout_has_readable_error(monkeypatch):
    def fail(*args, **kwargs):
        raise URLError("private-details")

    monkeypatch.setattr(gemini_connection, "urlopen", fail)
    with pytest.raises(ValueError, match="인터넷 연결"):
        gemini_connection.check_gemini_connection("example-key")


def test_app_never_connects_automatically_and_shows_no_key(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "load_gemini_key", lambda: "example-secret")
    monkeypatch.setattr(gemini_connection, "check_gemini_connection", lambda key: calls.append(key))
    app = AppTest.from_file(APP_PATH).run()
    assert calls == []
    app.button(key="check_gemini").click().run()
    assert calls == ["example-secret"]
    assert "키 인증" in app.success[0].value
    assert "example-secret" not in str(app)
    assert not app.exception


def test_missing_key_does_not_make_network_request(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "load_gemini_key", lambda: "")
    monkeypatch.setattr(gemini_connection, "check_gemini_connection", lambda key: calls.append(key))
    app = AppTest.from_file(APP_PATH).run()
    app.button(key="check_gemini").click().run()
    assert calls == []
    assert ".env" in app.warning[0].value
    assert not app.exception
