from copy import deepcopy

import pandas as pd
import pytest

from result_chart_helpers import chart_data, chart_event, sentiment_data, sentiment_event
from test_grouping import completed, open_results, response_table
from src.category_summary import overview_mentions, sentiment_response_ids
from src.chart_data import COUNT, PERCENT
from src.response_basis import ALL_BASIS, VALID_BASIS, NO_CONTENT, basis_responses
from src.models import CodingResult


def test_twelve_responses_and_three_opinions_have_two_consistent_denominators():
    originals = pd.DataFrame({"VOC ID": list(range(12)), "응답 상태": ["의견 있음"] * 3 + [NO_CONTENT] * 9})
    issues = pd.DataFrame({"VOC ID": [0, 0, 1, 2], "감성": ["긍정", "긍정", "긍정", "중립"]})
    before = originals.copy(deep=True)
    all_rows = overview_mentions(basis_responses(originals), issues).set_index("감성")
    valid_rows = overview_mentions(basis_responses(originals, VALID_BASIS), issues).set_index("감성")
    assert all_rows.loc["긍정", COUNT] == valid_rows.loc["긍정", COUNT] == 2
    assert all_rows.loc["긍정", PERCENT] == pytest.approx(2 / 12 * 100)
    assert valid_rows.loc["긍정", PERCENT] == pytest.approx(2 / 3 * 100)
    assert all_rows.loc["무응답", COUNT] == 9 and all_rows.loc["중립", COUNT] == 1
    assert valid_rows.loc["무응답", COUNT] == 0 and valid_rows.loc["중립", COUNT] == 1
    assert set(sentiment_response_ids(originals, issues, "무응답")) == set(range(3, 12))
    assert list(sentiment_response_ids(originals, issues, "중립")) == [2]
    pd.testing.assert_frame_equal(originals, before)


def test_empty_valid_scope_is_zero_and_unknown_is_not_no_response():
    originals = pd.DataFrame({"VOC ID": ["empty"], "응답 상태": [NO_CONTENT]})
    issues = pd.DataFrame(columns=["VOC ID", "감성"])
    rows = overview_mentions(basis_responses(originals, VALID_BASIS), issues)
    assert rows[COUNT].eq(0).all() and rows[PERCENT].eq(0).all()
    originals.loc[0, "응답 상태"] = "미처리"
    assert len(basis_responses(originals, VALID_BASIS)) == 1
    assert overview_mentions(originals, issues).set_index("감성").loc["미검토", COUNT] == 1


def test_basis_updates_charts_preserves_drill_and_original_data(completed, monkeypatch):
    store, run_id, _ = completed
    before = deepcopy(store.results(run_id))
    app, prefix = open_results(completed, monkeypatch)
    selector = f"result_basis_{run_id}"
    assert app.radio(key=selector).value == ALL_BASIS
    assert sentiment_data(app)["denominator"] == chart_data(app)["denominator"] == 5
    chart_event(app, "category", "filter", ["배송"])
    previous_signature = chart_data(app)["signature"]
    app.radio(key=selector).set_value(VALID_BASIS).run()
    assert not app.exception
    assert sentiment_data(app)["denominator"] == chart_data(app)["denominator"] == 4
    assert sentiment_data(app)["rows"][0]["percent"] == 50
    assert chart_data(app)["signature"] != previous_signature
    assert app.session_state[prefix + "_drill_categories"] == ["배송"]
    sentiment_event(app, "긍정")
    assert chart_data(app)["denominator"] == 2
    app.radio(key=selector).set_value(ALL_BASIS).run()
    assert chart_data(app)["denominator"] == 2  # 감성 선택의 분모 유지
    sentiment_event(app, "긍정")
    assert chart_data(app)["denominator"] == 5
    assert store.results(run_id) == before


def test_no_response_filter_can_be_exited_by_switching_basis(completed, monkeypatch):
    _, run_id, _ = completed
    app, prefix = open_results(completed, monkeypatch)
    sentiment_event(app, "무응답")
    app.button(key=prefix + "_open_originals").click().run()
    assert len(response_table(app)) == 1
    app.session_state[prefix + "_show_originals"] = False
    app.radio(key=f"result_basis_{run_id}").set_value(VALID_BASIS).run()
    assert not app.exception
    assert sentiment_data(app)["selected"] is None
    assert chart_data(app)["denominator"] == 4


def test_all_no_response_result_has_safe_zero_valid_basis(completed, monkeypatch):
    store, run_id, _ = completed
    for row in store.results(run_id):
        store.save_result(run_id, 0, row["voc_id"], CodingResult(
            voc_id=row["voc_id"], response_type="no_content", no_content_reason="무응답"))
    app, _ = open_results(completed, monkeypatch)
    app.radio(key=f"result_basis_{run_id}").set_value(VALID_BASIS).run()
    assert not app.exception and not app.error
    assert sentiment_data(app)["denominator"] == 0
    assert all(row["percent"] == 0 for row in sentiment_data(app)["rows"])
    assert len(response_table(app)) == 5  # 원문은 조회 기준과 무관하게 보존
