"""새 버전의 편집·통합·분리 조작과 결과 적용에 필요한 안내를 제공한다."""

import pandas as pd
from pydantic import ValidationError
import streamlit as st

from src.codebook_changes import merge_codes, split_code
from src.codebook_table import save_table_draft
from src.codebook_table_ui import interactive_table


def show_codebook_changes(store, book, with_ai):
    prefix = f"revise_{book['id']}"

    def generate_definitions(*args):
        with st.spinner("AI가 입력한 이름에 맞는 분류 기준을 채우고 있습니다…"):
            return with_ai(lambda ai: ai.code_definitions(*args), model=book["model"])

    with st.container():
        operation = st.radio("작업", ["표 편집", "분류 통합", "분류 분리"], key=prefix + "_mode", horizontal=True)
        labels = {c.id: f"{c.category} → {c.name}" for c in book["codes"]}
        try:
            new_id = None
            if operation == "표 편집":
                with st.expander("편집 방법"):
                    st.markdown("- 수정: 셀 클릭 후 입력\n- 합치기: 행 왼쪽 손잡이를 다른 행에 끌어 놓기\n- 분리·삭제: 행 우클릭 또는 오른쪽 메뉴\n- 추가·취소: 행 추가·되돌리기\n- 변경 내용 저장을 눌러 확정")
                draft, save_requested = interactive_table(book)
                save_clicked = st.button("변경 내용 저장", key=prefix + "_save", type="primary")
                if save_requested or save_clicked:
                    new_id = save_table_draft(store, book, draft, generate_definitions=generate_definitions)
            elif operation == "분류 통합":
                st.caption("같은 의미의 분류만 통합하세요. 함께 조회하려면 결과 화면의 묶어보기를 사용하세요.")
                selected = st.multiselect("통합할 세부분류", list(labels), format_func=labels.get, key=prefix + "_merge_ids")
                st.dataframe(pd.DataFrame([{"분류": labels[c.id], "기준": c.definition} for c in book["codes"] if c.id in selected]),
                             hide_index=True, width="stretch")
                name = st.text_input("통합 후 이름", key=prefix + "_merge_name")
                definition = st.text_area("통합 후 분류 기준 (선택)", key=prefix + "_merge_definition",
                    help="비워두면 저장할 때 AI가 기존 기준과 원문을 참고해 채웁니다. 직접 입력한 기준은 그대로 사용합니다.")
                if st.button("통합한 새 버전 저장", key=prefix + "_merge", disabled=len(selected) < 2):
                    new_id = merge_codes(store, book["id"], selected, name, definition, generate_definitions=generate_definitions)
            elif labels:
                source = st.selectbox("분리할 세부분류", list(labels), format_func=labels.get, key=prefix + "_split_id")
                code = next(c for c in book["codes"] if c.id == source)
                st.write(code.definition)
                st.caption(f"대분류: {code.category} · 새 세부분류를 두 개 이상 입력하세요.")
                edited = st.data_editor(pd.DataFrame([{"name": "", "definition": ""}] * 2), num_rows="dynamic", hide_index=True,
                    width="stretch", key=f"{prefix}_{source}_split_editor", column_config={
                        "name": st.column_config.TextColumn("새 세부분류", required=True, max_chars=80),
                        "definition": st.column_config.TextColumn("분류 기준 (선택)", max_chars=1500,
                            help="비워두면 AI가 기존 기준과 원문을 참고해 채웁니다.")})
                if st.button("분리한 새 버전 저장", key=prefix + "_split"):
                    new_id = split_code(store, book["id"], source, edited.astype(object).where(pd.notna(edited), None).to_dict("records"),
                        generate_definitions=generate_definitions)
            else:
                st.info("분리할 분류가 없습니다. 먼저 분류를 추가해주세요.")
            st.caption("변경된 기준은 저장 후 다시 분류하면 결과에 반영됩니다.")
            if new_id:
                st.session_state.confirmed_book = new_id
                st.session_state["book_notice"] = "분류 기준표 저장 완료"
                st.rerun()
        except ValidationError as exc:
            fields = {"category": "대분류", "name": "세부분류 이름", "definition": "분류 기준"}
            messages = []
            for error in exc.errors(include_input=False, include_url=False):
                label = fields.get(error["loc"][0], "분류 기준표 항목")
                if error["type"] == "string_too_long":
                    messages.append(f"{label}은 {error['ctx']['max_length']}자 이하로 입력해주세요.")
                else:
                    messages.append(f"{label}을 빠짐없이 입력해주세요.")
            st.error(" ".join(dict.fromkeys(messages)))
        except ValueError as exc:
            st.error(str(exc))
