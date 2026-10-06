"""실행: .venv/Scripts/python.exe -m streamlit run app.py"""

from collections import Counter
from hashlib import sha256
from datetime import datetime, timedelta, timezone
import sqlite3

import pandas as pd
import streamlit as st

from src.ai import DEFAULT_MODEL, GeminiAI, PROMPT_VERSION
from src.config import load_gemini_key
from src.codebook_ui import show_codebook_changes
from src.codebook_picker_ui import show_codebook_picker
from src.codebook_table import table_has_changes
from src.corrections import inheritance_source
from src.dataset_ui import clear_deleted_dataset_state, show_dataset_picker
from src.grouping_ui import show_grouped_results
from src.ingestion import excel_sheet_names, prepare_preview, read_csv, read_excel, read_pasted_text
from src.results import STATUS_LABELS, result_tables
from src.storage import Store
from src.workspace_ui import apply_workspace_theme
from src.workflow import execute_run, generate_codebook

CODE_COLUMNS = {"id": "코드 ID", "category": "대분류", "name": "세부분류", "definition": "분류 기준"}


def with_ai(action, model=None):
    ai = GeminiAI(load_gemini_key(), model or DEFAULT_MODEL)
    try:
        return action(ai)
    finally:
        ai.close()


def code_frame(codes):
    return pd.DataFrame([{key: getattr(code, key) for key in CODE_COLUMNS} for code in codes], columns=list(CODE_COLUMNS))


def unique_analysis_labels(labels):
    counts = Counter(labels.values())
    return {identifier: label + (f" · 분석 {len(labels) - index}" if counts[label] > 1 else "")
            for index, (identifier, label) in enumerate(labels.items())}


def input_screen(store):
    with st.container(key="input_header"):
        st.header("데이터 입력", anchor=False)
    with st.container(key="input_identity", width=500):
        name = st.text_input("분석 이름", value="새 VOC 분석", max_chars=100)
    input_modes = ["직접 붙여넣기", "파일 업로드"]
    if st.session_state.get("input_mode") not in (None, *input_modes):
        st.session_state.pop("input_mode", None)
        st.session_state.pop("preview", None)
    with st.container(key="input_source"):
        mode = st.radio("입력 방법", input_modes, horizontal=True, key="input_mode", label_visibility="collapsed")
    frame = None
    if mode == "직접 붙여넣기":
        text = st.text_area("고객 의견", height=300, key="voc_text",
            placeholder="한 줄에 한 건씩 입력해 주세요")
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
    with st.container(key="input_background"), st.popover("분석 배경 (선택)"):
        context = st.text_area("분석 배경 (선택)", key="input_context", max_chars=10000,
            help="현재 자료 전체에 공통으로 적용되는 상품 설명·설문 문항·약어를 적어주세요. 비워도 분석할 수 있습니다.",
            placeholder="예: SA는 서비스 어드바이저를 뜻합니다.")
    if frame is None or frame.empty:
        with st.container(key="input_actions"):
            st.button("입력 데이터 확인", key="preview_button", disabled=True)
        return
    if not len(frame.columns):
        st.warning("본문과 열 제목이 있는 파일을 선택해주세요.")
        return
    columns = list(frame.columns)
    text_column = ("VOC" if mode == "직접 붙여넣기" else
        st.selectbox("VOC 본문이 들어 있는 열", columns, index=columns.index("VOC") if "VOC" in columns else 0))
    st.subheader("입력 미리보기", anchor=False)
    st.dataframe(frame.head(20), hide_index=True, width="stretch", height=320,
        column_config={text_column: st.column_config.TextColumn(width="large")})
    summary = st.empty()
    fingerprint = sha256((frame.to_json(force_ascii=False) + str(text_column) + context + name + mode).encode()).hexdigest()
    saved_preview = st.session_state.get("preview")
    is_validated = bool(saved_preview and saved_preview[0] == fingerprint)
    actions = st.container(key="input_actions", horizontal=True)
    if actions.button("입력 데이터 확인", key="preview_button", type="secondary" if is_validated else "primary"):
        preview = prepare_preview(frame, text_column)
        st.session_state.preview = (fingerprint, preview)
        st.rerun()
    saved = st.session_state.get("preview")
    if not saved or saved[0] != fingerprint:
        return
    preview = saved[1]
    counts = f"원본 응답 {preview.input_count}건 · 본문 있는 응답 {preview.input_count - preview.blank_count}건 · 빈 본문·무응답 {preview.blank_count}건"
    if preview.duplicate_count:
        counts += f" · 중복 본문 {preview.duplicate_count}건 포함"
    summary.caption(counts)
    if actions.button("입력 저장 → 분류 기준표로", key="save_input", type="primary"):
        identifier = store.save_dataset(name, preview, frame, text_column, mode, context)
        st.session_state.pending_dataset = identifier
        st.session_state.page = "2. 분류 기준표"
        st.rerun()


def codebook_screen(store, dataset):
    books = store.list_codebooks(dataset["id"])
    with st.container(key="codebook_metadata"):
        counts, versions = st.columns([4, 1], vertical_alignment="center")
    with versions:
        selected = show_codebook_picker(store, dataset["id"], books)
    counts.caption(f"원본 응답 {len(dataset['records'])}건")
    book = store.codebook(selected) if selected else None
    confirmed = book is not None and book["status"] == "confirmed"
    content = st.container() if confirmed else None
    generation = st.expander("분류 기준표 새로 만들기") if confirmed else st.container()
    classification_controls = st.container(key="codebook_actions") if confirmed else None
    with generation:
        context = st.text_area("자료 설명과 분류 요청 (선택)", value=book["context"] if book else dataset["context"],
            max_chars=10000, key=f"context_{book['id'] if book else dataset['id']}",
            placeholder="예: 자동차 구매 과정의 만족도 조사입니다. 영업사원 응대, 계약 절차, 차량 인도 경험을 중심으로 분류해주세요.")
        if st.button("AI로 새 분류 기준표 만들기" if book else "AI로 분류 기준표 만들기", key="generate_codebook", type="secondary" if confirmed else "primary"):
            with st.spinner("VOC 표본에서 분류 기준을 만들고 있습니다…"):
                identifier = with_ai(lambda ai: generate_codebook(store, dataset["id"], ai, context.strip()))
            st.session_state.confirmed_book = identifier
            st.rerun()
    if book is None:
        return
    with content if content is not None else st.container():
        editing = st.session_state.get(f"revise_{book['id']}_draft")
        st.caption(f"분류 {len(editing['rows']) if editing else len(book['codes'])}개")
        notice = st.session_state.pop("book_notice", None)
        if notice:
            st.success(notice)
        if context.strip() != book["context"]:
            st.warning("자료 설명과 분류 요청이 변경되었습니다. 새 분류 기준표를 만들어주세요.")
        if book["status"] == "draft":
            st.subheader("초안 편집")
            show_codebook_changes(store, book, with_ai, save_disabled=context.strip() != book["context"])
        else:
            editor_container = st.container()
            source = inheritance_source(store, book)
            parent_id = None
            with classification_controls:
                if source and st.checkbox("직접 수정한 분류 결과 유지", value=True, key=f"keep_corrections_{dataset['id']}"):
                    parent_id = source["id"]
                actions = st.container(horizontal=True)
            with editor_container:
                show_codebook_changes(store, book, with_ai, save_container=actions)
            editing = st.session_state.get(f"revise_{book['id']}_draft")
            unsaved = bool(editing and table_has_changes(book, editing))
            if unsaved:
                st.caption("분류 기준표를 먼저 저장해주세요.")
            if actions.button("이 기준표로 VOC 분류하기", key="start_classification", type="primary", disabled=context.strip() != book["context"] or unsaved):
                def start(ai):
                    identifier = store.create_run(dataset["id"], selected, ai.model, PROMPT_VERSION, parent_run_id=parent_id)
                    st.session_state.run_id = identifier
                    run_with_progress(store, identifier, ai)
                with_ai(start)
                st.session_state.page = "3. 분류 결과"
                st.rerun()


def run_with_progress(store, identifier, ai):
    bar = st.progress(0, text="분류 결과를 만들고 있습니다…")
    execute_run(store, identifier, ai, lambda count, total, message: bar.progress(count / max(total, 1), text=f"{message} · {count}/{total}건"))


def results_screen(store, dataset, tools_container=None):
    runs = store.list_runs(dataset["id"])
    if not runs:
        st.info("분류 기준표를 저장한 뒤 ‘이 기준표로 VOC 분류하기’를 눌러주세요.")
        return
    labels = {run["id"]: f"{datetime.fromisoformat(run['created_at']).astimezone(timezone(timedelta(hours=9))):%Y-%m-%d %H:%M:%S} KST · {STATUS_LABELS[run['status']]}" for run in runs}
    labels = unique_analysis_labels(labels)
    key = f"result_choice_{dataset['id']}"
    pending = st.session_state.pop("run_id", None)
    if pending in labels:
        st.session_state[key] = pending
    if key not in st.session_state:
        st.session_state[key] = next((item["id"] for item in runs if item["status"] == "completed"), runs[0]["id"])
    with st.container(key="result_metadata"):
        summary, history = st.columns([4, 1], vertical_alignment="center")
    with history.container(horizontal=True, horizontal_alignment="right"):
        with st.popover("분석 이력", icon=":material/history:", type="tertiary"):
            selected = st.selectbox("저장된 분석 실행", list(labels), format_func=labels.get, key=key)
    run = store.run(selected)
    book = store.codebook(run["codebook_id"])
    originals, issues = result_tables(store, selected)
    empty_count = originals['응답 상태'].eq('없음·무응답·모름').sum()
    book_name = f"{book['name']} · " if book["name"] else ""
    summary.caption(f"전체 응답 {len(originals)}건 · 분류 기준표 {book_name}v{book['version']}",
        help=f"내용 없는 응답 {empty_count}건 포함")
    if run["status"] != "completed":
        st.warning(f"{STATUS_LABELS[run['status']]} · {run['error'] or '저장된 지점부터 이어서 처리할 수 있습니다.'}")
        if st.button("보완 이어서 실행 (최대 2회 추가)" if run["status"] == "needs_review" else "실패·미처리 이어서 실행", key="resume_run"):
            def resume(ai):
                if run["status"] == "needs_review":
                    store.extend_limit(selected)
                run_with_progress(store, selected, ai)
            with_ai(resume, run["model"])
            st.rerun()
    show_grouped_results(store, run, book, originals, issues, dataset, tools_container=tools_container)


def main():
    st.set_option("client.toolbarMode", "minimal")
    st.set_page_config(page_title="AI VOC Analyzer", layout="wide")
    apply_workspace_theme()
    store = Store()
    clear_deleted_dataset_state()
    if "page" in st.session_state:
        st.session_state.nav = st.session_state.pop("page")
    if "pending_dataset" in st.session_state:
        st.session_state.dataset_id = st.session_state.pop("pending_dataset")
    datasets = store.list_datasets()
    with st.sidebar:
        st.title("VOC Analyzer")
        nav_labels = {"1. 입력": ":material/description: 데이터 입력",
                      "2. 분류 기준표": ":material/table_chart: 분류 기준표",
                      "3. 분류 결과": ":material/bar_chart: 분류 결과"}
        page = st.radio("분석 단계", list(nav_labels), key="nav",
            format_func=nav_labels.get, label_visibility="collapsed")
    if not datasets:
        st.session_state.pop("dataset_id", None)
    try:
        if page == "1. 입력":
            notice = st.session_state.pop("dataset_notice", None)
            if notice:
                st.toast(notice)
            input_screen(store)
        else:
            tools_container = None
            if page == "3. 분류 결과":
                with st.container(key="result_header", horizontal=True, vertical_alignment="center", gap="medium"):
                    st.header("분류 결과", anchor=False, width="content")
                    if datasets:
                        with st.container(key="result_dataset", width=230):
                            show_dataset_picker(store, datasets, compact=True)
                    tools_container = st.container(key="result_header_tools", horizontal=True,
                        horizontal_alignment="right", vertical_alignment="center", gap="small")
            else:
                with st.container(key="codebook_header", horizontal=True, vertical_alignment="center", gap="medium"):
                    st.header("분류 기준표", anchor=False, width="content")
                    if datasets:
                        with st.container(key="codebook_dataset", width=230):
                            show_dataset_picker(store, datasets, compact=True)
            if not datasets:
                st.info("먼저 입력 자료를 저장해주세요.")
            else:
                dataset = store.dataset(st.session_state.dataset_id)
                if page == "2. 분류 기준표":
                    codebook_screen(store, dataset)
                else:
                    results_screen(store, dataset, tools_container)
    except (ValueError, sqlite3.Error, OSError) as exc:
        st.error(str(exc) if isinstance(exc, ValueError) else "로컬 파일·저장소를 읽거나 저장하지 못했습니다. 접근 권한과 여유 공간을 확인해주세요.")


if __name__ == "__main__":
    main()
