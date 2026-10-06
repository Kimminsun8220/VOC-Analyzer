from copy import deepcopy
from io import BytesIO
import json

import pandas as pd
import pytest

from src.chart_data import COUNT, DENOMINATOR, PERCENT, build_dashboard, chart_selection_values, dashboard_export, selection_scope
from src.charts_ui import bar_figure
from src.grouping import group_results
from src.ingestion import prepare_preview
from src.models import Code, CodingResult, Issue
from src.results import csv_download, result_tables
from src.storage import Store
from test_corrections import prepared, rows_for, save
from test_grouping import completed, response_table, open_results
from result_chart_helpers import charts, chart_event
from test_category_summary import pie_spec
from src.category_summary import OTHER_SENTIMENT


def dashboard(completed, selected=None, sentiment="전체"):
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    grouped = group_results(originals, issues, codes, selected, sentiment)
    return build_dashboard(grouped, selected is not None or sentiment != "전체")


def test_unique_voc_denominators_and_multiple_sentiments(completed):
    data = dashboard(completed)
    assert data.denominator == 5  # 무응답도 전체 분모에 포함
    assert data.categories[COUNT].tolist() == [4]
    assert data.categories[PERCENT].tolist() == [80]
    assert data.codes.set_index("코드 ID")[COUNT].to_dict() == {"wrong": 3, "speed": 2}
    assert data.codes.set_index("코드 ID")["대분류 내 비율 (%)"].to_dict() == {"wrong": 75, "speed": 50}
    assert data.sentiments.set_index("감성")[COUNT].to_dict() == {"긍정": 2, "부정": 3, "중립": 0, "판단 불가": 0}
    # 같은 응답의 긍정·부정은 각각 포함하고, 같은 코드의 반복 의견은 중복 제거
    assert data.sentiments[COUNT].sum() == 5 > data.categories[COUNT].sum()


def test_filtered_chart_uses_union_denominator_and_selected_opinions(completed):
    data = dashboard(completed, ["speed", "wrong"])
    assert data.denominator == 4
    assert data.codes[PERCENT].sum() == 125  # 합을 억지로 100%에 맞추지 않는다
    positive = dashboard(completed, ["speed", "wrong"], "긍정")
    assert positive.denominator == 2
    assert positive.codes["코드 ID"].tolist() == ["speed"]
    assert positive.codes[COUNT].tolist() == [2]
    speed = dashboard(completed, ["speed"])
    assert speed.sentiments.set_index("감성").loc["부정", COUNT] == 0
    assert speed.codes[PERCENT].tolist() == [100]


def test_empty_dashboard_does_not_divide_by_zero(completed):
    for data in [dashboard(completed, ["quality"]), dashboard(completed, ["speed"], "부정"),
                 build_dashboard(group_results(pd.DataFrame(), pd.DataFrame(), []))]:
        assert data.categories.empty and data.codes.empty and data.denominator == 0
        assert data.sentiments[COUNT].sum() == data.sentiments[PERCENT].sum() == 0
        assert not data.sentiments.isna().any().any()


def test_identical_leaf_names_in_different_parents_remain_distinct(completed):
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    issues.loc[issues["코드 ID"].eq("wrong"), ["대분류", "세부분류"]] = ["다른 분류", "배송 속도"]
    data = build_dashboard(group_results(originals, issues, codes))
    assert len(data.codes) == 2 and data.codes["코드 ID"].nunique() == 2
    assert data.codes["세부분류"].nunique() == 1
    assert data.categories[COUNT].tolist() == [3, 2]


def test_chart_selection_validates_keys_and_narrows_existing_scope(completed):
    _, _, codes = completed
    event = {"selection": {"points": [{"customdata": ["speed", 2]}, {"customdata": ["speed"]},
        {"customdata": ["not-visible"]}, {"customdata": "wrong"}, {"point_index": 1}]}}
    assert chart_selection_values(event, ["speed", "wrong"]) == ["speed"]
    assert chart_selection_values({}, ["speed"]) == []
    assert selection_scope("category", ["배송"], codes, ["speed"], "부정") == (["speed"], "부정")
    assert selection_scope("category", ["배송"], codes, None, "전체") == (["speed", "wrong"], "전체")
    assert selection_scope("code", ["wrong"], codes, ["speed"], "전체") is None
    assert selection_scope("sentiment", ["부정"], codes, ["wrong"], "전체") == (["wrong"], "부정")
    assert selection_scope("category", [], codes, None, "전체") is None


def test_export_and_figure_use_same_counts_and_record_scope(completed):
    store, run_id, _ = completed
    run = store.run(run_id)
    book = store.codebook(run["codebook_id"])
    data = dashboard(completed, ["speed", "wrong"], "부정")
    table = pd.read_csv(BytesIO(csv_download(dashboard_export(data, run, book, "배송 / 감성: 부정"))))
    assert table["분석 실행"].eq(run_id).all()
    assert table["분류 기준표 버전"].eq(book["version"]).all()
    assert table["결과 개정"].eq(0).all()
    assert table[DENOMINATOR].eq(3).all()
    assert table.loc[table["집계 종류"].eq("세부분류"), COUNT].tolist() == [3]
    figure = bar_figure(data.codes, "세부분류", "코드 ID", PERCENT)
    assert list(figure.data[0].x) == [100]
    assert figure.data[0].customdata[0][:4] == ["wrong", 3, 100.0, 3]


def chart_specs(app):
    return [json.loads(chart.proto.json) for chart in charts(app)]


def test_ui_group_saved_scope_and_top_n_share_chart_data(completed, monkeypatch):
    store, run_id, _ = completed
    store.save_group(run_id, "배송 부정", ["speed", "wrong"], "부정")
    app, prefix = open_results(completed, monkeypatch)
    assert len(chart_specs(app)) == 2
    assert [row["value"] for row in chart_specs(app)[1]["rows"]] == [3, 2]
    app.button(key=prefix + "_load").click().run()
    assert len(response_table(app)) == 3
    leaf = chart_specs(app)[1]
    assert [row["value"] for row in leaf["rows"]] == [4]
    assert leaf["rows"][0]["members"] == ["speed", "wrong"] and leaf["denominator"] == 5
    assert leaf["rows"][0]["percent"] == 80
    app.selectbox(key=prefix + "_top_n").set_value(5).run()
    assert len(response_table(app)) == 3
    assert not app.exception and not app.error


def test_ui_no_charts_for_unfinished_or_empty_results(completed, monkeypatch):
    store, run_id, codes = completed
    store.update_status(run_id, "failed", "검증용 미완료")
    app, _ = open_results(completed, monkeypatch)
    assert not charts(app)
    assert any("분류와 검토가 완료" in info.value for info in app.info)
    for row in store.results(run_id):
        store.save_result(run_id, 0, row["voc_id"], CodingResult(voc_id=row["voc_id"], response_type="no_content", no_content_reason="테스트"))
    store.update_status(run_id, "completed")
    app.run()
    assert not charts(app) and not app.exception and not app.error
    assert len(response_table(app)) == 5


@pytest.mark.parametrize("counts, expected_rows", [
    ([3, 3, 2, 2, 2, 2, 1], 6),
    ([3, 3, 2, 2, 1, 1, 1], 7),
    ([7, 6, 5, 4, 3, 2, 1], 5),
    ([2, 2, 2, 2, 2, 2, 2], 7),
    ([2, 1], 2),
])
def test_ui_defaults_to_all_and_top_n_keeps_boundary_ties(counts, expected_rows, monkeypatch):
    store = Store()
    frame = pd.DataFrame({"VOC": [f"의견 {index}" for index in range(max(counts))]})
    dataset_id = store.save_dataset("동률 검증", prepare_preview(frame, "VOC"), frame, "VOC", "검증", "")
    codes = [Code(id=f"C{index:02d}", category="제품", name=f"분류 {index:02d}", definition=f"분류 {index:02d}에 해당하는 의견", reason="검증")
             for index in range(len(counts))]
    book_id = store.save_codebook(dataset_id, codes, "", "test-model", [], "confirmed")
    run_id = store.create_run(dataset_id, book_id, "test-model", "test")
    for index, text in enumerate(frame["VOC"]):
        result = CodingResult(voc_id=f"V{index + 1:04d}", response_type="opinions", issues=[
            Issue(code_id=code.id, sentiment="긍정", evidence_text=text)
            for code, count in zip(codes, counts) if index < count])
        store.save_result(run_id, 0, result.voc_id, result)
    store.update_status(run_id, "completed")
    before = deepcopy(store.results(run_id))
    app, prefix = open_results((store, run_id, codes), monkeypatch)
    assert app.selectbox(key=prefix + "_top_n").value == "전체"
    assert len(chart_specs(app)[1]["rows"]) == len(counts)
    app.selectbox(key=prefix + "_top_n").set_value(5).run()
    chart = chart_specs(app)[1]
    assert [row["value"] for row in chart["rows"]] == counts[:expected_rows]
    extra = expected_rows - 5
    notices = [caption.value for caption in app.caption if "동률로" in caption.value]
    assert len(notices) == (1 if extra > 0 else 0)
    if extra > 0:
        assert notices[0] == f"동률로 {extra}개 더 표시했습니다."
    assert not any("개 중" in caption.value and "개 표시" in caption.value for caption in app.caption)
    assert len(response_table(app)) == max(counts)
    assert [row["percent"] for row in chart_specs(app)[1]["rows"]] == pytest.approx([
        count / max(counts) * 100 for count in counts[:expected_rows]])
    assert app.selectbox(key=prefix + "_top_n").options[0] == "전체"
    app.selectbox(key=prefix + "_top_n").set_value("전체").run()
    assert len(chart_specs(app)[1]["rows"]) == len(counts)
    assert [row["value"] for row in chart_specs(app)[1]["rows"]] == counts
    assert not any("동률로" in caption.value for caption in app.caption)
    # 추가로 표시된 분류도 원문 조회에 사용할 수 있어야 한다.
    chart_event(app, "code", "open", [codes[expected_rows - 1].id])
    assert len(response_table(app)) == counts[expected_rows - 1]
    assert store.results(run_id) == before and not app.exception and not app.error


def test_manual_correction_refreshes_charts_exports_and_selection_revision(prepared, monkeypatch):
    store, run_id, book_id = prepared
    codes = store.codebook(book_id)["codes"]
    app, prefix = open_results((store, run_id, codes), monkeypatch)
    before_signatures = [data["signature"] for data in chart_specs(app)]
    raw = deepcopy(store.results(run_id))
    rows = rows_for(store, run_id)
    rows[0]["sentiment"] = "중립"
    rows[0]["code_id"] = "C4"
    save(store, run_id, rows)
    app.run()
    assert before_signatures != [data["signature"] for data in chart_specs(app)]
    sentiments = pie_spec(app)
    assert sentiments["labels"] == ["부정", OTHER_SENTIMENT] and sentiments["values"] == [1, 1]
    assert not app.dataframe
    assert {row["members"][0] for row in chart_specs(app)[1]["rows"]} == {"C2", "C4"}
    export = dashboard_export(dashboard((store, run_id, codes)), store.run(run_id), store.codebook(book_id), "전체")
    assert export["결과 개정"].eq(1).all() and store.results(run_id) == raw
    assert not app.exception and not app.error
