"""한 실행의 결과만 표로 만든다. 의견 행과 원본 VOC 수를 구분한다."""

import pandas as pd

from src.models import CodingResult, overall_sentiment

STATUS_LABELS = {"queued": "대기", "running": "진행 중 / 중단 복구 가능", "completed": "분류 완료",
                 "failed": "실패·미처리 있음", "needs_review": "검토 필요"}


def result_tables(store, run_id):
    run = store.run(run_id)
    dataset = store.dataset(run["dataset_id"])
    book = store.codebook(run["codebook_id"])
    codes = {code.id: code for code in book["codes"]}
    saved = {row["voc_id"]: row for row in store.effective_results(run_id)}
    originals, issues = [], []
    for record in dataset["records"]:
        saved_result = saved.get(record["id"])
        base = {"VOC ID": record["id"], "입력 행": record["row"], "VOC 원문": record["text"],
                "분류 출처": saved_result.get("source", "AI 분류") if saved_result else "미처리"}
        if not saved_result or saved_result["status"] != "success":
            state = "수정값 승계 검토" if saved_result and saved_result["status"] == "review" else "실패" if saved_result else "미처리"
            originals.append({**base, "응답 상태": state,
                              "전체 감성": "해당 없음", "설명": saved_result["error"] if saved_result else ""})
            continue
        result = CodingResult.model_validate(saved_result["result"])
        response_label = {"opinions": "의견 있음", "no_content": "없음·무응답·모름", "unclear": "해석 검토 필요"}[result.response_type]
        if any(issue.code_id is None for issue in result.issues):
            response_label = "맞는 코드 없음·검토 필요"
        originals.append({**base, "응답 상태": response_label, "전체 감성": overall_sentiment(result),
                          "설명": result.no_content_reason or result.review_reason or result.summary})
        for issue in result.issues:
            code = codes.get(issue.code_id)
            issues.append({**base, "코드 ID": issue.code_id or "", "대분류": code.category if code else "검토 필요",
                "세부분류": code.name if code else "맞는 코드 없음", "감성": issue.sentiment,
                "원문 근거": issue.evidence_text, "배경 근거": issue.context_evidence_text,
                "대상·역할": issue.subject_label or "미상", "대상 근거": issue.subject_evidence_text,
                "대상 근거 출처": {"original": "원문", "context": "배경", None: ""}[issue.subject_evidence_source],
                "검토 내용": issue.missing_code, "핵심 내용": result.summary, "키워드": ", ".join(result.keywords)})
    return pd.DataFrame(originals), pd.DataFrame(issues)


def csv_download(frame):
    # Excel에서 원문이 수식으로 실행되지 않도록 내보내는 사본에만 방어 문자를 붙인다.
    def safe(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value
    return frame.map(safe).to_csv(index=False).encode("utf-8-sig")
