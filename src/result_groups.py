"""기존 분류를 바꾸지 않고 차트의 묶음과 고유 응답 집계를 관리한다."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

import pandas as pd

from src.chart_data import COUNT, DENOMINATOR, PERCENT, DashboardData, build_dashboard


def group_id(kind, members):
    signature = json.dumps([kind, sorted(members)], ensure_ascii=False).encode()
    return sha256(signature).hexdigest()[:20]


def initial_layout(codes):
    return {"category": [[name] for name in dict.fromkeys(code.category for code in codes)],
            "code": [[code.id] for code in codes], "history": [], "revision": 0}


def change_layout(layout, kind, source_id=None, target_id=None):
    following = deepcopy(layout)
    if kind == "undo":
        if following["history"]:
            following.update(following["history"].pop())
        following["revision"] += 1
        return following
    if kind not in ("category", "code"):
        raise ValueError("같은 차트의 다른 분류에 놓아주세요.")
    groups = following[kind]
    selected = [members for members in groups if group_id(kind, members) in (source_id, target_id)]
    if source_id == target_id or len(selected) != 2:
        raise ValueError("합칠 다른 분류를 다시 선택해주세요.")
    previous = {level: deepcopy(following[level]) for level in ("category", "code")}
    index = min(groups.index(members) for members in selected)
    merged = [member for members in selected for member in members]
    following[kind] = [members for members in groups if members not in selected]
    following[kind].insert(index, merged)
    following["history"].append(previous)
    following["revision"] += 1
    return following


def load_code_group(layout, codes, identifiers):
    """저장한 선택은 원래 코드 목록에서 복원해 다른 묶음의 코드를 섞지 않는다."""
    ordered = [code.id for code in codes if code.id in identifiers]
    if not ordered or set(identifiers) - set(ordered):
        raise ValueError("현재 분류 기준표에서 다시 선택해주세요.")
    following = deepcopy(layout)
    following["history"].append({level: deepcopy(layout[level]) for level in ("category", "code")})
    groups = []
    for code in codes:
        if code.id == ordered[0]:
            groups.append(ordered)
        elif code.id not in ordered:
            groups.append([code.id])
    following["code"] = groups
    following["revision"] += 1
    return following


def grouped_chart(grouped, codes, layout, kind):
    """복수 의견의 건수를 더하지 않고 원래 VOC ID의 합집합을 집계한다."""
    code_map = {code.id: code for code in codes}
    rows = []
    for members in layout[kind]:
        identifiers = [code.id for code in codes if code.category in members] if kind == "category" else members
        opinions = grouped.issues[grouped.issues["코드 ID"].isin(identifiers)]
        count = int(opinions["VOC ID"].nunique())
        if not count:
            continue
        if kind == "category":
            label = "/".join(members)
        else:
            categories = list(dict.fromkeys(code_map[identifier].category for identifier in members))
            if len(categories) == 1:
                label = f"[{categories[0]}] " + "/".join(code_map[identifier].name for identifier in members)
            else:
                label = " / ".join(f"[{code_map[identifier].category}] {code_map[identifier].name}" for identifier in members)
        rows.append({"id": group_id(kind, members), "분류": label, "members": members[:], "code_ids": identifiers[:],
                     COUNT: count, PERCENT: count / grouped.total_count * 100 if grouped.total_count else 0.0,
                     DENOMINATOR: grouped.total_count})
    columns = ["id", "분류", "members", "code_ids", COUNT, PERCENT, DENOMINATOR]
    return pd.DataFrame(rows, columns=columns).sort_values([COUNT, "분류", "id"],
        ascending=[False, True, True], ignore_index=True)


def chart_signature(run, layout, measure, top_n, epoch):
    return sha256(json.dumps([run["result_revision"], layout["revision"], measure, top_n, epoch]).encode()).hexdigest()[:20]


def drill_code_chart(grouped, codes, layout, categories):
    """대분류 안의 코드만 표시하되 감성 응답의 분모와 원래 묶음을 보존한다."""
    if not categories:
        return grouped_chart(grouped, codes, layout, "code")
    allowed = {code.id for code in codes if code.category in categories}
    projected = {**layout, "code": [[identifier for identifier in members if identifier in allowed]
                                  for members in layout["code"]]}
    projected["code"] = [members for members in projected["code"] if members]
    scoped = replace(grouped, issues=grouped.issues[grouped.issues["코드 ID"].isin(allowed)])
    frame = grouped_chart(scoped, codes, projected, "code")
    complete = {group_id("code", members) for members in layout["code"]}
    frame["can_merge"] = frame["id"].isin(complete)
    return frame


def grouped_dashboard(grouped, codes, layout):
    """상세 집계와 CSV에도 같은 묶음을 적용하되 현재 범위의 분모를 쓴다."""
    categories = grouped_chart(grouped, codes, layout, "category")
    categories = categories.rename(columns={"분류": "대분류"})[["대분류", COUNT, PERCENT, DENOMINATOR]]
    code_map = {code.id: code for code in codes}
    rows = []
    for row in grouped_chart(grouped, codes, layout, "code").to_dict("records"):
        members = [code_map[identifier] for identifier in row["members"]]
        parents = list(dict.fromkeys(code.category for code in members))
        parent_members = {name for group in layout["category"] if set(group) & set(parents) for name in group}
        parent_count = int(grouped.issues.loc[grouped.issues["대분류"].isin(parent_members), "VOC ID"].nunique())
        rows.append({"코드 ID": "/".join(row["members"]), "대분류": "/".join(parents),
            "세부분류": "/".join(code.name for code in members), "분류": row["분류"],
            COUNT: row[COUNT], PERCENT: row[PERCENT], DENOMINATOR: row[DENOMINATOR],
            "대분류 안 VOC 수": parent_count,
            "대분류 내 비율 (%)": row[COUNT] / parent_count * 100 if parent_count else 0.0})
    columns = ["코드 ID", "대분류", "세부분류", "분류", COUNT, PERCENT, DENOMINATOR,
               "대분류 안 VOC 수", "대분류 내 비율 (%)"]
    return DashboardData(categories, pd.DataFrame(rows, columns=columns), build_dashboard(grouped).sentiments,
                         grouped.total_count)


def chart_action(store, run, layout, kind, frame, signature, action):
    """표시 중인 분류만 받고 이전 개정 화면에서 온 조작을 거절한다."""
    if action.get("signature") != signature or store.run(run["id"])["result_revision"] != run["result_revision"]:
        raise ValueError("결과가 변경되었습니다. 새 화면을 확인한 뒤 다시 조작해주세요.")
    by_id = {row["id"]: row for row in frame.to_dict("records")}
    source = by_id.get(action.get("source_id"))
    if not source:
        raise ValueError("화면에 표시된 분류를 다시 선택해주세요.")
    if action.get("kind") == "merge":
        target = by_id.get(action.get("target_id"))
        if not target:
            raise ValueError("같은 차트의 다른 분류에 놓아주세요.")
        if not source.get("can_merge", True) or not target.get("can_merge", True):
            raise ValueError("다른 대분류가 포함된 묶음은 대분류 조건을 해제한 뒤 합쳐주세요.")
        following = change_layout(layout, kind, source["id"], target["id"])
        members = [members for members in following[kind] if set(source["members"]).issubset(members)][0]
        return following, members, False
    if action.get("kind") == "open":
        return layout, source["members"], True
    if action.get("kind") == "filter" and kind == "category":
        return layout, source["members"], False
    raise ValueError("지원하지 않는 조작입니다.")
