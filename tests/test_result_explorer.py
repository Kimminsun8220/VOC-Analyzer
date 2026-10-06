import pandas as pd
from pathlib import Path
from streamlit.testing.v1 import AppTest

from test_grouping import completed, open_results, response_table, select_response
from src.chart_data import COUNT, DENOMINATOR, PERCENT
from src.grouping import group_results
from src.models import CodingResult
from src.result_explorer import download_frame, response_view, table_key, visible_group
from src.results import result_tables
from result_chart_helpers import charts, chart_event, table_event


def test_main_page_shows_both_charts_and_opens_all_originals_directly(completed):
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.radio(key="nav").set_value("3. 분류 결과").run()
    assert len(charts(app)) == 2
    assert [header.value for header in app.subheader] == ["감성 비중", "대분류", "세부분류"]
    assert len(app.get("plotly_chart")) == 0
    assert not any(expander.label == "고객 원문" for expander in app.expander)
    assert response_table(app).empty
    next(button for button in app.button if button.label == "전체 원문 보기").click().run()
    assert len(response_table(app)) == 5 and not app.exception and not app.error
    chart_event(app, "code", "open", ["wrong"])
    table_event(app, "filter", column="VOC 원문", values=None, search="빠름")
    assert len(response_table(app)) == 1
    store, run_id, _ = completed
    prefix = f"group_{run_id}_{store.run(run_id)['codebook_id']}"
    app.session_state[prefix + "_show_originals"] = False
    app.run()
    app.button(key=prefix + "_open_originals").click().run()
    assert len(response_table(app)) == 5 and app.session_state[prefix + "_filters"] == {}
    assert app.session_state[prefix + "_mode"] == "전체 보기"
    assert not app.exception and not app.error


def test_default_view_and_state_filters_preserve_every_input(completed):
    store, run, codes = completed
    store.save_result(run, 0, "V0002", None, "연결 실패")
    store.save_result(run, 0, "V0003", CodingResult(voc_id="V0003", response_type="unclear", review_reason="해석 확인"))
    originals, issues = result_tables(store, run)
    grouped = group_results(originals, issues, codes)
    assert len(response_view(originals, grouped, None, "전체")) == 5
    for state, identifiers in [("내용 없음", ["V0005"]), ("검토 필요", ["V0003"]), ("실패·미처리", ["V0002"])]:
        assert response_view(originals, grouped, None, "전체", state)["VOC ID"].tolist() == identifiers


def test_search_is_literal_and_selection_never_uses_an_old_row_position(completed):
    store, run, codes = completed
    originals, issues = result_tables(store, run)
    grouped = group_results(originals, issues, codes)
    all_rows = response_view(originals, grouped, None, "전체")
    narrowed = response_view(originals, grouped, None, "전체", search="오배송")
    assert narrowed["VOC ID"].tolist() == ["V0001", "V0002", "V0003"]
    assert response_view(originals, grouped, None, "전체", search=".*").empty
    first = table_key("scope", all_rows, None, "전체", "전체", "", 0)
    assert first != table_key("scope", narrowed, None, "전체", "전체", "오배송", 0)
    assert first != table_key("scope", all_rows, None, "전체", "전체", "", 1)


def test_visible_exports_share_originals_opinions_and_denominator(completed):
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    selected = ["speed", "wrong"]
    grouped = group_results(originals, issues, codes, selected)
    view = response_view(originals, grouped, selected, "전체", search="오배송")
    scoped = visible_group(originals, issues, codes, view, selected, "전체")
    run = store.run(run_id)
    book = store.codebook(run["codebook_id"])
    current = (view, scoped)
    assert download_frame("현재 원문", originals, current, run, book, "배송")["VOC ID"].tolist() == ["V0001", "V0002", "V0003"]
    opinions = download_frame("현재 의견·근거", originals, current, run, book, "배송")
    assert set(opinions["VOC ID"]) == set(view["VOC ID"]) and len(opinions) == 4
    aggregate = download_frame("현재 집계", originals, current, run, book, "배송 · 검색: 오배송")
    wrong = aggregate[aggregate["코드 ID"].eq("wrong")].iloc[0]
    assert wrong[COUNT] == 3 and wrong[DENOMINATOR] == 3 and wrong[PERCENT] == 100
    assert set(aggregate["조회 범위"]) == {"배송 · 검색: 오배송"}
    assert len(download_frame("전체 응답", originals, current, run, book, "배송")) == 5


def test_no_content_export_with_no_opinion_columns(completed):
    store, run_id, codes = completed
    originals, _ = result_tables(store, run_id)
    empty_original = originals[originals["VOC ID"].eq("V0005")]
    grouped = group_results(empty_original, pd.DataFrame(), codes)
    view = response_view(empty_original, grouped, None, "전체", "내용 없음")
    scoped = visible_group(empty_original, pd.DataFrame(), codes, view, None, "전체")
    assert len(view) == 1 and scoped.total_count == 1 and scoped.issues.empty


def test_selected_detail_closes_when_filter_excludes_the_response(completed, monkeypatch):
    app, prefix = open_results(completed, monkeypatch)
    assert len(response_table(app)) == 5 and len(charts(app)) == 2 and len(app.tabs) == 0
    select_response(app, "V0001")
    assert not any(title.value == "응답 상세" for title in app.subheader)
    assert app.expander[-1].label == "수정 이력" and not app.expander[-1].proto.expanded
    assert not any(button.label == "이 응답 수정" for button in app.button)
    table_event(app, "filter", column="VOC 원문", values=None, search="없음")
    assert response_table(app)["VOC ID"].tolist() == ["V0005"]
    assert not any(title.value == "응답 상세" for title in app.subheader)
    assert not any(box.label == "수정 이력" for box in app.expander)
    app.button(key=prefix + "_reset").click().run()
    assert len(response_table(app)) == 5 and not app.exception and not app.error
    assert not any(title.value == "응답 상세" for title in app.subheader)


def test_reset_closes_detail_and_does_not_restore_old_selection(completed, monkeypatch):
    app, prefix = open_results(completed, monkeypatch)
    select_response(app, "V0001")
    app.button(key=prefix + "_reset").click().run()
    assert not any(title.value == "응답 상세" for title in app.subheader)
    assert not app.exception and not app.error


def test_review_shortcut_clears_old_scope(completed, monkeypatch):
    store, run, _ = completed
    store.save_result(run, 0, "V0003", CodingResult(voc_id="V0003", response_type="unclear", review_reason="해석 확인"))
    store.update_status(run, "needs_review")
    app, prefix = open_results(completed, monkeypatch)
    app.session_state[prefix + "_mode"] = "세부분류 직접 선택"
    app.session_state[prefix + "_codes"] = ["speed"]
    app.run()  # 미완료 실행에는 차트가 없으므로 이전 조회 조건을 재현한다.
    table_event(app, "filter", column="VOC 원문", values=None, search="빠름")
    app.button(key=prefix + "_review").click().run()
    assert response_table(app)["VOC ID"].tolist() == ["V0003"]
    select_response(app, "V0003")
    assert any(warning.value == "해석 확인" for warning in app.warning)
    assert not app.exception and not app.error
