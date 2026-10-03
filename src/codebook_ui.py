"""기존 확정본을 남기고 새 버전으로 편집·통합·분리한다."""

import pandas as pd
from pydantic import ValidationError
import streamlit as st

from src.codebook_changes import edit_codebook, merge_codes, split_code


def show_codebook_changes(store, book, with_ai):
    prefix = f"revise_{book['id']}"

    def generate_definitions(*args):
        with st.spinner("AI가 입력한 이름에 맞는 분류 기준을 채우고 있습니다…"):
            return with_ai(lambda ai: ai.code_definitions(*args), model=book["model"])

    with st.expander("이 분류 기준표 수정·통합·분리"):
        st.caption("변경은 새 확정 버전으로 저장됩니다. 결과에 적용하려면 저장 후 ‘변경된 기준으로 다시 분류’를 실행하세요.")
        operation = st.radio("변경 방법", ["이름·기준·행 편집", "같은 의미 코드 통합", "코드 분리"], key=prefix + "_mode", horizontal=True)
        labels = {c.id: f"{c.category} → {c.name}" for c in book["codes"]}
        try:
            new_id = None
            if operation == "이름·기준·행 편집":
                st.caption("이름만 바꾸면 같은 코드로 승계합니다. 분류 기준 변경·삭제가 관련된 사용자 수정은 재분류 후 검토합니다.")
                frame = pd.DataFrame([{key: getattr(c, key) for key in ("id", "category", "name", "definition")} for c in book["codes"]],
                                     columns=["id", "category", "name", "definition"])
                edited = st.data_editor(frame, num_rows="dynamic", disabled=["id"], hide_index=True, width="stretch", key=prefix + "_editor",
                    column_config={"id": None, "category": "대분류", "name": "세부분류", "definition": "분류 기준"})
                if st.button("편집 내용으로 새 버전 저장", key=prefix + "_save"):
                    new_id = edit_codebook(store, book["id"], edited.astype(object).where(pd.notna(edited), None).to_dict("records"))
            elif operation == "같은 의미 코드 통합":
                st.info("정의가 같은 코드를 하나로 정리합니다. 오배송과 배송 속도처럼 다른 의미를 함께 조회할 때는 결과 화면의 묶어보기를 사용하세요.")
                selected = st.multiselect("통합할 세부분류", list(labels), format_func=labels.get, key=prefix + "_merge_ids")
                st.dataframe(pd.DataFrame([{"분류": labels[c.id], "기준": c.definition} for c in book["codes"] if c.id in selected]),
                             hide_index=True, width="stretch")
                name = st.text_input("통합 후 이름", key=prefix + "_merge_name")
                definition = st.text_area("통합 후 분류 기준 (선택)", key=prefix + "_merge_definition",
                    help="비워두면 저장할 때 AI가 기존 기준과 원문을 참고해 채웁니다. 직접 입력한 기준은 그대로 사용합니다.")
                st.caption("통합 후 이름만 입력해 저장할 수 있습니다. 빈 분류 기준은 AI가 작성합니다.")
                if st.button("통합한 새 버전 저장", key=prefix + "_merge", disabled=len(selected) < 2):
                    new_id = merge_codes(store, book["id"], selected, name, definition, generate_definitions=generate_definitions)
            elif labels:
                source = st.selectbox("분리할 세부분류", list(labels), format_func=labels.get, key=prefix + "_split_id")
                code = next(c for c in book["codes"] if c.id == source)
                st.write(code.definition)
                st.caption(f"대분류는 기존 코드의 ‘{code.category}’로 유지됩니다.")
                st.caption("새 세부분류 이름을 두 개 이상 입력하세요. 빈 분류 기준은 저장할 때 AI가 작성하며, 직접 쓴 기준은 유지합니다.")
                st.caption("기존 코드가 연결된 사용자 수정은 재분류 뒤 확인합니다.")
                edited = st.data_editor(pd.DataFrame([{"name": "", "definition": ""}] * 2), num_rows="dynamic", hide_index=True,
                    width="stretch", key=f"{prefix}_{source}_split_editor", column_config={
                        "name": st.column_config.TextColumn("새 세부분류", required=True, max_chars=80),
                        "definition": st.column_config.TextColumn("분류 기준 (선택)", max_chars=1500,
                            help="비워두면 AI가 기존 기준과 원문을 참고해 채웁니다.")})
                if st.button("분리한 새 버전 저장", key=prefix + "_split"):
                    new_id = split_code(store, book["id"], source, edited.astype(object).where(pd.notna(edited), None).to_dict("records"),
                        generate_definitions=generate_definitions)
            else:
                st.info("분리할 코드가 없습니다. 먼저 코드를 추가해주세요.")
            if new_id:
                st.session_state.confirmed_book = new_id
                st.session_state["book_notice"] = "새 분류 기준표 버전을 저장했습니다. 아래에서 이전 분석을 선택하고 ‘변경된 기준으로 다시 분류’를 누르세요."
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
