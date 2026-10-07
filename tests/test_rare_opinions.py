from src.models import CodebookDraft, CodingBatch
from src.results import result_tables
from src.storage import Store
from src.workflow import execute_run, missing_frequency_summary
from tests.test_workflow import FakeAI, issue, opinion, setup_run


def test_missing_frequency_counts_unique_responses_and_keeps_one_percent_boundary():
    candidates = [
        {"voc_id": "V1", "missing_code": "설명서 글씨"},
        {"voc_id": "V1", "missing_code": "설명서 글씨"},
        {"voc_id": "V2", "missing_code": "구성품 누락"},
        {"voc_id": "V3", "missing_code": "구성품 누락"},
    ]
    summary = missing_frequency_summary(candidates, 200)
    assert summary["total_responses"] == 200
    assert [(m["response_count"], m["percent"]) for m in summary["meanings"]] == [(1, 0.5), (2, 1.0)]


def test_rare_other_supplement_preserves_mixed_opinions_and_finishes_without_review():
    store = Store()
    run_id = setup_run(store, ["빠름"] * 200 + ["빠름 설명서 글씨가 작음"])

    class RareAI(FakeAI):
        summaries = []

        def classify(self, records, codes, context, feedback=None):
            other = next((code.id for code in codes if code.name == "기타"), None)
            results = []
            for row in records:
                items = [issue("C1", "빠름", "긍정")]
                if "글씨" in row["text"]:
                    items.append(issue(other, "설명서 글씨가 작음", "부정",
                                       missing_code="설명서 글씨 크기" if other is None else ""))
                results.append(opinion(row["id"], items))
            return CodingBatch(results=results)

        def supplement(self, records, codes, context, candidates, constraints, frequency_summary=None):
            self.summaries.append(frequency_summary)
            assert len(records) == 1
            assert frequency_summary["total_responses"] == 201
            assert frequency_summary["meanings"][0]["response_count"] == 1
            assert frequency_summary["meanings"][0]["percent"] < 1
            return CodebookDraft(codes=[{
                "category": "기타", "name": "기타", "definition": "지엽적이고 희소한 설명서 글씨 의견",
                "reason": "전체 201건 중 1건인 세부 의견을 기타로 묶음",
                "evidence": [{"voc_id": records[0]["id"], "quote": "설명서 글씨가 작음"}],
            }])

    service = RareAI()
    execute_run(store, run_id, service)
    assert store.run(run_id)["status"] == "completed"
    assert len(service.summaries) == 1
    originals, issues = result_tables(store, run_id)
    rare = originals[originals["VOC 원문"].str.contains("글씨")].iloc[0]
    assert rare["응답 상태"] == "의견 있음"
    assert rare["전체 감성"] == "혼합"
    opinions = issues[issues["VOC ID"] == rare["VOC ID"]]
    assert set(zip(opinions["세부분류"], opinions["감성"], opinions["원문 근거"])) == {
        ("배송 속도", "긍정", "빠름"), ("기타", "부정", "설명서 글씨가 작음"),
    }
