import pytest

from src.models import CodebookDraft, materialize_codes, validate_ai_topic_overlap
from src.storage import Store
from src.workflow import execute_run, generate_codebook
from tests.test_workflow import FakeAI, code, create_dataset, issue, opinion, setup_run
from src.models import CodingBatch


def draft(name="흡입력", definition="이물질을 흡입하는 성능과 효과에 관한 의견"):
    return CodebookDraft(codes=[{
        "category": "상품 성능", "name": name, "definition": definition,
        "reason": "성능 주제 관측", "evidence": [{"voc_id": "V0001", "quote": "흡입력이 좋아요"}],
    }])


@pytest.mark.parametrize("name,definition", [
    ("흡입력 긍정", "이물질 흡입 성능"),
    ("흡입력 부정", "이물질 흡입 성능"),
    ("흡입력 (긍정)", "이물질 흡입 성능"),
    ("흡입력", "머리카락을 잘 빨아들이는 긍정적인 평가"),
    ("흡입력", "과자 부스러기를 못 빨아들이는 부정적 평가"),
])
def test_polarized_ai_codes_are_rejected_before_storage(name, definition):
    with pytest.raises(ValueError, match="감성"):
        materialize_codes(draft(name, definition), [{"id": "V0001", "text": "흡입력이 좋아요"}])


def test_general_definition_accepts_opposite_sentiments_and_keeps_evidence_separate():
    codes = materialize_codes(draft(), [{"id": "V0001", "text": "흡입력이 좋아요"}])
    assert codes[0].name == "흡입력"
    assert codes[0].evidence[0].quote == "흡입력이 좋아요"
    assert codes[0].definition == "이물질을 흡입하는 성능과 효과에 관한 의견"
    both = draft(definition="흡입 성능에 대한 긍정적인 평가와 부정적인 평가를 모두 포함")
    assert materialize_codes(both, [{"id": "V0001", "text": "흡입력이 좋아요"}])


def test_codebook_generation_repairs_polarized_draft_before_saving():
    store = Store()
    dataset = create_dataset(store, ["흡입력이 좋아요"])

    class RepairAI(FakeAI):
        def codebook(self, records, context, feedback=""):
            self.calls.append(feedback)
            return draft() if feedback else draft("흡입력 긍정")

    service = RepairAI()
    book = store.codebook(generate_codebook(store, dataset, service, ""))
    assert len(service.calls) == 2 and "감성" in service.calls[1]
    assert [(c.name, c.definition) for c in book["codes"]] == [
        ("흡입력", "이물질을 흡입하는 성능과 효과에 관한 의견"),
    ]


def test_supplement_repairs_polarized_definition_before_adding_code():
    store = Store()
    run = setup_run(store, ["흡입력이 좋아요"], codes=[code()])

    class RepairAI(FakeAI):
        def classify(self, records, codes, context, feedback=None):
            target = next((c.id for c in codes if c.name == "흡입력"), None)
            return CodingBatch(results=[opinion("V0001", [issue(
                target, "흡입력이 좋아요", missing_code="흡입력" if target is None else "")])])

        def supplement(self, records, codes, context, candidates, constraints, frequency_summary=None, feedback=""):
            self.calls.append(feedback)
            return draft() if feedback else draft(definition="흡입력이 뛰어나다는 긍정적 평가")

    service = RepairAI()
    execute_run(store, run, service)
    assert store.run(run)["status"] == "completed"
    assert len(service.calls) == 2 and "감성" in service.calls[1]
    assert store.run(run)["round"] == 1


def test_repeated_invalid_drafts_are_never_saved():
    store = Store()
    dataset = create_dataset(store, ["흡입력이 좋아요"])

    class InvalidAI(FakeAI):
        def codebook(self, records, context, feedback=""):
            self.calls.append(feedback)
            return draft("흡입력 긍정")

    service = InvalidAI()
    with pytest.raises(ValueError, match="감성 분리"):
        generate_codebook(store, dataset, service, "")
    assert len(service.calls) == 3 and store.list_codebooks(dataset) == []


def portability_draft(names):
    return CodebookDraft(codes=[{
        "category": "사용성", "name": name, "definition": f"{name}에 관한 의견",
        "reason": "사용성 주제 관측", "evidence": [{"voc_id": "V0001", "quote": "가볍고 들고 다니기 편하고 버튼도 편해요"}],
    } for name in names])


@pytest.mark.parametrize("names", [
    ["무게 및 휴대성", "조작 및 휴대 편의성"],
    ["휴대성", "휴대 편의성"],
    ["무게/휴대성", "휴대 용이성"],
    ["디자인 및 색상", "색상 및 마감"],
])
def test_synonyms_and_repeated_compound_topics_cannot_create_separate_ai_codes(names):
    with pytest.raises(ValueError, match="같은 주제"):
        materialize_codes(portability_draft(names), [{"id": "V0001", "text": "가볍고 들고 다니기 편하고 버튼도 편해요"}])


def test_distinct_weight_portability_and_controls_are_accepted():
    codes = materialize_codes(portability_draft(["무게", "휴대성", "조작 편의성"]),
                              [{"id": "V0001", "text": "가볍고 들고 다니기 편하고 버튼도 편해요"}])
    assert [code.name for code in codes] == ["무게", "휴대성", "조작 편의성"]
    validate_ai_topic_overlap(portability_draft(["A/S 비용", "A/S 응대"]).codes)


def test_overlap_feedback_repairs_compound_draft_into_independent_topics():
    store = Store()
    dataset = create_dataset(store, ["가볍고 들고 다니기 편하고 버튼도 편해요"])

    class RepairAI(FakeAI):
        def codebook(self, records, context, feedback=""):
            self.calls.append(feedback)
            return portability_draft(["무게", "휴대성", "조작 편의성"] if feedback else
                                     ["무게 및 휴대성", "조작 및 휴대 편의성"])

    service = RepairAI()
    book = store.codebook(generate_codebook(store, dataset, service, ""))
    assert len(service.calls) == 2 and "휴대성" in service.calls[1]
    assert [code.name for code in book["codes"]] == ["무게", "휴대성", "조작 편의성"]


def test_supplement_checks_topics_against_existing_codes_without_rejecting_old_user_layout():
    old = portability_draft(["무게 및 휴대성", "조작 및 휴대 편의성"]).codes
    validate_ai_topic_overlap(portability_draft(["소음"]).codes, old)
    validate_ai_topic_overlap(portability_draft(["무게 및 휴대성"]).codes, old[:1])
    with pytest.raises(ValueError, match="같은 주제"):
        validate_ai_topic_overlap(portability_draft(["휴대 편의성"]).codes, old[:1])
