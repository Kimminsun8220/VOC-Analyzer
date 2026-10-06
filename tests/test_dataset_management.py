from pathlib import Path
import sqlite3
import time

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from src import ai as ai_module
from src.ai import PROMPT_VERSION
from src.ingestion import prepare_preview
from src.models import Code, CodingResult, Issue
from src.storage import Store


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def saved_bundle(store, name="관리할 자료"):
    frame = pd.DataFrame({"VOC": ["배송이 빠름", ""], "상품": ["샘플", "샘플"]})
    identifier = store.save_dataset(name, prepare_preview(frame, "VOC"), frame, "VOC", "파일 업로드", "분석 배경")
    codes = [Code(id="C1", category="배송", name="배송 속도", definition="배송의 빠르기", reason="검증")]
    first_book = store.save_codebook(identifier, codes, "분석 배경", "test-model", [], "confirmed")
    second_book = store.save_codebook(identifier, codes, "분석 배경", "test-model", [], "confirmed", first_book)
    result = CodingResult(voc_id="V0001", response_type="opinions",
                          issues=[Issue(code_id="C1", sentiment="긍정", evidence_text="배송이 빠름")])
    first_run = store.create_run(identifier, first_book, "test-model", PROMPT_VERSION)
    store.save_result(first_run, 0, "V0001", result)
    store.update_status(first_run, "completed")
    with store.connect() as db:
        store.insert_correction(db, store.run(first_run), "V0001", result.model_dump(), [],
                                result.model_dump(), "수정 이력 검증")
    second_run = store.create_run(identifier, second_book, "test-model", PROMPT_VERSION, first_run)
    store.save_result(second_run, 0, "V0001", result)
    store.update_status(second_run, "completed")
    for run in (first_run, second_run):
        store.save_group(run, "배송 묶음", ["C1"])
    return identifier, (first_book, second_book), (first_run, second_run)


def database_snapshot(store):
    with store.connect() as db:
        return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("datasets", "codebooks", "runs", "results", "corrections", "result_groups")}


def test_rename_persists_without_changing_inputs_or_analysis():
    store = Store()
    identifier, _, _ = saved_bundle(store)
    before = database_snapshot(store)
    store.rename_dataset(identifier, "  10월 배송 의견  ")
    fresh = Store(store.path)
    after = database_snapshot(fresh)
    expected_dataset = list(before["datasets"][0])
    expected_dataset[1] = "10월 배송 의견"
    assert after == {**before, "datasets": [tuple(expected_dataset)]}
    assert fresh.list_datasets()[0]["name"] == "10월 배송 의견"


@pytest.mark.parametrize("name", ["", "   ", "가" * 101])
def test_invalid_name_leaves_dataset_unchanged(name):
    store = Store()
    identifier, _, _ = saved_bundle(store)
    before = database_snapshot(store)
    with pytest.raises(ValueError, match="1~100자"):
        store.rename_dataset(identifier, name)
    assert database_snapshot(store) == before


def test_summary_and_delete_remove_only_the_selected_bundle():
    store = Store()
    target, _, _ = saved_bundle(store)
    other, other_books, other_runs = saved_bundle(store)  # 같은 이름도 ID로 구분한다.
    other_before = store.dataset(other), [store.codebook(book) for book in other_books], [store.run(run) for run in other_runs]
    summary = store.dataset_summary(target)
    assert (summary["record_count"], summary["codebook_count"], summary["run_count"],
            summary["correction_count"], summary["group_count"], summary["analysis_running"]) == (2, 2, 2, 1, 2, 0)
    store.delete_dataset(target)
    assert [row["id"] for row in store.list_datasets()] == [other]
    assert (store.dataset(other), [store.codebook(book) for book in other_books], [store.run(run) for run in other_runs]) == other_before
    assert {table: len(rows) for table, rows in database_snapshot(store).items()} == {
        "datasets": 1, "codebooks": 2, "runs": 2, "results": 2, "corrections": 1, "result_groups": 2}
    with store.connect() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_delete_failure_rolls_back_all_related_records():
    store = Store()
    target, _, _ = saved_bundle(store)
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_dataset_delete BEFORE DELETE ON datasets "
                   "BEGIN SELECT RAISE(ABORT, 'delete failed'); END")
    before = database_snapshot(store)
    with pytest.raises(sqlite3.IntegrityError, match="delete failed"):
        store.delete_dataset(target)
    assert database_snapshot(store) == before


def test_running_dataset_cannot_be_deleted_but_expired_run_can():
    store = Store()
    target, _, runs = saved_bundle(store)
    store.claim(runs[1])
    before = database_snapshot(store)
    assert store.dataset_summary(target)["analysis_running"]
    with pytest.raises(ValueError, match="진행 중"):
        store.delete_dataset(target)
    assert database_snapshot(store) == before
    with store.connect() as db:
        db.execute("UPDATE runs SET lease_until=? WHERE id=?", (time.time() - 1, runs[1]))
    store.delete_dataset(target)
    assert not any(database_snapshot(store).values())


@pytest.mark.parametrize("operation", ["dataset", "dataset_summary", "rename_dataset", "delete_dataset"])
def test_missing_dataset_returns_a_clear_error(operation):
    store = Store()
    args = ("missing", "새 이름") if operation == "rename_dataset" else ("missing",)
    with pytest.raises(ValueError, match="자료를 찾을 수 없습니다"):
        getattr(store, operation)(*args)


def no_ai(monkeypatch):
    monkeypatch.setattr(ai_module, "GeminiAI", lambda *args: pytest.fail("자료 관리에서 AI 호출 금지"))


def open_management(page="2. 분류 기준표"):
    app = AppTest.from_file(APP_PATH).run()
    app.radio(key="nav").set_value(page).run()
    return app


@pytest.mark.parametrize("page, title, choice_prefix, control_prefix", [
    ("2. 분류 기준표", "분류 기준표", "book_choice_", "codebook_select_"),
    ("3. 분류 결과", "분류 결과", "result_choice_", "result_choice_"),
])
def test_ui_picker_is_hidden_on_input_and_selects_analysis_from_page_top(monkeypatch, page, title, choice_prefix, control_prefix):
    no_ai(monkeypatch)
    store = Store()
    target, books, runs = saved_bundle(store)
    saved_bundle(store, "다른 자료")
    app = AppTest.from_file(APP_PATH).run()
    assert not any((button.key or "").startswith("dataset_") for button in app.button)
    assert "저장된 입력 자료" not in [item.value for item in app.markdown]
    app.radio(key="nav").set_value(page).run()
    assert app.main.header[0].value == title
    assert not app.sidebar.button
    assert any((button.key or "").startswith("dataset_select_") for button in app.main.button)
    nodes = list(app.main)
    picker_position = next(i for i, node in enumerate(nodes) if getattr(node, "key", None) == f"dataset_select_{target}")
    analysis_position = next(i for i, node in enumerate(nodes)
                             if (getattr(node, "key", None) or "").startswith(control_prefix))
    assert picker_position < analysis_position
    app.button(key=f"dataset_select_{target}").click().run()
    assert app.session_state.dataset_id == target
    expected = books if page == "2. 분류 기준표" else runs
    assert app.session_state[f"{choice_prefix}{target}"] in expected
    app.radio(key="nav").set_value("1. 입력").run()
    assert not any((button.key or "").startswith("dataset_") for button in app.button)
    app.radio(key="nav").set_value(page).run()
    assert app.session_state.dataset_id == target
    assert app.session_state[f"{choice_prefix}{target}"] in expected
    assert not app.exception and not app.error


def test_ui_inline_rename_and_immediate_delete_switch_to_remaining_dataset(monkeypatch):
    no_ai(monkeypatch)
    store = Store()
    target, books, runs = saved_bundle(store)
    other, _, _ = saved_bundle(store, "남겨둘 자료")
    app = open_management("3. 분류 결과")
    app.button(key=f"dataset_select_{target}").click().run()
    assert "입력 자료 관리" not in [expander.label for expander in app.expander]
    app.button(key=f"dataset_icon_edit_{target}").click().run()
    app.text_input(key=f"dataset_name_{target}").set_value("변경한 자료 이름")
    app.button(key=f"dataset_icon_save_{target}").click().run()
    assert store.dataset(target)["name"] == "변경한 자료 이름"
    assert app.session_state.dataset_id == target
    assert app.button(key=f"dataset_select_{target}").label == "변경한 자료 이름"
    app.button(key=f"dataset_icon_delete_{target}").click().run()
    assert not app.exception and not app.error
    assert [row["id"] for row in store.list_datasets()] == [other]
    assert app.session_state.dataset_id == other
    assert app.radio(key="nav").value == "3. 분류 결과"
    assert all(not any(identifier in key for identifier in (target, *books, *runs)) for key in app.session_state.keys())
    assert not any("삭제 확인" in button.label for button in app.button)
    fresh = open_management("3. 분류 결과")
    assert fresh.session_state.dataset_id == other and not fresh.exception


def test_ui_delete_last_dataset_returns_to_input_and_can_save_again(monkeypatch):
    no_ai(monkeypatch)
    store = Store()
    target, _, _ = saved_bundle(store)
    app = AppTest.from_file(APP_PATH).run()
    app.radio(key="nav").set_value("2. 분류 기준표").run()
    app.button(key=f"dataset_icon_delete_{target}").click().run()
    assert not store.list_datasets() and not app.exception and not app.error
    assert app.radio(key="nav").value == "1. 입력"
    assert not any((button.key or "").startswith("dataset_") for button in app.button)
    app.radio(key="input_mode").set_value("직접 붙여넣기").run()
    app.text_area(key="voc_text").set_value("배송이 빠름").run()
    app.button(key="preview_button").click().run()
    app.button(key="save_input").click().run()
    assert app.radio(key="nav").value == "2. 분류 기준표"
    assert len(store.list_datasets()) == 1 and not app.exception


def test_ui_manages_unselected_row_without_changing_current_analysis(monkeypatch):
    no_ai(monkeypatch)
    store = Store()
    target, _, _ = saved_bundle(store)
    other, _, _ = saved_bundle(store)  # Identical names must still act on the right ID.
    app = open_management("3. 분류 결과")
    assert app.button(key=f"dataset_select_{other}").label == "관리할 자료 1"
    assert app.button(key=f"dataset_select_{target}").label == "관리할 자료 2"
    app.button(key=f"dataset_select_{other}").click().run()
    selected_run = app.selectbox(key=f"result_choice_{other}").value
    metrics = [metric.value for metric in app.metric]
    app.button(key=f"dataset_icon_edit_{target}").click().run()
    app.text_input(key=f"dataset_name_{target}").set_value("다른 줄에서 수정")
    app.button(key=f"dataset_icon_save_{target}").click().run()
    assert store.dataset(target)["name"] == "다른 줄에서 수정"
    assert app.session_state.dataset_id == other
    app.button(key=f"dataset_icon_delete_{target}").click().run()
    assert app.session_state.dataset_id == other
    assert store.dataset(other)["name"] == "관리할 자료"
    assert not app.exception and not app.error
    assert app.selectbox(key=f"result_choice_{other}").value == selected_run
    assert [metric.value for metric in app.metric] == metrics


@pytest.mark.parametrize("name", ["", "   "])
def test_ui_bad_name_keeps_inline_editor_and_original_name(monkeypatch, name):
    no_ai(monkeypatch)
    store = Store()
    target, _, _ = saved_bundle(store)
    app = open_management()
    app.button(key=f"dataset_icon_edit_{target}").click().run()
    app.text_input(key=f"dataset_name_{target}").set_value(name)
    app.button(key=f"dataset_icon_save_{target}").click().run()
    assert store.dataset(target)["name"] == "관리할 자료"
    assert app.text_input(key=f"dataset_name_{target}").value == name
    assert any("1~100자" in error.value for error in app.error)
    assert not app.exception


def test_ui_selection_leaves_unsaved_edit_and_running_disables_delete(monkeypatch):
    no_ai(monkeypatch)
    store = Store()
    target, _, runs = saved_bundle(store)
    other, _, _ = saved_bundle(store, "다른 자료")
    app = open_management()
    app.button(key=f"dataset_icon_edit_{target}").click().run()
    app.text_input(key=f"dataset_name_{target}").set_value("아직 저장하지 않음")
    app.button(key=f"dataset_select_{other}").click().run()
    assert store.dataset(target)["name"] == "관리할 자료"
    assert f"dataset_name_{target}" not in [field.key for field in app.text_input]
    store.claim(runs[1])
    app.run()
    assert app.button(key=f"dataset_icon_delete_{target}").disabled
    assert not app.exception and not app.error


def test_ui_delete_race_preserves_data_and_reports_running_analysis(monkeypatch):
    no_ai(monkeypatch)
    store = Store()
    target, _, runs = saved_bundle(store)
    app = open_management()
    assert not app.button(key=f"dataset_icon_delete_{target}").disabled
    store.claim(runs[1])
    app.button(key=f"dataset_icon_delete_{target}").click().run()
    assert store.dataset(target)["name"] == "관리할 자료"
    assert any("진행 중" in error.value for error in app.error)
    assert app.button(key=f"dataset_icon_delete_{target}").disabled
    assert not app.exception
