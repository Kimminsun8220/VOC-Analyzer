from copy import deepcopy
import sqlite3

import pytest

from src.storage import Store
from test_grouping import completed, open_results, response_table
from result_chart_helpers import chart_event, sentiment_event


def test_rename_and_delete_only_change_the_saved_item_in_its_run(completed):
    store, run_id, _ = completed
    target = store.save_group(run_id, "배송", ["speed", "wrong"], "부정")
    keep = store.save_group(run_id, "오배송", ["wrong"])
    run = deepcopy(store.run(run_id))
    other_run = store.create_run(run["dataset_id"], run["codebook_id"], "test", "test")
    store.update_status(other_run, "completed")
    other_group = store.save_group(other_run, "배송", ["speed"])
    before = deepcopy(store.list_groups(run_id))
    raw = deepcopy(store.results(run_id))
    book = deepcopy(store.codebook(run["codebook_id"]))
    store.rename_group(run_id, target, " 배송 경험 ")
    renamed = Store(store.path).list_groups(run_id)
    assert renamed == [{**row, "name": "배송 경험"} if row["id"] == target else row for row in before]
    for action in (lambda: store.rename_group(other_run, target, "다른 이름"),
                   lambda: store.delete_group(other_run, target)):
        with pytest.raises(ValueError, match="저장한 분류가 없습니다"):
            action()
    store.delete_group(run_id, target)
    assert [row["id"] for row in Store(store.path).list_groups(run_id)] == [keep]
    assert [row["id"] for row in store.list_groups(other_run)] == [other_group]
    assert store.run(run_id) == run
    assert store.results(run_id) == raw
    assert store.codebook(run["codebook_id"]) == book


def test_invalid_rename_and_failed_delete_preserve_saved_records(completed):
    store, run_id, _ = completed
    target = store.save_group(run_id, "배송", ["speed", "wrong"])
    store.save_group(run_id, "오배송", ["wrong"])
    before = deepcopy(store.list_groups(run_id))
    for name in ("", "   ", "x" * 81, "오배송"):
        with pytest.raises(ValueError):
            store.rename_group(run_id, target, name)
        assert store.list_groups(run_id) == before
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_group_delete BEFORE DELETE ON result_groups "
                   "BEGIN SELECT RAISE(ABORT, 'delete failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="delete failed"):
        store.delete_group(run_id, target)
    assert store.list_groups(run_id) == before


def test_ui_inline_rename_submits_input_and_loads_in_a_new_session(completed, monkeypatch):
    store, run_id, _ = completed
    target = store.save_group(run_id, "배송", ["speed", "wrong"])
    app, prefix = open_results(completed, monkeypatch)
    chart_event(app, "category", "filter", ["배송"])
    app.button(key=f"saved_group_icon_edit_{target}").click().run()
    app.text_input(key=f"{prefix}_saved_name_{target}").set_value("배송 경험")
    app.button(key=f"saved_group_icon_save_{target}").click().run()
    assert store.list_groups(run_id)[0]["name"] == "배송 경험"
    assert app.session_state[prefix + "_saved"] == target
    assert app.button(key=f"saved_group_select_{target}").label == "배송 경험"
    assert not app.exception and not app.error
    fresh, prefix = open_results(completed, monkeypatch)
    fresh.button(key=prefix + "_load").click().run()
    assert fresh.session_state[prefix + "_codes"] == ["speed", "wrong"]
    assert len(response_table(fresh)) == 4
    assert not fresh.exception and not fresh.error


def test_ui_delete_selection_and_last_item_preserve_chart_and_filters(completed, monkeypatch):
    store, run_id, _ = completed
    target = store.save_group(run_id, "배송", ["speed", "wrong"])
    unselected = store.save_group(run_id, "오배송", ["wrong"])
    app, prefix = open_results(completed, monkeypatch)
    app.button(key=f"saved_group_select_{target}").click().run()
    app.button(key=prefix + "_load").click().run()
    sentiment_event(app, "긍정")
    chart_event(app, "category", "filter", ["배송"])
    layout = deepcopy(app.session_state[prefix + "_layout"])
    raw = deepcopy(store.results(run_id))
    app.button(key=f"saved_group_icon_delete_{unselected}").click().run()
    assert app.session_state[prefix + "_saved"] == target
    assert app.session_state[prefix + "_layout"] == layout
    replacement = store.save_group(run_id, "품질", ["quality"])
    app.run()
    app.button(key=f"saved_group_icon_delete_{target}").click().run()
    assert app.session_state[prefix + "_saved"] == replacement
    app.button(key=f"saved_group_icon_delete_{replacement}").click().run()
    assert store.list_groups(run_id) == []
    assert prefix + "_saved" not in app.session_state
    assert prefix + "_load" not in [button.key for button in app.button]
    assert app.session_state[prefix + "_layout"] == layout
    assert app.session_state[prefix + "_drill_categories"] == ["배송"]
    assert app.session_state[prefix + "_dashboard_sentiment"] == "긍정"
    assert store.results(run_id) == raw
    assert not app.exception and not app.error


def test_ui_invalid_rename_keeps_editing_and_failed_delete_can_retry(completed, monkeypatch):
    store, run_id, _ = completed
    target = store.save_group(run_id, "배송", ["speed", "wrong"])
    other = store.save_group(run_id, "오배송", ["wrong"])
    app, prefix = open_results(completed, monkeypatch)
    chart_event(app, "category", "filter", ["배송"])
    app.button(key=f"saved_group_icon_edit_{target}").click().run()
    for name in ("   ", "오배송"):
        app.text_input(key=f"{prefix}_saved_name_{target}").set_value(name)
        app.button(key=f"saved_group_icon_save_{target}").click().run()
        assert app.session_state[prefix + "_saved_editing"] == target
        assert app.session_state[prefix + "_saved"] == other
        assert app.error and not app.exception
    app.text_input(key=f"{prefix}_saved_name_{target}").set_value("배송 수정")
    app.button(key=f"saved_group_icon_save_{target}").click().run()
    assert not app.error and not app.exception
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_group_delete BEFORE DELETE ON result_groups "
                   "BEGIN SELECT RAISE(ABORT, 'delete failed'); END")
    app.button(key=f"saved_group_icon_delete_{target}").click().run()
    assert len(store.list_groups(run_id)) == 2
    assert [error.value for error in app.error] == ["변경하지 못했습니다. 다시 시도해주세요."]
    assert not app.exception
    with store.connect() as db:
        db.execute("DROP TRIGGER reject_group_delete")
    app.button(key=f"saved_group_icon_delete_{target}").click().run()
    assert [row["id"] for row in store.list_groups(run_id)] == [other]
    assert not app.error and not app.exception


def test_ui_recovers_when_selected_editing_item_was_deleted_elsewhere(completed, monkeypatch):
    store, run_id, _ = completed
    target = store.save_group(run_id, "배송", ["speed", "wrong"])
    remaining = store.save_group(run_id, "오배송", ["wrong"])
    app, prefix = open_results(completed, monkeypatch)
    app.button(key=f"saved_group_select_{target}").click().run()
    app.button(key=f"saved_group_icon_edit_{target}").click().run()
    store.delete_group(run_id, target)
    app.run()
    assert app.session_state[prefix + "_saved"] == remaining
    assert prefix + "_saved_editing" not in app.session_state
    assert not app.exception and not app.error
