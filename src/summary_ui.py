"""차트보다 앞서 읽는 집계 정보를 작은 요약으로 표시한다."""

import streamlit as st


def show_compact_metrics(items, key, muted_labels=()):
    with st.container(key=key):
        st.html(f"""<style>
            .st-key-{key} [data-testid="stMetricValue"] {{
                font-size: 18px; font-weight: 600; line-height: 1.4;
            }}
        </style>""")
        for index, (column, (label, value, help_text)) in enumerate(zip(st.columns(len(items)), items)):
            with column, st.container(key=f"{key}_{index}"):
                if label in muted_labels:
                    st.html(f"""<style>
                        .st-key-{key}_{index} [data-testid="stMetricValue"] {{
                            font-size: 16px; font-weight: 400; color: #64748B;
                        }}
                    </style>""")
                st.metric(label, value, help=help_text)
