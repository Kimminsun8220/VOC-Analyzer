"""전체 분포에서 원문 조회와 응답 수정으로 이어지는 결과 화면."""

import streamlit as st

from src.charts_ui import show_dashboard
from src.category_summary import show_category_summary, show_sentiment_summary, show_sentiment_overview
from src.corrections_ui import show_history, show_response_detail
from src.grouping import group_results
from src.result_explorer import (
    NORMAL_STATES, FAILED_STATES, filtered_responses,
)
from src.response_table import show_response_table
from src.result_downloads import XLSX_MIME, download_tables, xlsx_download
from src.result_groups import initial_layout, load_code_group, grouped_dashboard, change_layout
from src.saved_groups_ui import show_saved_group_picker
from src.response_basis import ALL_BASIS, VALID_BASIS, basis_responses

MODES = ["전체 보기", "대분류로 묶기", "세부분류 직접 선택"]


def show_grouped_results(store, run, book, originals, issues, dataset=None, *, tools_container=None, basis=ALL_BASIS):
    if tools_container is None:
        tools_container = st.container(key="result_header_tools", horizontal=True,
            horizontal_alignment="right", vertical_alignment="center", gap="small")
    prefix = f"group_{run['id']}_{book['id']}"
    codes = book["codes"]
    labels = {code.id: f"[{code.category}] {code.name}" for code in codes}
    mode_key, category_key, codes_key = [f"{prefix}_{key}" for key in ("mode", "categories", "codes")]
    selected_key, editing_key = prefix + "_selected_voc", prefix + "_editing_voc"
    defaults = {mode_key: MODES[0], category_key: [], codes_key: [], prefix + "_filters": {}}
    pending = st.session_state.pop(prefix + "_pending_group", None)
    if pending:
        st.session_state[prefix + "_drill_categories"] = []
        st.session_state[prefix + "_category_sentiment"] = None
        st.session_state[prefix + "_layout"] = load_code_group(
            st.session_state.get(prefix + "_layout", initial_layout(codes)), codes, pending["code_ids"])
        st.session_state[mode_key] = MODES[2]
        st.session_state[codes_key] = pending["code_ids"]
        st.session_state[prefix + "_filters"] = ({"감성": {"values": [pending["sentiment"]]}}
            if pending["sentiment"] != "전체" else {})
        st.session_state.pop(selected_key, None)
        st.session_state.pop(editing_key, None)
        st.session_state[prefix + "_table_epoch"] = st.session_state.get(prefix + "_table_epoch", 0) + 1
        st.session_state[prefix + "_show_originals"] = True
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    def reset():
        for key, value in defaults.items():
            st.session_state[key] = value
        st.session_state.pop(selected_key, None)
        st.session_state.pop(editing_key, None)
        st.session_state[prefix + "_chart_epoch"] = st.session_state.get(prefix + "_chart_epoch", 0) + 1
        st.session_state[prefix + "_table_epoch"] = st.session_state.get(prefix + "_table_epoch", 0) + 1

    mode = st.session_state[mode_key]
    if mode == MODES[0]:
        selected_ids = None
    elif mode == MODES[1]:
        selected_ids = [code.id for code in codes if code.category in st.session_state[category_key]]
    else:
        selected_ids = st.session_state[codes_key]
    chart_originals = basis_responses(originals, basis)
    chart_issues = issues if issues.empty else issues[issues["VOC ID"].isin(chart_originals["VOC ID"])]
    if basis != ALL_BASIS and st.session_state.get(prefix + "_dashboard_sentiment") == "무응답":
        st.session_state[prefix + "_dashboard_sentiment"] = None
    baseline = group_results(chart_originals, chart_issues, codes)
    if run["status"] == "completed" and not originals.empty:
        sentiment_scope = show_sentiment_overview(store, run, chart_originals, baseline.issues, prefix, basis=basis)
        if sentiment_scope:
            reset()
            st.session_state[prefix + "_drill_categories"] = []
            st.session_state[prefix + "_category_sentiment"] = None
            sentiment_key = prefix + "_dashboard_sentiment"
            st.session_state[sentiment_key] = (None if st.session_state.get(sentiment_key) == sentiment_scope
                                              else sentiment_scope)
            st.session_state[prefix + "_show_originals"] = False
            st.rerun()
    dashboard_sentiment = st.session_state.get(prefix + "_dashboard_sentiment")
    if dashboard_sentiment:
        _, chart_group, _ = filtered_responses(chart_originals, chart_issues, codes, None,
            {"_response_sentiment": dashboard_sentiment})
    else:
        chart_group = baseline
    drill = st.session_state.get(prefix + "_drill_categories", [])
    category_sentiment = st.session_state.get(prefix + "_category_sentiment") if drill else None
    with st.container(key="result_filter_toolbar"):
        conditions, display_controls = st.columns([1, 1], vertical_alignment="center", gap="small")
    if dashboard_sentiment or drill:
        with conditions.container(key="result_conditions", horizontal=True, vertical_alignment="center", gap="xxsmall"):
            if dashboard_sentiment:
                if st.button(f"{dashboard_sentiment} ×", key=prefix + "_clear_sentiment", type="tertiary"):
                    st.session_state[prefix + "_dashboard_sentiment"] = None
                    st.session_state[prefix + "_drill_categories"] = []
                    st.session_state[prefix + "_category_sentiment"] = None
                    reset()
                    st.session_state[prefix + "_show_originals"] = False
                    st.rerun()
            if category_sentiment:
                label = "기타" if category_sentiment == "무응답/중립/미검토" else category_sentiment
                if st.button(f"대분류 내 {label} ×", key=prefix + "_clear_category_sentiment", type="tertiary"):
                    st.session_state[prefix + "_category_sentiment"] = None
                    reset()
                    st.session_state[prefix + "_show_originals"] = False
                    st.rerun()
            if drill:
                if st.button(" / ".join(drill) + " ×", key=prefix + "_clear_category", type="tertiary"):
                    st.session_state[prefix + "_drill_categories"] = []
                    st.session_state[prefix + "_category_sentiment"] = None
                    reset()
                    st.session_state[prefix + "_show_originals"] = False
                    st.rerun()
            if st.button("조건 전체 해제", key=prefix + "_clear_all", type="tertiary"):
                st.session_state[prefix + "_dashboard_sentiment"] = None
                st.session_state[prefix + "_drill_categories"] = []
                st.session_state[prefix + "_category_sentiment"] = None
                reset()
                st.session_state[prefix + "_show_originals"] = False
                st.rerun()
    review_count = (~originals["응답 상태"].isin(NORMAL_STATES | FAILED_STATES)).sum()
    if review_count:
        if st.button(f"확인이 필요한 응답 {review_count}건 보기", key=prefix + "_review"):
            reset()
            st.session_state[prefix + "_dashboard_sentiment"] = None
            st.session_state[prefix + "_drill_categories"] = []
            st.session_state[prefix + "_category_sentiment"] = None
            st.session_state[prefix + "_filters"] = {"응답 상태": {"values": list(dict.fromkeys(
                originals.loc[~originals["응답 상태"].isin(NORMAL_STATES | FAILED_STATES), "응답 상태"]))}}
            st.session_state[prefix + "_show_originals"] = True
            st.rerun()
    show_dashboard(store, run, book, chart_group, None, dashboard_sentiment or "전체", prefix, originals,
        controls_container=display_controls, basis=basis)
    group_notice = st.session_state.pop(prefix + "_notice", None)
    if group_notice:
        st.success(group_notice)
    actions = st.container(key="result_actions", horizontal=True, vertical_alignment="center", gap="small")
    originals_label = "현재 조건 원문 보기" if dashboard_sentiment or drill else "전체 원문 보기"
    if actions.button(originals_label, on_click=reset, key=prefix + "_open_originals", icon=":material/table_rows:"):
        st.session_state[prefix + "_show_originals"] = True
        st.session_state[prefix + "_table_epoch"] = st.session_state.get(prefix + "_table_epoch", 0) + 1
    saved = store.list_groups(run["id"])
    save_ids = selected_ids
    if save_ids is None and drill:
        save_ids = [code.id for code in codes if code.category in drill]
    with tools_container.popover("지금 분류 저장", icon=":material/save:", key="saved_groups_actions"):
        layout = st.session_state.get(prefix + "_layout", initial_layout(codes))
        if layout["history"]:
            if st.button("합치기 되돌리기", key=prefix + "_undo_group"):
                st.session_state[prefix + "_layout"] = change_layout(layout, "undo")
                st.session_state[prefix + "_drill_categories"] = []
                st.session_state[prefix + "_category_sentiment"] = None
                reset()
                st.session_state[prefix + "_show_originals"] = False
                st.rerun()
        if save_ids and run["status"] == "completed":
            st.caption("저장할 분류: " + " / ".join(labels[identifier] for identifier in save_ids))
            with st.form(prefix + "_save_form", border=False):
                name = st.text_input("이름", max_chars=80, placeholder="예: 배송 문제", key=prefix + "_name")
                submitted = st.form_submit_button("저장", key=prefix + "_save")
            if submitted:
                if not name.strip():
                    st.error("이름을 입력해주세요.")
                else:
                    try:
                        store.save_group(run["id"], name, save_ids)
                        st.session_state[prefix + "_notice"] = f"‘{name.strip()}’ 저장 완료"
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
        if saved:
            with st.expander("저장한 분류", expanded=True):
                saved_map = {group["id"]: group for group in saved}
                choice = show_saved_group_picker(store, run["id"], saved, prefix)
                if st.button("불러오기", key=prefix + "_load"):
                    group = saved_map[choice]
                    if group["codebook_id"] != book["id"] or set(group["code_ids"]) - labels.keys():
                        st.warning("현재 분류 기준표에서 다시 선택해주세요.")
                    else:
                        st.session_state[prefix + "_pending_group"] = group
                        st.rerun()
        else:
            show_saved_group_picker(store, run["id"], [], prefix)
        if not save_ids:
            st.caption("저장할 분류를 차트에서 선택해주세요.")
    with tools_container.popover("다운로드", icon=":material/download:"):
        download_key = prefix + "_download_basis"
        if st.session_state.get(prefix + "_download_screen_basis") != basis:
            st.session_state[download_key] = basis
            st.session_state[prefix + "_download_screen_basis"] = basis
        download_basis = st.radio("다운로드 기준", [ALL_BASIS, VALID_BASIS], horizontal=True,
            key=download_key, help="선택한 기준의 전체 자료를 받습니다. 화면의 감성·분류 필터는 적용하지 않습니다.")
        suffix = "전체기준" if download_basis == ALL_BASIS else "유효기준"
        layout = st.session_state.get(prefix + "_layout")
        st.download_button("VOC별 분류 다운로드", xlsx_download(download_tables(
            "vocs", originals, issues, codes, book, download_basis)),
            file_name=f"voc_classifications_{suffix}.xlsx", mime=XLSX_MIME, on_click="ignore", key=prefix + "_download_vocs")
        st.download_button("분류 통계 다운로드", xlsx_download(download_tables(
            "statistics", originals, issues, codes, book, download_basis, layout)), file_name=f"classification_statistics_{suffix}.xlsx",
            mime=XLSX_MIME, on_click="ignore", key=prefix + "_download_statistics")

    def close_popup():
        st.session_state[prefix + "_show_originals"] = False
        st.session_state.pop(selected_key, None)
        st.session_state.pop(editing_key, None)
        st.session_state[prefix + "_filters"] = {}
        st.session_state[prefix + "_table_epoch"] = st.session_state.get(prefix + "_table_epoch", 0) + 1

    @st.dialog("고객 원문", width="large", on_dismiss=close_popup)
    def originals_popup():
        show_originals(store, run, book, originals, issues, dataset, prefix, reset)

    if st.session_state.get(prefix + "_show_originals"):
        originals_popup()


def show_originals(store, run, book, originals, issues, dataset, prefix, reset):
    codes = book["codes"]
    labels = {code.id: f"[{code.category}] {code.name}" for code in codes}
    mode_key, category_key, codes_key = [f"{prefix}_{key}" for key in ("mode", "categories", "codes")]
    selected_key, editing_key = prefix + "_selected_voc", prefix + "_editing_voc"
    mode = st.session_state[mode_key]
    dashboard_sentiment = st.session_state.get(prefix + "_dashboard_sentiment")
    if mode == MODES[0]:
        selected_ids = None
    elif mode == MODES[1]:
        selected_ids = [code.id for code in codes if code.category in st.session_state[category_key]]
    else:
        selected_ids = st.session_state[codes_key]
    drill = st.session_state.get(prefix + "_drill_categories", [])
    if selected_ids is None and drill:
        selected_ids = [code.id for code in codes if code.category in drill]
        scope = "/".join(drill)
    elif selected_ids is None:
        sentiment_scope = dashboard_sentiment
        scope = f"{sentiment_scope} 응답" if sentiment_scope else "전체 분류"
    elif mode == MODES[1]:
        scope = "/".join(st.session_state[category_key])
    else:
        categories = list(dict.fromkeys(code.category for code in codes if code.id in selected_ids))
        if len(categories) == 1:
            scope = f"[{categories[0]}] " + "/".join(code.name for code in codes if code.id in selected_ids)
        else:
            scope = " / ".join(labels[identifier] for identifier in selected_ids)
    if selected_ids is not None and dashboard_sentiment:
        scope += f" · {dashboard_sentiment}"
    category_sentiment = st.session_state.get(prefix + "_category_sentiment") if drill else None
    if category_sentiment:
        scope += f" · 대분류 내 {category_sentiment}"
    title, action = st.columns([4, 1], vertical_alignment="center")
    title.subheader(scope or "선택한 분류 없음", anchor=False)
    action.button("전체 보기", on_click=reset, key=prefix + "_reset", width="stretch",
        help="표 안의 선택과 필터를 해제합니다. 상단에서 선택한 감성과 대분류는 유지합니다.")
    notice = st.session_state.pop("correction_notice", None)
    if notice:
        st.success(notice)
    filters = dict(st.session_state.get(prefix + "_filters", {}))
    if dashboard_sentiment:
        filters["_response_sentiment"] = dashboard_sentiment
    if category_sentiment:
        filters["_category_sentiment"] = {"categories": drill, "sentiment": category_sentiment}
    view, scoped_group, options = filtered_responses(originals, issues, codes, selected_ids, filters)
    percentage = len(view) / len(originals) * 100 if len(originals) else 0
    if mode != MODES[1]:
        st.caption(f"응답 {len(view)}건 · 전체 {len(originals)}건의 {percentage:.1f}%")
    if run["status"] != "completed":
        st.caption("검토·처리가 끝나기 전의 잠정 결과입니다.")
    table_notice = st.session_state.pop(prefix + "_table_notice", None)
    if table_notice:
        st.info(table_notice)
    table = show_response_table(view, options, filters, store, run, prefix, selected_ids, codes)
    if not st.session_state.get(selected_key) and not view.empty:
        st.caption("분류·감성 셀을 더블클릭하면 바로 수정할 수 있습니다.")

    if mode != MODES[0]:
        with st.expander("감성 비중" if mode == MODES[2] else "분류·감성 비중", expanded=True):
            if mode == MODES[1]:
                layout = st.session_state.get(prefix + "_layout", initial_layout(codes))
                data = grouped_dashboard(scoped_group, codes, layout)
                show_category_summary(data, view, scoped_group.issues, len(originals), prefix)
            else:
                show_sentiment_summary(view, scoped_group.issues, len(originals), prefix + "_code_sentiments")

    selected_voc = st.session_state.get(selected_key)
    if selected_voc:
        inline_editable = any(row["id"] == selected_voc and row.get("opinions") for row in table["rows"])
        if inline_editable:
            st.session_state.pop(editing_key, None)
        else:
            with st.container(border=True):
                show_response_detail(store, run, book, dataset or store.dataset(run["dataset_id"]),
                    selected_voc, editing_key, include_history=False)
        show_history(store, run, book, selected_voc)
