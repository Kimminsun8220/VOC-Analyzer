from copy import deepcopy
from pathlib import Path
import json
import sqlite3

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from src import ai as ai_module, config
from src.ai import PROMPT_VERSION
from src.codebook_changes import code_mapping, edit_codebook, merge_codes, split_code
from src.corrections import save_correction
from src.corrections_ui import issue_rows
from src.grouping import group_results
from src.ingestion import prepare_preview
from src.models import Code, CodebookDraft, CodingBatch, CodingResult, Issue
from src.results import result_tables
from src.storage import Store
from src.workflow import execute_run


def issue(code="C1", sentiment="긍정", quote="배송은 빠름", **kwargs):
    return Issue(code_id=code, sentiment=sentiment, evidence_text=quote, **kwargs)


def result(issues=None):
    return CodingResult(voc_id="V0001", response_type="opinions", issues=issues or [
        issue(), issue("C2", "부정", "상담은 불친절")])


class AI:
    model = "test-model"

    def __init__(self, first=None):
        self.first = first or result()
        self.calls = []

    def classify(self, records, codes, context, feedback=None):
        self.calls.append(records)
        return CodingBatch(results=[self.first if r["id"] == "V0001" else CodingResult(
            voc_id=r["id"], response_type="no_content", no_content_reason="없음") for r in records])

    def close(self):
        pass


@pytest.fixture
def prepared():
    store = Store()
    frame = pd.DataFrame({"VOC": ["배송은 빠름, 상담은 불친절, 포장도 나쁨", "없음"]})
    dataset = store.save_dataset("수정 검증", prepare_preview(frame, "VOC"), frame, "VOC", "test", "")
    codes = [Code(id=i, category=c, name=n, definition=d, reason="test") for i, c, n, d in [
        ("C1", "배송", "배송 속도", "배송 빠르기에 관한 의견"),
        ("C2", "응대", "상담 태도", "상담 친절도에 관한 의견"),
        ("C3", "배송", "도착 빠르기", "도착까지 소요된 시간에 관한 의견"),
        ("C4", "포장", "포장 상태", "포장 훼손 상태에 관한 의견")]]
    book = store.save_codebook(dataset, codes, "", AI.model, [], "confirmed")
    run = store.create_run(dataset, book, AI.model, PROMPT_VERSION)
    execute_run(store, run, AI())
    return store, run, book


def rows_for(store, run, voc="V0001"):
    data = next(row["result"] for row in store.effective_results(run) if row["voc_id"] == voc)
    return issue_rows(CodingResult.model_validate(data))


def save(store, run, rows, voc="V0001", state="opinions", reason="검증 수정", empty_reason=""):
    return save_correction(store, run, voc, rows, state, reason, store.run(run)["result_revision"], empty_reason)


def change_sentiment(store, run, sentiment="중립"):
    rows = rows_for(store, run)
    rows[0]["sentiment"] = sentiment
    save(store, run, rows)


def recode(store, parent, book=None, service=None):
    run = store.run(parent)
    new = store.create_run(run["dataset_id"], book or run["codebook_id"], AI.model, PROMPT_VERSION, parent)
    execute_run(store, new, service or AI())
    return new


def edit_rows(store, book):
    return [{key: getattr(c, key) for key in ("id", "category", "name", "definition")} for c in store.codebook(book)["codes"]]


def test_manual_save_updates_current_results_and_groups_preserves_ai(prepared):
    store, run, book = prepared
    raw = deepcopy(store.results(run))
    change_sentiment(store, run)
    assert store.results(run) == raw
    originals, issues = result_tables(Store(store.path), run)
    assert issues.iloc[0]["감성"] == "중립"
    assert set(issues["분류 출처"]) == {"사용자 수정"}
    assert group_results(originals, issues, store.codebook(book)["codes"], ["C1"], "긍정").voc_count == 0
    assert store.run(run)["result_revision"] == 1
    assert store.run(run)["status"] == "completed"


def test_invalid_evidence_empty_opinions_and_stale_revision_are_atomic(prepared):
    store, run, _ = prepared
    before = store.effective_results(run)
    rows = rows_for(store, run)
    rows[0]["evidence_text"] = "없는 원문"
    with pytest.raises(ValueError, match="원문"):
        save(store, run, rows)
    with pytest.raises(ValueError):
        save(store, run, [])
    assert store.effective_results(run) == before and store.run(run)["result_revision"] == 0
    change_sentiment(store, run)
    with pytest.raises(ValueError, match="다른 화면"):
        save_correction(store, run, "V0001", rows_for(store, run), "opinions", "stale", 0)
    assert store.run(run)["result_revision"] == 1


def test_unchanged_codebook_inherits_only_edited_fields_and_preserves_origin(prepared):
    store, run, _ = prepared
    change_sentiment(store, run)
    changed_ai = AI(result([issue(sentiment="부정"), issue("C2", "중립", "상담은 불친절")]))
    new = recode(store, run, service=changed_ai)
    current = rows_for(store, new)
    assert [row["sentiment"] for row in current] == ["중립", "중립"]
    assert len(changed_ai.calls[0]) == 2  # 수정한 VOC도 전체 재평가
    assert store.run(new)["status"] == "completed"
    first_origin = store.corrections(run)["V0001"]["edits"][0]["origin_id"]
    third = recode(store, new)
    assert rows_for(store, third)[0]["sentiment"] == "중립"
    assert store.corrections(third)["V0001"]["edits"][0]["origin_id"] == first_origin


def test_source_revision_is_frozen_and_independent_branches_do_not_mix(prepared):
    store, run, book = prepared
    change_sentiment(store, run, "중립")
    parent = store.run(run)
    new = store.create_run(parent["dataset_id"], book, AI.model, PROMPT_VERSION, run)
    change_sentiment(store, run, "부정")
    execute_run(store, new, AI())
    assert rows_for(store, new)[0]["sentiment"] == "중립"
    assert store.run(new)["parent_result_revision"] == 1
    independent = store.create_run(parent["dataset_id"], book, AI.model, PROMPT_VERSION)
    execute_run(store, independent, AI())
    assert rows_for(store, independent)[0]["sentiment"] == "긍정"


def test_renamed_code_and_unrelated_definition_change_inherit(prepared):
    store, run, book = prepared
    change_sentiment(store, run)
    rows = edit_rows(store, book)
    rows[0]["name"] = "도착 속도"
    rows[3]["definition"] = "포장 완충재 상태와 손상"
    new_book = edit_codebook(store, book, rows)
    assert code_mapping(store, book, new_book)["C1"] == "C1"
    new = recode(store, run, new_book)
    assert store.run(new)["status"] == "completed"
    assert rows_for(store, new)[0]["sentiment"] == "중립"
    assert store.codebook(book)["codes"][0].name == "배송 속도"


def test_same_meaning_merge_follows_explicit_mapping(prepared):
    store, run, book = prepared
    rows = rows_for(store, run)
    rows[0]["code_id"] = "C3"
    rows[0]["sentiment"] = "중립"
    save(store, run, rows)
    with pytest.raises(ValueError, match="같은 의미"):
        merge_codes(store, book, ["C1", "C3"], "빠르기", "배송 소요시간")
    merged = merge_codes(store, book, ["C1", "C3"], "빠르기", "배송 소요시간", True)
    target = code_mapping(store, book, merged)["C1"]
    assert target == code_mapping(store, book, merged)["C3"]
    new = recode(store, run, merged, AI(result([issue(target), issue("C2", "부정", "상담은 불친절")])))
    assert store.run(new)["status"] == "completed"
    assert rows_for(store, new)[0]["code_id"] == target and rows_for(store, new)[0]["sentiment"] == "중립"


@pytest.mark.parametrize("operation", ["definition", "delete", "split", "unrelated_version"])
def test_ambiguous_code_changes_require_review_exclude_candidate_and_resolve(prepared, operation):
    store, run, book = prepared
    change_sentiment(store, run)
    rows = edit_rows(store, book)
    candidate_code = "C1"
    if operation == "definition":
        rows[0]["definition"] = "예정일을 지켰는지 여부"
        new_book = edit_codebook(store, book, rows)
    elif operation == "delete":
        new_book = edit_codebook(store, book, rows[1:])
        candidate_code = "C3"
    elif operation == "split":
        new_book = split_code(store, book, "C1", [{"name": "지연", "definition": "예정일 지연"},
            {"name": "도착", "definition": "실제 소요 기간"}])
        candidate_code = store.codebook(new_book)["codes"][-1].id
    else:
        new_book = store.save_codebook(store.run(run)["dataset_id"], store.codebook(book)["codes"], "", AI.model, [], "confirmed")
    new = recode(store, run, new_book, AI(result([issue(candidate_code), issue("C2", "부정", "상담은 불친절")])))
    assert store.run(new)["status"] == "needs_review"
    originals, issues = result_tables(store, new)
    assert issues.empty and originals.iloc[0]["응답 상태"] == "수정값 승계 검토"
    assert store.run(run)["status"] == "completed"
    raw = store.results(new)[0]["result"]
    rows = issue_rows(CodingResult.model_validate(raw))
    rows[0]["sentiment"] = "중립"
    save(store, new, rows)
    assert store.run(new)["status"] == "completed"
    third = recode(store, new, service=AI(result([issue(candidate_code), issue("C2", "부정", "상담은 불친절")])))
    assert store.run(third)["status"] == "completed" and rows_for(store, third)[0]["sentiment"] == "중립"


def test_addition_deletion_and_later_edits_survive_repeated_recode(prepared):
    store, run, _ = prepared
    rows = rows_for(store, run)[:1]  # 상담 삭제
    rows.append({"item_id": None, **issue("C4", "부정", "포장도 나쁨").model_dump(exclude={"missing_code"})})
    save(store, run, rows)
    current = rows_for(store, run)
    current[1]["sentiment"] = "중립"
    save(store, run, current)
    new = recode(store, run)
    assert [row["code_id"] for row in rows_for(store, new)] == ["C1", "C4"]
    assert rows_for(store, new)[1]["sentiment"] == "중립"
    third = recode(store, new, service=AI(result([issue(), issue("C4", "부정", "포장도 나쁨")])))
    assert len(rows_for(store, third)) == 2 and rows_for(store, third)[1]["sentiment"] == "중립"


def test_no_content_override_and_reverse_direction_are_carried(prepared):
    store, run, _ = prepared
    save(store, run, [], state="no_content", empty_reason="분석 범위와 무관")
    new = recode(store, run)
    assert store.effective_results(new)[0]["result"]["response_type"] == "no_content"
    rows = [{"item_id": None, **issue().model_dump(exclude={"missing_code"})}]
    save(store, new, rows)
    third = recode(store, new)
    assert len(rows_for(store, third)) == 1 and rows_for(store, third)[0]["code_id"] == "C1"


def test_changed_evidence_or_multiple_matches_require_review(prepared):
    store, run, _ = prepared
    change_sentiment(store, run)
    new = recode(store, run, service=AI(result([issue(quote="빠름"), issue("C2", "부정", "상담은 불친절")])))
    assert store.run(new)["status"] == "needs_review"
    ambiguous = recode(store, run, service=AI(result([issue(), issue(), issue("C2", "부정", "상담은 불친절")])))
    assert store.run(ambiguous)["status"] == "needs_review"


def test_definition_changed_then_reverted_does_not_guess_identity(prepared):
    store, run, book = prepared
    rows = edit_rows(store, book)
    original_definition = rows[0]["definition"]
    rows[0]["definition"] = "다른 정의"
    changed = edit_codebook(store, book, rows)
    rows[0]["definition"] = original_definition
    reverted = edit_codebook(store, changed, rows)
    assert code_mapping(store, book, reverted)["C1"] is None


def test_inheritance_during_auto_supplement_and_resume_does_not_duplicate(prepared):
    store, run, book = prepared
    change_sentiment(store, run)
    class Supplement(AI):
        def classify(self, records, codes, context, feedback=None):
            new_code = next((c.id for c in codes if c.name == "추가 주제"), None)
            self.first = result([issue(), issue("C2", "부정", "상담은 불친절"),
                issue(new_code, "부정", "포장도 나쁨", missing_code="추가 주제" if not new_code else "")])
            return super().classify(records, codes, context, feedback)
        def supplement(self, records, codes, context, candidates, constraints):
            return CodebookDraft(codes=[{"category": "새 분류", "name": "추가 주제", "definition": "새 포장 의견",
                "reason": "검증", "evidence": [{"voc_id": "V0001", "quote": "포장도 나쁨"}]}])
    service = Supplement()
    new = recode(store, run, service=service)
    assert store.run(new)["round"] == 1 and store.run(new)["status"] == "completed"
    assert rows_for(store, new)[0]["sentiment"] == "중립" and len(rows_for(store, new)) == 3
    history = store.correction_history(new)
    execute_run(store, new, service)
    assert store.correction_history(new) == history


def test_unrelated_context_change_does_not_block_inheritance(prepared):
    store, run, book = prepared
    # 근거로 쓰지 않은 배경 변경은 무관한 수정을 막지 않는다.
    change_sentiment(store, run)
    child = store.save_codebook(store.run(run)["dataset_id"], store.codebook(book)["codes"], "추가 배경", AI.model, [], "confirmed", book)
    new = recode(store, run, child)
    assert store.run(new)["status"] == "completed"


def test_removed_context_evidence_needs_review(prepared):
    store, run, book = prepared
    context = "상담은 고객센터를 뜻한다"
    first_book = store.save_codebook(store.run(run)["dataset_id"], store.codebook(book)["codes"], context,
        AI.model, [], "confirmed", book)
    first = recode(store, run, first_book)
    rows = rows_for(store, first)
    rows[1].update(subject_label="고객센터", subject_evidence_text="고객센터", subject_evidence_source="context",
                   context_evidence_text=context)
    save(store, first, rows)
    second_book = store.save_codebook(store.run(run)["dataset_id"], store.codebook(book)["codes"], "", AI.model, [], "confirmed", first_book)
    second = recode(store, first, second_book)
    assert store.run(second)["status"] == "needs_review"
    assert "배경" in store.corrections(second)["V0001"]["reason"]


def test_transaction_failure_rolls_back_revision_and_correction(prepared, monkeypatch):
    store, run, _ = prepared
    original_insert = store.insert_correction
    def fail(*args, **kwargs):
        original_insert(*args, **kwargs)
        raise sqlite3.OperationalError("검증용 저장 실패")
    monkeypatch.setattr(store, "insert_correction", fail)
    with pytest.raises(sqlite3.OperationalError):
        change_sentiment(store, run)
    assert store.run(run)["result_revision"] == 0 and store.corrections(run) == {}
    assert rows_for(store, run)[0]["sentiment"] == "긍정"


def test_old_database_migration_preserves_runs_and_results(prepared):
    store, run, _ = prepared
    raw = store.results(run)
    with store.connect() as db:
        for column in ("parent_run_id", "parent_result_revision", "result_revision", "inheritance_json"):
            db.execute(f"ALTER TABLE runs DROP COLUMN {column}")
    reopened = Store(store.path)
    assert reopened.results(run) == raw
    assert reopened.run(run)["result_revision"] == 0
    assert reopened.run(run)["parent_run_id"] is None


def test_wrong_dataset_or_unfinished_parent_cannot_seed_run(prepared):
    store, run, book = prepared
    frame = pd.DataFrame({"VOC": ["다른 자료"]})
    other = store.save_dataset("다른 자료", prepare_preview(frame, "VOC"), frame, "VOC", "test", "")
    other_book = store.save_codebook(other, store.codebook(book)["codes"], "", AI.model, [], "confirmed")
    with pytest.raises(ValueError, match="같은 입력"):
        store.create_run(other, other_book, AI.model, PROMPT_VERSION, run)
    store.update_status(run, "needs_review")
    with pytest.raises(ValueError, match="완료"):
        store.create_run(store.run(run)["dataset_id"], book, AI.model, PROMPT_VERSION, run)


def test_user_can_resolve_missing_code_and_inherit_that_decision(prepared):
    store, run, book = prepared
    parent = store.run(run)
    new = store.create_run(parent["dataset_id"], book, AI.model, PROMPT_VERSION)
    with store.connect() as db:
        db.execute("UPDATE runs SET round_limit=0 WHERE id=?", (new,))
    candidate = AI(result([issue(None, "긍정", "배송은 빠름", missing_code="배송 빠르기"),
                           issue("C2", "부정", "상담은 불친절")]))
    execute_run(store, new, candidate)
    rows = rows_for(store, new)
    rows[0]["code_id"] = "C1"
    save(store, new, rows)
    assert store.run(new)["status"] == "completed"
    third = recode(store, new, service=candidate)
    assert store.run(third)["status"] == "completed"
    assert rows_for(store, third)[0]["code_id"] == "C1"


def test_ui_review_resolution_and_completed_run_default(prepared):
    store, run, book = prepared
    change_sentiment(store, run)
    rows = edit_rows(store, book)
    rows[0]["definition"] = "예정일 준수 여부"
    changed = edit_codebook(store, book, rows)
    new = recode(store, run, changed)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.radio(key="nav").set_value("3. 분류 결과").run()
    choice_key = f"result_choice_{store.run(run)['dataset_id']}"
    assert app.selectbox(key=choice_key).value == run  # 미완료 후보보다 이전 완료 결과가 기본
    app.selectbox(key=choice_key).set_value(new).run()
    assert not app.exception and not app.error
    prefix = f"correction_{new}_0_V0001_1_0"
    app.session_state[prefix + "_editor"] = {"edited_rows": {0: {"sentiment": "중립"}}, "deleted_rows": [], "added_rows": []}
    app.text_input(key=prefix + "_reason").set_value("새 기준에서 이전 감성 유지")
    next(button for button in app.button if button.label == "검토 완료·수정 저장").click().run()
    assert not app.exception and not app.error
    assert store.run(new)["status"] == "completed"


def test_ui_manual_edit_cancel_and_save_updates_results_without_ai(prepared, monkeypatch):
    store, run, _ = prepared
    monkeypatch.setattr(ai_module, "GeminiAI", lambda *args: pytest.fail("수정 저장은 AI 호출 금지"))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.radio(key="nav").set_value("3. 분류 결과").run()
    prefix = f"correction_{run}_0_V0001_0_0"
    app.session_state[prefix + "_editor"] = {"edited_rows": {0: {"sentiment": "중립"}}, "deleted_rows": [], "added_rows": []}
    next(button for button in app.button if button.label == "편집 취소").click().run()
    assert store.run(run)["result_revision"] == 0
    prefix = f"correction_{run}_0_V0001_0_1"
    app.session_state[prefix + "_editor"] = {"edited_rows": {0: {"sentiment": "중립"}}, "deleted_rows": [], "added_rows": []}
    app.text_input(key=prefix + "_reason").set_value("감성 정정")
    next(button for button in app.button if button.label == "수정 저장").click().run()
    assert not app.exception and not app.error
    assert rows_for(store, run)[0]["sentiment"] == "중립"
    fresh = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    fresh.radio(key="nav").set_value("3. 분류 결과").run()
    assert not fresh.exception and not fresh.error
    assert any("개정 1" in caption.value for caption in fresh.caption)


def test_ui_codebook_edit_and_recode_with_parent(prepared, monkeypatch):
    store, run, book = prepared
    change_sentiment(store, run)
    monkeypatch.setattr(config, "load_gemini_key", lambda: "test-key")
    monkeypatch.setattr(ai_module, "GeminiAI", lambda *args: AI())
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.radio(key="nav").set_value("2. 분류 기준표").run()
    app.session_state[f"revise_{book}_editor"] = {"edited_rows": {0: {"name": "도착 속도"}}, "deleted_rows": [], "added_rows": []}
    app.button(key=f"revise_{book}_save").click().run()
    assert not app.exception and not app.error
    app.button(key="start_classification").click().run()
    assert not app.exception and not app.error
    new = store.list_runs(store.run(run)["dataset_id"])[0]
    assert new["parent_run_id"] == run and new["status"] == "completed"
    assert rows_for(store, new["id"])[0]["sentiment"] == "중립"
