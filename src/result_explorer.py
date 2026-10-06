"""응답 목록의 범위와 원문·의견·집계 내보내기를 일치시킨다."""

from hashlib import sha256
import json

from src.chart_data import build_dashboard, dashboard_export
from src.grouping import group_results
from src.grouping import SENTIMENTS
from src.result_groups import grouped_dashboard
from src.category_summary import response_sentiment_labels

RESPONSE_FILTERS = ["전체", "의견 있음", "내용 없음", "검토 필요", "실패·미처리"]
NORMAL_STATES = {"의견 있음", "없음·무응답·모름"}
FAILED_STATES = {"실패", "미처리"}
FILTER_COLUMNS = ["VOC 원문", "분류", "감성", "응답 상태"]
STATUS_LABELS = {"없음·무응답·모름": "내용 없음", "수정값 승계 검토": "승계 검토",
    "해석 검토 필요": "해석 검토", "맞는 코드 없음·검토 필요": "분류 검토"}


def filter_options(view):
    """선택한 분류 안의 값 목록. 빈 결과에서도 감성 조건을 바꿀 수 있다."""
    return {column: (SENTIMENTS[1:] if column == "감성" else
        list(dict.fromkeys(view[column].fillna("").astype(str)))) for column in FILTER_COLUMNS}


def filtered_responses(originals, issues, codes, selected_ids, filters):
    base_group = group_results(originals, issues, codes, selected_ids)
    base = response_view(originals, base_group, selected_ids, "전체")
    response_sentiment = filters.get("_response_sentiment")
    if response_sentiment:
        labels = response_sentiment_labels(originals, issues)
        base = base[base["VOC ID"].isin(labels.index[labels.eq(response_sentiment)])].copy()
    view = base.copy()
    for column in FILTER_COLUMNS:
        condition = filters.get(column, {})
        if column != "감성" and condition.get("values") is not None:
            view = view[view[column].isin(condition["values"])].copy()
        query = condition.get("search", "").strip() if column == "VOC 원문" else ""
        if query:
            view = view[view[column].str.contains(query, regex=False, na=False)].copy()
    sentiments = filters.get("감성", {}).get("values")
    matching_issues = base_group.issues
    if sentiments is not None:
        matching_issues = matching_issues[matching_issues["감성"].isin(sentiments)].copy()
        matches = set(matching_issues["VOC ID"])
        if "중립" in sentiments:
            matches.update(base.loc[base["감성"].eq("중립"), "VOC ID"])
        view = view[view["VOC ID"].isin(matches)].copy()
        # 감성 조건에 해당하는 의견·분류를 화면과 현재 의견 CSV에 함께 적용한다.
        summary = group_results(originals, matching_issues, codes, selected_ids).originals.set_index("VOC ID")
        has_opinions = view["VOC ID"].isin(summary.index)
        view.loc[has_opinions, "분류"] = view.loc[has_opinions, "VOC ID"].map(summary["선택된 분류"])
        view.loc[has_opinions, "감성"] = view.loc[has_opinions, "VOC ID"].map(summary["선택 의견의 감성"])
    view = view.reset_index(drop=True)
    scoped = visible_group(originals, matching_issues, codes, view, selected_ids, "전체")
    return view, scoped, filter_options(base)


def filter_scope(filters):
    parts = []
    for column in FILTER_COLUMNS:
        condition = filters.get(column, {})
        values = condition.get("values")
        if values is not None:
            labels = [STATUS_LABELS.get(value, value) if column == "응답 상태" else value or "(빈 값)" for value in values]
            parts.append(f"{column.replace('VOC ', '')}: {' / '.join(labels) if labels else '선택 없음'}")
        if column == "VOC 원문" and condition.get("search", "").strip():
            parts.append(f"검색: {condition['search'].strip()}")
    return " · ".join(parts)


def response_view(originals, grouped, selected_ids, sentiment, state="전체", search=""):
    view = originals.copy()
    if selected_ids is not None or sentiment != "전체":
        view = view[view["VOC ID"].isin(grouped.originals["VOC ID"])].copy()
    status = view["응답 상태"]
    if state == "의견 있음":
        view = view[status.isin(["의견 있음", "맞는 코드 없음·검토 필요"])].copy()
    elif state == "내용 없음":
        view = view[status.eq("없음·무응답·모름")].copy()
    elif state == "검토 필요":
        view = view[~status.isin(NORMAL_STATES | FAILED_STATES)].copy()
    elif state == "실패·미처리":
        view = view[status.isin(FAILED_STATES)].copy()
    if search.strip():
        view = view[view["VOC 원문"].str.contains(search.strip(), regex=False, na=False)].copy()
    summary = grouped.originals.set_index("VOC ID")
    view["분류"] = view["VOC ID"].map(summary["선택된 분류"]).fillna("")
    view["감성"] = view["VOC ID"].map(summary["선택 의견의 감성"]).fillna(view["전체 감성"])
    status_labels = {"없음·무응답·모름": "내용 없음", "수정값 승계 검토": "확인 필요",
        "해석 검토 필요": "해석 확인", "실패": "실패", "미처리": "미처리"}
    empty_class = view["분류"].eq("")
    view.loc[empty_class, "분류"] = view.loc[empty_class, "응답 상태"].map(status_labels).fillna("")
    view["감성"] = view["감성"].replace({"해당 없음": "중립", "": "중립", "—": "중립"})
    return view.reset_index(drop=True)


def table_key(prefix, view, selected_ids, sentiment, state, search, revision):
    # 조건과 행 순서가 바뀌면 이전 행 번호를 다른 응답에 적용하지 않는다.
    signature = sha256(json.dumps([view["VOC ID"].tolist(), selected_ids, sentiment, state, search, revision],
        ensure_ascii=False).encode()).hexdigest()[:16]
    return f"{prefix}_responses_{signature}"


def visible_group(originals, issues, codes, view, selected_ids, sentiment):
    matching_issues = issues if issues.empty else issues[issues["VOC ID"].isin(view["VOC ID"])]
    return group_results(originals[originals["VOC ID"].isin(view["VOC ID"])],
        matching_issues, codes, selected_ids, sentiment)


def download_frame(kind, originals, grouped, run, book, scope_label, layout=None):
    if kind == "전체 응답":
        return originals
    if kind == "현재 원문":
        return grouped[0]
    if kind == "현재 의견·근거":
        return grouped[1].issues
    data = grouped_dashboard(grouped[1], book["codes"], layout) if layout else build_dashboard(grouped[1])
    return dashboard_export(data, run, book, scope_label)
