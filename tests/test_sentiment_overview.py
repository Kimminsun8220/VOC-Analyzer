from copy import deepcopy

import pytest

from result_chart_helpers import chart_data, chart_event, charts, sentiment_data, sentiment_event, table_event
from test_grouping import completed, open_results, response_table
from src.category_summary import OTHER_SENTIMENT, overview_mentions
from src.chart_data import COUNT
from src.models import CodingResult, Issue
from src.result_explorer import filtered_responses
from src.results import result_tables


COHORTS = [("긍정", {"V0001", "V0004"}), ("부정", {"V0001", "V0002", "V0003"}),
           ("무응답", {"V0005"})]


@pytest.mark.parametrize("label,ids", COHORTS)
def test_segment_count_matches_unique_vocs_and_keeps_all_their_opinions(completed, label, ids):
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    view, grouped, _ = filtered_responses(originals, issues, codes, None, {"_response_sentiment": label})
    assert set(view["VOC ID"]) == ids
    assert len(view) == overview_mentions(originals, issues).set_index("감성").loc[label, COUNT]
    assert set(grouped.issues["감성"]) <= {label}
    if label == "혼합":
        assert set(grouped.issues["코드 ID"]) == {"speed", "wrong"}
        assert set(grouped.issues["감성"]) == {"긍정", "부정"}


def test_reviewed_status_controls_bucket_even_with_positive_opinion(completed):
    store, run_id, codes = completed
    store.save_result(run_id, 0, "V0002", CodingResult(voc_id="V0002", response_type="opinions", issues=[
        Issue(code_id="wrong", sentiment="긍정", evidence_text="오배송"),
        Issue(code_id=None, sentiment="부정", evidence_text="오배송", missing_code="검토")]))
    originals, issues = result_tables(store, run_id)
    view, _, _ = filtered_responses(originals, issues, codes, None, {"_response_sentiment": OTHER_SENTIMENT})
    assert set(view["VOC ID"]) == {"V0002", "V0005"}
    positive, _, _ = filtered_responses(originals, issues, codes, None, {"_response_sentiment": "긍정"})
    assert set(positive["VOC ID"]) == {"V0001", "V0004"}


@pytest.mark.parametrize("label,ids", COHORTS)
def test_click_filters_both_charts_and_reclick_restores_all(completed, monkeypatch, label, ids):
    store, run_id, _ = completed
    before = deepcopy(store.results(run_id))
    app, prefix = open_results(completed, monkeypatch)
    chart_event(app, "code", "merge", ["speed"], ["wrong"])
    layout = deepcopy(app.session_state[prefix + "_layout"])
    sentiment_event(app, label)
    assert response_table(app).empty
    assert not app.session_state[prefix + "_show_originals"]
    assert app.session_state[prefix + "_dashboard_sentiment"] == label
    assert sentiment_data(app)["selected"] == label
    if label == "무응답":
        assert not charts(app)
        assert any("분류된 의견이 없습니다" in info.value for info in app.info)
    else:
        for level in ("category", "code"):
            data = chart_data(app, level)
            assert data["scope"] == label and data["denominator"] == len(ids)
            assert len(data["rows"]) == 1
            assert data["rows"][0]["count"] == len(ids)
            assert data["rows"][0]["percent"] == 100
    assert app.session_state[prefix + "_layout"] == layout
    assert sum(row["count"] for row in sentiment_data(app)["rows"]) == 6
    sentiment_event(app, label)
    assert not app.session_state[prefix + "_dashboard_sentiment"]
    assert sentiment_data(app)["selected"] is None
    for level in ("category", "code"):
        data = chart_data(app, level)
        assert data["denominator"] == 5
        assert data["rows"][0]["count"] == 4 and data["rows"][0]["percent"] == 80
    assert app.session_state[prefix + "_layout"] == layout
    assert store.results(run_id) == before


def test_another_sentiment_switches_graphs_and_preserves_all_mixed_classifications(completed, monkeypatch):
    app, prefix = open_results(completed, monkeypatch)
    sentiment_event(app, "긍정")
    assert [row["members"] for row in chart_data(app)["rows"]] == [["speed"]]
    sentiment_event(app, "부정")
    assert chart_data(app)["denominator"] == 3
    assert [row["members"] for row in chart_data(app)["rows"]] == [["wrong"]]
    app.button(key=prefix + "_open_originals").click().run()
    assert set(response_table(app)["VOC ID"]) == {"V0001", "V0002", "V0003"}
    assert set(response_table(app)["감성"]) == {"부정"}


def test_category_popup_filters_and_whole_view_keep_visible_dashboard_scope(completed, monkeypatch):
    app, prefix = open_results(completed, monkeypatch)
    sentiment_event(app, "부정")
    chart_event(app, "category", "open", ["배송"])
    assert set(response_table(app)["VOC ID"]) == {"V0001", "V0002", "V0003"}
    assert "배송 · 부정" in [header.value for header in app.subheader]
    table_event(app, "filter", column="VOC 원문", search="없음")
    assert response_table(app).empty
    assert chart_data(app)["denominator"] == 3
    chart_event(app, "category", "open", ["배송"])
    assert len(response_table(app)) == 3 and app.session_state[prefix + "_filters"] == {}
    app.button(key=prefix + "_reset").click().run()
    assert len(response_table(app)) == 3
    assert app.session_state[prefix + "_dashboard_sentiment"] == "부정"
    app.session_state[prefix + "_show_originals"] = False
    app.run()
    assert response_table(app).empty and sentiment_data(app)["selected"] == "부정"
    app.button(key=prefix + "_open_originals").click().run()
    assert len(response_table(app)) == 3
    assert "배송 · 부정" in [header.value for header in app.subheader]
    sentiment_event(app, "부정")
    assert chart_data(app)["denominator"] == 5 and response_table(app).empty


def test_removed_mixed_event_is_ignored(completed, monkeypatch):
    app, prefix = open_results(completed, monkeypatch)
    sentiment_event(app, "혼합")
    assert not app.session_state.get(prefix + "_dashboard_sentiment")
    assert "혼합" not in {r["id"] for r in sentiment_data(app)["rows"]}


def test_sentiment_edit_recomputes_bar_and_selected_cohort_without_changing_ai(completed, monkeypatch):
    store, run_id, _ = completed
    before = deepcopy(store.results(run_id))
    app, prefix = open_results(completed, monkeypatch)
    sentiment_event(app, "긍정")
    app.button(key=prefix + "_open_originals").click().run()
    table_event(app, "sentiment", id="V0004", sentiments=["부정", "부정"])
    assert set(response_table(app)["VOC ID"]) == {"V0001"}
    assert charts(app)
    assert {row["id"]: row["count"] for row in sentiment_data(app)["rows"]} == {
        "긍정": 1, "부정": 4, "무응답": 1}
    sentiment_event(app, "긍정")
    assert chart_data(app)["denominator"] == 5
    assert store.results(run_id) == before


def test_stale_or_invalid_segment_events_do_not_change_current_scope(completed, monkeypatch):
    app, prefix = open_results(completed, monkeypatch)
    sentiment_event(app, "긍정", signature="old")
    assert len(response_table(app)) == 5
    assert any("결과가 변경" in info.value for info in app.info)
    sentiment_event(app, "존재하지 않는 감성")
    assert app.session_state[prefix + "_filters"] == {}
    sentiment_event(app, "긍정", kind="open")
    assert len(response_table(app)) == 5
    sentiment_event(app, "긍정")
    assert response_table(app).empty
    assert chart_data(app)["denominator"] == 2
    assert app.session_state[prefix + "_dashboard_sentiment"] == "긍정"
