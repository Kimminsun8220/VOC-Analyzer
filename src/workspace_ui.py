"""분석 작업 화면의 공통 표현. 데이터 처리와 분리된 디자인 토큰."""

from pathlib import Path

import streamlit as st


def apply_workspace_theme():
    css = (Path(__file__).parent / "components" / "workspace.css").read_text(encoding="utf-8")
    st.html(f"<style>{css}</style>")
