from copy import deepcopy
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from src import ai as ai_module
from src.grouping import group_results
from src.ingestion import prepare_preview
from src.models import Code, CodingResult, Issue
from src.results import csv_download, result_tables
from src.storage import Store


@pytest.fixture
def completed():
    store = Store()
    frame = pd.DataFrame({"VOC": ["빠름 그러나 오배송", "오배송", "오배송", "빠름 또 빠름", "없음"]})
    dataset_id = store.save_dataset("묶음 검증", prepare_preview(frame, "VOC"), frame, "VOC", "검증", "")
    codes = [Code(id=i, category=category, name=name, definition=name + " 의견", reason="검증")
             for i, category, name in [("speed", "배송", "배송 속도"), ("wrong", "배송", "오배송"),
                                      ("quality", "제품", "품질")]]
    book_id = store.save_codebook(dataset_id, codes, "", "test-model", [], "confirmed")
    run_id = store.create_run(dataset_id, book_id, "test-model", "test")
    opinions = [
        [("speed", "긍정", "빠름"), ("wrong", "부정", "오배송")],
        [("wrong", "부정", "오배송")], [("wrong", "부정", "오배송")],
        [("speed", "긍정", "빠름"), ("speed", "긍정", "또 빠름")],
    ]
    for index, values in enumerate(opinions, 1):
        result = CodingResult(voc_id=f"V{index:04d}", response_type="opinions", issues=[
            Issue(code_id=c, sentiment=s, evidence_text=e) for c, s, e in values])
        store.save_result(run_id, 0, result.voc_id, result)
    store.save_result(run_id, 0, "V0005", CodingResult(voc_id="V0005", response_type="no_content", no_content_reason="없음"))
    store.update_status(run_id, "completed")
    return store, run_id, codes


def test_union_counts_voc_ids_once_and_preserves_identical_responses(completed):
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    before_originals, before_issues = originals.copy(deep=True), issues.copy(deep=True)
    grouped = group_results(originals, issues, codes, ["speed", "wrong"])
    assert grouped.voc_count == 4 and len(grouped.issues) == 6
    assert grouped.total_count == 5 and grouped.percent == 80
    assert grouped.overlap_count == 1  # 같은 코드가 두 번인 V0004는 분류 간 겹침이 아니다.
    assert grouped.counts["고유 VOC 수"].tolist() == [2, 3]
    assert grouped.originals["VOC 원문"].eq("오배송").sum() == 2  # 같은 본문, 다른 VOC
    pd.testing.assert_frame_equal(originals, before_originals)
    pd.testing.assert_frame_equal(issues, before_issues)


def test_sentiment_and_exports_only_include_matching_opinions(completed):
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    grouped = group_results(originals, issues, codes, ["speed", "wrong"], "긍정")
    assert grouped.voc_count == 2 and grouped.overlap_count == 0
    assert set(grouped.issues["코드 ID"]) == {"speed"}
    assert set(grouped.originals["선택 의견의 감성"]) == {"긍정"}
    assert grouped.counts["고유 VOC 수"].tolist() == [2, 0]
    exported = pd.read_csv(BytesIO(csv_download(grouped.issues)))
    assert set(exported["감성"]) == {"긍정"} and len(exported) == 3
    exported_originals = pd.read_csv(BytesIO(csv_download(grouped.originals)))
    assert exported_originals["VOC ID"].is_unique and len(exported_originals) == 2
    assert group_results(originals, issues, codes, ["speed"], "부정").voc_count == 0


def test_empty_and_unknown_selections_are_not_treated_as_all(completed):
    store, run_id, codes = completed
    originals, issues = result_tables(store, run_id)
    assert group_results(originals, issues, codes).voc_count == 4
    assert group_results(originals, issues, codes, []).voc_count == 0
    assert group_results(originals, issues, codes, ["quality"]).voc_count == 0
    assert group_results(originals, pd.DataFrame(), codes).percent == 0
    assert group_results(pd.DataFrame(), pd.DataFrame(), []).total_count == 0
    with pytest.raises(ValueError, match="분류 기준표"):
        group_results(originals, issues, codes, ["unknown"])
    with pytest.raises(ValueError, match="감성"):
        group_results(originals, issues, codes, sentiment="혼합")


def test_pending_unmatched_opinions_appear_only_in_all_view(completed):
    store, run_id, codes = completed
    store.save_result(run_id, 0, "V0002", CodingResult(voc_id="V0002", response_type="opinions", issues=[
        Issue(code_id=None, missing_code="새 의미", sentiment="부정", evidence_text="오배송")]))
    originals, issues = result_tables(store, run_id)
    assert group_results(originals, issues, codes).voc_count == 4
    selected = group_results(originals, issues, codes, ["speed", "wrong"])
    assert selected.voc_count == 3 and "V0002" not in selected.originals["VOC ID"].tolist()


def test_saved_groups_persist_per_run_without_changing_classification(completed):
    store, run_id, codes = completed
    run_before = deepcopy(store.run(run_id))
    book_before = deepcopy(store.codebook(run_before["codebook_id"]))
    results_before = deepcopy(store.results(run_id))
    group_id = store.save_group(run_id, " 배송 경험 ", ["wrong", "speed", "wrong"], "부정")
    fresh = Store(store.path)
    saved = fresh.list_groups(run_id)
    assert len(saved) == 1 and saved[0]["id"] == group_id
    assert saved[0]["code_ids"] == ["speed", "wrong"]
    assert saved[0]["name"] == "배송 경험" and saved[0]["sentiment"] == "부정"
    assert fresh.run(run_id) == run_before
    assert fresh.codebook(run_before["codebook_id"]) == book_before
    assert fresh.results(run_id) == results_before
    other = store.create_run(run_before["dataset_id"], run_before["codebook_id"], "test", "test")
    assert fresh.list_groups(other) == []
    with pytest.raises(ValueError, match="완료"):
        fresh.save_group(other, "미완료", ["speed"])
    for name, ids, sentiment in [("배송 경험", ["speed"], "전체"), (" ", ["speed"], "전체"),
                                 ("x" * 81, ["speed"], "전체"), ("빈 선택", [], "전체"),
                                 ("없는 코드", ["missing"], "전체"), ("감성 오류", ["speed"], "혼합")]:
        with pytest.raises(ValueError):
            fresh.save_group(run_id, name, ids, sentiment)
    assert fresh.list_groups(run_id) == saved


def open_results(completed, monkeypatch):
    monkeypatch.setattr(ai_module, "GeminiAI", lambda *a, **k: pytest.fail("묶어보기는 AI를 호출하면 안 된다"))
    store, run_id, _ = completed
    prefix = f"group_{run_id}_{store.run(run_id)['codebook_id']}"
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.radio(key="nav").set_value("3. 분류 결과").run()
    assert not app.exception and not app.error
    return app, prefix


def metric_value(app, label):
    return next(metric.value for metric in app.metric if metric.label == label)


def test_ui_selection_parent_sentiment_empty_and_reset(completed, monkeypatch):
    app, prefix = open_results(completed, monkeypatch)
    app.radio(key=prefix + "_mode").set_value("세부분류 직접 선택").run()
    assert "묶음의 고유 VOC" not in [metric.label for metric in app.metric]
    app.multiselect(key=prefix + "_codes").set_value(["speed", "wrong"]).run()
    assert metric_value(app, "묶음의 고유 VOC") == "4건"
    assert metric_value(app, "전체 응답 대비") == "80.0%"
    app.selectbox(key=prefix + "_sentiment").set_value("긍정").run()
    assert metric_value(app, "묶음의 고유 VOC") == "2건"
    app.multiselect(key=prefix + "_codes").set_value(["wrong"]).run()
    assert metric_value(app, "묶음의 고유 VOC") == "0건"
    app.button(key=prefix + "_reset").click().run()
    assert app.radio(key=prefix + "_mode").value == "전체 보기"
    assert app.selectbox(key=prefix + "_sentiment").value == "전체"
    app.radio(key=prefix + "_mode").set_value("대분류로 묶기").run()
    app.multiselect(key=prefix + "_categories").set_value(["배송"]).run()
    assert metric_value(app, "묶음의 고유 VOC") == "4건"
    assert not app.exception and not app.error


def test_ui_save_load_and_new_session(completed, monkeypatch):
    store, run_id, _ = completed
    app, prefix = open_results(completed, monkeypatch)
    app.radio(key=prefix + "_mode").set_value("세부분류 직접 선택").run()
    app.multiselect(key=prefix + "_codes").set_value(["speed", "wrong"]).run()
    app.selectbox(key=prefix + "_sentiment").set_value("부정").run()
    app.text_input(key=prefix + "_name").set_value("배송 문제").run()
    app.button(key=prefix + "_save").click().run()
    assert len(store.list_groups(run_id)) == 1 and not app.exception
    fresh, prefix = open_results(completed, monkeypatch)
    fresh.button(key=prefix + "_load").click().run()
    assert fresh.multiselect(key=prefix + "_codes").value == ["speed", "wrong"]
    assert fresh.selectbox(key=prefix + "_sentiment").value == "부정"
    assert metric_value(fresh, "묶음의 고유 VOC") == "3건"
    assert not fresh.exception and not fresh.error


def test_ui_keeps_selections_scoped_to_run_and_marks_partial_counts(completed, monkeypatch):
    store, run_id, _ = completed
    app, prefix = open_results(completed, monkeypatch)
    app.radio(key=prefix + "_mode").set_value("세부분류 직접 선택").run()
    app.multiselect(key=prefix + "_codes").set_value(["wrong"]).run()
    first = store.run(run_id)
    second = store.create_run(first["dataset_id"], first["codebook_id"], "test", "test")
    for result in store.results(run_id):
        store.save_result(second, 0, result["voc_id"], CodingResult.model_validate(result["result"]))
    store.update_status(second, "failed", "검증용 미완료")
    app.run()
    app.selectbox(key=f"result_choice_{first['dataset_id']}").set_value(second).run()
    second_prefix = f"group_{second}_{first['codebook_id']}"
    assert app.radio(key=second_prefix + "_mode").value == "전체 보기"
    assert metric_value(app, "묶음의 고유 VOC") == "4건"
    assert any("잠정" in warning.value for warning in app.warning)
    app.radio(key=second_prefix + "_mode").set_value("세부분류 직접 선택").run()
    app.multiselect(key=second_prefix + "_codes").set_value(["speed"]).run()
    assert second_prefix + "_save" not in [button.key for button in app.button]
    assert not app.exception and not app.error
