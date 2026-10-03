"""실행: .venv/Scripts/python.exe -m streamlit run app.py"""

from collections import Counter
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
from src.codebook_table import table_has_changes
from src.corrections_ui import show_corrections
from src.dataset_ui import clear_deleted_dataset_state, show_dataset_management
from src.grouping_ui import show_grouped_results
from src.ingestion import excel_sheet_names, prepare_preview, read_csv, read_excel, read_pasted_text
from src.results import STATUS_LABELS, csv_download, result_tables
from src.storage import Store
from src.workflow import confirm_codebook, execute_run, generate_codebook

SAMPLE_PATH = Path(__file__).parent / "data" / "samples" / "voc_sample.csv"
CODE_COLUMNS = {"id": "코드 ID", "category": "대분류", "name": "세부분류", "definition": "분류 기준"}


def with_ai(action, model=None):
    ai = GeminiAI(load_gemini_key(), model or DEFAULT_MODEL)
    try:
        return action(ai)
    finally:
        ai.close()


def code_frame(codes):
    return pd.DataFrame([{key: getattr(code, key) for key in CODE_COLUMNS} for code in codes], columns=list(CODE_COLUMNS))


def input_screen(store):
    st.header("데이터 입력")
    name = st.text_input("분석 이름", value="새 VOC 분석", max_chars=100)
    mode = st.radio("입력 방법", ["연습용 샘플", "직접 붙여넣기", "파일 업로드"], horizontal=True, key="input_mode")
    frame = None
    if mode == "연습용 샘플":
        st.caption("가상 VOC 20건")
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
        st.caption(f"중복 본문 {preview.duplicate_count}건 포함")
    st.dataframe(preview.records, hide_index=True, width="stretch")
    if st.button("입력 저장 → 분류 기준표로", key="save_input", type="primary", width="stretch"):
        identifier = store.save_dataset(name, preview, frame, text_column, mode, context)
        st.session_state.pending_dataset = identifier
        st.session_state.page = "2. 분류 기준표"
        st.rerun()


def codebook_screen(store, dataset):
    st.header("분류 기준표")
    st.write(f"**{dataset['name']}** · 원본 응답 {len(dataset['records'])}건")
    books = store.list_codebooks(dataset["id"])
    book = None
    if books:
        labels = {b["id"]: f"v{b['version']} · {'초안' if b['status'] == 'draft' else '확정'}" for b in books}
        choice_key = f"book_choice_{dataset['id']}"
        pending = st.session_state.pop("confirmed_book", None)
        if pending in labels:
            st.session_state[choice_key] = pending
        selected = st.selectbox("분류 기준표 버전", list(labels), format_func=labels.get, key=choice_key)
        book = store.codebook(selected)
    context = st.text_area("이번 분류 기준표의 분석 배경 (선택)", value=book["context"] if book else dataset["context"],
        max_chars=10000, key=f"context_{book['id'] if book else dataset['id']}")
    if st.button("분류 기준표 초안 만들기", key="generate_codebook", type="primary"):
        with st.spinner("VOC 표본에서 분류 기준을 만들고 있습니다…"):
            identifier = with_ai(lambda ai: generate_codebook(store, dataset["id"], ai, context.strip()))
        st.session_state.confirmed_book = identifier
        st.rerun()
    if book is None:
        return
    editing = st.session_state.get(f"revise_{book['id']}_draft") if book["status"] == "confirmed" else None
    st.caption(f"분류 {len(editing['rows']) if editing else len(book['codes'])}개")
    notice = st.session_state.pop("book_notice", None)
    if notice:
        st.success(notice)
    if context.strip() != book["context"]:
        st.warning("배경이 변경되었습니다. 새 배경으로 초안을 만들어주세요.")
    if book["status"] == "draft":
        st.subheader("초안 편집")
        with st.expander("편집 방법"):
            st.markdown("- 수정: 셀 더블클릭 → 입력 → Enter\n- 삭제: 행 왼쪽 체크박스 → 휴지통\n- 추가: ＋ 또는 마지막 빈 행")
        edited = st.data_editor(code_frame(book["codes"]), num_rows="dynamic", disabled=["id"],
            column_config={"id": None, **{key: st.column_config.TextColumn(label, required=True)
                for key, label in CODE_COLUMNS.items() if key != "id"}},
            hide_index=True, width="stretch", key=f"editor_{selected}")
        if st.button("분류 기준표 확정", key="confirm_codebook", type="primary", disabled=context.strip() != book["context"]):
            identifier = confirm_codebook(store, selected, edited.where(pd.notna(edited), None).to_dict("records"))
            st.session_state.confirmed_book = identifier
            st.rerun()
    else:
        show_codebook_changes(store, book, with_ai)
        prior = [run for run in store.list_runs(dataset["id"]) if run["status"] == "completed"]
        prior_labels = {run["id"]: f"{datetime.fromisoformat(run['created_at']).astimezone(timezone(timedelta(hours=9))):%Y-%m-%d %H:%M:%S}" for run in prior}
        parent_id = None
        if prior:
            parent_id = st.selectbox("수정값을 가져올 이전 분석", [*prior_labels, None],
                format_func=lambda identifier: prior_labels.get(identifier, "가져오지 않음 · 독립된 새 분석"), key=f"parent_{book['id']}")
        classification_label = "변경된 기준으로 다시 분류" if parent_id else "고객 의견 자동 분류"
        editing = st.session_state.get(f"revise_{book['id']}_draft")
        unsaved = bool(editing and table_has_changes(book, editing))
        if unsaved:
            st.caption("편집 중입니다. 변경 내용을 저장해주세요.")
        if st.button(classification_label, key="start_classification", type="primary", disabled=context.strip() != book["context"] or unsaved):
            def start(ai):
                identifier = store.create_run(dataset["id"], selected, ai.model, PROMPT_VERSION, parent_run_id=parent_id)
                st.session_state.run_id = identifier
                run_with_progress(store, identifier, ai)
            with_ai(start)
            st.session_state.page = "3. 분류 결과"
            st.rerun()


def run_with_progress(store, identifier, ai):
    bar = st.progress(0, text="고객 의견 자동 분류를 준비하고 있습니다…")
    execute_run(store, identifier, ai, lambda count, total, message: bar.progress(count / max(total, 1), text=f"{message} · {count}/{total}건"))


def results_screen(store, dataset):
    st.header("분류 결과")
    runs = store.list_runs(dataset["id"])
    if not runs:
        st.info("분류 기준표를 확정하고 고객 의견 자동 분류를 시작해주세요.")
        return
    labels = {run["id"]: f"{datetime.fromisoformat(run['created_at']).astimezone(timezone(timedelta(hours=9))):%Y-%m-%d %H:%M:%S} KST · {STATUS_LABELS[run['status']]}" for run in runs}
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
    st.caption(f"{dataset['name']} · 분류 기준표 v{book['version']}")
    correction_notice = st.session_state.pop("correction_notice", None)
    if correction_notice:
        st.success(correction_notice)
    if run["status"] != "completed":
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
    with st.expander("분석 정보"):
        st.caption(f"모델 {run['model']} · 자동 보완 {run['round']}회 · 결과 개정 {run['result_revision']}")
        if run["parent_run_id"]:
            st.caption(f"이전 분석 {run['parent_run_id'][:6]} · 결과 개정 {run['parent_result_revision']}")
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
    with st.expander("분석 기록 내려받기"):
        st.download_button("실행 기록 JSON", json.dumps({"run": run, "records": dataset["records"],
            "codes": [c.model_dump() for c in book["codes"]], "results": store.results(selected),
            "current_results": store.effective_results(selected), "corrections": store.correction_history(selected)}, ensure_ascii=False, indent=2),
            file_name="voc_run.json", mime="application/json")


def main():
    st.set_option("client.toolbarMode", "minimal")
    st.set_page_config(page_title="AI VOC Analyzer", layout="wide")
    store = Store()
    clear_deleted_dataset_state()
    if "page" in st.session_state:
        st.session_state.nav = st.session_state.pop("page")
    if "pending_dataset" in st.session_state:
        st.session_state.dataset_id = st.session_state.pop("pending_dataset")
    with st.sidebar:
        st.title("AI VOC Analyzer")
        page = st.radio("분석 단계", ["1. 입력", "2. 분류 기준표", "3. 분류 결과"], key="nav")
        datasets = store.list_datasets()
        if datasets:
            names = Counter(row["name"] for row in datasets)
            labels = {row["id"]: row["name"] + (f" · {datetime.fromisoformat(row['created_at']).astimezone(timezone(timedelta(hours=9))):%Y-%m-%d %H:%M:%S}" if names[row["name"]] > 1 else "") for row in datasets}
            if st.session_state.get("dataset_id") not in labels:
                st.session_state.dataset_id = datasets[0]["id"]
            st.selectbox("저장된 입력 자료", list(labels), format_func=labels.get, key="dataset_id")
            show_dataset_management(store, st.session_state.dataset_id)
        else:
            st.session_state.pop("dataset_id", None)
            notice = st.session_state.pop("dataset_notice", None)
            if notice:
                st.success(notice)
            st.caption("저장된 입력 자료가 없습니다.")
    try:
        if page == "1. 입력":
            input_screen(store)
        elif not datasets:
            st.info("먼저 입력 자료를 저장해주세요.")
        else:
            dataset = store.dataset(st.session_state.dataset_id)
            if page == "2. 분류 기준표":
                codebook_screen(store, dataset)
            else:
                results_screen(store, dataset)
    except (ValueError, sqlite3.Error, OSError) as exc:
        st.error(str(exc) if isinstance(exc, ValueError) else "로컬 파일·저장소를 읽거나 저장하지 못했습니다. 접근 권한과 여유 공간을 확인해주세요.")


if __name__ == "__main__":
    main()
