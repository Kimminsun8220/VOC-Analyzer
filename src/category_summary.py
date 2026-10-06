"""분류 상세의 두 비율 비교와 중복 없는 VOC 감성 구성."""

from html import escape
import textwrap

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.chart_data import COUNT, PERCENT
from src.charts_ui import PRIMARY_FONT, SECONDARY_FONT

OTHER_SENTIMENT = "무응답/중립/미검토"
SENTIMENT_COLORS = {"긍정": "#2563EB", "부정": "#C2410C", "혼합": "#7C3AED",
                    OTHER_SENTIMENT: "#CBD5E1"}
CHART_CONFIG = {"displayModeBar": False, "scrollZoom": False}


def response_sentiments(view, opinions):
    """현재 범위의 VOC를 긍정·부정·혼합·그 외로 한 번씩 센다."""
    by_voc = opinions.groupby("VOC ID")["감성"].agg(set).to_dict()
    counts = dict.fromkeys(SENTIMENT_COLORS, 0)
    records = view.drop_duplicates("VOC ID")
    for row in records.to_dict("records"):
        values = by_voc.get(row["VOC ID"], set())
        if {"긍정", "부정"}.issubset(values):
            label = "혼합"
        elif "긍정" in values:
            label = "긍정"
        elif "부정" in values:
            label = "부정"
        else:
            label = OTHER_SENTIMENT
        counts[label] += 1
    denominator = len(records)
    return pd.DataFrame([{"감성": label, COUNT: count, PERCENT: count / denominator * 100 if denominator else 0.0}
                         for label, count in counts.items()])


def comparison_figure(frame, overall_count):
    """진한 부분은 전체 비율, 연한 부분까지는 대분류 내 비율. 두 비율을 더하지 않는다."""
    rows = frame.to_dict("records")
    within = [float(row["대분류 내 비율 (%)"]) for row in rows]
    overall = [row[COUNT] / overall_count * 100 if overall_count else 0.0 for row in rows]
    labels = ["<br>".join(escape(part) for part in textwrap.wrap(str(row["분류"]), width=21)) for row in rows]
    custom = [[escape(str(row["분류"])), int(row[COUNT]), int(row["대분류 안 VOC 수"]),
               overall_count, within[index], overall[index]] for index, row in enumerate(rows)]
    hover = ("%{customdata[0]}<br>해당 응답 %{customdata[1]}건"
             "<br>대분류 내 %{customdata[4]:.1f}% · 대분류 응답 %{customdata[2]}건"
             "<br>전체 대비 %{customdata[5]:.1f}% · 전체 응답 %{customdata[3]}건<extra></extra>")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=overall, y=list(range(len(rows))), orientation="h", name="전체 응답 대비",
                         marker_color="#2563EB", customdata=custom, hovertemplate=hover))
    fig.add_trace(go.Bar(x=[max(0, a - b) for a, b in zip(within, overall, strict=True)],
                         y=list(range(len(rows))), orientation="h", name="대분류 내",
                         marker_color="#DBEAFE", customdata=custom, hovertemplate=hover))
    for index, (a, b) in enumerate(zip(within, overall, strict=True)):
        fig.add_annotation(x=b, y=index, text=f"{b:.1f}%", showarrow=False,
                           xanchor="right", xshift=-6, font={**PRIMARY_FONT, "color": "white"})
        fig.add_annotation(x=a, y=index, text=f"{a:.1f}%", showarrow=False,
                           xanchor="left", xshift=8, font=PRIMARY_FONT)
    fig.update_layout(template="plotly_white", barmode="stack", bargap=.46,
        height=max(280, len(rows) * 58 + 105), margin=dict(l=8, r=66, t=32, b=58), font=PRIMARY_FONT,
        hoverlabel=dict(bgcolor="white", bordercolor="#CBD5E1", font=PRIMARY_FONT),
        legend=dict(orientation="h", x=0, y=1.22, font=SECONDARY_FONT, itemclick=False, itemdoubleclick=False),
        xaxis=dict(range=[0, 106], showgrid=False, zeroline=False, showticklabels=False, fixedrange=True),
        yaxis=dict(tickmode="array", tickvals=list(range(len(rows))), ticktext=labels,
                   tickfont=PRIMARY_FONT, autorange="reversed", automargin=True, fixedrange=True))
    return fig


def sentiment_figure(frame, response_count, overall_count):
    populated = frame[frame[COUNT].gt(0)]
    custom = [[label, int(count), response_count, overall_count]
              for label, count in zip(populated["감성"], populated[COUNT], strict=True)]
    tooltips = [f"{label}<br>{count / response_count * 100:.1f}% · 응답 {count}건"
                f"<br>현재 응답 {response_count}건<br>전체 응답 {overall_count}건" for label, count, _, _ in custom]
    fig = go.Figure(go.Pie(labels=populated["감성"], values=populated[COUNT], hole=.48, sort=False,
        marker=dict(colors=[SENTIMENT_COLORS[label] for label in populated["감성"]], line=dict(color="white", width=3)),
        texttemplate="%{percent:.1%}", textposition="inside", textfont={**PRIMARY_FONT,
            "color": ["#0F172A" if label == OTHER_SENTIMENT else "white" for label in populated["감성"]]},
        customdata=custom, text=tooltips, hovertemplate="%{text}<extra></extra>"))
    fig.update_layout(template="plotly_white", font=PRIMARY_FONT, height=280, showlegend=True,
        hoverlabel=dict(bgcolor="white", bordercolor="#CBD5E1", font=PRIMARY_FONT),
        margin=dict(l=6, r=6, t=14, b=38),
        legend=dict(orientation="h", x=.5, xanchor="center", y=-.08, font=SECONDARY_FONT,
                    itemclick=False, itemdoubleclick=False))
    return fig


def show_sentiment_summary(view, opinions, overall_count, key):
    if view.empty:
        st.info("조건에 맞는 응답이 없습니다. 표의 필터를 해제해주세요.")
        return
    sentiments = response_sentiments(view, opinions)
    st.plotly_chart(sentiment_figure(sentiments, view["VOC ID"].nunique(), overall_count), width="stretch",
                    config=CHART_CONFIG, key=key)


def show_category_summary(data, view, opinions, overall_count, prefix):
    if view.empty:
        st.info("조건에 맞는 응답이 없습니다. 표의 필터를 해제해주세요.")
        return
    bars, pie = st.columns([3, 2])
    with bars:
        st.markdown("**세부분류 비율**")
        if data.codes.empty:
            st.info("표시할 분류가 없습니다.")
        else:
            st.plotly_chart(comparison_figure(data.codes, overall_count), width="stretch",
                            config=CHART_CONFIG, key=prefix + "_category_ratios")
    with pie:
        st.markdown("**감성 비중**")
        show_sentiment_summary(view, opinions, overall_count, prefix + "_category_sentiments")
