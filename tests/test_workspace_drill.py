from copy import deepcopy

import pytest

from src.chart_data import COUNT, DENOMINATOR, PERCENT
from src.grouping import group_results
from src.models import CodingResult, Issue
from src.result_groups import initial_layout, change_layout, group_id, drill_code_chart, chart_action
from src.results import result_tables
from test_grouping import completed, open_results, response_table
from result_chart_helpers import chart_data, chart_event, sentiment_data, sentiment_event


def add_product(completed):
    store, run_id, _ = completed
    store.save_result(run_id, 0, "V0002", CodingResult(voc_id="V0002", response_type="opinions", issues=[
        Issue(code_id="wrong", sentiment="부정", evidence_text="오배송"),
        Issue(code_id="quality", sentiment="부정", evidence_text="오배송")]))


def test_sentiment_category_leaf_originals_and_clear_keep_denominator(completed, monkeypatch):
    add_product(completed)
    app, prefix = open_results(completed, monkeypatch)
    sentiment_event(app, "부정")
    left = deepcopy(chart_data(app, "category")["rows"])
    chart_event(app, "category", "filter", ["제품"])
    assert response_table(app).empty
    assert [(r["count"], r["percent"]) for r in chart_data(app, "category")["rows"]] == [
        (r["count"], r["percent"]) for r in left]
    right = chart_data(app)
    assert right["denominator"] == 2
    assert right["maximum"] == chart_data(app, "category")["maximum"] == 2
    assert [(r["members"], r["count"], r["percent"]) for r in right["rows"]] == [(["quality"], 1, 50)]
    chart_event(app, "code", "open", ["quality"])
    assert set(response_table(app)["VOC ID"]) == {"V0002"}
    app.session_state[prefix + "_show_originals"] = False
    app.run()
    assert app.session_state[prefix + "_drill_categories"] == ["제품"]
    chart_event(app, "category", "filter", ["제품"])
    assert len(chart_data(app)["rows"]) == 2
    assert chart_data(app)["denominator"] == 2
    assert sentiment_data(app)["selected"] == "부정"


def test_clear_conditions_keeps_merges_and_sentiment_change_clears_category(completed, monkeypatch):
    app, prefix = open_results(completed, monkeypatch)
    chart_event(app, "code", "merge", ["speed"], ["wrong"])
    layout = deepcopy(app.session_state[prefix + "_layout"])
    sentiment_event(app, "혼합")
    chart_event(app, "category", "filter", ["배송"])
    sentiment_event(app, "긍정")
    assert app.session_state[prefix + "_drill_categories"] == []
    app.button(key=prefix + "_clear_all").click().run()
    assert sentiment_data(app)["selected"] is None and chart_data(app)["denominator"] == 5
    assert app.session_state[prefix + "_layout"] == layout


def test_cross_category_group_projection_preserves_hidden_members_and_rejects_merge(completed):
    add_product(completed)
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    grouped = group_results(originals, issues, codes)
    layout = change_layout(initial_layout(codes), "code", group_id("code", ["wrong"]), group_id("code", ["quality"]))
    before = deepcopy(layout)
    frame = drill_code_chart(grouped, codes, layout, ["배송"])
    row = frame[frame["id"].eq(group_id("code", ["wrong"]))].iloc[0]
    assert row["members"] == ["wrong"] and row[COUNT] == 3 and row[PERCENT] == 60
    assert row[DENOMINATOR] == 5 and not row["can_merge"]
    with pytest.raises(ValueError, match="대분류 조건"):
        chart_action(store, store.run(run_id), layout, "code", frame, "current", {
            "kind": "merge", "signature": "current", "source_id": row["id"],
            "target_id": group_id("code", ["speed"])})
    assert layout == before
    assert "quality" in drill_code_chart(grouped, codes, layout, [])["members"].iloc[0]
