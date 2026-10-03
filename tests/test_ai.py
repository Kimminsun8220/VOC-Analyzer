from types import SimpleNamespace

from google.genai import errors
import pytest

from src import ai
from src.models import CodebookDraft


def test_structured_request_and_transient_retry_do_not_expose_key(monkeypatch):
    calls = []
    def generate_content(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise errors.APIError(429, {"error": {"message": "private-secret", "code": 429}})
        return SimpleNamespace(text='{"codes":[]}')
    monkeypatch.setattr(ai.time, "sleep", lambda _: None)
    service = ai.GeminiAI.__new__(ai.GeminiAI)
    service.model = "test-model"
    service.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    result = service.generate("codebook", {"records": []}, CodebookDraft)
    assert result.codes == [] and len(calls) == 2
    assert calls[-1]["config"].response_mime_type == "application/json"
    assert "properties" in calls[-1]["config"].response_json_schema


def test_authentication_failure_is_not_retried_or_echoed(monkeypatch):
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise errors.APIError(403, {"error": {"message": "secret-key", "code": 403}})
    service = ai.GeminiAI.__new__(ai.GeminiAI)
    service.model = "test-model"
    service.client = SimpleNamespace(models=SimpleNamespace(generate_content=fail))
    with pytest.raises(ai.AIError) as error:
        service.generate("test", {}, CodebookDraft)
    assert "secret-key" not in str(error.value)
    assert len(calls) == 1
