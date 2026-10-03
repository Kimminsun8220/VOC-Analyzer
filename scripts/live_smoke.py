"""수동 실행 전용. 가상 샘플만 Gemini에 보내 실제 연결 흐름을 점검한다.

실행: .venv/Scripts/python.exe -m scripts.live_smoke
"""

import json
from pathlib import Path

import pandas as pd

from src.ai import GeminiAI, PROMPT_VERSION
from src.config import load_gemini_key
from src.ingestion import prepare_preview, read_csv
from src.results import result_tables
from src.storage import Store
from src.workflow import confirm_codebook, execute_run, generate_codebook


def main():
    root = Path(__file__).resolve().parents[1]
    sample = read_csv((root / "data/samples/voc_sample.csv").read_bytes())
    edges = pd.DataFrame({"VOC": [
        "배송은 빠른데 상담원이 불친절해요.", "직원이 불친절했어요.", "SA가 불친절했어요.",
        "없음", "모름", "", "하자가 없음", "사용법을 모르겠어요.",
        "제품은 파란색입니다.", "포장은 예쁜데 충전기가 빠져 있어요.",
    ]})
    frame = pd.concat([sample[["VOC"]], edges], ignore_index=True)
    store = Store(root / ".local/live_smoke.db")
    context = "SA는 서비스 어드바이저를 뜻합니다."
    dataset_id = store.save_dataset("가상 VOC 실제 연결 점검", prepare_preview(frame, "VOC"), frame, "VOC", "가상 샘플", context)
    ai = GeminiAI(load_gemini_key())
    try:
        draft_id = generate_codebook(store, dataset_id, ai, context)
        book = store.codebook(draft_id)
        confirmed = confirm_codebook(store, draft_id, [{key: getattr(c, key) for key in ("id", "category", "name", "definition")} for c in book["codes"]])
        run_id = store.create_run(dataset_id, confirmed, ai.model, PROMPT_VERSION)
        execute_run(store, run_id, ai, lambda count, total, message: print(f"{message}: {count}/{total}", flush=True))
        originals, issues = result_tables(store, run_id)
        run = store.run(run_id)
        report = {"run": run, "originals": originals.to_dict("records"), "issues": issues.to_dict("records"),
                  "codes": [c.model_dump() for c in store.codebook(run["codebook_id"])["codes"]]}
        (root / ".local/live_smoke_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": run["status"], "error": run["error"], "responses": len(originals), "issues": len(issues),
                          "no_content": int(originals["응답 상태"].eq("없음·무응답·모름").sum())}, ensure_ascii=False), flush=True)
        if run["status"] != "completed":
            raise SystemExit(1)
    finally:
        ai.close()


if __name__ == "__main__":
    main()
