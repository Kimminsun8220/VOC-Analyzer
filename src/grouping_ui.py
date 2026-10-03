"""같은 묶음 상태로 차트·집계·원문·근거를 함께 갱신하는 결과 화면."""

import streamlit as st

from src.charts_ui import show_dashboard
from src.grouping import SENTIMENTS, group_results
from src.results import csv_download

MODES = ["전체 보기", "대분류로 묶기", "세부분류 직접 선택"]


def show_grouped_results(store, run, book, originals, issues):
    prefix = f"group_{run['id']}_{book['id']}"
    codes = book["codes"]
    labels = {code.id: f"{code.category} → {code.name}" for code in codes}
    mode_key, category_key, codes_key, sentiment_key = [f"{prefix}_{key}" for key in ("mode", "categories", "codes", "sentiment")]

    def clear_chart_history():
        st.session_state[prefix + "_chart_history"] = []
        st.session_state[prefix + "_chart_epoch"] = st.session_state.get(prefix + "_chart_epoch", 0) + 1

    def go_back():
        history = list(st.session_state.get(prefix + "_chart_history", []))
        if history:
            previous = history.pop()
            for key, value in previous.items():
                st.session_state[f"{prefix}_{key}"] = value
            st.session_state[prefix + "_chart_history"] = history
            st.session_state[prefix + "_chart_epoch"] = st.session_state.get(prefix + "_chart_epoch", 0) + 1

    def reset():
        st.session_state[mode_key] = MODES[0]
        st.session_state[category_key] = []
        st.session_state[codes_key] = []
        st.session_state[sentiment_key] = "전체"
        clear_chart_history()

    with st.container(border=True):
        st.subheader("분류 묶어보기")
        st.caption("데이터를 보면서 함께 볼 분류를 골라보세요. 선택하면 아래 차트·건수·원문이 함께 바뀝니다.")
        saved = store.list_groups(run["id"])
        if saved:
            with st.expander(f"저장한 묶음 불러오기 · {len(saved)}개"):
                saved_map = {group["id"]: group for group in saved}
                choice = st.selectbox("저장한 묶음", list(saved_map),
                    format_func=lambda identifier: saved_map[identifier]["name"], key=f"{prefix}_saved")
                if st.button("선택한 묶음 불러오기", key=f"{prefix}_load"):
                    group = saved_map[choice]
                    if group["codebook_id"] != book["id"] or set(group["code_ids"]) - labels.keys():
                        st.warning("다른 분류 기준표 버전에서 저장한 묶음입니다. 현재 분류에서 다시 선택해주세요.")
                    else:
                        st.session_state[mode_key] = MODES[2]
                        st.session_state[codes_key] = group["code_ids"]
                        st.session_state[sentiment_key] = group["sentiment"]
                        clear_chart_history()
                        st.success(f"‘{group['name']}’ 묶음을 불러왔습니다.")

        mode = st.radio("묶어보기 방식", MODES, horizontal=True, key=mode_key, on_change=clear_chart_history)
        selected_ids = None
        if mode == MODES[1]:
            categories = st.multiselect("함께 볼 대분류", sorted({code.category for code in codes}),
                key=category_key, placeholder="예: 배송을 선택하면 하위 분류를 모두 모아봅니다", wrap=True, on_change=clear_chart_history)
            selected_ids = [code.id for code in codes if code.category in categories]
        elif mode == MODES[2]:
            selected_ids = st.multiselect("함께 볼 세부분류", list(labels), format_func=labels.get,
                key=codes_key, placeholder="예: 오배송과 배송 속도를 함께 선택하세요", wrap=True, on_change=clear_chart_history)
        filter_column, reset_column = st.columns([3, 1], vertical_alignment="bottom")
        sentiment = filter_column.selectbox("선택 범위의 감성", SENTIMENTS, key=sentiment_key, on_change=clear_chart_history)
        reset_column.button("선택 초기화 · 전체 보기", on_click=reset, key=f"{prefix}_reset", width="stretch")
        if st.session_state.get(prefix + "_chart_history"):
            st.button("← 차트 선택 이전으로", on_click=go_back, key=prefix + "_back")
        chart_notice = st.session_state.pop(prefix + "_chart_notice", None)
        if chart_notice:
            st.info(chart_notice)
        if selected_ids is not None:
            if not selected_ids:
                st.info("분류를 하나 이상 선택해주세요. 여러 개를 선택하면 한 묶음으로 보여줍니다.")
            else:
                st.caption(f"세부분류 {len(selected_ids)}개를 함께 보고 있습니다. 선택 항목의 ×로 개별 해제할 수 있습니다.")
        if sentiment != "전체":
            st.caption(f"현재 선택한 분류의 ‘{sentiment}’ 의견만 조회합니다.")
        st.caption("분류 기준표와 기존 분류는 유지됩니다. 묶어보기에는 AI를 다시 호출하지 않습니다.")

    grouped = group_results(originals, issues, codes, selected_ids, sentiment)
    if run["status"] != "completed":
        st.warning("진행 중이거나 검토가 남은 분석입니다. 아래 수치는 현재 저장된 의견만으로 계산한 잠정 결과입니다.")
    if selected_ids == []:
        return
    a, b, c = st.columns(3)
    a.metric("묶음의 고유 VOC", f"{grouped.voc_count}건")
    b.metric("전체 응답 대비", f"{grouped.percent:.1f}%")
    c.metric("선택 범위의 의견", f"{len(grouped.issues)}개")
    st.caption(
        f"분모: 이 실행의 원본 응답 {grouped.total_count}건(무응답 포함). "
        f"여러 선택 분류에 겹친 VOC {grouped.overlap_count}건도 묶음에서는 각각 1건으로 셉니다."
    )
    if grouped.voc_count == 0:
        st.info("선택한 분류·감성에 해당하는 의견이 없습니다. 선택 범위를 바꿔보세요.")

    if selected_ids is not None:
        with st.expander("선택 분류별 건수 확인"):
            st.dataframe(grouped.counts, hide_index=True, width="stretch")
            st.caption("분류마다 고유 VOC를 셉니다. 분류 사이에 같은 VOC가 겹칠 수 있으므로 행의 건수 합계는 묶음 건수와 다를 수 있습니다.")
        if selected_ids and run["status"] == "completed":
            with st.expander("이 조합을 저장해 다시 보기"):
                name = st.text_input("묶음 이름", max_chars=80, placeholder="예: 배송 경험", key=f"{prefix}_name")
                st.caption("선택한 세부분류와 감성 조건을 이 분석 실행에 저장합니다.")
                if st.button("묶음 저장", key=f"{prefix}_save", disabled=not name.strip()):
                    try:
                        store.save_group(run["id"], name, selected_ids, sentiment)
                        st.session_state[f"{prefix}_notice"] = f"‘{name.strip()}’ 묶음을 저장했습니다. 위에서 다시 불러올 수 있습니다."
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
    notice = st.session_state.pop(f"{prefix}_notice", None)
    if notice:
        st.success(notice)

    show_dashboard(store, run, book, grouped, selected_ids, sentiment, prefix)

    view_originals, view_issues = st.tabs(["묶음의 원문 · 중복 제거", "선택한 의견과 근거"], key=f"{prefix}_tables")
    with view_originals:
        st.dataframe(grouped.originals, hide_index=True, width="stretch",
            column_order=["VOC 원문", "선택된 분류", "선택 의견의 감성", "선택 의견 수", "VOC ID"])
        st.download_button("현재 묶음 원문 CSV", csv_download(grouped.originals),
            file_name="voc_group_originals.csv", mime="text/csv", key=f"{prefix}_download_originals")
    with view_issues:
        st.dataframe(grouped.issues, hide_index=True, width="stretch",
            column_order=["대분류", "세부분류", "감성", "VOC 원문", "원문 근거", "대상·역할", "배경 근거", "VOC ID"])
        st.download_button("현재 묶음 의견 CSV", csv_download(grouped.issues),
            file_name="voc_group_issues.csv", mime="text/csv", key=f"{prefix}_download_issues")
    if not grouped.issues.empty:
        with st.expander("원문 한 건의 선택된 의견 자세히 보기"):
            identifiers = grouped.originals["VOC ID"].tolist()
            detail_key = f"{prefix}_detail"
            if st.session_state.get(detail_key) not in identifiers:
                st.session_state[detail_key] = identifiers[0]
            voc_id = st.selectbox("확인할 VOC ID", identifiers, key=detail_key)
            details = grouped.issues[grouped.issues["VOC ID"] == voc_id]
            st.text(details.iloc[0]["VOC 원문"])
            st.caption("아래는 현재 선택한 분류·감성에 해당하는 의견입니다.")
            for item in details.to_dict("records"):
                st.write(f"**{item['대분류']} → {item['세부분류']} · {item['감성']}**")
                st.text(f"원문 근거: {item['원문 근거']}\n대상·역할: {item['대상·역할']}")
                if item["배경 근거"]:
                    st.text(f"배경 근거: {item['배경 근거']}")
