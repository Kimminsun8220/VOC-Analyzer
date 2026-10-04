"""분류 기준표 드롭다운의 각 줄에서 선택·이름 수정·삭제한다."""

import sqlite3

import streamlit as st


PICKER_KEY = "codebook_dropdown"
EDIT_KEY = "codebook_editing"

# Keep accessible button labels; visually present compact row actions as icons.
PICKER_STYLE = """
<style>
.st-key-codebook_dropdown [data-testid="stPopoverButton"] {
    anchor-name: --codebook-trigger;
}
.st-key-codebook_dropdown [data-testid="stPopoverButton"] > div {
    width: 100%;
    justify-content: space-between;
}
.st-key-codebook_dropdown [data-testid="stPopoverButton"] > div > div[title] {
    flex: 1;
    min-width: 0;
}
.st-key-codebook_dropdown [data-testid="stPopoverButton"] [data-has-shortcut],
.st-key-codebook_dropdown_rows [class*="st-key-codebook_select_"] button [data-has-shortcut] {
    width: 100%;
    justify-content: flex-start;
    text-align: left;
}
.st-key-codebook_dropdown [data-testid="stMarkdownContainer"],
.st-key-codebook_dropdown_rows [class*="st-key-codebook_select_"] button [data-testid="stMarkdownContainer"] {
    text-align: left;
}
.st-key-codebook_dropdown_rows [class*="st-key-codebook_select_"] button [data-has-shortcut]:not(:has([data-testid="stIconMaterial"]))::before {
    content: "";
    flex: 0 0 1rem;
}
[data-testid="stPopoverBody"]:has(.st-key-codebook_dropdown_rows) {
    position-anchor: --codebook-trigger;
    width: anchor-size(--codebook-trigger width) !important;
    min-width: 0 !important;
    max-width: anchor-size(--codebook-trigger width) !important;
    box-sizing: border-box;
    padding: 0.5rem;
    background: var(--backgroundColor, white);
}
.st-key-codebook_dropdown_rows {
    max-height: min(360px, 60dvh);
    padding-bottom: 4px;
    overflow-y: auto;
    overflow-x: clip;
}
.st-key-codebook_dropdown_rows [data-testid="stHorizontalBlock"] {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 40px 40px;
    gap: 2px;
    align-items: center;
}
.st-key-codebook_dropdown_rows [data-testid="stHorizontalBlock"] > [data-testid="stElementContainer"] {
    min-width: 0;
}
.st-key-codebook_dropdown_rows [class*="st-key-codebook_icon_"] button p {
    font-size: 0;
}
.st-key-codebook_dropdown_rows [class*="st-key-codebook_icon_"] button {
    width: 100%;
    min-width: 40px;
    padding-inline: 0;
}
.st-key-codebook_dropdown_rows [class*="st-key-codebook_icon_"] button [data-has-shortcut] {
    gap: 0;
}
.st-key-codebook_dropdown_rows [class*="st-key-codebook_icon_edit_"] [data-testid="stIconMaterial"] {
    transform: translateX(6px);
}
.st-key-codebook_dropdown_rows [class*="st-key-codebook_select_"] button {
    justify-content: flex-start;
}
.st-key-codebook_dropdown_rows [data-testid="stTextInput"] input {
    min-width: 0;
}
.st-key-codebook_dropdown_rows [data-testid="stTextInputRootElement"] {
    border: 1px solid var(--primaryColor, #1E40AF);
    border-radius: 0.5rem;
}
.st-key-codebook_dropdown_rows [data-testid="stTextInputRootElement"]:focus-within {
    box-shadow: 0 0 0 1px var(--primaryColor, #1E40AF);
}
@media (max-width: 640px) {
    .st-key-codebook_dropdown_rows [data-testid="stHorizontalBlock"] {
        grid-template-columns: minmax(0, 1fr) 44px 44px;
    }
    .st-key-codebook_dropdown_rows [class*="st-key-codebook_icon_"] button {
        min-width: 44px;
        min-height: 44px;
    }
}
</style>
"""


def codebook_label(book):
    version = f"v{book['version']} · {'초안' if book['status'] == 'draft' else '확정'}"
    return f"{book['name']} · {version}" if book["name"] else version


def picker_changed():
    if not st.session_state.get(PICKER_KEY):
        st.session_state.pop(EDIT_KEY, None)
        st.session_state.pop("codebook_manage_error", None)


def select_codebook(dataset_id, identifier):
    st.session_state[f"book_choice_{dataset_id}"] = identifier
    st.session_state[PICKER_KEY] = False
    st.session_state.pop(EDIT_KEY, None)
    st.session_state.pop("codebook_manage_error", None)


def edit_codebook(identifier, name):
    st.session_state[EDIT_KEY] = identifier
    st.session_state[f"codebook_name_{identifier}"] = name
    st.session_state.pop("codebook_manage_error", None)


def action_failed(identifier, error):
    st.session_state.codebook_manage_error = (
        identifier, str(error) if isinstance(error, ValueError) else "기준표를 변경하지 못했습니다. 다시 시도해주세요."
    )


def rename_codebook(store, identifier):
    try:
        store.rename_codebook(identifier, st.session_state[f"codebook_name_{identifier}"])
    except (ValueError, sqlite3.Error, OSError) as error:
        action_failed(identifier, error)
        return
    st.session_state.pop(EDIT_KEY, None)
    st.session_state.pop("codebook_manage_error", None)
    st.session_state.codebook_manage_notice = "기준표 이름을 변경했습니다."


def delete_codebook(store, dataset_id, identifier):
    try:
        store.delete_codebook(identifier)
    except (ValueError, sqlite3.Error, OSError) as error:
        action_failed(identifier, error)
        return
    for key in list(st.session_state):
        if identifier in key:
            del st.session_state[key]
    choice_key = f"book_choice_{dataset_id}"
    if st.session_state.get(choice_key) == identifier:
        st.session_state.pop(choice_key, None)
        st.session_state.pop("book_notice", None)
    if st.session_state.get("confirmed_book") == identifier:
        st.session_state.pop("confirmed_book", None)
    if st.session_state.get(EDIT_KEY) == identifier:
        st.session_state.pop(EDIT_KEY, None)
    st.session_state.pop("codebook_manage_error", None)
    st.session_state.codebook_manage_notice = "기준표를 삭제했습니다. 기존 분류 결과는 유지됩니다."


def icon_button(label, icon, action, args, key, *, disabled=False, help=None, submit=False):
    button = st.form_submit_button if submit else st.button
    button(label, icon=f":material/{icon}:", type="tertiary", width="stretch",
           key=f"codebook_icon_{key}", on_click=action, args=args,
           disabled=disabled, help=help or label)


def show_codebook_picker(store, dataset_id, books):
    notice = st.session_state.pop("codebook_manage_notice", None)
    if notice:
        st.toast(notice)
    labels = {book["id"]: codebook_label(book) for book in books}
    choice_key = f"book_choice_{dataset_id}"
    if not books:
        st.session_state.pop(choice_key, None)
        st.session_state.pop(EDIT_KEY, None)
        st.session_state.pop("codebook_manage_error", None)
        st.session_state[PICKER_KEY] = False
        return None
    pending = st.session_state.pop("confirmed_book", None)
    if pending in labels:
        st.session_state[choice_key] = pending
    if st.session_state.get(choice_key) not in labels:
        st.session_state[choice_key] = books[0]["id"]
    if st.session_state.get(EDIT_KEY) not in labels:
        st.session_state.pop(EDIT_KEY, None)
    st.html(PICKER_STYLE)
    st.markdown("분류 기준표 버전")
    with st.popover(labels[st.session_state[choice_key]], key=PICKER_KEY,
                    on_change=picker_changed, width="stretch", wrap=False):
        with st.container(key="codebook_dropdown_rows", gap="xxsmall"):
            for book in books:
                identifier = book["id"]
                editing = st.session_state.get(EDIT_KEY) == identifier
                delete_help = ("분류 진행 중에는 삭제할 수 없습니다." if book["analysis_running"]
                               else "기준표 목록에서 삭제합니다. 기존 분류 결과는 유지됩니다.")
                if editing:
                    with st.form(f"codebook_rename_form_{identifier}", border=False):
                        with st.container(horizontal=True, wrap=False, vertical_alignment="center", gap="xxsmall"):
                            st.text_input("기준표 이름", key=f"codebook_name_{identifier}",
                                          max_chars=100, label_visibility="collapsed")
                            icon_button("이름 저장", "check", rename_codebook, (store, identifier),
                                        f"save_{identifier}", submit=True)
                            icon_button("삭제", "close", delete_codebook, (store, dataset_id, identifier),
                                        f"delete_{identifier}", disabled=bool(book["analysis_running"]),
                                        help=delete_help, submit=True)
                else:
                    with st.container(horizontal=True, wrap=False, vertical_alignment="center", gap="xxsmall"):
                        st.button(labels[identifier], key=f"codebook_select_{identifier}", type="tertiary",
                                  width="stretch", wrap=False,
                                  icon=":material/check:" if st.session_state[choice_key] == identifier else None,
                                  on_click=select_codebook, args=(dataset_id, identifier))
                        icon_button("이름 수정", "edit", edit_codebook,
                                    (identifier, book["name"] or f"분류 기준표 {book['version']}"), f"edit_{identifier}")
                        icon_button("삭제", "close", delete_codebook, (store, dataset_id, identifier),
                                    f"delete_{identifier}", disabled=bool(book["analysis_running"]), help=delete_help)
                error = st.session_state.get("codebook_manage_error")
                if error and error[0] == identifier:
                    st.error(error[1])
    return st.session_state[choice_key]
