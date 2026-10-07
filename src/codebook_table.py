"""기준표의 임시 편집·통합·분리와 새 버전 저장. 브라우저에는 표시 필드만 전달한다."""

from copy import deepcopy
from uuid import uuid4

from src.codebook_changes import save_revision
from src.codebook_definitions import complete_definitions
from src.models import Code

FIELDS = ("category", "name", "definition")


def new_id():
    return "C" + uuid4().hex[:12]


def table_draft(book):
    layouts = [change["rows"] for change in book["changes"] if change["kind"] == "table_layout"]
    parts = {row["id"]: row.get("parts", []) for row in layouts[-1]} if layouts else {}
    rows = [{**code.model_dump(), "parts": deepcopy(parts.get(code.id, []))} for code in book["codes"]]
    return {"rows": rows, "changes": [], "history": [], "revision": 0, "focus": None,
            "filters": {"category": None, "name": None}, "sort": {"category": None, "name": None}, "kept_rows": []}


def visible_rows(draft):
    return [{key: row[key] for key in ("id", *FIELDS)} for row in draft["rows"]]


def table_has_changes(book, draft):
    return visible_rows(draft) != [{key: getattr(code, key) for key in ("id", *FIELDS)} for code in book["codes"]]


def update_fields(draft, rows):
    if not isinstance(rows, list) or len(rows) != len(draft["rows"]):
        raise ValueError("표가 변경되었습니다. 다시 조작해주세요.")
    for source, edited in zip(draft["rows"], rows):
        if not isinstance(edited, dict) or source["id"] != edited.get("id"):
            raise ValueError("표가 변경되었습니다. 다시 조작해주세요.")
        if any(not isinstance(edited.get(field), str) for field in FIELDS):
            raise ValueError("대분류·세부분류·분류 기준을 문자로 입력해주세요.")
    for source, edited in zip(draft["rows"], rows):
        source.update({field: edited[field] for field in FIELDS})


def update_view(draft, payload):
    sorting = payload.get("sort")
    if isinstance(sorting, dict) and all(sorting.get(field) in (None, "asc", "desc") for field in ("category", "name")):
        draft["sort"] = {field: sorting.get(field) for field in ("category", "name")}
    filters = payload.get("filters")
    if isinstance(filters, dict) and all(filters.get(field) is None or
            isinstance(filters[field], list) and all(isinstance(value, str) for value in filters[field])
            for field in ("category", "name")):
        draft["filters"] = {field: deepcopy(filters.get(field)) for field in ("category", "name")}
    kept = payload.get("kept_rows")
    if isinstance(kept, list) and all(isinstance(identifier, str) for identifier in kept):
        identifiers = {row["id"] for row in draft["rows"]}
        draft["kept_rows"] = [identifier for identifier in kept if identifier in identifiers]


def joined(values):
    parts = []
    for value in values:
        for part in value.split("/"):
            part = part.strip()
            if part and part not in parts:
                parts.append(part)
    return "/".join(parts)


def table_action(draft, action):
    following = deepcopy(draft)
    update_fields(following, action.get("rows"))
    update_view(following, action)
    kind = action.get("kind")
    if kind == "undo":
        if following["history"]:
            previous = following["history"].pop()
            following.update(previous)
    else:
        previous = {"rows": deepcopy(following["rows"]), "changes": deepcopy(following["changes"]),
                    "kept_rows": deepcopy(following["kept_rows"])}
        rows = following["rows"]
        by_id = {row["id"]: row for row in rows}
        source_id = action.get("source_id")
        if kind == "add":
            row = {"id": new_id(), "category": "", "name": "", "definition": "", "reason": "사용자 분류 추가", "evidence": [], "parts": []}
            rows.append(row)
            following["focus"] = row["id"]
        elif source_id not in by_id:
            raise ValueError("조작할 분류를 다시 선택해주세요.")
        elif kind == "merge":
            target_id = action.get("target_id")
            if target_id not in by_id or target_id == source_id:
                raise ValueError("합칠 다른 분류를 선택해주세요.")
            selected = [row for row in rows if row["id"] in (source_id, target_id)]
            if any(not row["category"].strip() or not row["name"].strip() or not row["definition"].strip() for row in selected):
                raise ValueError("합칠 행의 대분류·세부분류·분류 기준을 먼저 입력해주세요.")
            merged = {"id": new_id(), "category": joined(row["category"] for row in selected),
                      "name": joined(row["name"] for row in selected),
                      "definition": "\n".join(f"{row['name']}: {row['definition']}" for row in selected),
                      "reason": "사용자 행 통합", "evidence": [item for row in selected for item in row["evidence"]],
                      "parts": deepcopy(selected)}
            index = min(rows.index(row) for row in selected)
            following["rows"] = [row for row in rows if row not in selected]
            following["rows"].insert(index, merged)
            following["changes"].append({"kind": "merge", "mapping": {row["id"]: merged["id"] for row in selected},
                "same_meaning": False, "before": deepcopy(selected), "after": deepcopy(merged)})
            following["focus"] = merged["id"]
        elif kind == "split":
            source = by_id[source_id]
            index = rows.index(source)
            additions = deepcopy(source["parts"]) if source["parts"] else [
                {**deepcopy(source), "id": new_id(), "parts": []},
                {**deepcopy(source), "id": new_id(), "name": "", "definition": "", "parts": []},
            ]
            remaining_ids = set(by_id) - {source_id}
            for row in additions:
                if row["id"] in remaining_ids:
                    row["id"] = new_id()
                remaining_ids.add(row["id"])
            rows[index:index + 1] = additions
            following["changes"].append({"kind": "split", "source_id": source_id,
                "target_ids": [row["id"] for row in additions], "before": deepcopy(source), "after": deepcopy(additions)})
            following["focus"] = additions[-1]["id"]
        elif kind == "delete":
            following["rows"] = [row for row in rows if row["id"] != source_id]
            following["changes"].append({"kind": "delete", "source_id": source_id})
        else:
            raise ValueError("지원하지 않는 조작입니다.")
        identifiers = {row["id"] for row in following["rows"]}
        following["kept_rows"] = [identifier for identifier in following["kept_rows"] if identifier in identifiers]
        if any(value is not None for value in following["filters"].values()):
            following["kept_rows"] += [row["id"] for row in following["rows"] if row["id"] not in by_id]
        following["history"].append(previous)
    following["revision"] += 1
    return following


def save_table_draft(store, book, draft, generate_definitions=None):
    if not draft["rows"]:
        raise ValueError("분류를 한 개 이상 남겨주세요.")
    rows = complete_definitions(store, book, "split", [code.id for code in book["codes"]], draft["rows"], generate_definitions)
    original = {code.id: code for code in book["codes"]}
    for row in rows:
        previous = original.get(row["id"])
        if previous is None or any(row[field] != getattr(previous, field) for field in FIELDS):
            row["reason"] = "사용자 분류 기준표 편집" + (" · AI 분류 기준" if row.get("definition_source") == "ai" else "")
    codes = [Code(**{key: row[key] for key in ("id", *FIELDS, "reason", "evidence")}) for row in rows]
    if book["status"] != "draft" and [(code.id, code.category, code.name, code.definition) for code in codes] == [
            (code.id, code.category, code.name, code.definition) for code in book["codes"]]:
        raise ValueError("변경한 내용이 없습니다.")
    changes = deepcopy(draft["changes"])
    changes += [{"kind": "user_confirmation" if book["status"] == "draft" else "user_edit", "before": [code.model_dump() for code in book["codes"]]},
                {"kind": "table_layout", "rows": rows}]
    return save_revision(store, book, codes, changes)
