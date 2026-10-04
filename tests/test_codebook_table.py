from copy import deepcopy

import pandas as pd
import pytest

from src.codebook_changes import code_mapping
from src.codebook_table import save_table_draft, table_action, table_draft, table_has_changes, update_fields, update_view, visible_rows
from src.ingestion import prepare_preview
from src.models import Code, CodeDefinition, CodeDefinitions
from src.storage import Store


@pytest.fixture
def book():
    store = Store()
    frame = pd.DataFrame({"VOC": ["배송은 빨랐지만 포장이 찌그러졌어요."]})
    dataset = store.save_dataset("행 조작 검증", prepare_preview(frame, "VOC"), frame, "VOC", "test", "")
    codes = [Code(id=identifier, category=category, name=name, definition=definition, reason="test")
             for identifier, category, name, definition in [
        ("C1", "배송", "배송 속도", "배송 소요 시간에 관한 의견"),
        ("C2", "포장", "포장 상태", "포장 훼손과 보호 상태에 관한 의견"),
        ("C3", "제품", "음질", "음질의 선명도에 관한 의견"),
    ]]
    identifier = store.save_codebook(dataset, codes, "", "test-model", [], "confirmed")
    return store, store.codebook(identifier)


def operate(draft, kind, **kwargs):
    return table_action(draft, {"kind": kind, "rows": visible_rows(draft), **kwargs})


def test_drag_merge_keeps_table_order_automatic_names_definitions_and_original(book):
    store, source = book
    original = deepcopy(source)
    draft = table_draft(source)
    merged = operate(draft, "merge", source_id="C2", target_id="C1")
    assert draft == table_draft(source)
    row = merged["rows"][0]
    assert (row["category"], row["name"]) == ("배송/포장", "배송 속도/포장 상태")
    assert row["definition"] == "배송 속도: 배송 소요 시간에 관한 의견\n포장 상태: 포장 훼손과 보호 상태에 관한 의견"
    identifier = save_table_draft(store, source, merged)
    assert store.codebook(source["id"]) == original
    assert store.codebook(identifier)["parent_id"] == source["id"]
    assert code_mapping(store, source["id"], identifier) == {"C1": None, "C2": None, "C3": "C3"}


def test_saved_composite_reopens_and_splits_into_original_rows(book):
    store, source = book
    merged = operate(table_draft(source), "merge", source_id="C1", target_id="C2")
    saved = store.codebook(save_table_draft(store, source, merged))
    reopened = table_draft(Store(store.path).codebook(saved["id"]))
    split = operate(reopened, "split", source_id=reopened["rows"][0]["id"])
    assert [(row["category"], row["name"], row["definition"]) for row in split["rows"]] == [
        (code.category, code.name, code.definition) for code in source["codes"]]
    restored = store.codebook(save_table_draft(store, saved, split))
    assert len(restored["codes"]) == 3
    assert code_mapping(store, saved["id"], restored["id"])[saved["codes"][0].id] is None


def test_nested_merge_splits_one_step_at_a_time_and_undo_restores(book):
    _, source = book
    original = table_draft(source)
    first = operate(original, "merge", source_id="C1", target_id="C2")
    second = operate(first, "merge", source_id=first["rows"][0]["id"], target_id="C3")
    split = operate(second, "split", source_id=second["rows"][0]["id"])
    assert visible_rows(split) == visible_rows(first)
    undo = operate(first, "undo")
    assert visible_rows(undo) == visible_rows(original)
    assert not undo["changes"]


def test_plain_row_split_adds_editable_row_and_blank_name_cannot_save(book):
    store, source = book
    split = operate(table_draft(source), "split", source_id="C1")
    assert len(split["rows"]) == 4
    assert split["rows"][0]["category"] == split["rows"][1]["category"] == "배송"
    assert split["rows"][1]["name"] == ""
    with pytest.raises(ValueError):
        save_table_draft(store, source, split)
    assert len(store.list_codebooks(source["dataset_id"])) == 1
    split["rows"][1]["name"] = "배송 안내"
    calls = []
    def generate(operation, codes, ids, targets, records, context):
        calls.append(targets)
        return CodeDefinitions(definitions=[CodeDefinition(target_index=1, definition="배송 일정 안내의 정확성에 관한 의견")])
    saved = store.codebook(save_table_draft(store, source, split, generate))
    assert saved["codes"][1].name == "배송 안내"
    assert saved["codes"][1].definition == "배송 일정 안내의 정확성에 관한 의견" and len(calls) == 1


@pytest.mark.parametrize("action", [
    {"kind": "merge", "source_id": "C1", "target_id": "C1"},
    {"kind": "merge", "source_id": "C1", "target_id": "unknown"},
    {"kind": "split", "source_id": "unknown"},
])
def test_invalid_actions_leave_draft_and_saved_book_unchanged(book, action):
    store, source = book
    draft = table_draft(source)
    original = deepcopy(draft)
    with pytest.raises(ValueError):
        table_action(draft, {**action, "rows": visible_rows(draft)})
    assert draft == original and len(store.list_codebooks(source["dataset_id"])) == 1


def test_delete_all_rows_cannot_save_and_undo_recovers(book):
    store, source = book
    draft = table_draft(source)
    for identifier in ("C1", "C2", "C3"):
        draft = operate(draft, "delete", source_id=identifier)
    with pytest.raises(ValueError, match="한 개 이상"):
        save_table_draft(store, source, draft)
    assert len(operate(draft, "undo")["rows"]) == 1
    assert len(store.list_codebooks(source["dataset_id"])) == 1


def test_filtered_edit_saves_hidden_codes_and_does_not_store_view_as_code_changes(book):
    store, source = book
    draft = table_draft(source)
    update_view(draft, {"filters": {"category": ["배송"], "name": None}, "kept_rows": []})
    assert not table_has_changes(source, draft)
    rows = visible_rows(draft)
    rows[0]["name"] = "도착 속도"
    update_fields(draft, rows)
    saved = store.codebook(save_table_draft(store, source, draft))
    assert saved["codes"][0].name == "도착 속도"
    assert saved["codes"][1:] == source["codes"][1:]
    assert all("filters" not in change for change in saved["changes"])
    with pytest.raises(ValueError, match="표가 변경"):
        update_fields(draft, rows[:1])  # 표시한 행만 보내 저장하는 오류를 차단한다.


def test_structural_edits_preserve_filter_and_undo_keeps_hidden_rows(book):
    _, source = book
    draft = table_draft(source)
    update_view(draft, {"filters": {"category": ["배송"], "name": ["배송 속도"]}, "kept_rows": ["C1"]})
    split = operate(draft, "split", source_id="C1")
    assert split["filters"] == draft["filters"]
    assert len(split["rows"]) == 4
    assert set(split["kept_rows"]) == {row["id"] for row in split["rows"][:2]}
    restored = operate(split, "undo")
    assert visible_rows(restored) == visible_rows(draft)
    assert restored["filters"] == draft["filters"]
    assert restored["kept_rows"] == ["C1"]
    added = operate(draft, "add")
    assert added["rows"][-1]["id"] in added["kept_rows"]
    merged = operate(draft, "merge", source_id="C1", target_id="C2")
    assert merged["rows"][0]["id"] in merged["kept_rows"]


def test_unedited_draft_can_be_confirmed_with_all_codes(book):
    store, source = book
    identifier = store.save_codebook(source["dataset_id"], source["codes"], "", "test-model", [], "draft")
    original = store.codebook(identifier)
    confirmed = store.codebook(save_table_draft(store, original, table_draft(original)))
    assert confirmed["status"] == "confirmed" and confirmed["codes"] == original["codes"]
    assert confirmed["parent_id"] == identifier


def test_draft_confirmation_preserves_category_only_edit_constraint(book):
    store, source = book
    identifier = store.save_codebook(source["dataset_id"], source["codes"], "", "test-model", [], "draft")
    original = store.codebook(identifier)
    edited = table_draft(original)
    edited["rows"][0]["category"] = "물류"
    confirmed = store.codebook(save_table_draft(store, original, edited))
    assert confirmed["codes"][0].category == "물류"
    assert confirmed["constraints"] == [original["codes"][0].model_dump()]
