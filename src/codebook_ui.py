"""기존 확정본을 남기고 새 버전으로 편집·통합·분리한다."""

import pandas as pd
from pydantic import ValidationError
import streamlit as st

from src.codebook_changes import edit_codebook, merge_codes, split_code


def show_codebook_changes(store, book):
    prefix = f"revise_{book['id']}"
    with st.expander("이 코드북 수정·통합·분리"):
        st.caption("변경은 새 확정 버전으로 저장됩니다. 결과에 적용하려면 저장 후 전체 분류를 실행하세요.")
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
                    new_id = edit_codebook(store, book["id"], edited.where(pd.notna(edited), None).to_dict("records"))
            elif operation == "같은 의미 코드 통합":
                st.info("정의가 같은 코드를 하나로 정리합니다. 오배송과 배송 속도처럼 다른 의미를 함께 조회할 때는 결과 화면의 묶어보기를 사용하세요.")
                selected = st.multiselect("통합할 세부분류", list(labels), format_func=labels.get, key=prefix + "_merge_ids")
                st.dataframe(pd.DataFrame([{"분류": labels[c.id], "기준": c.definition} for c in book["codes"] if c.id in selected]),
                             hide_index=True, width="stretch")
                name = st.text_input("통합 후 이름", key=prefix + "_merge_name")
                definition = st.text_area("통합 후 분류 기준", key=prefix + "_merge_definition")
                same = st.checkbox("정의와 원문을 확인했고, 같은 의미의 코드입니다", key=prefix + "_same")
                if st.button("통합한 새 버전 저장", key=prefix + "_merge", disabled=len(selected) < 2 or not same):
                    new_id = merge_codes(store, book["id"], selected, name, definition, same)
            elif labels:
                source = st.selectbox("분리할 세부분류", list(labels), format_func=labels.get, key=prefix + "_split_id")
                code = next(c for c in book["codes"] if c.id == source)
                st.write(code.definition)
                st.caption("새 세부분류를 두 개 이상 입력하세요. 기존 코드가 연결된 사용자 수정은 재분류 뒤 확인합니다.")
                edited = st.data_editor(pd.DataFrame([{"name": "", "definition": ""}] * 2), num_rows="dynamic", hide_index=True,
                    width="stretch", key=f"{prefix}_{source}_split_editor", column_config={"name": "새 세부분류", "definition": "분류 기준"})
                if st.button("분리한 새 버전 저장", key=prefix + "_split"):
                    new_id = split_code(store, book["id"], source, edited.where(pd.notna(edited), None).to_dict("records"))
            else:
                st.info("분리할 코드가 없습니다. 먼저 코드를 추가해주세요.")
            if new_id:
                st.session_state.confirmed_book = new_id
                st.session_state["book_notice"] = "새 코드북 버전을 저장했습니다. 아래에서 이전 수정값을 가져올 실행을 선택하고 전체 분류를 시작하세요."
                st.rerun()
        except ValidationError:
            st.error("대분류·세부분류 이름·분류 기준을 빠짐없이 입력해주세요.")
        except ValueError as exc:
            st.error(str(exc))
