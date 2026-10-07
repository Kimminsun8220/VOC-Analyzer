"""AI 출력 계약과 원문에 대한 검증. 구조 검증은 의미 정확도를 보장하지 않는다."""

import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sentiment = Literal["긍정", "부정", "중립", "판단 불가"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(Contract):
    voc_id: str
    quote: str = Field(min_length=1)


class DraftCode(Contract):
    category: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    definition: str = Field(min_length=1, max_length=1500)
    reason: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class Code(DraftCode):
    id: str
    evidence: list[Evidence] = Field(default_factory=list)


class CodebookDraft(Contract):
    codes: list[DraftCode]


class CodeDefinition(Contract):
    target_index: int = Field(ge=0)
    definition: str = Field(min_length=1, max_length=1500)


class CodeDefinitions(Contract):
    definitions: list[CodeDefinition]


class Issue(Contract):
    code_id: str | None
    sentiment: Sentiment
    evidence_text: str = Field(min_length=1)
    context_evidence_text: str = ""
    subject_label: str | None = None
    subject_evidence_text: str = ""
    subject_evidence_source: Literal["original", "context"] | None = None
    missing_code: str = ""

    @model_validator(mode="after")
    def generic_staff_has_unknown_role(self):
        if self.subject_label in {"직원", "담당자", "관계자", "staff", "employee"}:
            self.subject_label = None
            self.subject_evidence_text = ""
            self.subject_evidence_source = None
        return self


class CodingResult(Contract):
    voc_id: str
    response_type: Literal["opinions", "no_content", "unclear"]
    no_content_reason: str = ""
    review_reason: str = ""
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_response(self):
        if self.response_type == "opinions" and not self.issues:
            raise ValueError("의견이 있는 응답에는 의견 목록이 필요합니다.")
        if self.response_type != "opinions" and self.issues:
            raise ValueError("내용 없음·해석 불가 응답에는 의견을 부여할 수 없습니다.")
        if self.response_type == "no_content" and not self.no_content_reason.strip():
            raise ValueError("내용 없는 응답에는 판단 이유가 필요합니다.")
        if self.response_type == "unclear" and not self.review_reason.strip():
            raise ValueError("해석 불가에는 검토 이유가 필요합니다.")
        return self


class CodingBatch(Contract):
    results: list[CodingResult]


def normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def validate_codes(codes: list[Code]) -> None:
    ids, names, definitions = set(), set(), set()
    for code in codes:
        if not all(v.strip() for v in (code.id, code.category, code.name, code.definition)):
            raise ValueError("코드 ID·대분류·세부분류·정의를 모두 입력해주세요.")
        pair = (normalized(code.category), normalized(code.name))
        definition = normalized(code.definition)
        if code.id in ids or pair in names or definition in definitions:
            raise ValueError("중복된 코드 ID·분류명·정의가 있습니다. 같은 의미의 코드를 확인해주세요.")
        ids.add(code.id)
        names.add(pair)
        definitions.add(definition)


def validate_ai_code(item: DraftCode) -> None:
    """AI 초안의 명시적인 감성 분류를 차단한다. 사용자 직접 편집에는 강제하지 않는다."""
    polarity_label = r"(?:^|[\s(/·:_-])(?:긍정|부정|중립)(?:적(?:인)?)?(?:\s*(?:평가|의견|반응))?\s*\)?$"
    if any(re.search(polarity_label, value.strip()) for value in (item.category, item.name)):
        raise ValueError("분류명에 감성을 붙이지 마세요. 같은 주제의 긍정·부정은 하나의 주제명과 일반 판정 기준으로 통합해주세요.")
    polarized = set(re.findall(r"(긍정|부정)(?:적(?:인)?)?\s*(?:평가|의견|반응)", item.definition))
    if len(polarized) == 1:
        raise ValueError("분류 정의를 한쪽 감성의 사례 요약으로 제한하지 마세요. 긍정·부정 모두에 적용할 일반적인 포함 범위를 작성해주세요.")


def ai_name_topics(name: str) -> set[str]:
    """복합 이름의 명시적인 공통 주제를 찾는다. 일반적인 의미 판정은 AI 지침에서 수행한다."""
    name = re.sub(r"(?<![a-z])a\s*/\s*s(?![a-z])", "as", normalized(name))
    parts = re.split(r"\s+및\s+|[/·,]|\s+[와과]\s+", name)
    aliases = {"휴대편의성": "휴대성", "휴대용이성": "휴대성"}
    return {aliases.get(part.replace(" ", ""), part.replace(" ", "")) for part in parts if part.strip()}


def validate_ai_topic_overlap(proposed, existing=()) -> None:
    # 기존 사용자 기준끼리의 중복은 여기서 다시 거절하지 않는다.
    seen = [(code, ai_name_topics(code.name)) for code in existing]
    existing_count = len(seen)
    for code in proposed:
        topics = ai_name_topics(code.name)
        for index, (other, other_topics) in enumerate(seen):
            if normalized(code.category) != normalized(other.category):
                continue
            # 보완의 완전 중복 제안은 기존 중복 제거 경로에서 처리한다.
            if index < existing_count and normalized(code.name) == normalized(other.name):
                continue
            overlap = topics & other_topics
            if overlap:
                common = ", ".join(sorted(overlap))
                raise ValueError(
                    f"세부분류 '{other.name}'와 '{code.name}'에 같은 주제({common})가 중복됩니다. "
                    "같은 뜻은 하나의 세부분류로 통일하고, 복합 분류의 공통 주제와 나머지 평가 축은 "
                    "근거가 있는 독립된 세부분류로 나눠주세요. 기존 확정 기준이 있으면 임의 변경하지 마세요.")
        seen.append((code, topics))


def materialize_codes(draft: CodebookDraft, records: list[dict], existing: list[Code] | None = None) -> list[Code]:
    originals = {row["id"]: row["text"] for row in records}
    codes = list(existing or [])
    for item in draft.codes:
        validate_ai_code(item)
        for source in item.evidence:
            if source.voc_id not in originals or source.quote not in originals[source.voc_id]:
                raise ValueError(f"분류 기준표 근거가 원문과 일치하지 않습니다: {source.voc_id}. 이 ID의 원문을 그대로 인용해주세요.")
        codes.append(Code(id="C" + uuid4().hex[:12], **item.model_dump()))
    validate_codes(codes)
    validate_ai_topic_overlap(draft.codes, existing or [])
    return codes


def validate_result(result: CodingResult, record: dict, codes: list[Code], context: str) -> None:
    if result.voc_id != record["id"]:
        raise ValueError("분류 결과의 VOC ID가 원문과 다릅니다.")
    allowed = {code.id for code in codes}
    for issue in result.issues:
        if issue.code_id is not None and issue.code_id not in allowed:
            raise ValueError("분류 기준표에 없는 코드 ID가 반환됐습니다.")
        if issue.code_id is None and not issue.missing_code.strip():
            raise ValueError("맞는 코드가 없으면 누락된 의미를 설명해야 합니다.")
        if issue.code_id is not None and issue.missing_code:
            raise ValueError("기존 코드 분류와 누락 코드 제안이 충돌합니다.")
        if not issue.evidence_text.strip() or issue.evidence_text not in record["text"]:
            raise ValueError("의견의 근거가 VOC 원문에 없습니다.")
        if issue.context_evidence_text and issue.context_evidence_text not in context:
            raise ValueError("배경 근거가 실행에 사용한 분석 배경에 없습니다.")
        if issue.subject_label is None:
            if issue.subject_evidence_text or issue.subject_evidence_source:
                raise ValueError("대상 미상에는 대상 근거를 붙일 수 없습니다.")
        else:
            source = record["text"] if issue.subject_evidence_source == "original" else context
            if not issue.subject_label.strip() or not issue.subject_evidence_source or not issue.subject_evidence_text.strip() or issue.subject_evidence_text not in source:
                raise ValueError("대상·역할의 근거를 확인할 수 없습니다.")
            if issue.subject_evidence_source == "context" and not issue.context_evidence_text:
                raise ValueError("배경으로 대상을 해석했다면 배경 근거가 필요합니다.")


def overall_sentiment(result: CodingResult) -> str:
    sentiments = {issue.sentiment for issue in result.issues}
    if {"긍정", "부정"} <= sentiments:
        return "혼합"
    for value in ("긍정", "부정", "중립", "판단 불가"):
        if value in sentiments:
            return value
    return "해당 없음"


def validate_specific_topic(result: CodingResult, codes):
    """명시적 가격 근거가 일반 만족도로 우회하는 알려진 오류를 차단한다."""
    general = {c.id for c in codes if c.name.strip() in {
        "전반적 만족도", "전반적 평가", "종합 만족도", "일반 평가"}}
    price = r"가격|가성비|할인|정가|세일|금액|비싸|비싼|저렴|싸게|싼게|만원"
    for issue in result.issues:
        if issue.code_id in general and re.search(price, issue.evidence_text):
            raise ValueError("가격·할인·지불 가치가 명시된 근거는 전반적 만족도로 배정하지 마세요. 가격 및 가성비 코드로 분류하거나 해당 코드가 없으면 누락 주제로 제안하세요.")
