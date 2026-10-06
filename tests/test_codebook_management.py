from copy import deepcopy
from pathlib import Path
import sqlite3
import time

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from src import ai as ai_module
from src.codebook_changes import code_mapping
from src.ingestion import prepare_preview
from src.models import Code, CodingResult, Issue
from src.results import result_tables
from src.storage import Store


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def bundle(store):
    frame = pd.DataFrame({"VOC": ["배송이 빨라요"]})
    dataset = store.save_dataset("기준표 관리 검증", prepare_preview(frame, "VOC"), frame, "VOC", "test", "")
    codes = [Code(id="C1", category="배송", name="속도", definition="배송 속도", reason="검증")]
    first = store.save_codebook(dataset, codes, "", "test", [], "confirmed")
    second = store.save_codebook(dataset, codes, "", "test", [], "confirmed", first)
    run = store.create_run(dataset, second, "test", "test")
    result = CodingResult(voc_id="V0001", response_type="opinions",
                          issues=[Issue(code_id="C1", sentiment="긍정", evidence_text="배송이 빨라요")])
    store.save_result(run, 0, "V0001", result)
    store.update_status(run, "completed")
    store.save_group(run, "배송", ["C1"])
    return dataset, first, second, run


def screen(monkeypatch):
    monkeypatch.setattr(ai_module, "GeminiAI", lambda *args: pytest.fail("기준표 관리에서 AI 호출 금지"))
    store = Store()
    dataset, first, second, run = bundle(store)
    app = AppTest.from_file(APP_PATH).run()
    app.radio(key="nav").set_value("2. 분류 기준표").run()
    return store, dataset, first, second, run, app


def test_legacy_database_migration_preserves_books_and_results():
    store = Store()
    dataset, first, second, run = bundle(store)
    before = result_tables(store, run)
    with store.connect() as db:
        db.execute("ALTER TABLE codebooks DROP COLUMN name")
        db.execute("ALTER TABLE codebooks DROP COLUMN deleted_at")
    migrated = Store(store.path)
    assert [row["id"] for row in migrated.list_codebooks(dataset)] == [second, first]
    assert migrated.codebook(second)["name"] == ""
    assert migrated.codebook(second)["parent_id"] == first
    for expected, actual in zip(before, result_tables(migrated, run)):
        pd.testing.assert_frame_equal(expected, actual)
    migrated.rename_codebook(second, "이전 자료 이름")
    migrated.delete_codebook(second)
    third = migrated.save_codebook(dataset, migrated.codebook(first)["codes"], "", "test", [], "confirmed")
    assert migrated.codebook(third)["version"] == 3


def test_rename_persists_only_the_name_and_does_not_create_a_version():
    store = Store()
    dataset, _, second, run = bundle(store)
    before = deepcopy(store.codebook(second))
    store.rename_codebook(second, "  10월 배송 기준  ")
    fresh = Store(store.path)
    assert fresh.codebook(second) == {**before, "name": "10월 배송 기준"}
    assert len(fresh.list_codebooks(dataset)) == 2
    assert fresh.run(run)["codebook_id"] == second


@pytest.mark.parametrize("name", ["", "   ", "가" * 101])
def test_invalid_names_leave_the_book_unchanged(name):
    store = Store()
    _, _, second, _ = bundle(store)
    before = store.codebook(second)
    with pytest.raises(ValueError, match="1~100자"):
        store.rename_codebook(second, name)
    assert store.codebook(second) == before


def test_delete_preserves_existing_results_groups_and_version_lineage():
    store = Store()
    dataset, first, second, run = bundle(store)
    third = store.save_codebook(dataset, store.codebook(second)["codes"], "", "test", [], "confirmed", second)
    before = result_tables(store, run)
    with store.connect() as db:
        references = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
                      for table in ("runs", "results", "corrections", "result_groups")}
    store.delete_codebook(second)
    fresh = Store(store.path)
    assert [row["id"] for row in fresh.list_codebooks(dataset)] == [third, first]
    assert fresh.codebook(second)["deleted_at"]
    assert code_mapping(fresh, first, third) == {"C1": "C1"}
    for expected, actual in zip(before, result_tables(fresh, run)):
        pd.testing.assert_frame_equal(expected, actual)
    with fresh.connect() as db:
        assert references == {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in references}
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    with pytest.raises(ValueError):
        fresh.create_run(dataset, second, "test", "test")
    with pytest.raises(ValueError):
        fresh.save_codebook(dataset, fresh.codebook(second)["codes"], "", "test", [], "confirmed", second)
    fresh.delete_dataset(dataset)
    assert not fresh.list_codebooks(dataset, include_deleted=True)


def test_running_analysis_blocks_delete_and_database_failure_rolls_back():
    store = Store()
    _, _, second, run = bundle(store)
    store.claim(run)
    before = store.codebook(second)
    with pytest.raises(ValueError, match="진행 중"):
        store.delete_codebook(second)
    assert store.codebook(second) == before
    with store.connect() as db:
        db.execute("UPDATE runs SET lease_until=? WHERE id=?", (time.time() - 1, run))
        db.execute("CREATE TRIGGER reject_book_delete BEFORE UPDATE OF deleted_at ON codebooks "
                   "BEGIN SELECT RAISE(ABORT, 'delete failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="delete failed"):
        store.delete_codebook(second)
    assert store.codebook(second) == before


def test_ui_rename_delete_selection_and_last_delete_preserve_results(monkeypatch):
    store, dataset, first, second, run, app = screen(monkeypatch)
    app.button(key=f"codebook_icon_edit_{second}").click().run()
    app.text_input(key=f"codebook_name_{second}").set_value("가" * 100)
    app.button(key=f"codebook_icon_save_{second}").click().run()
    assert store.codebook(second)["name"] == "가" * 100
    assert app.session_state[f"book_choice_{dataset}"] == second
    assert "가" * 100 in app.button(key=f"codebook_select_{second}").label
    assert not app.exception and not app.error
    app.button(key=f"codebook_icon_delete_{second}").click().run()
    assert app.session_state[f"book_choice_{dataset}"] == first
    assert all(second not in key for key in app.session_state.keys())
    app.button(key=f"codebook_icon_delete_{first}").click().run()
    assert not store.list_codebooks(dataset)
    assert f"book_choice_{dataset}" not in app.session_state.keys()
    assert app.button(key="generate_codebook")
    assert not app.exception and not app.error
    app.radio(key="nav").set_value("3. 분류 결과").run()
    assert app.selectbox(key=f"result_choice_{dataset}").value == run
    assert not app.exception and not app.error
    fresh = AppTest.from_file(APP_PATH).run()
    fresh.radio(key="nav").set_value("2. 분류 기준표").run()
    assert f"book_choice_{dataset}" not in fresh.session_state.keys()
    assert not fresh.exception
    store.delete_dataset(dataset)
    assert not store.list_datasets()


def test_ui_invalid_name_retry_and_running_delete_disabled(monkeypatch):
    store, dataset, _, second, run, app = screen(monkeypatch)
    app.button(key=f"codebook_icon_edit_{second}").click().run()
    app.text_input(key=f"codebook_name_{second}").set_value("   ")
    app.button(key=f"codebook_icon_save_{second}").click().run()
    assert any("1~100자" in error.value for error in app.error)
    assert store.codebook(second)["name"] == ""
    assert app.text_input(key=f"codebook_name_{second}").value == "   "
    app.text_input(key=f"codebook_name_{second}").set_value("재시도")
    app.button(key=f"codebook_icon_save_{second}").click().run()
    assert not app.error and not app.exception
    store.claim(run)
    app.run()
    assert app.button(key=f"codebook_icon_delete_{second}").disabled
    assert app.session_state[f"book_choice_{dataset}"] == second


def test_ui_delete_race_and_stale_selection(monkeypatch):
    store, dataset, first, second, run, app = screen(monkeypatch)
    store.claim(run)
    app.button(key=f"codebook_icon_delete_{second}").click().run()
    assert any("진행 중" in error.value for error in app.error)
    assert len(store.list_codebooks(dataset)) == 2 and not app.exception
    store.update_status(run, "completed")
    store.delete_codebook(second)
    app.run()
    assert app.session_state[f"book_choice_{dataset}"] == first
    assert not app.exception


def test_ui_manages_an_unselected_row_without_changing_current_draft(monkeypatch):
    store, dataset, first, second, _, app = screen(monkeypatch)
    draft_key = f"revise_{second}_draft"
    draft = deepcopy(app.session_state[draft_key])
    draft["rows"][0]["definition"] = "아직 저장하지 않은 기준"
    app.session_state[draft_key] = draft
    app.button(key=f"codebook_icon_edit_{first}").click().run()
    app.text_input(key=f"codebook_name_{first}").set_value("선택하지 않은 기준표")
    app.button(key=f"codebook_icon_save_{first}").click().run()
    assert store.codebook(first)["name"] == "선택하지 않은 기준표"
    assert app.session_state[f"book_choice_{dataset}"] == second
    assert app.session_state[draft_key] == draft
    app.button(key=f"codebook_icon_delete_{first}").click().run()
    assert app.session_state[f"book_choice_{dataset}"] == second
    assert app.session_state[draft_key] == draft
    assert not app.exception and not app.error


def test_ui_selection_exits_unsaved_name_edit_and_opens_chosen_book(monkeypatch):
    store, dataset, first, second, _, app = screen(monkeypatch)
    app.button(key=f"codebook_icon_edit_{first}").click().run()
    app.text_input(key=f"codebook_name_{first}").set_value("아직 저장하지 않은 이름")
    app.button(key=f"codebook_select_{second}").click().run()
    assert store.codebook(first)["name"] == ""
    assert f"codebook_name_{first}" not in [field.key for field in app.text_input]
    app.button(key=f"codebook_select_{first}").click().run()
    assert app.session_state[f"book_choice_{dataset}"] == first
    assert f"revise_{first}_save" in [button.key for button in app.button]
    assert not app.exception and not app.error
