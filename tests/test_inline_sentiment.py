from copy import deepcopy

import pytest

from result_chart_helpers import chart_event, table_event, table_spec
from test_grouping import completed, open_results, response_table
from test_inline_classification import spec
from src.category_summary import response_sentiments
from src.chart_data import COUNT
from src.models import CodingResult
from src.response_table import classification_edit_data, save_table_classification, save_table_sentiment, table_action
from src.result_explorer import filtered_responses
from src.results import result_tables


def test_unknown_cell_edit_preserves_other_opinions_and_refreshes_filtered_view(completed, monkeypatch):
    store, run_id, codes = completed
    source = CodingResult.model_validate(store.results(run_id)[0]["result"])
    source.issues[0].sentiment = "판단 불가"
    source.issues[0].subject_label = "배송 업체"
    source.issues[0].subject_evidence_text = "빠름"
    source.issues[0].subject_evidence_source = "original"
    store.save_result(run_id, 0, "V0001", source)
    store.update_status(run_id, "completed")
    raw = deepcopy(store.results(run_id))
    app, prefix = open_results(completed, monkeypatch)
    table_event(app, "filter", column="감성", values=["판단 불가"])
    data = table_spec(app)
    assert [option["value"] for option in data["sentiment_options"]] == ["긍정", "부정", "중립", "판단 불가"]
    assert data["rows"][0]["감성"] == "판단 불가"
    assert [item["sentiment"] for item in data["rows"][0]["opinions"]] == ["판단 불가", "부정"]
    table_event(app, "sentiment", id="V0001", sentiments=["긍정", "부정"])
    changed = store.corrections(run_id)["V0001"]
    expected = deepcopy(raw[0]["result"]["issues"])
    expected[0]["sentiment"] = "긍정"
    assert changed["result"]["issues"] == expected
    assert changed["reason"] == "표에서 감성 변경" and changed["edits"][0]["fields"] == ["sentiment"]
    assert store.results(run_id) == raw and store.run(run_id)["result_revision"] == 1
    assert response_table(app).empty  # 바꾼 감성이 판단 불가 필터를 벗어난다.
    assert app.session_state.get(prefix + "_selected_voc") is None
    assert any(success.value == "감성을 변경했습니다." for success in app.success)
    app.button(key=prefix + "_reset").click().run()
    assert response_table(app).set_index("VOC ID").loc["V0001", "감성"] == "긍정 · 부정"
    originals, issues = result_tables(store, run_id)
    view, grouped, _ = filtered_responses(originals, issues, codes, None, {})
    assert response_sentiments(view, grouped.issues).set_index("감성").loc["혼합", COUNT] == 1


def test_same_text_separate_vocs_and_repeated_sentiment_changes_preserve_history(completed):
    store, run_id, codes = completed
    raw = deepcopy(store.results(run_id))
    for sentiment in ["판단 불가", "중립", "긍정"]:
        data = spec(store, run_id, codes)
        kind, (voc_id, values) = table_action(data, {"kind": "sentiment", "signature": data["signature"],
            "id": "V0002", "sentiments": [sentiment]})
        assert kind == "sentiment"
        save_table_sentiment(store, store.run(run_id), voc_id, values)
    effective = {row["voc_id"]: row["result"] for row in store.effective_results(run_id)}
    assert effective["V0002"]["issues"][0]["sentiment"] == "긍정"
    assert effective["V0003"]["issues"][0]["sentiment"] == "부정"
    assert effective["V0002"]["issues"][0]["code_id"] == "wrong"
    history = store.correction_history(run_id)
    assert len(history) == 3 and len(history[-1]["edits"]) == 3
    assert history[-1]["previous"]["result"]["issues"][0]["sentiment"] == "중립"
    assert store.run(run_id)["result_revision"] == 3 and store.results(run_id) == raw


def test_invalid_noop_old_and_no_opinion_sentiment_edits_do_not_write(completed):
    store, run_id, codes = completed
    data = spec(store, run_id, codes)
    base = {"kind": "sentiment", "signature": data["signature"], "id": "V0001", "sentiments": ["중립", "부정"]}
    for change in [{"signature": "old"}, {"id": "missing"}, {"id": "V0005"}, {"sentiments": []},
                   {"sentiments": ["중립"]}, {"sentiments": "중립"}, {"sentiments": [None, "부정"]},
                   {"sentiments": ["혼합", "부정"]}, {"sentiments": ["긍정", "부정"]}]:
        with pytest.raises(ValueError):
            table_action(data, {**base, **change})
    assert not store.correction_history(run_id)
    old_run = store.run(run_id)
    save_table_sentiment(store, old_run, "V0001", ["중립", "부정"])
    current = deepcopy(store.effective_results(run_id))
    with pytest.raises(ValueError, match="다른 화면"):
        save_table_sentiment(store, old_run, "V0001", ["부정", "부정"])
    with pytest.raises(ValueError, match="변경한"):
        save_table_sentiment(store, store.run(run_id), "V0001", ["중립", "부정"])
    with pytest.raises(ValueError, match="상세"):
        save_table_sentiment(store, store.run(run_id), "V0005", [])
    assert store.effective_results(run_id) == current and store.run(run_id)["result_revision"] == 1


def test_sentiment_edit_keeps_previous_classification_edit(completed):
    store, run_id, _ = completed
    raw = deepcopy(store.results(run_id))
    save_table_classification(store, store.run(run_id), "V0002", ["quality"])
    save_table_sentiment(store, store.run(run_id), "V0002", ["긍정"])
    changed = store.corrections(run_id)["V0002"]
    assert changed["result"]["issues"][0]["code_id"] == "quality"
    assert changed["result"]["issues"][0]["sentiment"] == "긍정"
    assert [edit["fields"] for edit in changed["edits"]] == [["code_id"], ["sentiment"]]
    assert store.results(run_id) == raw


def test_leaf_sentiment_edit_changes_only_selected_classification(completed, monkeypatch):
    store, run_id, _ = completed
    raw = deepcopy(store.results(run_id))
    app, prefix = open_results(completed, monkeypatch)
    chart_event(app, "code", "open", ["wrong"])
    data = table_spec(app)
    row = next(row for row in data["rows"] if row["id"] == "V0001")
    assert row["분류"] == "[배송] 오배송"
    assert row["sentiment_indices"] == [1]
    assert [item["code_id"] for item in row["opinions"]] == ["speed", "wrong"]
    # 숨긴 의견의 감성까지 바꾸려는 이벤트는 저장 전에 거절한다.
    with pytest.raises(ValueError, match="현재 선택한 분류"):
        table_action(data, {"kind": "sentiment", "signature": data["signature"],
            "id": "V0001", "sentiments": ["부정", "중립"]})
    assert not store.correction_history(run_id)
    table_event(app, "sentiment", id="V0001", sentiments=["긍정", "중립"])
    changed = store.corrections(run_id)["V0001"]
    expected = deepcopy(raw[0]["result"]["issues"])
    expected[1]["sentiment"] = "중립"
    assert changed["result"]["issues"] == expected
    assert changed["edits"][0]["fields"] == ["sentiment"]
    assert store.results(run_id) == raw and store.run(run_id)["result_revision"] == 1
    assert response_table(app).set_index("VOC ID").loc["V0001", "감성"] == "중립"
    app.button(key=prefix + "_reset").click().run()
    assert response_table(app).set_index("VOC ID").loc["V0001", "감성"] == "긍정 · 중립"


def test_scope_includes_all_matching_opinions_and_keeps_classification_editing(completed):
    store, run_id, codes = completed
    run = store.run(run_id)
    leaf = classification_edit_data(store, run, codes, ["speed"])
    assert leaf["sentiment_indices"]["V0001"] == [0]
    assert leaf["sentiment_indices"]["V0004"] == [0, 1]  # 같은 세부분류의 복수 의견
    assert leaf["sentiment_indices"]["V0002"] == []
    assert len(leaf["rows"]["V0001"]) == 2  # 저장 시 숨긴 의견을 보존하기 위해 전체 원본은 유지한다.
    for selected in (None, ["speed", "wrong"]):  # 전체·대분류·합친 세부분류
        data = classification_edit_data(store, run, codes, selected)
        assert data["sentiment_indices"]["V0001"] == [0, 1]
        assert data["sentiment_indices"]["V0004"] == [0, 1]
