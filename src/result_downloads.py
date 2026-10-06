"""전체 분류 결과를 원문별 결과와 분류별 통계 엑셀로 내보낸다."""

from io import BytesIO

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from src.category_summary import SENTIMENT_COLORS, response_sentiments
from src.chart_data import COUNT, PERCENT
from src.grouping import group_results
from src.result_groups import grouped_chart, initial_layout

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
VOC_COLUMNS = ["VOC ID", "VOC 원문", "대분류", "세부분류", "감성"]
STAT_COLUMNS = ["전체 건수", "%", *[f"{label} (%)" for label in SENTIMENT_COLORS]]


def classification_frame(originals, issues):
    classifications = issues.reindex(columns=["VOC ID", "대분류", "세부분류", "감성"])
    # 같은 분류·감성의 반복 근거는 한 행. 다른 분류와 다른 VOC ID는 보존한다.
    return originals.reindex(columns=["VOC ID", "VOC 원문"]).merge(
        classifications, on="VOC ID", how="left", sort=False).reindex(columns=VOC_COLUMNS).fillna("").drop_duplicates()


def statistics_frames(originals, issues, codes, layout=None):
    grouped = group_results(originals, issues, codes)
    layout = layout or initial_layout(codes)
    code_map = {code.id: code for code in codes}
    sheets = {}
    for kind, sheet_name in [("category", "대분류"), ("code", "세부분류")]:
        rows = []
        for row in grouped_chart(grouped, codes, layout, kind).to_dict("records"):
            opinions = grouped.issues[grouped.issues["코드 ID"].isin(row["code_ids"])]
            view = originals[originals["VOC ID"].isin(opinions["VOC ID"])]
            sentiments = response_sentiments(view, opinions).set_index("감성")[PERCENT]
            if kind == "category":
                labels = {"대분류": row["분류"]}
            else:
                members = [code_map[identifier] for identifier in row["members"]]
                parents = list(dict.fromkeys(code.category for code in members))
                labels = {"대분류": "/".join(parents), "세부분류":
                    "/".join(code.name for code in members) if len(parents) == 1 else row["분류"]}
            rows.append({**labels, "전체 건수": row[COUNT], "%": row[PERCENT],
                         **{f"{label} (%)": sentiments[label] for label in SENTIMENT_COLORS}})
        labels = ["대분류"] if kind == "category" else ["대분류", "세부분류"]
        sheets[sheet_name] = pd.DataFrame(rows, columns=[*labels, *STAT_COLUMNS])
    return sheets


def xlsx_download(sheets):
    """ID·원문은 텍스트로, 건수·비율은 숫자로 저장한다."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, frame in sheets.items():
        sheet = workbook.create_sheet(name)
        sheet.append(list(frame.columns))
        for values in frame.itertuples(index=False, name=None):
            sheet.append(list(values))
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.row_dimensions[1].height = 32
        for index, label in enumerate(frame.columns, 1):
            width = {"VOC ID": 14, "VOC 원문": 70, "대분류": 24, "세부분류": 32, "감성": 18}.get(label, 16)
            if "무응답" in label:
                width = 30
            sheet.column_dimensions[get_column_letter(index)].width = width
            header = sheet.cell(1, index)
            header.font = Font(name="맑은 고딕", bold=True, color="FFFFFF", size=11)
            header.fill = PatternFill("solid", fgColor="1E3A8A")
            header.alignment = Alignment(vertical="center", wrap_text=True)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.font = Font(name="맑은 고딕", size=11)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if isinstance(cell.value, str):
                    # 원문이 '='로 시작해도 수식으로 실행하지 않고 그대로 보존한다.
                    cell.data_type = "s"
                    cell.number_format = "@"
                else:
                    label = frame.columns[cell.column - 1]
                    cell.number_format = "0.0" if label == "%" or "(%)" in label else "#,##0"
        sheet.sheet_view.showGridLines = True
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
