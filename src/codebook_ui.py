"""새 버전의 편집·통합·분리 조작과 결과 적용에 필요한 안내를 제공한다."""

from copy import deepcopy

from pydantic import ValidationError
import streamlit as st

from src.codebook_table import save_table_draft, table_draft
from src.codebook_table_ui import interactive_table


def show_codebook_changes(store, book, with_ai, save_container=None, save_disabled=False):
    prefix = f"revise_{book['id']}"

    def generate_definitions(*args):
        with st.spinner("AI가 입력한 이름에 맞는 분류 기준을 채우고 있습니다…"):
            return with_ai(lambda ai: ai.code_definitions(*args), model=book["model"])

    with st.container():
        try:
            new_id = None
            draft, save_requested = interactive_table(book)
            save_target = save_container if save_container is not None else st
            is_draft = book["status"] == "draft"
            save_clicked = save_target.button("분류 기준표 저장", key="confirm_codebook" if is_draft else prefix + "_save",
                type="primary" if is_draft else "secondary", disabled=save_disabled)
            if (save_requested or save_clicked) and not save_disabled:
                new_id = save_table_draft(store, book, draft, generate_definitions=generate_definitions)
            if new_id:
                following = table_draft(store.codebook(new_id))
                following["filters"] = deepcopy(draft["filters"])
                following["kept_rows"] = list(draft["kept_rows"])
                st.session_state[f"revise_{new_id}_draft"] = following
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
