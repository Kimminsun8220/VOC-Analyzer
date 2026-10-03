"""코드 의미를 바꾸지 않고 선택한 의견의 합집합을 계산한다."""

from dataclasses import dataclass

import pandas as pd

SENTIMENTS = ["전체", "긍정", "부정", "중립", "판단 불가"]
ORIGINAL_COLUMNS = ["VOC ID", "VOC 원문", "선택된 분류", "선택 의견의 감성", "선택 의견 수"]


@dataclass
class GroupedResults:
    issues: pd.DataFrame
    originals: pd.DataFrame
    counts: pd.DataFrame
    total_count: int
    overlap_count: int

    @property
    def voc_count(self):
        return len(self.originals)

    @property
    def percent(self):
        return self.voc_count / self.total_count * 100 if self.total_count else 0.0


def group_results(originals, issues, codes, selected_ids=None, sentiment="전체"):
    """None은 전체, []는 빈 선택. 감성도 선택 범위 안의 의견에만 적용한다."""
    if sentiment not in SENTIMENTS:
        raise ValueError("허용된 감성을 선택해주세요.")
    code_map = {code.id: code for code in codes}
    if selected_ids is not None and set(selected_ids) - code_map.keys():
        raise ValueError("현재 실행의 분류 기준표에 없는 분류입니다. 선택을 다시 확인해주세요.")
    filtered = issues.copy()
    if filtered.empty:
        filtered = filtered.reindex(columns=list(dict.fromkeys([
            *filtered.columns, "VOC ID", "VOC 원문", "코드 ID", "대분류", "세부분류", "감성",
        ])))
    if selected_ids is not None:
        filtered = filtered[filtered["코드 ID"].isin(selected_ids)].copy()
    if sentiment != "전체":
        filtered = filtered[filtered["감성"].eq(sentiment)].copy()

    original_map = dict(zip(originals.get("VOC ID", []), originals.get("VOC 원문", [])))
    unique_rows = []
    overlap_count = 0
    for voc_id, opinions in filtered.groupby("VOC ID", sort=False):
        if voc_id not in original_map:
            raise ValueError("분류 결과에 해당하는 원문이 없습니다.")
        unique_rows.append({
            "VOC ID": voc_id, "VOC 원문": original_map[voc_id],
            "선택된 분류": " / ".join(dict.fromkeys(opinions["대분류"] + " → " + opinions["세부분류"])),
            "선택 의견의 감성": " · ".join(s for s in SENTIMENTS[1:] if s in set(opinions["감성"])),
            "선택 의견 수": len(opinions),
        })
        if opinions.loc[opinions["코드 ID"].ne(""), "코드 ID"].nunique() >= 2:
            overlap_count += 1
    counts = []
    selected_set = set(selected_ids) if selected_ids is not None else set(code_map)
    for code in codes:
        if code.id in selected_set:
            count = filtered.loc[filtered["코드 ID"].eq(code.id), "VOC ID"].nunique()
            counts.append({"대분류": code.category, "세부분류": code.name, "고유 VOC 수": int(count)})
    if selected_ids is None and filtered["코드 ID"].eq("").any():
        counts.append({"대분류": "검토 필요", "세부분류": "맞는 코드 없음",
                       "고유 VOC 수": int(filtered.loc[filtered["코드 ID"].eq(""), "VOC ID"].nunique())})
    return GroupedResults(
        issues=filtered,
        originals=pd.DataFrame(unique_rows, columns=ORIGINAL_COLUMNS),
        counts=pd.DataFrame(counts, columns=["대분류", "세부분류", "고유 VOC 수"]),
        total_count=int(originals["VOC ID"].nunique()) if not originals.empty else 0,
        overlap_count=overlap_count,
    )
