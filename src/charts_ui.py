"""공통 묶음 상태의 차트와 결과 해석에 필요한 집계 기준을 제공한다."""

from html import escape
from pathlib import Path
import textwrap

import plotly.graph_objects as go
import streamlit as st

from src.chart_data import COUNT, PERCENT, DENOMINATOR
from src.result_groups import initial_layout, grouped_chart, chart_signature, chart_action, change_layout

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


def show_dashboard(store, run, book, grouped, selected_ids, sentiment, prefix):
    """두 차트를 드래그로 묶고, 클릭한 범위의 원문 팝업을 연다."""
    if run["status"] != "completed":
        st.info("분류와 검토가 완료되면 전체 분포를 보여줍니다.")
        return
    if grouped.issues.empty:
        st.info(f"{sentiment} 응답에는 분류된 의견이 없습니다." if sentiment != "전체" else
                "분류할 의견이 없는 결과입니다. 고객 원문에서 응답 내용을 확인할 수 있습니다.")
        return
    caption, settings = st.columns([3, 2], vertical_alignment="center")
    caption.caption(f"{sentiment} 응답 {grouped.total_count}건 기준 · 클릭하면 원문 · 끌어 놓으면 합쳐 보기")
    with settings.container(horizontal=True, horizontal_alignment="right"):
        with st.popover("표시할 분류 수", width="content"):
            top_n = st.selectbox("표시할 분류 수", ["전체", 5, 10, 20, 50], key=prefix + "_top_n",
                help="마지막 순위와 응답 수가 같은 분류는 함께 표시합니다.")
    measure = COUNT
    layout_key = prefix + "_layout"
    if layout_key not in st.session_state:
        st.session_state[layout_key] = initial_layout(book["codes"])
    layout = st.session_state[layout_key]
    signature = chart_signature(run, layout, measure, top_n, st.session_state.get(prefix + "_chart_epoch", 0))
    notice = st.session_state.pop(prefix + "_chart_notice", None)
    if notice:
        st.info(notice)
    renderer = result_bars_renderer()

    def show_chart(kind, title):
        frame = grouped_chart(grouped, book["codes"], layout, kind)
        if top_n != "전체":
            frame = frame.nlargest(top_n, COUNT, keep="all")
        extra = len(frame) - top_n if top_n != "전체" else 0
        if extra > 0:
            st.caption(f"동률로 {extra}개 더 표시했습니다.")
        rows = [{"id": row["id"], "label": row["분류"], "count": row[COUNT], "percent": row[PERCENT],
                 "value": row[measure], "members": row["members"], "code_ids": row["code_ids"]}
                for row in frame.to_dict("records")]
        result = renderer(key=f"{prefix}_bars_{kind}",
            data={"rows": rows, "title": title, "signature": signature, "denominator": grouped.total_count,
                  "maximum": float(frame[measure].max()) if len(frame) else 0, "scope": sentiment},
            on_action_change=lambda: None)
        action = result.action
        if action and action.get("nonce") != st.session_state.get(prefix + "_last_chart_action"):
            st.session_state[prefix + "_last_chart_action"] = action.get("nonce")
            try:
                following, members, open_popup = chart_action(store, run, layout, kind, frame, signature, action)
                st.session_state[layout_key] = following
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

    category, code = st.columns(2, gap="large")
    with category:
        st.subheader("대분류", anchor=False)
        show_chart("category", "대분류")
    with code:
        st.subheader("세부분류", anchor=False)
        show_chart("code", "세부분류")
    if layout["history"]:
        if st.button("합치기 되돌리기", key=prefix + "_undo_group"):
            st.session_state[layout_key] = change_layout(layout, "undo")
            st.session_state[prefix + "_mode"] = "전체 보기"
            st.session_state[prefix + "_categories"] = []
            st.session_state[prefix + "_codes"] = []
            st.session_state[prefix + "_filters"] = {}
            st.session_state[prefix + "_table_epoch"] = st.session_state.get(prefix + "_table_epoch", 0) + 1
            st.session_state[prefix + "_show_originals"] = False
            st.session_state.pop(prefix + "_selected_voc", None)
            st.session_state.pop(prefix + "_editing_voc", None)
            st.rerun()
