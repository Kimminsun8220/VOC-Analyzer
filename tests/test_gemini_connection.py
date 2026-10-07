from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from streamlit.testing.v1 import AppTest

from src import ai, config, gemini_connection

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


def test_cloud_key_requires_explicit_opt_in(tmp_path, monkeypatch):
    import streamlit as st

    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(st, "secrets", {"GEMINI_API_KEY": "cloud-example"})
    assert config.load_gemini_key() == ""
    monkeypatch.setattr(st, "secrets", {
        "ENABLE_CLOUD_SECRETS": True, "GEMINI_API_KEY": "cloud-example",
    })
    assert config.load_gemini_key() == "cloud-example"
    # Explicit local paths never inherit a hosted key.
    assert config.load_gemini_key(tmp_path / "missing.env") == ""
    config.ENV_PATH.write_text("GEMINI_API_KEY=local-example", encoding="utf-8")
    assert config.load_gemini_key() == "local-example"


@pytest.mark.parametrize("key", ["secret with spaces", "비밀키", 123])
def test_invalid_cloud_key_is_rejected_without_echo(tmp_path, monkeypatch, key):
    import streamlit as st

    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(st, "secrets", {
        "ENABLE_CLOUD_SECRETS": True, "GEMINI_API_KEY": key,
    })
    with pytest.raises(ValueError) as error:
        config.load_gemini_key()
    assert str(key) not in str(error.value)


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


def test_app_does_not_read_credentials_or_show_connection_controls_on_visit(monkeypatch):
    calls = []

    def load_key():
        calls.append("key-read")
        return "example-secret"

    monkeypatch.setattr(config, "load_gemini_key", load_key)
    monkeypatch.setattr(ai.genai, "Client", lambda *a, **k: pytest.fail("화면 조회는 API를 호출하면 안 된다"))
    app = AppTest.from_file(APP_PATH).run()
    assert calls == []
    assert "check_gemini" not in [button.key for button in app.button]
    assert "model" not in [field.key for field in app.text_input]
    assert "Gemini 설정" not in [heading.value for heading in app.subheader]
    assert not any(".env" in caption.value or "AI는 실행 버튼" in caption.value or "일관된 분류 기준" in caption.value for caption in app.caption)
    assert "example-secret" not in str(app)
    assert not app.exception


def test_missing_key_does_not_make_network_request(monkeypatch):
    monkeypatch.setattr(config, "load_gemini_key", lambda: "")
    monkeypatch.setattr(ai.genai, "Client", lambda *a, **k: pytest.fail("키가 없으면 API를 호출하면 안 된다"))
    app = AppTest.from_file(APP_PATH).run()
    assert not app.error
    app.radio(key="input_mode").set_value("직접 붙여넣기").run()
    app.text_area(key="voc_text").set_value("배송이 빠름").run()
    app.button(key="preview_button").click().run()
    app.button(key="save_input").click().run()
    app.button(key="generate_codebook").click().run()
    assert ".env" in app.error[0].value
    assert not app.exception
