"""고객 원문 표의 필터·행 선택·분류·감성 편집을 현재 결과에 연결한다."""

from hashlib import sha256
import json
from pathlib import Path
import time

import streamlit as st

from src.result_explorer import FILTER_COLUMNS, STATUS_LABELS
from src.corrections import save_correction
from src.grouping import SENTIMENTS


def classification_edit_data(store, run, codes, selected_ids=None):
    """필터가 숨긴 의견도 포함해 편집하며 검토 중인 변경은 확정하지 않는다."""
    choices = [{"value": code.id, "label": f"[{code.category}] {code.name}"} for code in codes]
    rows = {}
    if run["status"] in {"completed", "needs_review"} and run["lease_until"] <= time.time():
        allowed = {code.id for code in codes}
        for row in store.effective_results(run["id"]):
            result = row["result"]
            if (row["status"] == "success" and result and result["response_type"] == "opinions"
                    and result["issues"] and all(item["code_id"] in allowed for item in result["issues"])):
                rows[row["voc_id"]] = [{key: item[key] for key in ("code_id", "evidence_text", "sentiment")}
                                       for item in result["issues"]]
    sentiment_indices = {voc_id: [index for index, item in enumerate(opinions)
        if selected_ids is None or item["code_id"] in selected_ids]
        for voc_id, opinions in rows.items()}
    return {"options": choices, "rows": rows, "sentiment_indices": sentiment_indices}


def table_data(view, options, filters, selected, context, editing=None):
    rows = [{"id": row["VOC ID"], **{column: str(row[column]) for column in FILTER_COLUMNS}}
            for row in view.to_dict("records")]
    columns = [{"key": column, "label": column.replace("VOC ", ""),
        "options": [{"value": value, "label": STATUS_LABELS.get(value, value) if column == "응답 상태" else value or "(빈 값)"}
                    for value in dict.fromkeys([*options[column], *(filters.get(column, {}).get("values") or [])])],
        "filter": filters.get(column, {})} for column in FILTER_COLUMNS]
    payload = {"rows": rows, "columns": columns, "selected": selected, "status_labels": STATUS_LABELS}
    if editing:
        payload["classification_options"] = editing["options"]
        payload["sentiment_options"] = [{"value": value, "label": value} for value in SENTIMENTS[1:]]
        for row in rows:
            if row["id"] in editing["rows"]:
                row["opinions"] = editing["rows"][row["id"]]
                row["sentiment_indices"] = editing["sentiment_indices"][row["id"]]
    payload["signature"] = sha256(json.dumps([payload, context], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    return payload


def table_action(data, action):
    """이전 행 위치·개정의 이벤트로 다른 원문을 선택하지 않는다."""
    if not isinstance(action, dict) or action.get("signature") != data["signature"]:
        raise ValueError("표가 업데이트되었습니다. 다시 선택해주세요.")
    kind = action.get("kind")
    if kind in {"classify", "sentiment"}:
        row = next((row for row in data["rows"] if row["id"] == action.get("id")), None)
        if row is None or not row.get("opinions"):
            raise ValueError("이 응답은 상세 화면에서 확인·수정해주세요.")
        field, key, options_key = (("code_id", "code_ids", "classification_options") if kind == "classify"
                                  else ("sentiment", "sentiments", "sentiment_options"))
        values = action.get(key)
        allowed = {option["value"] for option in data.get(options_key, [])}
        if (not isinstance(values, list) or len(values) != len(row["opinions"])
                or any(not isinstance(value, str) or value not in allowed for value in values)):
            raise ValueError("각 의견의 분류를 현재 기준표에서 선택해주세요." if kind == "classify"
                             else "각 의견의 감성을 목록에서 선택해주세요.")
        if kind == "sentiment" and any(value != item["sentiment"]
                for index, (item, value) in enumerate(zip(row["opinions"], values, strict=True))
                if index not in row["sentiment_indices"]):
            raise ValueError("현재 선택한 분류의 감성만 수정할 수 있습니다.")
        if values == [item[field] for item in row["opinions"]]:
            raise ValueError("변경한 분류가 없습니다." if kind == "classify" else "변경한 감성이 없습니다.")
        return kind, (row["id"], values)
    if kind == "select":
        identifier = action.get("id")
        if identifier is not None and (not isinstance(identifier, str) or identifier not in {row["id"] for row in data["rows"]}):
            raise ValueError("현재 표에 없는 응답입니다.")
        return kind, identifier
    if kind in ("filter", "clear"):
        column = next((item for item in data["columns"] if item["key"] == action.get("column")), None)
        if column is None:
            raise ValueError("현재 표에 없는 열입니다.")
        if kind == "clear":
            return kind, (column["key"], {})
        values = action.get("values")
        choices = {item["value"] for item in column["options"]}
        if values is not None and (not isinstance(values, list) or any(not isinstance(value, str) or value not in choices for value in values)):
            raise ValueError("표의 값에서 조건을 선택해주세요.")
        query = action.get("search", "")
        if not isinstance(query, str) or len(query) > 5000 or (query and column["key"] != "VOC 원문"):
            raise ValueError("검색 조건을 확인해주세요.")
        condition = {}
        if values is not None and set(values) != choices:
            condition["values"] = list(dict.fromkeys(values))
        if query.strip():
            condition["search"] = query.strip()
        return kind, (column["key"], condition)
    raise ValueError("표의 조작을 확인해주세요.")


def save_table_classification(store, run, voc_id, code_ids):
    return save_table_opinions(store, run, voc_id, code_ids, "code_id", "표에서 분류 변경")


def save_table_sentiment(store, run, voc_id, sentiments):
    return save_table_opinions(store, run, voc_id, sentiments, "sentiment", "표에서 감성 변경")


def save_table_opinions(store, run, voc_id, values, field, reason):
    row = next((row for row in store.effective_results(run["id"]) if row["voc_id"] == voc_id), None)
    if (row is None or row["status"] != "success" or not row["result"]
            or row["result"]["response_type"] != "opinions"):
        raise ValueError("이 응답은 상세 화면에서 확인·수정해주세요.")
    opinions = row["result"]["issues"]
    if len(opinions) != len(values):
        raise ValueError("의견이 변경되었습니다. 표를 다시 열어주세요.")
    rows = [{**item, "item_id": str(index), field: value}
            for index, (item, value) in enumerate(zip(opinions, values, strict=True))]
    # 이유 입력 단계를 추가하지 않고, 표에서 수행한 조작을 이력에 기록한다.
    return save_correction(store, run["id"], voc_id, rows, "opinions", reason, run["result_revision"])


def show_response_table(view, options, filters, store, run, prefix, selected_ids, codes):
    selected_key, editing_key = prefix + "_selected_voc", prefix + "_editing_voc"
    if st.session_state.get(selected_key) not in set(view["VOC ID"]):
        st.session_state.pop(selected_key, None)
        st.session_state.pop(editing_key, None)
    data = table_data(view, options, filters, st.session_state.get(selected_key),
        [selected_ids, run["result_revision"], st.session_state.get(prefix + "_table_epoch", 0)],
        classification_edit_data(store, run, codes, selected_ids))
    assets = Path(__file__).parent / "components"
    renderer = st.components.v2.component("response_table", html='<div class="response-table"></div>',
        css=(assets / "response_table.css").read_text(encoding="utf-8"),
        js=(assets / "response_table.js").read_text(encoding="utf-8"), isolate_styles=False)
    result = renderer(key=prefix + "_response_table", data=data, on_action_change=lambda: None)
    action = result.action
    if action and isinstance(action, dict) and action.get("nonce") != st.session_state.get(prefix + "_last_table_action"):
        st.session_state[prefix + "_last_table_action"] = action.get("nonce")
        try:
            if store.run(run["id"])["result_revision"] != run["result_revision"]:
                raise ValueError("결과가 업데이트되었습니다. 다시 선택해주세요.")
            kind, value = table_action(data, action)
            if kind == "select":
                if st.session_state.get(selected_key) != value:
                    st.session_state.pop(editing_key, None)
                st.session_state[selected_key] = value
            elif kind == "classify":
                voc_id, code_ids = value
                save_table_classification(store, run, voc_id, code_ids)
                st.session_state["correction_notice"] = "분류를 변경했습니다."
            elif kind == "sentiment":
                voc_id, sentiments = value
                save_table_sentiment(store, run, voc_id, sentiments)
                st.session_state["correction_notice"] = "감성을 변경했습니다."
            else:
                column, condition = value
                following = dict(filters)
                if condition:
                    following[column] = condition
                else:
                    following.pop(column, None)
                st.session_state[prefix + "_filters"] = following
        except ValueError as exc:
            st.session_state[prefix + "_table_notice"] = str(exc)
        st.session_state[prefix + "_table_epoch"] = st.session_state.get(prefix + "_table_epoch", 0) + 1
        st.rerun()
    return data
