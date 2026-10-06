"""저장한 분류 목록에서 선택·이름 수정·삭제한다."""

import sqlite3

import streamlit as st

from src.codebook_picker_ui import PICKER_STYLE as CODEBOOK_PICKER_STYLE


# Reuse the existing compact editable picker, with styles scoped to this list.
PICKER_STYLE = CODEBOOK_PICKER_STYLE.replace("codebook", "saved_group")
PICKER_STYLE += """
<style>
.st-key-saved_group_dropdown_rows [class*="st-key-saved_group_icon_edit_"] [data-testid="stIconMaterial"] {
    transform: translateX(12px);
}
@media (max-width: 640px) {
    .st-key-saved_group_dropdown_rows [class*="st-key-saved_group_icon_edit_"] [data-testid="stIconMaterial"] {
        transform: translateX(14px);
    }
}
.st-key-saved_groups_actions [data-testid="stPopoverButton"] {
    anchor-name: --saved-groups-actions-trigger;
}
[data-testid="stPopoverBody"]:has(.st-key-saved_group_dropdown) {
    position: fixed !important;
    position-anchor: --saved-groups-actions-trigger;
    transform: none !important;
    top: auto !important;
    bottom: anchor(top);
    left: clamp(8px, anchor(left), calc(100vw - 328px)) !important;
    width: 320px !important;
    max-width: calc(100vw - 16px) !important;
    margin-bottom: 8px;
    max-height: 60dvh !important;
}
</style>
"""


def picker_changed(prefix):
    if not st.session_state.get(prefix + "_saved_picker"):
        st.session_state.pop(prefix + "_saved_editing", None)
        st.session_state.pop(prefix + "_saved_error", None)


def select_group(prefix, identifier):
    st.session_state[prefix + "_saved"] = identifier
    st.session_state[prefix + "_saved_picker"] = False
    st.session_state.pop(prefix + "_saved_editing", None)
    st.session_state.pop(prefix + "_saved_error", None)


def edit_group(prefix, identifier, name):
    st.session_state[prefix + "_saved_editing"] = identifier
    st.session_state[f"{prefix}_saved_name_{identifier}"] = name
    st.session_state.pop(prefix + "_saved_error", None)


def action_failed(prefix, identifier, error):
    message = str(error) if isinstance(error, ValueError) else "변경하지 못했습니다. 다시 시도해주세요."
    st.session_state[prefix + "_saved_error"] = (identifier, message)


def rename_group(store, run_id, prefix, identifier):
    try:
        store.rename_group(run_id, identifier, st.session_state[f"{prefix}_saved_name_{identifier}"])
    except (ValueError, sqlite3.Error, OSError) as error:
        action_failed(prefix, identifier, error)
        return
    st.session_state.pop(prefix + "_saved_editing", None)
    st.session_state.pop(prefix + "_saved_error", None)
    st.session_state[prefix + "_saved_notice"] = "이름을 변경했습니다."


def delete_group(store, run_id, prefix, identifier):
    try:
        store.delete_group(run_id, identifier)
    except (ValueError, sqlite3.Error, OSError) as error:
        action_failed(prefix, identifier, error)
        return
    if st.session_state.get(prefix + "_saved") == identifier:
        st.session_state.pop(prefix + "_saved", None)
    if st.session_state.get(prefix + "_saved_editing") == identifier:
        st.session_state.pop(prefix + "_saved_editing", None)
    st.session_state.pop(f"{prefix}_saved_name_{identifier}", None)
    st.session_state.pop(prefix + "_saved_error", None)
    st.session_state[prefix + "_saved_notice"] = "저장한 분류를 삭제했습니다."


def icon_button(label, icon, action, args, key, *, help=None, submit=False):
    button = st.form_submit_button if submit else st.button
    button(label, icon=f":material/{icon}:", type="tertiary", width="stretch",
           key=f"saved_group_icon_{key}", on_click=action, args=args, help=help or label)


def show_saved_group_picker(store, run_id, groups, prefix):
    notice = st.session_state.pop(prefix + "_saved_notice", None)
    if notice:
        st.toast(notice)
    labels = {group["id"]: group["name"] for group in groups}
    if not groups:
        for suffix in ("_saved", "_saved_editing", "_saved_error"):
            st.session_state.pop(prefix + suffix, None)
        return None
    if st.session_state.get(prefix + "_saved") not in labels:
        st.session_state[prefix + "_saved"] = groups[0]["id"]
    if st.session_state.get(prefix + "_saved_editing") not in labels:
        st.session_state.pop(prefix + "_saved_editing", None)
    st.html(PICKER_STYLE)
    st.markdown("불러올 분류")
    with st.container(key="saved_group_dropdown"):
        with st.popover(labels[st.session_state[prefix + "_saved"]], key=prefix + "_saved_picker",
                        on_change=picker_changed, args=(prefix,), width="stretch", wrap=False):
            with st.container(key="saved_group_dropdown_rows", gap="xxsmall"):
                for group in groups:
                    identifier, name = group["id"], group["name"]
                    if st.session_state.get(prefix + "_saved_editing") == identifier:
                        with st.form(f"{prefix}_saved_rename_{identifier}", border=False):
                            with st.container(horizontal=True, wrap=False, vertical_alignment="center", gap="xxsmall"):
                                st.text_input("분류 이름", key=f"{prefix}_saved_name_{identifier}",
                                              max_chars=80, label_visibility="collapsed")
                                icon_button("이름 저장", "check", rename_group, (store, run_id, prefix, identifier),
                                            f"save_{identifier}", submit=True)
                                icon_button(f"{name} 삭제", "close", delete_group, (store, run_id, prefix, identifier),
                                            f"delete_{identifier}", submit=True)
                    else:
                        with st.container(horizontal=True, wrap=False, vertical_alignment="center", gap="xxsmall"):
                            st.button(name, key=f"saved_group_select_{identifier}", type="tertiary", width="stretch",
                                      wrap=False, icon=":material/check:" if st.session_state[prefix + "_saved"] == identifier else None,
                                      on_click=select_group, args=(prefix, identifier))
                            icon_button(f"{name} 이름 수정", "edit", edit_group, (prefix, identifier, name),
                                        f"edit_{identifier}")
                            icon_button(f"{name} 삭제", "close", delete_group, (store, run_id, prefix, identifier),
                                        f"delete_{identifier}")
                    error = st.session_state.get(prefix + "_saved_error")
                    if error and error[0] == identifier:
                        st.error(error[1])
                error = st.session_state.get(prefix + "_saved_error")
                if error and error[0] not in labels:
                    st.error(error[1])
    return st.session_state[prefix + "_saved"]
