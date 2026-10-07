import pytest
from pydantic import ValidationError

from src.models import CodebookDraft, CodebookReview, QUALITY_CHECKS, materialize_codes
from src.storage import Store
from src.workflow import generate_codebook, execute_run, other_quality, other_review_notice
from tests.test_workflow import FakeAI, create_dataset, setup_run, issue, opinion
from tests.test_other_quality import OTHER
from src.models import CodingBatch


RECORDS = [{"id": "V0001", "text": "배송 빠르고 포장도 좋아요"}]


def draft(*pairs):
    return CodebookDraft(codes=[dict(category=category, name=name, definition=f"{name}에 대한 의견",
        reason="반복되는 배송 평가", evidence=[dict(voc_id="V0001", quote=RECORDS[0]["text"])])
        for category, name in pairs])


def review(value):
    return CodebookReview(codes=value.codes, decisions=[dict(draft_code_index=i, target_names=[c.name], reason="유지")
        for i, c in enumerate(value.codes)], checks=[dict(criterion=key, finding="검토 근거", resolution="수정·유지")
                                                   for key in QUALITY_CHECKS])


@pytest.mark.parametrize("pairs", [
    [("배송", "속도"), ("서비스", "속 도")],
    [("배송", "배송")],
    [("배송", "긍정 배송 속도")],
    [("배송", "배송 속도 혼합")],
])
def test_ai_draft_rejects_structural_ambiguity(pairs):
    with pytest.raises(ValueError):
        materialize_codes(draft(*pairs), RECORDS)


def test_review_requires_all_eight_distinct_checks():
    payload = review(draft(("배송", "배송 속도"))).model_dump()
    payload["checks"][0] = payload["checks"][1]
    with pytest.raises(ValidationError):
        CodebookReview.model_validate(payload)


def test_semantic_review_changes_hierarchy_before_saving_without_schema_change():
    class Service(FakeAI):
        def codebook(self, *args, **kwargs):
            return draft(("제품", "배송 속도"))

        def review_codebook(self, records, context, value, **kwargs):
            assert value.codes[0].category == "제품"
            corrected = draft(("배송", "배송 속도"))
            corrected.codes[0].definition = "포함: 배송 소요 시간 / 제외: 포장 상태"
            return review(corrected)

    store = Store()
    dataset = create_dataset(store, [RECORDS[0]["text"]])
    book = store.codebook(generate_codebook(store, dataset, Service(), ""))
    assert book["codes"][0].category == "배송"
    assert "제외:" in book["codes"][0].definition
    assert set(book["codes"][0].model_dump()) == {"id", "category", "name", "definition", "reason", "evidence"}
    assert book["changes"][0]["kind"] == "ai_quality_review"
    assert len(book["changes"][0]["checks"]) == 8


def test_review_cannot_save_invented_evidence_and_receives_feedback():
    class Service(FakeAI):
        feedbacks = []

        def review_codebook(self, records, context, value, **kwargs):
            self.feedbacks.append(kwargs["feedback"])
            bad = draft(("배송", "배송 속도"))
            bad.codes[0].evidence[0].quote = "원문에 없는 문장"
            return review(bad)

    store = Store()
    dataset = create_dataset(store, [RECORDS[0]["text"]])
    service = Service()
    with pytest.raises(ValueError, match="품질 검토"):
        generate_codebook(store, dataset, service, "")
    assert len(service.feedbacks) == 3 and "근거" in service.feedbacks[1]
    assert not store.list_codebooks(dataset)


def test_review_repairs_code_mapping_without_losing_original_draft():
    class Service(FakeAI):
        attempts = 0

        def review_codebook(self, records, context, value, **kwargs):
            self.attempts += 1
            assert len(value.codes) == 1
            if self.attempts == 1:
                bad = review(value)
                bad.decisions[0].draft_code_index = 20  # VOC 순번과 혼동한 응답
                return bad
            assert "모든 코드" in kwargs["feedback"]
            return review(value)

    store = Store()
    dataset = create_dataset(store, [RECORDS[0]["text"]])
    service = Service()
    book = store.codebook(generate_codebook(store, dataset, service, ""))
    assert service.attempts == 2
    assert book["changes"][0]["before_count"] == 1
    assert book["changes"][0]["decisions"][0]["draft_code_index"] == 0


@pytest.mark.parametrize("limit", [0, 2])
def test_high_other_alone_never_blocks_completion_or_forces_additions(limit):
    class Service(FakeAI):
        def classify(self, records, codes, context, **kwargs):
            return CodingBatch(results=[opinion(r["id"], [issue("CO", r["text"])]) for r in records])

        def supplement(self, records, codes, context, candidates, constraints, **kwargs):
            assert any(c.id == "CO" for c in codes)  # 확정 기준 전체를 검토에 전달
            assert kwargs["frequency_summary"]["other_quality"]["hard_limit"] is False
            return CodebookDraft(codes=[])

    store = Store()
    run_id = setup_run(store, ["드문 이야기", "다른 드문 이야기"], [OTHER])
    with store.connect() as db:
        db.execute("UPDATE runs SET round_limit=? WHERE id=?", (limit, run_id))
    execute_run(store, run_id, Service())
    run = store.run(run_id)
    assert run["status"] == "completed" and run["round"] == 0
    signal = other_quality(store.effective_results(run_id), [OTHER], 2)[1]
    assert signal["percent"] == 100 and signal["signal"] == "warning"
    assert "새 분류를 만들 필요는 없습니다" in other_review_notice(signal)


def test_semantic_review_can_reject_unnecessary_supplement_without_changing_existing_codes():
    class Service(FakeAI):
        def supplement(self, *args, **kwargs):
            return draft(("배송", "배송 속도"))

        def review_codebook(self, records, context, value, **kwargs):
            assert kwargs["existing"][0].id == "CO"
            decision = review(CodebookDraft(codes=[]))
            from src.models import CodeReviewDecision
            decision.decisions = [CodeReviewDecision(draft_code_index=0, target_names=[], reason="별도 분석 가치 없음")]
            return decision

    store = Store()
    run_id = setup_run(store, [RECORDS[0]["text"]], [OTHER])
    execute_run(store, run_id, Service())
    run = store.run(run_id)
    assert run["status"] == "completed" and run["round"] == 0
    assert store.codebook(run["codebook_id"])["codes"] == [OTHER]
