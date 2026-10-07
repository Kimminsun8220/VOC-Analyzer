"""분류 상세의 두 비율 비교와 중복 없는 VOC 감성 구성."""

from html import escape
from hashlib import sha256
import json
import textwrap

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.chart_data import COUNT, PERCENT
from src.charts_ui import PRIMARY_FONT, SECONDARY_FONT, result_bars_renderer
from src.response_basis import NO_CONTENT

OTHER_SENTIMENT = "무응답/중립/미검토"
SENTIMENT_COLORS = {"긍정": "#238577", "부정": "#B45339", "혼합": "#7964A4",
                    OTHER_SENTIMENT: "#CBD5E1"}
CHART_CONFIG = {"displayModeBar": False, "scrollZoom": False}


def response_sentiment_labels(view, opinions):
    """집계와 클릭 조회에서 동일한 VOC별 감성 배정을 사용한다."""
    by_voc = opinions.groupby("VOC ID")["감성"].agg(set).to_dict()
    labels = {}
    records = view.drop_duplicates("VOC ID")
    for row in records.to_dict("records"):
        values = by_voc.get(row["VOC ID"], set()) if row.get("응답 상태", "의견 있음") == "의견 있음" else set()
        if {"긍정", "부정"}.issubset(values):
            label = "혼합"
        elif "긍정" in values:
            label = "긍정"
        elif "부정" in values:
            label = "부정"
        else:
            label = OTHER_SENTIMENT
        labels[row["VOC ID"]] = label
    return pd.Series(labels, dtype="object", name="감성")


def response_sentiments(view, opinions):
    """현재 범위의 VOC를 긍정·부정·혼합·그 외로 한 번씩 센다."""
    labels = response_sentiment_labels(view, opinions)
    counts = labels.value_counts().reindex(SENTIMENT_COLORS, fill_value=0)
    denominator = len(labels)
    return pd.DataFrame([{"감성": label, COUNT: count, PERCENT: count / denominator * 100 if denominator else 0.0}
                         for label, count in counts.items()])


def sentiment_response_ids(view, opinions, sentiment):
    if sentiment in ("무응답", "중립", "미검토"):
        labels = overview_sentiment_labels(view, opinions)
        return labels.index[labels.eq(sentiment)]
    labels = response_sentiment_labels(view, opinions)
    allowed = [sentiment, "혼합"] if sentiment in ("긍정", "부정") else [sentiment]
    return labels.index[labels.isin(allowed)]


def sentiment_mentions(view, opinions):
    total = view["VOC ID"].nunique()
    return pd.DataFrame([{"감성": label, COUNT: len(sentiment_response_ids(view, opinions, label)),
                         PERCENT: len(sentiment_response_ids(view, opinions, label)) / total * 100 if total else 0}
                        for label in ("긍정", "부정", OTHER_SENTIMENT)])


def overview_sentiment_labels(view, opinions):
    """무응답을 중립 의견과 분리하되 혼합 의견의 기존 규칙을 보존한다."""
    labels = response_sentiment_labels(view, opinions)
    statuses = view.drop_duplicates("VOC ID").set_index("VOC ID")["응답 상태"]
    sentiments = opinions.groupby("VOC ID")["감성"].agg(set)
    for identifier in labels.index[labels.eq(OTHER_SENTIMENT)]:
        if statuses[identifier] == NO_CONTENT:
            labels[identifier] = "무응답"
        elif statuses[identifier] == "의견 있음" and sentiments.get(identifier, set()) == {"중립"}:
            labels[identifier] = "중립"
        else:
            labels[identifier] = "미검토"
    return labels


def overview_mentions(view, opinions):
    labels = overview_sentiment_labels(view, opinions)
    total = len(labels)
    rows = []
    for label in ("긍정", "부정", "중립", "무응답", "미검토"):
        allowed = [label, "혼합"] if label in ("긍정", "부정") else [label]
        count = int(labels.isin(allowed).sum())
        rows.append({"감성": label, COUNT: count, PERCENT: count / total * 100 if total else 0.0})
    return pd.DataFrame(rows)


def category_sentiment_labels(view, opinions, categories):
    """대분류에 속한 의견으로 고유 응답의 감성을 배정한다."""
    scoped = opinions[opinions["대분류"].isin(categories)]
    return response_sentiment_labels(view[view["VOC ID"].isin(scoped["VOC ID"])], scoped)


def show_category_context(store, run, view, opinions, categories, sentiment, prefix):
    """별도 차트 섹션 없이 선택한 대분류의 맥락과 추가 필터를 제공한다."""
    labels = category_sentiment_labels(view, opinions, categories)
    selected = st.session_state.get(prefix + "_category_sentiment")
    count = int(labels.eq(selected).sum()) if selected else len(labels)
    title = " / ".join(categories)
    rows = []
    condition = None
    if sentiment != "전체" or selected:
        label = selected or sentiment
        condition = f"{title} · {'기타' if label == OTHER_SENTIMENT else label} 필터 적용 · {count}건"
    else:
        counts = labels.value_counts()
        for label in SENTIMENT_COLORS:
            value = int(counts.get(label, 0))
            if label == "혼합" and not value:
                continue
            rows.append({"id": label, "label": "기타" if label == OTHER_SENTIMENT else label,
                         "count": value, "percent": value / count * 100 if count else 0.0,
                         "color": SENTIMENT_COLORS[label]})
    signature = sha256(json.dumps([run["id"], run["result_revision"], categories, rows, condition,
        selected, st.session_state.get(prefix + "_chart_epoch", 0)], ensure_ascii=False).encode()).hexdigest()[:20]
    result = result_bars_renderer()(key=prefix + "_category_context",
        data={"variant": "category_context", "rows": rows, "title": title, "signature": signature,
              "denominator": count, "condition": condition, "selected": selected},
        on_action_change=lambda: None)
    action = result.action
    if not action or action.get("nonce") == st.session_state.get(prefix + "_last_context_action"):
        return
    st.session_state[prefix + "_last_context_action"] = action.get("nonce")
    if (action.get("signature") != signature or
            store.run(run["id"])["result_revision"] != run["result_revision"]):
        st.info("결과가 변경되었습니다. 새 화면에서 다시 선택해주세요.")
        return
    if action.get("kind") == "clear" and selected:
        following = None
    elif (action.get("kind") == "filter" and not condition and
          action.get("source_id") in {row["id"] for row in rows if row["count"]}):
        following = action["source_id"]
    else:
        return
    st.session_state[prefix + "_category_sentiment"] = following
    st.session_state[prefix + "_mode"] = "전체 보기"
    st.session_state[prefix + "_categories"] = []
    st.session_state[prefix + "_codes"] = []
    st.session_state[prefix + "_filters"] = {}
    st.session_state[prefix + "_show_originals"] = False
    st.session_state.pop(prefix + "_selected_voc", None)
    st.session_state.pop(prefix + "_editing_voc", None)
    for suffix in ("_chart_epoch", "_table_epoch"):
        st.session_state[prefix + suffix] = st.session_state.get(prefix + suffix, 0) + 1
    st.rerun()


def show_sentiment_overview(store, run, view, opinions, prefix, *, basis="전체 기준"):
    sentiments = overview_mentions(view, opinions)
    selected = st.session_state.get(prefix + "_dashboard_sentiment")
    rows = [{"id": row["감성"], "label": row["감성"], "count": int(row[COUNT]),
             "percent": float(row[PERCENT]), "color": SENTIMENT_COLORS.get(row["감성"], "#CBD5E1")}
            for row in sentiments.to_dict("records") if row[COUNT] or row["감성"] in ("긍정", "부정") or row["감성"] == selected]
    signature = sha256(json.dumps([run["id"], run["result_revision"], basis, len(view), rows,
        selected, st.session_state.get(prefix + "_chart_epoch", 0)], ensure_ascii=False).encode()).hexdigest()[:20]
    result = result_bars_renderer()(key=prefix + "_overall_sentiments",
        data={"variant": "sentiment", "rows": rows, "title": "긍정·부정 언급률", "signature": signature, "selected": selected,
              "basis": basis, "denominator": int(view["VOC ID"].nunique())}, on_action_change=lambda: None)
    action = result.action
    if not action or action.get("nonce") == st.session_state.get(prefix + "_last_sentiment_action"):
        return None
    st.session_state[prefix + "_last_sentiment_action"] = action.get("nonce")
    if (action.get("signature") != signature or
            store.run(run["id"])["result_revision"] != run["result_revision"]):
        st.info("결과가 변경되었습니다. 새 화면에서 다시 선택해주세요.")
    elif action.get("kind") == "filter" and action.get("source_id") in {row["id"] for row in rows}:
        return action["source_id"]
    return None


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
