"""명시적인 코드 변경 이력과 사용자 수정 승계용 대응 관계."""

from uuid import uuid4

from src.models import Code, normalized, validate_codes


def save_revision(store, book, codes, changes):
    validate_codes(codes)
    current = {code.id: code for code in codes}
    protected = [code.model_dump() for code in book["codes"] if code.id not in current
                 or normalized(code.definition) != normalized(current[code.id].definition)]
    return store.save_codebook(book["dataset_id"], codes, book["context"], book["model"],
        book["sample_ids"], "confirmed", book["id"], changes, book["constraints"] + protected)


def edit_codebook(store, book_id, rows):
    book = store.codebook(book_id)
    original = {code.id: code for code in book["codes"]}
    codes = []
    for row in rows:
        identifier = row.get("id") or "C" + uuid4().hex[:12]
        if row.get("id") and identifier not in original:
            raise ValueError("기존 코드 ID는 변경할 수 없습니다.")
        source = original.get(identifier)
        codes.append(Code(id=identifier, category=row.get("category"), name=row.get("name"),
            definition=row.get("definition"), reason="사용자 분류 기준표 편집", evidence=source.evidence if source else []))
    if [(c.id, c.category, c.name, c.definition) for c in codes] == [
            (c.id, c.category, c.name, c.definition) for c in book["codes"]]:
        raise ValueError("변경한 내용이 없습니다.")
    return save_revision(store, book, codes, [{"kind": "user_edit", "before": [c.model_dump() for c in book["codes"]]}])


def merge_codes(store, book_id, selected_ids, name, definition, same_meaning=False):
    book = store.codebook(book_id)
    selected = [code for code in book["codes"] if code.id in set(selected_ids)]
    if len(selected) < 2 or {c.id for c in selected} != set(selected_ids):
        raise ValueError("통합할 기존 세부분류를 두 개 이상 선택해주세요.")
    if len({c.category for c in selected}) != 1:
        raise ValueError("같은 대분류의 코드만 통합할 수 있습니다. 먼저 대분류를 변경해주세요.")
    if not same_meaning:
        raise ValueError("정의와 원문을 확인해 같은 의미의 코드인지 확인해주세요. 함께 조회만 하려면 묶어보기를 사용하세요.")
    merged = Code(id="C" + uuid4().hex[:12], category=selected[0].category, name=name,
        definition=definition, reason="사용자가 동일 의미로 통합", evidence=[e for c in selected for e in c.evidence])
    codes = [c for c in book["codes"] if c.id not in selected_ids] + [merged]
    return save_revision(store, book, codes, [{"kind": "merge", "mapping": {c.id: merged.id for c in selected},
        "same_meaning": True, "before": [c.model_dump() for c in selected], "after": merged.model_dump()}])


def split_code(store, book_id, source_id, rows):
    book = store.codebook(book_id)
    source = next((c for c in book["codes"] if c.id == source_id), None)
    if source is None or len(rows) < 2:
        raise ValueError("분리할 코드와 두 개 이상의 새 이름·정의를 입력해주세요.")
    additions = [Code(id="C" + uuid4().hex[:12], category=source.category, name=row.get("name"),
        definition=row.get("definition"), reason="사용자 코드 분리", evidence=source.evidence) for row in rows]
    return save_revision(store, book, [c for c in book["codes"] if c.id != source_id] + additions,
        [{"kind": "split", "source_id": source_id, "target_ids": [c.id for c in additions],
          "before": source.model_dump(), "after": [c.model_dump() for c in additions]}])


def code_mapping(store, source_id, target_id):
    """명시된 버전 경로만 따른다. 분리·삭제·정의 변경은 None으로 남긴다."""
    source = store.codebook(source_id)
    mapping = {c.id: c.id for c in source["codes"]}
    if source_id == target_id:
        return mapping
    chain, current = [], store.codebook(target_id)
    while current["id"] != source_id:
        chain.append(current)
        if not current["parent_id"]:
            return {identifier: None for identifier in mapping}
        current = store.codebook(current["parent_id"])
    previous = {c.id: c for c in source["codes"]}
    for book in reversed(chain):
        following = {c.id: c for c in book["codes"]}
        merged = {}
        for change in book["changes"]:
            if change["kind"] == "merge" and change.get("same_meaning"):
                merged.update(change["mapping"])
        for original, identifier in mapping.items():
            if identifier is None:
                continue
            if identifier in merged and merged[identifier] in following:
                mapping[original] = merged[identifier]
            elif identifier not in following or identifier not in previous or (
                    normalized(previous[identifier].definition) != normalized(following[identifier].definition)):
                mapping[original] = None
        previous = following
    return mapping
