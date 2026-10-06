from copy import deepcopy
from io import BytesIO

import pandas as pd
import pytest

from result_chart_helpers import table_event, table_spec, chart_event
from test_grouping import completed, open_results, response_table, select_response
from src.chart_data import COUNT, DENOMINATOR, PERCENT
from src.models import CodingResult
from src.response_table import table_action, table_data
from src.result_explorer import filtered_responses, download_frame, filter_scope
from src.results import result_tables, csv_download


def test_header_filters_combine_values_columns_and_matching_opinion_exports(completed):
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    filters = {"감성": {"values": ["긍정", "부정"]}, "VOC 원문": {"search": "오배송"},
        "응답 상태": {"values": ["의견 있음"]}}
    view, grouped, options = filtered_responses(originals, issues, codes, None, filters)
    assert view["VOC ID"].tolist() == ["V0001", "V0002", "V0003"]
    assert len(grouped.issues) == 4 and grouped.total_count == 3
    assert "빠름 또 빠름" in options["VOC 원문"]  # 현재 필터로 가려진 값도 다시 선택 가능
    assert options["감성"] == ["긍정", "부정", "중립", "판단 불가"]
    filters["감성"]["values"] = ["긍정"]
    view, grouped, _ = filtered_responses(originals, issues, codes, None, filters)
    assert view["VOC ID"].tolist() == ["V0001"]
    assert view["분류"].tolist() == ["[배송] 배송 속도"]
    assert grouped.issues["감성"].tolist() == ["긍정"]
    run, book = store.run(run_id), store.codebook(store.run(run_id)["codebook_id"])
    scope = filter_scope(filters)
    aggregate = download_frame("현재 집계", originals, (view, grouped), run, book, scope)
    assert aggregate[DENOMINATOR].eq(1).all() and aggregate["조회 범위"].eq(scope).all()
    assert aggregate[COUNT].max() == 1 and aggregate[PERCENT].max() == 100
    exported = pd.read_csv(BytesIO(csv_download(download_frame("현재 원문", originals, (view, grouped), run, book, scope))))
    assert exported["VOC ID"].tolist() == ["V0001"]
    assert len(download_frame("전체 응답", originals, (view, grouped), run, book, scope)) == 5


def test_classification_values_are_exact_and_duplicate_text_preserves_response_ids(completed):
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    filters = {"분류": {"values": ["[배송] 오배송"]}, "VOC 원문": {"values": ["오배송"]}}
    view, grouped, _ = filtered_responses(originals, issues, codes, None, filters)
    assert view["VOC ID"].tolist() == ["V0002", "V0003"] and grouped.total_count == 2
    assert filtered_responses(originals, issues, codes, None, {"VOC 원문": {"search": ".*"}})[0].empty
    assert filtered_responses(originals, issues, codes, None, {"응답 상태": {"values": []}})[0].empty


def test_neutral_filter_includes_missing_sentiment_and_keeps_unknown_separate(completed):
    store, run_id, codes = completed
    store.save_result(run_id, 0, "V0002", None, "검증 실패")
    store.save_result(run_id, 0, "V0003", CodingResult(voc_id="V0003", response_type="unclear", review_reason="확인"))
    originals, issues = result_tables(store, run_id)
    # 중립 의견과 감성값 없는 응답을 합치고 판단 불가 의견은 별도로 조회한다.
    issues.loc[issues["VOC ID"].eq("V0001") & issues["코드 ID"].eq("speed"), "감성"] = "중립"
    issues.loc[issues["VOC ID"].eq("V0004"), "감성"] = "판단 불가"
    view, grouped, options = filtered_responses(originals, issues, codes, None, {"감성": {"values": ["중립"]}})
    assert view["VOC ID"].tolist() == ["V0001", "V0002", "V0003", "V0005"]
    assert view["감성"].eq("중립").all()
    assert "—" not in options["감성"]
    assert set(grouped.issues["VOC ID"]) == {"V0001"} and grouped.total_count == 4
    assert grouped.issues["감성"].tolist() == ["중립"]
    assert view.set_index("VOC ID")["응답 상태"].to_dict() == {
        "V0001": "의견 있음", "V0002": "실패", "V0003": "해석 검토 필요", "V0005": "없음·무응답·모름"}
    view, grouped, _ = filtered_responses(originals, issues, codes, None,
                                          {"감성": {"values": ["판단 불가"]}})
    assert view["VOC ID"].tolist() == ["V0004"]
    assert view["감성"].eq("판단 불가").all() and grouped.total_count == 1
    assert grouped.issues["감성"].eq("판단 불가").all()
    view, _, _ = filtered_responses(originals, issues, codes, None,
        {"응답 상태": {"values": ["없음·무응답·모름", "해석 검토 필요"]}})
    assert view["VOC ID"].tolist() == ["V0003", "V0005"]


def test_table_rejects_old_or_invalid_events_and_normalizes_full_selection(completed):
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    view, _, options = filtered_responses(originals, issues, codes, None, {})
    data = table_data(view, options, {}, None, [None, 0, 0])
    for changes in ({"signature": "old"}, {"id": "missing"}, {"column": "unknown", "kind": "filter"},
        {"column": "감성", "kind": "filter", "values": ["unknown"]},
        {"column": "감성", "kind": "filter", "values": {}, "search": ""},
        {"column": "분류", "kind": "filter", "search": "x"}):
        with pytest.raises(ValueError):
            table_action(data, {"kind": "select", "signature": data["signature"], "id": "V0001", **changes})
    assert table_action(data, {"kind": "filter", "signature": data["signature"], "column": "응답 상태", "values": options["응답 상태"]}) == ("filter", ("응답 상태", {}))
    assert table_action(data, {"kind": "filter", "signature": data["signature"], "column": "감성", "values": []}) == ("filter", ("감성", {"values": []}))
    newer = table_data(view, options, {}, None, [None, 1, 0])
    assert newer["signature"] != data["signature"]


def test_ui_headers_empty_filter_clear_and_new_chart_scope(completed, monkeypatch):
    app, prefix = open_results(completed, monkeypatch)
    before = deepcopy(completed[0].results(completed[1]))
    assert [column["key"] for column in table_spec(app)["columns"]] == ["VOC 원문", "분류", "감성", "응답 상태"]
    assert not any(select.key in (prefix + "_sentiment", prefix + "_state") for select in app.selectbox)
    assert not any(item.key == prefix + "_search" for item in app.text_input)
    select_response(app, "V0001")
    table_event(app, "filter", column="응답 상태", values=["없음·무응답·모름"])
    assert response_table(app)["VOC ID"].tolist() == ["V0005"]
    assert not any(button.label == "이 응답 수정" for button in app.button)
    table_event(app, "filter", column="감성", values=[])
    assert response_table(app).empty and len(table_spec(app)["columns"]) == 4
    table_event(app, "clear", column="감성")
    assert response_table(app)["VOC ID"].tolist() == ["V0005"]
    chart_event(app, "code", "open", ["wrong"])
    assert app.session_state[prefix + "_filters"] == {} and len(response_table(app)) == 3
    assert completed[0].results(completed[1]) == before
