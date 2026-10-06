from copy import deepcopy
from base64 import b64decode
import json

import numpy as np
import pandas as pd
import pytest

from result_chart_helpers import chart_event, table_event
from test_grouping import completed, open_results, response_table
from src.category_summary import OTHER_SENTIMENT, comparison_figure, response_sentiments, sentiment_figure
from src.chart_data import COUNT, PERCENT
from src.result_explorer import filtered_responses
from src.result_groups import grouped_dashboard, initial_layout, load_code_group
from src.models import CodingResult, Issue
from src.results import result_tables


def scope(completed, selected=None, filters=None, layout=None):
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    view, grouped, _ = filtered_responses(originals, issues, codes, selected, filters or {})
    data = grouped_dashboard(grouped, codes, layout or initial_layout(codes))
    return view, grouped, data, len(originals)


def pie_spec(app):
    pie = next(json.loads(chart.proto.spec)["data"][0] for chart in reversed(app.get("plotly_chart"))
               if json.loads(chart.proto.spec)["data"][0]["type"] == "pie")
    if isinstance(pie["values"], dict):
        encoded = pie["values"]
        pie["values"] = np.frombuffer(b64decode(encoded["bdata"]), dtype=encoded["dtype"]).tolist()
    return pie


def test_nested_bar_keeps_two_denominators_instead_of_adding_percentages(completed):
    _, _, data, total = scope(completed, ["speed", "wrong"])
    figure = comparison_figure(data.codes, total)
    assert list(figure.data[0].x) == [60, 40]
    assert list(figure.data[1].x) == [15, 10]
    assert [a + b for a, b in zip(figure.data[0].x, figure.data[1].x)] == [75, 50]
    assert figure.data[0].customdata[0][1:] == [3, 4, 5, 75, 60]
    assert [annotation.text for annotation in figure.layout.annotations] == ["60.0%", "75.0%", "40.0%", "50.0%"]
    assert all("건" not in annotation.text for annotation in figure.layout.annotations)
    inside = list(figure.layout.annotations)[::2]
    assert [annotation.x for annotation in inside] == [60, 40]
    assert all(annotation.xanchor == "right" and annotation.xshift == -6 and
               not annotation.yshift and annotation.font.color == "white" for annotation in inside)
    assert "전체 응답" in figure.data[0].hovertemplate


def test_pie_counts_each_voc_once_including_mixed_and_no_response(completed):
    view, grouped, _, total = scope(completed)
    sentiments = response_sentiments(view, grouped.issues)
    values = sentiments.set_index("감성")[COUNT].to_dict()
    assert values == {"긍정": 1, "부정": 2, "혼합": 1, OTHER_SENTIMENT: 1}
    assert sentiments[PERCENT].sum() == pytest.approx(100)
    assert sentiments[COUNT].sum() == view["VOC ID"].nunique() == 5
    figure = sentiment_figure(sentiments, 5, total)
    assert list(figure.data[0].labels) == ["긍정", "부정", "혼합", OTHER_SENTIMENT]
    assert list(figure.data[0].values) == [1, 2, 1, 1]
    assert figure.data[0].texttemplate == "%{percent:.1%}"
    assert figure.data[0].text[2] == "혼합<br>20.0% · 응답 1건<br>현재 응답 5건<br>전체 응답 5건"
    # 반복된 같은 분류·감성은 원그래프에서도 한 응답이다.
    duplicate = pd.concat([grouped.issues, grouped.issues], ignore_index=True)
    pd.testing.assert_frame_equal(response_sentiments(view, duplicate), sentiments)


def test_filters_empty_scope_and_merged_codes_use_current_unique_responses(completed):
    _, _, codes = completed
    layout = load_code_group(initial_layout(codes), codes, ["speed", "wrong"])
    view, grouped, data, total = scope(completed, ["speed", "wrong"], {"감성": {"values": ["긍정"]}}, layout)
    figure = comparison_figure(data.codes, total)
    assert list(figure.data[0].x) == [40] and list(figure.data[1].x) == [60]
    assert figure.data[0].customdata[0][1:4] == [2, 2, 5]
    assert response_sentiments(view, grouped.issues).set_index("감성").loc["긍정", COUNT] == 2
    empty_view, empty_group, empty_data, _ = scope(completed, ["quality"])
    assert not comparison_figure(empty_data.codes, total).data[0].x
    empty_pie = response_sentiments(empty_view, empty_group.issues)
    assert empty_pie[COUNT].sum() == empty_pie[PERCENT].sum() == 0


def test_no_response_neutral_unknown_and_unprocessed_share_one_slice():
    view = pd.DataFrame({"VOC ID": ["empty", "neutral", "unknown", "pending", "mixed", "positive"],
                         "응답 상태": ["없음·무응답·모름", "의견 있음", "의견 있음", "미처리", "의견 있음", "의견 있음"]})
    opinions = pd.DataFrame({"VOC ID": ["neutral", "neutral", "unknown", "mixed", "mixed", "positive"],
                             "감성": ["중립", "중립", "판단 불가", "긍정", "부정", "긍정"]})
    sentiments = response_sentiments(view, opinions).set_index("감성")
    assert sentiments[COUNT].to_dict() == {"긍정": 1, "부정": 0, "혼합": 1, OTHER_SENTIMENT: 4}
    assert sentiments[PERCENT].sum() == pytest.approx(100)
    figure = sentiment_figure(sentiments.reset_index(), 6, 6)
    assert list(figure.data[0].labels) == ["긍정", "혼합", OTHER_SENTIMENT]
    assert figure.data[0].text[-1].startswith(OTHER_SENTIMENT + "<br>66.7% · 응답 4건")


@pytest.mark.parametrize("has_matched_opinion", [False, True])
def test_unmatched_code_stays_unreviewed_until_its_classification_is_resolved(completed, has_matched_opinion):
    store, run_id, _ = completed
    opinions = [Issue(code_id=None, sentiment="긍정", evidence_text="오배송", missing_code="새 분류 필요")]
    if has_matched_opinion:
        opinions.append(Issue(code_id="wrong", sentiment="부정", evidence_text="오배송"))
    store.save_result(run_id, 0, "V0002", CodingResult(voc_id="V0002", response_type="opinions", issues=opinions))
    originals, issues = result_tables(store, run_id)
    assert originals.set_index("VOC ID").loc["V0002", "응답 상태"] == "맞는 코드 없음·검토 필요"
    sentiments = response_sentiments(originals, issues).set_index("감성")
    assert sentiments[COUNT].to_dict() == {"긍정": 1, "부정": 1, "혼합": 1, OTHER_SENTIMENT: 2}
    assert sentiments[PERCENT].sum() == pytest.approx(100)
    store.save_result(run_id, 0, "V0002", CodingResult(voc_id="V0002", response_type="opinions", issues=[
        Issue(code_id="wrong", sentiment="긍정", evidence_text="오배송")]))
    resolved = response_sentiments(*result_tables(store, run_id)).set_index("감성")
    assert resolved[COUNT].to_dict() == {"긍정": 2, "부정": 1, "혼합": 1, OTHER_SENTIMENT: 1}


def test_ui_category_only_visuals_refresh_with_filters_without_changing_ai(completed, monkeypatch):
    store, run_id, _ = completed
    raw = deepcopy(store.results(run_id))
    app, _ = open_results(completed, monkeypatch)
    assert len(app.get("plotly_chart")) == 1 and not app.dataframe
    chart_event(app, "category", "open", ["배송"])
    assert len(app.get("plotly_chart")) == 3 and len(response_table(app)) == 4
    assert not any("전체 5건의" in caption.value for caption in app.caption)
    assert not app.dataframe
    assert "비율 표" not in [expander.label for expander in app.expander]
    table_event(app, "filter", column="감성", values=["긍정"])
    assert len(response_table(app)) == 2 and len(app.get("plotly_chart")) == 3
    table_event(app, "filter", column="감성", values=[])
    assert response_table(app).empty and len(app.get("plotly_chart")) == 1
    app.button(key=next(button.key for button in app.button if button.label == "전체 보기")).click().run()
    assert len(app.get("plotly_chart")) == 1 and not app.dataframe and len(response_table(app)) == 5
    assert store.results(run_id) == raw


def test_leaf_popup_only_pie_uses_selected_sentiments_and_refreshes_after_edit(completed, monkeypatch):
    store, run_id, _ = completed
    raw = deepcopy(store.results(run_id))
    app, prefix = open_results(completed, monkeypatch)
    chart_event(app, "code", "open", ["wrong"])
    assert not app.dataframe and len(app.get("plotly_chart")) == 2
    assert "감성 비중" in [expander.label for expander in app.expander]
    assert not any("100%를 넘을 수 있습니다" in caption.value for caption in app.caption)
    pie = pie_spec(app)
    assert pie["type"] == "pie" and pie["labels"] == ["부정"]
    assert json.loads(app.get("plotly_chart")[-1].proto.spec)["layout"]["showlegend"] is True
    assert pie["values"] == [3] and pie["customdata"] == [["부정", 3, 3, 5]]
    # 같은 VOC의 배송 속도 긍정은 오배송 원그래프에 포함하지 않는다.
    table_event(app, "sentiment", id="V0001", sentiments=["긍정", "긍정"])
    pie = pie_spec(app)
    assert pie["labels"] == ["긍정", "부정"] and pie["values"] == [1, 2]
    table_event(app, "filter", column="감성", values=["긍정"])
    pie = pie_spec(app)
    assert pie["values"] == [1] and pie["customdata"] == [["긍정", 1, 1, 5]]
    table_event(app, "filter", column="감성", values=[])
    assert response_table(app).empty and len(app.get("plotly_chart")) == 1
    assert any("표의 필터를 해제" in message.value for message in app.info)
    app.button(key=prefix + "_reset").click().run()
    assert len(app.get("plotly_chart")) == 1 and not app.dataframe
    assert store.results(run_id) == raw


def test_merged_leaf_popup_counts_mixed_voc_once_without_tables(completed, monkeypatch):
    app, _ = open_results(completed, monkeypatch)
    chart_event(app, "code", "merge", ["speed"], ["wrong"])
    chart_event(app, "code", "open", ["speed", "wrong"])
    assert not app.dataframe and len(app.get("plotly_chart")) == 2
    pie = pie_spec(app)
    assert pie["labels"] == ["긍정", "부정", "혼합"]
    assert pie["values"] == [1, 2, 1] and sum(pie["values"]) == len(response_table(app)) == 4


def test_main_sentiment_pie_stays_global_and_whole_popup_has_no_visuals(completed, monkeypatch):
    app, _ = open_results(completed, monkeypatch)
    assert len(app.get("plotly_chart")) == 1 and not app.dataframe
    assert pie_spec(app)["values"] == [1, 2, 1, 1]
    assert not any(box.label in {"분류·감성 비중", "감성 비중"} for box in app.expander)
    table_event(app, "filter", column="VOC 원문", values=None, search="빠름")
    assert len(response_table(app)) == 2 and pie_spec(app)["values"] == [1, 2, 1, 1]
    assert pie_spec(app)["customdata"] == [["긍정", 1, 5, 5], ["부정", 2, 5, 5],
                                          ["혼합", 1, 5, 5], [OTHER_SENTIMENT, 1, 5, 5]]
    chart_event(app, "code", "merge", ["speed"], ["wrong"])
    next(button for button in app.button if button.label == "전체 원문 보기").click().run()
    assert len(app.get("plotly_chart")) == 1 and pie_spec(app)["values"] == [1, 2, 1, 1]
    assert not any(box.label in {"분류·감성 비중", "감성 비중"} for box in app.expander)
    table_event(app, "filter", column="VOC 원문", values=None, search="없음")
    assert len(response_table(app)) == 1 and pie_spec(app)["values"] == [1, 2, 1, 1]
    assert len(app.get("plotly_chart")) == 1 and not app.dataframe
