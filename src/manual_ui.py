"""Customer manual viewer; the editable PowerPoint is kept separately."""

from pathlib import Path
from base64 import b64encode

import streamlit as st


MANUAL_PATH = Path(__file__).resolve().parents[1] / "static" / "manual" / "VOC_Analyzer_사용_매뉴얼.pdf"


@st.dialog("사용 매뉴얼", width="large")
def show_manual():
    try:
        pdf = MANUAL_PATH.read_bytes()
    except OSError:
        st.warning("매뉴얼을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
        return
    with st.container(key="manual_viewer"):
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.download_button("다운로드", pdf, file_name=MANUAL_PATH.name,
                               mime="application/pdf", icon=":material/download:",
                               key="manual_download", on_click="ignore")
        # Preview the PDF's rendered pages without a fixed-size PDF canvas.
        pages = sorted((MANUAL_PATH.parent / "pages").glob("*.png"))
        if not pages:
            st.info("다운로드 버튼으로 매뉴얼을 확인해 주세요.")
            return
        images = "".join(
            f'<img src="data:image/png;base64,{b64encode(page.read_bytes()).decode()}" '
            f'alt="매뉴얼 {index}쪽" loading="lazy">'
            for index, page in enumerate(pages, 1)
        )
        st.html(f'<div class="manual-pages" tabindex="0" role="region" '
                f'aria-label="매뉴얼 페이지">{images}</div>')
