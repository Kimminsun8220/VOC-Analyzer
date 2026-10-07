"""저장된 상태를 따라 분류 기준표 생성 → 전체 분류 → 누락 코드 보완을 진행한다."""

import random
from uuid import uuid4

from pydantic import ValidationError

from src.ai import AIError, PROMPT_VERSION
from src.corrections import apply_inheritance
from src.models import Code, CodingResult, materialize_codes, normalized, validate_codes, validate_result

BATCH_SIZE = 50
MAX_BATCH_CHARS = 20000
MAX_SAMPLE_CHARS = 60000


def ai_records(records):
    # 사람 코딩 등 비교용 부가 열은 AI 입력에서 분리한다.
    return [{"id": row["id"], "text": row["text"]} for row in records]


def sample_records(records):
    eligible = [row for row in records if row["text"].strip()]
    if len(eligible) > 100:
        eligible = random.Random(42).sample(eligible, 100)
    chosen, size = [], 0
    for row in eligible:
        if size + len(row["text"]) > MAX_SAMPLE_CHARS:
            continue
        chosen.append(row)
        size += len(row["text"])
    return ai_records(chosen)


def generate_codebook(store, dataset_id, ai, context):
    dataset = store.dataset(dataset_id)
    sample = sample_records(dataset["records"])
    codes, feedback = [], ""
    for attempt in range(3 if sample else 0):
        draft = ai.codebook(sample, context, feedback=feedback) if feedback else ai.codebook(sample, context)
        try:
            codes = materialize_codes(draft, sample)
            break
        except ValueError as exc:
            feedback = str(exc)
            if attempt == 2:
                raise ValueError("분류 기준표 근거·중복 검증에 실패했습니다. 초안을 다시 생성해주세요.") from None
    return store.save_codebook(dataset_id, codes, context, ai.model, [r["id"] for r in sample])


def confirm_codebook(store, draft_id, edited_rows):
    draft = store.codebook(draft_id)
    original = {code.id: code for code in draft["codes"]}
    codes = []
    for row in edited_rows:
        code_id = row.get("id")
        code_id = code_id if isinstance(code_id, str) and code_id else "C" + uuid4().hex[:12]
        if code_id not in original and row.get("id"):
            raise ValueError("기존 코드 ID는 변경할 수 없습니다.")
        source = original.get(code_id)
        try:
            code = Code(id=code_id, category=row.get("category"), name=row.get("name"), definition=row.get("definition"),
                reason="사용자 검토·확정", evidence=source.evidence if source else [])
        except ValidationError:
            raise ValueError("대분류·세부분류·정의를 모두 입력해주세요.") from None
        codes.append(code)
    validate_codes(codes)
    current = {code.id: code for code in codes}
    changed = [code.model_dump() for code in original.values() if code.id not in current or
               (code.definition, code.category) != (current[code.id].definition, current[code.id].category)]
    constraints = draft["constraints"] + changed
    return store.save_codebook(draft["dataset_id"], codes, draft["context"], draft["model"],
        draft["sample_ids"], "confirmed", draft_id, [{"kind": "user_confirmation", "changed_concepts": changed}], constraints)


def batches(records):
    batch, size = [], 0
    for row in records:
        if batch and (len(batch) >= BATCH_SIZE or size + len(row["text"]) > MAX_BATCH_CHARS):
            yield batch
            batch, size = [], 0
        batch.append(row)
        size += len(row["text"])
    if batch:
        yield batch


def execute_run(store, run_id, ai, progress=lambda *args: None):
    run = store.run(run_id)
    if run["status"] == "completed":
        return
    if run["model"] != ai.model or run["prompt_version"] != PROMPT_VERSION:
        raise ValueError("재시도는 처음 실행한 모델과 프롬프트 버전으로 진행해야 합니다.")
    store.claim(run_id)
    try:
        _execute(store, run_id, ai, progress)
    except AIError as exc:
        store.update_status(run_id, "failed", str(exc))
    except ValueError:
        store.update_status(run_id, "failed", "AI 결과 검증에 실패했습니다. 저장된 결과를 유지했습니다. 이어서 처리해주세요.")
    except Exception:
        store.update_status(run_id, "failed", "처리가 중단됐습니다. 저장된 결과를 유지했습니다. 이어서 처리해주세요.")
        raise


def _execute(store, run_id, ai, progress):
    dataset = store.dataset(store.run(run_id)["dataset_id"])
    records = dataset["records"]
    while True:
        run = store.run(run_id)
        book = store.codebook(run["codebook_id"])
        saved = {row["voc_id"]: row for row in store.effective_results(run_id)}
        pending = [row for row in records if row["id"] not in saved or saved[row["id"]]["status"] == "failed"
                   or (saved[row["id"]]["result"] and saved[row["id"]]["result"]["response_type"] == "unclear")]
        for row in list(pending):
            if not row["text"].strip():
                store.save_result(run_id, run["round"], row["id"], CodingResult(
                    voc_id=row["id"], response_type="no_content", no_content_reason="빈 본문(무응답)"))
        pending = [row for row in pending if row["text"].strip()]
        completed = len(records) - len(pending)
        progress(completed, len(records), f"분류 기준표 v{book['version']} · 고객 의견 자동 분류")
        for batch in batches(pending):
            feedback = {row["id"]: saved[row["id"]]["error"] for row in batch
                        if row["id"] in saved and saved[row["id"]]["error"]}
            if feedback:
                response = ai.classify(ai_records(batch), book["codes"], run["context"], feedback=feedback)
            else:
                response = ai.classify(ai_records(batch), book["codes"], run["context"])
            expected = {row["id"] for row in batch}
            if any(result.voc_id not in expected for result in response.results):
                raise ValueError("요청하지 않은 VOC ID가 응답에 포함됐습니다.")
            for record in batch:
                matches = [result for result in response.results if result.voc_id == record["id"]]
                try:
                    if len(matches) != 1:
                        raise ValueError("VOC 결과가 누락되거나 중복됐습니다.")
                    validate_result(matches[0], record, book["codes"], run["context"])
                    store.save_result(run_id, run["round"], record["id"], matches[0])
                except ValueError as exc:
                    store.save_result(run_id, run["round"], record["id"], error=str(exc))
                completed += 1
            progress(completed, len(records), f"분류 기준표 v{book['version']} · 고객 의견 자동 분류")
        apply_inheritance(store, run_id)
        results = store.effective_results(run_id)
        if any(row["status"] == "failed" for row in results) or len(results) != len(records):
            store.update_status(run_id, "failed", "검증에 실패한 응답이 있습니다. 실패·미처리 건만 이어서 처리할 수 있습니다.")
            return
        candidates = [{"voc_id": row["voc_id"], **issue} for row in results
                      for issue in (row["result"]["issues"] if row["result"] else []) if issue["code_id"] is None]
        unclear = any(row["status"] == "review" or row["result"]["response_type"] == "unclear" for row in results)
        if not candidates:
            store.update_status(run_id, "needs_review" if unclear else "completed",
                "미해결 분류·수정값 승계 검토가 남아 있습니다. 개별 수정·승계 검토 탭에서 확인해주세요." if unclear else "")
            return
        if run["round"] >= run["round_limit"]:
            store.update_status(run_id, "needs_review", "자동 보완 상한에 도달했습니다. 맞는 코드가 없는 의견을 검토하거나 보완을 이어서 실행해주세요.")
            return
        progress(len(records), len(records), "누락 의미 비교 · 분류 기준표 자동 보완")
        sources = ai_records([row for row in records if row["id"] in {c["voc_id"] for c in candidates}])
        # 후보도 소규모로 묶고 각 호출에 직전 추가까지 전달해 중복 생성을 줄인다.
        expanded = list(book["codes"])
        for source_batch in batches(sources):
            store.refresh_lease(run_id)
            ids = {row["id"] for row in source_batch}
            draft = ai.supplement(source_batch, expanded, run["context"],
                [candidate for candidate in candidates if candidate["voc_id"] in ids], book["constraints"])
            proposed = materialize_codes(draft, source_batch)
            protected = {normalized(code["definition"]) for code in book["constraints"]}
            names = {(normalized(c.category), normalized(c.name)) for c in expanded}
            definitions = {normalized(c.definition) for c in expanded} | protected
            for code in proposed:
                if normalized(code.definition) in definitions or (normalized(code.category), normalized(code.name)) in names:
                    continue
                expanded.append(code)
                names.add((normalized(code.category), normalized(code.name)))
                definitions.add(normalized(code.definition))
        additions = expanded[len(book["codes"]):]
        if not additions:
            store.update_status(run_id, "needs_review", "중복·사용자 변경 의도를 고려했을 때 추가할 코드를 확정하지 못했습니다. 미해결 의견을 확인해주세요.")
            return
        store.add_round(run_id, expanded, [{"kind": "ai_addition", "code": c.model_dump()} for c in additions])
