"""VOC 수정·저장·취소와 승계 예외 확인에 필요한 조작을 제공한다."""

from datetime import datetime, timedelta, timezone

import pandas as pd
from pydantic import ValidationError
import streamlit as st

from src.corrections import save_correction
from src.models import CodingResult

LABELS = {"item_id": "의견 ID", "code_id": "분류", "sentiment": "감성", "evidence_text": "원문 근거",
          "context_evidence_text": "배경 근거", "subject_label": "대상·역할", "subject_evidence_text": "대상 근거",
          "subject_evidence_source": "대상 근거 출처"}


def issue_rows(result):
    return [{"item_id": str(index), **{key: value for key, value in item.model_dump().items() if key != "missing_code"}}
            for index, item in enumerate(result.issues)]


def show_corrections(store, run, book, dataset, voc_id=None, close_editor=None, include_history=True):
    st.subheader("응답 수정", anchor=False)
    if run["status"] not in {"completed", "needs_review"}:
        st.info("분류를 완료하거나 실패·미처리 응답을 재시도한 뒤 수정할 수 있습니다.")
        return
    corrections = store.corrections(run["id"])
    raw = {row["voc_id"]: row for row in store.results(run["id"]) if row["status"] == "success"}
    effective = {row["voc_id"]: row for row in store.effective_results(run["id"])}
    records = {row["id"]: row for row in dataset["records"]}
    review_ids = [identifier for identifier, row in effective.items() if row["status"] == "review"]
    prefix = f"correction_{run['id']}_{run['round']}"
    if review_ids:
        st.warning(f"이전 수정 내용을 확인할 응답 {len(review_ids)}건이 있습니다. 확인 전 결과는 집계에 포함하지 않습니다.")
    options = review_ids + [identifier for identifier in raw if identifier not in review_ids]
    if not options:
        st.info("수정할 저장 결과가 없습니다.")
        return
    if voc_id is None:
        voc_id = st.selectbox("수정할 응답", options, key=prefix + "_voc", format_func=lambda identifier:
            f"{'[확인 필요] ' if identifier in review_ids else ''}{records[identifier]['text'][:90] or '(빈 본문)'}")
        st.text(records[voc_id]["text"] or "(빈 본문)")
    if voc_id not in options:
        st.info("처리 실패·미처리 응답은 먼저 이어서 실행해주세요.")
        return
    previous = corrections.get(voc_id)
    resolving = voc_id in review_ids
    current = CodingResult.model_validate(raw[voc_id]["result"] if resolving else effective[voc_id]["result"])
    if resolving:
        st.warning(previous["reason"])
        with st.expander("이전 수정과 새 분류 결과 비교", expanded=True):
            st.write("**이전 수정 기록**")
            source = previous["previous"]
            source_result = CodingResult.model_validate(source["result"])
            source_labels = {c.id: f"[{c.category}] {c.name}" for c in store.codebook(source["codebook_id"])["codes"]}
            source_frame = pd.DataFrame(issue_rows(source_result), columns=list(LABELS)).drop(columns="item_id")
            source_frame["code_id"] = source_frame["code_id"].map(source_labels)
            st.write("내용 없음·무응답" if source_result.response_type == "no_content" else "의견 있음")
            if source_result.no_content_reason:
                st.text(source_result.no_content_reason)
            st.dataframe(source_frame.rename(columns=LABELS), hide_index=True, width="stretch")
            st.write("**새 분류 결과**")
            candidate_frame = pd.DataFrame(issue_rows(current), columns=list(LABELS)).drop(columns="item_id")
            candidate_frame["code_id"] = candidate_frame["code_id"].map({c.id: f"[{c.category}] {c.name}" for c in book["codes"]})
            st.dataframe(candidate_frame.rename(columns=LABELS), hide_index=True, width="stretch")
        st.caption("재분류 결과를 확인한 뒤 저장해주세요.")
    with st.expander("현재 분류 기준표의 분류 기준 확인"):
        st.dataframe(pd.DataFrame([{"대분류": c.category, "세부분류": c.name, "기준": c.definition} for c in book["codes"]]),
                     hide_index=True, width="stretch")
    nonce_key = prefix + "_nonce"
    edit_key = f"{prefix}_{voc_id}_{run['result_revision']}_{st.session_state.get(nonce_key, 0)}"
    state_labels = {"opinions": "의견 있음", "no_content": "내용 없음·무응답"}
    response_type = st.radio("응답 상태", list(state_labels), format_func=state_labels.get, horizontal=True,
        index=1 if current.response_type == "no_content" else 0, key=edit_key + "_state")
    with st.form(edit_key + "_form"):
        frame = pd.DataFrame(issue_rows(current), columns=list(LABELS))
        labels = {code.id: f"[{code.category}] {code.name}" for code in book["codes"]}
        rows, no_content_reason = [], ""
        if response_type == "opinions":
            with st.expander("편집 방법"):
                st.markdown("- 수정: 셀 더블클릭\n- 삭제: 행 왼쪽 체크박스 → 휴지통\n- 추가: ＋\n- 원문 근거: 위 원문에서 인용\n- 대상·역할: 확인되지 않으면 빈칸")
            frame["code_id"] = frame["code_id"].map(labels)
            frame["subject_evidence_source"] = frame["subject_evidence_source"].map({"original": "원문", "context": "배경"})
            edited = st.data_editor(frame, num_rows="dynamic", disabled=["item_id"], hide_index=True, width="stretch",
                column_order=["code_id", "sentiment", "evidence_text"],
                key=edit_key + "_editor", column_config={
                    **{key: st.column_config.TextColumn(label) for key, label in LABELS.items()}, "item_id": None,
                    "code_id": st.column_config.SelectboxColumn("분류", options=list(labels.values()), required=True, width="medium"),
                    "sentiment": st.column_config.SelectboxColumn("감성", options=["긍정", "부정", "중립", "판단 불가"], required=True),
                    "evidence_text": st.column_config.TextColumn("원문 근거", required=True, width="large"),
                    "subject_evidence_source": st.column_config.SelectboxColumn("대상 근거 출처", options=["원문", "배경"]),
                })
            detail_fields = ["context_evidence_text", "subject_label", "subject_evidence_text", "subject_evidence_source"]
            with st.expander("상세 근거 수정"):
                details = st.data_editor(frame[["item_id", "code_id", *detail_fields]], hide_index=True, width="stretch",
                    disabled=["item_id", "code_id"], key=edit_key + "_details_editor", column_config={
                        "item_id": None, **{key: st.column_config.TextColumn(LABELS[key]) for key in ["code_id", *detail_fields]},
                        "subject_evidence_source": st.column_config.SelectboxColumn("대상 근거 출처", options=["원문", "배경"]),
                    })
            detail_map = {item["item_id"]: item for item in details.astype(object).where(pd.notna(details), None).to_dict("records")}
            inverse = {label: identifier for identifier, label in labels.items()}
            for row in edited.astype(object).where(pd.notna(edited), None).to_dict("records"):
                if row["item_id"] in detail_map:
                    for field in detail_fields:
                        row[field] = detail_map[row["item_id"]][field]
                row["code_id"] = inverse.get(row["code_id"])
                row["subject_evidence_source"] = {"원문": "original", "배경": "context"}.get(row["subject_evidence_source"])
                for key in ("context_evidence_text", "subject_evidence_text"):
                    row[key] = row[key] or ""
                row["subject_label"] = row["subject_label"] or None
                rows.append(row)
        else:
            no_content_reason = st.text_input("내용 없는 응답으로 판단한 이유", value=current.no_content_reason,
                                              key=edit_key + "_empty_reason")
        reason = st.text_input("수정·검토 이유", placeholder="예: 배송 속도에 관한 중립 의견으로 정정", key=edit_key + "_reason")
        left, right = st.columns(2)
        save = left.form_submit_button("확인하고 저장" if resolving else "수정 저장", type="primary", width="stretch")
        cancel = right.form_submit_button("편집 취소", width="stretch")
    if cancel:
        st.session_state[nonce_key] = st.session_state.get(nonce_key, 0) + 1
        if close_editor:
            close_editor()
        st.rerun()
    if save:
        try:
            save_correction(store, run["id"], voc_id, rows, response_type, reason, run["result_revision"], no_content_reason)
            st.session_state["correction_notice"] = "응답 수정 저장 완료"
            if close_editor:
                close_editor()
            st.rerun()
        except ValidationError:
            st.error("분류·감성·원문 근거를 빠짐없이 입력해주세요. 의견이 없는 경우 응답 상태를 명시적으로 바꿔주세요.")
        except ValueError as exc:
            st.error(str(exc))
    if include_history:
        show_history(store, run, book, voc_id)


def display_result(result, book):
    if result.response_type == "no_content":
        st.caption(f"내용 없는 응답 · {result.no_content_reason}")
        return
    labels = {code.id: f"[{code.category}] {code.name}" for code in book["codes"]}
    for issue in result.issues:
        st.markdown(f"**{labels.get(issue.code_id, '분류 확인 필요')} · {issue.sentiment}**")
        st.text(f"근거: {issue.evidence_text}")
        if issue.context_evidence_text or issue.subject_label:
            with st.expander("대상·배경 근거"):
                if issue.subject_label:
                    st.text(f"대상: {issue.subject_label} · {issue.subject_evidence_text}")
                if issue.context_evidence_text:
                    st.text(f"배경: {issue.context_evidence_text}")
    if result.review_reason:
        st.warning(result.review_reason)


def show_history(store, run, book, voc_id):
    with st.expander("수정 이력", expanded=False):
        raw = next((row for row in store.results(run["id"]) if row["voc_id"] == voc_id and row["status"] == "success"), None)
        if raw:
            st.markdown("**최초 분류**")
            display_result(CodingResult.model_validate(raw["result"]), book)
        history = [row for row in store.correction_history(run["id"]) if row["voc_id"] == voc_id]
        for row in history:
            moment = datetime.fromisoformat(row["created_at"]).astimezone(timezone(timedelta(hours=9)))
            st.markdown(f"**{moment:%Y-%m-%d %H:%M} · {row['reason']}**")
            if row["result"]:
                display_result(CodingResult.model_validate(row["result"]), store.codebook(row["codebook_id"]))
            elif row["previous"]:
                st.caption("새 기준에 적용하기 전의 수정 내용")
                previous = row["previous"]
                display_result(CodingResult.model_validate(previous["result"]), store.codebook(previous["codebook_id"]))
        if not history:
            st.caption("수정한 기록이 없습니다.")


def show_response_detail(store, run, book, dataset, voc_id, editing_key, allow_edit=True, include_history=True):
    record = next(item for item in dataset["records"] if item["id"] == voc_id)
    st.subheader("응답 상세", anchor=False)
    st.text(record["text"] or "(빈 본문)")
    row = next((item for item in store.effective_results(run["id"]) if item["voc_id"] == voc_id), None)
    if not row or row["status"] == "failed":
        st.info("분류하지 못한 응답입니다. 실패·미처리 이어서 실행으로 다시 처리해주세요.")
        return
    raw = next((item for item in store.results(run["id"]) if item["voc_id"] == voc_id and item["status"] == "success"), None)
    if not raw:
        st.info("분류 처리 후 수정할 수 있습니다.")
        return

    def close():
        st.session_state.pop(editing_key, None)

    if not allow_edit:
        close()
    if st.session_state.get(editing_key) == voc_id:
        show_corrections(store, run, book, dataset, voc_id, close, include_history=include_history)
        return
    if row["status"] == "review":
        st.warning("이전 수정 내용과 새 분류 결과를 확인해주세요. 확인 전에는 집계에 포함하지 않습니다.")
    else:
        display_result(CodingResult.model_validate(row["result"]), book)
    if allow_edit and run["status"] in {"completed", "needs_review"}:
        if st.button("확인·수정" if row["status"] == "review" else "이 응답 수정", key=editing_key + "_open"):
            st.session_state[editing_key] = voc_id
            st.rerun()
    elif allow_edit:
        st.caption("분류 처리를 완료한 뒤 수정할 수 있습니다.")
    if include_history:
        show_history(store, run, book, voc_id)
