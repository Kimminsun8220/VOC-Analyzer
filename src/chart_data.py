"""묶어보기와 같은 의견 범위로 차트·집계표·선택 이벤트를 계산한다."""

from dataclasses import dataclass

import pandas as pd

from src.grouping import SENTIMENTS

COUNT = "고유 VOC 수"
PERCENT = "범위 내 비율 (%)"
DENOMINATOR = "분모 VOC 수"


@dataclass
class DashboardData:
    categories: pd.DataFrame
    codes: pd.DataFrame
    sentiments: pd.DataFrame
    denominator: int


def build_dashboard(grouped, filtered=False):
    """전체 보기의 분모는 무응답 포함 원본 수, 필터 적용 시 해당 범위의 고유 VOC 수."""
    opinions = grouped.issues
    denominator = grouped.voc_count if filtered else grouped.total_count

    def attach_ratio(frame):
        frame[DENOMINATOR] = denominator
        frame[PERCENT] = frame[COUNT] / denominator * 100 if denominator else 0.0
        return frame

    categories = opinions.groupby("대분류", sort=True)["VOC ID"].nunique().reset_index(name=COUNT)
    categories = attach_ratio(categories).sort_values([COUNT, "대분류"], ascending=[False, True], ignore_index=True)
    codes = opinions.groupby(["코드 ID", "대분류", "세부분류"], sort=True)["VOC ID"].nunique().reset_index(name=COUNT)
    codes = attach_ratio(codes).sort_values([COUNT, "대분류", "세부분류", "코드 ID"], ascending=[False, True, True, True], ignore_index=True)
    parent_counts = dict(zip(categories["대분류"], categories[COUNT]))
    codes["대분류 안 VOC 수"] = codes["대분류"].map(parent_counts)
    codes["대분류 내 비율 (%)"] = codes[COUNT] / codes["대분류 안 VOC 수"] * 100
    sentiments = attach_ratio(pd.DataFrame([
        {"감성": sentiment, COUNT: int(opinions.loc[opinions["감성"].eq(sentiment), "VOC ID"].nunique())}
        for sentiment in SENTIMENTS[1:]
    ]))
    return DashboardData(categories, codes, sentiments, denominator)


def chart_selection_values(event, allowed):
    """차트 순번 대신 데이터 키를 검증한다. 화면에 없는 값은 선택으로 쓰지 않는다."""
    allowed = set(allowed)
    values = []
    for point in event.get("selection", {}).get("points", []):
        custom = point.get("customdata")
        if not isinstance(custom, (list, tuple)) or not custom or not isinstance(custom[0], str):
            continue
        if custom[0] in allowed and custom[0] not in values:
            values.append(custom[0])
    return values


def selection_scope(kind, values, codes, selected_ids, sentiment):
    """차트 클릭은 현재 묶음 안에서만 좁힌다. 선택하지 않았던 분류를 다시 넣지 않는다."""
    visible = {code.id for code in codes} if selected_ids is None else set(selected_ids)
    if not values:
        return None
    if kind == "sentiment":
        valid = [value for value in values if value in SENTIMENTS[1:]]
        return (selected_ids, valid[-1]) if valid else None
    chosen = [code.id for code in codes if code.id in visible and (
        code.category in values if kind == "category" else code.id in values if kind == "code" else False)]
    return (chosen, sentiment) if chosen else None


def dashboard_export(data, run, book, scope_label):
    tables = []
    for label, frame in [("대분류", data.categories), ("세부분류", data.codes), ("감성", data.sentiments)]:
        table = frame.copy()
        table.insert(0, "집계 종류", label)
        tables.append(table)
    result = pd.concat(tables, ignore_index=True).fillna("")
    for label, value in [("분석 실행", run["id"]), ("코드북 버전", book["version"]),
                         ("결과 개정", run["result_revision"]), ("조회 범위", scope_label)]:
        result[label] = value
    return result
