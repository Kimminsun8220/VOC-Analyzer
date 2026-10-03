from types import SimpleNamespace
import json

from google.genai import errors
import pytest

from src import ai
from src.models import Code, CodebookDraft


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


def test_code_definition_generation_uses_names_sources_and_strict_schema():
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text='{"definitions":[{"target_index":1,"definition":"영업소 청결 상태에 관한 의견"}]}')

    service = ai.GeminiAI.__new__(ai.GeminiAI)
    service.model = "test-model"
    service.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    code = Code(id="C1", category="종합 평가", name="구매 경험", definition="구매 과정과 영업소 상태", reason="test")
    targets = [{"target_index": 0, "category": "종합 평가", "name": "구매 경험", "definition": "사용자 기준"},
               {"target_index": 1, "category": "종합 평가", "name": "영업소 청결", "definition": ""}]
    records = [{"id": "V0001", "text": "영업소가 지저분했어요"}]
    result = service.code_definitions("split", [code], ["C1"], targets, records, "구매 설문")
    assert [(item.target_index, item.definition) for item in result.definitions] == [(1, "영업소 청결 상태에 관한 의견")]
    payload = json.loads(calls[0]["contents"].split("분석 데이터 JSON:\n", 1)[1])
    assert payload == {"operation": "split", "codes": [code.model_dump()], "source_ids": ["C1"],
                       "targets": targets, "records": records, "context": "구매 설문"}
    schema = calls[0]["config"].response_json_schema
    assert set(schema["properties"]) == {"definitions"}
    assert schema["$defs"]["CodeDefinition"]["additionalProperties"] is False
