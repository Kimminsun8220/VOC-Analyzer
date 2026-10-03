"""사용자 이름·수동 기준을 보존하며 빈 기준만 AI로 채운다."""

from pydantic import ValidationError

from src.models import Code, CodeDefinitions


def blank_definition(value):
    return value is None or (isinstance(value, str) and not value.strip())


def complete_definitions(store, book, operation, source_ids, rows, generate=None):
    prepared = [dict(row) for row in rows]
    missing = {index for index, row in enumerate(prepared) if blank_definition(row.get("definition"))}
    # 이름·길이·자료형 오류는 AI 호출 전에 확인한다.
    for index, row in enumerate(prepared):
        Code(id=str(index), category=row.get("category"), name=row.get("name"),
             definition="AI 생성 예정" if index in missing else row.get("definition"), reason="입력 검증")
    if not missing:
        return prepared
    if generate is None:
        raise ValueError("빈 분류 기준을 생성하려면 Gemini 설정이 필요합니다. 분류 기준을 직접 입력해도 저장할 수 있습니다.")

    # 동일한 표본·본문 제한을 사용하며 부가 열은 AI에 전달하지 않는다.
    from src.workflow import sample_records

    targets = [{"target_index": index, "category": row["category"], "name": row["name"],
                "definition": "" if index in missing else row["definition"]} for index, row in enumerate(prepared)]
    response = generate(operation, book["codes"], source_ids, targets,
                        sample_records(store.dataset(book["dataset_id"])["records"]), book["context"])
    try:
        response = CodeDefinitions.model_validate(response)
    except ValidationError:
        raise ValueError("AI 분류 기준 응답 형식이 올바르지 않습니다. 다시 저장해주세요.") from None
    indices = [item.target_index for item in response.definitions]
    if len(indices) != len(missing) or set(indices) != missing:
        raise ValueError("AI 분류 기준이 누락되거나 입력한 이름과 일치하지 않습니다. 다시 저장해주세요.")
    for item in response.definitions:
        if not item.definition.strip():
            raise ValueError("AI가 빈 분류 기준을 반환했습니다. 다시 저장해주세요.")
        prepared[item.target_index]["definition"] = item.definition
        prepared[item.target_index]["definition_source"] = "ai"
    return prepared
