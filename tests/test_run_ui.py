from pathlib import Path
import time

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from src import ai as ai_module, config
from src.ai import AIError, PROMPT_VERSION
from src.ingestion import prepare_preview
from src.models import Code, CodingBatch, CodingResult, Issue
from src.run_ui import progress_snapshot
from src.storage import Store
from src.workflow import execute_run


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def setup_run(store, count=4):
    frame = pd.DataFrame({"VOC": ["배송이 빠름"] * count})
    dataset_id = store.save_dataset("진행 검증", prepare_preview(frame, "VOC"), frame, "VOC", "검증", "")
    codes = [Code(id="C1", category="배송", name="속도", definition="배송 속도", reason="검증")]
    book_id = store.save_codebook(dataset_id, codes, "", "test-model", [], "confirmed")
    return store.create_run(dataset_id, book_id, "test-model", PROMPT_VERSION)


def result(identifier):
    return CodingResult(voc_id=identifier, response_type="opinions",
        issues=[Issue(code_id="C1", sentiment="긍정", evidence_text="배송이 빠름")])


def open_results():
    app = AppTest.from_file(APP_PATH).run()
    app.radio(key="nav").set_value("3. 분류 결과").run()
    assert not app.exception
    return app


def reject_ai(monkeypatch):
    monkeypatch.setattr(ai_module, "GeminiAI", lambda *args: pytest.fail("진행 중 AI 호출 금지"))


def test_active_run_disables_resume_and_new_classification(monkeypatch):
    reject_ai(monkeypatch)
    store = Store()
    run_id = setup_run(store)
    store.claim(run_id)
    store.save_result(run_id, 0, "V0001", result("V0001"))
    app = open_results()
    assert app.button(key="resume_run").disabled
    assert app.get("progress")[0].proto.value == 25
    assert "1/4건" in app.get("progress")[0].proto.text
    assert not app.error
    assert any("진행 중" in item.value for item in app.info)
    app.radio(key="nav").set_value("2. 분류 기준표").run()
    assert app.button(key="start_classification").disabled
    assert not app.exception


def test_other_run_lock_blocks_resume_without_changing_results(monkeypatch):
    reject_ai(monkeypatch)
    store = Store()
    other = setup_run(store)
    store.claim(other)
    target = setup_run(store)
    store.update_status(target, "failed", "호출 실패")
    before = store.run(target), store.results(target)
    app = open_results()
    assert app.button(key="resume_run").disabled
    assert any("다른 분류" in item.value for item in app.info)
    assert (store.run(target), store.results(target)) == before


def test_expired_lock_reenables_resume_and_preserves_saved_progress(monkeypatch):
    reject_ai(monkeypatch)
    store = Store()
    run_id = setup_run(store)
    store.claim(run_id)
    store.save_result(run_id, 0, "V0001", result("V0001"))
    app = open_results()
    assert app.button(key="resume_run").disabled
    with store.connect() as db:
        db.execute("UPDATE runs SET lease_until=? WHERE id=?", (time.time() - 1, run_id))
    app.run()
    assert not app.button(key="resume_run").disabled
    assert app.get("progress")[0].proto.value == 25
    assert any("중단" in item.value for item in app.warning)
    assert not app.exception


def test_button_is_disabled_before_ai_starts_and_restored_after_failure(monkeypatch):
    store = Store()
    run_id = setup_run(store)
    store.update_status(run_id, "failed", "호출 실패")
    monkeypatch.setattr(config, "load_gemini_key", lambda: "test-key")
    buttons = []
    import streamlit as st
    real_button = st.button

    def observe_button(*args, **kwargs):
        if kwargs.get("key") == "resume_run":
            buttons.append(kwargs.get("disabled"))
        return real_button(*args, **kwargs)

    monkeypatch.setattr(st, "button", observe_button)

    class FailAI:
        model = "test-model"
        def __init__(self, *args):
            assert buttons[-1] is True
        def classify(self, *args, **kwargs):
            raise AIError("검증용 서버 오류", False)
        def close(self):
            pass

    monkeypatch.setattr(ai_module, "GeminiAI", FailAI)
    app = open_results()
    app.button(key="resume_run").click().run()
    assert not app.button(key="resume_run").disabled
    assert store.active_run() is None
    assert "검증용 서버 오류" in app.warning[0].value
    assert not app.exception


def test_stale_click_is_blocked_before_ai_or_review_budget_changes(monkeypatch):
    reject_ai(monkeypatch)
    store = Store()
    run_id = setup_run(store)
    store.update_status(run_id, "needs_review", "검토 필요")
    app = open_results()
    assert not app.button(key="resume_run").disabled
    store.claim(run_id)
    before = store.run(run_id)
    app.button(key="resume_run").click().run()
    assert app.button(key="resume_run").disabled
    assert store.run(run_id) == before
    assert not app.error and not app.exception


def test_progress_uses_current_round_only():
    store = Store()
    run_id = setup_run(store)
    for n in range(1, 5):
        store.save_result(run_id, 0, f"V{n:04}", result(f"V{n:04}"))
    assert progress_snapshot(store, store.run(run_id)) == (4, 4, 1)
    book = store.codebook(store.run(run_id)["codebook_id"])
    store.add_round(run_id, book["codes"], [])
    assert progress_snapshot(store, store.run(run_id)) == (0, 4, 0)
    store.save_result(run_id, 1, "V0001", result("V0001"))
    store.save_result(run_id, 1, "V0002", error="결과 검증 실패")
    assert progress_snapshot(store, store.run(run_id)) == (1, 4, .25)


def test_workflow_reports_wait_then_saved_results_then_completion():
    store = Store()
    run_id = setup_run(store)
    updates = []

    class FakeAI:
        model = "test-model"
        def classify(self, records, *args, **kwargs):
            assert updates[-1][0] == 0 and "AI 응답 대기" in updates[-1][2]
            assert store.active_run()["id"] == run_id
            return CodingBatch(results=[result(row["id"]) for row in records])

    execute_run(store, run_id, FakeAI(), lambda *update: updates.append(update))
    assert [item[0] for item in updates if item[2] == "분류 결과 저장 중"] == [1, 2, 3, 4]
    assert updates[-1] == (4, 4, "분류 완료")
    assert store.active_run() is None
