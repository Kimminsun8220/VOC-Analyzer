from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from src import ai as ai_module, config
from src.ai import AIError, DEFAULT_MODEL, PROMPT_VERSION
from src.ingestion import prepare_preview, read_csv, read_excel, read_pasted_text
from src.models import Code, CodebookDraft, CodingBatch, CodingResult, Issue, materialize_codes, validate_result
from src.results import csv_download, result_tables
from src.storage import Store
from src.workflow import confirm_codebook, execute_run, generate_codebook, sample_records


def code(identifier="C1", name="배송 속도", definition="배송의 빠르기와 약속일 준수에 관한 의견"):
    return Code(id=identifier, category="배송", name=name, definition=definition, reason="검증")


def issue(code_id="C1", evidence="빠름", sentiment="긍정", **kwargs):
    return Issue(code_id=code_id, sentiment=sentiment, evidence_text=evidence, **kwargs)


def opinion(identifier="V0001", issues=None):
    return CodingResult(voc_id=identifier, response_type="opinions", issues=issues or [issue()])


def create_dataset(store, texts, context=""):
    frame = pd.DataFrame({"VOC": texts, "사람 코드": ["비교 전용"] * len(texts)})
    return store.save_dataset("검증", prepare_preview(frame, "VOC"), frame, "VOC", "검증", context)


def setup_run(store, texts, codes=None, context=""):
    dataset_id = create_dataset(store, texts, context)
    book_id = store.save_codebook(dataset_id, codes if codes is not None else [code()], context, "test-model", [], "confirmed")
    return store.create_run(dataset_id, book_id, "test-model", PROMPT_VERSION)


class FakeAI:
    model = "test-model"

    def __init__(self, classify=None):
        self.calls = []
        self.classifier = classify

    def classify(self, records, codes, context, feedback=None):
        self.calls.append(deepcopy(records))
        assert all(set(row) == {"id", "text"} for row in records)
        if self.classifier:
            return self.classifier(records, codes, context)
        return CodingBatch(results=[opinion(row["id"], [issue(codes[0].id, row["text"])]) for row in records])

    def codebook(self, records, context):
        assert all(set(row) == {"id", "text"} for row in records)
        return CodebookDraft(codes=[{
            "category": "배송", "name": "배송 속도", "definition": "배송 빠르기에 관한 의견", "reason": "배송 의견 관측",
            "evidence": [{"voc_id": records[0]["id"], "quote": records[0]["text"]}],
        }])

    def close(self):
        pass


def test_csv_format_blanks_are_not_response_records():
    frame = read_csv('ID,VOC\n1,좋아요\n\n2,\n3,""\n'.encode())
    preview = prepare_preview(frame, "VOC")
    assert preview.input_count == 3
    assert preview.blank_count == 2


def test_pasted_blank_lines_ignored_but_literal_no_response_kept():
    assert read_pasted_text("\n없음\n \n모름\n")["VOC"].tolist() == ["없음", "모름"]


def test_excel_empty_responses_with_ids_are_retained():
    from io import BytesIO
    buffer = BytesIO()
    pd.DataFrame({"ID": [1, 2], "VOC": ["좋아요", ""]}).to_excel(buffer, index=False)
    preview = prepare_preview(read_excel(buffer.getvalue(), "Sheet1"), "VOC")
    assert preview.input_count == 2 and preview.blank_count == 1


@pytest.mark.parametrize("bad_issue", [
    issue("missing"), issue(evidence="원문에 없음"), issue(context_evidence_text="없는 배경"),
    issue(subject_label="접수 직원"), issue(subject_label=None, subject_evidence_text="직원"),
    issue(code_id=None), issue(missing_code="중복 상태"),
])
def test_invalid_classification_cannot_enter_success_results(bad_issue):
    with pytest.raises(ValueError):
        validate_result(opinion(issues=[bad_issue]), {"id": "V0001", "text": "빠름"}, [code()], "")


def test_context_and_original_evidence_kept_separate():
    item = issue(evidence="SA가 불친절", sentiment="부정", subject_label="서비스 어드바이저",
        subject_evidence_text="SA는 서비스 어드바이저", subject_evidence_source="context",
        context_evidence_text="SA는 서비스 어드바이저")
    validate_result(opinion(issues=[item]), {"id": "V0001", "text": "SA가 불친절"}, [code()], "SA는 서비스 어드바이저")


def test_empty_opinions_and_no_content_with_issues_rejected():
    with pytest.raises(ValueError):
        CodingResult(voc_id="V0001", response_type="opinions")
    with pytest.raises(ValueError):
        CodingResult(voc_id="V0001", response_type="no_content", no_content_reason="무응답", issues=[issue()])


def test_codebook_rejects_invented_sources_and_duplicate_definitions():
    draft = FakeAI().codebook([{"id": "V0001", "text": "빠름"}], "")
    with pytest.raises(ValueError, match="근거"):
        materialize_codes(draft, [{"id": "V0001", "text": "늦음"}])
    with pytest.raises(ValueError, match="중복"):
        materialize_codes(CodebookDraft(codes=draft.codes * 2), [{"id": "V0001", "text": "빠름"}])


def test_sampling_reproducible_no_metadata_no_truncation():
    records = [{"id": str(i), "text": str(i) + "가" * 4000, "metadata": "secret human code"} for i in range(200)]
    first = sample_records(records)
    assert first == sample_records(records)
    assert sum(len(r["text"]) for r in first) <= 60000
    assert all(set(r) == {"id", "text"} and r["text"] == records[int(r["id"])]["text"] for r in first)


def test_generate_confirm_and_reopen_preserve_snapshots(tmp_path):
    store = Store(tmp_path / "persist.db")
    dataset = create_dataset(store, ["빠름"], "초기 배경")
    draft_id = generate_codebook(store, dataset, FakeAI(), "수정 배경")
    draft = store.codebook(draft_id)
    rows = [{key: getattr(c, key) for key in ("id", "category", "name", "definition")} for c in draft["codes"]]
    rows[0]["name"] = "빠르기"
    confirmed_id = confirm_codebook(store, draft_id, rows)
    reopened = Store(tmp_path / "persist.db")
    assert reopened.codebook(draft_id)["status"] == "draft"
    assert reopened.codebook(draft_id)["codes"][0].name == "배송 속도"
    assert reopened.codebook(confirmed_id)["codes"][0].name == "빠르기"
    assert reopened.codebook(confirmed_id)["context"] == "수정 배경"
    assert reopened.dataset(dataset)["records"][0]["metadata"] == {"사람 코드": "비교 전용"}


def test_mixed_sentiments_and_no_content_are_not_collapsed():
    store = Store()
    run_id = setup_run(store, ["빠름 그러나 늦음", "", "모름"], [code()])
    def classify(records, codes, context):
        return CodingBatch(results=[
            opinion("V0001", [issue(evidence="빠름"), issue(evidence="늦음", sentiment="부정")]),
            CodingResult(voc_id="V0003", response_type="no_content", no_content_reason="모름만 응답"),
        ])
    execute_run(store, run_id, FakeAI(classify))
    originals, issues = result_tables(store, run_id)
    assert store.run(run_id)["status"] == "completed"
    assert len(originals) == 3 and len(issues) == 2
    assert originals.iloc[0]["전체 감성"] == "혼합"
    assert originals["응답 상태"].eq("없음·무응답·모름").sum() == 2
    assert set(issues["감성"]) == {"긍정", "부정"}


def test_all_blank_dataset_completes_without_ai():
    store = Store()
    run_id = setup_run(store, ["", " "], [])
    ai = FakeAI(lambda *args: pytest.fail("blank responses must not call AI"))
    execute_run(store, run_id, ai)
    assert store.run(run_id)["status"] == "completed"
    assert len(store.results(run_id)) == 2


def test_failure_resume_only_retries_invalid_rows():
    store = Store()
    run_id = setup_run(store, ["빠름", "빠름"])
    bad = FakeAI(lambda *args: CodingBatch(results=[opinion("V0001"), opinion("V0002", [issue(evidence="없는 근거")])]))
    execute_run(store, run_id, bad)
    assert store.run(run_id)["status"] == "failed"
    good = FakeAI()
    execute_run(Store(store.path), run_id, good)
    assert good.calls == [[{"id": "V0002", "text": "빠름"}]]
    assert store.run(run_id)["status"] == "completed"
    assert len(store.results(run_id)) == 2
    execute_run(store, run_id, good)
    assert len(good.calls) == 1


def test_api_failure_does_not_become_no_content():
    store = Store()
    run_id = setup_run(store, ["빠름"])
    def fail(*args):
        raise AIError("요청 한도", True)
    execute_run(store, run_id, FakeAI(fail))
    assert store.run(run_id)["status"] == "failed"
    assert store.results(run_id) == []


def test_missing_or_duplicate_ids_fail_without_partial_opinions():
    store = Store()
    run_id = setup_run(store, ["빠름", "빠름"])
    execute_run(store, run_id, FakeAI(lambda *args: CodingBatch(results=[opinion(), opinion()])))
    assert store.run(run_id)["status"] == "failed"
    assert all(row["status"] == "failed" for row in store.results(run_id))


def test_supplement_reclassifies_all_and_preserves_previous_round():
    store = Store()
    run_id = setup_run(store, ["빠름", "구성품 누락"])
    class SupplementAI(FakeAI):
        def classify(self, records, codes, context):
            self.calls.append(records)
            new_code = next((c.id for c in codes if c.name == "구성품 누락"), None)
            return CodingBatch(results=[opinion(row["id"], [issue(
                code_id="C1" if row["text"] == "빠름" else new_code, evidence=row["text"],
                missing_code="구성품 누락" if row["text"] != "빠름" and not new_code else "")]) for row in records])
        def supplement(self, records, codes, context, candidates, constraints):
            return CodebookDraft(codes=[{"category": "제품", "name": "구성품 누락", "definition": "구성품이 빠짐",
                "reason": "기존 코드에 없는 의미", "evidence": [{"voc_id": "V0002", "quote": "구성품 누락"}]}])
    ai = SupplementAI()
    execute_run(store, run_id, ai)
    assert store.run(run_id)["status"] == "completed"
    assert store.run(run_id)["round"] == 1
    assert [len(call) for call in ai.calls] == [2, 2]
    assert len(store.results(run_id, 0)) == len(store.results(run_id, 1)) == 2
    assert len(store.codebook(store.run(run_id)["codebook_id"])["codes"]) == 2


def test_supplement_limit_and_resume_budget_persist():
    store = Store()
    run_id = setup_run(store, ["새 의미"])
    with store.connect() as db:
        db.execute("UPDATE runs SET round_limit=0 WHERE id=?", (run_id,))
    ai = FakeAI(lambda *args: CodingBatch(results=[opinion(issues=[issue(None, "새 의미", missing_code="새 의미")])]))
    execute_run(store, run_id, ai)
    assert store.run(run_id)["status"] == "needs_review"
    store.extend_limit(run_id)
    assert Store(store.path).run(run_id)["round_limit"] == 2


def test_run_lock_and_expired_lease_recovery():
    store = Store()
    run_id = setup_run(store, ["빠름"])
    store.claim(run_id)
    with pytest.raises(ValueError, match="진행 중"):
        execute_run(store, run_id, FakeAI())
    with store.connect() as db:
        db.execute("UPDATE runs SET lease_until=0 WHERE id=?", (run_id,))
    execute_run(store, run_id, FakeAI())
    assert store.run(run_id)["status"] == "completed"


def test_csv_export_does_not_change_stored_original():
    frame = pd.DataFrame({"원문": ["=1+1", " 정상 의견 "]})
    exported = csv_download(frame).decode("utf-8-sig")
    assert "'=1+1" in exported and frame.iloc[0, 0] == "=1+1"


def test_codebook_bad_quote_is_repaired_before_saving():
    store = Store()
    dataset_id = create_dataset(store, ["빠름"])
    class RepairAI(FakeAI):
        def codebook(self, records, context, feedback=""):
            self.calls.append(feedback)
            draft = super().codebook(records, context)
            if not feedback:
                draft.codes[0].evidence[0].quote = "원문에 없는 인용"
            return draft
    service = RepairAI()
    draft_id = generate_codebook(store, dataset_id, service, "")
    assert len(service.calls) == 2 and "V0001" in service.calls[1]
    assert len(store.list_codebooks(dataset_id)) == 1
    assert store.codebook(draft_id)["codes"][0].evidence[0].quote == "빠름"


def test_unclear_response_can_be_retried():
    store = Store()
    run_id = setup_run(store, ["빠름"])
    service = FakeAI(lambda *args: CodingBatch(results=[CodingResult(voc_id="V0001", response_type="unclear", review_reason="해석 확인")]))
    execute_run(store, run_id, service)
    assert store.run(run_id)["status"] == "needs_review"
    execute_run(store, run_id, FakeAI())
    assert store.run(run_id)["status"] == "completed"


def test_app_input_to_codebook_to_results_and_reload(monkeypatch):
    monkeypatch.setattr(config, "load_gemini_key", lambda: "test-key")
    models = []

    def service(key, model):
        models.append(model)
        return FakeAI()

    monkeypatch.setattr(ai_module, "GeminiAI", service)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.button(key="preview_button").click().run()
    app.button(key="save_input").click().run()
    assert not app.exception
    assert app.radio(key="nav").value == "2. 분류 기준표"
    app.button(key="generate_codebook").click().run()
    assert not app.exception and not app.error
    app.button(key="confirm_codebook").click().run()
    assert not app.exception and not app.error
    app.button(key="start_classification").click().run(timeout=10)
    assert not app.exception and not app.error
    assert app.radio(key="nav").value == "3. 분류 결과"
    assert app.metric[0].value == "20건"
    assert models == [DEFAULT_MODEL, DEFAULT_MODEL]
    fresh = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    fresh.radio(key="nav").set_value("3. 분류 결과").run()
    assert fresh.metric[0].value == "20건" and not fresh.exception
    # 저장된 자료가 있는 상태에서 새 입력을 저장해도 위젯 상태 변경 오류가 없어야 한다.
    fresh.radio(key="nav").set_value("1. 입력").run()
    fresh.button(key="preview_button").click().run()
    fresh.button(key="save_input").click().run()
    assert not fresh.exception


def test_app_resume_uses_saved_model_without_model_setting(monkeypatch):
    store = Store()
    run_id = setup_run(store, ["빠름"])
    store.update_status(run_id, "failed", "검증용 재시도")
    monkeypatch.setattr(config, "load_gemini_key", lambda: "test-key")
    models = []

    def service(key, model):
        models.append(model)
        fake = FakeAI()
        fake.model = model
        return fake

    monkeypatch.setattr(ai_module, "GeminiAI", service)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.radio(key="nav").set_value("3. 분류 결과").run()
    app.button(key="resume_run").click().run()
    assert not app.exception and not app.error
    assert models == ["test-model"] and store.run(run_id)["status"] == "completed"


def test_app_saved_codebook_context_survives_new_browser_session(monkeypatch):
    store = Store()
    dataset_id = create_dataset(store, ["빠름"], "초기 배경")
    store.save_codebook(dataset_id, [code()], "변경한 배경", "test-model", [], "confirmed")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.radio(key="nav").set_value("2. 분류 기준표").run()
    assert app.text_area[0].value == "변경한 배경"
    assert not app.button(key="start_classification").disabled
    assert not app.exception


def test_draft_editor_applies_cell_edits_deletion_and_addition_on_confirmation():
    store = Store()
    dataset_id = create_dataset(store, ["빠름", "포장이 찌그러짐"])
    draft_id = store.save_codebook(dataset_id, [code(), code("C2", "포장", "포장 상태 의견")], "", "test-model", [])
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.radio(key="nav").set_value("2. 분류 기준표").run()
    app.session_state[f"editor_{draft_id}"] = {
        "edited_rows": {0: {"category": "물류", "name": "도착 속도", "definition": "약속한 날짜의 도착 여부"}},
        "deleted_rows": [1],
        "added_rows": [{"category": "제품", "name": "구성품", "definition": "구성품 제공 여부"}],
    }
    app.button(key="confirm_codebook").click().run()
    assert not app.exception and not app.error
    confirmed = store.codebook(store.list_codebooks(dataset_id)[0]["id"])
    assert confirmed["status"] == "confirmed"
    assert [(c.category, c.name, c.definition) for c in confirmed["codes"]] == [
        ("물류", "도착 속도", "약속한 날짜의 도착 여부"), ("제품", "구성품", "구성품 제공 여부"),
    ]
    assert confirmed["codes"][0].id == "C1"
    assert "C2" not in {c.id for c in confirmed["codes"]}
    assert {c["id"] for c in confirmed["constraints"]} == {"C1", "C2"}
    assert store.codebook(draft_id)["codes"][0].name == "배송 속도"
