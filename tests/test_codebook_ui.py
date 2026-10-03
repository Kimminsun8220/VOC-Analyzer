from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from src import ai as ai_module
from src.ai import AIError, GeminiAI
from src.ingestion import prepare_preview
from src.models import Code, CodeDefinition, CodeDefinitions
from src.storage import Store


@pytest.fixture
def split_screen(monkeypatch):
    monkeypatch.setattr(ai_module, "GeminiAI", lambda *args: pytest.fail("분류 기준표 수정은 AI 호출 금지"))
    store = Store()
    frame = pd.DataFrame({"VOC": ["구매 과정은 좋았지만 영업소가 지저분했어요"], "내부 메모": ["AI 입력에서 제외"]})
    dataset = store.save_dataset("분리 검증", prepare_preview(frame, "VOC"), frame, "VOC", "test", "구매 설문")
    codes = [
        Code(id="C1", category="종합 평가", name="구매 경험", definition="구매 과정과 영업소 상태에 관한 의견", reason="test"),
        Code(id="C2", category="차량", name="주행 성능", definition="차량 주행 성능에 관한 의견", reason="test"),
        Code(id="C3", category="종합 평가", name="방문 경험", definition="방문 절차에 관한 의견", reason="test"),
    ]
    book = store.save_codebook(dataset, codes, "구매 설문", "test-model", ["V0001"], "confirmed")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.radio(key="nav").set_value("2. 분류 기준표").run()
    prefix = f"revise_{book}"
    app.radio(key=prefix + "_mode").set_value("코드 분리").run()
    return store, dataset, book, app, prefix


def set_split_rows(app, prefix, first, second=None):
    app.session_state[f"{prefix}_C1_split_editor"] = {
        "edited_rows": {0: first, 1: second or {"name": "영업소 청결", "definition": "영업소 청결 상태에 관한 의견"}},
        "deleted_rows": [], "added_rows": [],
    }


@pytest.mark.parametrize("first, message", [
    ({"name": "", "definition": "구매 과정에 관한 의견"}, "1행: 새 세부분류 이름을 입력해주세요."),
    ({"name": "", "definition": ""}, "1행: 새 세부분류 이름을 입력해주세요."),
    ({"name": "가" * 81, "definition": "구매 과정에 관한 의견"}, "세부분류 이름은 80자 이하로 입력해주세요."),
    ({"name": "구매 경험", "definition": "가" * 1501}, "분류 기준은 1500자 이하로 입력해주세요."),
])
def test_split_validation_names_only_the_invalid_visible_fields(split_screen, first, message):
    store, dataset, book, app, prefix = split_screen
    before = deepcopy(store.codebook(book))
    set_split_rows(app, prefix, first)
    app.button(key=prefix + "_split").click().run()
    assert not app.exception
    assert [error.value for error in app.error] == [message]
    assert len(store.list_codebooks(dataset)) == 1
    assert store.codebook(book) == before


def test_split_identifies_incomplete_second_row(split_screen):
    store, dataset, _, app, prefix = split_screen
    set_split_rows(app, prefix, {"name": "구매 경험", "definition": "구매 과정에 관한 의견"},
                   {"name": "", "definition": ""})
    app.button(key=prefix + "_split").click().run()
    assert not app.exception
    assert [error.value for error in app.error] == ["2행: 새 세부분류 이름을 입력해주세요."]
    assert len(store.list_codebooks(dataset)) == 1


def test_split_retry_saves_inherited_category_and_preserves_original(split_screen):
    store, dataset, book, app, prefix = split_screen
    before = deepcopy(store.codebook(book))
    assert any("대분류: 종합 평가" in caption.value for caption in app.caption)
    set_split_rows(app, prefix, {"name": "", "definition": ""})
    app.button(key=prefix + "_split").click().run()
    assert app.error
    set_split_rows(app, prefix, {"name": "구매 경험", "definition": "구매 과정의 만족도에 관한 의견"})
    app.button(key=prefix + "_split").click().run()
    assert not app.exception and not app.error
    versions = store.list_codebooks(dataset)
    assert len(versions) == 2
    revised = store.codebook(versions[0]["id"])
    assert revised["parent_id"] == book and revised["status"] == "confirmed"
    assert revised["context"] == before["context"] and revised["sample_ids"] == before["sample_ids"]
    assert [(code.category, code.name, code.definition) for code in revised["codes"]] == [
        ("차량", "주행 성능", "차량 주행 성능에 관한 의견"),
        ("종합 평가", "방문 경험", "방문 절차에 관한 의견"),
        ("종합 평가", "구매 경험", "구매 과정의 만족도에 관한 의견"),
        ("종합 평가", "영업소 청결", "영업소 청결 상태에 관한 의견"),
    ]
    assert store.codebook(book) == before
    assert app.selectbox(key=f"book_choice_{dataset}").value == revised["id"]


class DefinitionAI:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.failure = None
        self.response = None

    def code_definitions(self, operation, codes, source_ids, targets, records, context):
        self.calls.append(deepcopy({"operation": operation, "codes": codes, "source_ids": source_ids,
                                   "targets": targets, "records": records, "context": context}))
        if self.failure:
            raise self.failure
        if self.response is not None:
            return self.response
        definitions = {"구매 경험": "구매 과정의 만족도에 관한 의견", "영업소 청결": "영업소 청결 상태에 관한 의견",
                       "구매 및 방문 경험": "구매 과정과 방문 절차 전반에 관한 의견"}
        return CodeDefinitions(definitions=[CodeDefinition(target_index=row["target_index"], definition=definitions[row["name"]])
                                           for row in targets if not row["definition"]])

    def close(self):
        self.closed += 1


@pytest.fixture
def definition_ai(split_screen, monkeypatch):
    service = DefinitionAI()
    monkeypatch.setattr(ai_module, "GeminiAI", lambda *args: service)
    return service


@pytest.mark.parametrize("blank", ["", None, "   "])
def test_split_names_only_generates_definitions_and_preserves_source(split_screen, definition_ai, blank):
    store, dataset, book, app, prefix = split_screen
    before = deepcopy(store.codebook(book))
    set_split_rows(app, prefix, {"name": "구매 경험", "definition": blank}, {"name": "영업소 청결", "definition": ""})
    app.button(key=prefix + "_split").click().run()
    assert not app.exception and not app.error, [item.value for item in app.error]
    revised = store.codebook(store.list_codebooks(dataset)[0]["id"])
    additions = revised["codes"][-2:]
    assert [(c.category, c.name, c.definition) for c in additions] == [
        ("종합 평가", "구매 경험", "구매 과정의 만족도에 관한 의견"),
        ("종합 평가", "영업소 청결", "영업소 청결 상태에 관한 의견"),
    ]
    assert all("AI 분류 기준" in c.reason for c in additions)
    assert set(revised["changes"][0]["definition_sources"].values()) == {"ai"}
    assert store.codebook(book) == before and len(store.list_codebooks(dataset)) == 2
    assert len(definition_ai.calls) == 1 and definition_ai.closed == 1
    payload = definition_ai.calls[0]
    assert payload["operation"] == "split" and payload["source_ids"] == ["C1"]
    assert payload["context"] == before["context"]
    assert payload["codes"] == before["codes"]
    assert all(set(row) == {"id", "text"} for row in payload["records"])
    assert app.selectbox(key=f"book_choice_{dataset}").value == revised["id"]


def test_split_fills_only_missing_definition(split_screen, definition_ai):
    store, dataset, _, app, prefix = split_screen
    manual = "사용자가 직접 적은 구매 절차의 만족 기준"
    set_split_rows(app, prefix, {"name": "구매 경험", "definition": manual}, {"name": "영업소 청결", "definition": ""})
    app.button(key=prefix + "_split").click().run()
    assert not app.exception and not app.error
    revised = store.codebook(store.list_codebooks(dataset)[0]["id"])
    assert revised["codes"][-2].definition == manual
    assert list(revised["changes"][0]["definition_sources"].values()) == ["user", "ai"]
    assert definition_ai.calls[0]["targets"][0]["definition"] == manual


def merge_screen(app, prefix):
    app.radio(key=prefix + "_mode").set_value("같은 의미 코드 통합").run()
    app.multiselect(key=prefix + "_merge_ids").set_value(["C1", "C3"]).run()
    app.text_input(key=prefix + "_merge_name").set_value("구매 및 방문 경험")
    assert not app.button(key=prefix + "_merge").disabled
    assert not any(item.label == "정의와 원문을 확인했고, 같은 의미의 코드입니다" for item in app.checkbox)


def test_merge_names_only_saves_without_checkbox(split_screen, definition_ai):
    store, dataset, book, app, prefix = split_screen
    before = deepcopy(store.codebook(book))
    merge_screen(app, prefix)
    app.button(key=prefix + "_merge").click().run()
    assert not app.exception and not app.error
    revised = store.codebook(store.list_codebooks(dataset)[0]["id"])
    merged = revised["codes"][-1]
    assert (merged.category, merged.name, merged.definition) == (
        "종합 평가", "구매 및 방문 경험", "구매 과정과 방문 절차 전반에 관한 의견")
    assert revised["changes"][0]["mapping"] == {"C1": merged.id, "C3": merged.id}
    assert revised["changes"][0]["definition_source"] == "ai"
    assert definition_ai.calls[0]["operation"] == "merge"
    assert definition_ai.calls[0]["source_ids"] == ["C1", "C3"]
    assert store.codebook(book) == before and definition_ai.closed == 1


def test_manual_merge_saves_without_ai_or_checkbox(split_screen):
    store, dataset, _, app, prefix = split_screen
    merge_screen(app, prefix)
    app.text_area(key=prefix + "_merge_definition").set_value("직접 입력한 구매 및 방문 절차 기준")
    app.button(key=prefix + "_merge").click().run()
    assert not app.exception and not app.error
    revised = store.codebook(store.list_codebooks(dataset)[0]["id"])
    assert revised["codes"][-1].definition == "직접 입력한 구매 및 방문 절차 기준"
    assert revised["changes"][0]["definition_source"] == "user"


def test_ai_failure_preserves_inputs_and_version_then_retry_saves(split_screen, definition_ai):
    store, dataset, book, app, prefix = split_screen
    before = deepcopy(store.codebook(book))
    definition_ai.failure = AIError("Gemini 연결 실패")
    set_split_rows(app, prefix, {"name": "구매 경험", "definition": ""}, {"name": "영업소 청결", "definition": ""})
    app.button(key=prefix + "_split").click().run()
    assert not app.exception and [item.value for item in app.error] == ["Gemini 연결 실패"]
    assert len(store.list_codebooks(dataset)) == 1 and store.codebook(book) == before
    assert app.session_state[f"{prefix}_C1_split_editor"]["edited_rows"][0]["name"] == "구매 경험"
    retained = deepcopy(app.session_state[f"{prefix}_C1_split_editor"])
    definition_ai.failure = None
    # AppTest는 data_editor의 브라우저 값을 재전송하지 않으므로 보존된 편집 상태를 전달한다.
    app.session_state[f"{prefix}_C1_split_editor"] = retained
    app.button(key=prefix + "_split").click().run()
    assert not app.exception and not app.error, [item.value for item in app.error]
    assert len(store.list_codebooks(dataset)) == 2 and definition_ai.closed == 2


@pytest.mark.parametrize("response", [
    {"definitions": []},
    {"definitions": [{"target_index": 0, "definition": "생성 기준"}, {"target_index": 0, "definition": "중복 대상"}]},
    {"definitions": [{"target_index": 1, "definition": "사용자 기준 덮어쓰기 시도"}]},
    {"definitions": [{"target_index": 0, "definition": " "}]},
    {"definitions": [{"target_index": 0, "definition": "직접 입력한 영업소 기준"}]},
    {"definitions": [{"target_index": 0, "definition": "생성 기준", "name": "이름 변경 시도"}]},
])
def test_invalid_ai_definitions_never_save_or_overwrite_manual_values(split_screen, definition_ai, response):
    store, dataset, book, app, prefix = split_screen
    before = deepcopy(store.codebook(book))
    definition_ai.response = response
    set_split_rows(app, prefix, {"name": "구매 경험", "definition": ""},
                   {"name": "영업소 청결", "definition": "직접 입력한 영업소 기준"})
    app.button(key=prefix + "_split").click().run()
    assert not app.exception and app.error
    assert len(store.list_codebooks(dataset)) == 1 and store.codebook(book) == before
    assert app.session_state[f"{prefix}_C1_split_editor"]["edited_rows"][1]["definition"] == "직접 입력한 영업소 기준"
    assert definition_ai.closed == 1


def test_missing_key_leaves_name_only_inputs_available_for_retry(split_screen, monkeypatch):
    store, dataset, _, app, prefix = split_screen
    monkeypatch.setattr(ai_module, "GeminiAI", GeminiAI)
    monkeypatch.setattr("src.config.load_gemini_key", lambda: "")
    set_split_rows(app, prefix, {"name": "구매 경험", "definition": ""})
    app.button(key=prefix + "_split").click().run()
    assert not app.exception
    assert [item.value for item in app.error] == [".env에 GEMINI_API_KEY를 먼저 입력해주세요."]
    assert len(store.list_codebooks(dataset)) == 1
    assert app.session_state[f"{prefix}_C1_split_editor"]["edited_rows"][0]["name"] == "구매 경험"
