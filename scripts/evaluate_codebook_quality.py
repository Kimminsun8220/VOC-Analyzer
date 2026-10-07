"""Compare a saved run with a reviewed codebook in an isolated database.

No API calls without --execute. Source database, confirmed books and corrections
are read only. Private input, API ledger and reports stay under .local/.
"""
import argparse
from collections import Counter
import hashlib
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.ai import GeminiAI, PROMPT_VERSION
from src.config import load_gemini_key
from src.evaluation import RequestLedger
from src.models import validate_ai_topic_overlap
from src.storage import Store
from src.workflow import generate_codebook, confirm_codebook, execute_run, other_quality


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def metrics(codes, results, total):
    counts = Counter()
    for row in results:
        if row["result"]:
            counts.update({i["code_id"] for i in row["result"]["issues"] if i["code_id"]})
    collisions = []
    for i, first in enumerate(codes):
        for second in codes[i+1:]:
            try:
                validate_ai_topic_overlap([first, second])
            except ValueError as exc:
                collisions.append(str(exc))
    return {"categories": len({c.category for c in codes}), "codes": len(codes),
            "explicit_name_collisions": sorted(set(collisions)),
            "exclusion_definitions": sum("제외" in c.definition for c in codes),
            "zero_response_codes": [c.name for c in codes if not counts[c.id]],
            "single_response_codes": [c.name for c in codes if counts[c.id] == 1],
            "other": other_quality(results, codes, total)[1],
            "failed_responses": sum(r["status"] == "failed" for r in results),
            "unassigned_responses": sum(bool(r["result"]) and any(i["code_id"] is None for i in r["result"]["issues"]) for r in results),
            "code_response_counts": {f"[{c.category}] {c.name}": counts[c.id] for c in codes}}


def render_report(out, before, report):
    def code_table(codes):
        rows = "".join("<tr>" + "".join(f"<td>{escape(c[key])}</td>" for key in ("category", "name", "definition")) + "</tr>"
                       for c in sorted(codes, key=lambda c: (c["category"], c["name"])))
        return "<table><thead><tr><th>대분류</th><th>세부분류</th><th>판정 기준</th></tr></thead><tbody>" + rows + "</tbody></table>"
    metrics_rows = []
    for label, key in [("대분류", "categories"), ("세부분류", "codes"), ("제외 기준이 있는 정의", "exclusion_definitions"),
                       ("실패 응답", "failed_responses"), ("맞는 코드가 없는 응답", "unassigned_responses")]:
        metrics_rows.append(f"<tr><td>{label}</td><td>{report['before'][key]}</td><td>{report['after'][key]}</td></tr>")
    metrics_rows.append(f"<tr><td>기타 비중</td><td>{report['before']['other']['percent']:.1f}%</td><td>{report['after']['other']['percent']:.1f}%</td></tr>")
    summary = "<table><tr><th>항목</th><th>기존 저장 결과</th><th>새 실험 결과</th></tr>" + "".join(metrics_rows) + "</table>"
    singleton = escape(", ".join(report["after"]["single_response_codes"]) or "없음")
    body = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>코드북 생성 원칙 적용 비교</title><style>
    body{{font-family:Arial,'Malgun Gothic',sans-serif;color:#183044;max-width:1160px;margin:36px auto;padding:0 24px;line-height:1.6}}
    h1{{font-size:26px}}h2{{font-size:20px;margin-top:32px}}p,li{{font-size:15px}}.note{{background:#f1f5f8;padding:16px;border-radius:8px}}
    table{{border-collapse:collapse;width:100%;font-size:14px;margin:16px 0}}th,td{{border:1px solid #dce3e9;text-align:left;padding:10px;vertical-align:top}}th{{background:#f1f5f8}}td:first-child{{min-width:80px}}
    </style><h1>코드북 생성 원칙 적용 비교</h1>
    <p>2026-10-07 · 기존 샘플 {len(before['records'])}건 · 기존 v{report['source_version']} → {escape(report['prompt_version'])} 실험 · 처리 상태: {escape(report['status'])}</p>
    <p>새 초안 {report['initial_code_count']}개로 시작해 자동 보완 {report['rounds']}회 후 {report['after']['codes']}개가 되었습니다. 기존 확정 자료와 수정 이력은 변경하지 않았습니다.</p>
    {summary}
    <div class="note"><strong>해석 범위</strong><p>단일 샘플 비교이며 정답 라벨 기반 정확도 실험이 아닙니다. 기존 결과에는 사용자 수정이 포함돼 있습니다. 코드 수 감소나 기타 감소 자체가 개선을 의미하지 않습니다. 자동 검사는 의미 중복 전체를 보증하지 않습니다.</p>
    <p>한 응답만 배정된 코드: {singleton}. 신규 코드의 반복성·독립 분석 가치와 넓은 코드의 포함 범위를 추가 검토해야 합니다.</p></div>
    <h2>적용 흐름</h2><p>초안 생성 → 구조·원문 근거 자동 검사 → 별도 AI 8항목 품질 검토 및 코드별 처리 기록 → 자동 재검사 → 초안 저장 → 사용자 확정 → 복수 의견 분류 → 필요 시 추가 코드 검토</p>
    <p>기타 10~15%는 재검토 신호입니다. 비중만으로 완료를 막거나 신규 코드를 강제하지 않습니다.</p>
    <h2>기존 기준표</h2>{code_table(before['codes'])}
    <h2>새 실험 기준표</h2>{code_table(report['codes'])}
    </html>"""
    (out / "comparison.html").write_text(body, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--output", default=".local/codebook-quality-v8")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    out = (ROOT / args.output).resolve()
    if not out.is_relative_to(ROOT / ".local"):
        raise ValueError("Private experiment outputs must stay under .local")
    out.mkdir(parents=True, exist_ok=True)
    source = Store()
    run = source.run(args.source_run)
    book = source.codebook(run["codebook_id"])
    dataset = source.dataset(run["dataset_id"])
    snapshot = {"run": run, "codes": [c.model_dump() for c in book["codes"]],
                "records": dataset["records"], "context": run["context"],
                "results": source.effective_results(run["id"])}
    fingerprint = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    before_path = out / "before.json"
    if before_path.exists():
        assert json.loads(before_path.read_text(encoding="utf-8"))["fingerprint"] == fingerprint, "Source changed"
    else:
        save(before_path, {"fingerprint": fingerprint, **snapshot})
    if not args.execute:
        print("Prepared source snapshot; no API calls", flush=True)
        return
    import pandas as pd
    from src.ingestion import prepare_preview
    store = Store(out / "experiment.db")
    state_path = out / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    ledger = RequestLedger(out / "requests.db", limit=20)
    ai = GeminiAI(load_gemini_key(), run["model"])
    ai.client.models.generate_content = ledger.wrap(ai.client.models.generate_content, "reviewed_codebook_comparison")
    try:
        if "dataset" not in state:
            frame = pd.DataFrame({"VOC": [r["text"] for r in dataset["records"]]})
            state["dataset"] = store.save_dataset("코드북 품질 비교", prepare_preview(frame, "VOC"), frame, "VOC", "샘플 비교", run["context"])
            save(state_path, state)
        assert [r["id"] for r in store.dataset(state["dataset"])["records"]] == [r["id"] for r in dataset["records"]]
        if "draft" not in state:
            print("Generating and reviewing codebook", flush=True)
            state["draft"] = generate_codebook(store, state["dataset"], ai, run["context"])
            save(state_path, state)
        initial = store.codebook(state["draft"])
        save(out / "reviewed-draft.json", {"codes": [c.model_dump() for c in initial["codes"]], "reviews": initial["changes"]})
        if "run" not in state:
            confirmed = confirm_codebook(store, initial["id"], [c.model_dump() for c in initial["codes"]])
            state["run"] = store.create_run(state["dataset"], confirmed, ai.model, PROMPT_VERSION)
            save(state_path, state)
        def progress(done, total, phase):
            if "AI" in phase or done == total:
                print(f"{phase}: {done}/{total}", flush=True)
        execute_run(store, state["run"], ai, progress)
        current = store.run(state["run"])
        final = store.codebook(current["codebook_id"])
        results = store.effective_results(current["id"])
        report = {"source_run": args.source_run, "source_version": book["version"], "prompt_version": PROMPT_VERSION,
                  "status": current["status"], "error": current["error"], "rounds": current["round"],
                  "before": metrics(book["codes"], snapshot["results"], len(dataset["records"])),
                  "initial_code_count": len(initial["codes"]),
                  "after": metrics(final["codes"], results, len(dataset["records"])),
                  "codes": [c.model_dump() for c in final["codes"]], "results": results,
                  "generation_review": initial["changes"], "requests": ledger.summary(),
                  "limitations": "One sample, no human gold labels. Code counts and singleton counts do not by themselves prove semantic quality."}
        save(out / "comparison.json", report)
        render_report(out, snapshot, report)
        print(json.dumps({k: report[k] for k in ["status", "initial_code_count", "rounds", "before", "after", "requests"]}, ensure_ascii=False), flush=True)
    finally:
        ai.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
