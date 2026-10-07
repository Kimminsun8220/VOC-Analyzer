"""분류 실행 잠금과 저장된 결과에 연결된 진행 표시."""

import sqlite3

import streamlit as st

from src.storage import RunBusyError


def request_run(key, identifier):
    st.session_state[key] = identifier
    st.session_state.pop(f"run_action_error_{identifier}", None)


def progress_snapshot(store, run):
    total = len(store.dataset(run["dataset_id"])["records"])
    saved = store.effective_results(run["id"])
    count = sum(row["status"] in ("success", "review") for row in saved)
    ratio = min(count / max(total, 1), 1.0)
    return count, total, ratio


def saved_progress_text(run, count, total, active):
    if active:
        phase = "보완된 기준표로 재분류 중" if run["round"] else "고객 의견 분류 중"
        if count == total:
            phase = "분류 결과 정리·기준표 검토 중"
    else:
        phase = "저장된 처리 결과"
    return f"{phase} · {count}/{total}건 · {count / max(total, 1):.0%}"


@st.fragment(run_every=2)
def show_run_controls(store, identifier, with_ai, run_with_progress):
    run = store.run(identifier)
    if run["status"] == "completed":
        st.rerun()
    active = store.active_run()
    requested = st.session_state.pop("resume_requested", None) == identifier
    own_active = active is not None and active["id"] == identifier
    if requested and active is None:
        st.info("분류를 시작하고 있습니다.")
    elif own_active:
        st.info("분류가 진행 중입니다.")
    elif active:
        st.info("다른 분류가 진행 중입니다. 완료 후 이어서 실행해주세요.")
    elif run["status"] == "running":
        st.warning("실행이 중단되었습니다. 저장된 지점부터 이어서 실행해주세요.")
    elif run["error"]:
        st.warning(run["error"])
    else:
        st.warning("저장된 지점부터 이어서 실행할 수 있습니다.")
    st.button("보완 이어서 실행 (최대 2회 추가)" if run["status"] == "needs_review" else "실패·미처리 이어서 실행",
              key="resume_run", disabled=active is not None or requested,
              on_click=request_run, args=("resume_requested", identifier))
    count, total, ratio = progress_snapshot(store, run)
    bar = st.progress(ratio, text=saved_progress_text(run, count, total, own_active))
    error_key = f"run_action_error_{identifier}"
    if error_key in st.session_state:
        st.error(st.session_state[error_key])
    if not requested or active is not None:
        return
    # Recheck after the button event; claim() still protects simultaneous clients.
    if store.active_run() is not None:
        st.rerun()

    def resume(ai):
        if run["status"] == "needs_review":
            store.extend_limit(identifier)
        run_with_progress(store, identifier, ai, bar=bar)

    try:
        with_ai(resume, run["model"])
    except RunBusyError:
        pass
    except (ValueError, sqlite3.Error, OSError) as exc:
        st.session_state[error_key] = (str(exc) if isinstance(exc, ValueError) else
            "실행하지 못했습니다. 저장소의 접근 권한과 여유 공간을 확인해주세요.")
    st.rerun()
