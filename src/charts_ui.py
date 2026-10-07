"""공통 묶음 상태의 차트와 결과 해석에 필요한 집계 기준을 제공한다."""

from html import escape
from dataclasses import replace
from pathlib import Path
import textwrap

import plotly.graph_objects as go
import streamlit as st

from src.chart_data import COUNT, PERCENT, DENOMINATOR
from src.result_groups import initial_layout, grouped_chart, drill_code_chart, chart_signature, chart_action

SENTIMENT_COLORS = {"긍정": "#2563EB", "부정": "#C2410C", "중립": "#64748B", "판단 불가": "#7C3AED"}
PRIMARY_FONT = {"family": "Pretendard Variable, Pretendard, sans-serif", "size": 14, "color": "#0F172A", "weight": 500}
SECONDARY_FONT = {**PRIMARY_FONT, "size": 12, "color": "#64748B", "weight": 400}
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
    hover = "%{customdata[4]}<br>응답 %{customdata[1]}건<br>전체 응답 대비 %{customdata[2]:.1f}% (분모 %{customdata[3]}건)"
    if "대분류 내 비율 (%)" in frame:
        hover += "<br>대분류 내 %{customdata[5]:.1f}%"
    colors = [SENTIMENT_COLORS[label] for label in labels] if sentiment_colors else "#1E40AF"
    fig = go.Figure(go.Bar(x=values, y=list(range(len(labels))), orientation="h", customdata=custom,
        marker_color=colors, text=text, textfont=PRIMARY_FONT, textposition="outside", cliponaxis=False,
        hovertemplate=hover + "<extra></extra>",
        selected={"marker": {"opacity": 1}}, unselected={"marker": {"opacity": 0.65}}))
    maximum = max(values, default=0)
    fig.update_layout(height=max(220, min(1500, 42 * len(labels) + 60)), margin=dict(l=12, r=120, t=16, b=40),
        template="plotly_white", showlegend=False, clickmode="event+select", dragmode=False,
        font=PRIMARY_FONT,
        xaxis=dict(title=dict(text="응답 수 (건)" if measure == COUNT else "전체 응답 대비 (%)",
                              font=SECONDARY_FONT), tickfont=SECONDARY_FONT, rangemode="tozero",
                   range=[0, max(maximum * 1.12, 1)], ticksuffix="" if measure == COUNT else "%", fixedrange=True,
                   dtick=1 if measure == COUNT and maximum <= 10 else None),
        yaxis=dict(tickmode="array", tickvals=list(range(len(labels))), ticktext=safe_labels, tickfont=PRIMARY_FONT,
                   autorange="reversed", automargin=True, fixedrange=True), bargap=0.32)
    return fig



def result_bars_renderer():
    assets = Path(__file__).parent / "components"
    return st.components.v2.component("result_bars", html='<div class="result-bars"></div>',
        css=(assets / "result_bars.css").read_text(encoding="utf-8"),
        js=(assets / "result_bars.js").read_text(encoding="utf-8"), isolate_styles=False)


def show_dashboard(store, run, book, grouped, selected_ids, sentiment, prefix, originals, *, controls_container=None, basis="전체 기준"):
    """대분류에서 세부분류로 탐색하고 세부분류 원문을 연다."""
    if run["status"] != "completed":
        st.info("분류와 검토가 완료되면 전체 분포를 보여줍니다.")
        return
    if grouped.issues.empty:
        st.info(f"{sentiment} 응답에는 분류된 의견이 없습니다." if sentiment != "전체" else
                "분류할 의견이 없는 결과입니다. 고객 원문에서 응답 내용을 확인할 수 있습니다.")
        return
    controls = controls_container if controls_container is not None else st.container()
    with controls.container(key="result_display_controls", horizontal=True, horizontal_alignment="right", vertical_alignment="center", gap="small"):
        caption = st.container(width="content")
        settings = st.container(width="content")
    scope_label = "무응답 제외" if sentiment == "전체" and basis == "유효 기준" else sentiment
    caption.caption(f"{scope_label} 응답 {grouped.total_count}건 기준",
        help="분류별 고유 응답 수를 현재 감성의 전체 응답 수로 나눕니다. 대분류를 선택해도 분모는 유지하며, 한 응답에 여러 분류가 있으면 분류 비율의 합은 100%를 넘을 수 있습니다.")
    with settings.container(horizontal=True, horizontal_alignment="right"):
        current_limit = st.session_state.get(prefix + "_top_n", "전체")
        with st.popover("전체 분류" if current_limit == "전체" else f"상위 {current_limit}개", width="content", help="표시할 분류 수"):
            top_n = st.selectbox("표시할 분류 수", ["전체", 5, 10, 20, 50], key=prefix + "_top_n",
                help="마지막 순위와 응답 수가 같은 분류는 함께 표시합니다.")
    measure = COUNT
    layout_key = prefix + "_layout"
    if layout_key not in st.session_state:
        st.session_state[layout_key] = initial_layout(book["codes"])
    layout = st.session_state[layout_key]
    signature = chart_signature(run, layout, measure, top_n, st.session_state.get(prefix + "_chart_epoch", 0))
    signature += f":{basis}:{grouped.total_count}"
    notice = st.session_state.pop(prefix + "_chart_notice", None)
    if notice:
        st.info(notice)
    renderer = result_bars_renderer()
    drill = st.session_state.get(prefix + "_drill_categories", [])
    category_sentiment = st.session_state.get(prefix + "_category_sentiment") if drill else None
    code_group = grouped
    if drill and category_sentiment:
        from src.category_summary import category_sentiment_labels
        from src.grouping import group_results
        labels = category_sentiment_labels(originals, grouped.issues, drill)
        matching = labels.index[labels.eq(category_sentiment)]
        code_group = replace(group_results(originals[originals["VOC ID"].isin(matching)],
            grouped.issues[grouped.issues["VOC ID"].isin(matching)], book["codes"]),
            total_count=grouped.total_count)

    def show_chart(kind, title):
        frame = (drill_code_chart(code_group, book["codes"], layout, drill) if kind == "code" else
                 grouped_chart(grouped, book["codes"], layout, kind))
        if top_n != "전체":
            frame = frame.nlargest(top_n, COUNT, keep="all")
        extra = len(frame) - top_n if top_n != "전체" else 0
        if extra > 0:
            st.caption(f"동률로 {extra}개 더 표시했습니다.")
        rows = [{"id": row["id"], "label": row["분류"], "count": row[COUNT], "percent": row[PERCENT],
                 "value": row[measure], "members": row["members"], "code_ids": row["code_ids"],
                 "can_merge": bool(row.get("can_merge", True)),
                 "selected": kind == "category" and set(row["members"]) == set(drill)}
                for row in frame.to_dict("records")]
        result = renderer(key=f"{prefix}_bars_{kind}",
            data={"rows": rows, "title": title, "signature": signature, "denominator": grouped.total_count,
                  "maximum": grouped.total_count, "scope": scope_label,
                  "level": kind, "filtered": bool(drill)},
            on_action_change=lambda: None)
        action = result.action
        if action and action.get("nonce") != st.session_state.get(prefix + "_last_chart_action"):
            st.session_state[prefix + "_last_chart_action"] = action.get("nonce")
            try:
                following, members, open_popup = chart_action(store, run, layout, kind, frame, signature, action)
                st.session_state[layout_key] = following
                if kind == "category" and (action.get("kind") == "filter" or set(drill) != set(members)):
                    st.session_state[prefix + "_category_sentiment"] = None
                if kind == "category" and action.get("kind") == "filter":
                    st.session_state[prefix + "_drill_categories"] = [] if set(drill) == set(members) else members
                elif kind == "category":
                    st.session_state[prefix + "_drill_categories"] = members
                st.session_state[prefix + "_mode"] = "대분류로 묶기" if kind == "category" else "세부분류 직접 선택"
                st.session_state[prefix + ("_categories" if kind == "category" else "_codes")] = members
                st.session_state[prefix + "_filters"] = {}
                st.session_state.pop(prefix + "_selected_voc", None)
                st.session_state.pop(prefix + "_editing_voc", None)
                st.session_state[prefix + "_table_epoch"] = st.session_state.get(prefix + "_table_epoch", 0) + 1
                st.session_state[prefix + "_show_originals"] = open_popup
            except ValueError as exc:
                st.session_state[prefix + "_chart_notice"] = str(exc)
            st.session_state[prefix + "_chart_epoch"] = st.session_state.get(prefix + "_chart_epoch", 0) + 1
            st.rerun()

    with st.container(key="result_analysis"):
        category, code = st.columns([1, 1.15], gap="large")
    with category:
        with st.container(key="result_category_heading"):
            st.subheader("대분류", anchor=False)
        show_chart("category", "대분류")
    with code:
        with st.container(key="result_code_heading"):
            st.subheader("세부분류" + (" · " + "/".join(drill) if drill else ""), anchor=False)
            if drill:
                from src.category_summary import show_category_context
                show_category_context(store, run, originals, grouped.issues, drill, sentiment, prefix)
        if drill and category_sentiment and code_group.issues.empty:
            st.caption("현재 조건에 해당하는 세부분류가 없습니다.")
        show_chart("code", "세부분류")
