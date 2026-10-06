from copy import deepcopy

import pandas as pd
import pytest

from src.chart_data import COUNT, DENOMINATOR, PERCENT
from src.grouping import group_results
from src.result_groups import (initial_layout, group_id, change_layout, grouped_chart, grouped_dashboard,
                               chart_signature, chart_action, load_code_group)
from src.result_explorer import response_view, visible_group, download_frame
from src.results import result_tables
from src.models import CodingResult, Issue
from test_grouping import completed, open_results, response_table
from result_chart_helpers import chart_data, chart_event, charts
from test_corrections import rows_for, save


def test_merge_counts_union_and_preserves_book_results_and_originals(completed):
    store, run_id, codes = completed
    run = store.run(run_id)
    book = store.codebook(run["codebook_id"])
    before_book, before_run, before_results = deepcopy(book), deepcopy(run), deepcopy(store.results(run_id))
    originals, issues = result_tables(store, run_id)
    before_originals, before_issues = originals.copy(deep=True), issues.copy(deep=True)
    layout = initial_layout(codes)
    merged = change_layout(layout, "code", group_id("code", ["wrong"]), group_id("code", ["speed"]))
    data = grouped_chart(group_results(originals, issues, codes), codes, merged, "code")
    assert data["분류"].tolist() == ["[배송] 배송 속도/오배송"]
    assert data[COUNT].tolist() == [4] and data[PERCENT].tolist() == [80] and data[DENOMINATOR].tolist() == [5]
    assert layout == initial_layout(codes)
    assert store.codebook(book["id"]) == before_book and store.run(run_id) == before_run
    assert store.results(run_id) == before_results and not store.corrections(run_id)
    pd.testing.assert_frame_equal(originals, before_originals)
    pd.testing.assert_frame_equal(issues, before_issues)


def test_category_union_and_nested_undo_restore_original_partitions(completed):
    store, run_id, codes = completed
    store.save_result(run_id, 0, "V0002", CodingResult(voc_id="V0002", response_type="opinions", issues=[
        Issue(code_id="wrong", sentiment="부정", evidence_text="오배송"),
        Issue(code_id="quality", sentiment="부정", evidence_text="오배송")]))
    originals, issues = result_tables(store, run_id)
    base = initial_layout(codes)
    first = change_layout(base, "category", group_id("category", ["배송"]), group_id("category", ["제품"]))
    frame = grouped_chart(group_results(originals, issues, codes), codes, first, "category")
    assert frame["분류"].tolist() == ["배송/제품"] and frame[COUNT].tolist() == [4]
    second = change_layout(first, "code", group_id("code", ["speed"]), group_id("code", ["wrong"]))
    third = change_layout(second, "code", group_id("code", ["speed", "wrong"]), group_id("code", ["quality"]))
    assert grouped_chart(group_results(originals, issues, codes), codes, third, "code")[COUNT].tolist() == [4]
    undone = change_layout(third, "undo")
    assert undone["category"] == second["category"] and undone["code"] == second["code"]
    undone = change_layout(change_layout(undone, "undo"), "undo")
    assert undone["category"] == base["category"] and undone["code"] == base["code"]


@pytest.mark.parametrize("action", [
    {"kind": "open", "source_id": "unknown", "signature": "current"},
    {"kind": "merge", "source_id": group_id("code", ["speed"]), "target_id": group_id("code", ["speed"]), "signature": "current"},
    {"kind": "merge", "source_id": group_id("code", ["speed"]), "target_id": group_id("category", ["배송"]), "signature": "current"},
    {"kind": "open", "source_id": group_id("code", ["speed"]), "signature": "old"},
])
def test_invalid_or_old_actions_do_not_change_layout(completed, action):
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    layout = initial_layout(codes)
    frame = grouped_chart(group_results(originals, issues, codes), codes, layout, "code")
    with pytest.raises(ValueError):
        chart_action(store, store.run(run_id), layout, "code", frame, "current", action)
    assert layout == initial_layout(codes)


def test_old_result_revision_and_hidden_rows_cannot_be_operated(completed):
    store, run_id, codes = completed
    run = store.run(run_id)
    originals, issues = result_tables(store, run_id)
    layout = initial_layout(codes)
    frame = grouped_chart(group_results(originals, issues, codes), codes, layout, "code")
    action = {"kind": "open", "source_id": group_id("code", ["speed"]), "signature": "current"}
    with pytest.raises(ValueError):
        chart_action(store, run, layout, "code", frame.iloc[:1], "current", action)
    rows = rows_for(store, run_id)
    rows[0]["sentiment"] = "중립"
    save(store, run_id, rows)
    with pytest.raises(ValueError, match="변경"):
        chart_action(store, run, layout, "code", frame, "current", action)


def test_merged_current_csv_uses_same_response_union_and_current_denominator(completed):
    store, run_id, codes = completed
    run, originals = store.run(run_id), result_tables(store, run_id)[0]
    book = store.codebook(run["codebook_id"])
    issues = result_tables(store, run_id)[1]
    layout = load_code_group(initial_layout(codes), codes, ["speed", "wrong"])
    selected = group_results(originals, issues, codes, ["speed", "wrong"])
    view = response_view(originals, selected, ["speed", "wrong"], "전체")
    scoped = visible_group(originals, issues, codes, view, ["speed", "wrong"], "전체")
    frame = download_frame("현재 집계", originals, (view, scoped), run, book, "배송 경험", layout)
    leaf = frame[frame["집계 종류"].eq("세부분류")]
    assert leaf[COUNT].tolist() == [4] and leaf[PERCENT].tolist() == [100]
    assert frame[DENOMINATOR].eq(4).all() and view["VOC ID"].is_unique
    empty = group_results(originals.iloc[:0], pd.DataFrame(), codes)
    assert grouped_dashboard(empty, codes, layout).codes.empty


def test_ui_merge_open_undo_and_revision_refresh_without_ai(completed, monkeypatch):
    store, run_id, _ = completed
    before = deepcopy(store.results(run_id))
    app, prefix = open_results(completed, monkeypatch)
    assert len(charts(app)) == 2 and not app.multiselect
    assert not any(pop.proto.popover.label == "여러 분류 함께 보기" for pop in app.get("popover"))
    chart_event(app, "code", "merge", ["speed"], ["wrong"])
    assert response_table(app).empty
    assert [row["count"] for row in chart_data(app)["rows"]] == [4]
    chart_event(app, "code", "open", ["speed", "wrong"])
    assert response_table(app)["VOC ID"].tolist() == ["V0001", "V0002", "V0003", "V0004"]
    revision_before = chart_data(app)["signature"]
    rows = rows_for(store, run_id)
    rows[0]["sentiment"] = "중립"
    save(store, run_id, rows)
    app.run()
    assert chart_data(app)["signature"] != revision_before
    app.button(key=prefix + "_undo_group").click().run()
    assert [row["count"] for row in chart_data(app)["rows"]] == [3, 2]
    assert response_table(app).empty and store.results(run_id) == before
    assert not app.exception and not app.error


def test_ui_category_merge_opens_union_without_merging_leaf_classifications(completed, monkeypatch):
    store, run_id, _ = completed
    store.save_result(run_id, 0, "V0002", CodingResult(voc_id="V0002", response_type="opinions", issues=[
        Issue(code_id="wrong", sentiment="부정", evidence_text="오배송"),
        Issue(code_id="quality", sentiment="부정", evidence_text="오배송")]))
    before = deepcopy(store.results(run_id))
    app, prefix = open_results(completed, monkeypatch)
    chart_event(app, "category", "merge", ["배송"], ["제품"])
    assert [row["count"] for row in chart_data(app, "category")["rows"]] == [4]
    assert len(chart_data(app, "code")["rows"]) == 3
    chart_event(app, "category", "open", ["배송", "제품"])
    assert len(response_table(app)) == 4
    assert app.session_state[prefix + "_categories"] == ["배송", "제품"]
    assert store.results(run_id) == before and not app.exception and not app.error
