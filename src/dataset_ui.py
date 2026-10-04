"""저장된 입력 자료 드롭다운 안에서 선택·이름 수정·즉시 삭제한다."""

from collections import Counter
import sqlite3

import streamlit as st


PICKER_KEY = "dataset_dropdown"
EDIT_KEY = "dataset_editing"

# Keep real button labels for keyboard/screen-reader users; visually show the icons.
PICKER_STYLE = """
<style>
.st-key-dataset_dropdown [data-testid="stPopoverButton"] {
    anchor-name: --dataset-trigger;
}
.st-key-dataset_dropdown [data-testid="stPopoverButton"] > div {
    width: 100%;
    justify-content: space-between;
}
.st-key-dataset_dropdown [data-testid="stPopoverButton"] > div > div[title] {
    flex: 1;
    min-width: 0;
}
.st-key-dataset_dropdown [data-testid="stPopoverButton"] [data-has-shortcut],
.st-key-dataset_dropdown_rows [class*="st-key-dataset_select_"] button [data-has-shortcut] {
    width: 100%;
    justify-content: flex-start;
    text-align: left;
}
.st-key-dataset_dropdown [data-testid="stPopoverButton"] [data-testid="stMarkdownContainer"],
.st-key-dataset_dropdown_rows [class*="st-key-dataset_select_"] button [data-testid="stMarkdownContainer"] {
    text-align: left;
}
.st-key-dataset_dropdown_rows [class*="st-key-dataset_select_"] button [data-has-shortcut]:not(:has([data-testid="stIconMaterial"]))::before {
    content: "";
    flex: 0 0 1rem;
}
[data-testid="stPopoverBody"]:has(.st-key-dataset_dropdown_rows) {
    position-anchor: --dataset-trigger;
    width: anchor-size(--dataset-trigger width) !important;
    min-width: 0 !important;
    max-width: anchor-size(--dataset-trigger width) !important;
    box-sizing: border-box;
    padding: 0.5rem;
    background: var(--backgroundColor, white);
}
.st-key-dataset_dropdown_rows {
    overflow-x: clip;
}
.st-key-dataset_dropdown_rows [class*="st-key-dataset_icon_"] button p {
    font-size: 0;
}
.st-key-dataset_dropdown_rows [data-testid="stHorizontalBlock"] {
    display: grid;
    grid-template-columns: minmax(0, 76fr) minmax(24px, 12fr) minmax(24px, 12fr);
    gap: 2px;
    align-items: center;
}
.st-key-dataset_dropdown_rows [data-testid="stHorizontalBlock"] > [data-testid="stElementContainer"] {
    min-width: 0;
}
.st-key-dataset_dropdown_rows [class*="st-key-dataset_icon_"] button {
    width: 100%;
    min-width: 24px;
    padding-inline: 0;
}
.st-key-dataset_dropdown_rows [class*="st-key-dataset_icon_"] button [data-has-shortcut] {
    gap: 0;
}
.st-key-dataset_dropdown_rows [class*="st-key-dataset_select_"] button {
    justify-content: flex-start;
}
.st-key-dataset_dropdown_rows [data-testid="stTextInput"] input {
    min-width: 0;
}
.st-key-dataset_dropdown_rows [data-testid="stTextInputRootElement"] {
    border: 1px solid var(--primaryColor, #1E40AF);
    border-radius: 0.5rem;
}
.st-key-dataset_dropdown_rows [data-testid="stTextInputRootElement"]:focus-within {
    box-shadow: 0 0 0 1px var(--primaryColor, #1E40AF);
}
</style>
"""


def clear_deleted_dataset_state():
    identifiers = st.session_state.pop("deleted_dataset_state_ids", [])
    if identifiers:
        for key in list(st.session_state):
            if any(identifier in key for identifier in identifiers):
                del st.session_state[key]
        if st.session_state.pop("deleted_selected_dataset", False):
            for key in ("confirmed_book", "run_id", "book_notice", "correction_notice"):
                st.session_state.pop(key, None)


def picker_changed():
    if not st.session_state.get(PICKER_KEY):
        st.session_state.pop(EDIT_KEY, None)
        st.session_state.pop("dataset_action_error", None)


def select_dataset(identifier):
    st.session_state.dataset_id = identifier
    st.session_state[PICKER_KEY] = False
    st.session_state.pop(EDIT_KEY, None)
    st.session_state.pop("dataset_action_error", None)


def edit_dataset(identifier, name):
    st.session_state[EDIT_KEY] = identifier
    st.session_state[f"dataset_name_{identifier}"] = name
    st.session_state.pop("dataset_action_error", None)


def action_failed(identifier, error):
    message = str(error) if isinstance(error, ValueError) else "자료를 변경하지 못했습니다. 다시 시도해주세요."
    st.session_state.dataset_action_error = (identifier, message)


def rename_dataset(store, identifier):
    try:
        store.rename_dataset(identifier, st.session_state[f"dataset_name_{identifier}"])
    except (ValueError, sqlite3.Error, OSError) as error:
        action_failed(identifier, error)
        return
    st.session_state.pop(EDIT_KEY, None)
    st.session_state.pop("dataset_action_error", None)
    st.session_state.dataset_notice = "이름을 변경했습니다."


def delete_dataset(store, identifier):
    try:
        identifiers = [identifier, *[book["id"] for book in store.list_codebooks(identifier, include_deleted=True)],
                       *[run["id"] for run in store.list_runs(identifier)]]
        store.delete_dataset(identifier)
    except (ValueError, sqlite3.Error, OSError) as error:
        action_failed(identifier, error)
        return
    st.session_state.deleted_dataset_state_ids = identifiers
    selected = st.session_state.get("dataset_id") == identifier
    st.session_state.deleted_selected_dataset = selected
    if selected:
        remaining = store.list_datasets()
        if remaining:
            st.session_state.dataset_id = remaining[0]["id"]
        else:
            st.session_state.pop("dataset_id", None)
            st.session_state.page = "1. 입력"
            st.session_state[PICKER_KEY] = False
    if st.session_state.get(EDIT_KEY) == identifier:
        st.session_state.pop(EDIT_KEY, None)
    st.session_state.pop("dataset_action_error", None)
    st.session_state.dataset_notice = "자료를 삭제했습니다."


def dataset_labels(datasets):
    counts = Counter(row["name"] for row in datasets)
    numbers = Counter()
    labels = {}
    for row in datasets:
        name = row["name"]
        numbers[name] += 1
        labels[row["id"]] = f"{name} {numbers[name]}" if counts[name] > 1 else name
    return labels


def icon_button(label, icon, action, args, key, *, disabled=False, help=None, submit=False):
    button = st.form_submit_button if submit else st.button
    button(label, icon=f":material/{icon}:", type="tertiary", width="stretch",
           key=f"dataset_icon_{key}", on_click=action, args=args,
           disabled=disabled, help=help or label)


def show_dataset_picker(store, datasets):
    labels = dataset_labels(datasets)
    if st.session_state.get("dataset_id") not in labels:
        st.session_state.dataset_id = datasets[0]["id"]
    notice = st.session_state.pop("dataset_notice", None)
    if notice:
        st.toast(notice)
    st.html(PICKER_STYLE)
    st.markdown("저장된 입력 자료")
    with st.popover(labels[st.session_state.dataset_id], key=PICKER_KEY,
                    on_change=picker_changed, width="stretch", wrap=False):
        with st.container(key="dataset_dropdown_rows", gap="xxsmall"):
            for row in datasets:
                identifier = row["id"]
                editing = st.session_state.get(EDIT_KEY) == identifier
                delete_help = "분류 진행 중에는 삭제할 수 없습니다." if row["analysis_running"] else f"{row['name']} 삭제"
                if editing:
                    with st.form(f"dataset_rename_{identifier}", border=False):
                        with st.container(horizontal=True, wrap=False, vertical_alignment="center", gap="xxsmall"):
                            st.text_input("자료 이름", max_chars=100,
                                          key=f"dataset_name_{identifier}", label_visibility="collapsed")
                            icon_button("이름 저장", "check", rename_dataset, (store, identifier),
                                        f"save_{identifier}", submit=True)
                            icon_button("삭제", "close", delete_dataset, (store, identifier),
                                        f"delete_{identifier}", disabled=bool(row["analysis_running"]),
                                        help=delete_help, submit=True)
                else:
                    with st.container(horizontal=True, wrap=False, vertical_alignment="center", gap="xxsmall"):
                        st.button(labels[identifier], key=f"dataset_select_{identifier}",
                                  type="tertiary", width="stretch", wrap=False,
                                  icon=":material/check:" if st.session_state.dataset_id == identifier else None,
                                  on_click=select_dataset, args=(identifier,))
                        icon_button("이름 수정", "edit", edit_dataset, (identifier, row["name"]),
                                    f"edit_{identifier}")
                        icon_button("삭제", "close", delete_dataset, (store, identifier),
                                    f"delete_{identifier}", disabled=bool(row["analysis_running"]), help=delete_help)
                error = st.session_state.get("dataset_action_error")
                if error and error[0] == identifier:
                    st.error(error[1])
