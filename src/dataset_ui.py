"""저장된 입력 자료의 정보 확인·이름 변경·삭제를 제공한다."""

from datetime import datetime, timedelta, timezone
import sqlite3

import streamlit as st


def clear_deleted_dataset_state():
    identifiers = st.session_state.pop("deleted_dataset_state_ids", [])
    if identifiers:
        for key in list(st.session_state):
            if any(identifier in key for identifier in identifiers):
                del st.session_state[key]
        for key in ("confirmed_book", "run_id", "book_notice", "correction_notice", "pending_delete_dataset"):
            st.session_state.pop(key, None)


def show_dataset_management(store, identifier):
    notice = st.session_state.pop("dataset_notice", None)
    if notice:
        st.success(notice)
    if st.session_state.get("pending_delete_dataset") != identifier:
        st.session_state.pop("pending_delete_dataset", None)
    with st.expander("입력 자료 관리"):
        try:
            summary = store.dataset_summary(identifier)
            created = datetime.fromisoformat(summary["created_at"]).astimezone(timezone(timedelta(hours=9)))
            st.caption(f"저장: {created:%Y-%m-%d %H:%M} KST · {summary['input_type']}")
            st.caption(f"응답 {summary['record_count']}건 · 분류 기준표 {summary['codebook_count']}개 · 분석 {summary['run_count']}회")
            with st.form(f"dataset_rename_{identifier}"):
                name = st.text_input("자료 이름", value=summary["name"], max_chars=100, key=f"dataset_name_{identifier}")
                if st.form_submit_button("이름 저장", key=f"dataset_rename_save_{identifier}", width="stretch"):
                    store.rename_dataset(identifier, name)
                    st.session_state.dataset_notice = "자료 이름을 저장했습니다."
                    st.rerun()
            st.divider()
            if summary["analysis_running"]:
                st.info("분류가 진행 중입니다. 완료 후 자료를 삭제할 수 있습니다.")
            if st.session_state.get("pending_delete_dataset") == identifier:
                st.warning(f"‘{summary['name']}’ 입력 자료와 연결된 분석 정보를 삭제합니다. 삭제 후 복구할 수 없습니다.")
                st.caption(f"함께 삭제: 분류 기준표 {summary['codebook_count']}개 · 분석 {summary['run_count']}회 · "
                           f"수정 이력 {summary['correction_count']}건 · 저장 묶음 {summary['group_count']}개")
                confirm, cancel = st.columns(2)
                if confirm.button("삭제 확인", key=f"dataset_delete_confirm_{identifier}", type="primary",
                                  disabled=bool(summary["analysis_running"]), width="stretch"):
                    identifiers = [identifier, *[book["id"] for book in store.list_codebooks(identifier)],
                                   *[run["id"] for run in store.list_runs(identifier)]]
                    store.delete_dataset(identifier)
                    st.session_state.deleted_dataset_state_ids = identifiers
                    remaining = store.list_datasets()
                    if remaining:
                        st.session_state.pending_dataset = remaining[0]["id"]
                        st.session_state.dataset_notice = "입력 자료와 연결된 분석 정보를 삭제했습니다."
                    else:
                        st.session_state.page = "1. 입력"
                        st.session_state.dataset_notice = "입력 자료와 연결된 분석 정보를 삭제했습니다. 새 자료를 입력해주세요."
                    st.rerun()
                if cancel.button("취소", key=f"dataset_delete_cancel_{identifier}", width="stretch"):
                    st.session_state.pop("pending_delete_dataset", None)
                    st.rerun()
            elif st.button("자료 삭제", key=f"dataset_delete_{identifier}", disabled=bool(summary["analysis_running"]), width="stretch"):
                st.session_state.pending_delete_dataset = identifier
                st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except (sqlite3.Error, OSError):
            st.error("자료를 변경하지 못했습니다. 잠시 후 다시 시도해주세요.")
