"""한 VOC를 명시적인 저장/취소 조작으로 수정하고 승계 예외를 확인한다."""

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


def show_corrections(store, run, book, dataset):
    st.subheader("VOC 한 건 수정")
    st.caption("코드·감성과 근거를 고치거나 의견을 추가·삭제할 수 있습니다. 저장하면 묶음 집계와 원문 표에도 반영됩니다.")
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
        st.warning(f"수정값 승계를 확인할 VOC {len(review_ids)}건이 있습니다. 이 건들의 AI 후보는 집계에 넣지 않았습니다.")
    options = review_ids + [identifier for identifier in raw if identifier not in review_ids]
    if not options:
        st.info("수정할 저장 결과가 없습니다.")
        return
    voc_id = st.selectbox("수정할 VOC", options, key=prefix + "_voc", format_func=lambda identifier:
        f"{'[승계 검토] ' if identifier in review_ids else ''}{identifier} · {records[identifier]['text'][:90] or '(빈 본문)'}")
    st.text(records[voc_id]["text"] or "(빈 본문)")
    previous = corrections.get(voc_id)
    resolving = voc_id in review_ids
    current = CodingResult.model_validate(raw[voc_id]["result"] if resolving else effective[voc_id]["result"])
    if resolving:
        st.warning(previous["reason"])
        with st.expander("이전 사용자 수정과 새 AI 후보 비교", expanded=True):
            st.write("**이전 수정 기록**")
            source = previous["previous"]
            source_result = CodingResult.model_validate(source["result"])
            source_labels = {c.id: f"{c.category} → {c.name}" for c in store.codebook(source["codebook_id"])["codes"]}
            source_frame = pd.DataFrame(issue_rows(source_result), columns=list(LABELS)).drop(columns="item_id")
            source_frame["code_id"] = source_frame["code_id"].map(source_labels)
            st.write("내용 없음·무응답" if source_result.response_type == "no_content" else "의견 있음")
            if source_result.no_content_reason:
                st.text(source_result.no_content_reason)
            st.dataframe(source_frame.rename(columns=LABELS), hide_index=True, width="stretch")
            st.write("**새 분류 기준표의 AI 후보**")
            candidate_frame = pd.DataFrame(issue_rows(current), columns=list(LABELS)).drop(columns="item_id")
            candidate_frame["code_id"] = candidate_frame["code_id"].map({c.id: f"{c.category} → {c.name}" for c in book["codes"]})
            st.dataframe(candidate_frame.rename(columns=LABELS), hide_index=True, width="stretch")
        st.caption("아래 목록은 새 AI 후보입니다. 이전 수정 의도를 확인해 현재 기준으로 고친 뒤 저장하면 이 건의 검토가 완료됩니다.")
    else:
        st.caption(f"현재 출처: {effective[voc_id]['source']} · 결과 개정 {run['result_revision']}")
    with st.expander("현재 분류 기준표 확인"):
        st.dataframe(pd.DataFrame([{"대분류": c.category, "세부분류": c.name, "기준": c.definition} for c in book["codes"]]),
                     hide_index=True, width="stretch")
    nonce_key = prefix + "_nonce"
    edit_key = f"{prefix}_{voc_id}_{run['result_revision']}_{st.session_state.get(nonce_key, 0)}"
    state_labels = {"opinions": "의견 있음", "no_content": "내용 없음·무응답"}
    response_type = st.radio("응답 상태", list(state_labels), format_func=state_labels.get, horizontal=True,
        index=1 if current.response_type == "no_content" else 0, key=edit_key + "_state")
    with st.form(edit_key + "_form"):
        frame = pd.DataFrame(issue_rows(current), columns=list(LABELS))
        labels = {code.id: f"{code.category} → {code.name}" for code in book["codes"]}
        rows, no_content_reason = [], ""
        if response_type == "opinions":
            st.caption("셀을 더블클릭해 수정하세요. 행 왼쪽 체크박스·휴지통으로 삭제하고 ＋로 추가합니다. 원문 근거는 위 원문에서 그대로 인용하세요.")
            frame["code_id"] = frame["code_id"].map(labels)
            frame["subject_evidence_source"] = frame["subject_evidence_source"].map({"original": "원문", "context": "배경"})
            edited = st.data_editor(frame, num_rows="dynamic", disabled=["item_id"], hide_index=True, width="stretch",
                key=edit_key + "_editor", column_config={
                    **{key: st.column_config.TextColumn(label) for key, label in LABELS.items()}, "item_id": None,
                    "code_id": st.column_config.SelectboxColumn("분류", options=list(labels.values()), required=True, width="medium"),
                    "sentiment": st.column_config.SelectboxColumn("감성", options=["긍정", "부정", "중립", "판단 불가"], required=True),
                    "evidence_text": st.column_config.TextColumn("원문 근거", required=True, width="large"),
                    "subject_evidence_source": st.column_config.SelectboxColumn("대상 근거 출처", options=["원문", "배경"]),
                })
            st.caption("역할이 확인되지 않으면 대상·역할과 대상 근거는 비워두세요. 배경으로 해석했다면 배경 근거와 출처도 선택하세요.")
            inverse = {label: identifier for identifier, label in labels.items()}
            for row in edited.where(pd.notna(edited), None).to_dict("records"):
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
        save = left.form_submit_button("검토 완료·수정 저장" if resolving else "수정 저장", type="primary", width="stretch")
        cancel = right.form_submit_button("편집 취소", width="stretch")
    if cancel:
        st.session_state[nonce_key] = st.session_state.get(nonce_key, 0) + 1
        st.rerun()
    if save:
        try:
            save_correction(store, run["id"], voc_id, rows, response_type, reason, run["result_revision"], no_content_reason)
            st.session_state["correction_notice"] = f"{voc_id} 수정 저장 완료. 표와 묶음 집계에 반영했습니다."
            st.rerun()
        except ValidationError:
            st.error("분류·감성·원문 근거를 빠짐없이 입력해주세요. 의견이 없는 경우 응답 상태를 명시적으로 바꿔주세요.")
        except ValueError as exc:
            st.error(str(exc))
    with st.expander("AI 최초 결과와 수정 이력"):
        st.json({"AI 최초 결과": raw[voc_id]["result"], "사용자 수정 기록": [
            row for row in store.correction_history(run["id"]) if row["voc_id"] == voc_id]})
