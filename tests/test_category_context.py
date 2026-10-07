from copy import deepcopy
import json
from uuid import uuid4

import pytest
from streamlit.components.v2.bidi_component.main import _make_trigger_id

from result_chart_helpers import chart_data, chart_event, sentiment_data, sentiment_event, table_event
from test_grouping import completed, open_results, response_table
from src.category_summary import OTHER_SENTIMENT, category_sentiment_labels
from src.models import CodingResult, Issue
from src.result_explorer import filtered_responses
from src.results import result_tables


def context(app):
    return next((chart for chart in app.get("bidi_component")
                 if chart.proto.component_name == "result_bars" and
                 json.loads(chart.proto.json).get("variant") == "category_context"), None)


def context_data(app):
    return json.loads(context(app).proto.json)


def context_event(app, label=None, **extra):
    chart = context(app)
    data = context_data(app)
    action = {"kind": "filter", "source_id": label, "signature": data["signature"], "nonce": uuid4().hex, **extra}
    state = app._tree.get_widget_states()
    trigger = state.widgets.add(id=_make_trigger_id(chart.proto.id, "events"))
    trigger.json_trigger_value = json.dumps([{"event": "action", "value": action}])
    app._run(state)
    assert not app.exception and not app.error
    return app


def add_cross_category_mixed(completed):
    store, run_id, _ = completed
    store.save_result(run_id, 0, "V0001", CodingResult(voc_id="V0001", response_type="opinions", issues=[
        Issue(code_id="speed", sentiment="긍정", evidence_text="빠름"),
        Issue(code_id="wrong", sentiment="긍정", evidence_text="오배송"),
        Issue(code_id="quality", sentiment="부정", evidence_text="오배송")]))


def test_summary_only_on_category_and_counts_unique_responses(completed, monkeypatch):
    app, _ = open_results(completed, monkeypatch)
    assert context(app) is None
    chart_event(app, "category", "filter", ["배송"])
    data = context_data(app)
    assert data["denominator"] == 4 and data["condition"] is None
    assert {r["id"]: (r["count"], r["percent"]) for r in data["rows"]} == {
        "긍정": (1, 25), "부정": (2, 50), "혼합": (1, 25), OTHER_SENTIMENT: (0, 0)}
    assert sum(r["percent"] for r in data["rows"]) == 100
    chart_event(app, "category", "filter", ["배송"])
    assert context(app) is None


def test_category_labels_use_its_opinions_and_other_states_not_entire_response(completed):
    add_cross_category_mixed(completed)
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    labels = category_sentiment_labels(originals, issues, ["배송"])
    assert labels.to_dict() == {"V0001": "긍정", "V0002": "부정", "V0003": "부정", "V0004": "긍정"}
    filters = {"_category_sentiment": {"categories": ["배송"], "sentiment": "긍정"}}
    view, group, _ = filtered_responses(originals, issues, codes, ["speed"], filters)
    assert set(view["VOC ID"]) == {"V0001", "V0004"}
    assert set(group.issues["코드 ID"]) == {"speed"}
    originals.loc[originals["VOC ID"].eq("V0001"), "응답 상태"] = "해석 검토 필요"
    assert category_sentiment_labels(originals, issues, ["배송"])["V0001"] == OTHER_SENTIMENT


@pytest.mark.parametrize("label,ids", [("긍정", {"V0004"}), ("부정", {"V0002", "V0003"}), ("혼합", {"V0001"})])
def test_context_click_filters_leaf_and_originals_keeps_parent_denominator_and_merges(completed, monkeypatch, label, ids):
    store, run_id, _ = completed
    before = deepcopy(store.results(run_id))
    app, prefix = open_results(completed, monkeypatch)
    chart_event(app, "code", "merge", ["speed"], ["wrong"])
    layout = deepcopy(app.session_state[prefix + "_layout"])
    chart_event(app, "category", "filter", ["배송"])
    left = chart_data(app, "category")
    context_event(app, label)
    assert sentiment_data(app)["selected"] is None
    assert app.session_state[prefix + "_drill_categories"] == ["배송"]
    assert chart_data(app, "category")["rows"] == left["rows"]
    right = chart_data(app)
    assert right["denominator"] == 5 and right["maximum"] == 5
    assert right["rows"][0]["count"] == len(ids)
    assert context_data(app)["rows"] == []
    assert context_data(app)["condition"] == f"배송 · {label} 필터 적용 · {len(ids)}건"
    chart_event(app, "code", "open", ["speed", "wrong"])
    assert set(response_table(app)["VOC ID"]) == ids
    app.button(key=prefix + "_reset").click().run()
    assert set(response_table(app)["VOC ID"]) == ids
    app.session_state[prefix + "_show_originals"] = False
    app.run()
    app.button(key=prefix + "_open_originals").click().run()
    assert set(response_table(app)["VOC ID"]) == ids
    context_event(app, label, kind="clear")
    assert response_table(app).empty and context_data(app)["denominator"] == 4
    assert app.session_state[prefix + "_drill_categories"] == ["배송"]
    assert app.session_state[prefix + "_layout"] == layout
    assert store.results(run_id) == before


def test_global_filter_shows_condition_without_second_chart(completed, monkeypatch):
    app, prefix = open_results(completed, monkeypatch)
    sentiment_event(app, "부정")
    chart_event(app, "category", "filter", ["배송"])
    assert context_data(app)["condition"] == "배송 · 부정 필터 적용 · 3건"
    assert context_data(app)["rows"] == [] and not context_data(app)["selected"]
    context_event(app, "긍정")  # A fabricated click cannot add another filter in condition-only mode.
    assert not app.session_state.get(prefix + "_category_sentiment")
    assert chart_data(app)["denominator"] == 3


def test_local_filter_clears_with_parent_or_global_change_and_all_clear(completed, monkeypatch):
    add_cross_category_mixed(completed)
    app, prefix = open_results(completed, monkeypatch)
    chart_event(app, "category", "filter", ["배송"])
    context_event(app, "긍정")
    assert context_data(app)["denominator"] == 2  # Includes V0001 despite globally mixed sentiment.
    app.button(key=prefix + "_clear_category_sentiment").click().run()
    assert app.session_state[prefix + "_drill_categories"] == ["배송"]
    context_event(app, "긍정")
    chart_event(app, "category", "filter", ["제품"])
    assert not app.session_state[prefix + "_category_sentiment"]
    assert context_data(app)["denominator"] == 1
    context_event(app, "부정")
    sentiment_event(app, "긍정")
    assert not app.session_state[prefix + "_category_sentiment"]
    assert app.session_state[prefix + "_drill_categories"] == []
    chart_event(app, "category", "filter", ["배송"])
    app.button(key=prefix + "_clear_all").click().run()
    assert context(app) is None and chart_data(app)["denominator"] == 5


def test_stale_zero_invalid_actions_and_empty_after_correction(completed, monkeypatch):
    app, prefix = open_results(completed, monkeypatch)
    chart_event(app, "category", "filter", ["배송"])
    context_event(app, "긍정", signature="old")
    assert not app.session_state.get(prefix + "_category_sentiment")
    context_event(app, OTHER_SENTIMENT)
    assert not app.session_state.get(prefix + "_category_sentiment")
    context_event(app, "invalid")
    assert not app.session_state.get(prefix + "_category_sentiment")
    context_event(app, "긍정")
    chart_event(app, "code", "open", ["speed"])
    table_event(app, "sentiment", id="V0004", sentiments=["부정", "부정"])
    assert response_table(app).empty and chart_data(app)["rows"] == []
    assert context_data(app)["condition"] == "배송 · 긍정 필터 적용 · 0건"
    context_event(app, "긍정", kind="clear")
    assert context_data(app)["denominator"] == 4 and len(chart_data(app)["rows"]) == 2
