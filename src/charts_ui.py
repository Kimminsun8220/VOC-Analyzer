"""막대 선택을 공통 묶음 상태에 연결하는 시각화 화면."""

from hashlib import sha256
from html import escape
import json
import textwrap

import plotly.graph_objects as go
import streamlit as st

from src.chart_data import COUNT, PERCENT, DENOMINATOR, build_dashboard, chart_selection_values, dashboard_export, selection_scope
from src.results import csv_download

SENTIMENT_COLORS = {"긍정": "#2563EB", "부정": "#C2410C", "중립": "#64748B", "판단 불가": "#7C3AED"}
CONFIG = {"displaylogo": False, "scrollZoom": False, "modeBarButtonsToRemove": ["lasso2d", "select2d", "zoom2d", "pan2d"],
          "toImageButtonOptions": {"format": "png", "filename": "voc_chart", "scale": 2}}


def bar_figure(frame, label_column, id_column, measure=COUNT, sentiment_colors=False):
    """정확한 수치·분모를 텍스트로도 제공한다. 차트에는 원문을 포함하지 않는다."""
    labels = [str(value) for value in frame[label_column]]
    safe_labels = ["<br>".join(escape(part) for part in textwrap.wrap(label, width=24)) for label in labels]
    values = frame[measure].tolist()
    custom = [[str(row[id_column]), int(row[COUNT]), float(row[PERCENT]), int(row[DENOMINATOR]),
               escape(str(row[label_column])), float(row.get("대분류 내 비율 (%)", 0))] for row in frame.to_dict("records")]
    text = [f"{int(row[COUNT])}건 · {row[PERCENT]:.1f}%" for row in frame.to_dict("records")]
    hover = "%{customdata[4]}<br>고유 VOC %{customdata[1]}건<br>범위 내 %{customdata[2]:.1f}% (분모 %{customdata[3]}건)"
    if "대분류 내 비율 (%)" in frame:
        hover += "<br>대분류 내 %{customdata[5]:.1f}%"
    colors = [SENTIMENT_COLORS[label] for label in labels] if sentiment_colors else "#1E40AF"
    fig = go.Figure(go.Bar(x=values, y=list(range(len(labels))), orientation="h", customdata=custom,
        marker_color=colors, text=text, textposition="outside", cliponaxis=False, hovertemplate=hover + "<extra></extra>",
        selected={"marker": {"opacity": 1}}, unselected={"marker": {"opacity": 0.65}}))
    maximum = max(values, default=0)
    fig.update_layout(height=max(280, min(1500, 42 * len(labels) + 60)), margin=dict(l=12, r=120, t=16, b=40),
        template="plotly_white", showlegend=False, clickmode="event+select", dragmode=False,
        font=dict(family="Pretendard Variable, Pretendard, sans-serif", size=13, color="#0F172A"),
        xaxis=dict(title="고유 VOC 수 (건)" if measure == COUNT else "현재 범위 내 비율 (%)", rangemode="tozero",
                   range=[0, max(maximum * 1.12, 1)], ticksuffix="" if measure == COUNT else "%", fixedrange=True,
                   dtick=1 if measure == COUNT and maximum <= 10 else None),
        yaxis=dict(tickmode="array", tickvals=list(range(len(labels))), ticktext=safe_labels,
                   autorange="reversed", automargin=True, fixedrange=True), bargap=0.32)
    return fig


def show_dashboard(store, run, book, grouped, selected_ids, sentiment, prefix):
    st.subheader("현재 범위 시각화")
    if run["status"] != "completed":
        st.info("차트는 분류와 승계 검토를 마친 실행에서 제공합니다. 현재 저장된 의견은 아래 표에서 확인하세요.")
        return
    if grouped.voc_count == 0:
        st.info("시각화할 의견이 없습니다. 선택 범위를 바꾸거나 ‘전체 응답·무응답’ 탭에서 응답 상태를 확인하세요.")
        return
    filtered = selected_ids is not None or sentiment != "전체"
    data = build_dashboard(grouped, filtered)
    denominator_label = "현재 선택 범위의 고유 VOC" if filtered else "전체 원본 응답(무응답 포함)"
    st.caption(f"차트 비율의 분모: {denominator_label} {data.denominator}건. 복수 분류·감성은 각각 포함하므로 비율 합계가 100%를 넘을 수 있습니다.")
    st.caption("막대를 누르면 해당 범위로 좁혀지고 아래 원문도 바뀝니다. 여러 분류를 함께 보려면 위의 묶어보기를 사용하세요.")
    count_column, top_column = st.columns([1, 3])
    count_column.metric("현재 범위의 대분류", f"{len(data.categories)}개")
    top = data.codes.iloc[0]
    top_column.metric("가장 많은 세부분류", top["세부분류"])
    top_column.caption(f"{int(top[COUNT])}건 · {top['대분류']}")
    display = st.radio("차트 표시", ["VOC 건수", "범위 내 비율"], key=prefix + "_measure", horizontal=True)
    measure = COUNT if display == "VOC 건수" else PERCENT
    selected_codes = book["codes"]
    mode_key, category_key, codes_key, sentiment_key = [f"{prefix}_{key}" for key in ("mode", "categories", "codes", "sentiment")]
    scope_signature = sha256(json.dumps([sorted(selected_ids) if selected_ids is not None else None, sentiment,
        run["result_revision"], st.session_state.get(prefix + "_chart_epoch", 0)], ensure_ascii=False).encode()).hexdigest()[:16]

    def show_selectable(frame, kind, label, identifier, title):
        st.markdown(f"**{title}**")
        chart_key = f"{prefix}_{kind}_chart_{scope_signature}_{len(frame)}_{measure}"

        def on_select():
            if store.run(run["id"])["result_revision"] != run["result_revision"]:
                st.session_state[prefix + "_chart_notice"] = "결과가 수정되어 차트를 갱신했습니다. 새 수치를 보고 다시 선택해주세요."
                return
            event = st.session_state.get(chart_key, {})
            values = chart_selection_values(event, frame[identifier])
            scope = selection_scope(kind, values, selected_codes, selected_ids, sentiment)
            if scope is None or scope == (selected_ids, sentiment):
                return
            snapshot = {"mode": st.session_state[mode_key], "categories": list(st.session_state.get(category_key, [])),
                        "codes": list(st.session_state.get(codes_key, [])), "sentiment": sentiment}
            history = st.session_state.get(prefix + "_chart_history", [])
            st.session_state[prefix + "_chart_history"] = (history + [snapshot])[-10:]
            identifiers, next_sentiment = scope
            if kind != "sentiment":
                st.session_state[mode_key] = "세부분류 직접 선택"
                st.session_state[codes_key] = identifiers
            st.session_state[sentiment_key] = next_sentiment
            st.session_state[prefix + "_chart_epoch"] = st.session_state.get(prefix + "_chart_epoch", 0) + 1

        st.plotly_chart(bar_figure(frame, label, identifier, measure, kind == "sentiment"), width="stretch",
            key=chart_key, on_select=on_select, selection_mode="points", config=CONFIG)

    left, right = st.columns(2)
    with left:
        show_selectable(data.categories.head(20), "category", "대분류", "대분류", "대분류별 고유 VOC")
        if len(data.categories) > 20:
            st.caption("상위 20개를 표시합니다. 전체 대분류는 아래 집계표에서 확인할 수 있습니다.")
    with right:
        show_selectable(data.sentiments, "sentiment", "감성", "감성", "감성이 포함된 고유 VOC")
    options = [5, 10, 20, 50]
    top_n = st.selectbox("세부분류 TOP N", options, index=1, key=prefix + "_top_n")
    code_frame = data.codes.head(top_n).copy()
    code_frame["분류"] = code_frame["대분류"] + " → " + code_frame["세부분류"]
    show_selectable(code_frame, "code", "분류", "코드 ID", "세부분류별 고유 VOC")
    st.caption(f"현재 범위의 세부분류 {len(data.codes)}개 중 {len(code_frame)}개를 표시합니다. TOP N은 차트 표시 개수이며 원문 범위를 줄이지 않습니다.")
    scope_label = ("전체 분류" if selected_ids is None else " + ".join(
        f"{code.category} → {code.name}" for code in selected_codes if code.id in selected_ids)) + f" / 감성: {sentiment}"
    with st.expander("차트 수치·분모 확인 및 내려받기"):
        st.caption("차트 조작 대신 위의 분류·감성 선택을 이용해도 같은 범위를 확인할 수 있습니다. 표에는 TOP N 밖의 분류도 포함됩니다.")
        for label, table in [("대분류", data.categories), ("세부분류", data.codes), ("감성", data.sentiments)]:
            st.markdown(f"**{label} 집계**")
            st.dataframe(table, hide_index=True, width="stretch", column_config={"코드 ID": None,
                PERCENT: st.column_config.NumberColumn(PERCENT, format="%.1f"),
                "대분류 내 비율 (%)": st.column_config.NumberColumn("대분류 내 비율 (%)", format="%.1f")})
        st.download_button("현재 범위 집계 CSV", csv_download(dashboard_export(data, run, book, scope_label)),
            file_name="voc_chart_counts.csv", mime="text/csv", key=prefix + "_counts_download")
