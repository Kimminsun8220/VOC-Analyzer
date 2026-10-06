"""AppTest가 아직 제공하지 않는 v2 컴포넌트 조작을 위젯 이벤트로 전달한다."""

import json
from uuid import uuid4

from streamlit.components.v2.bidi_component.main import _make_trigger_id


def charts(app):
    return [chart for chart in app.get("bidi_component") if chart.proto.component_name == "result_bars"
            and not json.loads(chart.proto.json).get("variant")]


def sentiment_chart(app):
    return next(chart for chart in app.get("bidi_component") if chart.proto.component_name == "result_bars"
                and json.loads(chart.proto.json).get("variant") == "sentiment")


def sentiment_data(app):
    return json.loads(sentiment_chart(app).proto.json)


def sentiment_event(app, label, **extra):
    chart = sentiment_chart(app)
    data = sentiment_data(app)
    action = {"kind": "filter", "source_id": label, "signature": data["signature"], "nonce": uuid4().hex, **extra}
    widget_state = app._tree.get_widget_states()
    trigger = widget_state.widgets.add(id=_make_trigger_id(chart.proto.id, "events"))
    trigger.json_trigger_value = json.dumps([{"event": "action", "value": action}])
    app._run(widget_state)
    assert not app.exception and not app.error
    return app


def chart_data(app, level="code"):
    return json.loads(charts(app)[0 if level == "category" else 1].proto.json)


def chart_event(app, level, kind, source_members, target_members=None):
    chart = charts(app)[0 if level == "category" else 1]
    data = json.loads(chart.proto.json)
    by_members = {frozenset(row["members"]): row["id"] for row in data["rows"]}
    action = {"kind": kind, "source_id": by_members[frozenset(source_members)],
              "signature": data["signature"], "nonce": uuid4().hex}
    if target_members is not None:
        action["target_id"] = by_members[frozenset(target_members)]
    widget_state = app._tree.get_widget_states()
    trigger = widget_state.widgets.add(id=_make_trigger_id(chart.proto.id, "events"))
    trigger.json_trigger_value = json.dumps([{"event": "action", "value": action}])
    app._run(widget_state)
    assert not app.exception and not app.error
    return app


def original_table(app):
    return next((table for table in app.get("bidi_component") if table.proto.component_name == "response_table"), None)


def table_spec(app):
    table = original_table(app)
    return json.loads(table.proto.json) if table else {"rows": [], "columns": []}


def table_event(app, kind, **extra):
    table = original_table(app)
    data = json.loads(table.proto.json)
    action = {"kind": kind, "signature": data["signature"], "nonce": uuid4().hex, **extra}
    widget_state = app._tree.get_widget_states()
    trigger = widget_state.widgets.add(id=_make_trigger_id(table.proto.id, "events"))
    trigger.json_trigger_value = json.dumps([{"event": "action", "value": action}])
    app._run(widget_state)
    assert not app.exception and not app.error
    return app
