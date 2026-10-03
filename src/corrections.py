"""AI 결과를 보존하면서 사용자 변경만 승계한다. 모호한 대응은 검토로 남긴다."""

from copy import deepcopy
import json
import time
from uuid import uuid4

from src.codebook_changes import code_mapping
from src.models import CodingResult, Issue, validate_result


def evidence_span(text, quote):
    start = text.find(quote)
    if not quote or start < 0 or text.find(quote, start + 1) >= 0:
        return None
    return [start, start + len(quote)]


def make_edits(before, after, source_indices, record, origin):
    if after.response_type == "no_content":
        return [{"kind": "response", "origin_id": origin, "after": after.model_dump()}]
    used, edits = set(), []
    for item, index in zip(after.issues, source_indices, strict=True):
        new = item.model_dump()
        old = None
        if index is not None:
            if index in used or index < 0 or index >= len(before.issues):
                raise ValueError("의견 행 식별자가 바뀌었습니다. 편집을 취소하고 다시 열어주세요.")
            used.add(index)
            old = before.issues[index].model_dump()
        if old != new:
            edits.append({"kind": "update" if old else "add", "origin_id": origin, "before": old, "after": new,
                "fields": [key for key in new if old is None or old[key] != new[key]],
                "before_span": evidence_span(record["text"], old["evidence_text"]) if old else None,
                "after_span": evidence_span(record["text"], new["evidence_text"])})
    for index, item in enumerate(before.issues):
        if index not in used:
            edits.append({"kind": "delete", "origin_id": origin, "before": item.model_dump(), "after": None,
                          "before_span": evidence_span(record["text"], item.evidence_text), "after_span": None})
    return edits


def refresh_status(store, db, run):
    """새 현재 결과와 검토 여부를 같은 트랜잭션에서 반영한다."""
    rows = [dict(row) for row in db.execute("SELECT * FROM results WHERE run_id=? AND round=?", (run["id"], run["round"]))]
    changes = {row["voc_id"]: dict(row) for row in db.execute(
        "SELECT * FROM corrections WHERE run_id=? AND round=? ORDER BY revision", (run["id"], run["round"]))}
    failed, review = False, False
    for row in rows:
        change = changes.get(row["voc_id"])
        if change and change["status"] == "needs_review":
            review = True
            continue
        result = json.loads(change["result_json"] if change else row["result_json"]) if change or row["result_json"] else None
        if not change and row["status"] == "failed":
            failed = True
        elif not result or result["response_type"] == "unclear" or any(i["code_id"] is None for i in result["issues"]):
            review = True
    if len(rows) != len(store.dataset(run["dataset_id"])["records"]):
        failed = True
    status = "failed" if failed else "needs_review" if review else "completed"
    error = "실패·미처리 응답을 이어서 처리해주세요." if failed else "미해결 분류·수정값 승계 검토가 남아 있습니다." if review else ""
    db.execute("UPDATE runs SET status=?,error=?,lease_until=0 WHERE id=?", (status, error, run["id"]))


def save_correction(store, run_id, voc_id, rows, response_type, reason, expected_revision, no_content_reason=""):
    """rows의 item_id는 편집 시작 시 현재 목록의 순번. 승계에는 원문 구간을 사용한다."""
    if not reason.strip():
        raise ValueError("수정·검토 이유를 입력해주세요.")
    if response_type not in {"opinions", "no_content"}:
        raise ValueError("의견 있음 또는 내용 없음을 선택해주세요.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        run = store.run(run_id)
        if run["status"] not in {"completed", "needs_review"} or run["lease_until"] > time.time():
            raise ValueError("완료되었거나 검토 중인 분석에서만 수정할 수 있습니다.")
        if run["result_revision"] != expected_revision:
            raise ValueError("다른 화면에서 결과가 변경됐습니다. 현재 결과를 다시 연 뒤 수정해주세요.")
        record = next((r for r in store.dataset(run["dataset_id"])["records"] if r["id"] == voc_id), None)
        if record is None:
            raise ValueError("이 입력 자료의 VOC를 선택해주세요.")
        raw = next((r for r in store.results(run_id) if r["voc_id"] == voc_id and r["status"] == "success"), None)
        if raw is None:
            raise ValueError("AI 처리 실패·미처리 응답은 먼저 재시도해주세요.")
        previous = store.corrections(run_id).get(voc_id)
        resolving = previous is not None and previous["status"] == "needs_review"
        before = CodingResult.model_validate(previous["result"] if previous and not resolving else raw["result"])
        issues, indices = [], []
        if response_type == "opinions":
            for row in rows:
                value = dict(row)
                identifier = value.pop("item_id", None)
                if identifier is not None and (not isinstance(identifier, str) or not identifier.isdigit()):
                    raise ValueError("의견 식별자가 올바르지 않습니다.")
                indices.append(int(identifier) if identifier is not None else None)
                issues.append(Issue.model_validate(value))
            if any(item.code_id is None for item in issues):
                raise ValueError("모든 의견에 현재 분류 기준표의 분류를 선택해주세요.")
        after = CodingResult(voc_id=voc_id, response_type=response_type, issues=issues,
                             no_content_reason=no_content_reason.strip() if response_type == "no_content" else "")
        validate_result(after, record, store.codebook(run["codebook_id"])["codes"], run["context"])
        origin = uuid4().hex
        edits = make_edits(before, after, indices, record, origin)
        if not edits and not resolving:
            raise ValueError("변경한 의견이나 응답 상태가 없습니다.")
        if resolving:
            # 사용자가 현재 기준으로 전체 목록을 확인했다. 이전 모호한 패치는 다시 적용하지 않는다.
            edits.insert(0, {"kind": "acknowledge", "origin_id": origin,
                            "resolved_origins": sorted({e["origin_id"] for e in previous["edits"]})})
        elif previous:
            edits = previous["edits"] + edits
        identifier = store.insert_correction(db, run, voc_id, after.model_dump(), edits,
            {"result": before.model_dump(), "correction": previous}, reason.strip(),
            kind="review_resolution" if resolving else "manual")
        refresh_status(store, db, run)
        return identifier


def map_edits(edits, mapping):
    mapped = deepcopy(edits)
    for edit in mapped:
        if edit["kind"] in {"response", "acknowledge"}:
            continue
        for side in ("before", "after"):
            item = edit.get(side)
            if item and item["code_id"] is not None:
                target = mapping.get(item["code_id"])
                if target is None:
                    raise ValueError("수정에 관련된 코드가 분리·삭제·정의 변경되었거나 버전 간 대응이 없습니다.")
                item["code_id"] = target
    return mapped


def apply_edits(candidate, edits, record):
    result = candidate.model_copy(deep=True)
    for edit in edits:
        kind = edit["kind"]
        if kind == "acknowledge":
            continue
        if kind == "response":
            result = CodingResult.model_validate(edit["after"])
            continue
        before, after = edit.get("before"), edit.get("after")
        anchors = [edit[side + "_span"] for side in ("before", "after") if edit.get(side)]
        if any(span is None for span in anchors):
            raise ValueError("근거 구절이 원문에서 반복되어 수정할 위치를 하나로 확인할 수 없습니다.")
        matches = [index for index, item in enumerate(result.issues)
                   if evidence_span(record["text"], item.evidence_text) in anchors]
        if len(matches) > 1:
            codes = {item["code_id"] for item in (before, after) if item}
            matches = [index for index in matches if result.issues[index].code_id in codes]
        if len(matches) > 1:
            exact = [index for index in matches if result.issues[index].model_dump() in (before, after)]
            matches = exact or matches
        if len(matches) > 1:
            raise ValueError("같은 근거에 여러 의견이 있어 수정 대상을 하나로 확인할 수 없습니다.")
        if not matches and kind in {"delete", "add"}:
            # AI가 근거를 넓히거나 줄인 경우에는 삭제/추가 의도를 추측하지 않는다.
            if any(any(item.evidence_text in v["evidence_text"] or v["evidence_text"] in item.evidence_text
                       for v in (before, after) if v) for item in result.issues):
                raise ValueError("새 의견의 근거 범위가 바뀌어 기존 추가·삭제 의도를 확인해야 합니다.")
        if kind == "delete":
            if matches:
                result.issues.pop(matches[0])
        elif kind == "update":
            if not matches:
                raise ValueError("새 AI 제안에서 수정 대상의 같은 원문 근거를 찾지 못했습니다.")
            value = result.issues[matches[0]].model_dump()
            value.update({field: after[field] for field in edit["fields"]})
            if value["code_id"] is not None:
                value["missing_code"] = ""
            result.issues[matches[0]] = Issue.model_validate(value)
        elif kind == "add":
            if matches:
                if result.issues[matches[0]].code_id != after["code_id"]:
                    raise ValueError("사용자가 추가한 근거에 다른 새 분류가 있어 확인이 필요합니다.")
                result.issues[matches[0]] = Issue.model_validate(after)
            else:
                result.issues.append(Issue.model_validate(after))
        if result.issues:
            result.response_type = "opinions"
            result.no_content_reason = result.review_reason = ""
    if not result.issues and result.response_type == "opinions":
        raise ValueError("승계 후 의견이 비었습니다. 내용 없는 응답인지 직접 확인해주세요.")
    unique = {json.dumps(i.model_dump(), sort_keys=True, ensure_ascii=False): i for i in result.issues}
    result.issues = list(unique.values())
    result.summary, result.keywords = "", []
    return result


def apply_inheritance(store, run_id):
    run = store.run(run_id)
    sources = json.loads(run["inheritance_json"])
    if not sources:
        return
    records = {r["id"]: r for r in store.dataset(run["dataset_id"])["records"]}
    raw = {r["voc_id"]: r for r in store.results(run_id)}
    book = store.codebook(run["codebook_id"])
    existing = store.corrections(run_id)
    for source in sources:
        voc_id = source["voc_id"]
        if voc_id in existing or voc_id not in raw or raw[voc_id]["status"] != "success":
            continue
        edits, value, status, reason = source["edits"], None, "applied", "이전 실행의 사용자 변경 자동 승계"
        try:
            edits = map_edits(edits, code_mapping(store, source["codebook_id"], book["id"]))
            result = apply_edits(CodingResult.model_validate(raw[voc_id]["result"]), edits, records[voc_id])
            validate_result(result, records[voc_id], book["codes"], run["context"])
            value = result.model_dump()
        except ValueError as exc:
            status, reason = "needs_review", str(exc)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            store.insert_correction(db, run, voc_id, value, edits, source, reason, kind="inherited", status=status)
