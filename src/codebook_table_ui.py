"""Streamlit v2 컴포넌트로 드래그와 행 메뉴를 지원한다."""

from pathlib import Path

import streamlit as st

from src.codebook_table import table_action, table_draft, update_fields, visible_rows

ASSETS = Path(__file__).parent / "components"


def interactive_table(book):
    prefix = f"revise_{book['id']}"
    state_key = prefix + "_draft"
    component_key = prefix + "_table"
    if state_key not in st.session_state:
        st.session_state[state_key] = table_draft(book)
    draft = st.session_state[state_key]
    notice = st.session_state.pop(prefix + "_table_error", None)
    if notice:
        st.error(notice)
    prior = st.session_state.get(component_key)
    edits = getattr(prior, "edits", None)
    if edits and edits.get("revision") == draft["revision"]:
        update_fields(draft, edits["rows"])
    renderer = st.components.v2.component("criteria_table", html='<div class="criteria-table"></div>',
        css=(ASSETS / "criteria_table.css").read_text(encoding="utf-8"),
        js=(ASSETS / "criteria_table.js").read_text(encoding="utf-8"), isolate_styles=False)
    result = renderer(key=component_key,
        data={"rows": visible_rows(draft), "revision": draft["revision"], "can_undo": bool(draft["history"]), "focus": draft["focus"]},
        default={"edits": None}, on_action_change=lambda: None, on_edits_change=lambda: None)
    if result.edits and result.edits.get("revision") == draft["revision"]:
        update_fields(draft, result.edits["rows"])
    action = result.action
    save_requested = False
    if action and action.get("nonce") != st.session_state.get(prefix + "_last_action"):
        st.session_state[prefix + "_last_action"] = action.get("nonce")
        if action.get("revision") == draft["revision"]:
            try:
                if action.get("kind") == "save":
                    update_fields(draft, action["rows"])
                    save_requested = True
                else:
                    st.session_state[state_key] = table_action(draft, action)
            except ValueError as exc:
                st.session_state[prefix + "_table_error"] = str(exc)
                draft["revision"] += 1
            if not save_requested:
                st.rerun()
    return draft, save_requested
