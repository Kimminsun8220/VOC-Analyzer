"""실행: .venv/Scripts/python.exe -m streamlit run app.py"""

from hashlib import sha256
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st

from src.ai import DEFAULT_MODEL, GeminiAI, PROMPT_VERSION
from src.config import load_gemini_key
from src.codebook_ui import show_codebook_changes
from src.corrections_ui import show_corrections
from src.gemini_connection import check_gemini_connection
from src.grouping_ui import show_grouped_results
from src.ingestion import excel_sheet_names, prepare_preview, read_csv, read_excel, read_pasted_text
from src.results import STATUS_LABELS, csv_download, result_tables
from src.storage import Store
from src.workflow import confirm_codebook, execute_run, generate_codebook

SAMPLE_PATH = Path(__file__).parent / "data" / "samples" / "voc_sample.csv"
CODE_COLUMNS = {"id": "코드 ID", "category": "대분류", "name": "세부분류", "definition": "분류 기준"}


def show_gemini_settings():
    st.subheader("Gemini 설정")
    st.text_input("분석 모델", value=DEFAULT_MODEL, key="model")
    st.caption("키는 프로젝트의 .env에서 읽습니다. 연결 확인은 모델 목록만 요청합니다.")
    if st.button("설정 확인 / 연결 확인", key="check_gemini", width="stretch"):
        try:
            key = load_gemini_key()
            if not key:
                st.warning(".env의 GEMINI_API_KEY= 뒤에 키를 넣고 저장해주세요.")
            else:
                with st.spinner("Gemini 키를 확인하고 있습니다…"):
                    check_gemini_connection(key)
                st.success("키 인증과 모델 목록 조회에 성공했습니다.")
        except ValueError as exc:
            st.error(str(exc))


def with_ai(action, model=None):
    ai = GeminiAI(load_gemini_key(), model or st.session_state.model)
    try:
        return action(ai)
    finally:
        ai.close()


def code_frame(codes):
    return pd.DataFrame([{key: getattr(code, key) for key in CODE_COLUMNS} for code in codes], columns=list(CODE_COLUMNS))


def input_screen(store):
    st.caption("STEP 01 / 데이터 준비")
    st.header("분석할 고객 의견을 넣어주세요")
    name = st.text_input("분석 이름", value="새 VOC 분석", max_chars=100)
    mode = st.radio("입력 방법", ["연습용 샘플", "직접 붙여넣기", "파일 업로드"], horizontal=True, key="input_mode")
    frame = None
    if mode == "연습용 샘플":
        st.info("상품·배송·가격·고객응대에 관한 가상 VOC 20건입니다.")
        frame = read_csv(SAMPLE_PATH.read_bytes())
    elif mode == "직접 붙여넣기":
        text = st.text_area("고객 의견", height=180, key="voc_text", help="빈 줄을 제외한 한 줄이 VOC 한 건입니다. 본문 속 줄바꿈은 파일 입력을 이용해주세요.")
        frame = read_pasted_text(text)
    else:
        uploaded = st.file_uploader("CSV 또는 Excel 파일", type=["csv", "xlsx"], help="10MB 이하 · 최대 500행 · 본문당 5,000자")
        if uploaded:
            content = uploaded.getvalue()
            if uploaded.name.lower().endswith(".xlsx"):
                sheet = st.selectbox("분석할 시트", excel_sheet_names(content))
                frame = read_excel(content, sheet)
            else:
                frame = read_csv(content)
    if frame is None:
        st.info("파일을 선택하면 본문 열과 입력 내용을 확인할 수 있습니다.")
        return
    if not len(frame.columns):
        st.warning("본문과 열 제목이 있는 파일을 선택해주세요.")
        return
    columns = list(frame.columns)
    text_column = st.selectbox("VOC 본문이 들어 있는 열", columns, index=columns.index("VOC") if "VOC" in columns else 0)
    context = st.text_area("분석 배경 (선택)", key="input_context", max_chars=10000,
        help="현재 자료 전체에 공통으로 적용되는 상품 설명·설문 문항·약어를 적어주세요. 비워도 분석할 수 있습니다.",
        placeholder="예: SA는 서비스 어드바이저를 뜻합니다.")
    with st.expander("입력 데이터 미리보기", expanded=True):
        st.dataframe(frame.head(20), hide_index=True, width="stretch")
        st.caption("최대 20행 미리보기. 다른 열은 함께 저장하며, 선택한 본문과 배경만 AI에 전달합니다.")
    fingerprint = sha256((frame.to_json(force_ascii=False) + str(text_column) + context + name + mode).encode()).hexdigest()
    if st.button("입력 데이터 확인", key="preview_button", type="primary", width="stretch"):
        preview = prepare_preview(frame, text_column)
        st.session_state.preview = (fingerprint, preview)
    saved = st.session_state.get("preview")
    if not saved or saved[0] != fingerprint:
        return
    preview = saved[1]
    a, b, c = st.columns(3)
    a.metric("원본 응답", f"{preview.input_count}건")
    b.metric("본문 있는 응답", f"{preview.input_count - preview.blank_count}건")
    c.metric("빈 본문·무응답", f"{preview.blank_count}건")
    if preview.duplicate_count:
        st.info(f"같은 본문이 반복된 추가 행 {preview.duplicate_count}건을 각각 보존했습니다.")
    st.dataframe(preview.records, hide_index=True, width="stretch")
    st.caption("파일의 빈 응답 행은 무응답으로 보존합니다. CSV 형식상의 빈 줄과 붙여넣기의 빈 줄은 세지 않습니다.")
    if st.button("입력 저장 → 코드북으로", key="save_input", type="primary", width="stretch"):
        identifier = store.save_dataset(name, preview, frame, text_column, mode, context)
        st.session_state.pending_dataset = identifier
        st.session_state.page = "2. 코드북"
        st.rerun()


def codebook_screen(store, dataset):
    st.caption("STEP 02 / 분류 기준")
    st.header("코드북을 만들고 검토하세요")
    st.write(f"**{dataset['name']}** · 원본 응답 {len(dataset['records'])}건")
    books = store.list_codebooks(dataset["id"])
    book = None
    if books:
        labels = {b["id"]: f"v{b['version']} · {'초안' if b['status'] == 'draft' else '확정'}" for b in books}
        choice_key = f"book_choice_{dataset['id']}"
        pending = st.session_state.pop("confirmed_book", None)
        if pending in labels:
            st.session_state[choice_key] = pending
        selected = st.selectbox("코드북 버전", list(labels), format_func=labels.get, key=choice_key)
        book = store.codebook(selected)
    context = st.text_area("이번 코드북의 분석 배경 (선택)", value=book["context"] if book else dataset["context"],
        max_chars=10000, key=f"context_{book['id'] if book else dataset['id']}")
    st.caption("AI 버튼을 누르면 본문과 배경을 Gemini에 전송합니다. 초안은 최대 100건·60,000자의 표본으로 만들며 원문을 자르지 않습니다.")
    if st.button("AI 코드북 초안 만들기", key="generate_codebook", type="primary"):
        with st.spinner("VOC 표본에서 분류 기준을 만들고 있습니다…"):
            identifier = with_ai(lambda ai: generate_codebook(store, dataset["id"], ai, context.strip()))
        st.session_state.confirmed_book = identifier
        st.rerun()
    if book is None:
        st.info("초안을 만든 뒤 이름과 정의를 검토하고 전체 분류를 시작할 수 있습니다.")
        return
    st.caption(f"초안 생성 표본 {len(book['sample_ids'])}건 · 코드 {len(book['codes'])}개 · 모델 {book['model']}")
    notice = st.session_state.pop("book_notice", None)
    if notice:
        st.success(notice)
    if context.strip() != book["context"]:
        st.warning("입력한 배경이 이 코드북의 배경과 다릅니다. 바뀐 배경으로 초안을 새로 만들어주세요. 기존 버전은 유지됩니다.")
    with st.expander("이 버전의 배경·생성 근거·변경 이력"):
        st.text(book["context"] or "배경 없음")
        st.json({"표본 VOC ID": book["sample_ids"], "코드별 근거": [c.model_dump() for c in book["codes"]], "변경 이력": book["changes"]})
    if book["status"] == "draft":
        st.subheader("초안 편집")
        st.markdown(
            "**내용 수정:** 대분류·세부분류·분류 기준의 셀을 **더블클릭**한 뒤 입력하고 **Enter**를 누르세요.\n\n"
            "**행 삭제:** 행 맨 왼쪽에 마우스를 올려 **체크박스를 선택**한 다음, "
            "표 오른쪽 위의 **휴지통 버튼**을 누르세요. 표에 마우스를 올리면 도구 버튼이 나타납니다.\n\n"
            "**행 추가:** 표 오른쪽 위 **＋ 버튼** 또는 마지막 빈 행을 사용하세요."
        )
        edited = st.data_editor(code_frame(book["codes"]), num_rows="dynamic", disabled=["id"],
            column_config={"id": None, **{key: st.column_config.TextColumn(label, required=True)
                for key, label in CODE_COLUMNS.items() if key != "id"}},
            hide_index=True, width="stretch", key=f"editor_{selected}")
        st.caption("편집한 내용은 아래 ‘코드북 확정’을 눌러야 저장됩니다. 코드 ID는 자동으로 유지됩니다.")
        if st.button("코드북 확정", key="confirm_codebook", type="primary", disabled=context.strip() != book["context"]):
            identifier = confirm_codebook(store, selected, edited.where(pd.notna(edited), None).to_dict("records"))
            st.session_state.confirmed_book = identifier
            st.rerun()
    else:
        st.dataframe(code_frame(book["codes"]).rename(columns=CODE_COLUMNS), hide_index=True, width="stretch")
        st.success("확정된 기준입니다. 새 의미가 발견되면 코드북 보완과 전체 재평가를 자동 진행합니다.")
        show_codebook_changes(store, book, with_ai)
        prior = [run for run in store.list_runs(dataset["id"]) if run["status"] == "completed"]
        prior_labels = {run["id"]: f"{run['id'][:6]} · 결과 개정 {run['result_revision']} · {run['created_at'][:19]}" for run in prior}
        parent_id = None
        if prior:
            parent_id = st.selectbox("수정값을 가져올 이전 분석", [*prior_labels, None],
                format_func=lambda identifier: prior_labels.get(identifier, "가져오지 않음 · 독립된 새 분석"), key=f"parent_{book['id']}")
            st.caption("선택한 실행의 시작 시점 수정값만 이어받습니다. 전체 원문을 다시 분류하며 대응이 불명확한 수정은 검토합니다.")
        if st.button("이 코드북으로 전체 분류", key="start_classification", type="primary", disabled=context.strip() != book["context"]):
            def start(ai):
                identifier = store.create_run(dataset["id"], selected, ai.model, PROMPT_VERSION, parent_run_id=parent_id)
                st.session_state.run_id = identifier
                run_with_progress(store, identifier, ai)
            with_ai(start)
            st.session_state.page = "3. 분류 결과"
            st.rerun()


def run_with_progress(store, identifier, ai):
    bar = st.progress(0, text="전체 분류를 준비하고 있습니다…")
    execute_run(store, identifier, ai, lambda count, total, message: bar.progress(count / max(total, 1), text=f"{message} · {count}/{total}건"))


def results_screen(store, dataset):
    st.caption("STEP 03 / 시각화와 분류 결과")
    st.header("분류 결과를 살펴보고 원문을 확인하세요")
    runs = store.list_runs(dataset["id"])
    if not runs:
        st.info("코드북을 확정하고 전체 분류를 시작해주세요.")
        return
    labels = {run["id"]: f"{datetime.fromisoformat(run['created_at']).astimezone(timezone(timedelta(hours=9))):%Y-%m-%d %H:%M:%S} KST · {STATUS_LABELS[run['status']]} · {run['id'][:6]}" for run in runs}
    key = f"result_choice_{dataset['id']}"
    pending = st.session_state.pop("run_id", None)
    if pending in labels:
        st.session_state[key] = pending
    if key not in st.session_state:
        st.session_state[key] = next((item["id"] for item in runs if item["status"] == "completed"), runs[0]["id"])
    selected = st.selectbox("저장된 분석 실행", list(labels), format_func=labels.get, key=key)
    run = store.run(selected)
    book = store.codebook(run["codebook_id"])
    originals, issues = result_tables(store, selected)
    st.caption(f"{dataset['name']} · 코드북 v{book['version']} · {run['model']} · 자동 보완 {run['round']}회")
    st.caption(f"결과 개정 {run['result_revision']}" + (f" · 승계 기준 {run['parent_run_id'][:6]} / 개정 {run['parent_result_revision']}" if run["parent_run_id"] else ""))
    correction_notice = st.session_state.pop("correction_notice", None)
    if correction_notice:
        st.success(correction_notice)
    if run["status"] == "completed":
        st.success("모든 응답의 분류를 저장했습니다. 아래에서 원문과 의견별 근거를 확인하세요.")
    else:
        st.warning(f"{STATUS_LABELS[run['status']]} · {run['error'] or '저장된 지점부터 이어서 처리할 수 있습니다.'}")
        if st.button("보완 이어서 실행 (최대 2회 추가)" if run["status"] == "needs_review" else "실패·미처리 이어서 실행", key="resume_run"):
            def resume(ai):
                if run["status"] == "needs_review":
                    store.extend_limit(selected)
                run_with_progress(store, selected, ai)
            with_ai(resume, run["model"])
            st.rerun()
    a, b, c, d = st.columns(4)
    a.metric("원본 응답", f"{len(originals)}건")
    b.metric("의견 있는 응답", f"{originals['응답 상태'].isin(['의견 있음', '맞는 코드 없음·검토 필요']).sum()}건")
    c.metric("없음·무응답·모름", f"{originals['응답 상태'].eq('없음·무응답·모름').sum()}건")
    d.metric("실패·미처리·검토", f"{(~originals['응답 상태'].isin(['의견 있음', '없음·무응답·모름'])).sum()}건")
    with st.expander("실행에 사용한 배경과 최종 코드북"):
        st.text(run["context"] or "배경 없음")
        st.dataframe(code_frame(book["codes"]).rename(columns=CODE_COLUMNS), hide_index=True, width="stretch")
        st.json(book["changes"])
    tab_issues, tab_originals, tab_corrections = st.tabs(["시각화·묶어보기", "전체 응답·무응답", "개별 수정·승계 검토"],
        key=f"results_tabs_{selected}", on_change="rerun")
    with tab_issues:
        show_grouped_results(store, run, book, originals, issues)
    with tab_originals:
        state = st.selectbox("응답 상태 필터", ["전체"] + list(originals["응답 상태"].unique()))
        st.dataframe(originals if state == "전체" else originals[originals["응답 상태"] == state], hide_index=True, width="stretch")
        st.download_button("전체 응답 CSV", csv_download(originals), file_name="voc_responses.csv", mime="text/csv")
    with tab_corrections:
        show_corrections(store, run, book, dataset)
    st.download_button("실행 기록 JSON", json.dumps({"run": run, "records": dataset["records"],
        "codes": [c.model_dump() for c in book["codes"]], "results": store.results(selected),
        "current_results": store.effective_results(selected), "corrections": store.correction_history(selected)}, ensure_ascii=False, indent=2),
        file_name="voc_run.json", mime="application/json")


def main():
    st.set_page_config(page_title="AI VOC Analyzer", layout="wide")
    store = Store()
    if "page" in st.session_state:
        st.session_state.nav = st.session_state.pop("page")
    if "pending_dataset" in st.session_state:
        st.session_state.dataset_id = st.session_state.pop("pending_dataset")
    with st.sidebar:
        st.title("AI VOC Analyzer")
        st.caption("고객 의견에서 일관된 분류 기준을 만듭니다.")
        page = st.radio("분석 단계", ["1. 입력", "2. 코드북", "3. 분류 결과"], key="nav")
        datasets = store.list_datasets()
        if datasets:
            labels = {row["id"]: f"{row['name']} · {row['id'][:6]}" for row in datasets}
            st.selectbox("저장된 입력 자료", list(labels), format_func=labels.get, key="dataset_id")
        st.divider()
        show_gemini_settings()
        st.divider()
        st.caption("입력·코드북·분류는 이 컴퓨터에 저장됩니다. AI는 실행 버튼을 눌렀을 때만 호출합니다.")
    try:
        if page == "1. 입력":
            input_screen(store)
        elif not datasets:
            st.info("먼저 입력 자료를 저장해주세요.")
        else:
            dataset = store.dataset(st.session_state.dataset_id)
            if page == "2. 코드북":
                codebook_screen(store, dataset)
            else:
                results_screen(store, dataset)
    except (ValueError, sqlite3.Error, OSError) as exc:
        st.error(str(exc) if isinstance(exc, ValueError) else "로컬 파일·저장소를 읽거나 저장하지 못했습니다. 접근 권한과 여유 공간을 확인해주세요.")


if __name__ == "__main__":
    main()
