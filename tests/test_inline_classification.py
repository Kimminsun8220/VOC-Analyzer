from copy import deepcopy

import pytest

from result_chart_helpers import chart_data, table_event, table_spec
from test_grouping import completed, open_results, response_table
from src.models import CodingResult, Issue
from src.response_table import classification_edit_data, save_table_classification, table_action, table_data
from src.result_explorer import filtered_responses
from src.results import result_tables


def spec(store, run_id, codes, filters=None):
    run = store.run(run_id)
    originals, issues = result_tables(store, run_id)
    view, _, options = filtered_responses(originals, issues, codes, None, filters or {})
    return table_data(view, options, filters or {}, None, [run["result_revision"]],
                      classification_edit_data(store, run, codes))


def test_filtered_cell_edits_one_opinion_without_dropping_hidden_opinion(completed, monkeypatch):
    store, run_id, _ = completed
    source = CodingResult.model_validate(store.results(run_id)[0]["result"])
    source.issues[0].subject_label = "배송 업체"
    source.issues[0].subject_evidence_text = "빠름"
    source.issues[0].subject_evidence_source = "original"
    store.save_result(run_id, 0, "V0001", source)
    store.update_status(run_id, "completed")
    before = deepcopy(store.results(run_id))
    app, prefix = open_results(completed, monkeypatch)
    table_event(app, "filter", column="감성", values=["긍정"])
    row = next(row for row in table_spec(app)["rows"] if row["id"] == "V0001")
    assert row["분류"] == "[배송] 배송 속도"  # 화면에는 긍정 의견만 보인다.
    assert [(item["code_id"], item["sentiment"]) for item in row["opinions"]] == [("speed", "긍정"), ("wrong", "부정")]
    table_event(app, "classify", id="V0001", code_ids=["quality", "wrong"])
    changed = store.corrections(run_id)["V0001"]
    expected = deepcopy(before[0]["result"]["issues"])
    expected[0]["code_id"] = "quality"
    assert changed["result"]["issues"] == expected
    assert changed["reason"] == "표에서 분류 변경" and changed["edits"][0]["fields"] == ["code_id"]
    assert store.results(run_id) == before
    assert app.session_state.get(prefix + "_selected_voc") is None
    assert not any(button.label == "이 응답 수정" for button in app.button)
    assert response_table(app).iloc[0]["분류"] == "[제품] 품질"
    assert any(success.value == "분류를 변경했습니다." for success in app.success)
    assert next(row for row in chart_data(app)["rows"] if row["members"] == ["quality"])["count"] == 1


def test_same_text_different_voc_and_repeated_updates_preserve_history(completed):
    store, run_id, codes = completed
    raw = deepcopy(store.results(run_id))
    for code in ("quality", "speed"):
        data = spec(store, run_id, codes)
        kind, (voc_id, code_ids) = table_action(data, {"kind": "classify", "signature": data["signature"],
                                                    "id": "V0002", "code_ids": [code]})
        assert kind == "classify"
        save_table_classification(store, store.run(run_id), voc_id, code_ids)
    effective = {row["voc_id"]: row["result"] for row in store.effective_results(run_id)}
    assert effective["V0002"]["issues"][0]["code_id"] == "speed"
    assert effective["V0003"]["issues"][0]["code_id"] == "wrong"
    history = store.correction_history(run_id)
    assert len(history) == 2 and len(history[-1]["edits"]) == 2
    assert history[-1]["previous"]["result"]["issues"][0]["code_id"] == "quality"
    assert store.run(run_id)["result_revision"] == 2 and store.results(run_id) == raw


def test_invalid_missing_noop_and_old_events_do_not_write(completed):
    store, run_id, codes = completed
    data = spec(store, run_id, codes)
    base = {"kind": "classify", "signature": data["signature"], "id": "V0001", "code_ids": ["quality", "wrong"]}
    for change in ({"signature": "old"}, {"id": "missing"}, {"id": "V0005"},
                   {"code_ids": []}, {"code_ids": ["quality"]}, {"code_ids": "quality"},
                   {"code_ids": [None, "wrong"]}, {"code_ids": ["unknown", "wrong"]},
                   {"code_ids": ["speed", "wrong"]}):
        with pytest.raises(ValueError):
            table_action(data, {**base, **change})
    assert not store.correction_history(run_id)
    old_run = store.run(run_id)
    save_table_classification(store, old_run, "V0001", ["quality", "wrong"])
    current = deepcopy(store.effective_results(run_id))
    with pytest.raises(ValueError, match="다른 화면"):
        save_table_classification(store, old_run, "V0001", ["wrong", "wrong"])
    with pytest.raises(ValueError, match="변경한"):
        save_table_classification(store, store.run(run_id), "V0001", ["quality", "wrong"])
    assert store.effective_results(run_id) == current and store.run(run_id)["result_revision"] == 1


def test_review_failed_unclear_and_unmatched_cannot_be_silently_confirmed(completed):
    store, run_id, codes = completed
    store.save_result(run_id, 0, "V0002", None, "검증 실패")
    store.save_result(run_id, 0, "V0003", CodingResult(voc_id="V0003", response_type="unclear", review_reason="확인"))
    store.save_result(run_id, 0, "V0004", CodingResult(voc_id="V0004", response_type="opinions", issues=[
        Issue(code_id=None, missing_code="다른 의미", sentiment="긍정", evidence_text="빠름")]))
    # 이전 변경의 승계 검토는 분류 한 셀 변경으로 전체 확인을 대신할 수 없다.
    with store.connect() as db:
        store.insert_correction(db, store.run(run_id), "V0001", None, [], {}, "승계 확인 필요", status="needs_review")
    store.update_status(run_id, "needs_review")
    data = spec(store, run_id, codes)
    assert all("opinions" not in row for row in data["rows"])
    with pytest.raises(ValueError, match="상세"):
        save_table_classification(store, store.run(run_id), "V0001", ["quality", "wrong"])
    assert len(store.correction_history(run_id)) == 1


def test_edit_leaving_classification_filter_removes_only_that_response(completed, monkeypatch):
    app, prefix = open_results(completed, monkeypatch)
    table_event(app, "filter", column="분류", values=["[배송] 오배송"])
    table_event(app, "classify", id="V0002", code_ids=["quality"])
    assert response_table(app)["VOC ID"].tolist() == ["V0003"]  # 열 필터는 표시된 분류 조합의 정확한 값이다.
    assert app.session_state[prefix + "_filters"] == {"분류": {"values": ["[배송] 오배송"]}}
    app.button(key=prefix + "_reset").click().run()
    assert len(response_table(app)) == 5
    assert response_table(app).set_index("VOC ID").loc["V0002", "분류"] == "[제품] 품질"


def test_leaf_classification_edit_preserves_hidden_opinions(completed, monkeypatch):
    from result_chart_helpers import chart_event
    store, run_id, _ = completed
    before = deepcopy(store.results(run_id))
    app, prefix = open_results(completed, monkeypatch)
    chart_event(app, "code", "open", ["wrong"])
    data = table_spec(app)
    row = next(row for row in data["rows"] if row["id"] == "V0001")
    assert row["sentiment_indices"] == [1]
    with pytest.raises(ValueError, match="현재 선택한 분류"):
        table_action(data, {"kind": "classify", "signature": data["signature"],
            "id": "V0001", "code_ids": ["quality", "wrong"]})
    table_event(app, "classify", id="V0001", code_ids=["speed", "quality"])
    changed = store.corrections(run_id)["V0001"]["result"]["issues"]
    assert changed[0] == before[0]["result"]["issues"][0]
    assert changed[1]["code_id"] == "quality"
    assert "V0001" not in response_table(app)["VOC ID"].tolist()
    assert store.results(run_id) == before


def test_edit_scope_matches_sentiment_filters(completed):
    store, run_id, codes = completed
    for filters in ({"감성": {"values": ["긍정"]}}, {"_response_sentiment": "긍정"}):
        data = classification_edit_data(store, store.run(run_id), codes, ["speed", "wrong"], filters)
        assert data["sentiment_indices"]["V0001"] == [0]
        assert len(data["rows"]["V0001"]) == 2
